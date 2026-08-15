"""LedgerIndex — builds lookup indexes from existing Beancount entries.

Pure Python, stdlib only.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING

from beancount.core.data import filter_txns

from beancount_hooks.normalizer import extract_keywords, normalize_payee, round_to_bin

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class LedgerIndex:
    """Index built from historical beancount entries for rule-based lookups.

    Scans *existing_entries* and populates multiple counter maps that
    drive the predictors' decision ladders.
    """

    def __init__(self, existing_entries: list) -> None:
        self.payee_map: dict[str, Counter[frozenset[str]]] = {}
        self.normalized_payee_map: dict[str, Counter[frozenset[str]]] = {}
        self.keyword_map: dict[str, Counter[frozenset[str]]] = {}
        self.amount_map: dict[tuple[str, float, int], Counter[frozenset[str]]] = {}
        self.cooccur_map: dict[str, Counter[str]] = {}
        self.tag_map: dict[str, Counter[str]] = {}
        self._build(existing_entries)

    def _build(self, entries: Iterable) -> None:
        """Scan all transactions and populate indexes."""
        import re

        for txn in filter_txns(entries):
            accounts = frozenset(p.account for p in txn.postings if p.account)
            if not accounts:
                continue

            payee = txn.payee or ""
            narration = txn.narration or ""
            tags = txn.tags or frozenset()

            # --- Payee indexes ---
            if payee:
                self.payee_map.setdefault(payee, Counter())[accounts] += 1
                norm = normalize_payee(payee)
                if norm:
                    self.normalized_payee_map.setdefault(norm, Counter())[accounts] += 1

            # --- Keyword index ---
            keywords = extract_keywords(narration)
            for kw in keywords:
                self.keyword_map.setdefault(kw, Counter())[accounts] += 1

            # --- Amount index ---
            for p in txn.postings:
                if p.units is None or p.units.number is None:
                    continue
                amount_val = float(p.units.number)
                sign = 1 if amount_val >= 0 else -1
                bin_val = round_to_bin(abs(amount_val))
                key = (payee, bin_val, sign)
                self.amount_map.setdefault(key, Counter())[accounts] += 1

            # --- Co-occurrence index ---
            for p in txn.postings:
                if not p.account:
                    continue
                other_accounts = [a for a in accounts if a != p.account]
                for other in other_accounts:
                    self.cooccur_map.setdefault(p.account, Counter())[other] += 1

            # --- Tag index (keyed by normalized payee, not account set) ---
            if payee and tags:
                tag_key = normalize_payee(payee) or payee
                for tag in tags:
                    self.tag_map.setdefault(tag_key, Counter())[tag] += 1

        # --- Post-build: prune keyword map ---
        # Keep only keywords seen in ≥3 transactions and non-numeric.
        self.keyword_map = {
            kw: counter
            for kw, counter in self.keyword_map.items()
            if counter.total() >= 3 and not re.match(r"^\d+$", kw)
        }

    # ------------------------------------------------------------------
    # Public query helpers
    # ------------------------------------------------------------------

    def _top_accounts(
        self, counter: Counter[frozenset[str]], min_count: int
    ) -> list[str] | None:
        """Return the most common account set from a counter, or None.

        Only returns the accounts if the top entry's count is >= *min_count*.
        """
        if not counter:
            return None
        top_accounts, top_count = counter.most_common(1)[0]
        if top_count < min_count:
            return None
        return sorted(top_accounts)

    def get_accounts_by_payee(
        self, payee: str, min_count: int = 3
    ) -> list[str] | None:
        """Exact payee → most common account set (≥ *min_count* occurrences)."""
        counter = self.payee_map.get(payee)
        if counter is None:
            return None
        return self._top_accounts(counter, min_count)

    def get_accounts_by_normalized_payee(
        self, payee: str, min_count: int = 3
    ) -> list[str] | None:
        """Normalized payee → most common account set (≥ *min_count* occurrences)."""
        norm = normalize_payee(payee)
        if not norm:
            return None
        counter = self.normalized_payee_map.get(norm)
        if counter is None:
            return None
        return self._top_accounts(counter, min_count)

    def get_accounts_by_keyword(
        self, narration: str, min_count: int = 3
    ) -> list[str] | None:
        """Keyword(s) extracted from narration → most common account set.

        Returns the account set of the keyword with the highest total
        count.  Only returns if that top count is ≥ *min_count*.
        """
        keywords = extract_keywords(narration)
        if not keywords:
            return None

        # Aggregate counters across all matched keywords.
        aggregated: Counter[frozenset[str]] = Counter()
        for kw in keywords:
            if kw in self.keyword_map:
                aggregated.update(self.keyword_map[kw])

        if not aggregated:
            return None

        top_accounts, top_count = aggregated.most_common(1)[0]
        if top_count < min_count:
            return None
        return sorted(top_accounts)

    def get_accounts_by_amount(
        self,
        payee: str,
        amount: float,
        sign: int,
        bin_size: float = 10.0,
        min_count: int = 3,
    ) -> list[str] | None:
        """(Payee, amount bin, sign) → most common account set (≥ *min_count*)."""
        bin_val = round_to_bin(abs(amount), bin_size)
        key = (payee, bin_val, sign)
        counter = self.amount_map.get(key)
        if counter is None:
            return None
        return self._top_accounts(counter, min_count)

    def get_counterpart(
        self, account: str, min_count: int = 10
    ) -> str | None:
        """Most common counterpart account for *account* (≥ *min_count* occurrences)."""
        counter = self.cooccur_map.get(account)
        if counter is None:
            return None
        top_account, top_count = counter.most_common(1)[0]
        if top_count < min_count:
            return None
        return top_account

    def get_tags(
        self, payee: str, min_count: int = 5
    ) -> list[str]:
        """Return tags that appear ≥ *min_count* times for *payee*.

        Uses normalized payee to handle variant names.
        Returns an empty list if no qualifying tags are found.
        """
        norm = normalize_payee(payee) or payee
        counter = self.tag_map.get(norm)
        if counter is None:
            return []
        return [
            tag
            for tag, count in counter.most_common()
            if count >= min_count
        ]

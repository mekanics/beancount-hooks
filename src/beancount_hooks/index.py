"""LedgerIndex — builds lookup indexes from existing Beancount entries.

Pure Python, stdlib only.
"""

from __future__ import annotations

import logging
from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING

from beancount.core.data import Transaction, filter_txns

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
        # For each (account set, which leg the money came from), the fraction every other leg
        # took, once per transaction.  Only genuine splits are recorded — see get_split_shares.
        self.split_map: dict[tuple[frozenset[str], str], list[dict[str, Decimal]]] = {}
        # Denominator for tag_map.  tag_map only counts transactions that carry a tag, so on
        # its own it cannot say whether a tag is a property of the payee or a leftover from a
        # handful of them — see get_tags.
        self.payee_total: Counter[str] = Counter()
        self._build(existing_entries)

    def _build(self, entries: Iterable) -> None:
        """Scan all transactions and populate indexes."""
        import re

        for txn in filter_txns(entries):
            accounts = frozenset(p.account for p in txn.postings if p.account)
            if not accounts:
                continue

            payee = txn.payee or ''
            narration = txn.narration or ''
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

            # --- Split index ---
            # Only for transactions with more than two accounts.  A two-legged transaction
            # needs no fractions: whatever the other leg is, it takes all of it.
            if len(accounts) > 2:
                self._record_split(txn, accounts)

            # --- Tag index (keyed by normalized payee, not account set) ---
            if payee:
                tag_key = normalize_payee(payee) or payee
                self.payee_total[tag_key] += 1
                for tag in tags:
                    self.tag_map.setdefault(tag_key, Counter())[tag] += 1

        # --- Post-build: prune keyword map ---
        # Keep only keywords seen in ≥3 transactions and non-numeric.
        self.keyword_map = {
            kw: counter
            for kw, counter in self.keyword_map.items()
            if counter.total() >= 3 and not re.match(r'^\d+$', kw)
        }

    # ------------------------------------------------------------------
    # Public query helpers
    # ------------------------------------------------------------------

    def _top_accounts(self, counter: Counter[frozenset[str]], min_count: int) -> list[str] | None:
        """Return the most common account set from a counter, or None.

        Only returns the accounts if the top entry's count is >= *min_count*.
        """
        if not counter:
            return None
        top_accounts, top_count = counter.most_common(1)[0]
        if top_count < min_count:
            return None
        return sorted(top_accounts)

    def _record_split(self, txn: Transaction, accounts: frozenset[str]) -> None:
        """Note what fraction each leg took, from the point of view of every other leg.

        Which leg is the source is not knowable here — it depends on which importer asks
        later — so the shares are recorded once per candidate source.
        """
        totals: dict[str, Decimal] = {}
        for posting in txn.postings:
            if not posting.account or posting.units is None or posting.units.number is None:
                continue
            totals[posting.account] = totals.get(posting.account, Decimal(0)) + posting.units.number
        if len(totals) != len(accounts):
            # A leg with no amount, so the fractions cannot be worked out.  Loaded ledgers
            # have interpolated amounts, but an entry straight from a parser may not.
            return

        for source, total in totals.items():
            if not total:
                continue
            self.split_map.setdefault((accounts, source), []).append(
                {account: value / -total for account, value in totals.items() if account != source}
            )

    def get_split_shares(
        self,
        accounts: frozenset[str],
        source_account: str,
        min_count: int = 3,
        tolerance: Decimal = Decimal('0.01'),
    ) -> list[tuple[str, Decimal]] | None:
        """How *accounts* divide an amount arriving from *source_account*, or None.

        Returns ``(account, fraction)`` pairs sorted by account name, so the caller allocates
        in a stable order and the last one absorbs the rounding remainder.

        Answers only where the division is a habit rather than a coincidence: the same set of
        legs, at least *min_count* times, with every fraction within *tolerance* of its mean.
        A household purchase halved with a partner qualifies; an insurance premium split three
        ways in proportions that are renegotiated yearly does not, and gets None so the legs
        stay blank for review.
        """
        observations = self.split_map.get((accounts, source_account))
        if observations is None or len(observations) < min_count:
            return None

        others = set(observations[0])
        if any(set(observation) != others for observation in observations):
            return None

        shares: list[tuple[str, Decimal]] = []
        for account in sorted(others):
            values = [observation[account] for observation in observations]
            if max(values) - min(values) > tolerance:
                return None
            shares.append((account, sum(values) / len(values)))
        return shares

    def get_accounts_by_payee(self, payee: str, min_count: int = 3) -> list[str] | None:
        """Exact payee → most common account set (≥ *min_count* occurrences)."""
        counter = self.payee_map.get(payee)
        if counter is None:
            return None
        return self._top_accounts(counter, min_count)

    def get_accounts_by_normalized_payee(self, payee: str, min_count: int = 3) -> list[str] | None:
        """Normalized payee → most common account set (≥ *min_count* occurrences)."""
        norm = normalize_payee(payee)
        if not norm:
            return None
        counter = self.normalized_payee_map.get(norm)
        if counter is None:
            return None
        return self._top_accounts(counter, min_count)

    def get_accounts_by_keyword(self, narration: str, min_count: int = 3) -> list[str] | None:
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

    def get_counterpart(self, account: str, min_count: int = 10) -> str | None:
        """Most common counterpart account for *account* (≥ *min_count* occurrences)."""
        counter = self.cooccur_map.get(account)
        if counter is None:
            return None
        top_account, top_count = counter.most_common(1)[0]
        if top_count < min_count:
            return None
        return top_account

    def get_tags(self, payee: str, min_count: int = 5, min_share: float = 0.0) -> list[str]:
        """Return tags that are a property of *payee*, most frequent first.

        A tag qualifies by appearing at least *min_count* times **and** on at least
        *min_share* of the payee's transactions.  The count alone says very little on a busy
        payee: five occurrences out of four hundred is a leftover from one trip, not something
        true of the payee, and a placeholder payee such as "self" collects the tags of every
        unrelated transfer that shares the name.

        Uses normalized payee to handle variant names.  Returns an empty list if no tag
        qualifies.
        """
        norm = normalize_payee(payee) or payee
        counter = self.tag_map.get(norm)
        if counter is None:
            return []
        total = self.payee_total.get(norm, 0)
        return [
            tag
            for tag, count in counter.most_common()
            if count >= min_count and (total == 0 or count / total >= min_share)
        ]

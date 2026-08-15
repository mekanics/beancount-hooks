"""Beangulp import hooks — rule-based predictors for postings, payees, and tags.

Each predictor implements a ``hook(imported_entries, existing_entries)`` method
that matches the beangulp hook API.

Pure Python, stdlib only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from beancount.core.data import filter_txns

from beancount_hooks.index import LedgerIndex
from beancount_hooks.normalizer import normalize_payee
from beancount_hooks.utils import get_amount_and_sign

if TYPE_CHECKING:
    from beancount.core.data import Transaction

logger = logging.getLogger(__name__)


def _make_cache_key(entries: list) -> int:
    """A stable cache key derived from ledger contents."""
    if not entries:
        return 0
    return hash((id(entries[0]), id(entries[-1]), len(entries)))


class RulesPostingsPredictor:
    """Predict missing posting accounts using a decision ladder.

    The importer already provides one leg (e.g. ``Assets:Bank:CHF``).
    This predictor attempts to fill the *other* leg(s).
    """

    def __init__(
        self,
        min_occurrence: int = 3,
        amount_bin_size: float = 10.0,
        enable_rule_5: bool = True,
    ) -> None:
        self.min_occurrence = min_occurrence
        self.amount_bin_size = amount_bin_size
        self.enable_rule_5 = enable_rule_5
        self._cache: tuple[int | None, LedgerIndex | None] = (None, None)

    # ------------------------------------------------------------------
    # Beangulp hook API
    # ------------------------------------------------------------------

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, object]],
        existing_entries: list,
    ) -> list[tuple[str, list, str, object]]:
        """Beangulp hook — mutates imported entries in-place.

        Args:
            imported_entries: list of ``(filename, entries, account, importer)``.
            existing_entries: existing ledger entries.

        Returns:
            The (possibly mutated) ``imported_entries`` list.
        """
        try:
            index = self._get_or_build_index(existing_entries)
        except Exception:
            logger.warning(
                "RulesPostingsPredictor: failed to build LedgerIndex, "
                "passing through.",
                exc_info=True,
            )
            return imported_entries

        for filename, entries, account, importer in imported_entries:
            for i, entry in enumerate(filter_txns(entries)):
                try:
                    predicted = self._predict(entry, account, index)
                    if predicted:
                        # Transaction is immutable — _replace returns a new object.
                        # Use enumerate index, not entries.index() — value
                        # equality would match duplicate transactions wrong.
                        new_entry = self._apply_prediction(entry, predicted)
                        entries[i] = new_entry
                except Exception:
                    logger.warning(
                        "RulesPostingsPredictor: prediction failed for entry "
                        "%s, skipping.",
                        entry.narration or entry.payee or "<unknown>",
                        exc_info=True,
                    )

        return imported_entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_build_index(self, existing_entries: list) -> LedgerIndex:
        """Return a cached ``LedgerIndex`` or build a new one."""
        key = _make_cache_key(existing_entries)
        cached_key, cached_index = self._cache
        if cached_key == key and cached_index is not None:
            return cached_index
        index = LedgerIndex(existing_entries)
        self._cache = (key, index)
        return index

    def _predict(
        self,
        entry: Transaction,
        importer_account: str | None,
        index: LedgerIndex,
    ) -> list[str] | None:
        """Apply decision ladder.  First match wins.

        Returns a list of account strings for the *other* leg(s), or None.
        """
        payee = entry.payee or ""
        narration = entry.narration or ""

        # Helper: only keep accounts that are NOT the importer account.
        def _filter_importer(accounts: list[str] | None) -> list[str] | None:
            if accounts is None:
                return None
            if importer_account and importer_account in accounts:
                filtered = [a for a in accounts if a != importer_account]
                return filtered if filtered else None
            # Predicted set doesn't include importer account → mismatch, skip.
            return None

        # Rule 1: Exact payee match.
        result = _filter_importer(
            index.get_accounts_by_payee(payee, self.min_occurrence)
        )
        if result:
            return result

        # Rule 2: Normalized payee match.
        result = _filter_importer(
            index.get_accounts_by_normalized_payee(payee, self.min_occurrence)
        )
        if result:
            return result

        # Rule 3: Narration keyword match.
        result = _filter_importer(
            index.get_accounts_by_keyword(narration, self.min_occurrence)
        )
        if result:
            return result

        # Rule 4: Amount binning.
        # We need at least one posting with units to derive amount+sign.
        amount: float | None = None
        sign: int | None = None
        for p in entry.postings:
            amt, s = get_amount_and_sign(p)
            if amt is not None:
                amount = amt
                sign = s
                break
        if amount is not None and sign is not None:
            result = _filter_importer(
                index.get_accounts_by_amount(
                    payee, amount, sign, self.amount_bin_size, self.min_occurrence
                )
            )
            if result:
                return result

        # Rule 5: Account co-occurrence (weakest, requires ≥10 by default).
        if self.enable_rule_5 and importer_account:
            counterpart = index.get_counterpart(
                importer_account, min_count=10
            )
            if counterpart:
                return [counterpart]

        return None

    def _apply_prediction(self, entry: Transaction, predicted_accounts: list[str]) -> Transaction:
        """Return a new Transaction with predicted postings added.

        Only adds postings for accounts not already present.  Sets a
        zero amount so the user can review in Fava.
        """
        existing_accounts = {p.account for p in entry.postings if p.account}
        from beancount.core.data import Posting
        from beancount.core.amount import Amount
        from beancount.core.number import ZERO

        new_postings = list(entry.postings)
        for account in predicted_accounts:
            if account in existing_accounts:
                continue
            new_postings.append(
                Posting(
                    account=account,
                    units=Amount(ZERO, "CHF"),
                    cost=None,
                    price=None,
                    flag=None,
                    meta=None,
                )
            )
        return entry._replace(postings=new_postings)


class RulesPayeePredictor:
    """Predict / normalize payee names."""

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, object]],
        existing_entries: list,
    ) -> list[tuple[str, list, str, object]]:
        """Normalize payee and attempt to derive from narration if missing."""
        for filename, entries, account, importer in imported_entries:
            for i, entry in enumerate(filter_txns(entries)):
                try:
                    new_entry = self._predict(entry)
                    if new_entry is not entry:
                        entries[i] = new_entry
                except Exception:
                    logger.warning(
                        "RulesPayeePredictor: failed for entry %s, "
                        "skipping.",
                        entry.narration or "<unknown>",
                        exc_info=True,
                    )
        return imported_entries

    def _predict(self, entry: Transaction) -> Transaction:
        """Apply payee prediction rules.

        Returns a (possibly modified) Transaction.
        """
        payee = entry.payee or ""
        narration = entry.narration or ""

        # Rule 1: Normalize importer-provided payee.
        normalized = normalize_payee(payee)
        if normalized and normalized != payee.lower().strip():
            entry = entry._replace(payee=normalized)
            payee = normalized

        # Rule 2: Derive from narration patterns if payee is empty.
        if not payee and narration:
            derived = self._derive_from_narration(narration)
            if derived:
                entry = entry._replace(payee=derived)

        return entry

    def _derive_from_narration(self, narration: str) -> str | None:
        """Extract a payee from narration heuristics."""
        import re

        lower = narration.lower().strip()

        # "Salary from X" → X
        m = re.search(r"salary\s+from\s+(.+)", lower)
        if m:
            return m.group(1).strip().title()

        # "Invoice.*from X" → X
        m = re.search(r"invoice.*from\s+(.+)", lower)
        if m:
            return m.group(1).strip().title()

        # "Transfer to X" → X
        m = re.search(r"transfer\s+to\s+(.+)", lower)
        if m:
            return m.group(1).strip().title()

        # "Payment to X" → X
        m = re.search(r"payment\s+to\s+(.+)", lower)
        if m:
            return m.group(1).strip().title()

        # "X GmbH / X AG" trailing
        m = re.search(r"([\w\s]+(?:\s+gmbh|\s+ag|\s+sa|\s+sarl))", lower)
        if m:
            return m.group(1).strip().title()

        return None


class RulesTagsPredictor:
    """Predict tags based on (payee, account_set) frequency from history."""

    def __init__(
        self,
        min_tag_occurrence: int = 5,
        max_tags: int = 3,
    ) -> None:
        self.min_tag_occurrence = min_tag_occurrence
        self.max_tags = max_tags
        self._cache: tuple[int | None, LedgerIndex | None] = (None, None)

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, object]],
        existing_entries: list,
    ) -> list[tuple[str, list, str, object]]:
        """Predict tags and store them in ``entry.meta``."""
        try:
            index = self._get_or_build_index(existing_entries)
        except Exception:
            logger.warning(
                "RulesTagsPredictor: failed to build LedgerIndex, "
                "passing through.",
                exc_info=True,
            )
            return imported_entries

        for filename, entries, account, importer in imported_entries:
            for i, entry in enumerate(filter_txns(entries)):
                try:
                    predicted = self._predict(entry, index)
                    if predicted:
                        new_entry = entry._replace(
                            meta={**entry.meta, "predicted_tags": ",".join(predicted)}
                        )
                        entries[i] = new_entry
                except Exception:
                    logger.warning(
                        "RulesTagsPredictor: failed for entry %s, skipping.",
                        entry.narration or entry.payee or "<unknown>",
                        exc_info=True,
                    )

        return imported_entries

    def _get_or_build_index(self, existing_entries: list) -> LedgerIndex:
        """Return a cached ``LedgerIndex`` or build a new one."""
        key = _make_cache_key(existing_entries)
        cached_key, cached_index = self._cache
        if cached_key == key and cached_index is not None:
            return cached_index
        index = LedgerIndex(existing_entries)
        self._cache = (key, index)
        return index

    def _predict(self, entry: Transaction, index: LedgerIndex) -> list[str]:
        """Apply tag prediction rules."""
        payee = entry.payee or ""
        accounts = frozenset(p.account for p in entry.postings if p.account)

        # Rule 1: payee → tags (now looks up by normalized payee only)
        tags = index.get_tags(payee, self.min_tag_occurrence)
        if tags:
            return tags[: self.max_tags]

        # Rule 2: Narration keyword → tags (not implemented in MVP)
        # Rule 3: Amount bin + account → tags (not implemented in MVP)

        return []

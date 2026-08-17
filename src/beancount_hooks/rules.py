"""Beangulp import hooks — rule-based predictors for postings, payees, and tags.

Each predictor implements a ``hook(imported_entries, existing_entries)`` method
that matches the beangulp hook API.

Pure Python, stdlib only.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import TYPE_CHECKING, TypeAlias

from beancount.core.data import Posting

from beancount_hooks.entries import map_transactions
from beancount_hooks.index import LedgerIndex
from beancount_hooks.utils import allocate, get_amount_and_sign, source_units

if TYPE_CHECKING:
    from beancount.core.data import Transaction
    from beangulp.importer import Importer

logger = logging.getLogger(__name__)

# An account to fill in, and what fraction of the amount goes to it.  ``None`` means the whole
# balance, left to Beancount to interpolate — the only option when there is a single leg.
PredictedLeg: TypeAlias = 'tuple[str, Decimal | None]'


def _make_cache_key(entries: list) -> int:
    """A stable cache key derived from ledger contents."""
    if not entries:
        return 0
    return hash((id(entries[0]), id(entries[-1]), len(entries)))


class RulesPostingsPredictor:
    """Predict missing posting accounts using a decision ladder.

    The importer already provides one leg (e.g. ``Assets:Bank:CHF``).
    This predictor attempts to fill the *other* leg(s).

    Rungs 1 to 4 all key on something specific to the transaction.  Rung 5 does not — see
    ``enable_rule_5``, which is off by default.

    Each rung asks about history funded from the account being imported, not about the payee in
    general, so a subscription that moved between cards is answered by the card it is on now.

    Where a rung answers with several legs, the amount has to be divided between them, since
    Beancount interpolates only one posting per currency.  That is offered only where *this
    payee* divides it the same way every time, within ``split_tolerance`` — a purchase halved
    with a partner.  The same three accounts on a different payee are a different habit.
    Proportions that move, such as an insurance premium renegotiated each year, leave the
    legs blank instead.
    """

    def __init__(
        self,
        min_occurrence: int = 3,
        amount_bin_size: float = 10.0,
        enable_rule_5: bool = False,
        split_tolerance: Decimal = Decimal('0.01'),
    ) -> None:
        self.min_occurrence = min_occurrence
        self.amount_bin_size = amount_bin_size
        self.enable_rule_5 = enable_rule_5
        self.split_tolerance = split_tolerance
        self._cache: tuple[int | None, LedgerIndex | None] = (None, None)

    # ------------------------------------------------------------------
    # Beangulp hook API
    # ------------------------------------------------------------------

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, Importer]],
        existing_entries: list,
    ) -> list[tuple[str, list, str, Importer]]:
        """Beangulp hook — mutates imported entries in-place.

        The ``Importer`` in the signature is load-bearing under Fava; see
        :mod:`beancount_hooks.entries`.

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
                'RulesPostingsPredictor: failed to build LedgerIndex, passing through.',
                exc_info=True,
            )
            return imported_entries

        def fill(entry: Transaction, account: str | None) -> Transaction:
            predicted = self._predict(entry, account, index)
            if not predicted:
                return entry
            return self._apply_prediction(entry, predicted)

        return map_transactions(imported_entries, fill, label='RulesPostingsPredictor')

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
        # Nothing to fill: the importer or a rule already supplied the other leg.  Adding
        # another one would give Beancount a second posting with no amount, which it
        # rejects with "You may not have more than one auto-posting per currency".
        if len([p for p in entry.postings if p.account]) > 1:
            return None

        payee = entry.payee or ''
        narration = entry.narration or ''

        # Helper: only keep accounts that are NOT the importer account.
        def _filter_importer(accounts: list[str] | None) -> list[PredictedLeg] | None:
            if accounts is None:
                return None
            if not (importer_account and importer_account in accounts):
                # Predicted set doesn't include importer account → mismatch, skip.
                return None
            filtered = [a for a in accounts if a != importer_account]
            if not filtered:
                return None
            if len(filtered) == 1:
                # One leg, no amount: Beancount works it out from the leg the importer gave.
                return [(filtered[0], None)]

            # More than one leg to fill, and Beancount accepts only one posting without an
            # amount — so the only way to offer these is to say how much goes where.  That is
            # answerable when the division is a habit: a purchase halved with a partner is
            # always halved.  When it is not, the legs stay blank for review.
            shares = self.index_split_shares(accounts, importer_account, index, payee)
            if shares is None:
                logger.debug(
                    'RulesPostingsPredictor: %s splits across %s in no settled proportion, '
                    'leaving the leg blank.',
                    payee or narration or '<unknown>',
                    filtered,
                )
                return None
            return [(account, fraction) for account, fraction in shares]

        # Every rung asks only about history funded from the account being imported, so a payee
        # that changed cards is answered by the card it is on now rather than the one it left.

        # Rule 1: Exact payee match.
        result = _filter_importer(
            index.get_accounts_by_payee(payee, self.min_occurrence, importer_account)
        )
        if result:
            return result

        # Rule 2: Normalized payee match.
        result = _filter_importer(
            index.get_accounts_by_normalized_payee(payee, self.min_occurrence, importer_account)
        )
        if result:
            return result

        # Rule 3: Narration keyword match.
        result = _filter_importer(
            index.get_accounts_by_keyword(narration, self.min_occurrence, importer_account)
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
                    payee,
                    amount,
                    sign,
                    self.amount_bin_size,
                    self.min_occurrence,
                    importer_account,
                )
            )
            if result:
                return result

        # Rule 5: the account's most frequent counterpart, ignoring this transaction
        # entirely.  Off by default: on a everyday-spending account the answer is whatever
        # you buy most, so every unrecognised payee gets that account — health insurance,
        # a card settlement and a tax refund all booked as restaurant meals.  A blank leg
        # is visible at review; a plausible wrong one is not.
        if self.enable_rule_5 and importer_account:
            counterpart = index.get_counterpart(importer_account, min_count=10)
            if counterpart:
                return [(counterpart, None)]

        return None

    def index_split_shares(
        self,
        accounts: list[str],
        importer_account: str,
        index: LedgerIndex,
        payee: str,
    ) -> list[tuple[str, Decimal]] | None:
        """The settled proportions for *payee* on *accounts*, or None if there are none."""
        return index.get_split_shares(
            frozenset(accounts),
            importer_account,
            payee,
            self.min_occurrence,
            self.split_tolerance,
        )

    def _apply_prediction(self, entry: Transaction, predicted: list[PredictedLeg]) -> Transaction:
        """Return a new Transaction with predicted postings added.

        A single leg carries no units, so Beancount interpolates the amount and currency from
        what the importer supplied — an explicit amount there would either unbalance the
        transaction or hardcode a currency a multi-currency import does not use.

        Several legs cannot all be left to interpolation, so they come with amounts worked out
        from the fractions the ledger has settled on.
        """
        existing_accounts = {p.account for p in entry.postings if p.account}
        pending = [(a, f) for a, f in predicted if a not in existing_accounts]
        if not pending:
            return entry

        if all(fraction is None for _account, fraction in pending):
            return entry._replace(
                postings=[
                    *entry.postings,
                    *(
                        Posting(account, None, None, None, None, None)
                        for account, _fraction in pending
                    ),
                ]
            )

        units = source_units(entry)
        if units is None:
            return entry
        shares = [(account, fraction) for account, fraction in pending if fraction is not None]
        return entry._replace(postings=[*entry.postings, *allocate(units, shares)])


class RulesPayeePredictor:
    """Fill in a missing payee by reading the narration.

    This predictor never rewrites a payee that is already set.  Canonicalising display
    names ("SBBCFFFFS" → "SBB CFF FFS") is a job for an explicit rule, not a guess:
    :func:`~beancount_hooks.normalizer.normalize_payee` produces lookup keys such as
    ``sbb`` or ``migros``, which are not names anyone wants in their ledger.
    """

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, Importer]],
        existing_entries: list,
    ) -> list[tuple[str, list, str, Importer]]:
        """Derive a payee from the narration where the importer supplied none."""
        return map_transactions(
            imported_entries,
            lambda entry, _account: self._predict(entry),
            label='RulesPayeePredictor',
        )

    def _predict(self, entry: Transaction) -> Transaction:
        """Return *entry* with a derived payee, or unchanged."""
        if entry.payee:
            return entry
        narration = entry.narration or ''
        if not narration:
            return entry
        derived = self._derive_from_narration(narration)
        return entry._replace(payee=derived) if derived else entry

    def _derive_from_narration(self, narration: str) -> str | None:
        """Extract a payee from narration heuristics."""
        lower = narration.lower().strip()

        # "Salary from X" → X
        m = re.search(r'salary\s+from\s+(.+)', lower)
        if m:
            return m.group(1).strip().title()

        # "Invoice.*from X" → X
        m = re.search(r'invoice.*from\s+(.+)', lower)
        if m:
            return m.group(1).strip().title()

        # "Transfer to X" → X
        m = re.search(r'transfer\s+to\s+(.+)', lower)
        if m:
            return m.group(1).strip().title()

        # "Payment to X" → X
        m = re.search(r'payment\s+to\s+(.+)', lower)
        if m:
            return m.group(1).strip().title()

        # "X GmbH / X AG" trailing
        m = re.search(r'([\w\s]+(?:\s+gmbh|\s+ag|\s+sa|\s+sarl))', lower)
        if m:
            return m.group(1).strip().title()

        return None


class RulesTagsPredictor:
    """Add tags a payee has historically carried, on top of any already present.

    A tag has to be true of the payee *most* of the time to be worth adding, which is what
    ``min_tag_share`` asks — see :meth:`LedgerIndex.get_tags`.  Without it a payee that appears
    hundreds of times collects a tag from any handful of them, and a placeholder payee such as
    "self" collects the tags of every unrelated transfer sharing the name.

    A share cannot catch a tag that names one occasion, though: a payee seen only during one
    trip carries that trip's tag every time, and predicting it onto something new is wrong
    however consistent it was.  Left as is deliberately — such payees tend to be specific to
    the occasion, so the tag is usually re-applied by hand anyway.
    """

    def __init__(
        self,
        min_tag_occurrence: int = 5,
        max_tags: int = 3,
        min_tag_share: float = 0.6,
    ) -> None:
        self.min_tag_occurrence = min_tag_occurrence
        self.max_tags = max_tags
        self.min_tag_share = min_tag_share
        self._cache: tuple[int | None, LedgerIndex | None] = (None, None)

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, Importer]],
        existing_entries: list,
    ) -> list[tuple[str, list, str, Importer]]:
        """Add predicted tags to ``entry.tags``."""
        try:
            index = self._get_or_build_index(existing_entries)
        except Exception:
            logger.warning(
                'RulesTagsPredictor: failed to build LedgerIndex, passing through.',
                exc_info=True,
            )
            return imported_entries

        def tag(entry: Transaction, _account: str | None) -> Transaction:
            predicted = self._predict(entry, index)
            if not predicted:
                return entry
            return entry._replace(tags=entry.tags | frozenset(predicted))

        return map_transactions(imported_entries, tag, label='RulesTagsPredictor')

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
        """Return the tags this payee has historically carried."""
        tags = index.get_tags(entry.payee or '', self.min_tag_occurrence, self.min_tag_share)
        return tags[: self.max_tags]

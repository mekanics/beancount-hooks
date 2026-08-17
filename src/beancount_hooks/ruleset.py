"""Declarative import rules — the deterministic rung of the decision ladder.

The predictors in :mod:`beancount_hooks.rules` guess from history.  A ``Ruleset`` states
facts: *this payee is always that account*.  Rules run first and, because a predictor only
ever fills a blank field, whatever a rule sets is left alone afterwards.

Rules are data rather than code, which is what makes :meth:`Ruleset.explain` and
:meth:`Ruleset.shadowed` possible — an ordering mistake becomes a test failure instead of
a comment asking the next reader to be careful.

Pure Python, stdlib only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, TypeAlias

from beancount.core.amount import Amount
from beancount.core.data import Posting

from beancount_hooks.entries import map_transactions
from beancount_hooks.utils import allocate, balancing_units, source_units

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from beancount.core.data import Transaction
    from beangulp.importer import Importer

logger = logging.getLogger(__name__)

Sign = Literal['debit', 'credit']

# How far a set of split fractions may drift from 1 before it counts as a mistake.
_SPLIT_TOLERANCE = Decimal('0.0001')

# An account to post to, optionally with an explicit amount.  Without one, Beancount
# interpolates the amount from the other legs.
PostSpec: TypeAlias = 'str | tuple[str, Amount]'


def _needles(value: str | Sequence[str]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def _contains_all(haystack: str | None, needles: tuple[str, ...]) -> bool:
    """True when every needle occurs in *haystack*, ignoring case.

    A null haystack matches nothing — a transaction without a payee is not a match, it is
    not a crash.
    """
    if haystack is None:
        return False
    lowered = haystack.lower()
    return all(needle.lower() in lowered for needle in needles)


def _contains_any(haystack: str | None, needles: tuple[str, ...]) -> bool:
    """True when at least one needle occurs in *haystack*, ignoring case."""
    if haystack is None:
        return False
    lowered = haystack.lower()
    return any(needle.lower() in lowered for needle in needles)


__all__ = ['Actions', 'Match', 'Rule', 'Ruleset']


@dataclass(frozen=True)
class Match:
    """When a rule applies.

    Every criterion given must hold.  ``payee`` and ``narration`` are case-insensitive
    substring tests; pass a tuple to require several substrings at once, which is how
    "Coop" plus "Mineraloel" distinguishes a filling station from a supermarket.

    ``payee_any`` holds when *any one* of its substrings is present — for the many payees
    that are one thing under several spellings, such as "Galaxus" and "Digitec".  Give
    both ``payee`` and ``payee_any`` and both must hold.

    Amounts are **absolute values**, with direction expressed separately through ``sign``.
    Comparing a signed amount against a positive literal is how a subscription rule ends
    up matching only refunds.
    """

    payee: str | Sequence[str] | None = None
    payee_any: Sequence[str] | None = None
    narration: str | Sequence[str] | None = None
    account: str | None = None
    currency: str | None = None
    amount: Decimal | None = None
    amount_lt: Decimal | None = None
    amount_gt: Decimal | None = None
    sign: Sign | None = None
    when: Callable[[Transaction], bool] | None = None

    def __post_init__(self) -> None:
        if self.sign is not None and self.sign not in ('debit', 'credit'):
            raise ValueError(f"sign must be 'debit' or 'credit', got {self.sign!r}")
        for name in ('amount', 'amount_lt', 'amount_gt'):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(
                    f'{name} is an absolute value and cannot be negative (got {value}); '
                    "use sign='debit' or sign='credit' for direction"
                )
        if not self._criteria and self.when is None:
            raise ValueError('Match needs at least one criterion, otherwise it matches everything')

    @property
    def _criteria(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                'payee',
                'payee_any',
                'narration',
                'account',
                'currency',
                'amount',
                'amount_lt',
                'amount_gt',
                'sign',
            )
            if getattr(self, name) is not None
        )

    def test(self, txn: Transaction, importer_account: str | None = None) -> bool:
        """True when *txn* satisfies every criterion."""
        if self.account is not None and (
            importer_account is None or not importer_account.startswith(self.account)
        ):
            return False
        if self.payee is not None and not _contains_all(txn.payee, _needles(self.payee)):
            return False
        if self.payee_any is not None and not _contains_any(txn.payee, _needles(self.payee_any)):
            return False
        if self.narration is not None and not _contains_all(
            txn.narration, _needles(self.narration)
        ):
            return False

        if self._needs_units:
            units = source_units(txn)
            if units is None:
                return False
            if self.currency is not None and units.currency != self.currency:
                return False
            if not self._test_amount(units.number):
                return False

        return self.when is None or bool(self.when(txn))

    @property
    def _needs_units(self) -> bool:
        return any(
            getattr(self, name) is not None
            for name in ('currency', 'amount', 'amount_lt', 'amount_gt', 'sign')
        )

    def _test_amount(self, value: Decimal) -> bool:
        # A debit moves money out of the account the importer covers.  Zero counts as a
        # credit, matching how the ledger has always classified it.
        if self.sign == 'debit' and value >= 0:
            return False
        if self.sign == 'credit' and value < 0:
            return False
        magnitude = abs(value)
        if self.amount is not None and magnitude != self.amount:
            return False
        if self.amount_lt is not None and magnitude >= self.amount_lt:
            return False
        return not (self.amount_gt is not None and magnitude <= self.amount_gt)

    def describe(self) -> str:
        """A short label used when a rule is not given an explicit name."""
        parts = []
        for name in self._criteria:
            value = getattr(self, name)
            if name in ('payee', 'narration'):
                rendered = '+'.join(_needles(value))
            elif name == 'payee_any':
                rendered = '|'.join(_needles(value))
            else:
                rendered = value
            parts.append(f'{name}={rendered}')
        if self.when is not None:
            parts.append('when=<callable>')
        return ' '.join(parts)


@dataclass(frozen=True)
class Actions:
    """What a rule does to a matched transaction.

    ``post`` adds legs.  An account on its own gets no amount, so Beancount interpolates
    it; pair it with an :class:`~beancount.core.amount.Amount` to pin one down, as a rent
    payment does with its fixed utilities share.

    ``split`` divides the balancing amount across accounts by fraction — the shape of an
    income stream shared with someone else.  The fractions must total exactly 1, and the
    final account absorbs any rounding remainder so the transaction still balances.
    """

    payee: str | None = None
    narration: str | None = None
    post: str | Sequence[PostSpec] = ()
    split: Sequence[tuple[str, Decimal]] = ()
    tags: Sequence[str] = ()
    links: Sequence[str] = ()
    flag: str | None = None
    drop: bool = False

    def __post_init__(self) -> None:
        if self.drop and self._does_anything_else:
            raise ValueError('drop=True cannot be combined with other actions')
        if not self.drop and not self._does_anything_else:
            raise ValueError('Actions does nothing')

        bare = [spec for spec in self._post_specs if isinstance(spec, str)]
        if len(bare) > 1:
            raise ValueError(
                f'only one posting may be left without an amount for Beancount to '
                f'interpolate, got {len(bare)}: {bare}'
            )

        if self.split:
            fractions = [fraction for _account, fraction in self.split]
            if any(fraction <= 0 for fraction in fractions):
                raise ValueError(f'split fractions must be positive, got {fractions}')
            total = sum(fractions, Decimal(0))
            # Repeating fractions such as thirds cannot sum to exactly 1, and the last
            # account absorbs the rounding remainder anyway.  The check is here to catch
            # a typo like (0.5, 0.2), not to enforce exact arithmetic.
            if abs(total - 1) > _SPLIT_TOLERANCE:
                raise ValueError(f'split fractions must total 1, got {total}')

    @property
    def _does_anything_else(self) -> bool:
        return bool(
            self.payee
            or self.narration
            or self.post
            or self.split
            or self.tags
            or self.links
            or self.flag
        )

    @property
    def _post_specs(self) -> tuple[PostSpec, ...]:
        if isinstance(self.post, str):
            return (self.post,)
        return tuple(self.post)

    def accounts(self) -> frozenset[str]:
        """Every account this rule can post to."""
        from_post = {spec if isinstance(spec, str) else spec[0] for spec in self._post_specs}
        return frozenset(from_post | {account for account, _fraction in self.split})

    def apply_to(self, txn: Transaction) -> Transaction:
        """Return a new Transaction with these actions applied.

        The original is never modified — not even its postings list, which is shared by
        every shallow copy of a Transaction.
        """
        changes: dict[str, object] = {}
        if self.payee:
            changes['payee'] = self.payee
        if self.narration:
            changes['narration'] = self.narration
        if self.flag:
            changes['flag'] = self.flag
        if self.tags:
            changes['tags'] = txn.tags | frozenset(self.tags)
        if self.links:
            changes['links'] = txn.links | frozenset(self.links)

        postings = self._build_postings(txn)
        if postings is not None:
            changes['postings'] = postings

        return txn._replace(**changes) if changes else txn

    def _build_postings(self, txn: Transaction) -> list[Posting] | None:
        if not self.post and not self.split:
            return None

        # A transaction that already balances, or that already carries an auto-posting,
        # has nothing left for a rule to fill.  Completeness is the residual, not the
        # account count: a bank leg plus a fee leg is two accounts and still incomplete.
        if balancing_units(txn) is None:
            logger.debug(
                'Actions: %s already balances or has an auto-posting, adding no postings.',
                txn.narration or txn.payee or '<unknown>',
            )
            return None

        present = {p.account for p in txn.postings if p.account}
        # Declared fractions assume the whole residual is theirs.  That is only true when
        # the entry still carries a single account — judged on the incoming transaction,
        # before this rule's own post specs are added.
        allow_split = len(present) == 1
        postings = list(txn.postings)

        for spec in self._post_specs:
            account, units = (spec, None) if isinstance(spec, str) else spec
            if account in present:
                continue
            present.add(account)
            postings.append(Posting(account, units, None, None, None, None))

        if allow_split:
            postings.extend(self._split_postings(txn, present))
        return postings

    def _split_postings(self, txn: Transaction, present: set[str]) -> list[Posting]:
        if not self.split:
            return []
        total = balancing_units(txn)
        if total is None:
            return []

        pending = [(a, f) for a, f in self.split if a not in present]
        if not pending:
            return []

        present.update(account for account, _fraction in pending)
        return allocate(total, pending)


@dataclass(frozen=True)
class Rule:
    """A :class:`Match` paired with the :class:`Actions` it triggers."""

    match: Match
    actions: Actions
    name: str = ''

    @property
    def label(self) -> str:
        return self.name or self.match.describe()


class Ruleset:
    """Applies the first matching rule to each imported transaction.

    Precedence is source order: the first rule whose :class:`Match` holds wins, which is
    the same semantics as the ``if`` / ``elif`` chain this replaces.  Put specific rules
    above general ones and let :meth:`shadowed` prove you did.

    ``accounts`` limits the ruleset to the importers whose account starts with one of the
    given prefixes.  Rules written against one bank's payee strings are rarely safe to run
    over a credit card that already assigns its own accounts, and a ruleset that only
    applies where it was meant to is easier to reason about than sixteen rules each
    repeating the same ``Match(account=...)``.
    """

    def __init__(
        self,
        rules: Iterable[Rule],
        *,
        label: str = 'Ruleset',
        accounts: Sequence[str] | None = None,
    ) -> None:
        self.rules = tuple(rules)
        self.label = label
        self.account_prefixes = tuple(accounts) if accounts is not None else None
        duplicates = self._duplicate_labels()
        if duplicates:
            raise ValueError(f'rule labels must be unique, repeated: {sorted(duplicates)}')

    def _duplicate_labels(self) -> set[str]:
        seen: set[str] = set()
        repeated: set[str] = set()
        for rule in self.rules:
            if rule.label in seen:
                repeated.add(rule.label)
            seen.add(rule.label)
        return repeated

    # ------------------------------------------------------------------
    # Beangulp hook API
    # ------------------------------------------------------------------

    def hook(
        self,
        imported_entries: list[tuple[str, list, str, Importer]],
        existing_entries: list | None = None,
    ) -> list[tuple[str, list, str, Importer]]:
        """Apply the ruleset to every imported transaction.

        *existing_entries* is accepted for the beangulp hook signature and ignored — a
        ruleset states facts and needs no history to do it.

        The ``Importer`` in the signature is load-bearing under Fava; see
        :mod:`beancount_hooks.entries`.
        """
        return map_transactions(imported_entries, self.apply, label=self.label)

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def apply(self, txn: Transaction, importer_account: str | None = None) -> Transaction | None:
        """Return *txn* with the first matching rule applied, or None if a rule drops it."""
        rule = self.match(txn, importer_account)
        if rule is None:
            return txn
        if rule.actions.drop:
            logger.info(
                '%s: rule %s dropped %s / %s',
                self.label,
                rule.label,
                txn.payee or '<no payee>',
                txn.narration or '<no narration>',
            )
            return None
        return rule.actions.apply_to(txn)

    def match(self, txn: Transaction, importer_account: str | None = None) -> Rule | None:
        """The first rule that matches *txn*, or None."""
        if not self.covers(importer_account):
            return None
        for rule in self.rules:
            if rule.match.test(txn, importer_account):
                return rule
        return None

    def covers(self, importer_account: str | None) -> bool:
        """Whether this ruleset applies to *importer_account* at all.

        An unrestricted ruleset covers everything.  A restricted one covers only its
        prefixes, and declines an unknown account rather than guessing.
        """
        if self.account_prefixes is None:
            return True
        if importer_account is None:
            return False
        return importer_account.startswith(self.account_prefixes)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def explain(self, txn: Transaction, importer_account: str | None = None) -> list[str]:
        """Labels of every rule matching *txn*, in precedence order.

        More than one entry means the rules below the first are unreachable for this
        transaction — useful when a rule you expected to fire did not.
        """
        if not self.covers(importer_account):
            return []
        return [rule.label for rule in self.rules if rule.match.test(txn, importer_account)]

    def accounts(self) -> frozenset[str]:
        """Every account any rule can post to, for checking against the ledger."""
        return frozenset().union(*(rule.actions.accounts() for rule in self.rules))

    def shadowed(self) -> list[tuple[str, str]]:
        """Rules that can never fire because an earlier rule always matches first.

        Returns ``(earlier_label, unreachable_label)`` pairs.  The check is deliberately
        conservative: it reports only provable cases, so an empty list is not a guarantee
        of good ordering, but anything it does report is a real bug.
        """
        found = []
        for index, later in enumerate(self.rules):
            for earlier in self.rules[:index]:
                if _subsumes(earlier.match, later.match):
                    found.append((earlier.label, later.label))
                    break
        return found


def _subsumes(earlier: Match, later: Match) -> bool:
    """True when *earlier* matches everything *later* does, and so hides it.

    Conservative by design.  Anything the checker cannot prove — a callable, an amount
    window, a criterion the later rule leaves open — counts as "not shadowed".
    """
    if earlier.when is not None or later.when is not None:
        return False
    # An amount or direction constraint on the earlier rule narrows it in ways this
    # checker does not model.
    if earlier._needs_units:
        return False

    for name in ('payee', 'narration'):
        mine = getattr(earlier, name)
        if mine is None:
            continue
        theirs = getattr(later, name)
        if theirs is None:
            return False
        mine_lower = {needle.lower() for needle in _needles(mine)}
        theirs_lower = {needle.lower() for needle in _needles(theirs)}
        # Every substring the earlier rule demands must also be demanded later.
        if not mine_lower <= theirs_lower:
            return False

    if earlier.payee_any is not None:
        if later.payee_any is None:
            return False
        # The earlier rule accepts more spellings than the later one, so it takes them all.
        mine_any = {needle.lower() for needle in _needles(earlier.payee_any)}
        theirs_any = {needle.lower() for needle in _needles(later.payee_any)}
        if not theirs_any <= mine_any:
            return False

    for name in ('account', 'currency'):
        mine = getattr(earlier, name)
        if mine is not None and getattr(later, name) != mine:
            return False

    return True

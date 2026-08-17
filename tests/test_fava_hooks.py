"""The contract that lets Fava call these hooks.

Fava supports two hook shapes and chooses per hook by searching the hook's *annotations*
for the literal text ``Importer`` — see ``fava.core.ingest.IngestModule.extract``.  A hook
that fails the check is handed ``(filename, entries)`` and loses the importer account.

Two things then go wrong, and only the first is visible: ``map_transactions`` unpacks four
names and raises, and — were it lenient — an account-scoped ``Ruleset`` would decline every
transaction and quietly do nothing.  These tests pin both, without needing Fava installed.
"""

from __future__ import annotations

import datetime
from inspect import get_annotations

import pytest
from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction, new_metadata
from beancount.core.number import D

from beancount_hooks import (
    Actions,
    Match,
    Rule,
    Ruleset,
    RulesPayeePredictor,
    RulesPostingsPredictor,
    RulesTagsPredictor,
    map_transactions,
)

ACCOUNT = 'Assets:Bank:Checking:CHF'


def _txn(payee: str) -> Transaction:
    return Transaction(
        meta=new_metadata('import.csv', 1),
        date=datetime.date(2026, 8, 13),
        flag='*',
        payee=payee,
        narration='',
        tags=frozenset(),
        links=frozenset(),
        postings=[
            Posting(
                account=ACCOUNT,
                units=Amount(D('-12.50'), 'CHF'),
                cost=None,
                price=None,
                flag=None,
                meta=None,
            )
        ],
    )


def wants_importer(hook_fn) -> bool:
    """Fava's dispatch predicate, copied from ``fava.core.ingest``."""
    return any('Importer' in a for a in get_annotations(hook_fn).values())


def fava_call(hook_fn, entries: list, existing: list | None = None) -> list:
    """Invoke *hook_fn* the way Fava's IngestModule.extract does.

    Including the branch: a hook that does not advertise ``Importer`` gets the legacy
    2-tuple, which is exactly the path that broke in the ledger.
    """
    if wants_importer(hook_fn):
        payload = [('import.csv', entries, ACCOUNT, object())]
    else:
        payload = [('import.csv', entries)]
    return hook_fn(payload, existing if existing is not None else [])[0][1]


HOOKS = [
    Ruleset([], label='Empty').hook,
    RulesPostingsPredictor().hook,
    RulesPayeePredictor().hook,
    RulesTagsPredictor().hook,
]


class TestDispatchContract:
    @pytest.mark.parametrize('hook_fn', HOOKS, ids=lambda h: h.__qualname__)
    def test_hook_advertises_importer_to_fava(self, hook_fn) -> None:
        assert wants_importer(hook_fn), (
            f'{hook_fn.__qualname__} does not mention Importer in its annotations, so '
            f'Fava will call it with (filename, entries) and no importer account.  '
            f'Annotate the tuple as list[tuple[str, list, str, Importer]] — an alias '
            f'does not work, Fava matches the annotation text.'
        )

    @pytest.mark.parametrize('hook_fn', HOOKS, ids=lambda h: h.__qualname__)
    def test_hook_survives_a_fava_call(self, hook_fn) -> None:
        entries = [_txn('Coop')]
        assert fava_call(hook_fn, entries) is entries


class TestScopedRulesStillFire:
    """The failure the crash was hiding: no account means no scope means no rules."""

    def test_account_scoped_rule_applies_under_fava(self) -> None:
        ruleset = Ruleset(
            [Rule(Match(payee='cafe'), Actions(payee='Cafe'), name='cafe')],
            label='Scoped',
            accounts=('Assets:Bank:Checking',),
        )
        entries = [_txn('cafe ag zurich')]
        fava_call(ruleset.hook, entries)
        assert entries[0].payee == 'Cafe'

    def test_scope_still_excludes_other_accounts(self) -> None:
        ruleset = Ruleset(
            [Rule(Match(payee='cafe'), Actions(payee='Cafe'), name='cafe')],
            label='Scoped',
            accounts=('Assets:Bank:Other',),
        )
        entries = [_txn('cafe ag zurich')]
        fava_call(ruleset.hook, entries)
        assert entries[0].payee == 'cafe ag zurich'


class TestLegacyTupleIsRejected:
    def test_two_tuple_raises_with_an_actionable_message(self) -> None:
        with pytest.raises(ValueError, match='Importer'):
            map_transactions(
                [('import.csv', [_txn('Coop')])],
                lambda txn, _account: txn,
                label='TestHook',
            )

    def test_the_message_names_the_hook(self) -> None:
        with pytest.raises(ValueError, match='TestHook'):
            map_transactions(
                [('import.csv', [_txn('Coop')])],
                lambda txn, _account: txn,
                label='TestHook',
            )

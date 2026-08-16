"""Tests for beancount_hooks.entries.map_transactions."""

from __future__ import annotations

import datetime
import logging

from beancount.core.amount import Amount
from beancount.core.data import Balance, Note, Posting, Transaction, new_metadata
from beancount.core.number import D

from beancount_hooks.entries import map_transactions


def _txn(narration: str, amount: str = '-20') -> Transaction:
    return Transaction(
        meta=new_metadata('import.csv', 1),
        date=datetime.date(2026, 8, 1),
        flag='*',
        payee='Coop',
        narration=narration,
        tags=frozenset(),
        links=frozenset(),
        postings=[
            Posting(
                account='Assets:Bank:CHF',
                units=Amount(D(amount), 'CHF'),
                cost=None,
                price=None,
                flag=None,
                meta=None,
            )
        ],
    )


def _balance(account: str = 'Assets:Bank:CHF') -> Balance:
    return Balance(
        meta=new_metadata('import.csv', 1),
        date=datetime.date(2026, 8, 2),
        account=account,
        amount=Amount(D('100'), 'CHF'),
        tolerance=None,
        diff_amount=None,
    )


def _note(comment: str = 'hello') -> Note:
    return Note(
        meta=new_metadata('import.csv', 1),
        date=datetime.date(2026, 8, 2),
        account='Assets:Bank:CHF',
        comment=comment,
        tags=frozenset(),
        links=frozenset(),
    )


def _imported(entries: list) -> list[tuple[str, list, str, object]]:
    return [('import.csv', entries, 'Assets:Bank:CHF', None)]


def _shape(entries: list) -> list[str]:
    """A readable structural fingerprint of an entry list."""
    out = []
    for entry in entries:
        if isinstance(entry, Transaction):
            out.append(f'Txn({entry.narration})')
        elif isinstance(entry, Balance):
            out.append(f'Balance({entry.account})')
        else:
            out.append(type(entry).__name__)
    return out


class TestPositionPreservation:
    def test_balance_in_the_middle_survives(self) -> None:
        """An IBKR-shaped batch interleaves Balance directives between transactions.

        `beancount_tools_collection.importers.ibkr` returns
        Trades + CashTransactions + Balances + CorporateActions, so a Balance sits
        mid-list.  Enumerating filter_txns() while assigning entries[i] overwrites it.
        """
        entries = [_txn('trade'), _balance(), _txn('corp-action')]
        map_transactions(
            _imported(entries),
            lambda txn, _account: txn._replace(narration=txn.narration + '!'),
            label='test',
        )
        assert _shape(entries) == [
            'Txn(trade!)',
            'Balance(Assets:Bank:CHF)',
            'Txn(corp-action!)',
        ]

    def test_non_transaction_first_survives(self) -> None:
        entries = [_balance(), _txn('a')]
        map_transactions(
            _imported(entries),
            lambda txn, _account: txn._replace(narration='rewritten'),
            label='test',
        )
        assert _shape(entries) == ['Balance(Assets:Bank:CHF)', 'Txn(rewritten)']

    def test_every_transaction_is_visited(self) -> None:
        entries = [_balance(), _txn('a'), _note(), _txn('b'), _balance('Assets:Other')]
        seen: list[str] = []

        def record(txn: Transaction, _account: str | None) -> Transaction:
            seen.append(txn.narration)
            return txn

        map_transactions(_imported(entries), record, label='test')
        assert seen == ['a', 'b']

    def test_importer_account_is_passed_through(self) -> None:
        seen: list[str | None] = []
        map_transactions(
            [('import.csv', [_txn('a')], 'Assets:Bank:CHF', None)],
            lambda txn, account: seen.append(account) or txn,
            label='test',
        )
        assert seen == ['Assets:Bank:CHF']


class TestReplacementSemantics:
    def test_duplicate_transactions_are_replaced_independently(self) -> None:
        """Two value-equal transactions must not collapse onto one slot."""
        entries = [_txn('same'), _txn('same')]
        counter = iter(['first', 'second'])
        map_transactions(
            _imported(entries),
            lambda txn, _account: txn._replace(narration=next(counter)),
            label='test',
        )
        assert _shape(entries) == ['Txn(first)', 'Txn(second)']

    def test_mutates_the_caller_list_in_place(self) -> None:
        """beangulp relies on hooks mutating the entry lists it handed over."""
        entries = [_txn('a')]
        imported = _imported(entries)
        result = map_transactions(
            imported, lambda txn, _account: txn._replace(narration='b'), label='test'
        )
        assert result is imported
        assert result[0][1] is entries
        assert entries[0].narration == 'b'

    def test_returning_the_same_object_changes_nothing(self) -> None:
        original = _txn('a')
        entries = [original]
        map_transactions(_imported(entries), lambda txn, _account: txn, label='test')
        assert entries[0] is original

    def test_multiple_files(self) -> None:
        first, second = [_txn('a')], [_balance(), _txn('b')]
        map_transactions(
            [('a.csv', first, 'Assets:A', None), ('b.csv', second, 'Assets:B', None)],
            lambda txn, _account: txn._replace(narration=txn.narration.upper()),
            label='test',
        )
        assert _shape(first) == ['Txn(A)']
        assert _shape(second) == ['Balance(Assets:Bank:CHF)', 'Txn(B)']


class TestDrop:
    def test_returning_none_drops_the_transaction(self) -> None:
        entries = [_txn('keep'), _txn('drop-me')]
        map_transactions(
            _imported(entries),
            lambda txn, _account: None if txn.narration == 'drop-me' else txn,
            label='test',
        )
        assert _shape(entries) == ['Txn(keep)']

    def test_drop_preserves_surrounding_directives(self) -> None:
        entries = [_balance(), _txn('drop-me'), _note(), _txn('keep')]
        map_transactions(
            _imported(entries),
            lambda txn, _account: None if txn.narration == 'drop-me' else txn,
            label='test',
        )
        assert _shape(entries) == ['Balance(Assets:Bank:CHF)', 'Note', 'Txn(keep)']

    def test_dropping_every_transaction_leaves_other_directives(self) -> None:
        entries = [_txn('a'), _balance(), _txn('b')]
        map_transactions(_imported(entries), lambda _txn, _account: None, label='test')
        assert _shape(entries) == ['Balance(Assets:Bank:CHF)']


class TestErrorHandling:
    def test_exception_leaves_the_entry_untouched(self, caplog) -> None:
        original = _txn('boom')
        entries = [original, _txn('fine')]

        def explode(txn: Transaction, _account: str | None) -> Transaction:
            if txn.narration == 'boom':
                raise ValueError('rule blew up')
            return txn._replace(narration='ok')

        with caplog.at_level(logging.WARNING):
            map_transactions(_imported(entries), explode, label='TestHook')

        assert entries[0] is original
        assert entries[1].narration == 'ok'
        assert 'TestHook' in caplog.text

    def test_empty_inputs(self) -> None:
        assert map_transactions([], lambda txn, _a: txn, label='test') == []
        entries: list = []
        map_transactions(_imported(entries), lambda txn, _a: txn, label='test')
        assert entries == []

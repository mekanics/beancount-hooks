"""Tests for beancount_hooks.index.LedgerIndex."""

from __future__ import annotations

import datetime
from collections import Counter

from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction, new_metadata
from beancount.core.number import D

from beancount_hooks.index import LedgerIndex
from beancount_hooks.normalizer import normalize_payee


def _tagged_txn(payee: str, tags: frozenset[str]) -> Transaction:
    """A one-payee transaction, for asking what share of them carries a tag."""
    return Transaction(
        new_metadata('ledger.bean', 1),
        datetime.date(2026, 1, 1),
        '*',
        payee,
        '',
        tags,
        frozenset(),
        [
            Posting('Assets:Bank:CHF', Amount(D('-10.00'), 'CHF'), None, None, None, None),
            Posting('Expenses:Other', Amount(D('10.00'), 'CHF'), None, None, None, None),
        ],
    )


class TestLedgerIndexInit:
    def test_empty_ledger(self, ledger_empty) -> None:
        idx = LedgerIndex(ledger_empty)
        assert idx.payee_map == {}
        assert idx.normalized_payee_map == {}
        assert idx.keyword_map == {}
        assert idx.amount_map == {}
        assert idx.cooccur_map == {}
        assert idx.tag_map == {}

    def test_single_txn(self, single_txn) -> None:
        idx = LedgerIndex([single_txn])
        assert 'Migros' in idx.payee_map
        assert idx.payee_map['Migros'][frozenset({'Assets:Bank:CHF', 'Expenses:Groceries'})] == 1

        # Normalized payee.
        norm = normalize_payee('Migros')
        assert norm in idx.normalized_payee_map

        # Keywords (must appear ≥3 times to survive pruning — single txns get pruned).
        assert isinstance(idx.keyword_map, dict)

        # Amount.
        assert ('Migros', 130.0, -1) in idx.amount_map

        # Co-occurrence.
        assert 'Assets:Bank:CHF' in idx.cooccur_map
        assert 'Expenses:Groceries' in idx.cooccur_map

        # Tags (now keyed by normalized payee only).
        assert 'migros' in idx.tag_map
        assert 'food' in idx.tag_map.get('migros', Counter())


class TestGetAccountsByPayee:
    def test_exact_match(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_accounts_by_payee('Migros', min_count=3)
        assert result is not None
        assert 'Expenses:Groceries' in result

    def test_below_threshold(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        # Coop only appears 4 times.
        result = idx.get_accounts_by_payee('Coop', min_count=5)
        assert result is None

    def test_no_match(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_accounts_by_payee('Unknown', min_count=1)
        assert result is None


class TestAskingAboutOneAccount:
    """A payee that changed cards has two shapes, and lifetime count favours the one it left.

    Asking without naming an account gets the retired card, which is the honest answer to the
    question but useless to an importer reading the new one.
    """

    def test_the_old_card_wins_on_count(self, ledger_changed_card) -> None:
        idx = LedgerIndex(ledger_changed_card)
        assert idx.get_accounts_by_payee('Nespresso', min_count=3) == [
            'Assets:Card:Old',
            'Expenses:Coffee',
        ]

    def test_naming_the_new_card_finds_its_own_history(self, ledger_changed_card) -> None:
        idx = LedgerIndex(ledger_changed_card)
        assert idx.get_accounts_by_payee(
            'Nespresso', min_count=3, containing='Assets:Card:New'
        ) == ['Assets:Card:New', 'Expenses:Coffee']

    def test_an_account_with_too_little_behind_it_still_declines(self, ledger_changed_card) -> None:
        """Narrowing picks a shape; it does not lower the bar for one."""
        assert (
            LedgerIndex(ledger_changed_card).get_accounts_by_payee(
                'Nespresso', min_count=5, containing='Assets:Card:New'
            )
            is None
        )

    def test_an_account_the_payee_never_used_declines(self, ledger_changed_card) -> None:
        assert (
            LedgerIndex(ledger_changed_card).get_accounts_by_payee(
                'Nespresso', min_count=1, containing='Assets:Card:Unrelated'
            )
            is None
        )

    def test_the_normalized_and_amount_rungs_narrow_too(self, ledger_changed_card) -> None:
        idx = LedgerIndex(ledger_changed_card)
        assert idx.get_accounts_by_normalized_payee(
            'Nespresso', min_count=3, containing='Assets:Card:New'
        ) == ['Assets:Card:New', 'Expenses:Coffee']
        assert idx.get_accounts_by_amount(
            'Nespresso', 25.0, -1, min_count=3, containing='Assets:Card:New'
        ) == ['Assets:Card:New', 'Expenses:Coffee']

    def test_the_keyword_rung_narrows_too(self, ledger_changed_card) -> None:
        idx = LedgerIndex(ledger_changed_card)
        assert idx.get_accounts_by_keyword(
            'Coffee capsules', min_count=3, containing='Assets:Card:New'
        ) == ['Assets:Card:New', 'Expenses:Coffee']


class TestGetAccountsByNormalizedPayee:
    def test_normalized_match(self, ledger_normalized_payees) -> None:
        idx = LedgerIndex(ledger_normalized_payees)
        # All variants normalize to "migros", 5 total occurrences.
        result = idx.get_accounts_by_normalized_payee('Migros Zürich', min_count=3)
        assert result is not None
        assert 'Expenses:Groceries' in result

    def test_below_threshold(self, ledger_normalized_payees) -> None:
        idx = LedgerIndex(ledger_normalized_payees)
        # Require 10 occurrences, only 5 exist.
        result = idx.get_accounts_by_normalized_payee('Migros', min_count=10)
        assert result is None


class TestGetAccountsByKeyword:
    def test_keyword_match(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_accounts_by_keyword('Weekly groceries', min_count=3)
        assert result is not None
        assert 'Expenses:Groceries' in result

    def test_no_keyword(self, ledger_empty) -> None:
        idx = LedgerIndex(ledger_empty)
        result = idx.get_accounts_by_keyword('anything', min_count=1)
        assert result is None


class TestGetAccountsByAmount:
    def test_amount_match(self, ledger_amount_patterns) -> None:
        idx = LedgerIndex(ledger_amount_patterns)
        result = idx.get_accounts_by_amount('Landlord AG', 1800.0, -1, min_count=3)
        assert result is not None
        assert 'Expenses:Housing:Rent' in result

    def test_amount_no_match(self, ledger_amount_patterns) -> None:
        idx = LedgerIndex(ledger_amount_patterns)
        result = idx.get_accounts_by_amount('Landlord AG', 999.0, -1, min_count=1)
        assert result is None


class TestGetCounterpart:
    def test_counterpart(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_counterpart('Assets:Bank:CHF', min_count=5)
        assert result is not None
        # The most common counterpart to Assets:Bank:CHF is Expenses:Groceries.
        assert result == 'Expenses:Groceries'

    def test_below_threshold(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_counterpart('Assets:Bank:CHF', min_count=100)
        assert result is None

    def test_unknown_account(self, ledger_empty) -> None:
        idx = LedgerIndex(ledger_empty)
        result = idx.get_counterpart('Assets:Bank:CHF', min_count=1)
        assert result is None


class TestGetTags:
    def test_tag_match(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        # get_tags now uses normalized payee only (no account_set param).
        result = idx.get_tags('Landlord AG', min_count=5)
        assert 'housing' in result
        assert 'recurring' in result

    def test_below_threshold(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_tags('Landlord AG', min_count=20)
        assert result == []

    def test_no_payee(self, ledger_no_payee) -> None:
        idx = LedgerIndex(ledger_no_payee)
        result = idx.get_tags('', min_count=1)
        assert result == []

    def test_a_tag_on_a_minority_of_a_payees_transactions_is_not_a_property_of_it(self) -> None:
        """Five occurrences out of a hundred is a leftover from one trip, not a payee's habit.

        A placeholder payee is the sharp case: "self" names every transfer between one's own
        accounts, so the count alone lets a holiday tag from a few of them attach to all.
        """
        history = [
            _tagged_txn('self', frozenset({'travel', 'philippines-2024'}) if i < 6 else frozenset())
            for i in range(100)
        ]
        idx = LedgerIndex(history)
        assert sorted(idx.get_tags('self', min_count=5)) == ['philippines-2024', 'travel']
        assert idx.get_tags('self', min_count=5, min_share=0.6) == []

    def test_a_tag_the_payee_usually_carries_still_qualifies(self) -> None:
        history = [
            _tagged_txn('FelFel', frozenset({'business'}) if i < 28 else frozenset())
            for i in range(30)
        ]
        idx = LedgerIndex(history)
        assert idx.get_tags('FelFel', min_count=5, min_share=0.6) == ['business']

    def test_share_counts_untagged_transactions_in_the_denominator(self) -> None:
        """The bug this guards: tag_map only ever saw transactions that had a tag."""
        history = [_tagged_txn('Migros', frozenset({'household'})) for _ in range(9)]
        history += [_tagged_txn('Migros', frozenset()) for _ in range(91)]
        idx = LedgerIndex(history)
        assert idx.payee_total[normalize_payee('Migros')] == 100
        assert idx.get_tags('Migros', min_count=5, min_share=0.6) == []

    def test_multi_leg(self, ledger_multi_leg) -> None:
        idx = LedgerIndex(ledger_multi_leg)
        # Should index the 3-leg transaction without error.
        accounts = frozenset({'Assets:Bank:CHF', 'Expenses:Food', 'Liabilities:Friend'})
        # Check that the multi-leg accounts appear in payee_map
        assert accounts in set(idx.payee_map.get('Split Bill', Counter()).keys())

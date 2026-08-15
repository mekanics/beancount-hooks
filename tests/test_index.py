"""Tests for beancount_hooks.index.LedgerIndex."""

from __future__ import annotations

from collections import Counter

from beancount_hooks.index import LedgerIndex
from beancount_hooks.normalizer import normalize_payee


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
        assert '#food' in idx.tag_map.get('migros', Counter())


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
        assert '#housing' in result
        assert '#recurring' in result

    def test_below_threshold(self, ledger_multi_payee) -> None:
        idx = LedgerIndex(ledger_multi_payee)
        result = idx.get_tags('Landlord AG', min_count=20)
        assert result == []

    def test_no_payee(self, ledger_no_payee) -> None:
        idx = LedgerIndex(ledger_no_payee)
        result = idx.get_tags('', min_count=1)
        assert result == []

    def test_multi_leg(self, ledger_multi_leg) -> None:
        idx = LedgerIndex(ledger_multi_leg)
        # Should index the 3-leg transaction without error.
        accounts = frozenset({'Assets:Bank:CHF', 'Expenses:Food', 'Liabilities:Friend'})
        # Check that the multi-leg accounts appear in payee_map
        assert accounts in set(idx.payee_map.get('Split Bill', Counter()).keys())

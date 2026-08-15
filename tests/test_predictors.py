"""Tests for beancount_hooks.rules predictors."""

from __future__ import annotations

import datetime

from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction, new_metadata, filter_txns
from beancount.core.number import D, ZERO

from beancount_hooks.rules import (
    RulesPayeePredictor,
    RulesPostingsPredictor,
    RulesTagsPredictor,
)


def _make_imported(
    payee: str | None,
    narration: str,
    importer_account: str = "Assets:Bank:CHF",
    amounts: list[str] | None = None,
    accounts: list[str] | None = None,
) -> list[tuple[str, list, str, object]]:
    """Build a single-file import entry tuple."""
    meta = new_metadata("import.csv", 1)
    if amounts is None:
        amounts = ["-100"]
    postings = []
    for i, amt in enumerate(amounts):
        if accounts and i < len(accounts):
            account = accounts[i]
        elif i == 0:
            account = importer_account
        else:
            # Second posting (if present) is a placeholder — will be replaced.
            account = "UNKNOWN"
        postings.append(
            Posting(
                account=account,
                units=Amount(D(amt), "CHF"),
                cost=None,
                price=None,
                flag=None,
                meta=None,
            )
        )
    txn = Transaction(
        meta=meta,
        date=datetime.date(2024, 6, 1),
        flag="*",
        payee=payee,
        narration=narration,
        tags=frozenset(),
        links=frozenset(),
        postings=postings,
    )
    return [("import.csv", [txn], importer_account, None)]


def _imported_txns(imported) -> list[Transaction]:
    """Extract Transaction objects from imported_entries."""
    txns = []
    for _filename, entries, _account, _importer in imported:
        for entry in filter_txns(entries):
            txns.append(entry)
    return txns


def _account_names(txn: Transaction) -> list[str]:
    return sorted({p.account for p in txn.postings if p.account})


# =============================================================================
# RulesPostingsPredictor
# =============================================================================

class TestRulesPostingsPredictor:
    def test_exact_payee_rule(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee="Migros", narration="Groceries")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert "Expenses:Groceries" in accounts

    def test_normalized_payee_rule(self, ledger_normalized_payees) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee="Migros Basel", narration="Shopping")
        result = predictor.hook(imported, ledger_normalized_payees)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert "Expenses:Groceries" in accounts

    def test_keyword_rule(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        # "Quick shop" matches the narration of Coop transactions.
        imported = _make_imported(payee="", narration="Quick shop")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Coop is below threshold for exact payee but keyword may hit.
        # "quick" appears 4 times from Coop, "shop" appears 4 times.
        # The aggregated count for Expenses:Groceries is 4.
        # With min_occurrence=3 it should match.
        # But wait — we need at least one posting with units to trigger keyword rule.
        # The imported entry has one posting with -100 CHF, so keyword rule can run.
        assert "Expenses:Groceries" in accounts

    def test_amount_rule(self, ledger_amount_patterns) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee="Landlord AG", narration="Rent", amounts=["-1800"])
        result = predictor.hook(imported, ledger_amount_patterns)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert "Expenses:Housing:Rent" in accounts

    def test_rule_5_counterpart(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3, enable_rule_5=True)
        # Use a payee with no exact/normalized/keyword/amount match.
        imported = _make_imported(payee="Unknown", narration="Something")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Rule 5: most common counterpart to Assets:Bank:CHF is Expenses:Groceries (≥10)
        # Wait — we only have 5 Migros + 4 Coop + 3 SBB + 5 Salary + 6 Rent = 23 total
        # But Expenses:Groceries appears 9 times (5 + 4), which is < 10.
        # Let's check: Expenses:Transport = 3, Income:Salary = 5, Expenses:Housing:Rent = 6.
        # None of those are ≥10, so rule 5 should NOT match.
        assert "Expenses:Groceries" not in accounts

    def test_rule_5_counterpart_above_threshold(self) -> None:
        # Build a ledger where Expenses:Groceries appears ≥10 times.
        from datetime import date
        from beancount.core.data import Transaction, Posting, new_metadata
        from beancount.core.amount import Amount
        from beancount.core.number import D

        entries = []
        for i in range(12):
            meta = new_metadata("test.beancount", i + 1)
            postings = [
                Posting(account="Assets:Bank:CHF", units=Amount(D("-50"), "CHF"), cost=None, price=None, flag=None, meta=None),
                Posting(account="Expenses:Groceries", units=Amount(D("50"), "CHF"), cost=None, price=None, flag=None, meta=None),
            ]
            entries.append(Transaction(
                meta=meta, date=date(2024, 1, i + 1), flag="*",
                payee="Migros", narration="Groceries",
                tags=frozenset(), links=frozenset(), postings=postings
            ))
        predictor = RulesPostingsPredictor(min_occurrence=3, enable_rule_5=True)
        imported = _make_imported(payee="Unknown", narration="Something")
        result = predictor.hook(imported, entries)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert "Expenses:Groceries" in accounts

    def test_no_prediction_when_importer_not_in_set(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        # Importer account is Assets:Bank:CHF which IS in the set.
        # Let's test when importer account is NOT in predicted set.
        # Create a ledger where Migros maps to a set without Assets:Bank:CHF.
        from datetime import date
        from beancount.core.data import Transaction, Posting, new_metadata
        from beancount.core.amount import Amount
        from beancount.core.number import D

        def _make_txn(p, n, accts):
            meta = new_metadata("test.beancount", 1)
            postings = []
            for a in accts:
                postings.append(Posting(account=a, units=Amount(D("0"), "CHF"), cost=None, price=None, flag=None, meta=None))
            return Transaction(meta=meta, date=date(2024, 1, 1), flag="*", payee=p, narration=n, tags=frozenset(), links=frozenset(), postings=postings)

        entries = [
            _make_txn("Migros", "Groceries", ["Liabilities:CreditCard", "Expenses:Groceries"])
        ] * 5
        imported = _make_imported(payee="Migros", narration="Groceries")
        result = predictor.hook(imported, entries)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Predicted set doesn't include importer account → skip.
        assert "Expenses:Groceries" not in accounts

    def test_empty_ledger(self, ledger_empty) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee="Migros", narration="Groceries")
        result = predictor.hook(imported, ledger_empty)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Empty ledger → no rules fire → no prediction.
        # The importer provides one leg (Assets:Bank:CHF).
        assert accounts == ["Assets:Bank:CHF"]

    def test_caching(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee="Migros", narration="Groceries")
        predictor.hook(imported, ledger_multi_payee)
        # Second call with same existing_entries should use cache.
        predictor.hook(imported, ledger_multi_payee)
        assert predictor._cache[0] is not None
        assert predictor._cache[1] is not None


# =============================================================================
# RulesPayeePredictor
# =============================================================================

class TestRulesPayeePredictor:
    def test_normalize_payee(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee="SBB CFF FFS", narration="Train")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == "sbb"

    def test_derive_from_narration_salary(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee="", narration="Salary from Acme Corp")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == "Acme Corp"

    def test_derive_from_narration_transfer(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee="", narration="Transfer to Savings Account")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == "Savings Account"

    def test_keep_existing_if_no_rule_matches(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee="Local Shop", narration="Purchase")
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        # Normalization only changes payee when it strips suffixes or unifies variants.
        # "Local Shop" has nothing to normalize → stays as-is.
        assert txn.payee == "Local Shop"


# =============================================================================
# RulesTagsPredictor
# =============================================================================

class TestRulesTagsPredictor:
    def test_tag_prediction(self, ledger_multi_payee) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=5, max_tags=3)
        # Tags are keyed by (payee, account_set). Must use the same accounts
        # as appear in the ledger fixture for a match.
        imported = _make_imported(payee="Landlord AG", narration="Rent",
                                  amounts=["-1800", "1800"],
                                  accounts=["Assets:Bank:CHF", "Expenses:Housing:Rent"])
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert "predicted_tags" in txn.meta
        tags_str = txn.meta["predicted_tags"]
        assert "#housing" in tags_str
        assert "#recurring" in tags_str

    def test_below_threshold(self, ledger_multi_payee) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=20, max_tags=3)
        imported = _make_imported(payee="Landlord AG", narration="Rent", amounts=["-1800", "1800"])
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        # Landlord AG tags appear 6 times each, below threshold of 20.
        assert "predicted_tags" not in txn.meta

    def test_empty_ledger(self, ledger_empty) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=1, max_tags=3)
        imported = _make_imported(payee="Landlord AG", narration="Rent")
        result = predictor.hook(imported, ledger_empty)
        txn = _imported_txns(result)[0]
        assert "predicted_tags" not in txn.meta

    def test_max_tags_limit(self, ledger_multi_payee) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=1, max_tags=1)
        imported = _make_imported(payee="Landlord AG", narration="Rent", amounts=["-1800", "1800"])
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        tags_str = txn.meta.get("predicted_tags", "")
        assert len(tags_str.split(",")) == 1

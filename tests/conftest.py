"""Pytest fixtures — synthetic Beancount transactions."""

from __future__ import annotations

import datetime

import pytest
from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction, new_metadata
from beancount.core.number import D


def _txn(
    payee: str | None,
    narration: str,
    accounts: list[str],
    amounts: list[str] | None = None,
    tags: list[str] | None = None,
    date: datetime.date | None = None,
    lineno: int = 1,
) -> Transaction:
    """Build a synthetic Transaction."""
    meta = new_metadata("test.beancount", lineno)
    if amounts is None:
        amounts = ["-100"]
    postings = []
    for i, account in enumerate(accounts):
        number = D(amounts[i]) if i < len(amounts) else D("0")
        currency = "CHF"
        postings.append(
            Posting(
                account=account,
                units=Amount(number, currency),
                cost=None,
                price=None,
                flag=None,
                meta=None,
            )
        )
    return Transaction(
        meta=meta,
        date=date or datetime.date(2024, 1, lineno),
        flag="*",
        payee=payee,
        narration=narration,
        tags=frozenset(tags) if tags else frozenset(),
        links=frozenset(),
        postings=postings,
    )


@pytest.fixture
def single_txn() -> Transaction:
    """A single simple transaction."""
    return _txn(
        payee="Migros",
        narration="Weekly groceries",
        accounts=["Assets:Bank:CHF", "Expenses:Groceries"],
        amounts=["-125.50", "125.50"],
        tags=["#food"],
        lineno=1,
    )


@pytest.fixture
def ledger_migros() -> list[Transaction]:
    """A small ledger with repeated Migros transactions."""
    return [
        _txn(
            payee="Migros",
            narration="Groceries",
            accounts=["Assets:Bank:CHF", "Expenses:Groceries"],
            amounts=["-120.00", "120.00"],
            tags=["#food"],
            lineno=i,
        )
        for i in range(1, 6)  # 5 occurrences
    ]


@pytest.fixture
def ledger_multi_payee() -> list[Transaction]:
    """Ledger with multiple payees and account sets."""
    txns = []
    # Migros → Expenses:Groceries (5x)
    for i in range(1, 6):
        txns.append(
            _txn(
                payee="Migros",
                narration="Weekly groceries",
                accounts=["Assets:Bank:CHF", "Expenses:Groceries"],
                amounts=["-100", "100"],
                tags=["#food"],
                lineno=i,
            )
        )
    # Coop → Expenses:Groceries (4x — below threshold)
    for i in range(6, 10):
        txns.append(
            _txn(
                payee="Coop",
                narration="Quick shop",
                accounts=["Assets:Bank:CHF", "Expenses:Groceries"],
                amounts=["-45", "45"],
                tags=["#food"],
                lineno=i,
            )
        )
    # SBB → Expenses:Transport (3x)
    for i in range(10, 13):
        txns.append(
            _txn(
                payee="SBB CFF FFS",
                narration="Train ticket",
                accounts=["Assets:Bank:CHF", "Expenses:Transport"],
                amounts=["-65", "65"],
                tags=["#travel"],
                lineno=i,
            )
        )
    # Salary → Income:Salary (5x)
    for i in range(13, 18):
        txns.append(
            _txn(
                payee="Acme Corp",
                narration="Monthly salary",
                accounts=["Assets:Bank:CHF", "Income:Salary"],
                amounts=["5000", "-5000"],
                tags=["#income"],
                lineno=i,
            )
        )
    # Rent → Expenses:Housing:Rent (6x)
    for i in range(18, 24):
        txns.append(
            _txn(
                payee="Landlord AG",
                narration="Rent payment",
                accounts=["Assets:Bank:CHF", "Expenses:Housing:Rent"],
                amounts=["-1800", "1800"],
                tags=["#housing", "#recurring"],
                lineno=i,
            )
        )
    return txns


@pytest.fixture
def ledger_empty() -> list:
    """Empty ledger."""
    return []


@pytest.fixture
def ledger_no_payee() -> list[Transaction]:
    """Ledger with transactions that have no payee."""
    return [
        _txn(
            payee=None,
            narration="ATM withdrawal",
            accounts=["Assets:Bank:CHF", "Assets:Cash"],
            amounts=["-200", "200"],
            lineno=1,
        ),
    ]


@pytest.fixture
def ledger_normalized_payees() -> list[Transaction]:
    """Ledger with payee variants that normalize to the same value."""
    variants = [
        "Migros Zürich",
        "Migros Basel",
        "MIGROS BERN",
        "Migros",
        "Migrolino",
    ]
    return [
        _txn(
            payee=variant,
            narration="Shopping",
            accounts=["Assets:Bank:CHF", "Expenses:Groceries"],
            amounts=["-50", "50"],
            lineno=i + 1,
        )
        for i, variant in enumerate(variants)
    ]


@pytest.fixture
def ledger_amount_patterns() -> list[Transaction]:
    """Ledger with recurring amount patterns."""
    return [
        _txn(
            payee="Landlord AG",
            narration="Rent",
            accounts=["Assets:Bank:CHF", "Expenses:Housing:Rent"],
            amounts=["-1800", "1800"],
            lineno=i,
        )
        for i in range(1, 6)
    ]


@pytest.fixture
def ledger_multi_leg() -> list[Transaction]:
    """Ledger with a 3-leg transaction."""
    return [
        _txn(
            payee="Split Bill",
            narration="Dinner with friends",
            accounts=[
                "Assets:Bank:CHF",
                "Expenses:Food",
                "Liabilities:Friend",
            ],
            amounts=["-150", "100", "50"],
            lineno=1,
        ),
    ]

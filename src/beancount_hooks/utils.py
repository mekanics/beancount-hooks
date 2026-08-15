"""Shared utility helpers.

Pure Python, stdlib only.
"""

from __future__ import annotations

from beancount.core.data import Transaction


def get_amount_and_sign(posting) -> tuple[float | None, int | None]:
    """Return (absolute amount value, sign) for a posting's units.

    Returns (None, None) if the posting has no units or a null amount.
    """
    units = posting.units
    if units is None or units.number is None:
        return None, None
    number = float(units.number)
    sign = 1 if number >= 0 else -1
    return abs(number), sign


def txn_accounts(txn: Transaction) -> frozenset[str]:
    """Return the set of account names used in a transaction's postings."""
    return frozenset(p.account for p in txn.postings if p.account)


def other_accounts(txn: Transaction, importer_account: str | None) -> list[str]:
    """Return the account(s) in *txn* that are NOT the importer account.

    If *importer_account* is None or not in the transaction's accounts,
    returns all accounts.
    """
    accounts = [p.account for p in txn.postings if p.account]
    if importer_account and importer_account in accounts:
        return [a for a in accounts if a != importer_account]
    return accounts

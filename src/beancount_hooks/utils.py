"""Shared utility helpers.

Pure Python, stdlib only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from beancount.core import convert, interpolate
from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction

if TYPE_CHECKING:
    from collections.abc import Sequence


def source_units(txn: Transaction) -> Amount | None:
    """The units of the first posting that has any — the leg the importer supplied."""
    for posting in txn.postings:
        if posting.units is not None and posting.units.number is not None:
            return posting.units
    return None


def balancing_units(txn: Transaction) -> Amount | None:
    """The amount a single new posting would have to carry for *txn* to balance.

    Returns ``None`` — meaning nothing to fill — when:

    * any posting already has no units (Beancount is interpolating one; a second is an error)
    * the residual is empty (the transaction already balances)
    * the residual spans more than one currency (one posting cannot resolve it)
    """
    if any(p.units is None or p.units.number is None for p in txn.postings if p.account):
        return None

    residual = interpolate.compute_residual(txn.postings).reduce(convert.get_units)
    if residual.is_empty():
        return None

    positions = list(residual)
    if len(positions) != 1:
        return None

    units = positions[0].units
    if units is None or units.number is None:
        return None
    # The residual is the sum of existing posting weights.  A balancing posting carries
    # the opposite amount.
    return Amount(-units.number, units.currency)


def _shortest_exact(value: Decimal, min_places: int) -> Decimal:
    """*value* with pointless trailing zeros removed, but never fewer than *min_places*.

    Multiplying exactly keeps every digit of both operands, so half of ``100.00`` comes out as
    ``50.000``.  ``normalize`` alone would trim that to ``50`` — and turn ``200.00`` into
    ``2E+2`` — so its exponent is used only as a floor to quantize back to.
    """
    exponent = min(value.normalize().as_tuple().exponent, -min_places)
    return value.quantize(Decimal(1).scaleb(exponent))


def allocate(total: Amount, shares: Sequence[tuple[str, Decimal]]) -> list[Posting]:
    """Divide *total* across *shares*, as explicit postings.

    *total* is the amount being divided — typically :func:`balancing_units` of the
    transaction — not the importer's source leg.  For a single-leg debit of ``-100``,
    balancing units are ``+100``, and halves of that are ``+50`` / ``+50``.

    An exact division is kept exact: half of 412.05 is 206.025, which is how a halved purchase
    has always been recorded, rather than 206.02 with the odd rappen pushed onto the other leg.
    Only a division that does not terminate — thirds being the obvious case — is rounded, and
    then the last account absorbs the remainder so the result still balances to the cent.
    Order therefore matters, and the caller decides it.

    Used both by a rule stating a split outright and by the predictor proposing one it
    measured, which is why it lives here rather than in either.
    """
    counter = total.number
    places = -total.number.as_tuple().exponent
    # How long a division may run before it counts as non-terminating.  Halving a two-decimal
    # amount needs three places, quartering it four; a third runs to Decimal's full precision.
    tolerable = max(places, 2) + 3

    allocated = Decimal(0)
    postings: list[Posting] = []
    for index, (account, fraction) in enumerate(shares):
        if index == len(shares) - 1:
            value = counter - allocated
        else:
            value = counter * fraction
            value = (
                value.quantize(total.number)
                if -value.as_tuple().exponent > tolerable
                else _shortest_exact(value, places)
            )
            allocated += value
        postings.append(Posting(account, Amount(value, total.currency), None, None, None, None))
    return postings


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

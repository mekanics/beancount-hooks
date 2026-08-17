"""Entry-list rewriting for beangulp hooks.

Beangulp always hands a hook ``(filename, entries, account, importer)``.  Fava supports
that shape *and* a legacy ``(filename, entries)``, and picks between them per hook by
searching the hook's annotations for the literal text ``Importer``
(``fava.core.ingest.IngestModule.extract``).  Every hook here must therefore spell
``Importer`` out in its signature — an alias or a widened ``object`` reads as the legacy
shape, and the hook then runs with no importer account to scope rules by.

Pure Python at runtime; the ``Importer`` annotation is never evaluated.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from beancount.core.data import Transaction

if TYPE_CHECKING:
    from collections.abc import Callable

    from beangulp.importer import Importer

logger = logging.getLogger(__name__)


def map_transactions(
    imported_entries: list[tuple[str, list, str, Importer]],
    fn: Callable[[Transaction, str | None], Transaction | None],
    *,
    label: str,
) -> list[tuple[str, list, str, Importer]]:
    """Apply *fn* to every Transaction in *imported_entries*, in place.

    ``fn(txn, importer_account)`` returns a replacement Transaction, the same object to
    leave it untouched, or ``None`` to drop the entry.  Exceptions raised by *fn* are
    logged against *label* and leave the entry as it was — a failing rule must never
    block an import.

    Non-Transaction directives (``Balance``, ``Note``, ``Document``, ...) keep their
    positions.  Importers interleave them with transactions: ``ibkr`` returns
    ``Trades + CashTransactions + Balances + CorporateActions``, so a ``Balance`` lands in
    the middle of the list.  Indexing into the unfiltered list is what keeps those
    directives from being overwritten.

    Returns *imported_entries* so it can be used as a hook return value directly.

    Raises ValueError on anything other than a 4-tuple.  Silently accepting the legacy
    ``(filename, entries)`` would cost the importer account, and an account-scoped
    ``Ruleset`` with no account declines every transaction — a whole ruleset doing
    nothing, with no error to show for it.
    """
    for item in imported_entries:
        if len(item) != 4:
            raise ValueError(
                f'{label}: expected (filename, entries, account, importer), got a '
                f'{len(item)}-tuple.  Fava sends the 4-tuple only to hooks whose '
                f'annotations mention Importer; see beancount_hooks.entries.'
            )
        _filename, entries, account, _importer = item
        replacements: dict[int, Transaction] = {}
        dropped: list[int] = []

        for i, entry in enumerate(entries):
            if not isinstance(entry, Transaction):
                continue
            try:
                result = fn(entry, account)
            except Exception:
                logger.warning(
                    '%s: failed on entry %s, leaving it unchanged.',
                    label,
                    entry.narration or entry.payee or '<unknown>',
                    exc_info=True,
                )
                continue
            if result is None:
                dropped.append(i)
            elif result is not entry:
                replacements[i] = result

        for i, replacement in replacements.items():
            entries[i] = replacement
        for i in reversed(dropped):
            del entries[i]

    return imported_entries

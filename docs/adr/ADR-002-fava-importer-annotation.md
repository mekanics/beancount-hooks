# ADR-002: Require the beangulp 4-tuple and spell `Importer`

**Date**: 2026-08-16
**Status**: Accepted
**Deciders**: Maintainer

## Context

Beangulp always calls a hook with
`(filename, entries, account, importer)` per file. Fava supports that shape
*and* a legacy `(filename, entries)`, and chooses per hook by searching the
hook's annotations for the literal text `Importer`
(`fava.core.ingest.IngestModule.extract`).

An account-scoped [Ruleset](../glossary.md) with no importer account declines
every transaction. A posting predictor with no account cannot restrict
history to the card being imported. Both look like "the hooks did nothing".

## Options Considered

### Option 1: Accept both arities

- Pros: Never crashes under Fava, even with a sloppy signature.
- Cons: The legacy path silently drops the importer account. A scoped
  ruleset then matches nothing, with no error.

### Option 2: Reject anything but a 4-tuple, and spell `Importer` in every hook

- Pros: Fava is forced onto the 4-tuple path; a mis-annotated hook fails
  loudly instead of applying zero rules.
- Cons: An alias (`ImportedBatch`) or `object` in the annotation reads as
  the legacy shape and then raises. Tests must pin the annotation text.

### Option 3: A Fava-specific adapter

- Pros: Isolates the quirk.
- Cons: Two public hook surfaces; the adapter is easy to forget; beangulp
  users gain nothing.

## Decision

**Chosen**: Option 2 — require the 4-tuple and write `Importer` in every
public `hook` signature.

`map_transactions` raises `ValueError` naming the hook when it sees any
other arity. The `Importer` type is imported under `TYPE_CHECKING` only; it
is never evaluated at runtime.

## Consequences

### Positive

- Fava ingest and `beangulp.Ingest` run the same hook list.
- A broken annotation fails the first import, not after a month of empty
  reviews.
- Account-scoped rulesets and account-scoped prediction rungs have the
  account they were written for.

### Negative / Trade-offs

- The contract is Fava's string search, not Python's type system. A type
  alias that does not contain the letters `Importer` is a production bug.
- Callers who wrap `hook` must preserve the annotation or Fava will send
  the 2-tuple and the wrapper will raise.

## Implementation Notes

- Every public `hook` is annotated
  `list[tuple[str, list, str, Importer]]`. Do not introduce an alias.
- `tests/test_fava_hooks.py` copies Fava's `Importer in annotation` check
  and drives each hook through that dispatch. Do not delete it to "simplify".
- Non-`Transaction` directives stay at their original indexes. Importers
  interleave `Balance` with transactions; rewriting the filtered list
  overwrites those directives.

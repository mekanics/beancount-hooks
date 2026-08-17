# ADR-006: Complete means balanced

**Date**: 2026-08-17
**Status**: Accepted
**Deciders**: Maintainer
**Supersedes**: the account-count completeness check in
[ADR-001](./ADR-001-rules-assert-predictors-fill.md) implementation notes

## Context

Importers sometimes name more than one leg without finishing the transaction.
A Yuh foreign-currency purchase arrives as the bank leg plus a fee the export
states outright; the expense residual is still blank. The previous guard —
"more than one posting account means complete" — treated that shape as
finished, so neither a `Ruleset` nor `RulesPostingsPredictor` would fill it.

Importers had papered over the gap by inventing `Expenses:Unknown`. That
account is rarely opened, so the import does not load, and the balanced
placeholder still blocked the hooks.

## Options Considered

### Option 1: Treat a configurable placeholder account as blank

- Pros: Extracted files stay loadable without Beancount interpolation.
- Cons: The placeholder lives in two packages; the account still has to be
  opened; rules and predictors need a second concept of "blank".

### Option 2: Completeness is the residual

- Pros: Beancount already computes it (`interpolate.compute_residual`); a
  bank-plus-fee entry is incomplete for free; no placeholder account; the
  same test covers rules and predictors.
- Cons: Callers must accept unbalanced intermediate transactions (Beancount
  interpolates the missing leg on load when one posting is left amount-less).

### Option 3: Leave the account-count guard; fix only the importers

- Pros: Smallest hooks change.
- Cons: Foreign Yuh transactions land with a blank leg that rules never fill.

## Decision

**Chosen**: Option 2 — a transaction is complete when it balances.

`balancing_units(txn)` returns the amount a single new posting would have to
carry, or `None` when there is nothing to fill: an existing auto-posting, an
empty residual, or a residual that spans more than one currency. Both
`Actions._build_postings` and `RulesPostingsPredictor._predict` use that
result as their entry condition.

Multi-leg splits (declared or historical) are offered only when the entry
still carries a single account. Historical fractions include every other
leg; allocating them over an entry that already has a fee posting would not
balance.

## Consequences

### Positive

- Importers state only the legs their exports name.
- A bank leg plus a fee leg is filled by rules and predictors.
- Multi-currency residuals (priced IBKR entries, mixed FX) are left alone.

### Negative / Trade-offs

- Intermediate extracted transactions may not balance until a hook adds a
  bare posting for Beancount to interpolate.
- An unbalanced entry whose history splits across two or more remaining
  accounts stays blank for review rather than getting a wrong allocation.

## Implementation Notes

- `allocate` takes the amount being divided (`balancing_units`), not the
  importer's source leg. For a single-leg debit the two are opposites, so
  existing split tests stay numerically unchanged.
- `source_units` remains for `Match` amount / currency / sign tests, which
  ask about the leg the importer supplied.

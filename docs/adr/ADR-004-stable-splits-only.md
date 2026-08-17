# ADR-004: Predict multi-leg fills only when the split is settled

**Date**: 2026-08-16
**Status**: Accepted
**Deciders**: Maintainer

## Context

Beancount interpolates only one posting per currency. When history says a
payee uses three accounts, the predictor knows the names and cannot leave
two of them amount-less.

Some three-account habits are stable (a hotel always halved with a
partner). Some are not (a health-insurance premium renegotiated every
year; groceries booked to the same three accounts in
different proportions). Filling the latter with an average is a
plausible wrong split.

The same three accounts can also be a 50/50 habit for one payee and a
95/5 habit for another. A table keyed only by the account set treats
those as one unstable mixture and stays silent for both.

## Options Considered

### Option 1: Always fill the account names, leave amounts blank

- Pros: Simple.
- Cons: Beancount refuses to load the transaction. The import dies.

### Option 2: Always allocate by the mean of historical fractions

- Pros: Something always lands.
- Cons: A moving premium or a mixed cigarette/grocery run gets a number
  that looks reviewed. The blank-leg rule of
  [ADR-001](./ADR-001-rules-assert-predictors-fill.md) is violated.

### Option 3: Allocate only when that payee's fractions agree, within tolerance

- Pros: A habit becomes an answer; a coincidence stays blank for review.
- Cons: A payee that just started splitting will not be predicted until
  `min_occurrence` is reached, even if the first few are identical.

## Decision

**Chosen**: Option 3 — offer a multi-leg fill only for a
[settled split](../glossary.md) of that payee.

The split table is keyed by `(account set, source account, payee)`, stored
under both the exact payee and `normalize_payee`. Exact history that is
plentiful but unstable is an answer of none; the brand is not consulted as
a fallback that might look smoother.

## Consequences

### Positive

- Halved hotels fill in. Renegotiated premiums do not.
- Two payees sharing three accounts keep their own fractions.
- A store spelling with no history of its own still sees the brand's
  halves, unless its own exact history already disagreed.

### Negative / Trade-offs

- Thin history (`< min_occurrence`) looks the same as unstable history:
  a blank leg. That is intentional — both need a human.
- A rule that _must_ split (income shared 50/50) belongs on the
  [Ruleset](../glossary.md), not in the predictor.

## Implementation Notes

- `get_split_shares` returns `(account, fraction)` pairs sorted by account
  name so `allocate` is deterministic and the last leg absorbs the
  remainder.
- Default tolerance is `Decimal("0.01")` (one percent of the whole, not
  one currency unit).
- A single predicted leg still carries no amount. Interpolation is the
  only correct currency-preserving choice.

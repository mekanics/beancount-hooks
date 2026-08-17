# ADR-003: Match amounts are absolute; direction is `sign`

**Date**: 2026-08-16
**Status**: Accepted
**Deciders**: Maintainer

## Context

Bank exports give a signed amount. A subscription is usually a debit of
10.00; the matching refund is a credit of 10.00. The wrapper this library
replaced compared the signed amount to `D("10.00")`, so the rule fired on
refunds and missed every charge.

Importers also disagree about scale: one writes `-10.0`, another `-10.00`.
A string or Decimal-tuple comparison treats those as different.

## Options Considered

### Option 1: Compare the signed amount to a signed literal

- Pros: Matches how the file looks.
- Cons: `amount == 10` only hits credits; `amount == -10` only hits
  debits. The inverted-sign defect is the default.

### Option 2: Absolute amount plus a separate `sign`

- Pros: `amount=D("10.00"), sign="debit"` cannot match a refund; the two
  mistakes (wrong magnitude, wrong direction) are separate.
- Cons: Slightly more verbose than a signed literal.

### Option 3: Two fields, `debit` and `credit`, each an amount

- Pros: Impossible to forget a sign.
- Cons: A rule that cares about magnitude but not direction needs both;
  comparisons (`amount_lt`) duplicate.

## Decision

**Chosen**: Option 2 — `amount` / `amount_lt` / `amount_gt` are absolute;
`sign` is `"debit"` or `"credit"`.

A negative amount on a `Match` is rejected at construction. Comparisons are
numeric, so `-10.0` and `-10.00` are the same debit of 10.

## Consequences

### Positive

- A subscription rule and its refund are different matches, not an
  accident of the literal's sign.
- Scale differences between importers do not miss a match.

### Negative / Trade-offs

- Callers coming from signed comparisons have to split one number into two
  fields. The constructor error is the reminder.

## Implementation Notes

- Zero is a credit. There is no third sign.
- `Match.when` remains the escape hatch for anything this model does not
  express (weekends, metadata).
- `Actions` amounts stay signed `Amount` values — they are postings, not
  criteria.

# ADR-001: Rules assert, predictors fill blanks

**Date**: 2026-08-16
**Status**: Accepted
**Deciders**: Maintainer

## Context

Imported transactions are missing the other posting, often the payee, and
usually the tags. The established tool for this is `smart_importer`, which
trains a classifier on the existing ledger and writes its guess onto the
entry. A ledger that already has a curated `if`/`elif` wrapper needs those
facts to survive the guess, and does not want a scikit-learn runtime.

## Options Considered

### Option 1: Wrap `smart_importer`

- Pros: Existing ecosystem, one hook, no new predictor to write.
- Cons: ML dependency; guesses overwrite curated names; no way to *state* a
  fact separately from a guess; a plausible wrong account hides at review.

### Option 2: One combined hook that rules and then classifies

- Pros: Single object to install.
- Cons: The two jobs share no code and have opposite failure modes (a rule
  must be obeyed; a guess must yield). Mixing them hides the contract.

### Option 3: Two layers, rules first, predictors only fill blanks

- Pros: Facts and guesses are separate types; hook order is the contract;
  predictors cannot clobber a rule; no ML runtime; a blank leg is visible.
- Cons: Callers must install several hooks in the right order; a predictor
  will not "fix" a wrong rule.

## Decision

**Chosen**: Option 3 — two layers, rules first, predictors fill blanks.

This is the product. A `Ruleset` states what a transaction is and needs no
history. The `Rules*Predictor` hooks then write only empty fields. The public
package documents that order and will not grow a combined hook that blurs it.

## Consequences

### Positive

- A rule's payee, postings, and tags are stable under the rest of the list.
- Unrecognised payees stay blank instead of inheriting the most common
  expense account.
- The library stays stdlib + Beancount at runtime.

### Negative / Trade-offs

- Installing hooks in the wrong order (predictors before the ruleset) lets a
  guess land first; the ruleset will not overwrite it either.
- Canonical display names are a rule's job. `normalize_payee` produces index
  keys and must not be written back to `entry.payee`.

## Implementation Notes

- Predictors check "already set" before writing: more than one posting
  account, a non-empty payee, tags are unioned not replaced.
- `Ruleset.hook` ignores `existing_entries`.
- Rung 5 of the postings ladder (most common counterpart, ignoring the
  payee) stays off by default — it is the "plausible wrong account" option.

# beancount-hooks — Glossary

> Definitions of the domain, technical, and product terms used across the
> beancount-hooks documentation. When in doubt, link to this file rather than
> redefine a term in place.
>
> Tag legend: **(CH)** Swiss-market convention · **(host)** behaviour imposed
> by beangulp or Fava, not this library.

## A — C

**Actions** — What a rule does to a matched transaction: rename, post, split,
tag, link, flag, or drop. See [ADR-001](./adr/ADR-001-rules-assert-predictors-fill.md).

**Amount (match)** — An absolute value. Direction is expressed separately as
[sign](#s--z). Comparing a signed amount to a positive literal is how a
subscription rule matches only refunds. See
[ADR-003](./adr/ADR-003-absolute-amounts-and-sign.md).

**Assert** — A [Ruleset](#m--r) stating a fact about a transaction. Asserts
run first and are not guesses. See
[ADR-001](./adr/ADR-001-rules-assert-predictors-fill.md).

## D — F

**Decision ladder** — Ordered rungs a [predictor](#m--r) walks; the first
rung that answers wins. Used by the postings predictor. See [SAD](./SAD.md).

**Drop** — An action that removes a transaction from the imported batch
(typically a transfer already booked on the other side). Cannot be combined
with other actions.

**Fill blanks** — A [predictor](#m--r) writing only fields that are still
empty. The counterpart of [assert](#a--c). See
[ADR-001](./adr/ADR-001-rules-assert-predictors-fill.md).

## G — L

**Gate contract** — The named Makefile targets (`check`, `lint`, `format`,
`test`, `build`, `audit`) that local work, git hooks, and CI all call. See
[ADR-005](./adr/ADR-005-standardize-build-and-release.md).

**Hook** _(host)_ — A callable `hook(imported_entries, existing_entries)` that
returns the (possibly mutated) imported batch. Beangulp always passes a
4-tuple per file; Fava does so only when the signature mentions `Importer`.
See [ADR-002](./adr/ADR-002-fava-importer-annotation.md).

**Importer account** — The account the host associates with the file being
imported — the one leg the bank already provided. Rulesets may [scope](#s--z)
themselves to prefixes of this account; posting rungs ask only about history
funded from it.

**LedgerIndex** — Read-only counters built from the existing ledger that the
predictors query. Not used by a ruleset.

## M — R

**Match** — The criteria that decide whether a [rule](#m--r) applies. Every
given criterion must hold. See
[ADR-003](./adr/ADR-003-absolute-amounts-and-sign.md).

**Normalize (payee)** — Reduce a payee string to a lookup key (`Migros Zürich`
→ `migros`). A key, not a display name; predictors must not write it back.

**Post** — Add one or more other legs. An account alone is left for Beancount
to interpolate; paired with an amount it is pinned.

**Pending publisher** — A PyPI trusted-publisher registration created before
the project exists, so the first tag can claim the name via OIDC. See
[ADR-005](./adr/ADR-005-standardize-build-and-release.md).

**Predictor** — A hook that fills a blank field from ledger history. The three
are payee, postings, and tags. See
[ADR-001](./adr/ADR-001-rules-assert-predictors-fill.md).

**Ratchet** — A coverage floor set just below the measured value so CI stays
green today and cannot regress. See
[ADR-005](./adr/ADR-005-standardize-build-and-release.md).

**Reflow commit** — A formatting-only commit, recorded in
`.git-blame-ignore-revs`, that adopts a new Ruff config without mixing in
logic changes.

**Rule** — A [Match](#m--r) paired with the [Actions](#a--c) it triggers, with
a unique label.

**Ruleset** — An ordered list of [rules](#m--r). The first match wins. May be
[scoped](#s--z) to importer-account prefixes.

**Rung** — One step of a [decision ladder](#d--f).

## S — Z

**Scope** — The importer-account prefixes a [ruleset](#m--r) will consider. An
unknown or missing account is a decline, not a guess.

**Shadowed** — A later rule that can never fire because an earlier rule
matches everything it does. Reported by `Ruleset.shadowed()`, conservatively.

**Sign** — `debit` (money leaving the importer account) or `credit` (money
arriving). Paired with an absolute [amount](#a--c). See
[ADR-003](./adr/ADR-003-absolute-amounts-and-sign.md).

**Source account** — In the split table, the leg the money came from — usually
the [importer account](#g--l).

**Split** — Divide the balancing amount across accounts by fraction. Fractions
must be positive and total 1; the last account absorbs the rounding remainder.

**Split (settled)** — A [split](#s--z) the ledger has made the same way often
enough, within tolerance, for that payee. The only multi-leg fill a predictor
will offer. See [ADR-004](./adr/ADR-004-stable-splits-only.md).

**Tag share** — Fraction of a payee's transactions that carry a given tag.
Below the threshold the tag is treated as occasional, not a property of the
payee.

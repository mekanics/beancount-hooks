# Product Requirements Document

**Project**: beancount-hooks
**Last Updated**: 2026-08-16
**Status**: Active

## Vision

Beancount users import bank files through beangulp and then spend the review
pass filling in the other posting, the payee, and the tags. Machine-learning
hooks guess from history and overwrite curated names. This library does the
same job with two explicit layers: a [Ruleset](./glossary.md) that *asserts*
what a transaction is, and [Predictors](./glossary.md) that *fill blanks* from
the existing ledger. A rule's decision is never overwritten. There is no model
to train and no runtime dependency beyond Beancount.

## Target Users

- **Ledger maintainer**: imports from one or more banks, already has a chart of
  accounts, and wants recurring payees booked the same way every time — with
  the awkward cases (splits, pinned amounts, refunds) written down as data.
- **Reviewer of an import**: needs a blank leg to stay blank when history does
  not agree, rather than a plausible wrong account that hides at review.
- **Fava user**: runs the same hooks from the web ingest UI and expects them
  to see the importer account, not Fava's legacy two-tuple.

## Goals

1. Categorize imported transactions without a machine-learning runtime.
2. Let a ledger owner state facts (this payee is that account) separately from
   guesses (this payee has usually been that account).
3. Keep a rule's payee, postings, and tags intact when predictors run after it.
4. Work as beangulp hooks and as Fava ingest hooks without a special adapter.
5. Make rule-ordering mistakes visible (`explain`, `shadowed`) instead of
   comments asking the next reader to be careful.

## Non-Goals

- Training or shipping a classifier (see [ADR-001](./adr/ADR-001-rules-assert-predictors-fill.md)).
- Replacing beangulp importers or parsing bank files.
- A GUI or Fava plugin of its own — hooks only.
- Rewriting a transaction the importer already completed (both legs present).
- Canonicalising display names via the predictor (`SBBCFFFFS` → `SBB CFF FFS`
  is a rule, not a guess).
- Multi-user or server-side deployment.

## Features

### Declarative rules

- A `Match` tests payee, narration, account prefix, currency, absolute amount,
  sign, and an optional callable.
- `Actions` rename, post (with an optional pinned amount), split by fraction,
  tag, link, set a flag, or drop a duplicate.
- First matching rule wins. `Ruleset.shadowed()` reports rules that can never
  fire; `Ruleset.explain()` lists every match in precedence order.
- A ruleset can be limited to importer-account prefixes so one bank's payee
  strings are not applied to another importer that already assigns accounts.

### History-backed predictors

- `RulesPostingsPredictor` walks a five-rung [decision ladder](./glossary.md)
  (exact payee, normalized payee, narration keyword, amount bin, optional
  counterpart). Rungs 1–4 are scoped to the account being imported.
- Multi-leg predictions are offered only when the ledger divides that payee
  the same way every time, within tolerance. Unstable proportions leave the
  leg blank.
- `RulesPayeePredictor` derives a payee from narration when the importer
  supplied none. It never overwrites an existing payee.
- `RulesTagsPredictor` adds tags a payee has historically carried, only when
  the tag is true of most of that payee's transactions.

### Hook contract

- Every public hook accepts the beangulp 4-tuple
  `(filename, entries, account, importer)`.
- The `Importer` annotation is spelled out so Fava dispatches the 4-tuple
  (see [ADR-002](./adr/ADR-002-fava-importer-annotation.md)).
- A failing rule or index build logs and leaves the entry unchanged; it does
  not abort the import.

## Success Metrics

- A ruleset plus the three predictors is enough to replace an ad-hoc
  `if`/`elif` importer wrapper.
- `Ruleset.shadowed()` is empty for the ledger the library is configured
  against.
- Unrecognised or unstable payees arrive at review with a blank other leg,
  not a silently wrong account.
- The same hook list runs under `beangulp.Ingest` and Fava ingest.

## Constraints

- Python ≥ 3.11, Beancount ≥ 3.0. Runtime stays stdlib + Beancount; beangulp
  is a dev/integration dependency.
- Beancount interpolates only one posting per currency — multi-leg fills must
  carry amounts.
- Fava chooses the hook arity by searching annotation *text* for `Importer`.
- Public examples and tests must not publish a household chart of accounts.

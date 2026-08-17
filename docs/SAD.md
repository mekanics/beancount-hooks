# Software Architecture Document

**Project**: beancount-hooks
**Last Updated**: 2026-08-16
**Version**: 0.6.0

## Overview

A Python library of beangulp import hooks. A [Ruleset](./glossary.md) applies
the first matching declarative [Rule](./glossary.md) to each imported
transaction. Three [Predictors](./glossary.md) then fill fields that are still
blank, using a [LedgerIndex](./glossary.md) built from the existing ledger.
Hooks mutate the imported entry list in place and return it.

## Architecture Style

Single Python package (`beancount_hooks`). Two layers, always this order:

1. **Assert** — `Ruleset` (no history).
2. **Fill blanks** — `RulesPayeePredictor`, `RulesPostingsPredictor`,
   `RulesTagsPredictor` (history via `LedgerIndex`).

Predictors never overwrite a field that is already set, so a rule's decision
survives the rest of the hook list. See
[ADR-001](./adr/ADR-001-rules-assert-predictors-fill.md).

## System Components

```
beangulp / Fava ingest
        │
        ▼
  hook(imported_entries, existing_entries)
        │
        ├─ Ruleset.hook ──────────────► Match / Actions
        │                                      │
        ├─ RulesPayeePredictor.hook            │
        ├─ RulesPostingsPredictor.hook ──► LedgerIndex ◄── existing_entries
        └─ RulesTagsPredictor.hook             │
                                               ▼
                                    map_transactions
                                    (4-tuple only; keeps
                                     Balance/Note in place)
```

## Technology Stack

| Layer        | Technology                         | Rationale                                      |
|--------------|------------------------------------|------------------------------------------------|
| Language     | Python 3.11+                       | Beancount v3 baseline                          |
| Ledger       | beancount ≥ 3.0                    | Transaction / Posting types, interpolation     |
| Import host  | beangulp (dev) / Fava ingest       | Hook list API; not imported at runtime         |
| Build        | hatchling + uv                     | Src layout, lockfile                           |
| Lint/format  | ruff                               | Single-quote, 100-col, matches the package     |
| Test         | pytest                             | No network, no ledger files                    |

There is no database, no service, and no ML library.

## Key Components

### `Ruleset` (`ruleset.py`)

- **Responsibility**: Apply the first matching `Rule`. Optionally restrict
  itself to importer-account prefixes. Expose `explain` and `shadowed`.
- **Interfaces**: `hook`, `apply`, `match`, `covers`, `explain`, `shadowed`,
  `accounts`.
- **Data ownership**: The `Rule` tuple it was constructed with. Ignores
  `existing_entries`.

### `Match` / `Actions` / `Rule`

- **Responsibility**: `Match` is the when; `Actions` is the what; `Rule` pairs
  them. Amounts on a `Match` are absolute; direction is `sign`
  ([ADR-003](./adr/ADR-003-absolute-amounts-and-sign.md)).
- **Interfaces**: `Match.test`, `Actions.apply_to`, `Rule.label`.
- **Data ownership**: Frozen dataclasses. `apply_to` never mutates the input
  `Transaction` (including its postings list).

### Predictors (`rules.py`)

- **Responsibility**: Fill a missing payee, the other posting(s), or tags.
- **Interfaces**: Each exposes `hook` with the beangulp 4-tuple signature.
- **Data ownership**: A cached `LedgerIndex` keyed by identity of the
  `existing_entries` list. Cache is dropped when that list object changes.

`RulesPostingsPredictor` rungs, first match wins, each filtered to history
that includes the [importer account](./glossary.md):

| Rung | Key                         | Default |
|------|-----------------------------|---------|
| 1    | Exact payee                 | on      |
| 2    | Normalized payee            | on      |
| 3    | Narration keywords          | on      |
| 4    | Payee + amount bin + sign   | on      |
| 5    | Account's top counterpart   | off     |

Rung 5 is off because on an everyday-spending account it books every unknown
payee as whatever you buy most. A blank leg is visible; a plausible wrong one
is not.

Several other legs are filled only when `LedgerIndex.get_split_shares` finds a
stable division for that payee. Otherwise the predictor stays silent
([ADR-004](./adr/ADR-004-stable-splits-only.md)).

### `LedgerIndex` (`index.py`)

- **Responsibility**: One pass over `existing_entries` into counter maps
  (payee, normalized payee, keyword, amount bin, co-occurrence, tags) and a
  split table keyed by `(account set, source account, payee)`.
- **Interfaces**: `get_accounts_by_*`, `get_counterpart`, `get_tags`,
  `get_split_shares`.
- **Data ownership**: The maps. Split observations are stored under both the
  exact payee and `normalize_payee`, so a store spelling with no history of
  its own still sees the brand. Exact history that is plentiful but unstable
  is an answer of none — the brand is not tried as a fallback.

### `map_transactions` (`entries.py`)

- **Responsibility**: Walk imported batches, apply a callback to each
  `Transaction`, keep non-transaction directives at their original indexes,
  drop entries the callback returns `None` for.
- **Interfaces**: `map_transactions(imported_entries, fn, *, label)`.
- **Data ownership**: None. Raises `ValueError` on anything other than a
  4-tuple ([ADR-002](./adr/ADR-002-fava-importer-annotation.md)).

### `normalizer` (`normalizer.py`)

- **Responsibility**: `normalize_payee` (lookup key, not a display name),
  `extract_keywords` (DE/EN/FR stopwords), `round_to_bin`.
- **Interfaces**: The three functions exported from the package.
- **Data ownership**: Compiled variant list and stopword set.

### `utils` (`utils.py`)

- **Responsibility**: `allocate` (last share absorbs the rounding remainder),
  `source_units`, `get_amount_and_sign`.
- **Interfaces**: Called by `Actions` and the postings predictor.

## Data Flow

1. The host (beangulp or Fava) calls each hook with
   `imported_entries` — a list of `(filename, entries, account, importer)` —
   and the parsed ledger as `existing_entries`.
2. `Ruleset.hook` ignores history. For each transaction, if the importer
   account is in scope, the first matching rule applies or drops it.
3. Predictors build or reuse a `LedgerIndex` from `existing_entries`.
4. `RulesPayeePredictor` writes `entry.payee` only when it is empty.
5. `RulesPostingsPredictor` writes other legs only when the transaction still
   has a single account. One predicted leg is left amount-less for Beancount
   to interpolate; several legs are allocated from settled fractions.
6. `RulesTagsPredictor` unions predicted tags onto `entry.tags`.
7. The same `imported_entries` list object is returned, mutated in place, so
   the next hook sees the previous hook's work.

A callback exception is logged against the hook label and the entry is left
as it was. An index-build failure passes the batch through unchanged.

## External Integrations

| Service                         | Purpose                                      | Authentication |
|---------------------------------|----------------------------------------------|----------------|
| beangulp `Ingest`               | CLI extract; supplies the 4-tuple            | none           |
| Fava `IngestModule.extract`     | Web ingest; 4-tuple iff annotation has `Importer` | none      |
| Beancount loader                | Interpolation, balance check in tests        | none           |

The library does not call banks, price sources, or the network.

## Security Model

- No authentication, no secrets, no I/O beyond what the host already loaded.
- Hooks run in the same process as the ledger. A `Match.when` callable is
  user code; a failing callable is logged, not raised.
- Public tests and docs use generic account names. A household chart of
  accounts belongs in the consuming ledger, not this repository.

## Scalability

- Current capacity: a personal or household ledger (thousands to low tens of
  thousands of transactions). `LedgerIndex` is a single linear scan plus
  in-memory counters.
- Scaling strategy: none planned. The index is rebuilt when the
  `existing_entries` list identity changes; Fava typically passes the same
  list for a session.
- Known bottlenecks: rebuilding the index on every distinct list object;
  `shadowed()` is O(n²) in the number of rules (fine for a few hundred).

## ADR References

See [`docs/adr/`](./adr/) for all architectural decision records.

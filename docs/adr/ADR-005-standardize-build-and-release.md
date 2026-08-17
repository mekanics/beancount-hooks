# ADR-005: Standardized build, gate contract, and release pipeline

**Date**: 2026-08-17
**Status**: Accepted
**Deciders**: Maintainer

## Context

`beancount-hooks` and `beancount-tools-collection` are sibling libraries used
by the same ledger. They had drifted on every setup axis: build backend,
dev-dependency declaration, Ruff config, git hooks, CI, and release. Hooks
had never been published; its README already told users to `uv add
beancount-hooks` while the PyPI name was unclaimed.

## Options Considered

### Option 1: Leave each repo as it is

- Pros: No reflow, no consumer change.
- Cons: Two ways to develop, two ways to release, and hooks cannot be
  installed from PyPI.

### Option 2: Extract shared scaffolding into a template repo

- Pros: A third library would inherit the standard for free.
- Cons: Two repos do not pay for Copier/cruft coordination.

### Option 3: One gate contract, copied into both repos

- Pros: CI, git hooks, and local work call the same named targets; the
  tooling behind a target can change in one file. Both packages are
  pure-Python with one top-level module, so `uv_build` fits both.
- Cons: Duplicated workflow YAML until a third repo justifies a template.

## Decision

**Chosen**: Option 3.

Locked forks:

- **Build backend**: `uv_build` in both. Version lives in `pyproject.toml`;
  `__version__` is derived via `importlib.metadata`.
- **Python floor**: `requires-python = ">=3.11"`; CI matrix 3.11–3.13.
  3.14 is omitted until Beancount ships a `cp314` wheel (3.2.0 stops at 3.13).
- **Hardening**: SHA-pinned actions, `permissions: {}` at workflow level,
  zizmor as a CI gate, Renovate with a release-age cooldown.
- **Release**: tag push only. The first step checks that the tag equals `v`
  plus the project version. Hooks claims the PyPI name via a pending
  trusted publisher, then `v0.6.1`.

The [gate contract](../glossary.md) is a `Makefile`. pre-commit may format
staged files with the lockfile Ruff; `pre-push` and CI call `make check`.

## Consequences

### Positive

- A packaging regression fails the test suite (tests import the installed
  package, not `src/` via `pythonpath`).
- A mismatched tag never reaches PyPI.
- Coverage cannot silently drop: hooks ratchets at 90, tools-collection at 30.

### Negative / Trade-offs

- tools-collection's Python 3.9/3.10 users stay on 1.2.0. That is graceful
  because of `requires-python`, but it is still a floor rise.
- Shared YAML is duplicated. Revisit a template at three repos.

## Implementation Notes

- Bump with `uv version --bump {patch|minor|major}` so `pyproject.toml` and
  `uv.lock` move together.
- The first hooks release needs the PyPI pending publisher and the GitHub
  `pypi` environment configured before the tag is pushed.

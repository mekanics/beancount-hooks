# beancount-hooks

Rule-based [beangulp](https://github.com/beancount/beangulp) import hooks for
Beancount v3. A [Ruleset](docs/glossary.md) asserts what a transaction is;
predictors then fill only the blanks from the existing ledger. No ML, no
runtime dependency beyond Beancount.

```
Ruleset.hook                 # facts — first match wins
RulesPayeePredictor.hook     # fill an empty payee from the narration
RulesPostingsPredictor.hook  # fill the other leg(s) from history
RulesTagsPredictor.hook      # add tags the payee usually carries
```

That order is the contract. Reverse it and a guess lands before the rule
that should have won. See [ADR-001](docs/adr/ADR-001-rules-assert-predictors-fill.md).

## Install

Requires Python 3.11+ and Beancount 3. After the first release (`v0.6.1`):

```bash
uv add beancount-hooks
```

Until that tag is on PyPI, install from a checkout:

```bash
uv add --editable /path/to/beancount-hooks
```

## Use

```python
from decimal import Decimal as D

from beancount.core.amount import Amount

from beancount_hooks import (
    Actions,
    Match,
    Rule,
    Ruleset,
    RulesPayeePredictor,
    RulesPostingsPredictor,
    RulesTagsPredictor,
)

RULES = [
    Rule(
        Match(payee='Coop'),
        Actions(post='Expenses:Groceries'),
        name='coop',
    ),
    Rule(
        Match(payee='Landlord', sign='debit'),
        Actions(
            post=(
                ('Expenses:Housing:Utilities', Amount(D('150.00'), 'CHF')),
                'Expenses:Housing:Rent',
            ),
            tags=('recurring',),
        ),
        name='rent',
    ),
]

# Limit the ruleset to the importers it was written for.  A card that
# already assigns its own accounts is left alone.
LEDGER_RULES = Ruleset(RULES, label='LedgerRules', accounts=('Assets:Bank',))

HOOKS = [
    LEDGER_RULES.hook,
    RulesPayeePredictor().hook,
    RulesPostingsPredictor().hook,
    RulesTagsPredictor().hook,
]
```

Pass `HOOKS` to `beangulp.Ingest(CONFIG, HOOKS)`. The same list is what Fava
ingest runs.

`Match` amounts are absolute; direction is `sign='debit'` or `'credit'`.
`amount=D("10.00")` without a sign matches both the charge and the refund —
add `sign` if you mean only one. See
[ADR-003](docs/adr/ADR-003-absolute-amounts-and-sign.md).

`Ruleset.shadowed()` returns `(earlier, unreachable)` pairs for rules that
can never fire. `Ruleset.explain(txn, account)` lists every match in
precedence order.

## Fava

Fava sends the beangulp 4-tuple only to hooks whose annotations contain the
literal text `Importer`. Every hook in this package does. If you wrap
`hook`, keep that word in the signature or Fava will call you with
`(filename, entries)`, the importer account will be missing, and
`map_transactions` will raise. See
[ADR-002](docs/adr/ADR-002-fava-importer-annotation.md).

## Documentation

| Doc | What it is |
|-----|------------|
| [docs/PRD.md](docs/PRD.md) | Scope, users, non-goals |
| [docs/SAD.md](docs/SAD.md) | Components, data flow, the posting ladder |
| [docs/glossary.md](docs/glossary.md) | Shared vocabulary |
| [docs/adr/](docs/adr/) | Binding decisions |

## Develop

```bash
make install
uv run pre-commit install
make check
make test
```

`make install` syncs the lockfile into `.venv`. pre-commit formats staged
files; `pre-push` runs `make check` (the same gate CI uses).

```bash
make format   # rewrite the tree
make build    # sdist + wheel, no local path sources
make audit    # zizmor over the workflows
```

## Release

```bash
uv version --bump patch   # or minor / major
git add pyproject.toml uv.lock
git commit -m "Release 0.6.2"
git tag v0.6.2
git push origin main --tags
```

The tag must equal `v` plus the version in `pyproject.toml`. A mismatch fails
before any PyPI contact. That tag push is the only publish trigger.

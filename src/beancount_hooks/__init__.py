"""beancount-hooks — rule-based beangulp import hooks for Beancount v3.

Two layers, meant to be used together and in this order:

* :class:`Ruleset` — declarative rules that *assert* what a transaction is.
* the ``Rules*Predictor`` hooks — statistics over the existing ledger that *fill blanks*
  a rule did not cover.  They never overwrite a field that is already set.
"""

from importlib.metadata import PackageNotFoundError, version

from beancount_hooks.entries import map_transactions
from beancount_hooks.index import LedgerIndex
from beancount_hooks.normalizer import extract_keywords, normalize_payee, round_to_bin
from beancount_hooks.rules import RulesPayeePredictor, RulesPostingsPredictor, RulesTagsPredictor
from beancount_hooks.ruleset import Actions, Match, Rule, Ruleset

try:
    __version__ = version('beancount-hooks')
except PackageNotFoundError:  # source checkout, not installed
    __version__ = '0+unknown'

__all__ = [
    'Actions',
    'LedgerIndex',
    'Match',
    'Rule',
    'Ruleset',
    'RulesPostingsPredictor',
    'RulesPayeePredictor',
    'RulesTagsPredictor',
    'map_transactions',
    'normalize_payee',
    'extract_keywords',
    'round_to_bin',
]

"""beancount-hooks — rule-based beangulp import hooks for Beancount v3.

Public API exports the three predictors and the LedgerIndex utility.
"""

from beancount_hooks.index import LedgerIndex
from beancount_hooks.normalizer import extract_keywords, normalize_payee, round_to_bin
from beancount_hooks.rules import RulesPayeePredictor, RulesPostingsPredictor, RulesTagsPredictor

__version__ = "0.1.0"

__all__ = [
    "LedgerIndex",
    "RulesPostingsPredictor",
    "RulesPayeePredictor",
    "RulesTagsPredictor",
    "normalize_payee",
    "extract_keywords",
    "round_to_bin",
]

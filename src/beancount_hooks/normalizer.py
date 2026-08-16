"""Payee normalization and keyword extraction utilities.

Pure Python, stdlib only.
"""

from __future__ import annotations

import re
from typing import Final

# Stopwords for German, English, French — common in Swiss financial data.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        # German
        'der',
        'die',
        'das',
        'den',
        'dem',
        'des',
        'ein',
        'eine',
        'einer',
        'eines',
        'einem',
        'einen',
        'und',
        'für',
        'mit',
        'von',
        'zu',
        'bei',
        'nach',
        'aus',
        'an',
        'in',
        'auf',
        'über',
        'unter',
        'vor',
        'hinter',
        'zwischen',
        'durch',
        'wegen',
        'trotz',
        'während',
        'bis',
        'seit',
        'ohne',
        'gegen',
        'um',
        'als',
        'wie',
        'so',
        'auch',
        'noch',
        'nur',
        'schon',
        'immer',
        'nie',
        'manchmal',
        'oft',
        'selten',
        'hier',
        'dort',
        'da',
        'dann',
        'wann',
        'warum',
        'wo',
        'wer',
        'was',
        'wieso',
        'weshalb',
        # English
        'the',
        'a',
        'and',
        'for',
        'with',
        'of',
        'to',
        'at',
        'from',
        'by',
        'on',
        'over',
        'under',
        'before',
        'after',
        'between',
        'through',
        'because',
        'during',
        'until',
        'since',
        'without',
        'against',
        'about',
        'as',
        'like',
        'too',
        'very',
        'just',
        'only',
        'even',
        'still',
        'already',
        'always',
        'never',
        'sometimes',
        'often',
        'here',
        'there',
        'then',
        'when',
        'why',
        'where',
        'who',
        'what',
        'how',
        # French
        'le',
        'la',
        'les',
        'un',
        'une',
        'du',
        'de',
        'et',
        'pour',
        'avec',
        'par',
        'sur',
        'sous',
        'dans',
        'en',
        'à',
        'au',
        'aux',
        'chez',
        'entre',
        'sans',
        'contre',
        'vers',
        'pendant',
        'depuis',
        'jusque',
        'ainsi',
        'aussi',
        'trop',
        'très',
        'juste',
        'seulement',
        'encore',
        'déjà',
        'toujours',
        'jamais',
        'souvent',
        'ici',
        'là',
        'alors',
        'quand',
        'pourquoi',
        'où',
        'qui',
        'que',
        'quoi',
        'comment',
    }
)

# Words naming *how* money moved rather than *what* it bought.  Kept separate from the
# grammatical stopwords above because the reason for excluding them is different, and the
# test for adding one is specific: if the word tells you nothing about the category, it is
# payment mechanics.  Left in, they are actively harmful — "Twint" appears on a bus ticket
# and on a four-figure transfer between friends, so a keyword index learns to book every
# Twint payment as public transport.
_PAYMENT_MECHANICS: Final[frozenset[str]] = frozenset(
    {
        # Swiss payment rails
        'twint',
        'lsv',
        'sepa',
        'einzug',
        'dauerauftrag',
        'ebanking',
        'banking',
        'esr',
        # Instruments
        'kreditkarte',
        'debitkarte',
        'karte',
        'card',
        'visa',
        'mastercard',
        'maestro',
        # Movement, in three languages
        'zahlung',
        'zahlungen',
        'payment',
        'paiement',
        'überweisung',
        'transfer',
        'virement',
        'gutschrift',
        'belastung',
        'rechnung',
        'invoice',
        'facture',
    }
)

# Known suffixes to strip from payees for normalization.
_LEGAL_SUFFIXES: Final[list[str]] = [
    r'\s+ag\b',
    r'\s+gmbh\b',
    r'\s+sarl\b',
    r'\b(sa)\b',
    r'\s+inc\.?\s*$',
    r'\s+ltd\.?\b',
    r'\s+llc\b',
    r'\s+corp\.?\b',
    r'\s+corp\b',
    r'\s+co\.?\s*$',
    r'\s+co\b',
    r'\s+bv\b',
    r'\s+nv\b',
    r'\s+plc\b',
    r'\s+kg\b',
    r'\s+ohg\b',
    r'\s+ug\b',
]

# Known variants to unify (pattern → replacement).
_UNIFY_VARIANTS: Final[list[tuple[str, str]]] = [
    # Migros variants
    (r'^migros\s+.*', 'migros'),
    # Coop variants
    (r'^coop\s+.*', 'coop'),
    (r'^coop\b', 'coop'),
    # SBB variants
    (r'^sbb\s+cff\s+ffs\b', 'sbb'),
    (r'^sbb\b', 'sbb'),
    (r'^cff\b', 'sbb'),
    (r'^ffs\b', 'sbb'),
    # ZVV / public transport
    (r'^zvv\b', 'zvv'),
    (r'^vbsg\b', 'vbsg'),
    (r'^vbz\b', 'vbz'),
    # Swiss supermarkets / gas
    (r'^aldi\s+.*', 'aldi'),
    (r'^lidl\s+.*', 'lidl'),
    (r'^denner\s+.*', 'denner'),
    (r'^manor\s+.*', 'manor'),
    (r'^jumbo\s+.*', 'jumbo'),
    (r'^bauhaus\s+.*', 'bauhaus'),
    (r'^landi\s+.*', 'landi'),
    (r'^migrolino\b', 'migrolino'),
    (r'^migrol\b', 'migrol'),
    (r'^shell\s+.*', 'shell'),
    (r'^bp\s+.*', 'bp'),
    (r'^avia\s+.*', 'avia'),
    # Banks
    (r'^ubs\b', 'ubs'),
    (r'^credit\s+suisse\b', 'credit suisse'),
    (r'^zürcher\s+kantonalbank\b', 'zürcher kantonalbank'),
    (r'^raiffeisen\b', 'raiffeisen'),
    (r'^postfinance\b', 'postfinance'),
    # Tech
    (r'^apple\s+.*', 'apple'),
    (r'^google\s+.*', 'google'),
    (r'^microsoft\s+.*', 'microsoft'),
    (r'^amazon\s+.*', 'amazon'),
    (r'^spotify\s+.*', 'spotify'),
    (r'^netflix\s+.*', 'netflix'),
    (r'^dropbox\s+.*', 'dropbox'),
]


def normalize_payee(payee: str | None) -> str:
    """Normalize payee for fuzzy matching.

    Strips legal suffixes, postal codes, trailing numbers, and unifies
    known variants (e.g. "Migros Zürich" → "migros").

    Returns a lowercase string.  Empty/None input returns "".
    """
    if not payee:
        return ''

    normalized = payee.strip().lower()

    # Strip legal entity suffixes.
    for pattern in _LEGAL_SUFFIXES:
        normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)

    # Strip trailing numbers and postal codes (4-digit Swiss / 5-digit generic).
    normalized = re.sub(r'\s+\d{4,5}\b', '', normalized)
    normalized = re.sub(r'\s+\d+\s*$', '', normalized)

    # Strip trailing location in parentheses / brackets.
    normalized = re.sub(r'\s*[\(\[].*?[\)\]]', '', normalized)

    # Unify known variants.
    for pattern, replacement in _UNIFY_VARIANTS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            normalized = replacement
            break

    # Strip trailing dots left after suffix stripping.
    normalized = re.sub(r'\s+\.', '', normalized)
    normalized = re.sub(r'\.$|,', '', normalized)
    # Strip TLD suffixes (.de, .com, .ch, .net, .org, .io, etc.)
    normalized = re.sub(r'\.\w{2,6}$', '', normalized)

    # Collapse multiple spaces and strip again.
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def extract_keywords(narration: str | None, min_length: int = 3) -> list[str]:
    """Extract meaningful keywords from narration.

    Splits on whitespace and punctuation, filters grammatical stopwords and payment
    mechanics, and drops tokens shorter than *min_length* unless they are known
    abbreviations.

    Returns a deduplicated list in insertion order.
    """
    if not narration:
        return []

    # Split on non-alphanumeric characters.
    tokens = re.split(r'[^\w\-]', narration.lower())

    keywords: list[str] = []
    seen: set[str] = set()
    known_abbrs: set[str] = {'chf', 'eur', 'usd', 'gbp', 'top', 'vip', 'atm', 'pos', 'iban', 'bic'}

    for token in tokens:
        token = token.strip('-')
        if not token:
            continue
        if token in _STOPWORDS or token in _PAYMENT_MECHANICS:
            continue
        if len(token) < min_length and token not in known_abbrs:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)

    return keywords


def round_to_bin(amount: float, bin_size: float = 10.0) -> float:
    """Round a positive amount to the nearest multiple of *bin_size*.

    >>> round_to_bin(47.5, 10)
    50.0
    >>> round_to_bin(3.2, 10)
    0.0
    """
    return round(amount / bin_size) * bin_size

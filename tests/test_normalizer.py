"""Tests for beancount_hooks.normalizer."""

from __future__ import annotations

import pytest

from beancount_hooks.normalizer import extract_keywords, normalize_payee, round_to_bin


class TestNormalizePayee:
    def test_empty_and_none(self) -> None:
        assert normalize_payee('') == ''
        assert normalize_payee(None) == ''

    def test_no_change(self) -> None:
        assert normalize_payee('Migros') == 'migros'
        assert normalize_payee('Coop') == 'coop'

    def test_legal_suffixes(self) -> None:
        assert normalize_payee('Acme AG') == 'acme'
        assert normalize_payee('Tech GmbH') == 'tech'
        assert normalize_payee('Shop Sarl') == 'shop'
        assert normalize_payee('Global SA') == 'global'
        assert normalize_payee('Mega Inc.') == 'mega'
        assert normalize_payee('Widgets Ltd') == 'widgets'

    def test_postal_codes(self) -> None:
        assert normalize_payee('Migros 8001') == 'migros'
        assert normalize_payee('Coop 8050') == 'coop'

    def test_trailing_numbers(self) -> None:
        assert normalize_payee('Store 123') == 'store'

    def test_location_in_parens(self) -> None:
        assert normalize_payee('Migros (Zürich)') == 'migros'
        assert normalize_payee('Coop [Basel]') == 'coop'

    def test_unify_migros_variants(self) -> None:
        assert normalize_payee('Migros Zürich') == 'migros'
        assert normalize_payee('Migros Basel') == 'migros'
        assert normalize_payee('MIGROS BERN') == 'migros'

    def test_migrol_and_migrolino_are_not_the_supermarket(self) -> None:
        assert normalize_payee('Migrolino') == 'migrolino'
        assert normalize_payee('Migrol') == 'migrol'
        assert normalize_payee('Migrol Tanken (80.00)') == 'migrol'
        assert normalize_payee('Migrolino Benzin') == 'migrolino'
        assert normalize_payee('Migrolino Zürich HB') == 'migrolino'

    def test_unify_coop_variants(self) -> None:
        assert normalize_payee('Coop Pronto') == 'coop'
        assert normalize_payee('Coop City') == 'coop'
        assert normalize_payee('Coop') == 'coop'

    def test_unify_sbb_variants(self) -> None:
        assert normalize_payee('SBB CFF FFS') == 'sbb'
        assert normalize_payee('sbb') == 'sbb'
        assert normalize_payee('cff') == 'sbb'
        assert normalize_payee('ffs') == 'sbb'

    def test_unify_tech(self) -> None:
        assert normalize_payee('Apple Store') == 'apple'
        assert normalize_payee('Apple Inc.') == 'apple'
        assert normalize_payee('Amazon.de') == 'amazon'
        assert normalize_payee('Google Ireland') == 'google'

    def test_unify_7_eleven_and_uniqlo_variants(self) -> None:
        assert normalize_payee('7-Eleven Zürich') == '7-eleven'
        assert normalize_payee('7-ELEVEN HB') == '7-eleven'
        assert normalize_payee('Uniqlo Zürich HB') == 'uniqlo'
        assert normalize_payee('UNIQLO STORE') == 'uniqlo'

    def test_combined_strip(self) -> None:
        assert normalize_payee('Migros AG 8001') == 'migros'
        assert normalize_payee('Coop GmbH (Basel) 4051') == 'coop'

    def test_case_insensitivity(self) -> None:
        assert normalize_payee('MIGROS') == 'migros'
        assert normalize_payee('SBB CFF FFS') == 'sbb'

    def test_preserve_unknown(self) -> None:
        assert normalize_payee('Local Bakery') == 'local bakery'


class TestExtractKeywords:
    def test_empty_and_none(self) -> None:
        assert extract_keywords('') == []
        assert extract_keywords(None) == []

    def test_basic_extraction(self) -> None:
        result = extract_keywords('Weekly groceries at Migros')
        assert 'weekly' in result
        assert 'groceries' in result
        assert 'migros' in result
        # Stopwords should be filtered.
        assert 'at' not in result

    def test_stopwords_filtered(self) -> None:
        result = extract_keywords('der die das und für mit von zu')
        assert result == []

    def test_short_tokens_excluded(self) -> None:
        result = extract_keywords('I paid CHF 50 for some xy thing')
        # "CHF" is a known abbreviation so it's kept.
        assert 'chf' in result
        # "xy" is too short and not a known abbr.
        assert 'xy' not in result

    def test_deduplication(self) -> None:
        result = extract_keywords('Migros and migros')
        assert result.count('migros') == 1

    def test_punctuation_split(self) -> None:
        result = extract_keywords('Groceries: Migros, Zurich.')
        assert 'groceries' in result
        assert 'migros' in result
        assert 'zurich' in result

    @pytest.mark.parametrize(
        'word',
        ['Twint', 'LSV', 'Einzug', 'Dauerauftrag', 'Zahlung', 'Payment', 'Kreditkarte'],
    )
    def test_payment_mechanics_are_not_keywords(self, word) -> None:
        """How money moved says nothing about what it bought.

        "Twint" appears on a 6.50 bus ticket and on a 1334.40 transfer between friends, so
        indexing it teaches the predictor to book every Twint payment as public transport.
        """
        assert extract_keywords(word) == []

    def test_a_real_word_survives_alongside_mechanics(self) -> None:
        assert extract_keywords('Twint Zahlung Coiffeur') == ['coiffeur']

    def test_german_stopwords(self) -> None:
        result = extract_keywords('Miete für die Wohnung')
        assert 'miete' in result
        assert 'wohnung' in result
        assert 'für' not in result
        assert 'die' not in result


class TestRoundToBin:
    def test_basic(self) -> None:
        assert round_to_bin(47.5, 10) == 50.0
        assert round_to_bin(3.2, 10) == 0.0
        assert round_to_bin(15.0, 10) == 20.0
        assert round_to_bin(14.9, 10) == 10.0

    def test_exact_multiple(self) -> None:
        assert round_to_bin(100.0, 10) == 100.0
        assert round_to_bin(50.0, 10) == 50.0

    def test_custom_bin_size(self) -> None:
        assert round_to_bin(75.0, 50) == 100.0
        assert round_to_bin(24.0, 50) == 0.0

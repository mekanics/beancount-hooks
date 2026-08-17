"""Tests for beancount_hooks.rules predictors."""

from __future__ import annotations

import datetime

import pytest
from beancount.core.amount import Amount
from beancount.core.data import Posting, Transaction, filter_txns, new_metadata
from beancount.core.number import D

from beancount_hooks.rules import (
    RulesPayeePredictor,
    RulesPostingsPredictor,
    RulesTagsPredictor,
)


def _make_imported(
    payee: str | None,
    narration: str,
    importer_account: str = 'Assets:Bank:CHF',
    amounts: list[str] | None = None,
    accounts: list[str] | None = None,
) -> list[tuple[str, list, str, object]]:
    """Build a single-file import entry tuple."""
    meta = new_metadata('import.csv', 1)
    if amounts is None:
        amounts = ['-100']
    postings = []
    for i, amt in enumerate(amounts):
        if accounts and i < len(accounts):
            account = accounts[i]
        elif i == 0:
            account = importer_account
        else:
            # Second posting (if present) is a placeholder — will be replaced.
            account = 'UNKNOWN'
        postings.append(
            Posting(
                account=account,
                units=Amount(D(amt), 'CHF'),
                cost=None,
                price=None,
                flag=None,
                meta=None,
            )
        )
    txn = Transaction(
        meta=meta,
        date=datetime.date(2024, 6, 1),
        flag='*',
        payee=payee,
        narration=narration,
        tags=frozenset(),
        links=frozenset(),
        postings=postings,
    )
    return [('import.csv', [txn], importer_account, None)]


def _imported_txns(imported) -> list[Transaction]:
    """Extract Transaction objects from imported_entries."""
    txns = []
    for _filename, entries, _account, _importer in imported:
        for entry in filter_txns(entries):
            txns.append(entry)
    return txns


def _account_names(txn: Transaction) -> list[str]:
    return sorted({p.account for p in txn.postings if p.account})


def _split_txn(
    payee: str,
    source: str,
    legs: dict[str, str],
    month: int,
    source_account: str = 'Assets:Bank:CHF',
) -> Transaction:
    """A historical transaction where *source* was divided across *legs*."""
    return Transaction(
        new_metadata('ledger.bean', month),
        datetime.date(2025, month, 1),
        '*',
        payee,
        '',
        frozenset(),
        frozenset(),
        [
            Posting(source_account, Amount(D(source), 'CHF'), None, None, None, None),
            *(
                Posting(account, Amount(D(value), 'CHF'), None, None, None, None)
                for account, value in legs.items()
            ),
        ],
    )


def _loads_cleanly(txn: Transaction) -> bool:
    """Whether Beancount accepts *txn* — the check a balance or auto-posting bug fails."""
    from beancount import loader
    from beancount.parser import printer

    header = 'option "operating_currency" "CHF"\n' + ''.join(
        f'2020-01-01 open {account}\n' for account in _account_names(txn)
    )
    _entries, errors, _options = loader.load_string(header + '\n' + printer.format_entry(txn))
    assert errors == [], [printer.format_error(e) for e in errors]
    return True


# =============================================================================
# RulesPostingsPredictor
# =============================================================================


class TestRulesPostingsPredictor:
    def test_exact_payee_rule(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros', narration='Groceries')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert 'Expenses:Groceries' in accounts

    def test_normalized_payee_rule(self, ledger_normalized_payees) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros Basel', narration='Shopping')
        result = predictor.hook(imported, ledger_normalized_payees)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert 'Expenses:Groceries' in accounts

    def test_keyword_rule(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        # "Quick shop" matches the narration of Coop transactions.
        imported = _make_imported(payee='', narration='Quick shop')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Coop is below threshold for exact payee but keyword may hit.
        # "quick" appears 4 times from Coop, "shop" appears 4 times.
        # The aggregated count for Expenses:Groceries is 4.
        # With min_occurrence=3 it should match.
        # But wait — we need at least one posting with units to trigger keyword rule.
        # The imported entry has one posting with -100 CHF, so keyword rule can run.
        assert 'Expenses:Groceries' in accounts

    def test_amount_rule(self, ledger_amount_patterns) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Landlord AG', narration='Rent', amounts=['-1800'])
        result = predictor.hook(imported, ledger_amount_patterns)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert 'Expenses:Housing:Rent' in accounts

    def test_rule_5_counterpart(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3, enable_rule_5=True)
        # Use a payee with no exact/normalized/keyword/amount match.
        imported = _make_imported(payee='Unknown', narration='Something')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Rule 5: most common counterpart to Assets:Bank:CHF is Expenses:Groceries (≥10)
        # Wait — we only have 5 Migros + 4 Coop + 3 SBB + 5 Salary + 6 Rent = 23 total
        # But Expenses:Groceries appears 9 times (5 + 4), which is < 10.
        # Let's check: Expenses:Transport = 3, Income:Salary = 5, Expenses:Housing:Rent = 6.
        # None of those are ≥10, so rule 5 should NOT match.
        assert 'Expenses:Groceries' not in accounts

    def test_rule_5_counterpart_above_threshold(self) -> None:
        # Build a ledger where Expenses:Groceries appears ≥10 times.
        from datetime import date

        from beancount.core.amount import Amount
        from beancount.core.data import Posting, Transaction, new_metadata
        from beancount.core.number import D

        entries = []
        for i in range(12):
            meta = new_metadata('test.beancount', i + 1)
            postings = [
                Posting(
                    account='Assets:Bank:CHF',
                    units=Amount(D('-50'), 'CHF'),
                    cost=None,
                    price=None,
                    flag=None,
                    meta=None,
                ),
                Posting(
                    account='Expenses:Groceries',
                    units=Amount(D('50'), 'CHF'),
                    cost=None,
                    price=None,
                    flag=None,
                    meta=None,
                ),
            ]
            entries.append(
                Transaction(
                    meta=meta,
                    date=date(2024, 1, i + 1),
                    flag='*',
                    payee='Migros',
                    narration='Groceries',
                    tags=frozenset(),
                    links=frozenset(),
                    postings=postings,
                )
            )
        predictor = RulesPostingsPredictor(min_occurrence=3, enable_rule_5=True)
        imported = _make_imported(payee='Unknown', narration='Something')
        result = predictor.hook(imported, entries)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        assert 'Expenses:Groceries' in accounts

    def test_no_prediction_when_importer_not_in_set(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        # Importer account is Assets:Bank:CHF which IS in the set.
        # Let's test when importer account is NOT in predicted set.
        # Create a ledger where Migros maps to a set without Assets:Bank:CHF.
        from datetime import date

        from beancount.core.amount import Amount
        from beancount.core.data import Posting, Transaction, new_metadata
        from beancount.core.number import D

        def _make_txn(p, n, accts):
            meta = new_metadata('test.beancount', 1)
            postings = []
            for a in accts:
                postings.append(
                    Posting(
                        account=a,
                        units=Amount(D('0'), 'CHF'),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    )
                )
            return Transaction(
                meta=meta,
                date=date(2024, 1, 1),
                flag='*',
                payee=p,
                narration=n,
                tags=frozenset(),
                links=frozenset(),
                postings=postings,
            )

        entries = [
            _make_txn('Migros', 'Groceries', ['Liabilities:CreditCard', 'Expenses:Groceries'])
        ] * 5
        imported = _make_imported(payee='Migros', narration='Groceries')
        result = predictor.hook(imported, entries)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Predicted set doesn't include importer account → skip.
        assert 'Expenses:Groceries' not in accounts

    def test_empty_ledger(self, ledger_empty) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros', narration='Groceries')
        result = predictor.hook(imported, ledger_empty)
        txn = _imported_txns(result)[0]
        accounts = _account_names(txn)
        # Empty ledger → no rules fire → no prediction.
        # The importer provides one leg (Assets:Bank:CHF).
        assert accounts == ['Assets:Bank:CHF']

    def test_caching(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros', narration='Groceries')
        predictor.hook(imported, ledger_multi_payee)
        # Second call with same existing_entries should use cache.
        predictor.hook(imported, ledger_multi_payee)
        assert predictor._cache[0] is not None
        assert predictor._cache[1] is not None


# =============================================================================
# RulesPayeePredictor
# =============================================================================


class TestRulesPayeePredictor:
    @pytest.mark.parametrize(
        'payee',
        [
            'SBB CFF FFS',  # normalize_payee -> 'sbb'
            'Migros Zürich',  # -> 'migros'
            'b.side digital GmbH',  # -> 'b.side digital'
            'Property-Mgmt AG',  # -> 'property-mgmt'
        ],
    )
    def test_never_overwrites_an_existing_payee(self, payee, ledger_multi_payee) -> None:
        """normalize_payee produces index keys, not display names.

        Writing them back to entry.payee destroys curated names — including the ones a
        Ruleset deliberately set.  Predictors fill blanks; they never overwrite.
        """
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee=payee, narration='Whatever')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == payee

    def test_derive_from_narration_salary(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee='', narration='Salary from Acme Corp')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == 'Acme Corp'

    def test_derive_from_narration_transfer(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee='', narration='Transfer to Savings Account')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == 'Savings Account'

    def test_keep_existing_if_no_rule_matches(self, ledger_multi_payee) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee='Local Shop', narration='Purchase')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.payee == 'Local Shop'

    def test_missing_payee_with_unrecognised_narration_stays_empty(
        self, ledger_multi_payee
    ) -> None:
        predictor = RulesPayeePredictor()
        imported = _make_imported(payee='', narration='POS 4711 Zürich')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        # A blank prediction beats a wrong one.
        assert not txn.payee


# =============================================================================
# RulesTagsPredictor
# =============================================================================


class TestRulesTagsPredictor:
    def test_tag_prediction_writes_native_tags(self, ledger_multi_payee) -> None:
        """Tags belong on entry.tags, not in meta.

        A meta key renders as `predicted_tags: "housing,recurring"`, which the user then
        has to retype as `#housing #recurring` by hand.
        """
        predictor = RulesTagsPredictor(min_tag_occurrence=5, max_tags=3)
        imported = _make_imported(
            payee='Landlord AG',
            narration='Rent',
            amounts=['-1800', '1800'],
            accounts=['Assets:Bank:CHF', 'Expenses:Housing:Rent'],
        )
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.tags == frozenset({'housing', 'recurring'})
        assert 'predicted_tags' not in txn.meta

    def test_existing_tags_are_preserved(self, ledger_multi_payee) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=5, max_tags=3)
        imported = _make_imported(
            payee='Landlord AG',
            narration='Rent',
            amounts=['-1800', '1800'],
            accounts=['Assets:Bank:CHF', 'Expenses:Housing:Rent'],
        )
        entries = imported[0][1]
        entries[0] = entries[0]._replace(tags=frozenset({'business'}))
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert txn.tags == frozenset({'business', 'housing', 'recurring'})

    def test_below_threshold(self, ledger_multi_payee) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=20, max_tags=3)
        imported = _make_imported(payee='Landlord AG', narration='Rent', amounts=['-1800', '1800'])
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        # Landlord AG tags appear 6 times each, below threshold of 20.
        assert txn.tags == frozenset()

    def test_empty_ledger(self, ledger_empty) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=1, max_tags=3)
        imported = _make_imported(payee='Landlord AG', narration='Rent')
        result = predictor.hook(imported, ledger_empty)
        txn = _imported_txns(result)[0]
        assert txn.tags == frozenset()

    def test_max_tags_limit(self, ledger_multi_payee) -> None:
        predictor = RulesTagsPredictor(min_tag_occurrence=1, max_tags=1)
        imported = _make_imported(payee='Landlord AG', narration='Rent', amounts=['-1800', '1800'])
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert len(txn.tags) == 1


# =============================================================================
# Predicted postings must balance
# =============================================================================


class TestPredictedPostingsBalance:
    """A filled leg must carry no units so Beancount interpolates the amount.

    An explicit `0 CHF` leaves the transaction unbalanced, which means every successful
    prediction produces a file the loader rejects.
    """

    def test_predicted_leg_has_no_units(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros', narration='Groceries')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        predicted = [p for p in txn.postings if p.account == 'Expenses:Groceries']
        assert len(predicted) == 1
        assert predicted[0].units is None

    def test_predicted_txn_loads_without_errors(self, ledger_multi_payee) -> None:
        from beancount import loader
        from beancount.parser import printer

        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros', narration='Groceries')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]

        header = (
            'option "operating_currency" "CHF"\n'
            '2020-01-01 open Assets:Bank:CHF CHF\n'
            '2020-01-01 open Expenses:Groceries CHF\n'
        )
        _entries, errors, _options = loader.load_string(header + '\n' + printer.format_entry(txn))
        assert errors == [], [printer.format_error(e) for e in errors]

    def test_importer_leg_is_left_alone(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(payee='Migros', narration='Groceries')
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        bank = [p for p in txn.postings if p.account == 'Assets:Bank:CHF']
        assert len(bank) == 1
        assert bank[0].units == Amount(D('-100'), 'CHF')

    def test_no_duplicate_posting_when_account_already_present(self, ledger_multi_payee) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(
            payee='Migros',
            narration='Groceries',
            amounts=['-100', '100'],
            accounts=['Assets:Bank:CHF', 'Expenses:Groceries'],
        )
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == ['Assets:Bank:CHF', 'Expenses:Groceries']

    def test_a_payee_that_changed_cards_is_answered_by_the_card_it_is_on(
        self, ledger_changed_card
    ) -> None:
        """The retired card leads on lifetime count and is not what the importer is reading.

        Before every rung asked about one account, the payee rungs came back with the old card,
        the mismatch was thrown away, and a subscription with years of history on the current
        card got nothing.
        """
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(
            payee='Nespresso',
            narration='Coffee capsules',
            importer_account='Assets:Card:New',
            amounts=['-25.00'],
        )
        txn = _imported_txns(predictor.hook(imported, ledger_changed_card))[0]
        assert [p.account for p in txn.postings] == ['Assets:Card:New', 'Expenses:Coffee']
        assert txn.postings[1].units is None

    def test_the_old_card_is_never_offered_as_the_other_leg(self, ledger_changed_card) -> None:
        """Narrowing must not turn the retired card into a counterpart of the new one."""
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(
            payee='Nespresso',
            narration='Coffee capsules',
            importer_account='Assets:Card:New',
            amounts=['-25.00'],
        )
        txn = _imported_txns(predictor.hook(imported, ledger_changed_card))[0]
        assert 'Assets:Card:Old' not in [p.account for p in txn.postings]

    def test_a_card_the_payee_never_touched_gets_no_prediction(self, ledger_changed_card) -> None:
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(
            payee='Nespresso',
            narration='Coffee capsules',
            importer_account='Assets:Card:Third',
            amounts=['-25.00'],
        )
        txn = _imported_txns(predictor.hook(imported, ledger_changed_card))[0]
        assert [p.account for p in txn.postings] == ['Assets:Card:Third']

    def test_a_split_in_no_settled_proportion_gets_no_prediction(self) -> None:
        """Beancount accepts only one posting without an amount.

        A health-insurance premium is split across basic and supplementary cover in
        proportions renegotiated every year.  Filling both in without amounts fails to load
        with "You may not have more than one auto-posting per currency", and there is no
        proportion to fill them in *with*, so the legs stay blank for review.
        """
        history = [
            _split_txn(
                'HealthInsurer',
                '-400.00',
                {'Expenses:Insurance:KVG': '300.00', 'Expenses:Insurance:VVG': '100.00'},
                month,
            )
            for month in range(1, 4)
        ] + [
            # Next year's premium: same cover, a different division of it.
            _split_txn(
                'HealthInsurer',
                '-450.00',
                {'Expenses:Insurance:KVG': '350.00', 'Expenses:Insurance:VVG': '100.00'},
                month,
            )
            for month in range(4, 7)
        ]
        imported = _make_imported(payee='HealthInsurer', narration='')
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == ['Assets:Bank:CHF']

    def test_a_split_the_ledger_always_makes_the_same_way_is_proposed(self) -> None:
        """A hotel halved with a partner is halved every time, so the halves can be filled in.

        This is the difference from the premium above: the proportion is a habit, so there is
        an answer to give.  Without it the predictor knows exactly which accounts are involved
        and has to stay silent, which is what happened to every Booking.com import.
        """
        history = [
            _split_txn(
                'Booking.com',
                amount,
                {
                    'Expenses:Travel:Hotel': halved,
                    'Assets:Owed-to-Me:Partner': halved,
                },
                month,
            )
            for month, (amount, halved) in enumerate(
                [
                    ('-74.45', '37.225'),
                    ('-149.70', '74.85'),
                    ('-61.70', '30.85'),
                    ('-44.50', '22.25'),
                ],
                start=1,
            )
        ]
        imported = _make_imported(payee='Booking.com', narration='', amounts=['-412.05'])
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]

        assert [(p.account, str(p.units)) for p in txn.postings] == [
            ('Assets:Bank:CHF', '-412.05 CHF'),
            ('Assets:Owed-to-Me:Partner', '206.025 CHF'),
            ('Expenses:Travel:Hotel', '206.025 CHF'),
        ]
        assert _loads_cleanly(txn)

    def test_a_neighbours_unstable_split_does_not_veto_a_stable_payee(self) -> None:
        """Fractions are a habit of the payee, not of three account names.

        Denner books cigarettes to the partner account and a snack to groceries, so the
        same set {Bank, Groceries, Partner} divides ~95/5.  Migros always halves.  A
        table keyed only by the set sees a 50-point range and stays silent for both.
        """
        history = [
            _split_txn(
                'Migros',
                '-40.00',
                {'Expenses:Groceries': '20.00', 'Assets:Owed-to-Me:Partner': '20.00'},
                month,
            )
            for month in range(1, 5)
        ] + [
            _split_txn(
                'Denner',
                '-80.00',
                {'Expenses:Groceries': '4.00', 'Assets:Owed-to-Me:Partner': '76.00'},
                month,
            )
            for month in range(5, 9)
        ]
        imported = _make_imported(payee='Migros', narration='', amounts=['-23.90'])
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]

        assert [(p.account, str(p.units)) for p in txn.postings] == [
            ('Assets:Bank:CHF', '-23.90 CHF'),
            ('Assets:Owed-to-Me:Partner', '11.95 CHF'),
            ('Expenses:Groceries', '11.95 CHF'),
        ]
        assert _loads_cleanly(txn)

    def test_a_normalized_payee_still_gets_the_settled_split(self) -> None:
        """A store spelling with no history of its own still uses the brand's halves."""
        history = [
            _split_txn(
                'Migros Kreuzplatz',
                '-30.00',
                {'Expenses:Groceries': '15.00', 'Assets:Owed-to-Me:Partner': '15.00'},
                1,
            ),
            _split_txn(
                'Migros Kreuzplatz',
                '-12.00',
                {'Expenses:Groceries': '6.00', 'Assets:Owed-to-Me:Partner': '6.00'},
                2,
            ),
            _split_txn(
                'Migros',
                '-40.00',
                {'Expenses:Groceries': '20.00', 'Assets:Owed-to-Me:Partner': '20.00'},
                3,
            ),
            _split_txn(
                'Migros',
                '-8.00',
                {'Expenses:Groceries': '4.00', 'Assets:Owed-to-Me:Partner': '4.00'},
                4,
            ),
            # Same three accounts, a different payee, a different division — must not
            # be in the brand's bucket once fractions are keyed by payee.
            *(
                _split_txn(
                    'Denner',
                    '-80.00',
                    {'Expenses:Groceries': '4.00', 'Assets:Owed-to-Me:Partner': '76.00'},
                    month,
                )
                for month in range(5, 9)
            ),
        ]
        imported = _make_imported(payee='Migros Basel', narration='', amounts=['-23.90'])
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]

        assert [(p.account, str(p.units)) for p in txn.postings] == [
            ('Assets:Bank:CHF', '-23.90 CHF'),
            ('Assets:Owed-to-Me:Partner', '11.95 CHF'),
            ('Expenses:Groceries', '11.95 CHF'),
        ]
        assert _loads_cleanly(txn)

    def test_an_odd_amount_still_balances_to_the_rappen(self) -> None:
        """Halving 33.35 leaves a remainder, and the last leg has to absorb it."""
        history = [
            _split_txn(
                'Booking.com',
                '-100.00',
                {'Expenses:Travel:Hotel': '50.00', 'Assets:Owed-to-Me:Partner': '50.00'},
                month,
            )
            for month in range(1, 5)
        ]
        imported = _make_imported(payee='Booking.com', narration='', amounts=['-33.35'])
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]

        total = sum(p.units.number for p in txn.postings)
        assert total == D('0'), f'legs do not balance: {[str(p.units) for p in txn.postings]}'
        assert _loads_cleanly(txn)

    def test_the_payee_blind_fallback_is_off_by_default(self, ledger_multi_payee) -> None:
        """An unrecognised payee must reach review with a blank leg, not a plausible guess.

        Rung 5 answers with the account's most frequent counterpart regardless of the
        transaction, so on a spending account every unknown payee lands on whatever you buy
        most often.  A wrong account that looks reasonable is harder to catch than a hole.
        """
        imported = _make_imported(payee='Totally Unknown Shop', narration='Something')
        result = RulesPostingsPredictor().hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == ['Assets:Bank:CHF']

    def test_the_payee_blind_fallback_can_be_switched_on(self) -> None:
        """Still available for a single-purpose account where the guess is always right."""
        history = [
            Transaction(
                new_metadata('ledger.bean', i),
                datetime.date(2025, 1, 1),
                '*',
                f'Shop {i}',
                'Groceries',
                frozenset(),
                frozenset(),
                [
                    Posting('Assets:Bank:CHF', Amount(D('-20'), 'CHF'), None, None, None, None),
                    Posting('Expenses:Groceries', Amount(D('20'), 'CHF'), None, None, None, None),
                ],
            )
            for i in range(12)
        ]
        imported = _make_imported(payee='Totally Unknown Shop', narration='Something')
        result = RulesPostingsPredictor(enable_rule_5=True).hook(imported, history)
        txn = _imported_txns(result)[0]
        assert _account_names(txn) == ['Assets:Bank:CHF', 'Expenses:Groceries']

    def test_complete_transaction_is_left_alone(self, ledger_multi_payee) -> None:
        """A transaction that already balances must not gain another leg.

        Beancount allows only one posting without an amount per currency, so predicting on
        top of a leg someone else supplied produces a file it refuses to load.
        """
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(
            payee='Migros',
            narration='Groceries',
            amounts=['-100', '100'],
            accounts=['Assets:Bank:CHF', 'Expenses:Something-Else'],
        )
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == [
            'Assets:Bank:CHF',
            'Expenses:Something-Else',
        ]


class TestResidualCompleteness:
    """Completeness is the residual, not the account count."""

    def test_bank_leg_plus_fee_gains_a_third_posting(self, ledger_multi_payee) -> None:
        """A Yuh-shaped foreign purchase already names the fee; the residual still needs filling."""
        predictor = RulesPostingsPredictor(min_occurrence=3)
        imported = _make_imported(
            payee='Migros',
            narration='Groceries',
            amounts=['-100', '2'],
            accounts=['Assets:Bank:CHF', 'Expenses:Fees:Yuh'],
        )
        result = predictor.hook(imported, ledger_multi_payee)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == [
            'Assets:Bank:CHF',
            'Expenses:Fees:Yuh',
            'Expenses:Groceries',
        ]
        assert txn.postings[2].units is None
        assert _loads_cleanly(txn)

    def test_fee_in_history_resolves_to_one_auto_posting(self) -> None:
        """History that includes the fee account must not propose a split of what is left."""
        history = [
            Transaction(
                new_metadata('ledger.bean', month),
                datetime.date(2025, month, 1),
                '*',
                'Restaurant X',
                '',
                frozenset(),
                frozenset(),
                [
                    Posting('Assets:Bank:CHF', Amount(D('-50'), 'CHF'), None, None, None, None),
                    Posting('Expenses:Fees:Yuh', Amount(D('2'), 'CHF'), None, None, None, None),
                    Posting('Expenses:Restaurant', Amount(D('48'), 'CHF'), None, None, None, None),
                ],
            )
            for month in range(1, 5)
        ]
        imported = _make_imported(
            payee='Restaurant X',
            narration='',
            amounts=['-50', '2'],
            accounts=['Assets:Bank:CHF', 'Expenses:Fees:Yuh'],
        )
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == [
            'Assets:Bank:CHF',
            'Expenses:Fees:Yuh',
            'Expenses:Restaurant',
        ]
        assert txn.postings[2].units is None
        assert _loads_cleanly(txn)

    def test_unbalanced_two_account_entry_declines_a_multi_leg_split(self) -> None:
        """Historical fractions include a leg that is already present; allocating them would not balance."""
        history = [
            _split_txn(
                'Shared Meal',
                '-100',
                {
                    'Expenses:Fees:Yuh': '2',
                    'Expenses:Restaurant': '49',
                    'Assets:Owed-to-Me:Friend': '49',
                },
                month,
            )
            for month in range(1, 5)
        ]
        imported = _make_imported(
            payee='Shared Meal',
            narration='',
            amounts=['-100', '2'],
            accounts=['Assets:Bank:CHF', 'Expenses:Fees:Yuh'],
        )
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, history)
        txn = _imported_txns(result)[0]
        assert [p.account for p in txn.postings] == [
            'Assets:Bank:CHF',
            'Expenses:Fees:Yuh',
        ]

    def test_multi_currency_residual_is_declined(self, ledger_multi_payee) -> None:
        """One posting cannot resolve a residual that spans two currencies."""
        meta = new_metadata('import.csv', 1)
        txn = Transaction(
            meta,
            datetime.date(2024, 6, 1),
            '*',
            'Migros',
            'Groceries',
            frozenset(),
            frozenset(),
            [
                Posting('Assets:Bank:CHF', Amount(D('-100'), 'CHF'), None, None, None, None),
                Posting('Assets:Bank:USD', Amount(D('-10'), 'USD'), None, None, None, None),
            ],
        )
        imported = [('import.csv', [txn], 'Assets:Bank:CHF', None)]
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, ledger_multi_payee)
        out = _imported_txns(result)[0]
        assert [p.account for p in out.postings] == ['Assets:Bank:CHF', 'Assets:Bank:USD']

    def test_existing_auto_posting_is_declined(self, ledger_multi_payee) -> None:
        """A second auto-posting is an error; leave the entry alone."""
        meta = new_metadata('import.csv', 1)
        txn = Transaction(
            meta,
            datetime.date(2024, 6, 1),
            '*',
            'Migros',
            'Groceries',
            frozenset(),
            frozenset(),
            [
                Posting('Assets:Bank:CHF', Amount(D('-100'), 'CHF'), None, None, None, None),
                Posting('Expenses:Something', None, None, None, None, None),
            ],
        )
        imported = [('import.csv', [txn], 'Assets:Bank:CHF', None)]
        result = RulesPostingsPredictor(min_occurrence=3).hook(imported, ledger_multi_payee)
        out = _imported_txns(result)[0]
        assert [p.account for p in out.postings] == ['Assets:Bank:CHF', 'Expenses:Something']
        assert out.postings[1].units is None

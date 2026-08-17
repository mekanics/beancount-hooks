"""Tests for beancount_hooks.ruleset."""

from __future__ import annotations

import datetime
import logging

import pytest
from beancount.core.amount import Amount
from beancount.core.data import Balance, Posting, Transaction, new_metadata
from beancount.core.number import D

from beancount_hooks.ruleset import Actions, Match, Rule, Ruleset

BANK = 'Assets:Bank:CHF'


def txn(
    payee: str | None = 'Coop',
    narration: str = 'Purchase',
    amount: str | None = '-20.00',
    currency: str = 'CHF',
    tags: tuple[str, ...] = (),
    links: tuple[str, ...] = (),
    account: str = BANK,
) -> Transaction:
    postings = []
    if amount is not None:
        postings.append(Posting(account, Amount(D(amount), currency), None, None, None, None))
    else:
        postings.append(Posting(account, None, None, None, None, None))
    return Transaction(
        meta=new_metadata('import.csv', 1),
        date=datetime.date(2026, 8, 1),
        flag='*',
        payee=payee,
        narration=narration,
        tags=frozenset(tags),
        links=frozenset(links),
        postings=postings,
    )


def imported(entries: list, account: str = BANK) -> list[tuple[str, list, str, object]]:
    return [('import.csv', entries, account, None)]


def postings_of(transaction: Transaction) -> list[tuple[str, str | None]]:
    return [
        (p.account, str(p.units) if p.units is not None else None) for p in transaction.postings
    ]


def loads_cleanly(transaction: Transaction, accounts: list[str]) -> list[str]:
    """Render *transaction* and load it, returning any error messages."""
    from beancount import loader
    from beancount.parser import printer

    header = 'option "operating_currency" "CHF"\n' + ''.join(
        f'2020-01-01 open {account}\n' for account in accounts
    )
    _entries, errors, _options = loader.load_string(
        header + '\n' + printer.format_entry(transaction)
    )
    return [error.message for error in errors]


# =============================================================================
# The three rules that shaped the design
# =============================================================================


class TestRealWorldRules:
    """Cases that exercise splits, pinned amounts, and sign-sensitive matches."""

    def test_rental_halves_a_shared_payout(self) -> None:
        """Income shared with someone else: half owed to them, half booked as income."""
        rules = Ruleset(
            [
                Rule(
                    Match(payee='RentalPlatform'),
                    Actions(
                        split=(
                            ('Assets:Owed-to-Me:Friend', D('0.5')),
                            ('Income:Rental:Platform', D('0.5')),
                        )
                    ),
                    name='rental-split',
                )
            ]
        )
        result = rules.apply(
            txn(payee='RentalPlatform AG', narration='Rental payout', amount='100.00')
        )
        assert postings_of(result) == [
            (BANK, '100.00 CHF'),
            ('Assets:Owed-to-Me:Friend', '-50.00 CHF'),
            ('Income:Rental:Platform', '-50.00 CHF'),
        ]
        assert (
            loads_cleanly(result, [BANK, 'Assets:Owed-to-Me:Friend', 'Income:Rental:Platform'])
            == []
        )

    def test_rent_pins_utilities_and_interpolates_the_rest(self) -> None:
        """A fixed utilities share, with rent taking whatever remains."""
        utilities = 'Expenses:Housing:Utilities'
        rent = 'Expenses:Housing:Rent'
        rules = Ruleset(
            [
                Rule(
                    Match(payee='PropertyMgmt'),
                    Actions(
                        post=((utilities, Amount(D('150.00'), 'CHF')), rent),
                        tags=('recurring',),
                    ),
                    name='landlord-rent',
                )
            ]
        )
        result = rules.apply(txn(payee='PropertyMgmt AG', narration='Rent', amount='-1800.00'))
        assert postings_of(result) == [
            (BANK, '-1800.00 CHF'),
            (utilities, '150.00 CHF'),
            (rent, None),
        ]
        assert result.tags == frozenset({'recurring'})
        assert loads_cleanly(result, [BANK, utilities, rent]) == []

    def test_vending_machine_splits_on_amount_but_not_on_refunds(self) -> None:
        """Under 2 CHF is a coffee, more is lunch — and a credit is neither.

        The rule this replaces compared the signed amount against -2.00, so every refund
        came out as a coffee.
        """
        food = 'Expenses:Food:Restaurant'
        rules = Ruleset(
            [
                Rule(
                    Match(payee='Selecta', sign='debit', amount_lt=D('2')),
                    Actions(
                        narration='☕️ Coffee',
                        post=food,
                        tags=('business',),
                        links=('reimbursable',),
                    ),
                    name='selecta-coffee',
                ),
                Rule(
                    Match(payee='Selecta', sign='debit'),
                    Actions(
                        narration='Mittagessen',
                        post=food,
                        tags=('business',),
                        links=('reimbursable',),
                    ),
                    name='selecta-lunch',
                ),
            ]
        )

        coffee = rules.apply(txn(payee='Selecta', narration='Snack', amount='-1.50'))
        assert coffee.narration == '☕️ Coffee'

        lunch = rules.apply(txn(payee='Selecta', narration='Snack', amount='-5.00'))
        assert lunch.narration == 'Mittagessen'

        refund = txn(payee='Selecta', narration='Refund', amount='5.00')
        assert rules.match(refund) is None
        assert rules.apply(refund) is refund

    def test_subscription_matches_the_charge_not_the_refund(self) -> None:
        """The inverted-sign defect: `== D("10.00")` on a signed amount only ever hit refunds."""
        icloud = 'Expenses:Subscriptions:Cloud'
        rules = Ruleset(
            [
                Rule(
                    Match(payee='Apple', sign='debit', amount=D('10.00')),
                    Actions(payee='Apple', narration='iCloud+', post=icloud, tags=('recurring',)),
                    name='apple-icloud',
                ),
                Rule(Match(payee='Apple'), Actions(payee='Apple'), name='apple-rename'),
            ]
        )

        charge = rules.apply(
            txn(payee='APPLE.COM/BILL', narration='APPLE.COM/BILL', amount='-10.00')
        )
        assert charge.narration == 'iCloud+'
        assert charge.tags == frozenset({'recurring'})
        assert loads_cleanly(charge, [BANK, icloud]) == []

        refund = rules.apply(
            txn(payee='APPLE.COM/BILL', narration='APPLE.COM/BILL', amount='10.00')
        )
        assert refund.narration == 'APPLE.COM/BILL'
        assert refund.payee == 'Apple'
        assert [p.account for p in refund.postings] == [BANK]


# =============================================================================
# Match
# =============================================================================


class TestMatch:
    def test_payee_substring_is_case_insensitive(self) -> None:
        assert Match(payee='apple').test(txn(payee='APPLE.COM/BILL'))

    def test_payee_tuple_requires_every_substring(self) -> None:
        match = Match(payee=('Coop', 'Mineraloel'))
        assert match.test(txn(payee='Coop Mineraloel AG'))
        assert not match.test(txn(payee='Coop City'))

    def test_payee_any_needs_only_one_substring(self) -> None:
        match = Match(payee_any=('Galaxus', 'Digitec'))
        assert match.test(txn(payee='Galaxus'))
        assert match.test(txn(payee='digitec.ch'))
        assert not match.test(txn(payee='Brack'))

    def test_payee_and_payee_any_must_both_hold(self) -> None:
        match = Match(payee='Coop', payee_any=('Pronto', 'Mineraloel'))
        assert match.test(txn(payee='Coop Pronto'))
        assert not match.test(txn(payee='Coop City'))
        assert not match.test(txn(payee='Migros Pronto'))

    def test_payee_any_with_missing_payee(self) -> None:
        assert not Match(payee_any=('Galaxus',)).test(txn(payee=None))

    def test_narration(self) -> None:
        assert Match(narration='ticket').test(txn(narration='Train Ticket'))
        assert not Match(narration='ticket').test(txn(narration='Groceries'))

    def test_missing_payee_is_not_a_match_and_not_a_crash(self) -> None:
        assert not Match(payee='Apple').test(txn(payee=None))

    def test_account_matches_on_prefix(self) -> None:
        assert Match(account='Assets:Bank').test(txn(), importer_account=BANK)
        assert not Match(account='Liabilities').test(txn(), importer_account=BANK)

    def test_account_without_an_importer_account_does_not_match(self) -> None:
        assert not Match(account='Assets').test(txn(), importer_account=None)

    def test_currency(self) -> None:
        assert Match(currency='CHF').test(txn(currency='CHF'))
        assert not Match(currency='EUR').test(txn(currency='CHF'))

    def test_amount_is_absolute(self) -> None:
        match = Match(amount=D('10.00'))
        assert match.test(txn(amount='-10.00'))
        assert match.test(txn(amount='10.00'))

    def test_amount_compares_numerically_not_by_scale(self) -> None:
        """Importers are inconsistent about trailing zeros.

        One importer emits -10.0, not -10.00.
        """
        match = Match(amount=D('10.00'), sign='debit')
        assert match.test(txn(amount='-10.0'))
        assert match.test(txn(amount='-10'))
        assert match.test(txn(amount='-10.000'))

    def test_amount_lt_and_gt_are_strict(self) -> None:
        assert Match(amount_lt=D('2')).test(txn(amount='-1.50'))
        assert not Match(amount_lt=D('2')).test(txn(amount='-2.00'))
        assert Match(amount_gt=D('2')).test(txn(amount='-2.50'))
        assert not Match(amount_gt=D('2')).test(txn(amount='-2.00'))

    def test_sign(self) -> None:
        assert Match(sign='debit').test(txn(amount='-1.00'))
        assert not Match(sign='debit').test(txn(amount='1.00'))
        assert Match(sign='credit').test(txn(amount='1.00'))
        assert not Match(sign='credit').test(txn(amount='-1.00'))

    def test_zero_counts_as_a_credit(self) -> None:
        assert Match(sign='credit').test(txn(amount='0.00'))
        assert not Match(sign='debit').test(txn(amount='0.00'))

    def test_amount_criteria_need_units(self) -> None:
        assert not Match(amount=D('10')).test(txn(amount=None))
        assert not Match(sign='debit').test(txn(amount=None))

    def test_criteria_without_units_still_work_without_units(self) -> None:
        assert Match(payee='Coop').test(txn(amount=None))

    def test_when_escape_hatch(self) -> None:
        weekend = Match(when=lambda t: t.date.weekday() >= 5)
        assert weekend.test(txn())  # 2026-08-01 is a Saturday
        assert Match(payee='Coop', when=lambda t: False).test(txn()) is False

    def test_empty_match_is_rejected(self) -> None:
        with pytest.raises(ValueError, match='at least one criterion'):
            Match()

    def test_negative_amount_is_rejected(self) -> None:
        with pytest.raises(ValueError, match='absolute value'):
            Match(amount=D('-10'))

    def test_bad_sign_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="'debit' or 'credit'"):
            Match(payee='x', sign='outgoing')  # type: ignore[arg-type]

    def test_describe_is_readable(self) -> None:
        assert Match(payee=('Coop', 'TS'), sign='debit').describe() == 'payee=Coop+TS sign=debit'


# =============================================================================
# Actions
# =============================================================================


class TestActions:
    def test_sets_payee_narration_and_flag(self) -> None:
        result = Actions(payee='Coop', narration='Groceries', flag='!').apply_to(txn())
        assert (result.payee, result.narration, result.flag) == ('Coop', 'Groceries', '!')

    def test_tags_and_links_are_added_not_replaced(self) -> None:
        result = Actions(tags=('business',), links=('reimbursable',)).apply_to(
            txn(tags=('travel',), links=('ubs',))
        )
        assert result.tags == frozenset({'travel', 'business'})
        assert result.links == frozenset({'ubs', 'reimbursable'})

    def test_bare_post_has_no_units(self) -> None:
        result = Actions(post='Expenses:Food').apply_to(txn())
        assert postings_of(result) == [(BANK, '-20.00 CHF'), ('Expenses:Food', None)]

    def test_post_with_an_amount(self) -> None:
        result = Actions(post=(('Expenses:Food', Amount(D('5.00'), 'CHF')),)).apply_to(txn())
        assert postings_of(result)[1] == ('Expenses:Food', '5.00 CHF')

    def test_post_skips_accounts_already_present(self) -> None:
        result = Actions(post=BANK).apply_to(txn())
        assert postings_of(result) == [(BANK, '-20.00 CHF')]

    def test_no_postings_are_added_to_an_already_complete_transaction(self) -> None:
        """Some importers hand over a finished transaction; a rule must not append to it.

        A credit card that assigns its own category and splits the amount with a partner
        already balances.  Adding a leg either unbalances it or gives Beancount a second
        posting with no amount, and the two cases cannot be told apart by account name
        because both importers claim the same one.
        """
        complete = txn()._replace(
            postings=[
                Posting(BANK, Amount(D('-20.00'), 'CHF'), None, None, None, None),
                Posting('Expenses:Groceries', Amount(D('10.00'), 'CHF'), None, None, None, None),
                Posting(
                    'Assets:Owed-to-Me:Friend', Amount(D('10.00'), 'CHF'), None, None, None, None
                ),
            ]
        )
        result = Actions(post='Expenses:Food').apply_to(complete)
        assert postings_of(result) == postings_of(complete)

    def test_a_rename_still_applies_to_a_complete_transaction(self) -> None:
        complete = txn()._replace(
            postings=[
                Posting(BANK, Amount(D('-20.00'), 'CHF'), None, None, None, None),
                Posting('Expenses:Groceries', Amount(D('20.00'), 'CHF'), None, None, None, None),
            ]
        )
        result = Actions(payee='Coop AG', post='Expenses:Food', tags=('shopping',)).apply_to(
            complete
        )
        assert result.payee == 'Coop AG'
        assert result.tags == frozenset({'shopping'})
        assert postings_of(result) == postings_of(complete)

    def test_a_split_is_also_skipped_on_a_complete_transaction(self) -> None:
        complete = txn()._replace(
            postings=[
                Posting(BANK, Amount(D('100.00'), 'CHF'), None, None, None, None),
                Posting('Income:Rent', Amount(D('-100.00'), 'CHF'), None, None, None, None),
            ]
        )
        result = Actions(
            split=(('Assets:Owed-to-Me:Friend', D('0.5')), ('Income:Other', D('0.5')))
        ).apply_to(complete)
        assert postings_of(result) == postings_of(complete)

    def test_post_appends_on_an_unbalanced_bank_plus_fee_transaction(self) -> None:
        """A bank leg plus a fee leg does not balance; a rule may still fill the residual."""
        unbalanced = txn()._replace(
            postings=[
                Posting(BANK, Amount(D('-100.00'), 'CHF'), None, None, None, None),
                Posting('Expenses:Fees:Yuh', Amount(D('2.00'), 'CHF'), None, None, None, None),
            ]
        )
        result = Actions(post='Expenses:Food').apply_to(unbalanced)
        assert postings_of(result) == [
            (BANK, '-100.00 CHF'),
            ('Expenses:Fees:Yuh', '2.00 CHF'),
            ('Expenses:Food', None),
        ]
        assert loads_cleanly(result, [BANK, 'Expenses:Fees:Yuh', 'Expenses:Food']) == []

    def test_a_split_is_skipped_on_an_unbalanced_bank_plus_fee_transaction(self) -> None:
        """Declared fractions assume the whole residual; a fee leg already takes part of it."""
        unbalanced = txn()._replace(
            postings=[
                Posting(BANK, Amount(D('-100.00'), 'CHF'), None, None, None, None),
                Posting('Expenses:Fees:Yuh', Amount(D('2.00'), 'CHF'), None, None, None, None),
            ]
        )
        result = Actions(split=(('Expenses:A', D('0.5')), ('Expenses:B', D('0.5')))).apply_to(
            unbalanced
        )
        assert postings_of(result) == postings_of(unbalanced)

    def test_a_rename_still_applies_to_an_unbalanced_bank_plus_fee_transaction(self) -> None:
        unbalanced = txn()._replace(
            postings=[
                Posting(BANK, Amount(D('-100.00'), 'CHF'), None, None, None, None),
                Posting('Expenses:Fees:Yuh', Amount(D('2.00'), 'CHF'), None, None, None, None),
            ]
        )
        result = Actions(payee='Coop AG', post='Expenses:Food', tags=('shopping',)).apply_to(
            unbalanced
        )
        assert result.payee == 'Coop AG'
        assert result.tags == frozenset({'shopping'})
        assert postings_of(result)[-1] == ('Expenses:Food', None)

    def test_split_gives_the_remainder_to_the_last_account(self) -> None:
        """Thirds of 100 do not divide evenly; the transaction must still balance."""
        result = Actions(
            split=(
                ('Expenses:A', D('1') / 3),
                ('Expenses:B', D('1') / 3),
                ('Expenses:C', D('1') / 3),
            )
        ).apply_to(txn(amount='-100.00'))
        values = [units for _account, units in postings_of(result)[1:]]
        assert values == ['33.33 CHF', '33.33 CHF', '33.34 CHF']
        assert loads_cleanly(result, [BANK, 'Expenses:A', 'Expenses:B', 'Expenses:C']) == []

    def test_an_exact_half_stays_exact(self) -> None:
        """Halving 412.05 is 206.025, not 206.02 with the odd rappen shoved onto the other leg.

        Rounding to the source's two places would balance too, so nothing would complain — it
        would just quietly disagree with every halved purchase already in the ledger.
        """
        result = Actions(
            split=(('Expenses:A', D('0.5')), ('Assets:Owed-to-Me:Friend', D('0.5')))
        ).apply_to(txn(amount='-412.05'))
        values = [units for _account, units in postings_of(result)[1:]]
        assert values == ['206.025 CHF', '206.025 CHF']
        assert loads_cleanly(result, [BANK, 'Expenses:A', 'Assets:Owed-to-Me:Friend']) == []

    def test_split_without_units_does_nothing(self) -> None:
        result = Actions(split=(('Expenses:A', D('1')),)).apply_to(txn(amount=None))
        assert postings_of(result) == [(BANK, None)]

    def test_does_not_mutate_the_original(self) -> None:
        original = txn(tags=('travel',))
        before = list(original.postings)
        Actions(post='Expenses:Food', tags=('business',)).apply_to(original)
        assert original.postings == before
        assert original.tags == frozenset({'travel'})

    def test_two_bare_postings_are_rejected(self) -> None:
        with pytest.raises(ValueError, match='only one posting'):
            Actions(post=('Expenses:A', 'Expenses:B'))

    def test_split_fractions_must_total_one(self) -> None:
        with pytest.raises(ValueError, match='must total 1'):
            Actions(split=(('Expenses:A', D('0.5')), ('Expenses:B', D('0.2'))))

    def test_split_fractions_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match='must be positive'):
            Actions(split=(('Expenses:A', D('1.5')), ('Expenses:B', D('-0.5'))))

    def test_drop_cannot_be_combined(self) -> None:
        with pytest.raises(ValueError, match='cannot be combined'):
            Actions(drop=True, payee='Coop')

    def test_empty_actions_are_rejected(self) -> None:
        with pytest.raises(ValueError, match='does nothing'):
            Actions()

    def test_accounts_lists_every_target(self) -> None:
        actions = Actions(
            post=('Expenses:A', ('Expenses:B', Amount(D('1'), 'CHF'))),
            split=(('Expenses:C', D('1')),),
        )
        assert actions.accounts() == frozenset({'Expenses:A', 'Expenses:B', 'Expenses:C'})


# =============================================================================
# Ruleset
# =============================================================================


class TestRuleset:
    def test_first_match_wins(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee=('Coop', 'Pronto')), Actions(payee='Coop Pronto'), name='pronto'),
                Rule(Match(payee='Coop'), Actions(payee='Coop'), name='generic'),
            ]
        )
        assert rules.apply(txn(payee='Coop Pronto Zürich')).payee == 'Coop Pronto'
        assert rules.apply(txn(payee='Coop City')).payee == 'Coop'

    def test_unmatched_transaction_is_returned_unchanged(self) -> None:
        rules = Ruleset([Rule(Match(payee='Migros'), Actions(payee='Migros'))])
        original = txn(payee='Coop')
        assert rules.apply(original) is original

    def test_explain_lists_every_match_in_order(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee=('Coop', 'Pronto')), Actions(payee='a'), name='pronto'),
                Rule(Match(payee='Coop'), Actions(payee='b'), name='generic'),
            ]
        )
        assert rules.explain(txn(payee='Coop Pronto')) == ['pronto', 'generic']
        assert rules.explain(txn(payee='Coop City')) == ['generic']
        assert rules.explain(txn(payee='Migros')) == []

    def test_shadowed_detects_a_general_rule_placed_first(self) -> None:
        """The ordering mistake a previous importer warned about in a comment."""
        rules = Ruleset(
            [
                Rule(Match(payee='Coop'), Actions(payee='Coop'), name='generic'),
                Rule(Match(payee=('Coop', 'Pronto')), Actions(payee='Coop Pronto'), name='pronto'),
            ]
        )
        assert rules.shadowed() == [('generic', 'pronto')]

    def test_correct_order_is_not_reported(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee=('Coop', 'Pronto')), Actions(payee='Coop Pronto'), name='pronto'),
                Rule(Match(payee='Coop'), Actions(payee='Coop'), name='generic'),
            ]
        )
        assert rules.shadowed() == []

    def test_an_amount_narrowed_rule_does_not_shadow(self) -> None:
        rules = Ruleset(
            [
                Rule(
                    Match(payee='Selecta', amount_lt=D('2')), Actions(narration='c'), name='coffee'
                ),
                Rule(Match(payee='Selecta'), Actions(narration='l'), name='lunch'),
            ]
        )
        assert rules.shadowed() == []

    def test_a_callable_is_never_treated_as_shadowing(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(when=lambda t: True), Actions(payee='a'), name='catch-all'),
                Rule(Match(payee='Coop'), Actions(payee='b'), name='coop'),
            ]
        )
        assert rules.shadowed() == []

    def test_a_wider_payee_any_shadows_a_narrower_one(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee_any=('Selecta', 'Boostbar')), Actions(narration='a'), name='both'),
                Rule(Match(payee_any=('Selecta',)), Actions(narration='b'), name='selecta'),
            ]
        )
        assert rules.shadowed() == [('both', 'selecta')]

    def test_a_narrower_payee_any_does_not_shadow(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee_any=('Selecta',)), Actions(narration='a'), name='selecta'),
                Rule(Match(payee_any=('Selecta', 'Boostbar')), Actions(narration='b'), name='both'),
            ]
        )
        assert rules.shadowed() == []

    def test_different_accounts_do_not_shadow(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee='Coop', account='Assets:A'), Actions(payee='a'), name='a'),
                Rule(Match(payee='Coop', account='Assets:B'), Actions(payee='b'), name='b'),
            ]
        )
        assert rules.shadowed() == []

    def test_duplicate_labels_are_rejected(self) -> None:
        with pytest.raises(ValueError, match='must be unique'):
            Ruleset(
                [
                    Rule(Match(payee='a'), Actions(payee='a'), name='same'),
                    Rule(Match(payee='b'), Actions(payee='b'), name='same'),
                ]
            )

    def test_unnamed_rules_are_labelled_from_their_match(self) -> None:
        rules = Ruleset([Rule(Match(payee='Coop'), Actions(payee='Coop'))])
        assert rules.explain(txn(payee='Coop')) == ['payee=Coop']

    def test_accounts_aggregates_every_rule(self) -> None:
        rules = Ruleset(
            [
                Rule(Match(payee='a'), Actions(post='Expenses:A'), name='a'),
                Rule(Match(payee='b'), Actions(post='Expenses:B'), name='b'),
            ]
        )
        assert rules.accounts() == frozenset({'Expenses:A', 'Expenses:B'})

    def test_accounts_of_an_empty_ruleset(self) -> None:
        assert Ruleset([]).accounts() == frozenset()


class TestRulesetScope:
    """A ruleset written for one bank must not run over another importer's entries."""

    def _rules(self) -> Ruleset:
        return Ruleset(
            [Rule(Match(payee='Coop'), Actions(post='Expenses:Food'), name='coop')],
            accounts=('Assets:Bank:Checking',),
        )

    def test_applies_within_the_prefix(self) -> None:
        rules = self._rules()
        entries = [txn(payee='Coop')]
        rules.hook(imported(entries, account='Assets:Bank:Checking:CHF'), [])
        assert [p.account for p in entries[0].postings] == [BANK, 'Expenses:Food']

    def test_skips_other_importers(self) -> None:
        rules = self._rules()
        entries = [txn(payee='Coop')]
        rules.hook(imported(entries, account='Liabilities:CreditCard'), [])
        assert [p.account for p in entries[0].postings] == [BANK]

    def test_declines_an_unknown_account_rather_than_guessing(self) -> None:
        assert self._rules().match(txn(payee='Coop'), None) is None

    def test_explain_says_nothing_out_of_scope(self) -> None:
        rules = self._rules()
        assert rules.explain(txn(payee='Coop'), 'Liabilities:Card') == []
        assert rules.explain(txn(payee='Coop'), 'Assets:Bank:Checking:CHF') == ['coop']

    def test_an_unrestricted_ruleset_covers_everything(self) -> None:
        rules = Ruleset([Rule(Match(payee='Coop'), Actions(payee='Coop AG'), name='coop')])
        assert rules.covers(None)
        assert rules.covers('Liabilities:Card')

    def test_a_drop_rule_out_of_scope_does_not_drop(self) -> None:
        rules = Ruleset(
            [Rule(Match(payee='Coop'), Actions(drop=True), name='dupe')],
            accounts=('Assets:Bank:Checking',),
        )
        entries = [txn(payee='Coop')]
        rules.hook(imported(entries, account='Liabilities:Card'), [])
        assert len(entries) == 1


class TestRulesetDrop:
    def test_drop_removes_the_transaction(self) -> None:
        rules = Ruleset([Rule(Match(narration='Besten Dank'), Actions(drop=True), name='dupe')])
        entries = [txn(narration='Ihre Zahlung, Besten Dank'), txn(narration='Groceries')]
        rules.hook(imported(entries), [])
        assert [t.narration for t in entries] == ['Groceries']

    def test_drop_is_logged_with_the_rule_name(self, caplog) -> None:
        rules = Ruleset([Rule(Match(payee='Swisscard'), Actions(drop=True), name='transfer-dupe')])
        with caplog.at_level(logging.INFO):
            rules.hook(imported([txn(payee='Swisscard', narration='Einzug LSV')]), [])
        assert 'transfer-dupe' in caplog.text
        assert 'Swisscard' in caplog.text


class TestRulesetHook:
    def test_hook_applies_rules_to_every_transaction(self) -> None:
        rules = Ruleset([Rule(Match(payee='Coop'), Actions(post='Expenses:Food'), name='coop')])
        entries = [txn(payee='Coop'), txn(payee='Migros')]
        rules.hook(imported(entries), [])
        assert [p.account for p in entries[0].postings] == [BANK, 'Expenses:Food']
        assert [p.account for p in entries[1].postings] == [BANK]

    def test_hook_preserves_non_transaction_directives(self) -> None:
        rules = Ruleset([Rule(Match(payee='Coop'), Actions(payee='Coop AG'), name='coop')])
        balance = Balance(
            new_metadata('import.csv', 1),
            datetime.date(2026, 8, 2),
            BANK,
            Amount(D('100'), 'CHF'),
            None,
            None,
        )
        entries = [txn(payee='Coop'), balance, txn(payee='Coop')]
        rules.hook(imported(entries), [])
        assert isinstance(entries[1], Balance)
        assert entries[0].payee == 'Coop AG'
        assert entries[2].payee == 'Coop AG'

    def test_hook_passes_the_importer_account_to_matches(self) -> None:
        rules = Ruleset(
            [Rule(Match(account='Assets:Bank'), Actions(tags=('bank',)), name='bank-only')]
        )
        entries = [txn()]
        rules.hook(imported(entries, account=BANK), [])
        assert entries[0].tags == frozenset({'bank'})

        others = [txn()]
        rules.hook(imported(others, account='Liabilities:Card'), [])
        assert others[0].tags == frozenset()

    def test_a_failing_callable_does_not_block_the_import(self, caplog) -> None:
        def explode(_t: Transaction) -> bool:
            raise RuntimeError('rule blew up')

        rules = Ruleset([Rule(Match(when=explode), Actions(payee='x'), name='bad')], label='Rules')
        original = txn()
        entries = [original]
        with caplog.at_level(logging.WARNING):
            rules.hook(imported(entries), [])
        assert entries[0] is original
        assert 'Rules' in caplog.text

    def test_hook_ignores_existing_entries(self) -> None:
        rules = Ruleset([Rule(Match(payee='Coop'), Actions(payee='Coop AG'), name='coop')])
        entries = [txn(payee='Coop')]
        rules.hook(imported(entries))
        assert entries[0].payee == 'Coop AG'


# =============================================================================
# Interaction with the predictors
# =============================================================================


class TestPredictorInteraction:
    def test_a_rule_set_payee_survives_the_payee_predictor(self) -> None:
        """Rules assert, predictors fill blanks — so nothing walks over a curated name."""
        from beancount_hooks import RulesPayeePredictor

        rules = Ruleset([Rule(Match(payee='Sbbcffffs'), Actions(payee='SBB CFF FFS'), name='sbb')])
        entries = [txn(payee='Sbbcffffs', narration='Train')]
        batch = imported(entries)
        rules.hook(batch, [])
        RulesPayeePredictor().hook(batch, [])
        assert entries[0].payee == 'SBB CFF FFS'

    def test_a_rule_posting_blocks_the_postings_predictor(self) -> None:
        from beancount_hooks import RulesPostingsPredictor

        history = [
            Transaction(
                new_metadata('ledger.bean', i),
                datetime.date(2025, 1, 1),
                '*',
                'Coop',
                'Groceries',
                frozenset(),
                frozenset(),
                [
                    Posting(BANK, Amount(D('-20'), 'CHF'), None, None, None, None),
                    Posting('Expenses:Groceries', Amount(D('20'), 'CHF'), None, None, None, None),
                ],
            )
            for i in range(6)
        ]
        rules = Ruleset([Rule(Match(payee='Coop'), Actions(post='Expenses:Coop'), name='coop')])
        entries = [txn(payee='Coop')]
        batch = imported(entries)
        rules.hook(batch, history)
        RulesPostingsPredictor(min_occurrence=3).hook(batch, history)
        accounts = [p.account for p in entries[0].postings]
        assert 'Expenses:Coop' in accounts
        assert 'Expenses:Groceries' not in accounts

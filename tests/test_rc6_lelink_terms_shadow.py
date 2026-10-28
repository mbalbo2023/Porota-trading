"""Deterministic issuer-term tests; synthetic figures are not market observations."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from rc6_lelink_terms_shadow import (
    A3500FixingEvidence,
    AuctionPriceEvidence,
    BCRA_A3500_SERIES,
    BCRA_BANK_CALENDAR,
    BusinessCalendarEvidence,
    ISSUER_SERIES,
    TermsEvidenceError,
    maturity_amortization,
    primary_subscription,
)


UTC = timezone.utc
AS_OF = datetime(2026, 10, 27, 15, tzinfo=UTC)


def calendar():
    start = date(2026, 9, 1)
    days = {
        start + timedelta(days=i): (start + timedelta(days=i)).weekday() < 5
        for i in range(91)
    }
    for holiday in (date(2026, 10, 12), date(2026, 11, 9),
                    date(2026, 11, 10), date(2026, 11, 23)):
        days[holiday] = False  # Official 2026 BCRA bank holidays in this span.
    return BusinessCalendarEvidence(
        days=days,
        source_url=BCRA_BANK_CALENDAR,
        version_id="BCRA_2026_CALENDAR_SNAPSHOT_OCT27_TEST",
        known_at=datetime(2026, 10, 27, 10, tzinfo=UTC),
    )


def original_auction():
    return AuctionPriceEvidence(
        auction_date=date(2026, 9, 11),
        usd_equivalent_per_usd_vno=Decimal("0.97"),
        known_at=datetime(2026, 9, 11, 20, tzinfo=UTC),
        evidence_id="SYNTHETIC_NORMALIZED_AWARD_NOT_AN_ACTUAL_FILL",
    )


def a3500(day, value, published_at):
    return A3500FixingEvidence(
        fixing_date=day,
        ars_per_usd=Decimal(value),
        published_at=published_at,
        evidence_id="SYNTHETIC_A3500_TEST_INPUT",
    )


def subscription(**changes):
    kwargs = dict(
        issuer_series=ISSUER_SERIES,
        vno_usd=Decimal("100"),
        calendar=calendar(),
        fixing=a3500(date(2026, 9, 10), "1450", datetime(2026, 9, 10, 19, tzinfo=UTC)),
        award=original_auction(),
        as_of=AS_OF,
    )
    kwargs.update(changes)
    return primary_subscription(**kwargs)


def redemption(**changes):
    kwargs = dict(
        issuer_series=ISSUER_SERIES,
        vno_usd=Decimal("100"),
        calendar=calendar(),
        as_of=AS_OF,
    )
    kwargs.update(changes)
    return maturity_amortization(**kwargs)


def test_original_auction_uses_t_minus_one_auction_not_issue_and_discount_award():
    result = subscription()
    assert result.a3500_fixing_date == date(2026, 9, 10)
    assert result.due_date == date(2026, 9, 15)
    assert result.vno_usd == Decimal("100")
    assert result.derived_cash_ars == Decimal("140650.00")
    assert result.scenario_cash_ars is None
    assert result.coupon_ars == 0
    assert result.status == "FORMULA_ONLY_NOT_ACCOUNT_SETTLEMENT"
    assert "NO_EXECUTION_OR_SETTLEMENT" in result.authority
    assert len(result.evidence_ids) == 5


def test_unpublished_future_a3500_is_not_a_realized_or_contractual_cashflow():
    result = redemption()
    assert result.a3500_fixing_date == date(2026, 11, 25)
    assert result.due_date == date(2026, 11, 30)
    assert result.derived_cash_ars is None
    assert result.scenario_cash_ars is None
    assert result.status == "A3500_FIXING_PENDING_NO_VERIFICADO"
    assert result.coupon_ars == 0

    scenario = redemption(scenario_ars_per_usd=Decimal("1510.25"), scenario_id="HYPOTHETICAL:1")
    assert scenario.scenario_cash_ars == Decimal("151025.00")
    assert scenario.derived_cash_ars is None
    assert scenario.status == "SCENARIO_NO_VERIFICADO"
    assert BCRA_A3500_SERIES not in scenario.evidence_ids


def test_published_t_minus_three_fixing_determines_issuer_due_not_receipt():
    fixing = a3500(date(2026, 11, 25), "1512.5", datetime(2026, 11, 25, 19, tzinfo=UTC))
    result = redemption(fixing=fixing, as_of=datetime(2026, 12, 1, 15, tzinfo=UTC))
    assert result.a3500_fixing_date == date(2026, 11, 25)
    assert result.derived_cash_ars == Decimal("151250.0")
    assert result.scenario_cash_ars is None
    assert result.status == "CONTRACTUAL_DUE_NOT_PAYMENT_RECEIPT"


@pytest.mark.parametrize("changed,code", [
    ({"issuer_series": "D30N6"}, "ISSUER_SERIES_IDENTITY_AMBIGUOUS"),
    ({"issuer_series": "LELINK D15E7"}, "ISSUER_SERIES_IDENTITY_AMBIGUOUS"),
    ({"vno_usd": Decimal("0.5")}, "VNO_USD_MINIMUM_ONE"),
    ({"vno_usd": Decimal("NaN")}, "INVALID_USD_VNO"),
    ({"as_of": datetime(2026, 10, 21, 23, tzinfo=UTC)}, "RC63_ALIAS_NOT_YET_VERIFIABLY_PUBLIC"),
    ({"as_of": datetime(2026, 10, 27, 15)}, "AS_OF_MUST_BE_TIMEZONE_AWARE"),
    ({"award": replace(original_auction(), auction_date=date(2026, 10, 20))}, "ORIGINAL_AUCTION_AWARD_UNKNOWN"),
    ({"award": replace(original_auction(), unit="ARS_PER_BOND")}, "ORIGINAL_AUCTION_AWARD_UNKNOWN"),
    ({"award": replace(original_auction(), usd_equivalent_per_usd_vno=Decimal("NaN"))}, "INVALID_AUCTION_PRICE_RATIO"),
    ({"award": replace(original_auction(), known_at=datetime(2026, 10, 28, tzinfo=UTC))}, "AUCTION_AWARD_NOT_KNOWN_AT_PIT"),
    ({"fixing": a3500(date(2026, 9, 11), "1450", datetime(2026, 9, 11, 19, tzinfo=UTC))}, "A3500_FIXING_DATE_MISMATCH"),
    ({"fixing": replace(a3500(date(2026, 9, 10), "1450", datetime(2026, 9, 10, 19, tzinfo=UTC)), unit="USD_PER_ARS")}, "A3500_PROVENANCE_OR_UNIT_UNKNOWN"),
    ({"fixing": replace(a3500(date(2026, 9, 10), "1450", datetime(2026, 9, 10, 19, tzinfo=UTC)), source_url="MARKET_QUOTE")}, "A3500_PROVENANCE_OR_UNIT_UNKNOWN"),
    ({"fixing": a3500(date(2026, 9, 10), "1450", datetime(2026, 10, 28, tzinfo=UTC))}, "A3500_NOT_KNOWN_AT_PIT"),
])
def test_primary_rejects_ambiguous_identity_units_pit_and_secondary_award(changed, code):
    with pytest.raises(TermsEvidenceError, match=f"^{code}$"):
        subscription(**changed)


def test_calendar_requires_every_day_crossed_and_snapshot_provenance():
    complete = calendar()
    missing = dict(complete.days)
    del missing[date(2026, 11, 27)]
    with pytest.raises(TermsEvidenceError, match="BUSINESS_CALENDAR_DAY_UNKNOWN"):
        redemption(calendar=replace(complete, days=missing))
    with pytest.raises(TermsEvidenceError, match="BUSINESS_CALENDAR_PROVENANCE_UNKNOWN"):
        redemption(calendar=replace(complete, source_url="WEEKDAYS_ASSUMED"))
    with pytest.raises(TermsEvidenceError, match="BUSINESS_CALENDAR_PROVENANCE_UNKNOWN"):
        redemption(calendar=replace(complete, version_id=None))
    with pytest.raises(TermsEvidenceError, match="BUSINESS_CALENDAR_NOT_KNOWN_AT_PIT"):
        redemption(calendar=replace(complete, known_at=datetime(2026, 10, 28, tzinfo=UTC)))
    conflicting = dict(complete.days)
    conflicting[date(2026, 11, 29)] = True  # Sunday is not a business day.
    with pytest.raises(TermsEvidenceError, match="BUSINESS_CALENDAR_WEEKEND_CONFLICT"):
        redemption(calendar=replace(complete, days=conflicting))


def test_future_fixing_cannot_be_backdated_to_make_a_scenario_look_observed():
    premature = a3500(date(2026, 11, 25), "1512", datetime(2026, 10, 27, 13, tzinfo=UTC))
    with pytest.raises(TermsEvidenceError, match="A3500_PUBLISHED_BEFORE_FIXING_DATE"):
        redemption(fixing=premature)


def test_future_fixing_or_scenario_requires_unambiguous_exclusive_provenance():
    fixing = a3500(date(2026, 11, 25), "1512", datetime(2026, 11, 25, 19, tzinfo=UTC))
    with pytest.raises(TermsEvidenceError, match="A3500_NOT_KNOWN_AT_PIT"):
        redemption(fixing=fixing)
    with pytest.raises(TermsEvidenceError, match="FIXING_AND_SCENARIO_CONFLICT"):
        redemption(fixing=fixing, scenario_ars_per_usd=Decimal("1500"), scenario_id="HYPOTHETICAL")
    with pytest.raises(TermsEvidenceError, match="FIXING_AND_SCENARIO_CONFLICT"):
        redemption(fixing=fixing, scenario_id="HYPOTHETICAL")
    with pytest.raises(TermsEvidenceError, match="SCENARIO_PROVENANCE_UNKNOWN"):
        redemption(scenario_ars_per_usd=Decimal("1500"))
    with pytest.raises(TermsEvidenceError, match="INVALID_SCENARIO_RATE"):
        redemption(scenario_ars_per_usd=Decimal("Infinity"), scenario_id="HYPOTHETICAL")


def test_malformed_external_evidence_is_rejected_without_type_errors():
    with pytest.raises(TermsEvidenceError, match="ORIGINAL_AUCTION_AWARD_UNKNOWN"):
        subscription(award=replace(original_auction(), evidence_id=None))
    fixing = a3500(date(2026, 9, 10), "1450", datetime(2026, 9, 10, 19, tzinfo=UTC))
    with pytest.raises(TermsEvidenceError, match="A3500_PROVENANCE_OR_UNIT_UNKNOWN"):
        subscription(fixing=replace(fixing, evidence_id=None))

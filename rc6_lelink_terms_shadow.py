"""Pure, issuer-level SHADOW cashflow terms for LELINK D30N6.

RC55/2026 art. 1 creates the USD-denominated, ARS-paid zero-coupon letter;
RC63/2026 art. 1 names that same issue LELINK D30N6 and expands it for a
conversion. Neither resolution supplies a broker identity, an executed
auction price, a secondary-market purchase price, or account-specific costs.
This module has no market-data, order, worker, or broker dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Mapping
from zoneinfo import ZoneInfo


RC55_ART1 = "https://www.argentina.gob.ar/normativa/nacional/norma-430114/texto"
RC63_ART1 = "https://www.argentina.gob.ar/normativa/nacional/norma-431472/texto"
BCRA_A3500_SERIES = "https://www.bcra.gob.ar/tipo-cambio-referencia-comunicacion-a-3500-tcnpm/"
BCRA_BANK_CALENDAR = "https://www.bcra.gob.ar/consulta-feriados-bancarios/"
ISSUER_SERIES = "RESFC-2026-55-APN-SH#MEC:ART1/LELINK D30N6"
AUCTION_DATE = date(2026, 9, 11)
ISSUE_DATE = date(2026, 9, 15)
MATURITY_DATE = date(2026, 11, 30)
RC63_PUBLICATION_DATE = date(2026, 10, 21)
ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class TermsEvidenceError(ValueError):
    """A required source, date, unit, or identity cannot be established."""


@dataclass(frozen=True)
class BusinessCalendarEvidence:
    """Explicit status for every day crossed by a T-minus-business-day rule.

    The caller must supply a dated, versioned calendar snapshot. Missing days
    are unknown rather than presumed to be business days or weekends.
    """

    days: Mapping[date, bool]
    source_url: str
    version_id: str
    known_at: datetime


@dataclass(frozen=True)
class A3500FixingEvidence:
    fixing_date: date
    ars_per_usd: Decimal
    published_at: datetime
    evidence_id: str
    source_url: str = BCRA_A3500_SERIES
    unit: str = "ARS_PER_USD"
    series: str = "BCRA_A3500"


@dataclass(frozen=True)
class AuctionPriceEvidence:
    """Independently normalized award price; no raw quote unit is inferred.

    A ratio of 0.97 means USD 0.97 of placement price per USD 1 of original
    nominal value, before converting to ARS at the subscription fixing. It
    must come from a separately verified award/receipt, never from a PPI or
    secondary-market quote interpreted by this module.
    """

    auction_date: date
    usd_equivalent_per_usd_vno: Decimal
    known_at: datetime
    evidence_id: str
    unit: str = "USD_EQUIVALENT_PER_USD_VNO"


@dataclass(frozen=True)
class IssuerCashflow:
    kind: str
    status: str
    issuer_series: str
    due_date: date
    vno_usd: Decimal
    derived_cash_ars: Decimal | None
    scenario_cash_ars: Decimal | None
    coupon_ars: Decimal
    a3500_fixing_date: date
    evidence_ids: tuple[str, ...]
    authority: str = "ISSUER_TERMS_SHADOW_ONLY_NO_EXECUTION_OR_SETTLEMENT"


def _asof(as_of: datetime, issuer_series: str) -> None:
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise TermsEvidenceError("AS_OF_MUST_BE_TIMEZONE_AWARE")
    if issuer_series != ISSUER_SERIES:
        raise TermsEvidenceError("ISSUER_SERIES_IDENTITY_AMBIGUOUS")
    # The official nickname appears in RC63 art. 1, published 21 Oct. Only
    # its publication date is known here; the same-day publication hour is not.
    if as_of.astimezone(ARGENTINA_TZ).date() <= RC63_PUBLICATION_DATE:
        raise TermsEvidenceError("RC63_ALIAS_NOT_YET_VERIFIABLY_PUBLIC")


def _positive_decimal(value: Decimal, code: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise TermsEvidenceError(code)


def _vno(vno_usd: Decimal) -> None:
    _positive_decimal(vno_usd, "INVALID_USD_VNO")
    if vno_usd < 1 or vno_usd != vno_usd.to_integral_value():
        raise TermsEvidenceError("VNO_USD_MINIMUM_ONE")


def _known_at(value: datetime, as_of: datetime, code: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TermsEvidenceError(code)
    if value > as_of:
        raise TermsEvidenceError(code)


def _prior_business_day(
    anchor: date, count: int, calendar: BusinessCalendarEvidence, as_of: datetime
) -> date:
    if not isinstance(calendar, BusinessCalendarEvidence) or (
        calendar.source_url != BCRA_BANK_CALENDAR
        or not isinstance(calendar.version_id, str)
        or not calendar.version_id.strip()
        or not isinstance(calendar.days, Mapping)
    ):
        raise TermsEvidenceError("BUSINESS_CALENDAR_PROVENANCE_UNKNOWN")
    _known_at(calendar.known_at, as_of, "BUSINESS_CALENDAR_NOT_KNOWN_AT_PIT")
    if calendar.days.get(anchor) is not True or anchor.weekday() >= 5:
        raise TermsEvidenceError("PAYMENT_OR_AUCTION_DAY_NOT_PROVEN_BUSINESS")
    day = anchor
    seen = 0
    for _ in range(370):
        day -= timedelta(days=1)
        status = calendar.days.get(day)
        if type(status) is not bool:
            raise TermsEvidenceError("BUSINESS_CALENDAR_DAY_UNKNOWN")
        if status and day.weekday() >= 5:
            raise TermsEvidenceError("BUSINESS_CALENDAR_WEEKEND_CONFLICT")
        if status:
            seen += 1
            if seen == count:
                return day
    raise TermsEvidenceError("BUSINESS_CALENDAR_SPAN_INCOMPLETE")


def _fixing(rate: A3500FixingEvidence, expected: date, as_of: datetime) -> None:
    if not isinstance(rate, A3500FixingEvidence) or (
        rate.source_url != BCRA_A3500_SERIES
        or rate.series != "BCRA_A3500"
        or rate.unit != "ARS_PER_USD"
        or not isinstance(rate.evidence_id, str)
        or not rate.evidence_id.strip()
    ):
        raise TermsEvidenceError("A3500_PROVENANCE_OR_UNIT_UNKNOWN")
    if rate.fixing_date != expected:
        raise TermsEvidenceError("A3500_FIXING_DATE_MISMATCH")
    _positive_decimal(rate.ars_per_usd, "INVALID_A3500_VALUE")
    _known_at(rate.published_at, as_of, "A3500_NOT_KNOWN_AT_PIT")
    if rate.published_at.astimezone(ARGENTINA_TZ).date() < rate.fixing_date:
        raise TermsEvidenceError("A3500_PUBLISHED_BEFORE_FIXING_DATE")


def primary_subscription(
    *,
    issuer_series: str,
    vno_usd: Decimal,
    calendar: BusinessCalendarEvidence,
    fixing: A3500FixingEvidence,
    award: AuctionPriceEvidence,
    as_of: datetime,
) -> IssuerCashflow:
    """Reconstruct only the RC55 original cash auction, not a conversion/trade.

    RC55 fixes T-1 relative to the *auction*, not relative to the issue date.
    The auction determines the price, which must be normalized externally and
    evidenced explicitly; this function cannot establish an actual fill.
    """

    _asof(as_of, issuer_series)
    _vno(vno_usd)
    if not isinstance(award, AuctionPriceEvidence) or (
        award.auction_date != AUCTION_DATE
        or award.unit != "USD_EQUIVALENT_PER_USD_VNO"
        or not isinstance(award.evidence_id, str)
        or not award.evidence_id.strip()
    ):
        raise TermsEvidenceError("ORIGINAL_AUCTION_AWARD_UNKNOWN")
    _positive_decimal(award.usd_equivalent_per_usd_vno, "INVALID_AUCTION_PRICE_RATIO")
    _known_at(award.known_at, as_of, "AUCTION_AWARD_NOT_KNOWN_AT_PIT")
    if award.known_at.astimezone(ARGENTINA_TZ).date() < AUCTION_DATE:
        raise TermsEvidenceError("AUCTION_AWARD_BEFORE_AUCTION")
    required_fixing = _prior_business_day(AUCTION_DATE, 1, calendar, as_of)
    _fixing(fixing, required_fixing, as_of)
    return IssuerCashflow(
        kind="ORIGINAL_PRIMARY_SUBSCRIPTION",
        status="FORMULA_ONLY_NOT_ACCOUNT_SETTLEMENT",
        issuer_series=ISSUER_SERIES,
        due_date=ISSUE_DATE,
        vno_usd=vno_usd,
        derived_cash_ars=vno_usd * award.usd_equivalent_per_usd_vno * fixing.ars_per_usd,
        scenario_cash_ars=None,
        coupon_ars=Decimal(0),
        a3500_fixing_date=required_fixing,
        evidence_ids=(RC55_ART1, RC63_ART1, calendar.version_id, fixing.evidence_id, award.evidence_id),
    )


def maturity_amortization(
    *,
    issuer_series: str,
    vno_usd: Decimal,
    calendar: BusinessCalendarEvidence,
    as_of: datetime,
    fixing: A3500FixingEvidence | None = None,
    scenario_ars_per_usd: Decimal | None = None,
    scenario_id: str | None = None,
) -> IssuerCashflow:
    """Calculate ARS due at maturity; never assert cash was received.

    Before the T-3 fixing is published, a separately labelled hypothetical
    rate can give a scenario, but never a contractual or realized cash amount.
    """

    _asof(as_of, issuer_series)
    _vno(vno_usd)
    required_fixing = _prior_business_day(MATURITY_DATE, 3, calendar, as_of)
    if fixing is not None and (scenario_ars_per_usd is not None or scenario_id is not None):
        raise TermsEvidenceError("FIXING_AND_SCENARIO_CONFLICT")
    if fixing is not None:
        _fixing(fixing, required_fixing, as_of)
        return IssuerCashflow(
            kind="MATURITY_AMORTIZATION",
            status="CONTRACTUAL_DUE_NOT_PAYMENT_RECEIPT",
            issuer_series=ISSUER_SERIES,
            due_date=MATURITY_DATE,
            vno_usd=vno_usd,
            derived_cash_ars=vno_usd * fixing.ars_per_usd,
            scenario_cash_ars=None,
            coupon_ars=Decimal(0),
            a3500_fixing_date=required_fixing,
            evidence_ids=(RC55_ART1, RC63_ART1, calendar.version_id, fixing.evidence_id),
        )
    if scenario_ars_per_usd is not None:
        _positive_decimal(scenario_ars_per_usd, "INVALID_SCENARIO_RATE")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise TermsEvidenceError("SCENARIO_PROVENANCE_UNKNOWN")
        return IssuerCashflow(
            kind="MATURITY_AMORTIZATION",
            status="SCENARIO_NO_VERIFICADO",
            issuer_series=ISSUER_SERIES,
            due_date=MATURITY_DATE,
            vno_usd=vno_usd,
            derived_cash_ars=None,
            scenario_cash_ars=vno_usd * scenario_ars_per_usd,
            coupon_ars=Decimal(0),
            a3500_fixing_date=required_fixing,
            evidence_ids=(RC55_ART1, RC63_ART1, calendar.version_id, scenario_id),
        )
    if scenario_id is not None:
        raise TermsEvidenceError("SCENARIO_RATE_UNKNOWN")
    return IssuerCashflow(
        kind="MATURITY_AMORTIZATION",
        status="A3500_FIXING_PENDING_NO_VERIFICADO",
        issuer_series=ISSUER_SERIES,
        due_date=MATURITY_DATE,
        vno_usd=vno_usd,
        derived_cash_ars=None,
        scenario_cash_ars=None,
        coupon_ars=Decimal(0),
        a3500_fixing_date=required_fixing,
        evidence_ids=(RC55_ART1, RC63_ART1, calendar.version_id),
    )

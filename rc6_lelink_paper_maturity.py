"""Bind the approved D30N6 fixing and cash receipt to the existing PAPER lot."""
from datetime import date, datetime

from bs_instrument_contracts import aware_datetime, decimal_value
from rc6_lelink_terms_shadow import (
    A3500FixingEvidence, ARGENTINA_TZ, BCRA_BANK_CALENDAR, BusinessCalendarEvidence,
    ISSUER_SERIES, MATURITY_DATE, RC55_ART1, maturity_amortization,
)


# BCRA's 2026 bank calendar, including the updates published October 1.
# Only dates needed by RC55 art. 1's maturity T-3 rule are in scope.
_MATURITY_CALENDAR = BusinessCalendarEvidence(
    days={
        date(2026, 11, 25): True,
        date(2026, 11, 26): True,
        date(2026, 11, 27): True,
        date(2026, 11, 28): False,
        date(2026, 11, 29): False,
        MATURITY_DATE: True,
    },
    source_url=BCRA_BANK_CALENDAR,
    version_id="BCRA:2026:C101352:C102390:A8487",
    known_at=datetime(2026, 10, 2, tzinfo=ARGENTINA_TZ),
)


def is_lelink_lot(seed):
    return seed.symbol == "D30N6" and seed.family == "LETRAS"


def validate_lelink_event(seed, event, existing_events, *, as_of):
    """Check the exact rate under the ledger lock before any ARS is booked.

    The existing receipt authority verifies source bytes and approval pins.
    This adapter checks the normalized facts against the issuer terms and
    reuses the same persisted events during terminal review.
    """
    if (not is_lelink_lot(seed) or seed.market != "BYMA"
            or seed.trading_currency != "ARS" or event.paper_id != seed.paper_id):
        raise ValueError("FI_LELINK_LOT_IDENTITY_UNVERIFIED")
    at = aware_datetime(as_of)
    fixings = [item for item in existing_events if item.kind == "FIXING"]
    if event.kind == "FIXING":
        if any(item.event_id != event.event_id for item in fixings):
            raise ValueError("FI_LELINK_EXACT_FIXING_REQUIRED")
        recorded_fixing = event
    elif event.kind == "AMORTIZATION":
        if len(fixings) != 1:
            raise ValueError("FI_LELINK_EXACT_FIXING_REQUIRED")
        recorded_fixing = fixings[0]
        if (recorded_fixing.obligation_id != event.obligation_id
                or recorded_fixing.paper_id != seed.paper_id):
            raise ValueError("FI_LELINK_FIXING_OBLIGATION_MISMATCH")
        if aware_datetime(recorded_fixing.recorded_at) > aware_datetime(event.recorded_at):
            raise ValueError("FI_LELINK_FIXING_NOT_KNOWN_AT_PAYMENT")
        # The issuer's rate must already be published when payment is confirmed.
        at = min(at, aware_datetime(event.source_at))
    else:
        raise ValueError("FI_LELINK_SINGLE_AMORTIZATION_REQUIRED")
    if recorded_fixing.source_kind != "OFFICIAL_FIXING":
        raise ValueError("FI_LELINK_EXACT_FIXING_REQUIRED")
    fixing = A3500FixingEvidence(
        fixing_date=aware_datetime(recorded_fixing.effective_at).astimezone(ARGENTINA_TZ).date(),
        ars_per_usd=decimal_value(recorded_fixing.fixing_value, "A3500", positive=True),
        published_at=aware_datetime(recorded_fixing.source_at),
        evidence_id=recorded_fixing.source_sha256,
        source_url=recorded_fixing.source_reference,
        unit=recorded_fixing.fixing_unit,
        series=recorded_fixing.fixing_code,
    )
    nominal = decimal_value(seed.initial_nominal, "D30N6 VNO USD", positive=True)
    due = maturity_amortization(
        issuer_series=ISSUER_SERIES, vno_usd=nominal, calendar=_MATURITY_CALENDAR,
        fixing=fixing, as_of=at,
    )
    if event.kind == "AMORTIZATION":
        if (event.source_kind != "SETTLEMENT_PROOF"
                or event.source_sha256 == recorded_fixing.source_sha256
                or event.source_reference == recorded_fixing.source_reference):
            raise ValueError("FI_LELINK_PAYMENT_REQUIRES_DISTINCT_SETTLEMENT_PROOF")
        if (event.payment_currency != "ARS"
                or aware_datetime(event.effective_at).astimezone(ARGENTINA_TZ).date() != due.due_date
                or decimal_value(event.nominal_base, "entitled USD VNO", positive=True) != nominal
                or decimal_value(event.nominal_redeemed, "redeemed USD VNO", positive=True) != nominal
                or decimal_value(event.cash_amount, "paid ARS", positive=True) != due.derived_cash_ars):
            raise ValueError("FI_LELINK_PAYMENT_ECONOMICS_MISMATCH")
    return fixing


def validate_lelink_schedule(seed, schedule, final_event, events, *, as_of):
    fixing = validate_lelink_event(seed, final_event, events, as_of=as_of)
    if (len(events) != 2 or schedule.get("series_id") != ISSUER_SERIES
            or schedule.get("source_reference") != RC55_ART1
            or schedule.get("principal_currency") != "ARS"
            or schedule.get("redemption_basis") != "OFFICIAL_INDEXED_FACE"
            or schedule.get("indexation") != fixing.series
            or schedule.get("redemption_face_official") is not True
            or aware_datetime(schedule["known_at"]) < fixing.published_at):
        raise ValueError("FI_LELINK_ISSUER_SCHEDULE_UNVERIFIED")

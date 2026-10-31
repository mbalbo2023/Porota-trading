"""A source label/hash is never sufficient to authorize live FCI allocation."""
from dataclasses import replace

import pytest

from rc6_fund_source_quarantine import (
    PAPER_ONLY, RUNTIME_ENTRY_AUTHORITY, SOURCE_AUTHENTICATED, REAL_ROUTES_USED,
    FundSourceFact, content_hash, inspect_fund_source_bundle,
)


AT = "2026-10-30T12:00:00+00:00"  # 09:00 America/Argentina/Buenos_Aires
RECEIVED = "2026-10-30T11:50:00+00:00"
NAV_DATE = "2026-10-29T18:00:00+00:00"
SHA = "a" * 64
CLASS = "FCI.REAL.CLASS.1"  # An opaque fixture label, not a real account class.


def fact(kind, fields, *, source, effective="2026-10-29T12:00:00+00:00",
         published="2026-10-29T20:00:00+00:00", received=RECEIVED,
         class_key=CLASS, market="FCI", reference=None):
    candidate = FundSourceFact(
        class_key=class_key, market=market, currency="ARS",
        settlement="INMEDIATA", kind=kind, source_kind=source,
        source_reference=reference or "PUBLIC_RECORD_001", effective_at=effective,
        published_at=published, received_at=received, fields=fields,
        content_sha256=SHA)
    return replace(candidate, content_sha256=content_hash(candidate))


def bundle():
    return [
        fact("CLASS_TERMS", {
            "manager": "FUND_MANAGER_FIXTURE", "regulation_reference": "REGULATION_FIXTURE",
            "regulation_sha256": SHA, "subscription_min": "1000",
            "subscription_step": "0.01", "broker_minimum_reference": "MINIMUM_FIXTURE",
            "broker_step_reference": "STEP_FIXTURE", "redemption_lead_days": "2",
        }, source="FUND_MANAGER_DOCUMENT"),
        fact("CALENDAR", {
            "calendar_date": "2026-10-30", "cutoff_hhmm": "14:30",
            "cutoff_zone": "America/Argentina/Buenos_Aires",
        }, source="FUND_MANAGER_DOCUMENT"),
        fact("ACCOUNT_FEES", {
            "subscription_fee": "0", "redemption_fee": "0",
            "account_tariff_reference": "ACCOUNT_TARIFF_FIXTURE",
        }, source="BROKER_ACCOUNT_TARIFF"),
        fact("NAV", {
            "nav_per_unit": "12.45", "nav_unit": "CUOTAPARTE",
        }, source="PPI_STRUCTURED_API", effective=NAV_DATE,
             published="2026-10-29T19:00:00+00:00"),
    ]


def test_structurally_complete_sources_remain_quarantined_not_authenticated():
    inputs = bundle()
    decision = inspect_fund_source_bundle(inputs, as_of=AT)
    assert decision.status == "QUARANTINED_STRUCTURALLY_COMPLETE"
    assert decision.reasons == () and decision.warnings == ()
    assert len(decision.source_fact_hashes) == 4
    assert set(decision.covered_kinds) == {
        "CLASS_TERMS", "CALENDAR", "ACCOUNT_FEES", "NAV"}
    assert decision.source_authenticated is SOURCE_AUTHENTICATED is False
    assert decision.runtime_entry_authority is RUNTIME_ENTRY_AUTHORITY is False
    assert decision.paper_only is PAPER_ONLY is True
    assert decision.real_routes_used == REAL_ROUTES_USED == ()
    # Idempotent replay cannot create different semantics or extra evidence.
    assert inspect_fund_source_bundle(inputs + [inputs[0]], as_of=AT) == decision


@pytest.mark.parametrize("modify,reason", [
    (lambda b: b[:3], "FCI_SOURCE_MISSING_NAV"),
    (lambda b: b[:2] + [replace(b[2], source_kind="IOL_MCP_READONLY")] + b[3:],
     "FCI_SOURCE_MISSING_ACCOUNT_FEES"),
    (lambda b: b[:1] + [replace(b[1], fields={**b[1].fields, "token": "DO_NOT_COPY"})] + b[2:],
     "FCI_SOURCE_ALLOWLIST_OR_REFERENCE_INVALID"),
    (lambda b: b[:3] + [replace(b[3], class_key="FCI.OTHER")],
     "FCI_SOURCE_HASH_OR_IDENTITY_MISMATCH"),
    (lambda b: b[:3] + [replace(b[3], content_sha256=SHA)],
     "FCI_SOURCE_HASH_OR_IDENTITY_MISMATCH"),
    (lambda b: b[:3] + [replace(b[3], received_at="2026-10-30T12:01:00+00:00")],
     "FCI_SOURCE_HASH_OR_IDENTITY_MISMATCH"),
])
def test_missing_legal_source_unknown_key_and_undigested_edit_hold(modify, reason):
    decision = inspect_fund_source_bundle(modify(bundle()), as_of=AT)
    assert decision.status == "QUARANTINED_INCOMPLETE"
    assert reason in decision.reasons
    assert decision.runtime_entry_authority is False


@pytest.mark.parametrize("kind,field,value,reason", [
    ("NAV", "nav_per_unit", "NaN", "FCI_NAV_AMOUNT_INVALID"),
    ("NAV", "nav_unit", "SHARE", "FCI_NAV_UNIT_UNVERIFIED"),
    ("CLASS_TERMS", "subscription_step", "0", "FCI_CLASS_TERMS_UNVERIFIED"),
    ("CLASS_TERMS", "redemption_lead_days", "-1", "FCI_CLASS_REDEMPTION_LEAD_INVALID"),
    ("CALENDAR", "calendar_date", "2026-10-31", "FCI_CALENDAR_CUTOFF_OR_DAY_INVALID"),
    ("CALENDAR", "cutoff_zone", "America/New_York", "FCI_CALENDAR_CUTOFF_OR_DAY_INVALID"),
    ("ACCOUNT_FEES", "subscription_fee", "-0.01", "FCI_ACCOUNT_FEES_INVALID"),
])
def test_financial_fields_and_calendar_are_fail_closed(kind, field, value, reason):
    original = bundle()
    i = next(i for i, x in enumerate(original) if x.kind == kind)
    changed = replace(original[i], fields={**original[i].fields, field: value})
    original[i] = replace(changed, content_sha256=content_hash(changed))
    decision = inspect_fund_source_bundle(original, as_of=AT)
    assert reason in decision.reasons
    assert decision.runtime_entry_authority is False


def test_nav_publish_cannot_precede_effective_and_stale_nav_is_not_fresh():
    base = bundle()
    nav = replace(base[-1], published_at="2026-10-29T17:59:59+00:00")
    base[-1] = replace(nav, content_sha256=content_hash(nav))
    assert "FCI_NAV_CLOCK_OR_AGE_INVALID" in inspect_fund_source_bundle(
        base, as_of=AT).reasons
    base = bundle()
    nav = replace(base[-1], effective_at="2026-10-20T18:00:00+00:00")
    base[-1] = replace(nav, content_sha256=content_hash(nav))
    assert "FCI_NAV_CLOCK_OR_AGE_INVALID" in inspect_fund_source_bundle(
        base, as_of=AT).reasons


def test_stale_secondary_iol_is_amber_not_veto_of_complete_primary():
    base = bundle()
    iol = replace(base[-1], source_kind="IOL_MCP_READONLY",
                  source_reference="IOL_OLD_NAV",
                  effective_at="2026-10-20T18:00:00+00:00")
    iol = replace(iol, content_sha256=content_hash(iol))
    result = inspect_fund_source_bundle(base + [iol], as_of=AT)
    assert result.status == "QUARANTINED_STRUCTURALLY_COMPLETE"
    assert result.reasons == ()
    assert result.warnings == (
        "FCI_SECONDARY_SOURCE_IGNORED:FCI_NAV_CLOCK_OR_AGE_INVALID",)
    assert len(result.source_fact_hashes) == 4


def test_conflicting_fresh_nav_and_exact_class_market_mismatch_are_not_selected():
    base = bundle()
    alternative = replace(base[-1], source_kind="IOL_MCP_READONLY",
                          source_reference="IOL_FRESH_NAV",
                          fields={"nav_per_unit": "12.46", "nav_unit": "CUOTAPARTE"})
    alternative = replace(alternative, content_sha256=content_hash(alternative))
    decision = inspect_fund_source_bundle(base + [alternative], as_of=AT)
    assert "FCI_SOURCE_CONTRADICTORY_NAV" in decision.reasons
    changed = replace(base[1], market="BYMA")
    changed = replace(changed, content_sha256=content_hash(changed))
    decision = inspect_fund_source_bundle(base + [changed], as_of=AT)
    assert "FCI_SOURCE_CLASS_IDENTITY_COLLISION" in decision.reasons


@pytest.mark.parametrize("market,currency", [
    ("FCI_EXTERIOR", "USD"), ("FCI", "USD"), ("EXTERIOR", "ARS"),
])
def test_fci_exterior_or_nonlocal_currency_remains_quarantined_even_with_valid_hash(
        market, currency):
    facts = []
    for original in bundle():
        changed = replace(original, market=market, currency=currency)
        facts.append(replace(changed, content_sha256=content_hash(changed)))
    review = inspect_fund_source_bundle(facts, as_of=AT)
    assert review.status == "QUARANTINED_INCOMPLETE"
    assert "FCI_SOURCE_IDENTITY_OR_KIND_INVALID" in review.reasons
    assert review.source_authenticated is SOURCE_AUTHENTICATED is False
    assert review.runtime_entry_authority is RUNTIME_ENTRY_AUTHORITY is False


def test_malformed_scope_raises_without_any_host_or_auth_operation():
    for args in ({"max_nav_age_days": True}, {"max_nav_age_days": -1},
                 {"max_nav_age_days": 367}):
        with pytest.raises(ValueError, match="FCI_SOURCE_BUNDLE_SCOPE_INVALID"):
            inspect_fund_source_bundle(bundle(), as_of=AT, **args)
    with pytest.raises(ValueError, match="FCI_SOURCE_AS_OF_INVALID"):
        inspect_fund_source_bundle(bundle(), as_of="no-time")

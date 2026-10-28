"""FCI cash-parking: deterministic synthetic PAPER, never broker authority."""
from dataclasses import replace
from decimal import Decimal

import pytest

from be_paper_engine import PaperBroker, PaperStore
from rc6_fund_paper_allocator import (
    PAPER_ONLY, REAL_ROUTES_USED, RUNTIME_ENTRY_AUTHORITY,
    FundAllocationBudget, FundAllocationEvidence, FundNavMark,
    assess_fund_cash_parking, simulate_fund_subscription,
)
from rc6_fund_paper_reconciler import FundClassEvidence, reconcile_fund_store
from rc6_paper_family_lifecycle import PaperFundTerms


STAMP = "2026-10-27T12:00:00+00:00"  # 09:00 ART
SYMBOL = "SYNTHETIC.FCI.C1"
SHA = "b" * 64


def fixture():
    terms = PaperFundTerms(SYMBOL, "FCI", "ARS", "FCI", "INMEDIATA",
                           "SYNTHETIC_EXACT_CLASS",
                           paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT")
    ce = FundClassEvidence(terms, SYMBOL, "SYNTHETIC_CLASS_REGULATION", SHA,
                           "2026-10-26T19:00:00+00:00")
    nav = FundNavMark(Decimal("12.5"), "2026-10-26T16:00:00+00:00",
                      "2026-10-26T18:00:00+00:00", "2026-10-26T18:01:00+00:00",
                      "SYNTHETIC_OFFICIAL_NAV", SHA)
    evidence = FundAllocationEvidence(
        ce, "SYNTHETIC_TEST_ONLY", "2026-10-27", "SYNTHETIC_FUND_CALENDAR", SHA,
        "2026-10-26T19:00:00+00:00", "14:30", "America/Argentina/Buenos_Aires",
        2, Decimal("0"), "SYNTHETIC_ACCOUNT_EXACT_FEES", SHA,
        "2026-10-26T19:00:00+00:00", nav)
    budget = FundAllocationBudget(Decimal("2000"), Decimal("250"), Decimal("1500"),
                                  Decimal("1000"), 4, 3)
    return evidence, budget


def test_synthetic_cash_parking_is_explicitly_not_runtime_authority():
    e, b = fixture()
    d = assess_fund_cash_parking(e, b, as_of=STAMP)
    assert d.status == "SYNTHETIC_OFFLINE_PAPER_CANDIDATE"
    assert d.amount_ars == Decimal("1000")
    assert d.reason_codes == ()
    assert d.strategy == "FCI_ARS_CASH_PARKING_NO_RETURN_FORECAST"
    assert d.runtime_entry_authority is False and RUNTIME_ENTRY_AUTHORITY is False
    assert d.paper_only and PAPER_ONLY and d.real_routes_used == REAL_ROUTES_USED == ()


def test_synthetic_exercises_actual_durable_subscription_and_open_reconciliation(tmp_path):
    e, b = fixture()
    store = PaperStore(str(tmp_path / "fund.db"))
    broker = PaperBroker(store, initial_cash="2000")
    result = simulate_fund_subscription(
        broker.family_paper, evidence=e, budget=b, lifecycle_id="fci-1",
        event_id="fci-e1", occurred_at=STAMP,
        cash_guard=lambda conn: Decimal("2000"), admission_guard=lambda conn: "")
    assert result["state"] == "SUBSCRIBE_REQUESTED"
    snap = reconcile_fund_store(store.path, "fci-1", [], e.class_evidence, as_of=STAMP)
    assert snap.subscription_ars == Decimal("1000")
    assert snap.units is None and snap.net_pnl_ars is None
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 1
        assert conn.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


@pytest.mark.parametrize("alter,reason", [
    (lambda e, b: (replace(e, provenance="UNVERIFIED_EXTERNAL"), b),
     "FCI_EXTERNAL_PROVENANCE_NOT_AUTHENTICATED"),
    (lambda e, b: (replace(e, calendar_date="2026-10-26"), b),
     "FCI_CLASS_CALENDAR_DATE_NOT_PROVEN"),
    (lambda e, b: (replace(e, cutoff_local_hhmm="09:00"), b),
     "FCI_CLASS_CUTOFF_PASSED"),
    (lambda e, b: (replace(e, calendar_known_at="2026-10-27T16:00:00+00:00"), b),
     "FCI_CLASS_CALENDAR_UNVERIFIED_AT_PIT"),
    (lambda e, b: (replace(e, calendar_sha256="not-a-sha"), b),
     "FCI_CLASS_CALENDAR_UNVERIFIED_AT_PIT"),
    (lambda e, b: (replace(e, fee_known_at="2026-10-27T16:00:00+00:00"), b),
     "FCI_ACCOUNT_FEES_UNVERIFIED_AT_PIT"),
    (lambda e, b: (replace(e, subscription_fee_ars=None), b),
     "FCI_EXPLICIT_ACCOUNT_FEE_UNKNOWN"),
    (lambda e, b: (replace(e, subscription_fee_ars=Decimal("10")), b),
     "FCI_NONZERO_FEE_NOT_MODELED_IN_LEDGER"),
    (lambda e, b: (replace(e, last_nav=None), b),
     "FCI_NAV_POINT_IN_TIME_MISSING"),
    (lambda e, b: (replace(e, last_nav=replace(e.last_nav,
                                              published_at="2026-10-27T16:00:00+00:00")), b),
     "FCI_NAV_FUTURE_OR_CLOCK_INCONSISTENT"),
    (lambda e, b: (replace(e, last_nav=replace(e.last_nav, nav_per_unit=Decimal("0"))), b),
     "FCI_NAV_NONPOSITIVE"),
    (lambda e, b: (e, replace(b, maximum_nav_age_days=0)),
     "FCI_NAV_TOO_OLD_OR_AGE_LIMIT_UNKNOWN"),
    (lambda e, b: (e, replace(b, available_cash_ars=Decimal("900"))),
     "FCI_CASH_OR_POSITION_LIMIT"),
    (lambda e, b: (e, replace(b, maximum_position_ars=Decimal("900"))),
     "FCI_CASH_OR_POSITION_LIMIT"),
    (lambda e, b: (e, replace(b, requested_subscription_ars=Decimal("999"))),
     "FCI_CLASS_AMOUNT_POLICY_REJECTED"),
    (lambda e, b: (e, replace(b, holding_horizon_days=1)),
     "FCI_REDEMPTION_HORIZON_NOT_COVERED"),
    (lambda e, b: (replace(e, class_evidence=replace(
        e.class_evidence, class_key="OTHER")), b),
     "FCI_EXACT_CLASS_MISMATCH"),
    (lambda e, b: (replace(e, class_evidence=replace(
        e.class_evidence, known_at="2026-10-27T17:00:00+00:00")), b),
     "FCI_EXACT_REGULATION_UNVERIFIED_AT_PIT"),
    (lambda e, b: (replace(e, cutoff_zone="UTC"), b),
     "FCI_CLASS_CUTOFF_ZONE_UNKNOWN"),
])
def test_missing_inconsistent_future_and_unverified_inputs_hold(alter, reason):
    e, b = fixture()
    e, b = alter(e, b)
    d = assess_fund_cash_parking(e, b, as_of=STAMP)
    assert d.status == "HOLD" and d.amount_ars is None
    assert reason in d.reason_codes


def test_no_evidence_never_invents_a_class_or_account_fee():
    d = assess_fund_cash_parking(None, None, as_of=STAMP)
    assert d.status == "HOLD" and d.amount_ars is None
    assert d.reason_codes == ("FCI_CLASS_SCHEDULE_FEES_NAV_MISSING", "FCI_RISK_BUDGET_MISSING")


def test_invalid_timestamp_rejected():
    with pytest.raises(ValueError, match="FCI_AS_OF_INVALID"):
        assess_fund_cash_parking(None, None, as_of="27 October")


def test_executor_never_operates_with_missing_guards_or_runtime_store(tmp_path, monkeypatch):
    import cg_paper_workspace
    e, b = fixture()
    store = PaperStore(str(tmp_path / "fund.db"))
    broker = PaperBroker(store, initial_cash="2000")
    kwargs = dict(evidence=e, budget=b, lifecycle_id="fci-1", event_id="e1",
                  occurred_at=STAMP, cash_guard=lambda conn: Decimal("2000"),
                  admission_guard=lambda conn: "")
    with pytest.raises(ValueError, match="FCI_PAPER_ADMISSION_GUARDS_REQUIRED"):
        simulate_fund_subscription(broker.family_paper, **{**kwargs, "cash_guard": None})
    monkeypatch.setattr(cg_paper_workspace, "database_path", lambda: store.path)
    with pytest.raises(ValueError, match="FCI_RUNTIME_ADAPTER_NOT_AUTHORIZED"):
        simulate_fund_subscription(broker.family_paper, **kwargs)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0


def test_executor_denies_non_authorized_external_or_admission_guard(tmp_path):
    e, b = fixture()
    store = PaperStore(str(tmp_path / "fund.db"))
    broker = PaperBroker(store, initial_cash="2000")
    kwargs = dict(evidence=e, budget=b, lifecycle_id="fci-1", event_id="e1",
                  occurred_at=STAMP, cash_guard=lambda conn: Decimal("2000"),
                  admission_guard=lambda conn: "")
    with pytest.raises(ValueError, match="FCI_PAPER_ALLOCATION_HOLD"):
        simulate_fund_subscription(broker.family_paper, **{
            **kwargs, "evidence": replace(e, provenance="UNVERIFIED_EXTERNAL")})
    with pytest.raises(ValueError, match="FCI_PAPER_INSUFFICIENT_CASH"):
        simulate_fund_subscription(broker.family_paper, **{
            **kwargs, "cash_guard": lambda conn: Decimal("100")})
    with pytest.raises(ValueError, match="TEST_ADMISSION_CLOSED"):
        simulate_fund_subscription(broker.family_paper, **{
            **kwargs, "admission_guard": lambda conn: "TEST_ADMISSION_CLOSED"})
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0

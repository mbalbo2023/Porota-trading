from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from bt_caucion_paper import CaucionOffer
from de_concurrent_risk_capacity_hf6 import capacity, emergency_position_guard
from df_caucion_end_of_day_sweep_hf6 import plan_sweep

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def test_concurrent_risk_uses_soft_stop_budget_not_small_position_count():
    snap = capacity(
        baseline_equity="1000000",
        soft_stop_pct="1.5",
        realized_pnl_today="0",
        open_stop_risk="10000",
        candidate_stop_risk="3000",
    )
    assert snap.soft_stop_budget == Decimal("15000")
    assert snap.remaining_before_candidate == Decimal("5000")
    assert snap.admitted is True

    blocked = capacity(
        baseline_equity="1000000",
        soft_stop_pct="1.5",
        realized_pnl_today="0",
        open_stop_risk="10000",
        candidate_stop_risk="6000",
    )
    assert blocked.admitted is False
    assert blocked.reason == "CANDIDATE_EXCEEDS_REMAINING_CONCURRENT_RISK"


def test_realized_gains_never_expand_concurrent_risk_budget():
    flat = capacity(
        baseline_equity="1000000", soft_stop_pct="1.5", realized_pnl_today="0",
        open_stop_risk="4000", candidate_stop_risk="1000")
    gain = capacity(
        baseline_equity="1000000", soft_stop_pct="1.5", realized_pnl_today="50000",
        open_stop_risk="4000", candidate_stop_risk="1000")
    assert gain.soft_stop_budget == flat.soft_stop_budget
    assert gain.remaining_before_candidate == flat.remaining_before_candidate


def test_realized_loss_consumes_capacity_before_new_candidate():
    snap = capacity(
        baseline_equity="1000000", soft_stop_pct="1.5", realized_pnl_today="-5000",
        open_stop_risk="8000", candidate_stop_risk="3000")
    assert snap.realized_loss_consumed == Decimal("5000")
    assert snap.remaining_before_candidate == Decimal("2000")
    assert snap.admitted is False


def test_emergency_position_cap_is_separate_runaway_guard():
    assert emergency_position_guard(7, emergency_cap=50) == ""
    assert emergency_position_guard(50, emergency_cap=50) == "EMERGENCY_POSITION_CAP"


def _offer(now, *, maturity, quoted_at=None):
    return CaucionOffer(
        instrument_id="TEST-CAUCION-1D",
        currency="ARS",
        annual_rate_fraction=Decimal("0.30"),
        start_date=now.astimezone(TZ).date().isoformat(),
        maturity_at=maturity.isoformat(),
        quoted_at=(quoted_at or now).isoformat(),
        available_principal=Decimal("500000"),
        minimum_principal=Decimal("1000"),
        principal_step=Decimal("1000"),
        day_count_basis=365,
        fee_payment="MATURITY",
        metadata_source="TEST_VERIFIED_CONTRACT",
    )


def test_sweep_requires_verified_schedule_source():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    plan = plan_sweep(
        offers=[], currency="ARS", as_of=now, available_cash="100000",
        required_reserve="10000", sweep_start_at=now-timedelta(minutes=20),
        order_cutoff_at=now+timedelta(minutes=10),
        liquidity_deadline=now+timedelta(hours=18), schedule_source="")
    assert plan.state == "FAIL_CLOSED"
    assert plan.reason == "SCHEDULE_SOURCE_MISSING"


def test_sweep_waits_until_final_window():
    now = datetime(2026, 9, 3, 16, 0, tzinfo=TZ)
    plan = plan_sweep(
        offers=[], currency="ARS", as_of=now, available_cash="100000",
        required_reserve="10000", sweep_start_at=now+timedelta(minutes=30),
        order_cutoff_at=now+timedelta(hours=1),
        liquidity_deadline=now+timedelta(hours=19),
        schedule_source="VERIFIED_TEST_SCHEDULE")
    assert plan.state == "WAITING_WINDOW"


def test_sweep_rejects_maturity_after_required_liquidity_deadline():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    deadline = datetime(2026, 9, 4, 10, 30, tzinfo=TZ)
    offer = _offer(now, maturity=deadline+timedelta(minutes=30))
    plan = plan_sweep(
        offers=[offer], currency="ARS", as_of=now, available_cash="100000",
        required_reserve="10000", sweep_start_at=now-timedelta(minutes=20),
        order_cutoff_at=now+timedelta(minutes=10), liquidity_deadline=deadline,
        schedule_source="VERIFIED_TEST_SCHEDULE")
    assert plan.state == "NO_ACTION"


def test_sweep_builds_candidate_only_from_free_settled_cash():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    deadline = datetime(2026, 9, 4, 10, 30, tzinfo=TZ)
    offer = _offer(now, maturity=datetime(2026, 9, 4, 10, 0, tzinfo=TZ))
    plan = plan_sweep(
        offers=[offer], currency="ARS", as_of=now, available_cash="100000",
        required_reserve="20000", sweep_start_at=now-timedelta(minutes=20),
        order_cutoff_at=now+timedelta(minutes=10), liquidity_deadline=deadline,
        schedule_source="VERIFIED_TEST_SCHEDULE", participation=Decimal("0.10"))
    assert plan.state == "PAPER_CANDIDATE"
    assert plan.principal <= Decimal("80000")
    assert plan.principal <= offer.available_principal * Decimal("0.10")
    assert plan.net_profit > 0

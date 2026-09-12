import ast
import inspect
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bt_caucion_paper import CaucionOffer
import fj_caucion_cash_sweep_shadow_rc6 as shadow


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _offer(now, *, exact_fees=True):
    return CaucionOffer(
        instrument_id="PESOS1",
        currency="ARS",
        annual_rate_fraction=Decimal("0.30"),
        start_date=now.date().isoformat(),
        maturity_at=(now + timedelta(hours=17)).isoformat(),
        quoted_at=now.isoformat(),
        available_principal=Decimal("500000"),
        minimum_principal=Decimal("1000"),
        principal_step=Decimal("1000"),
        day_count_basis=365,
        fee_payment="MATURITY",
        metadata_source="VERIFIED_TEST_CAUCION",
        quoted_total_fees=Decimal("20") if exact_fees else None,
        fee_quote_principal=Decimal("50000") if exact_fees else None,
    )


def _run(now, **overrides):
    cutoff = now.replace(hour=17, minute=0, second=0, microsecond=0)
    deadline = (now + timedelta(days=1)).replace(hour=10, minute=30, second=0, microsecond=0)
    kwargs = dict(
        offers=[_offer(now)],
        currency="ARS",
        as_of=now,
        available_cash="100000",
        required_reserve="20000",
        order_cutoff_at=cutoff,
        liquidity_deadline=deadline,
        schedule_source="VERIFIED_TEST_SCHEDULE",
        settlement_calendar_verified=True,
        already_swept_today=False,
        policy_lead_minutes=20,
        participation=Decimal("0.10"),
        max_quote_age_seconds=30,
    )
    kwargs.update(overrides)
    return shadow.shadow_cash_sweep(**kwargs)


def test_verified_window_produces_shadow_candidate_only():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    decision = _run(now)
    assert decision.mode == "SHADOW_ONLY"
    assert decision.state == "SHADOW_CANDIDATE"
    assert decision.real_execution_allowed is False
    assert decision.policy_lead_minutes == 20
    assert decision.sweep_start_at.endswith("16:40:00-03:00")
    assert Decimal(decision.principal) == Decimal("50000.00")
    assert Decimal(decision.principal) <= Decimal("80000")
    assert Decimal(decision.net_profit) > 0


def test_policy_lead_is_derived_from_verified_cutoff_not_a_market_hour_constant():
    now = datetime(2026, 9, 3, 16, 20, tzinfo=TZ)
    custom_cutoff = datetime(2026, 9, 3, 16, 55, tzinfo=TZ)
    decision = _run(now, order_cutoff_at=custom_cutoff, policy_lead_minutes=15)
    assert decision.sweep_start_at.endswith("16:40:00-03:00")
    assert decision.order_cutoff_at.endswith("16:55:00-03:00")
    assert decision.state == "WAITING_WINDOW"


def test_missing_schedule_or_calendar_evidence_fails_closed():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    missing_source = _run(now, schedule_source="")
    assert missing_source.state == "FAIL_CLOSED"
    assert missing_source.reason == "SCHEDULE_SOURCE_MISSING"
    missing_calendar = _run(now, settlement_calendar_verified=False)
    assert missing_calendar.state == "FAIL_CLOSED"
    assert missing_calendar.reason == "SETTLEMENT_CALENDAR_UNVERIFIED"


def test_one_sweep_per_day_guard_stays_non_executing():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    decision = _run(now, already_swept_today=True)
    assert decision.state == "NO_ACTION"
    assert decision.reason == "ALREADY_SWEPT_TODAY"
    assert decision.real_execution_allowed is False


def test_unknown_fee_curve_is_not_extrapolated():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    decision = _run(now, offers=[_offer(now, exact_fees=False)])
    assert decision.state == "HOLD"
    assert decision.reason == "EXACT_FEE_BUDGET_MISSING"


def test_policy_lead_has_conservative_bounds():
    now = datetime(2026, 9, 3, 16, 50, tzinfo=TZ)
    with pytest.raises(ValueError, match="POLICY_LEAD_MINUTES_OUT_OF_RANGE"):
        _run(now, policy_lead_minutes=5)
    with pytest.raises(ValueError, match="POLICY_LEAD_MINUTES_OUT_OF_RANGE"):
        _run(now, policy_lead_minutes=60)


def test_shadow_layer_has_no_position_liquidation_or_order_execution_surface():
    shadow.assert_shadow_only()
    source = inspect.getsource(shadow)
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert "di_caucion_cash_sweep_runtime_hf6" not in imports
    for token in ("allocate_caucion", "run_paper_sweep", "send_order", "place_order", "paper_positions"):
        assert token not in source

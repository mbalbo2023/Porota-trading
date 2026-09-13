from datetime import datetime

from rc6_caucion_schedule_gate import CaucionScheduleEvidence, evaluate_schedule

DAY = "2026-09-14"


def evidence(**changes):
    value = dict(
        business_date=DAY,
        currency="ARS",
        operation="COLOCAR-CAUCION",
        opens_at=DAY + "T10:30:00-03:00",
        market_closes_at=DAY + "T17:00:00-03:00",
        order_cutoff_at=DAY + "T17:00:00-03:00",
        byma_source="TEST_BYMA_SCHEDULE_VERSIONED",
        broker_source="TEST_PPI_CUTOFF_VERSIONED",
        broker_cutoff_verified=True,
        evidence_version="TEST-CAUCION-SCHEDULE-V1",
    )
    value.update(changes)
    return CaucionScheduleEvidence(**value)


def at(clock):
    return datetime.fromisoformat(DAY + "T" + clock + "-03:00")


def test_verified_window_and_broker_cutoff_open_inside_session():
    result = evaluate_schedule(evidence(), now=at("15:00:00"))
    assert result["state"] == "OPEN"
    assert result["calendar_state"] == "OPEN"
    assert result["cutoff_state"] == "OPEN"


def test_missing_broker_cutoff_evidence_is_hold_even_if_byma_window_known():
    result = evaluate_schedule(
        evidence(broker_cutoff_verified=False, broker_source=""),
        now=at("15:00:00"),
    )
    assert result["state"] == "HOLD"
    assert result["reason"] == "PPI_BROKER_CUTOFF_UNVERIFIED"


def test_before_open_and_at_market_close_are_hold():
    assert evaluate_schedule(evidence(), now=at("10:29:59"))["state"] == "HOLD"
    assert evaluate_schedule(evidence(), now=at("17:00:00"))["state"] == "HOLD"


def test_broker_cutoff_may_be_earlier_than_market_close_and_is_binding():
    ev = evidence(order_cutoff_at=DAY + "T16:45:00-03:00")
    open_result = evaluate_schedule(ev, now=at("16:44:59"))
    assert open_result["state"] == "OPEN"
    closed_result = evaluate_schedule(ev, now=at("16:45:00"))
    assert closed_result["state"] == "HOLD"
    assert closed_result["calendar_state"] == "OPEN"
    assert closed_result["reason"] == "PPI_CAUCION_ORDER_CUTOFF_REACHED"


def test_invalid_schedule_order_is_rejected():
    try:
        evidence(order_cutoff_at=DAY + "T17:01:00-03:00")
    except ValueError as exc:
        assert "schedule ordering invalid" in str(exc)
    else:
        raise AssertionError("cutoff after market close unexpectedly accepted")


def test_wrong_operation_is_rejected():
    try:
        evidence(operation="TOMAR-CAUCION")
    except ValueError as exc:
        assert "COLOCAR-CAUCION" in str(exc)
    else:
        raise AssertionError("wrong operation unexpectedly accepted")

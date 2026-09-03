import rc4_introspection_freshness as freshness


def test_midnight_waiting_snapshot_is_superseded_by_live_market_open():
    snapshot={
        "timestamp":"2026-09-03T00:15:00-03:00",
        "verdict":"WARN",
        "observer":{
            "process_state":"WAITING_MARKET","session_state":"MARKET_CLOSED",
            "heartbeat_at":"2026-09-03T00:15:11-03:00",
        },
    }
    live={
        "process_state":"RUNNING","session_state":"MARKET_OPEN",
        "heartbeat_at":"2026-09-03T13:37:38-03:00","ppi_auth":"OK",
        "real_orders_sent":0,
    }
    result=freshness.reconcile(snapshot,live,now="2026-09-03T13:38:00-03:00")
    assert result["display_state"] == "SUPERSEDED_BY_LIVE_STATE"
    assert result["current_observer"]["process_state"] == "RUNNING"
    assert result["current_observer"]["session_state"] == "MARKET_OPEN"
    assert result["deep_verdict"] == "WARN"
    assert result["warning"]


def test_current_snapshot_remains_current_when_live_agrees():
    snapshot={
        "timestamp":"2026-09-03T13:30:00-03:00",
        "verdict":"OK",
        "observer":{
            "process_state":"RUNNING","session_state":"MARKET_OPEN",
            "heartbeat_at":"2026-09-03T13:30:00-03:00",
        },
    }
    live={
        "process_state":"RUNNING","session_state":"MARKET_OPEN",
        "heartbeat_at":"2026-09-03T13:30:30-03:00",
    }
    result=freshness.reconcile(snapshot,live,now="2026-09-03T13:31:00-03:00")
    assert result["display_state"] == "CURRENT_DEEP_SNAPSHOT"
    assert result["warning"] == ""


def test_stale_snapshot_is_not_reported_as_current_even_without_live_row():
    snapshot={"timestamp":"2026-09-03T00:15:00-03:00","observer":{}}
    result=freshness.reconcile(snapshot,{},now="2026-09-03T13:30:00-03:00")
    assert result["display_state"] == "STALE"


def test_missing_snapshot_is_explicit():
    result=freshness.reconcile(None,{"process_state":"RUNNING"},now="2026-09-03T13:30:00-03:00")
    assert result["display_state"] == "NO_SNAPSHOT"
    assert result["current_observer"]["process_state"] == "RUNNING"

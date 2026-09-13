from datetime import datetime, timedelta

from be_paper_engine import PaperStore
from rc6_caucion_fresh_data_agent import DEFAULT_EXPECTED_TICKERS
from rc6_caucion_readiness_state import effective_state, persist_gate

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")


def gate(**changes):
    value = {
        "name":"CAUCION_FRESH_DATA_AGENT_GREEN",
        "family":"CAUCIONES",
        "green":True,
        "contract_status":"READY_PAPER_CANDIDATE",
        "real_order_capability":False,
        "evidence_id":"fresh-10-of-10",
        "tickers":list(DEFAULT_EXPECTED_TICKERS),
        "reasons":[],
    }
    value.update(changes)
    return value


def store(tmp_path):
    return PaperStore(str(tmp_path / "readiness.db"))


def test_missing_state_is_hold_and_zero_ready(tmp_path):
    result = effective_state(store(tmp_path), now=NOW)
    assert result["state"] == "HOLD"
    assert result["ready_paper_count"] == 0
    assert result["green"] is False


def test_green_exact_ten_universe_persists_as_specialized_ready(tmp_path):
    s = store(tmp_path)
    result = persist_gate(s, gate(), evaluated_at=NOW)
    assert result["state"] == "READY_PAPER"
    assert result["ready_paper_count"] == 10
    assert result["real_order_capability"] is False


def test_green_becomes_hold_when_state_ages_past_300_seconds(tmp_path):
    s = store(tmp_path)
    persist_gate(s, gate(), evaluated_at=NOW)
    result = effective_state(s, now=NOW + timedelta(seconds=301))
    assert result["state"] == "HOLD"
    assert result["ready_paper_count"] == 0
    assert result["reason"] == "CAUCION_READINESS_STALE_OR_FUTURE"


def test_red_gate_persists_hold_not_ready(tmp_path):
    s = store(tmp_path)
    result = persist_gate(
        s,
        gate(green=False, contract_status="BLOCKED", tickers=["PESOS1"], reasons=["MISSING_TICKER:PESOS2"]),
        evaluated_at=NOW,
    )
    assert result["state"] == "HOLD"
    assert result["ready_paper_count"] == 0
    assert result["reason"] == "MISSING_TICKER:PESOS2"


def test_green_gate_cannot_claim_partial_universe(tmp_path):
    s = store(tmp_path)
    try:
        persist_gate(s, gate(tickers=["PESOS1"]), evaluated_at=NOW)
    except ValueError as exc:
        assert "exact expected caucion universe" in str(exc)
    else:
        raise AssertionError("partial universe unexpectedly persisted green")


def test_real_order_capability_can_never_be_true(tmp_path):
    s = store(tmp_path)
    try:
        persist_gate(s, gate(real_order_capability=True), evaluated_at=NOW)
    except ValueError as exc:
        assert "real order capability must be zero" in str(exc)
    else:
        raise AssertionError("real order capability unexpectedly accepted")


def test_reader_cannot_relax_gate_ttl(tmp_path):
    s = store(tmp_path)
    persist_gate(s, gate(), evaluated_at=NOW)
    try:
        effective_state(s, now=NOW, max_age_seconds=301)
    except ValueError as exc:
        assert "cannot relax 300s" in str(exc)
    else:
        raise AssertionError("readiness TTL unexpectedly relaxed")

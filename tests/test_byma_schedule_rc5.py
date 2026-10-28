from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import co_market_sessions_hf6 as sessions
import rc4_contract_schedule as ce_schedule
from bq_exit_policy import PaperSessionPolicy

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def at(value):
    return datetime.fromisoformat(value).astimezone(TZ)


def test_byma_rc5_source_and_scope_are_explicit():
    status = sessions.byma_schedule_status()
    assert status["communication"] == "19024"
    assert status["source_date"] == "2026-09-22"
    assert status["paper_spot_regular_open"] == "10:30"
    assert status["paper_spot_regular_close"] == "17:00"
    assert status["interval"] == "[10:30,17:00)"
    assert status["extended_sessions_enabled"] is False
    assert status["unverified_special_sessions"] == "FAIL_CLOSED"
    assert status["public_attachment_status"].startswith("MISLINKED_")


def test_byma_regular_boundaries_are_half_open_and_business_day_only():
    assert sessions.byma_paper_spot_phase(at("2026-09-07T10:14:59-03:00")) == "CLOSED"
    assert sessions.byma_paper_spot_phase(at("2026-09-07T10:15:00-03:00")) == "PREOPEN"
    assert sessions.byma_paper_spot_phase(at("2026-09-07T10:29:59-03:00")) == "PREOPEN"
    assert sessions.byma_paper_spot_phase(at("2026-09-07T10:30:00-03:00")) == "OPEN"
    assert sessions.byma_paper_spot_phase(at("2026-09-07T16:59:59-03:00")) == "OPEN"
    assert sessions.byma_paper_spot_phase(at("2026-09-07T17:00:00-03:00")) == "CLOSED"
    assert sessions.byma_paper_spot_phase(at("2026-09-05T12:00:00-03:00")) == "CLOSED"


def test_unknown_byma_family_does_not_inherit_spot_clock():
    assert sessions.session_for("BYMA", "OPCIONES") is None
    assert sessions.session_for("BYMA", "FUTUROS") is None
    assert sessions.session_for("BYMA", "CAUCIONES") is None


def test_exit_policy_uses_same_regular_session():
    policy = PaperSessionPolicy()
    assert policy.open_time == sessions.BYMA_PAPER_SPOT_OPEN
    assert policy.close_time == sessions.BYMA_PAPER_SPOT_CLOSE
    assert policy.no_entry_minutes == 30
    assert policy.exit_minutes == 10


def test_contract_evidence_dynamic_window_starts_with_regular_session():
    assert ce_schedule.DYNAMIC_START == sessions.BYMA_PAPER_SPOT_OPEN
    assert ce_schedule.DYNAMIC_END == sessions.BYMA_PAPER_SPOT_CLOSE
    assert not ce_schedule.window_allows(
        "CONTRACT_EVIDENCE_DYNAMIC", at("2026-09-07T10:29:59-03:00"))
    assert ce_schedule.window_allows(
        "CONTRACT_EVIDENCE_DYNAMIC", at("2026-09-07T10:30:00-03:00"))
    assert not ce_schedule.window_allows(
        "CONTRACT_EVIDENCE_DYNAMIC", at("2026-09-07T17:00:00-03:00"))
    assert not ce_schedule.window_allows(
        "CONTRACT_EVIDENCE_DYNAMIC", at("2026-09-05T12:00:00-03:00"))


def test_runtime_launch_no_longer_hardcodes_1100():
    mode = Path("porota_mode_manager.py").read_text(encoding="utf-8")
    observer = Path("bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert '"MARKET_OPEN_HOUR=11"' not in mode
    assert '"MARKET_OPEN_MINUTE=0"' not in mode
    assert 'os.getenv("MARKET_OPEN_HOUR", "11")' not in observer
    assert 'os.getenv("MARKET_OPEN_MINUTE", "0")' not in observer
    assert '"MARKET_OPEN_HOUR=10"' in mode
    assert '"MARKET_OPEN_MINUTE=30"' in mode
    assert 'os.getenv("MARKET_OPEN_HOUR", "10")' in observer
    assert 'os.getenv("MARKET_OPEN_MINUTE", "30")' in observer


def test_intraday_scanner_uses_central_session_source():
    source = Path("cf_intraday_scalping.py").read_text(encoding="utf-8")
    assert "byma_paper_spot_open" in source
    assert "wall_time(10, 30)" not in source

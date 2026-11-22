from datetime import datetime, time
from pathlib import Path
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

import co_market_sessions_hf6 as sessions
import porota_mode_manager as mode_manager
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


def test_a3_reference_resolves_explicit_products_to_their_published_windows():
    for market, product, clocks in (
        ("A3", "DLR", (time(9, 30), time(10), time(15))),
        ("A3", "MONEDAS", (time(9, 30), time(10), time(15))),
        ("A3", "AGRO_ESTANDAR", (time(10, 30), time(11), time(17))),
        ("A3", "AGRO_MINI", (time(10, 30), time(11), time(17))),
        ("A3", "MAÍZ_MINI", (time(10, 30), time(11), time(17))),
        ("A3", "AGRO_CHICAGO", (time(10, 30), time(11), time(16, 20))),
        ("A3", "SOJA_CHICAGO", (time(10, 30), time(11), time(16, 20))),
        ("A3", "RFX20", (time(10), time(10, 30), time(18))),
        ("MATBA_ROFEX", "ACCIONES", (time(10), time(10, 30), time(18))),
        ("ROFEX", "WTI", (time(10), time(10, 30), time(17))),
        ("A3", "CAUC", (time(10), time(10, 30), time(17))),
        ("A3", "TAMAR", (time(10), time(10, 30), time(17))),
    ):
        session = sessions.session_for(market, product)
        assert session is not None, (market, product)
        assert session.source == "https://a3mercados.com.ar/info-de-mercado/datos-de-mercado"
        assert (session.preopen_start, session.regular_open, session.regular_close) == clocks


def test_a3_reference_requires_a_product_when_segment_hours_differ():
    for label in (
        "AGRO", "FUTUROS_OPCIONES_AGRO", "OTROS", "FUTUROS_OPCIONES_OTROS",
        "FUTUROS", "RFX20/OCT26", "DLR/OCT26",
    ):
        assert sessions.session_for("A3", label) is None, label


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


def test_runtime_launch_delegates_schedule_to_trusted_host_profile():
    # The observed PAPER spot session is 10:30, not the obsolete 11:00.
    # The manager no longer constructs `docker run -e MARKET_OPEN_*`; a typed
    # host helper validates the pinned observer profile and its 10:30 settings
    # in A1092 tests. This test verifies the manager boundary, not source text.
    assert sessions.byma_schedule_status()["paper_spot_regular_open"] == "10:30"
    attempt = "d" * 32

    def trusted_helper(*args, attempt=None):
        if args == ("manager", "begin"):
            return "RC6_CUSTODY=GENESIS\nRC6_CUSTODY_ATTEMPT=" + "d" * 32
        assert attempt == "d" * 32
        return "RC6_CUSTODY=OK"

    with patch.object(mode_manager, "_helper_call", side_effect=trusted_helper) as helper, \
         patch.object(mode_manager, "run") as raw_docker:
        session = mode_manager._begin_custody()
        session.birth("observer")

    assert helper.call_args_list == [
        call("manager", "begin"),
        call("manager", "birth", "observer", attempt=attempt),
        call("manager", "verify-newborn", "observer", attempt=attempt),
    ]
    raw_docker.assert_not_called()


def test_intraday_scanner_uses_central_session_source():
    source = Path("cf_intraday_scalping.py").read_text(encoding="utf-8")
    assert "byma_paper_spot_open" in source
    assert "wall_time(10, 30)" not in source

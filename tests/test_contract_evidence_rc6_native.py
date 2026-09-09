from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import rc6_contract_schedule as schedule

AR = ZoneInfo("America/Argentina/Buenos_Aires")
ROOT = Path(__file__).resolve().parents[1]


def at(y,m,d,h,minute=0):
    return datetime(y,m,d,h,minute,tzinfo=AR)


def test_weekend_never_starts_authenticated_browser():
    sunday = at(2026,9,6,12)
    assert all(not schedule.window_allows(job, sunday) for job in schedule.JOB_TO_CADENCE)


def test_dynamic_cadences_only_inside_verified_market_window():
    monday_before = at(2026,9,7,10,29)
    monday_open = at(2026,9,7,10,30)
    monday_close = at(2026,9,7,17,0)
    for job in ("CONTRACT_EVIDENCE_DYNAMIC","CONTRACT_EVIDENCE_CAUCIONES",
                "CONTRACT_EVIDENCE_AUCTIONS","CONTRACT_EVIDENCE_DERIVATIVES"):
        assert not schedule.window_allows(job, monday_before)
        assert schedule.window_allows(job, monday_open)
        assert not schedule.window_allows(job, monday_close)


def test_static_is_postclose_and_full_browser_is_friday_postclose_only():
    monday = at(2026,9,7,17,5)
    friday = at(2026,9,11,17,5)
    assert schedule.window_allows("CONTRACT_EVIDENCE_STATIC", monday)
    assert not schedule.window_allows("CONTRACT_EVIDENCE_FULL_BROWSER", monday)
    assert schedule.window_allows("CONTRACT_EVIDENCE_FULL_BROWSER", friday)


def test_ttls_come_from_canonical_policy():
    ref = at(2026,9,7,11,0)
    assert schedule.due((ref-timedelta(minutes=16)).isoformat(), "CONTRACT_EVIDENCE_DYNAMIC", ref)
    assert not schedule.due((ref-timedelta(minutes=14)).isoformat(), "CONTRACT_EVIDENCE_DYNAMIC", ref)
    assert schedule.due((ref-timedelta(minutes=6)).isoformat(), "CONTRACT_EVIDENCE_CAUCIONES", ref)
    assert not schedule.due((ref-timedelta(minutes=4)).isoformat(), "CONTRACT_EVIDENCE_CAUCIONES", ref)


def test_native_active_files_have_no_legacy_rc4_dependency_and_no_order_methods():
    files = [
        "rc6_contract_schedule.py","rc6_contract_due_job.py","rc6_ppi_contract_normalizer.py",
        "rc6_trusted_browser_contract_collector.py","rc6_contract_capture_importer.py",
        "scripts/porota_contract_evidence_rc6_runtime.sh",
        "systemd/porota-contract-evidence-rc6.service","systemd/porota-contract-evidence-rc6.timer",
    ]
    text = "\n".join((ROOT / f).read_text(encoding="utf-8") for f in files)
    assert "rc4" not in text.lower()
    for token in ("send_order(", "new_order(", "replace_order(", "cancel_order("):
        assert token not in text


def test_browser_is_get_head_options_only_and_cannot_take_credentials():
    text = (ROOT / "rc6_trusted_browser_contract_collector.py").read_text(encoding="utf-8")
    assert 'SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}' in text
    assert "--profile" in text and "--jobs" in text and "--output" in text
    assert "--username" not in text and "--password" not in text and "--otp" not in text
    assert 'route.abort()' in text


def test_timer_is_monotonic_and_not_persistent():
    text = (ROOT / "systemd/porota-contract-evidence-rc6.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=5min" in text
    assert "Persistent=" not in text

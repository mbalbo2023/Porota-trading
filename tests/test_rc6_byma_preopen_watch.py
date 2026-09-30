import json
from datetime import date
from pathlib import Path

import rc6_preopen as preopen


TODAY=date(2026,9,25)


def write(tmp_path, *, state="NO_CHANGE", changed=None, observed_at="2026-09-25T11:30:00+00:00", errors=None):
    path=tmp_path/"byma_morning_watch_latest.json"
    path.write_text(json.dumps({
        "state":state,
        "changed_components":changed or [],
        "observed_at":observed_at,
        "errors":errors or [],
    }),encoding="utf-8")
    return path


def test_today_no_change_is_green(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",write(tmp_path))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="GREEN"
    assert row["reason"]=="NO_CHANGE"


def test_hours_or_calendar_change_blocks_preopen_until_review(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="CHANGED_REVIEW_REQUIRED",
                              changed=["HOURS","COMMUNICATIONS"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="RED"
    assert row["critical_components"]==["HOURS"]


def test_non_clock_official_change_is_visible_without_global_block(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="CHANGED_REVIEW_REQUIRED",
                              changed=["OPTIONS"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="AMBER"
    assert row["reason"]=="BYMA_CHANGE_REVIEW_REQUIRED"


def test_missing_or_stale_watch_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",tmp_path/"missing.json")
    assert preopen.byma_morning_watch(TODAY)["state"]=="RED"
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,observed_at="2026-09-24T11:30:00+00:00"))
    assert preopen.byma_morning_watch(TODAY)["reason"]=="BYMA_WATCH_NOT_TODAY"


def test_dry_equivalent_accepts_only_recent_safe_prior_watch(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen, "BYMA_MORNING_WATCH",
                        write(tmp_path, observed_at="2026-09-24T11:30:00+00:00"))
    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            return preopen.datetime.fromisoformat("2026-09-25T00:10:00-03:00")
        fromisoformat = staticmethod(preopen.datetime.fromisoformat)
    monkeypatch.setattr(preopen, "datetime", FixedDateTime)
    row = preopen.byma_morning_watch(TODAY, allow_latest_safe=True)
    assert row["state"] == "AMBER"
    assert row["reason"] == "DRY_VALIDATION_LATEST_SAFE"


def test_dry_equivalent_rejects_old_or_changed_prior_watch(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen, "BYMA_MORNING_WATCH",
                        write(tmp_path, observed_at="2026-09-20T11:30:00+00:00"))
    assert preopen.byma_morning_watch(TODAY, allow_latest_safe=True)["state"] == "RED"


def test_runtime_disk_reserve_is_blocking_below_two_gib_and_amber_below_target():
    assert preopen.BLOCKING_MIN_FREE_BYTES == 2 * 1024**3
    assert preopen.TARGET_FREE_BYTES == 8 * 1024**3


def test_iol_source_unavailable_is_amber_only_with_complete_fail_safe(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "refreshed_at": "2026-09-25T02:00:00+00:00",
        "cache_state": "SOURCE_UNAVAILABLE",
        "records": [], "fci": [], "cauciones": {"ARS": []},
        "section_states": {"caucion:ARS": "SOURCE_UNAVAILABLE_NO_LKG"},
        "fallback_order": preopen.IOL_FALLBACK_ORDER,
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
    }), encoding="utf-8")
    monkeypatch.setattr(preopen, "IOL_FAMILY_REFERENCE", path)
    row = preopen.iol_reference_state(TODAY)
    assert row["state"] == "AMBER"
    assert row["reason"] == "CONTINUE_WITH_PPI_BYMA_ALTERNATIVES"
    assert row["never_zero_fill"] is True


def test_iol_source_unavailable_without_exact_fallback_is_red(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "refreshed_at": "2026-09-25T02:00:00+00:00",
        "cache_state": "SOURCE_UNAVAILABLE", "section_states": {"global": "SOURCE_UNAVAILABLE"},
        "fallback_order": ["IOL_LIVE_BOUNDED_RETRY"],
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
    }), encoding="utf-8")
    monkeypatch.setattr(preopen, "IOL_FAMILY_REFERENCE", path)
    assert preopen.iol_reference_state(TODAY)["state"] == "RED"


def test_degraded_watch_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="DEGRADED",errors=["CALENDAR:TimeoutError"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="RED"
    assert row["reason"]=="BYMA_WATCH_DEGRADED"


def test_morning_watch_timer_is_required_by_preopen():
    assert "porota-byma-morning-watch-rc6.timer" in preopen.REQUIRED_TIMERS

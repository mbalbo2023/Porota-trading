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


def test_degraded_watch_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="DEGRADED",errors=["CALENDAR:TimeoutError"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="RED"
    assert row["reason"]=="BYMA_WATCH_DEGRADED"


def test_morning_watch_timer_is_required_by_preopen():
    assert "porota-byma-morning-watch-rc6.timer" in preopen.REQUIRED_TIMERS

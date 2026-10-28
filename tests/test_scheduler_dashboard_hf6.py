from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import de_scheduler_catalog_hf6 as scheduler


def test_scheduler_catalog_invariants():
    scheduler.assert_scheduler_invariants()
    keys=[job.key for job in scheduler.INTERNAL_JOBS]
    assert len(keys) == len(set(keys))
    assert "porota-preopen.timer" in scheduler.SYSTEMD_DESCRIPTIONS
    assert "porota-scheduler-export-rc6.timer" in scheduler.SYSTEMD_DESCRIPTIONS


def test_internal_job_next_due_is_derived_from_persisted_last_run():
    rows=[{
        "job_key":"SRE_SNAPSHOT",
        "last_run_at":"2026-09-02T18:00:00-03:00",
        "last_success_at":"2026-09-02T18:00:00-03:00",
        "state":"VERDE",
        "detail":"ok",
    }]
    result={row["key"]:row for row in scheduler.internal_rows(rows)}
    assert result["SRE_SNAPSHOT"]["next_run_at"] == "2026-09-02T18:05:00-03:00"
    assert result["SRE_SNAPSHOT"]["state"] == "VERDE"


def test_unknown_persisted_job_remains_visible():
    rows=[{
        "job_key":"TELEGRAM_CLOSE_2026-09-02",
        "last_run_at":"2026-09-02T17:05:00-03:00",
        "last_success_at":None,
        "state":"EN_COLA",
        "detail":"pendiente",
    }]
    result=scheduler.internal_rows(rows)
    discovered=[x for x in result if x["key"]=="TELEGRAM_CLOSE_2026-09-02"]
    assert len(discovered)==1
    assert discovered[0]["source"] == "INTERNAL_DISCOVERED"
    assert discovered[0]["next_run_at"] is None


def test_systemd_snapshot_is_read_only_json(tmp_path: Path):
    payload={
        "state":"OK",
        "recorded_at":"2026-09-02T21:00:00+00:00",
        "timers":[{"unit":"porota-log-export-hf6.timer","next_elapse":"x"}],
    }
    path=tmp_path/"systemd_timers.json"
    path.write_text(json.dumps(payload),encoding="utf-8")
    loaded=scheduler.load_systemd_snapshot(path)
    assert loaded == payload
    assert scheduler.describe_systemd_timer("porota-log-export-hf6.timer")


def test_missing_systemd_snapshot_is_explicit(tmp_path: Path):
    loaded=scheduler.load_systemd_snapshot(tmp_path/"missing.json")
    assert loaded["state"] == "MISSING"
    assert loaded["timers"] == []

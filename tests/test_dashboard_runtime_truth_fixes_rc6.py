from __future__ import annotations

import importlib
from datetime import datetime, timedelta


def test_telegram_prefers_runtime_worker(monkeypatch):
    import et_dashboard_runtime_truth_fixes_rc6 as truth
    truth = importlib.reload(truth)
    now = datetime.now(truth.bg.TZ)
    monkeypatch.setattr(truth.bg, "_table", lambda name: name in {"paper_notification_worker","paper_notification_outbox","operational_jobs"})
    def rows(sql, params=()):
        if "paper_notification_worker" in sql:
            return [{"state":"RUNNING","heartbeat_at":(now-timedelta(seconds=20)).isoformat(),"last_sent_at":(now-timedelta(hours=2)).isoformat(),"real_orders_sent":0}]
        if "paper_notification_outbox" in sql:
            return [{"state":"SENT","total":83,"latest":(now-timedelta(hours=2)).isoformat()}]
        if "operational_jobs" in sql:
            return [{"job_key":"TELEGRAM_STARTUP_ACK","last_run_at":now.isoformat(),"last_success_at":now.isoformat(),"state":"VERDE","detail":"ok"}]
        return []
    monkeypatch.setattr(truth.bg, "_rows", rows)
    row = truth._telegram_runtime_evidence({"key":"TELEGRAM","state":"VERDE","detail":"legacy"})
    assert row["state"] == "VERDE"
    assert row["paper_blocking"] is False
    assert "Worker RUNNING" in row["detail"]
    assert "SENT=83" in row["detail"]


def test_sre_yellow_slow_query_is_explained_not_repainted(monkeypatch):
    import et_dashboard_runtime_truth_fixes_rc6 as truth
    truth = importlib.reload(truth)
    monkeypatch.setattr(truth.bg, "_table", lambda name: name == "sre_snapshots")
    monkeypatch.setattr(truth.bg, "_rows", lambda sql, params=(): [{
        "state":"AMARILLO","measured_at":"2026-09-06T21:00:00-03:00",
        "db_integrity":"ok","db_query_ms":17000.0,
        "disk_total_bytes":1000,"disk_free_bytes":434,
    }])
    row = truth._sre_runtime_evidence({"key":"SRE_SNAPSHOT","state":"AMARILLO","detail":"old","paper_blocking":True})
    assert row["state"] == "AMARILLO"
    assert row["paper_blocking"] is False
    assert "17000.0 ms" in row["detail"]
    assert "no por corrupción DB ni falta de disco" in row["detail"]


def test_sre_integrity_failure_remains_blocking(monkeypatch):
    import et_dashboard_runtime_truth_fixes_rc6 as truth
    truth = importlib.reload(truth)
    monkeypatch.setattr(truth.bg, "_table", lambda name: name == "sre_snapshots")
    monkeypatch.setattr(truth.bg, "_rows", lambda sql, params=(): [{
        "state":"AMARILLO","measured_at":"2026-09-06T21:00:00-03:00",
        "db_integrity":"not ok","db_query_ms":10.0,
        "disk_total_bytes":1000,"disk_free_bytes":434,
    }])
    row = truth._sre_runtime_evidence({"key":"SRE_SNAPSHOT","state":"AMARILLO","detail":"old","paper_blocking":False})
    assert row["paper_blocking"] is True

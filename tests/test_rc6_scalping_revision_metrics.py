import json
import sqlite3

import fk_scalping_revision_metrics_rc6 as metrics


def _event(symbol, action, age, received, mutable=120):
    return json.dumps({
        "symbol": symbol,
        "asset_class": "ACCIONES",
        "market": "BYMA",
        "currency": "ARS",
        "settlement": "A-24HS",
        "event_at": received,
        "received_at": received,
        "age_seconds": age,
        "action": action,
        "price_changed": True,
        "volume_changed": False,
        "mutable_seconds": mutable,
        "source": "PPI_MARKETDATA_INTRADAY",
    })


def test_summary_reports_distribution_without_changing_threshold():
    rows = [
        {"detail": _event("GGAL", "REFRESH_MUTABLE", 30, "2026-09-11T14:00:00+00:00")},
        {"detail": _event("GGAL", "REFRESH_MUTABLE", 90, "2026-09-11T14:01:00+00:00")},
        {"detail": _event("YPFD", "REJECT_CLOSED_REVISION", 180, "2026-09-11T14:02:00+00:00")},
        {"detail": _event("PAMP", "REJECT_CLOSED_REVISION", 660, "2026-09-11T14:03:00+00:00")},
    ]
    summary = metrics.summarize_revision_rows(rows, threshold_seconds=120)
    assert summary["state"] == "EVIDENCE_AVAILABLE"
    assert summary["total_valid"] == 4
    assert summary["refresh_mutable"] == 2
    assert summary["reject_closed_revision"] == 2
    assert summary["threshold_seconds"] == 120
    assert summary["age_p50_seconds"] == 90
    assert summary["age_p95_seconds"] == 660
    assert summary["age_max_seconds"] == 660
    assert summary["latest_received_at"] == "2026-09-11T14:03:00+00:00"
    assert summary["by_symbol"]["GGAL"] == 2
    assert "OBSERVABILITY_ONLY" in summary["interpretation"]


def test_malformed_evidence_is_counted_not_coerced_to_zero():
    summary = metrics.summarize_revision_rows([
        {"detail": "not-json"},
        {"detail": json.dumps({"action": "REFRESH_MUTABLE"})},
    ])
    assert summary["state"] == "NO_EVIDENCE"
    assert summary["total_valid"] == 0
    assert summary["invalid_events"] == 2
    assert summary["age_p50_seconds"] is None


def test_reader_executes_select_only_and_filters_event_type():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE paper_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_at TEXT NOT NULL,
        source TEXT NOT NULL,
        event_type TEXT NOT NULL,
        paper_id TEXT,
        detail TEXT NOT NULL)""")
    conn.execute(
        "INSERT INTO paper_events(event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)",
        ("2026-09-11T14:00:00+00:00", "PRODUCTION_PAPER", metrics.EVENT_TYPE, None,
         _event("GGAL", "REFRESH_MUTABLE", 75, "2026-09-11T14:00:00+00:00")),
    )
    conn.execute(
        "INSERT INTO paper_events(event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)",
        ("2026-09-11T14:01:00+00:00", "PRODUCTION_PAPER", "OTHER_EVENT", None, "{}"),
    )
    before = conn.total_changes
    summary = metrics.read_revision_summary(conn)
    after = conn.total_changes
    assert before == after
    assert summary["total_valid"] == 1
    assert summary["by_symbol"] == {"GGAL": 1}

from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import rc6_action4_audit as audit


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _database(path):
    connection = sqlite3.connect(path)
    connection.execute("""
        CREATE TABLE paper_positions (
            paper_id TEXT, status TEXT, opened_at TEXT, closed_at TEXT,
            close_reason TEXT
        )
    """)
    connection.execute("CREATE TABLE trade_gate_evaluations (evaluated_at TEXT, final_result TEXT)")
    connection.execute(
        "INSERT INTO paper_positions VALUES (?,?,?,?,?)",
        ("PAPER-1", "CLOSED", "2026-09-16T11:00:00-03:00",
         "2026-09-16T12:00:00-03:00", "TAKE_PROFIT"),
    )
    connection.execute(
        "INSERT INTO trade_gate_evaluations VALUES (?,?)",
        ("2026-09-16T11:00:00-03:00", "OPENED_SIMULATED"),
    )
    connection.commit()
    connection.close()


def test_build_is_read_only_and_summarizes_daily_data(tmp_path):
    database = tmp_path / "paper.db"
    _database(database)
    now = datetime(2026, 9, 16, 13, 0, tzinfo=TZ)

    payload = audit.build(db_path=database, now=now)

    assert payload["status"] == "PAPER_READ_ONLY"
    assert payload["separation"]["executed_closed"] == 1
    assert payload["separation"]["exit_reasons"]["TAKE_PROFIT"] == 1
    assert payload["separation"]["buy"] == 1
    assert payload["safety"]["broker_routes_called"] is False
    assert payload["dashboard_daily_report"]["scope_evidence"]["positions"] == "LEGACY_SCHEMA_UNFILTERED"


def test_publish_is_atomic_json_artifact(tmp_path):
    database = tmp_path / "paper.db"
    _database(database)
    root = tmp_path / "artifacts"

    payload = audit.publish(
        db_path=database, root=root,
        now=datetime(2026, 9, 16, 13, 0, tzinfo=TZ),
    )

    target = root / "reports" / audit.FILENAME
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_build_includes_bounded_event_and_learning_lessons(tmp_path):
    database = tmp_path / "paper.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE paper_events (event_at TEXT, event_type TEXT)")
        connection.execute(
            "INSERT INTO paper_events VALUES (?, ?)",
            ("2026-09-16T12:00:00-03:00", "TAKE_PROFIT_SHADOW"),
        )
        connection.execute(
            "CREATE TABLE paper_learning_samples (label_timestamp TEXT, feature_timestamp TEXT, outcome TEXT)"
        )
        connection.execute(
            "INSERT INTO paper_learning_samples VALUES (?, ?, ?)",
            ("2026-09-16T12:30:00-03:00", "2026-09-16T11:00:00-03:00", "WIN"),
        )
        connection.commit()

    payload = audit.build(
        db_path=database,
        now=datetime(2026, 9, 16, 13, 0, tzinfo=TZ),
    )

    report = payload["dashboard_daily_report"]
    assert report["event_counts"]["TAKE_PROFIT_SHADOW"] == 1
    assert report["learning_outcomes"]["WIN"] == 1
    assert any("Etiquetas de aprendizaje" in lesson for lesson in report["lessons"])

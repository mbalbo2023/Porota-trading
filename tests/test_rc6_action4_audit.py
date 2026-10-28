from datetime import datetime, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

import rc6_action4_audit as audit


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE observer_state (id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER, session_state TEXT)")
    connection.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0,'MARKET_OPEN')")
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
    assert payload["safety"]["observer_mode"] == "PRODUCTION_PAPER"
    assert payload["safety"]["real_orders_sent"] == 0
    assert payload["dashboard_daily_report"]["closure_state"] == "NOT_POSTCLOSE"
    assert audit.published_audit_is_safe(payload)
    assert not audit.published_audit_is_safe({**payload, "safety": {**payload["safety"],
                                                                  "real_orders_sent": False}})
    assert payload["dashboard_daily_report"]["event_counts"] is None
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


def test_daily_lessons_are_labeled_postclose_only_with_observed_closed_session(tmp_path):
    database = tmp_path / "paper.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE observer_state SET session_state='MARKET_CLOSED' WHERE id=1")
    result = audit.build(db_path=database, now=datetime(2026, 9, 16, 18, tzinfo=TZ))
    assert result["safety"]["observer_session_state"] == "MARKET_CLOSED"
    assert result["dashboard_daily_report"]["closure_state"] == "POSTCLOSE_VERIFIED"


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


def test_build_reports_all_paper_position_families_when_schema_has_asset_class(tmp_path):
    database = tmp_path / "scoped-paper.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE observer_state (id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER)")
        connection.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0)")
        connection.execute("""
            CREATE TABLE paper_positions (
                paper_id TEXT, status TEXT, opened_at TEXT, closed_at TEXT,
                close_reason TEXT, asset_class TEXT
            )
        """)
        connection.executemany(
            "INSERT INTO paper_positions VALUES (?,?,?,?,?,?)",
            [
                ("PAPER-A", "OPEN", "2026-09-16T11:00:00-03:00", None, None, "ACCIONES"),
                ("PAPER-B", "CLOSED", "2026-09-16T11:00:00-03:00",
                 "2026-09-16T12:00:00-03:00", "TAKE_PROFIT", "BONOS"),
            ],
        )
        connection.execute("CREATE TABLE trade_gate_evaluations (evaluated_at TEXT, final_result TEXT)")
        connection.commit()

    payload = audit.build(
        db_path=database,
        now=datetime(2026, 9, 16, 13, 0, tzinfo=TZ),
    )

    assert payload["separation"]["opened_simulated"] == 2
    assert payload["separation"]["executed_closed"] == 1
    report = payload["dashboard_daily_report"]
    assert report["scope"] == "ALL_CONTRACT_FAMILIES_PAPER"
    assert report["scope_evidence"]["positions"] == "ALL_PAPER_POSITION_FAMILIES"
    assert report["scope_evidence"]["position_families"] == {"ACCIONES": 1, "BONOS": 1}
    assert "no se atribuyen retroactivamente" in report["scope_evidence"]["interpretation"]


def test_action4_refuses_to_publish_without_verified_paper_state(tmp_path):
    database = tmp_path / "paper.db"
    _database(database)
    root = tmp_path / "artifacts"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
    try:
        audit.publish(db_path=database, root=root, now=datetime(2026, 9, 16, 13, tzinfo=TZ))
    except ValueError as error:
        assert str(error) == "ACTION4_PAPER_SAFETY_NOT_VERIFIED"
    else:
        assert False, "An unsafe receipt must never be published"
    assert not (root / "reports" / audit.FILENAME).exists()

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE observer_state SET real_orders_sent=0, mode='REAL' WHERE id=1")
    try:
        audit.build(db_path=database, now=datetime(2026, 9, 16, 13, tzinfo=TZ))
    except ValueError as error:
        assert str(error) == "ACTION4_PAPER_SAFETY_NOT_VERIFIED"
    else:
        assert False, "A non-PAPER observer cannot certify Action 4"


def test_action4_does_not_convert_required_source_error_to_zero(tmp_path):
    database = tmp_path / "paper.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE trade_gate_evaluations")
    try:
        audit.build(db_path=database, now=datetime(2026, 9, 16, 13, tzinfo=TZ))
    except sqlite3.OperationalError:
        pass
    else:
        assert False, "Missing gate evidence must fail the audit"


def test_weekly_action4_rejects_old_window_and_unverified_paper_scope():
    now = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)
    end = now.astimezone(TZ) - timedelta(minutes=1)
    payload = {
        "scope": "RC6", "status": "SUCCESS", "source": "/api/observer/state",
        "database_access_from_action": "NO", "mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0, "decision_snapshot_scope": "LATEST_100_ONLY; INCOMPLETE_FOR_WEEKLY_TOTAL",
        "operations": [], "separation": {"executed_closed": 0},
        "origin": {"run_id": "39998529579", "commit": "a" * 40},
        "window": {"from_local": (end - timedelta(days=7)).isoformat(), "to_local": end.isoformat()},
        "generated_at_utc": now.isoformat(),
    }
    assert audit.validate_weekly_payload(payload, now=now) is payload
    assert audit.published_audit_is_safe(payload)
    for unsafe in ({"real_orders_sent": False}, {"real_orders_sent": 1}, {"mode": "PRODUCTION_REAL"}):
        try:
            audit.validate_weekly_payload({**payload, **unsafe}, now=now)
        except ValueError as error:
            assert str(error) == "ACTION4_WEEKLY_PAPER_SCOPE_UNVERIFIED"
        else:
            assert False, "Non-PAPER scope must be rejected"
    old = dict(payload, window={"from_local": "2026-09-08T00:00:00-03:00",
                                "to_local": "2026-09-15T00:00:00-03:00"})
    assert not audit.published_audit_is_safe(old)
    try:
        audit.validate_weekly_payload(old, now=now)
    except ValueError as error:
        assert str(error) == "ACTION4_WEEKLY_WINDOW_UNVERIFIED"
    else:
        assert False, "September data must not overwrite the current weekly receipt"

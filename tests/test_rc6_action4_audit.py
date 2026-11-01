from datetime import datetime, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

import rc6_action4_audit as audit
from be_paper_engine import PaperStore


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PROVENANCE = {"workflow_commit": "a" * 40, "image_label_commit": "b" * 40,
              "container_id": "c" * 64, "image_id": "sha256:" + "d" * 64}


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
    source = {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "COMPLETE",
              "source": audit.WEEKLY_SOURCE_PATH, "mode": "PRODUCTION_PAPER",
              "real_orders_sent": 0, "generated_at_utc": now.isoformat(),
              "max_rows_per_set": 500,
              "window": {"from_local": (end - timedelta(days=7)).isoformat(),
                         "to_local": end.isoformat()},
              "counts": {"closed": 0, "opened": 0, "decisions": 0},
              "operations": [], "decisions_by_action": {}, "pnl_by_currency": {}}
    payload = audit.weekly_payload_from_source(source, run_id=39998529579,
                                               run_number=1, **PROVENANCE)
    assert audit.validate_weekly_payload(payload, now=now) is payload
    assert audit.published_audit_is_safe(payload)
    for unsafe in ({"real_orders_sent": False}, {"real_orders_sent": 1}, {"mode": "PRODUCTION_REAL"}):
        try:
            audit.validate_weekly_payload({**payload, **unsafe}, now=now)
        except ValueError as error:
            assert str(error) == "ACTION4_WEEKLY_PAPER_SCOPE_UNVERIFIED"
        else:
            assert False, "Non-PAPER scope must be rejected"
    old = audit.weekly_payload_from_source(
        {**source, "window": {"from_local": "2026-09-08T00:00:00-03:00",
                              "to_local": "2026-09-15T00:00:00-03:00"}},
        run_id=39998529579, run_number=1, **PROVENANCE)
    assert not audit.published_audit_is_safe(old)
    try:
        audit.validate_weekly_payload(old, now=now)
    except ValueError as error:
        assert str(error) == "ACTION4_WEEKLY_WINDOW_UNVERIFIED"
    else:
        assert False, "September data must not overwrite the current weekly receipt"
    legacy = dict(payload, source="/api/observer/state",
                  decision_snapshot_scope="LATEST_100_ONLY; INCOMPLETE_FOR_WEEKLY_TOTAL")
    assert not audit.published_audit_is_safe(legacy)


def test_weekly_source_bounds_seven_days_and_preserves_unknown_pnl(tmp_path):
    database = tmp_path / "paper.db"
    store = PaperStore(str(database))
    with store.connect() as c:
        end = datetime(2026, 10, 31, 12, tzinfo=TZ)
        rows = [
            ("P1", "GGAL", "ACCIONES", "ARS", "A-24HS", "CLOSED",
             "2026-10-30T11:00:00-03:00", "2026-10-30T12:00:00-03:00", "EXIT", "0.1"),
            ("P2", "AL30", "BONOS", "ARS", "A-24HS", "CLOSED",
             "2026-10-30T11:00:00-03:00", "2026-10-30T12:01:00-03:00", "EXIT", "0.2"),
            ("P3", "SPY", "CEDEARS", "USD", "A-24HS", "CLOSED",
             "2026-10-30T11:00:00-03:00", "2026-10-30T12:02:00-03:00", "EXIT", None),
            ("OLD", "GGAL", "ACCIONES", "ARS", "A-24HS", "CLOSED",
             "2026-10-22T11:00:00-03:00", "2026-10-22T12:00:00-03:00", "EXIT", "100"),
        ]
        c.executemany("""INSERT INTO paper_positions
            (paper_id,symbol,asset_class,settlement,status,quantity,entry_price,entry_cost,
             stop_price,target_price,opened_at,closed_at,close_reason,net_pnl,features_json,
             source,strategy_version,max_favorable,max_adverse,currency)
             VALUES(?,?,?,?,?,'1','1','0','1','1',?,?,?,?,'{}','PAPER','S1','0','0',?)""",
            [(pid, symbol, asset, settlement, status, opened, closed, reason, net, currency)
             for pid, symbol, asset, currency, settlement, status, opened, closed, reason, net in rows])
        c.executemany("""INSERT INTO paper_decisions
            (id,source,strategy_version,decision_key,decided_at,symbol,action,score,reason,features_json)
            VALUES(?,'PAPER','S1',?,?,'GGAL',?,'0','fixture','{}')""", [
            (1, "decision-1", "2026-10-30T11:00:00-03:00", "HOLD"),
            (2, "decision-2", "2026-10-30T11:10:00-03:00", "BLOCKED_SOURCE"),
            (3, "decision-3", "2026-10-22T11:00:00-03:00", "BUY"),
        ])
    before = database.read_bytes()
    result = audit.build_weekly_source(database, now=end)
    assert database.read_bytes() == before
    assert result["status"] == "COMPLETE"
    assert result["counts"] == {"closed": 3, "opened": 3, "decisions": 2}
    assert result["pnl_by_currency"] == {
        "ARS": {"state": "COMPLETE", "total": "0.3", "unknown_rows": 0},
        "USD": {"state": "NO_VERIFICADO", "total": None, "unknown_rows": 1}}
    assert next(row for row in result["operations"] if row["paper_id"] == "P3")["net_pnl"] is None
    payload = audit.weekly_payload_from_source(result, run_id=123, run_number=1, **PROVENANCE)
    assert payload["separation"]["rejected_blocked"] == 1
    assert payload["separation"]["hold"] == 1
    assert payload["scope_evidence"]["specialized_family_ledgers"] == "NO_VERIFICADO"
    assert audit.validate_weekly_payload(payload, now=end.astimezone(timezone.utc)) == payload


def test_weekly_publication_is_idempotent_and_cannot_move_window_backward():
    now = datetime(2026, 10, 31, 12, tzinfo=TZ)
    source = {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "COMPLETE",
              "source": audit.WEEKLY_SOURCE_PATH, "mode": "PRODUCTION_PAPER",
              "real_orders_sent": 0, "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
              "max_rows_per_set": 500,
              "window": {"from_local": (now - timedelta(days=7)).isoformat(),
                         "to_local": now.isoformat()},
              "counts": {"closed": 0, "opened": 0, "decisions": 0},
              "operations": [], "decisions_by_action": {}, "pnl_by_currency": {}}
    report = audit.weekly_payload_from_source(source, run_id=123, run_number=1,
                                              **PROVENANCE)
    assert audit.weekly_publication_decision({}, report) == "STORE"
    assert audit.weekly_publication_decision(report, report) == "ALREADY_STORED"
    changed = {**report, "source_sha256": "b" * 64}
    assert audit.weekly_publication_decision(report, changed) == "SAME_RUN_EVIDENCE_CHANGED"
    old = {**report, "origin": {**report["origin"], "run_id": "124"},
           "window": {"from_local": (now - timedelta(days=7, seconds=1)).isoformat(),
                      "to_local": (now - timedelta(seconds=1)).isoformat()}}
    assert audit.weekly_publication_decision(report, old) == "STALE_WINDOW"
    next_week = {**report, "origin": {**report["origin"], "run_id": "125"},
                 "window": {"from_local": now.isoformat(),
                            "to_local": (now + timedelta(days=7)).isoformat()}}
    assert audit.weekly_publication_decision(report, next_week) == "STORE"


def test_weekly_source_cap_is_partial_and_never_turns_missing_table_into_zero(tmp_path):
    database = tmp_path / "paper.db"
    store = PaperStore(str(database))
    with store.connect() as c:
        c.executemany("""INSERT INTO paper_decisions
            (id,source,strategy_version,decision_key,decided_at,symbol,action,score,reason,features_json)
            VALUES(?,'PAPER','S1',?,?,'GGAL','HOLD','0','fixture','{}')""", [
            (1, "decision-1", "2026-10-30T11:00:00-03:00"),
            (2, "decision-2", "2026-10-30T11:00:01-03:00")])
    result = audit.build_weekly_source(database, now=datetime(2026, 10, 31, tzinfo=TZ),
                                       max_rows=1)
    assert result["status"] == "PARTIAL"
    assert result["lower_bound_rows"]["decisions"] == 2
    try:
        audit.weekly_payload_from_source(result, run_id=123, run_number=1, **PROVENANCE)
    except ValueError as exc:
        assert str(exc) == "ACTION4_WEEKLY_SOURCE_INCOMPLETE"
    else:
        assert False, "A capped source must never publish"
    with sqlite3.connect(database) as c:
        c.execute("DROP TABLE paper_decisions")
    try:
        audit.build_weekly_source(database, now=datetime(2026, 10, 31, tzinfo=TZ))
    except ValueError as exc:
        assert str(exc) == "ACTION4_WEEKLY_SOURCE_SCHEMA_UNVERIFIED"
    else:
        assert False, "Missing evidence cannot produce zero decisions"


def test_weekly_source_unknown_currency_and_timeout_do_not_publish(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    store = PaperStore(str(database))
    with store.connect() as c:
        c.execute("""INSERT INTO paper_positions
            (paper_id,symbol,asset_class,settlement,status,quantity,entry_price,entry_cost,
             stop_price,target_price,opened_at,closed_at,close_reason,net_pnl,features_json,
             source,strategy_version,max_favorable,max_adverse,currency)
             VALUES('P1','GGAL','ACCIONES','A-24HS','CLOSED','1','1','0','1','1',
                    '2026-10-30T11:00:00-03:00','2026-10-30T12:00:00-03:00',
                    'EXIT','1.50','{}','PAPER','S1','0','0','XYZ')""")
    now = datetime(2026, 10, 31, tzinfo=TZ)
    result = audit.build_weekly_source(database, now=now)
    assert result["status"] == "PARTIAL"
    assert result["reason"] == "CLOSED_POSITION_CURRENCY_UNVERIFIED"
    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_positions SET currency='ARS'")
        c.execute("UPDATE paper_positions SET symbol='GGAL;FAKE'")
    invalid_label = audit.build_weekly_source(database, now=now)
    assert invalid_label["status"] == "PARTIAL"
    assert invalid_label["reason"] == "ACTION4_WEEKLY_LABEL_UNVERIFIED"
    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_positions SET symbol='GGAL'")
        c.executemany("""INSERT INTO paper_decisions
            (source,strategy_version,decision_key,decided_at,symbol,action,score,reason,features_json)
            VALUES('PAPER','S1',?,'2026-10-30T11:00:00-03:00','GGAL','HOLD','0','fixture','{}')""",
            [(f"decision-{n}",) for n in range(200)])
    elapsed = iter([10.0, 11.0] + [11.0] * 100)
    monkeypatch.setattr(audit.time, "monotonic", lambda: next(elapsed))
    timed = audit.build_weekly_source(database, now=now, seconds=.01)
    assert timed["status"] == "PARTIAL"
    assert timed["reason"] == "SQLITE_TIME_BUDGET_EXHAUSTED"

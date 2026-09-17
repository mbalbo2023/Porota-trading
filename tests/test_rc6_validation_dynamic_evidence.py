import sqlite3

import rc6_validation_dynamic as dynamic


def test_validation_does_not_present_bounded_old_data_as_current_evidence(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    with sqlite3.connect(database) as c:
        c.executescript("""
            CREATE TABLE observer_state (
              id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT,
              session_state TEXT, ppi_auth TEXT, heartbeat_at TEXT,
              last_market_data_at TEXT, real_orders_sent INTEGER
            );
            INSERT INTO observer_state VALUES
              (1, 'PRODUCTION_PAPER', 'RUNNING', 'MARKET_CLOSED', 'OK',
               '2026-09-16T12:00:00+00:00', '2026-09-16T12:00:00+00:00', 0);
            CREATE TABLE production_history (downloaded_at TEXT);
            INSERT INTO production_history VALUES ('2026-09-16T10:00:00+00:00');
            CREATE TABLE candle_versions (known_at TEXT);
            INSERT INTO candle_versions VALUES ('2026-09-16T10:05:00+00:00');
            CREATE TABLE paper_positions (paper_id TEXT);
            INSERT INTO paper_positions VALUES ('PAPER-1');
            CREATE TABLE trade_gate_evaluations (id INTEGER);
            INSERT INTO trade_gate_evaluations VALUES (1);
        """)
    monkeypatch.setenv("POROTA_PAPER_DB", str(database))
    monkeypatch.setenv("POROTA_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("EXECUTION", "SIMULATED")
    monkeypatch.setenv("REAL_ORDER_ROUTES", "BLOCKED")

    rows = dynamic.evaluate()

    for code in ("M2", "M3", "M5"):
        assert rows[code]["state"] == "GRAY"
        assert rows[code]["claim_status"] == "PENDING"
    assert rows["M3"]["observed_evidence"] == "Sin evidencia verificable actual"


def test_paper_green_needs_runtime_row_with_zero_real_orders(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    with sqlite3.connect(database) as c:
        c.execute("CREATE TABLE observer_state (id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT, session_state TEXT, ppi_auth TEXT, heartbeat_at TEXT, last_market_data_at TEXT, real_orders_sent INTEGER)")
    monkeypatch.setenv("POROTA_PAPER_DB", str(database))
    monkeypatch.setenv("POROTA_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("EXECUTION", "SIMULATED")
    monkeypatch.setenv("REAL_ORDER_ROUTES", "BLOCKED")

    rows = dynamic.evaluate()

    assert rows["M1"]["state"] == "GRAY"
    assert rows["M1"]["claim_status"] == "PENDING"
    assert rows["M1"]["observed_evidence"] == "Sin evidencia verificable actual"


def test_daily_db_probe_avoids_quick_check_but_explicit_deep_check_keeps_it(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    with sqlite3.connect(database) as c:
        c.execute("CREATE TABLE observer_state (id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO observer_state VALUES (1)")
    monkeypatch.setenv("POROTA_PAPER_DB", str(database))

    assert dynamic._db_ok(deep=False) == (True, "OBSERVER_ROW_READ_ONLY")
    assert dynamic._db_ok(deep=True) == (True, "ok")

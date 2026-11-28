from contextlib import closing
import hashlib
import json
import sqlite3

from scripts import rc6_iol_wheel_phase_readonly as phase


def test_wheel_phase_joins_exact_keys_and_exports_only_aggregates(tmp_path):
    database = tmp_path / "paper.db"
    at = "2026-11-27T13:31:00+00:00"
    closed = "2026-11-27T13:33:00+00:00"
    ready = ("PAMP", "ACCIONES", "BYMA", "ARS", "A-24HS")
    clock_hold = ("C", "ACCIONES", "BYMA", "ARS", "A-24HS")
    wrong_variant = ("C", "ACCIONES", "BYMA", "USD", "INMEDIATA")
    excluded = ("D", "ACCIONES", "BYMA", "ARS", "A-24HS")
    blocked = ("E", "CEDEARS", "BYMA", "ARS", "A-24HS")
    term_hold = ("F", "OPCIONES", "BYMA", "ARS", "A-24HS")
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript("""
            CREATE TABLE financial_instrument_catalog(ticker, instrument_type, market, currency,
                settlement, status, last_seen_at, settlement_source);
            CREATE TABLE candidate_identity_v2(ticker, instrument_type, market, currency,
                settlement, checked_at, detail);
            CREATE TABLE universe_cycle_metrics(id, started_at, finished_at, selected_count, detail);
            CREATE TABLE symbol_cycle_metrics(cycle_started_at, detail);
            CREATE TABLE paper_events(id, event_type, event_at, detail);
            CREATE TABLE paper_decisions(decision_key TEXT PRIMARY KEY, decided_at, symbol, action);
            CREATE TABLE trade_gate_evaluations(decision_key TEXT PRIMARY KEY, evaluated_at, symbol,
                final_result, reason, paper_id);
            CREATE TABLE decision_evidence_snapshots(decision_key TEXT PRIMARY KEY, captured_at, payload_json);
            CREATE INDEX idx_decision_evidence_captured ON decision_evidence_snapshots(captured_at,decision_key);
            CREATE TABLE paper_positions(symbol, asset_class, market, currency,
                settlement, paper_id TEXT PRIMARY KEY, opened_at, closed_at, status, features_json);
            CREATE TABLE paper_fills(paper_id, side, filled_at);
            CREATE INDEX idx_spot_fills_position_side ON paper_fills(paper_id,side);
            CREATE TABLE paper_equity(api_key, cash);
            INSERT INTO paper_equity VALUES('never publish this token', '123456789');
        """)
        conn.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?, 'AVAILABLE',?,'PPI_FIELD')",
                         ((*key, phase.START) for key in (ready, clock_hold, wrong_variant, excluded, blocked, term_hold)))
        conn.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?)",
                     (*term_hold, phase.START, "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"))
        plan = {"active_plan": {"iol_rest_budget": {}, "read_scopes": [{"identity": clock_hold}]}}
        conn.execute("INSERT INTO universe_cycle_metrics VALUES(1,?,?,1,?)",
                     (phase.START, closed, "factibilidad=" + json.dumps(plan)))
        conn.execute("INSERT INTO paper_events VALUES(1,'DATA_REJECTED',?,'C: IOL_LAST_TRADE_CLOCK_UNVERIFIED')", (at,))
        for key, result, reason, paper_id in ((ready, "OPENED_SIMULATED", "PRIVATE", "id-1"),
                                              (blocked, "BLOCKED", "NO_GO_PAPER_SCALPING", None)):
            native = f"STRATEGY:{key[0]}:{key[1]}:{key[4]}:{key[3]}:{key[2]}:{at[:16]}:BUY"
            conn.execute("INSERT INTO paper_decisions VALUES(?,?,?,'BUY')", (native, at, key[0]))
            conn.execute("INSERT INTO trade_gate_evaluations VALUES(?,?,?,?,?,?)",
                         (native, at, key[0], result, reason, paper_id))
            fields = ("symbol", "asset_class", "market", "currency", "settlement")
            evidence = {"quote_used": {**dict(zip(fields, key)), "observed_at": at, "metadata_source": "IOL_REST",
                                       "last_kind": "TRADE", "book_at": at, "trade_at": at,
                                       "bid": "10", "ask": "11", "bid_size": "20", "ask_size": "20"},
                        "decision": {"final_result": result, "paper_id": paper_id}}
            conn.execute("INSERT INTO decision_evidence_snapshots VALUES(?,?,?)", (native, at, json.dumps(evidence)))
        native_entry = f"STRATEGY:PAMP:ACCIONES:A-24HS:ARS:BYMA:{at[:16]}:BUY"
        conn.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,'id-1',?,?,'CLOSED',?)",
                     (*ready, at, closed, json.dumps({"native_decision_key": native_entry})))
        conn.execute("INSERT INTO paper_fills VALUES('id-1','BUY_SIMULATED',?)", (at,))
        conn.execute("INSERT INTO paper_fills VALUES('id-1','SELL_SIMULATED',?)", (closed,))
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        reference = {"planned": [], "fresh": [ready, blocked], "evaluated": [ready, blocked],
                     "totales_plan": (1, 1), "catalogo_selector": {
                         "ACCIONES": {"close_catalog_eligible": 3, "close_catalog_eligible_with_fresh_pair": 1},
                         "CEDEARS": {"close_catalog_eligible": 1, "close_catalog_eligible_with_fresh_pair": 1},
                         "ETFS": {"close_catalog_eligible": 0, "close_catalog_eligible_with_fresh_pair": 0}}}
        report = phase.audit(conn, reference, lambda *args, **kwargs: [ready, blocked, excluded, clock_hold])
        drifted = phase.audit(conn, reference, lambda *args, **kwargs: [ready, blocked])
        query_plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + phase.BUY_GATE_SQL,
                                                   (*phase.CAPTURE_INDEX_WINDOW, 100))]
        assert any("SEARCH e USING INDEX idx_decision_evidence_captured" in row for row in query_plan)
        assert all("SCAN d" not in row and "SCAN g" not in row for row in query_plan)
        gate_reference = {**reference, "buys_por_familia": {"ACCIONES": 1, "CEDEARS": 1},
                          "resultados_buy": {"OPENED_SIMULATED": 1, "BLOCKED": 1},
                          "vetos_buy": {"NO_GO_PAPER_SCALPING": 1}}
        prior = {name: {"posiciones_con_fill_buy": int(name == "ACCIONES"),
                        "posiciones_con_salida_completa": int(name == "ACCIONES")} for name in phase.FAMILIES}
        allowed_tables = {"decision_evidence_snapshots", "paper_decisions", "trade_gate_evaluations",
                          "paper_positions", "paper_fills"}
        conn.set_authorizer(lambda action, table, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_READ and table not in allowed_tables else sqlite3.SQLITE_OK)
        supplement = phase.gate_addendum(conn, gate_reference, prior)
        mismatch = phase.gate_addendum(conn, {**gate_reference, "resultados_buy": {"OPENED_SIMULATED": 2}}, prior)

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    actions = report["familias"]["ACCIONES"]
    assert actions["primera_fase"] == {
        "COMPLETO": {"ENTRADA_Y_SALIDA_SIMULADAS_MISMA_CLAVE_Y_POSICION": 1},
        "RELOJ_PAR": {"RECHAZO_EXACTO_RELOJ_O_LIBRO": 1},
        "PLAN": {"FUERA_DEL_PLAN_IOL_RECONCILIADO": 1},
        "CATALOGO": {"FUERA_DEL_SELECTOR_IOL_RECONCILIADO": 1},
    }
    assert drifted["estado_lecturas"]["catalogo_selector"] == "SELECTOR_COUNTS_CHANGED"
    assert "PLAN" not in drifted["familias"]["ACCIONES"]["primera_fase"]
    assert actions["rondas_cerradas_ligadas"] == 1
    assert report["familias"]["CEDEARS"]["vetos_buy"] == {"NO_GO_PAPER_SCALPING": 1}
    assert report["familias"]["OPCIONES"]["primera_fase"] == {"PLAZO": {"PLAZO_NO_CERTIFICADO_EN_PROYECCION_PREVIA": 1}}
    assert report["familias"]["ETFS"]["claves"] == 0
    assert supplement["estado_lecturas"] == {"ordenes_gate": "READ", "fills_por_paper_id": "READ"}
    assert supplement["reconciliado_buy_1301"] and supplement["reconciliado_fills_1303"]
    assert supplement["objetivo"]["estado"] == "PAMP_BUY_GATE_FILL_Y_SALIDA_VINCULADOS"
    assert mismatch["objetivo"]["estado"] == "NO_VERIFICADO"
    published = json.dumps((report, supplement))
    assert not any(private in published for private in ("id-1", "PRIVATE", "123456789", "never publish", "STRATEGY:"))


def test_missing_plan_tables_are_unknown_instead_of_exclusion(tmp_path):
    database = tmp_path / "partial.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript("""
            CREATE TABLE financial_instrument_catalog(ticker, instrument_type, market, currency,
                settlement, status, last_seen_at, settlement_source);
            CREATE TABLE candidate_identity_v2(ticker, instrument_type, market, currency,
                settlement, checked_at, detail);
        """)
        conn.execute("INSERT INTO financial_instrument_catalog VALUES('ABC','ACCIONES','BYMA','ARS',"
                     "'A-24HS','AVAILABLE',?,'PPI_FIELD')", (phase.START,))
        report = phase.audit(conn, {"planned": [], "fresh": [], "evaluated": []})
    assert report["estado_lecturas"]["planes"] == "SQLITE_READ_UNVERIFIED"
    assert report["familias"]["ACCIONES"]["primera_fase"] == {"FUENTE_FICHA": {"FICHA_DE_RUEDA_NO_ACREDITADA": 1}}

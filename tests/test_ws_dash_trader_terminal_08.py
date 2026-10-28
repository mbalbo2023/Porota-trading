"""Issue #467: independent regression guards; no edits to PR #344 tests."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from time import perf_counter

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from rc6_trader_dashboard import generation
from rc6_trader_dashboard.components import badge, fields, table, metric
from rc6_trader_dashboard.datasets import shadow_rows, logs, contracts, instrument_market, family_summary
from rc6_trader_dashboard.navigation import CANONICAL_PATHS, DESTINATIONS, LEGACY, BY_KEY, resolve
from rc6_trader_dashboard.projection import Page, Projection, Store, freshness
from rc6_trader_dashboard.routes import build_page, install
from rc6_trader_dashboard.view_common import committed_funnel

NOW = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "observer.db"
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,session_state TEXT,
          ppi_auth TEXT,heartbeat_at TEXT,last_market_data_at TEXT,real_orders_sent INTEGER,detail TEXT);
        CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
          settlement TEXT,description TEXT,last_seen_at TEXT,status TEXT,capability TEXT,metadata_json TEXT,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
        CREATE TABLE candidate_identity_v2(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
          settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
        CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,
          settlement TEXT,source TEXT,strategy_version TEXT,status TEXT,quantity TEXT,entry_price TEXT,entry_cost TEXT,
          stop_price TEXT,target_price TEXT,opened_at TEXT,closed_at TEXT,exit_price TEXT,exit_cost TEXT,gross_pnl TEXT,
          net_pnl TEXT,close_reason TEXT,features_json TEXT,max_favorable TEXT,max_adverse TEXT);
        CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,source TEXT,strategy_version TEXT,decision_key TEXT,
          decided_at TEXT,symbol TEXT,action TEXT,score TEXT,reason TEXT,features_json TEXT);
        CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,symbol TEXT,
          asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,last TEXT,bid TEXT,ask TEXT,
          bid_size TEXT,ask_size TEXT,book_at TEXT,trade_at TEXT,received_at TEXT);
        CREATE INDEX exact_book ON market_snapshots(symbol,asset_class,market,currency,settlement,id);
        CREATE TABLE paper_equity_by_currency(id INTEGER PRIMARY KEY,measured_at TEXT,currency TEXT,cash TEXT,
          exposure TEXT,pending_proceeds TEXT,caucion_principal TEXT,caucion_accrued TEXT,unrealized_pnl TEXT,
          realized_pnl TEXT,equity TEXT);
        CREATE TABLE paper_position_marks(paper_id TEXT PRIMARY KEY,mark_price TEXT,book_at TEXT,marked_at TEXT);
        CREATE TABLE paper_exit_intents(paper_id TEXT PRIMARY KEY,state TEXT,cause TEXT,due_at TEXT,
          blocked_reason TEXT,supervised_at TEXT,attempts INTEGER);
        CREATE TABLE api_health(component TEXT PRIMARY KEY,state TEXT,detail TEXT,checked_at TEXT,last_success_at TEXT,source TEXT);
        CREATE TABLE source_sync(source TEXT,status TEXT,last_attempt_at TEXT,last_success_at TEXT,items INTEGER,detail TEXT);
        CREATE TABLE paper_cauciones(paper_id TEXT PRIMARY KEY,request_id TEXT,source TEXT,instrument_id TEXT,
          currency TEXT,principal TEXT,annual_rate_fraction TEXT,interest_days INTEGER,day_count_basis INTEGER,
          opened_at TEXT,maturity_at TEXT,settled_at TEXT,status TEXT,total_fees TEXT,gross_interest TEXT,
          fee_payment TEXT,terms_json TEXT);
        CREATE TABLE paper_caucion_allocations(request_id TEXT PRIMARY KEY,evaluated_at TEXT,
          decision_json TEXT,paper_id TEXT);
        CREATE TABLE paper_caucion_cash_sweep_state(id INTEGER PRIMARY KEY,heartbeat_at TEXT,state TEXT,
          last_attempt_at TEXT,code TEXT,paper_id TEXT,real_orders_sent INTEGER,detail TEXT);
        CREATE TABLE contract_evidence_v2_current(family TEXT,ticker TEXT,market TEXT,currency TEXT,settlement TEXT,
          source_class TEXT,snapshot_id INTEGER,evidence_hash TEXT,observed_at TEXT);
        CREATE TABLE contract_evidence_v2_snapshots(snapshot_id INTEGER PRIMARY KEY,family TEXT,ticker TEXT,
          market TEXT,currency TEXT,settlement TEXT,source_class TEXT,observed_at TEXT,evidence_hash TEXT,evidence_json TEXT);
        """)
        c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER','RUNNING','MARKET_OPEN','OK',?,?,0,'fixture')", (STAMP, STAMP))
        for i in range(25):
            key = (f"T{i:03d}", "ACCIONES", "BYMA", "ARS", "INMEDIATA")
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,'AVAILABLE','EQUITY','{}')", (*key, "Fixture instrument", STAMP))
            c.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,1,'AVAILABLE','fixture',?)", (*key, STAMP))
        for i, (currency, net) in enumerate((("ARS", "0.1"), ("ARS", "0.2"), ("USD", "5"), ("USD_MEP", "7"), ("USD_CCL", "9"))):
            c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "closed-" + str(i), "T000", "ACCIONES", "BYMA", currency, "INMEDIATA", "PAPER",
                "S1", "CLOSED", "1", "100", "1", "90", "110", (NOW - timedelta(minutes=10)).isoformat(),
                STAMP, "102", "1", str(float(net) + 2), net, "EOD", "{}", "0", "0"))
        c.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "open-1", "T000", "ACCIONES", "BYMA", "ARS", "INMEDIATA", "PAPER", "S1", "OPEN", "10", "100", "1", "90", "110", STAMP,
            None, None, None, None, None, None, "{}", "0", "0"))
        c.execute("INSERT INTO paper_position_marks VALUES('open-1','103',?,?)", ((NOW - timedelta(hours=1)).isoformat(), STAMP))
        for i in range(22):
            c.execute("INSERT INTO paper_decisions VALUES(?,'PAPER','S1',?,?,'T000','HOLD','78',?,'{}')", (i, "key-" + str(i), STAMP, "LIQUIDITY_BLOCKED"))
        c.execute("INSERT INTO api_health VALUES('IOL:caucion:ARS','SOURCE_UNAVAILABLE','section missing',?,NULL,'IOL')", (STAMP,))
        c.execute("INSERT INTO source_sync VALUES('IOL_SHADOW','LIVE_FRESH',?,?,1,'capture only')", (STAMP, STAMP))
        c.execute("INSERT INTO paper_cauciones VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "CAU-PAPER-1", "req-1", "PPI", "CAU:ARS:1D", "ARS", "100000", "0.125", 1, 365,
            STAMP, (NOW + timedelta(days=1)).isoformat(), None, "OPEN", "1.25", "7.5", "AT_MATURITY",
            '{"api_token":"sensitive-value","side":"COLOCADORA"}'))
        c.execute("INSERT INTO paper_caucion_allocations VALUES(?,?,?,?)", (
            "req-1", STAMP, json.dumps({"status": "PLACED_SIMULATED", "code": "CANDIDATE_SELECTED",
                                     "selected": {"principal": "100000", "currency": "ARS"}}), "CAU-PAPER-1"))
        c.execute("INSERT INTO paper_caucion_allocations VALUES(?,?,?,NULL)", (
            "req-hold", STAMP, json.dumps({"status": "HOLD", "code": "NO_ELIGIBLE_OFFER", "candidates": []})))
        c.execute("INSERT INTO paper_caucion_cash_sweep_state VALUES(1,?,'WAITING_WINDOW',NULL,'WINDOW_NOT_OPEN',NULL,0,'fixture')", (STAMP,))
    return path


@pytest.fixture
def client(database, tmp_path, monkeypatch):
    import ay_dashboard_auth as auth
    monkeypatch.setattr(auth, "TOKEN_BEARER", "terminal-fixture-auth-" + "t" * 40)
    monkeypatch.setattr(auth, "ENTORNO", "SANDBOX")
    monkeypatch.setattr(auth, "SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(auth, "_sesiones", {})
    def authorize(token, header, cookie):
        if not auth.autorizado(cookie=cookie, encabezado=header, parametro=token):
            raise HTTPException(401, "authentication required")
    app = FastAPI()
    install(app, authorize, lambda: str(database))
    with TestClient(app) as result:
        result.headers["Authorization"] = "Bearer " + auth.TOKEN_BEARER
        yield result


@pytest.mark.parametrize("path", sorted(CANONICAL_PATHS))
def test_every_canonical_subview_is_authenticated_and_dark(client, path):
    response = client.get(path)
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert len(soup.select(".primary-nav > a")) == 8
    assert len(soup.select("#porota-canonical-nav")) == 1
    assert len(soup.select("#porota-paper-mode")) == 1
    # A responsive drawer must offer a named close control within the drawer;
    # its covered opening toggle and an external keyboard are insufficient.
    close = soup.select_one("#terminal-sidebar button[aria-label='Cerrar menú']")
    assert close is not None and close.get('type') == 'button'
    assert close.get_text(strip=True) == 'Cerrar menú'
    assert soup.select_one("#trader-terminal-tokens")
    assert soup.select_one("nav.subnav [aria-current=page]")
    assert len(soup.select(".terminal-table")) <= 1
    assert len(soup.select(".terminal-table tr[data-row]")) <= 10
    assert response.headers["Cache-Control"].startswith("no-store")
    assert "ART" in response.text
    assert "Server-Timing" in response.headers


@pytest.mark.parametrize("path", sorted(LEGACY))
def test_legacy_urls_keep_shell_and_corresponding_subview(client, path):
    response = client.get(path)
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.select_one("#terminal-content")["data-view"] == "/".join(LEGACY[path])
    assert len(soup.select(".primary-nav > a")) == 8


def test_validation_milestones_retain_their_own_route_and_visible_link(client, database):
    # The canonical dispatcher must pass through to the existing validation
    # middleware, which renders the M0–M11 campaign from its own projection.
    assert "/validacion" not in LEGACY
    assert resolve("/validacion") == (None, None)
    page = BeautifulSoup(client.get("/sistema/evidencia").text, "html.parser")
    assert page.select_one("a[href='/validacion']").get_text(strip=True) == "Ver hitos M0–M11"
    app = FastAPI()
    @app.get("/validacion")
    def existing_validation_view():
        return {"view": "M0–M11"}
    def authorize(token, header, cookie):
        return None
    install(app, authorize, lambda: str(database))
    with TestClient(app) as routed:
        result = routed.get("/validacion")
    assert result.status_code == 200 and result.json() == {"view": "M0–M11"}


def test_no_auth_no_mutation_and_token_clean_redirect(client):
    import ay_dashboard_auth as auth
    client.headers.pop("Authorization")
    assert client.get("/analitica/performance").status_code == 401
    assert client.get("/api/trader/logs/download").status_code == 401
    entry = client.get("/instrumentos/contrato?token=" + auth.TOKEN_BEARER + "&q=T000", follow_redirects=False)
    assert entry.status_code == 303
    assert entry.headers["location"] == "/instrumentos/contrato?q=T000"
    assert "HttpOnly" in entry.headers["set-cookie"]
    result = client.get(entry.headers["location"])
    assert result.status_code == 200
    assert auth.TOKEN_BEARER not in result.text
    assert client.post("/universo/discovery").status_code == 405
    assert client.get("/trading/nonexistent").status_code == 404


def test_pagination_is_server_side_and_filters_survive(client):
    response = client.get("/instrumentos?currency=ARS&market=BYMA")
    soup = BeautifulSoup(response.text, "html.parser")
    assert len(soup.select("tr[data-row]")) == 10
    more = soup.find("a", string="Mostrar 10 más")
    assert "offset=10" in more["href"] and "currency=ARS" in more["href"]
    second = BeautifulSoup(client.get(more["href"]).text, "html.parser")
    assert len(second.select("tr[data-row]")) == 10
    assert second.find("a", string="Mostrar menos")
    last = BeautifulSoup(client.get("/instrumentos?offset=20").text, "html.parser")
    assert len(last.select("tr[data-row]")) == 5
    assert not last.find("a", string="Mostrar 10 más")
    assert "1–10 de 25" in response.text


def test_exact_identity_ignores_same_ticker_currency_and_history(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE candidate_identity_v2 SET can_simulate=0,status='BLOCKED' WHERE ticker='T000'")
        c.execute("INSERT INTO candidate_identity_v2 VALUES('T000','ACCIONES','BYMA','USD','INMEDIATA',1,'AVAILABLE','other currency',?)", (STAMP,))
        c.execute("CREATE TABLE production_history(symbol TEXT, instrument_type TEXT,settlement TEXT,row_count INTEGER)")
        c.execute("INSERT INTO production_history VALUES('T000','ACCIONES','INMEDIATA',99999)")
    with Store(database, now=NOW) as s:
        rows = Projection(s, {"q": "T000"}).catalog().rows
    assert len(rows) == 1 and rows[0]["currency"] == "ARS"
    assert rows[0]["readiness"] == "NO_READY"


def test_missing_candidate_contract_and_database_fail_closed(database, tmp_path):
    with sqlite3.connect(database) as c:
        c.execute("DROP TABLE candidate_identity_v2")
    with Store(database, now=NOW) as s:
        p = Projection(s)
        assert p.catalog().rows[0]["readiness"] == "NO_VERIFICADO"
        assert "READY" not in p.counts()
        assert p.shadow["state"] == generation.AWAITING
    missing = tmp_path / "never-create.db"
    html, _ = build_page("/", {}, missing)
    assert not missing.exists()
    assert "NO_VERIFICADO" in html
    assert "real_orders_sent=NO_VERIFICADO" in html


def test_currencies_are_separate_and_money_is_decimal(database):
    with Store(database, now=NOW) as s:
        rows = Projection(s).performance().rows
    assert {r["currency"] for r in rows} == {"ARS", "USD", "USD_MEP", "USD_CCL"}
    assert next(r["net_pnl"] for r in rows if r["currency"] == "ARS") == "0.3"
    assert all(r["edge"].startswith("NO_VERIFICADO") for r in rows)


def test_live_closes_show_factual_win_rate_with_full_filtered_denominator(database):
    from rc6_trader_dashboard.views_live import render
    with Store(database, now=NOW) as s:
        p = Projection(s)
        assert p.close_win_rate_today() == {"state": "AVAILABLE", "sample": 5,
                                            "wins": 5, "win_rate": 100.0}
        html = render(p, BY_KEY["en-vivo"], "cierres")
        assert "Win rate de cierres hoy" in html and "100,00 %" in html
        assert "5/5 cierres con PnL neto positivo" in html
        filtered = Projection(s, {"currency": "ARS"}).close_win_rate_today()
        assert filtered["sample"] == 2 and filtered["wins"] == 2


def test_invalid_financial_numbers_never_become_zero(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_positions SET net_pnl='bad' WHERE paper_id='closed-0'")
    with Store(database, now=NOW) as s:
        row = next(r for r in Projection(s).performance().rows if r["currency"] == "ARS")
        close_stats = Projection(s).close_win_rate_today()
    assert row["net_pnl"] is None and row["win_rate"] is None
    assert row["expectancy"] is None
    assert close_stats["state"] == "INVALID_NET_PNL" and close_stats["win_rate"] is None


def test_open_position_shows_signed_paper_pnl_only_with_current_book(database):
    from rc6_trader_dashboard.views_live import render
    with sqlite3.connect(database) as c:
        c.executescript("""CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,paper_id TEXT,source TEXT,
            side TEXT,filled_at TEXT,quantity TEXT,price TEXT,costs TEXT,slippage TEXT);
            CREATE TABLE paper_spot_sales(fill_id INTEGER PRIMARY KEY,paper_id TEXT,entry_cost TEXT,
            gross_pnl TEXT,net_pnl TEXT,net_proceeds TEXT,available_at TEXT,basis TEXT,reason TEXT);""")
        c.execute("UPDATE paper_position_marks SET book_at=?,marked_at=? WHERE paper_id='open-1'", (STAMP, STAMP))
    with Store(database, now=NOW) as s:
        p = Projection(s)
        row = p.positions().rows[0]
        html = render(p, BY_KEY["en-vivo"], "posiciones")
    assert row["unrealized_pnl"] == "29" and row["remaining_quantity"] == "10"
    assert "Ganancia/pérdida PAPER" in html
    assert "29,00" in html and "result-positive" in html

    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_position_marks SET mark_price='98' WHERE paper_id='open-1'")
    with Store(database, now=NOW) as s:
        row = Projection(s).positions().rows[0]
        html = render(Projection(s), BY_KEY["en-vivo"], "posiciones")
    assert row["unrealized_pnl"] == "-21" and "result-negative" in html

    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_position_marks SET book_at=? WHERE paper_id='open-1'",
                  ((NOW - timedelta(hours=1)).isoformat(),))
    with Store(database, now=NOW) as s:
        row = Projection(s).positions().rows[0]
    assert row["unrealized_pnl"] is None and row["freshness"] == "STALE"


def test_open_position_pnl_uses_validated_residual_lot_after_partial_sale(database):
    with sqlite3.connect(database) as c:
        c.executescript("""CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,paper_id TEXT,source TEXT,
            side TEXT,filled_at TEXT,quantity TEXT,price TEXT,costs TEXT,slippage TEXT);
            CREATE TABLE paper_spot_sales(fill_id INTEGER PRIMARY KEY,paper_id TEXT,entry_cost TEXT,
            gross_pnl TEXT,net_pnl TEXT,net_proceeds TEXT,available_at TEXT,basis TEXT,reason TEXT);""")
        opened = (NOW - timedelta(minutes=10)).isoformat()
        sold = (NOW - timedelta(minutes=5)).isoformat()
        c.execute("UPDATE paper_positions SET opened_at=?,features_json=? WHERE paper_id='open-1'",
                  (opened, '{"contract_cash_multiplier":"2"}'))
        c.execute("UPDATE paper_position_marks SET book_at=?,marked_at=? WHERE paper_id='open-1'", (STAMP, STAMP))
        c.execute("INSERT INTO paper_fills VALUES(1,'open-1','PAPER','SELL_SIMULATED',?,'4','102','0.5','0')", (sold,))
        c.execute("INSERT INTO paper_spot_sales VALUES(1,'open-1','0.40','16','15.10','815.50',NULL,'PAPER_CONSERVATIVE_CALENDAR','PARTIAL')")
    with Store(database, now=NOW) as s:
        row = Projection(s).positions().rows[0]
    assert row["quantity"] == "10" and row["remaining_quantity"] == "6"
    assert row["unrealized_pnl"] == "35.40"  # (103 - 100) * 6 * 2 - 0.60

    # A sale whose money does not reconcile cannot silently become a result.
    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_spot_sales SET net_pnl='99' WHERE fill_id=1")
    with Store(database, now=NOW) as s:
        assert Projection(s).positions().rows[0]["unrealized_pnl"] is None


def test_canonical_reports_exposes_only_published_paper_action4(database):
    from cg_paper_workspace import artifact_root
    from rc6_trader_dashboard.views_analytics import _action4_card
    target = artifact_root(database) / "reports" / "rc6_action4_auditoria_latest.json"
    target.parent.mkdir(parents=True)
    payload = {"status": "PAPER_READ_ONLY", "generated_at_utc": STAMP,
               "window": {"from_local": STAMP, "to_local": STAMP},
               "origin": {"run_id": "LOCAL_DAILY", "commit": "NOT_AVAILABLE"},
               "separation": {"executed_closed": 2, "opened_simulated": 1, "hold": 5},
               "safety": {"observer_mode": "PRODUCTION_PAPER", "sqlite": "READ_ONLY",
                          "real_orders_sent": 0, "broker_routes_called": False}}
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        html = _action4_card(Projection(s))
    assert "Action 4 · auditoría publicada" in html and "Cierres PAPER" in html
    assert "/api/reports/action4" in html and "LOCAL_DAILY" in html
    payload["safety"]["real_orders_sent"] = 1
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        rejected = _action4_card(Projection(s))
    assert "NO_VERIFICADO" in rejected and "/api/reports/action4" not in rejected
    payload["safety"]["real_orders_sent"] = False
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        rejected_boolean = _action4_card(Projection(s))
    assert "NO_VERIFICADO" in rejected_boolean and "/api/reports/action4" not in rejected_boolean


def test_counterfactual_profiles_show_three_verified_lanes_without_extra_table(database):
    from cg_paper_workspace import artifact_root
    from rc6_trader_dashboard.counterfactuals import profile_decisions
    from rc6_trader_dashboard.views_analytics import _profile_decisions_panel
    target = artifact_root(database) / "reports" / "decision_evidence_counterfactual_latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"compact_projection": True, "read_only": True, "real_orders_authorized": False,
               "generated_at": STAMP, "decisions": [{"decision_key": "fact-1", "symbol": "YPFD",
               "state": "VERIFIED", "factual_decision": "HOLD", "evidence_at": STAMP,
               "profiles": [{"name": "BASELINE_CONSERVATIVE_V1", "state": "VERIFIED", "action": "HOLD", "reason": "factual"},
                            {"name": "SHADOW_BALANCED_V1", "state": "VERIFIED", "action": "CANDIDATE_OPEN", "reason": "score"},
                            {"name": "SHADOW_AGGRESSIVE_V1", "state": "INSUFFICIENT_EVIDENCE", "action": "BUY", "reason": "api_token=private"}]}]}
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        page = profile_decisions(Projection(s))
        html = _profile_decisions_panel(page)
    assert page.total == 1 and page.rows[0]["aggressive"] is None
    assert "Conservador" in html and "Balanceado" in html and "Agresivo" in html
    assert "CANDIDATE_OPEN" in html and "private" not in html
    assert "<table" not in html and "autoridad de entrada: No" in html
    payload["generated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        assert profile_decisions(Projection(s)).rows == []


def test_cierre_displays_only_same_day_paper_lessons(database):
    from cg_paper_workspace import artifact_root
    from rc6_trader_dashboard.views_analytics import _action4_card
    target = artifact_root(database) / "reports" / "rc6_action4_auditoria_latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": "PAPER_READ_ONLY", "generated_at_utc": STAMP,
               "window": {"from_local": "2026-10-05T00:00:00-03:00", "to_local": STAMP},
               "safety": {"observer_mode": "PRODUCTION_PAPER", "observer_session_state": "MARKET_CLOSED", "sqlite": "READ_ONLY",
                          "real_orders_sent": 0, "broker_routes_called": False},
               "dashboard_daily_report": {"scope": "ALL_CONTRACT_FAMILIES_PAPER",
                                          "closure_state": "POSTCLOSE_VERIFIED", "periodo": "2026-10-05",
                                          "lessons": ["Cierres simulados: 2", "api_token=private"]}}
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        html = _action4_card(Projection(s))
    assert "Lecciones del cierre · 2026-10-05" in html and "Cierres simulados: 2" in html
    assert "private" not in html
    payload["dashboard_daily_report"]["periodo"] = "2026-10-04"
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        assert "Lecciones del cierre" not in _action4_card(Projection(s))
    payload["dashboard_daily_report"]["periodo"] = "2026-10-05"
    payload["safety"]["observer_session_state"] = "MARKET_OPEN"
    target.write_text(json.dumps(payload))
    with Store(database, now=NOW) as s:
        assert "Lecciones del cierre ·" not in _action4_card(Projection(s))


def test_report_registry_dialog_does_not_invent_instrument_fields(client, database):
    with sqlite3.connect(database) as c:
        c.execute("""CREATE TABLE report_registry(id INTEGER PRIMARY KEY,period_type TEXT,
            period_start TEXT,period_end TEXT,created_at TEXT,state TEXT,pdf_path TEXT)""")
        c.execute("INSERT INTO report_registry VALUES(210,'DIARIO',?,?,?,'VERDE',NULL)",
                  (STAMP, STAMP, STAMP))
    html = client.get("/analitica/reportes?channel=exits").text
    dialog = BeautifulSoup(html, "html.parser").select_one(".terminal-table .row-dialog")
    assert dialog and "Detalle fila 1 · 210" in dialog.get_text()
    for field in ("Ticker", "Familia", "Settlement", "Autoridad de entrada", "Reloj de la fuente"):
        assert field not in dialog.get_text()


def test_counterfactual_channel_does_not_hide_daily_funnel_on_other_tab(client, monkeypatch):
    from rc6_trader_dashboard import views_analytics
    seen = []
    monkeypatch.setattr(views_analytics, "committed_funnel",
                        lambda p: seen.append(dict(p.filters)) or "<div>funnel checked</div>")
    response = client.get("/analitica/cierre-diario?channel=exits")
    assert response.status_code == 200 and "funnel checked" in response.text
    assert seen and "channel" not in seen[-1]
    client.get("/analitica/cierre-diario?channel=UNIVERSE_SHADOW")
    assert seen[-1].get("channel") == "UNIVERSE_SHADOW"


def test_source_scope_score_and_trajectory_semantics(database, monkeypatch):
    root = Path(database).parent
    (root / "iol_shadow_latest.json").write_text(json.dumps({"refreshed_at": STAMP, "cache_state": "LIVE",
        "symbols": [{"state": "LIVE", "source_state": "LIVE_FRESH"},
                    {"state": "CACHE_STALE", "source_state": "EMPTY_UNEXPECTED"}]}))
    (root / "iol_family_reference_latest.json").write_text(json.dumps({"refreshed_at": STAMP,
        "cache_state": "LIVE", "last_known_good_at": STAMP,
        "section_states": {"fci": "LIVE", "fixed_income": "SOURCE_UNAVAILABLE"}}))
    monkeypatch.setenv("POROTA_IOL_SHADOW_CACHE_PATH", str(root / "iol_shadow_latest.json"))
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(root))
    with Store(database, now=NOW) as s:
        p = Projection(s)
        assert p.positions().rows[0]["mfe"] == "NO_MEDIDO"
        assert p.positions().rows[0]["mae"] == "NO_MEDIDO"
        assert p.positions().rows[0]["freshness"] == "STALE"
        assert p.positions().rows[0]["executable_mark"] == "NO_VERIFICADO"
        source = p.sources().rows[0]
        assert source["scope"] == "IOL quotes + families (por sección)" and source["state"] == "PARTIAL_LIVE"
        assert source["observed_state"] == "LIVE" and source["freshness"] == "FRESH"
        assert "LIVE=1" in source["data_quality"] and "CACHE_STALE=1" in source["data_quality"]
        assert "fixed_income" in source["detail"] and "source_sync IOL: LIVE_FRESH" in source["detail"]
        decision = p.decisions().rows[0]
        assert decision["reason"] == "LIQUIDITY_BLOCKED"
        assert decision["economics"] == "NOT_EVALUATED" and decision["risk"] == "NOT_EVALUATED"
        assert decision["final"] == "HOLD"  # The recorded PAPER action, not an invented gate result.
        assert decision["score_is_probability"] is False
    assert "badge-hot" in badge("HOT") and "badge-pass" not in badge("HOT")
    assert "badge-pass" not in badge("WARM") and "badge-pass" not in badge("DISCOVERY")
    assert "NO PROBABILIDAD" in metric("Score", "78", kind="score")
    assert freshness(STAMP, NOW) == "FRESH"
    assert freshness("2026-10-05 16:00:00", NOW) == "NO_VERIFICADO"
    assert freshness((NOW + timedelta(seconds=1)).isoformat(), NOW) == "NO_VERIFICADO"


def test_buy_without_gate_remains_unverified(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_decisions SET action='BUY' WHERE id=(SELECT MAX(id) FROM paper_decisions)")
    with Store(database, now=NOW) as s:
        decision = Projection(s).decisions().rows[0]
    assert decision["final"] == "BUY"
    assert decision["economics"] == "NO_VERIFICADO"
    assert decision["risk"] == "NO_VERIFICADO"


def test_iol_cache_without_current_receipt_never_becomes_live(database, monkeypatch):
    root = Path(database).parent
    old = (NOW - timedelta(minutes=10)).isoformat()
    (root / "iol_shadow_latest.json").write_text(json.dumps({"refreshed_at": old,
        "cache_state": "LIVE", "symbols": [{"state": "LIVE", "source_state": "LIVE_FRESH"}]}))
    (root / "iol_family_reference_latest.json").write_text(json.dumps({"refreshed_at": old,
        "cache_state": "LIVE", "section_states": {"fci": "LIVE"}}))
    monkeypatch.setenv("POROTA_IOL_SHADOW_CACHE_PATH", str(root / "iol_shadow_latest.json"))
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(root))
    with Store(database, now=NOW) as s:
        row = Projection(s).sources().rows[0]
    assert row["observed_state"] == "LIVE" and row["state"] == "STALE"


def coherent_cut():
    ident = "a" * 32
    metadata = {"generation_id": ident, "source_watermark": {"as_of": STAMP}, "configuration_fingerprint": "fixture-config", "as_of": STAMP}
    report = {**metadata, "real_orders_sent": 0, "real_routes": "NOT_CALLED", "engines": {
        "EQUITY_SPOT": {"telemetry": [{"identity": ["T000", "ACCIONES", "BYMA", "ARS", "INMEDIATA"],
            "state": "HOT", "rank": 1, "tradeability_score": "80", "strategy": "S1",
            "entry_authority": True, "strategy_source_at": STAMP, "rejection_reason": []}]}}}
    manifest = {**metadata, "sequence": 1}
    pointer = {"generation_id": ident, "sequence": 1, "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()}
    return seal_cut({"pointer": pointer, "manifest": manifest, "report": report, "checkpoint": dict(metadata), "status": dict(metadata)})


def seal_cut(cut):
    cut["manifest"]["files"] = {role: {"payload_digest": hashlib.sha256(json.dumps(cut[role], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()} for role in ("report", "checkpoint", "status")}
    cut["pointer"]["manifest_sha256"] = hashlib.sha256(json.dumps(cut["manifest"], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return cut


def test_adapter_uses_only_canonical_reader_and_never_promotes_shadow(database):
    calls = []
    cut = coherent_cut()
    def reader(root, **kwargs):
        calls.append((root, kwargs))
        return cut
    with Store(database, now=NOW) as s:
        p = Projection(s, generation_reader=reader)
        page = shadow_rows(p, "opportunities")
        assert page.rows[0]["entry_authority"] is False
        assert page.rows[0]["currency"] == "ARS"
        assert p.shadow["state"] == "COMMITTED_COHERENT_SHADOW"
        assert shadow_rows(p, "discovery").rows
    assert len(calls) == 1 and calls[0][1]["payload_limit"] == generation.PAYLOAD_LIMIT


def test_hot_reason_summary_keeps_all_original_blockers_in_detail(database):
    cut = coherent_cut()
    blockers = ["SCANNER_CAPACITY", "DEPTH_NO_VERIFICADO", "SOURCE_UNAVAILABLE", "SPREAD_NO_VERIFICADO"]
    cut["report"]["engines"]["EQUITY_SPOT"]["telemetry"][0]["rejection_reason"] = blockers
    seal_cut(cut)
    with Store(database, now=NOW) as s:
        p = Projection(s, generation_reader=lambda root, **kwargs: cut)
        row = shadow_rows(p, "opportunities").rows[0]
    assert row["state"] == "HOT" and row["entry_authority"] is False
    assert row["reason_summary"] == "SCANNER_CAPACITY · DEPTH_NO_VERIFICADO · +2 motivos (ver detalle)"
    assert all(reason in row["reason"] for reason in blockers)


@pytest.mark.parametrize("mutation", ["digest", "manifest_id", "member_id", "watermark", "config", "safety", "sequence", "payload"])
def test_mixed_or_corrupt_generation_fails_closed(mutation, tmp_path):
    cut = coherent_cut()
    if mutation == "digest":
        cut["pointer"]["manifest_sha256"] = "bad"
    elif mutation == "manifest_id":
        cut["manifest"]["generation_id"] = "b" * 32
    elif mutation == "member_id":
        cut["checkpoint"]["generation_id"] = "b" * 32
    elif mutation == "watermark":
        cut["status"]["source_watermark"] = {"as_of": "old"}
    elif mutation == "config":
        cut["status"]["configuration_fingerprint"] = "other"
    elif mutation == "safety":
        cut["report"]["real_orders_sent"] = 1
    elif mutation == "sequence":
        cut["pointer"]["sequence"] = 2
    else:
        cut["report"]["engines"] = {}
    result = generation.read_shadow(tmp_path, lambda root, **kw: cut)
    assert result["state"] == "NO_VERIFICADO"
    assert result["report"] == {}


def test_canonical_reader_rejection_has_no_legacy_fallback(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps({"HOT": 999}))
    def rejected(*args, **kwargs):
        raise ValueError("SHA_MISMATCH private-token")
    result = generation.read_shadow(tmp_path, rejected)
    assert result["report"] == {} and result["state"] == "NO_VERIFICADO"
    assert "private-token" not in json.dumps(result)


def test_funnel_uses_one_committed_cohort_not_current_ready_or_other_currency(database):
    cut = coherent_cut()
    cut["report"]["operational_funnel"] = {"as_of": STAMP, "by_currency_channel": [
        {"currency": "ARS", "channel": "NATIVE_FACTUAL", "stages": {"CATALOG_READY": 3,
            "STRATEGY_ELIGIBLE": 2, "TRADEABLE": 1, "HOT": 1, "PAPER_OPENED": 1}},
        {"currency": "USD", "channel": "SHADOW", "stages": {"CATALOG_READY": 99,
            "STRATEGY_ELIGIBLE": 99, "TRADEABLE": 99, "HOT": 99, "PAPER_OPENED": 9}}]}
    seal_cut(cut)
    with Store(database, now=NOW) as s:
        p = Projection(s, generation_reader=lambda *args, **kwargs: cut)
        html = committed_funnel(p)
        assert {key: p.counts()[key] for key in ("ELIGIBLE", "TRADEABLE", "HOT")} == {
            "ELIGIBLE": 2, "TRADEABLE": 1, "HOT": 1}
        selected = Projection(s, {"currency": "USD", "channel": "SHADOW"},
                              generation_reader=lambda *args, **kwargs: cut)
        assert selected.counts()["ELIGIBLE"] == 99
    assert "ARS / NATIVE_FACTUAL" in html
    assert "<b>3</b>" in html and "<b>99</b>" not in html and "<b>25</b>" not in html


def test_universe_cards_read_the_actual_cohort_stage_schema(database):
    from rc6_trader_dashboard.views_universe import render
    cut = coherent_cut()
    cut["report"]["operational_funnel"] = {"as_of": STAMP, "by_currency_channel": [
        {"currency": "ARS", "channel": "NATIVE_FACTUAL", "stages": {"SIGNAL_EVALUATED": 584}},
        {"currency": "ARS", "channel": "SHADOW_ENTRY_EXPERIMENT", "stages": {"SIGNAL_CANDIDATE": 374}},
        {"currency": "ARS", "channel": "UNIVERSE_SHADOW", "stages": {
            "CATALOG_READY": 11530, "STRATEGY_ELIGIBLE": 1070, "DISCOVERY_TOUCHED": 258}},
        {"currency": "USD", "channel": "UNIVERSE_SHADOW", "stages": {"STRATEGY_ELIGIBLE": 30}}]}
    seal_cut(cut)
    with Store(database, now=NOW) as store:
        p = Projection(store, generation_reader=lambda *args, **kwargs: cut)
        assert p.counts()["SIGNAL"] == 374
        html = render(p, BY_KEY["universo"], "resumen")
        assert p.funnel_group["channel"] == "UNIVERSE_SHADOW"
        assert p.counts()["ELIGIBLE"] == 1070 and p.counts()["TRADEABLE"] == 0
        assert "1.070" in html and "ARS / UNIVERSE_SHADOW" in html
        assert "<b>0</b>" in html and "<b>NO_VERIFICADO</b>" not in html
        assert "Agregado por familia; sin identidad exacta" in html
        assert "<dt>Ticker</dt>" not in html and "<dt>Settlement</dt>" not in html
        unknown = Projection(store, {"currency": "USD", "channel": "NATIVE_FACTUAL"},
                             generation_reader=lambda *args, **kwargs: cut)
        assert "ELIGIBLE" not in unknown.counts()


def test_read_only_snapshot_parameterization_and_large_catalog(database):
    with sqlite3.connect(database) as c:
        c.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,'AVAILABLE','EQUITY','{}')", (
            (f"L{i:05d}", "ACCIONES", "BYMA", "ARS", "INMEDIATA", "synthetic", STAMP) for i in range(12000)))
        c.executemany("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            (i, "PPI", STAMP, "L00000", "ACCIONES", "BYMA", "ARS", "INMEDIATA", "10", "9", "11", "100", "100", STAMP, STAMP, STAMP) for i in range(60000)))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    statements = []
    start = perf_counter()
    with Store(database, now=NOW, trace=statements.append) as s:
        p = Projection(s)
        result = p.catalog()
        assert len(result.rows) == 10 and result.total == 12025
        assert len(Projection(s, {"q": "' OR 1=1 --"}).catalog().rows) == 0
        with pytest.raises(sqlite3.OperationalError):
            s.connection.execute("DELETE FROM financial_instrument_catalog")
        assert s.query_count < 20
    assert perf_counter() - start < 3
    rendered, headers = build_page("/instrumentos", {}, database)
    assert len(BeautifulSoup(rendered, "html.parser").select("tr[data-row]")) == 10
    assert int(headers["X-Porota-Read-Queries"]) < 20
    assert len(rendered) < 180000
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not any("quick_check" in query.lower() for query in statements)
    assert any("LIMIT 10 OFFSET 0" in query for query in statements)
    assert not any("SELECT *" in query.upper() for query in statements)


def test_exact_quote_and_contract_do_not_cross_currencies(database):
    with sqlite3.connect(database) as c:
        for i, currency, bid in ((1, "ARS", "10"), (2, "USD", "999")):
            c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (i,"PPI",STAMP,"T000","ACCIONES","BYMA",currency,"INMEDIATA","10",bid,"11","1","1",STAMP,STAMP,STAMP))
            c.execute("INSERT INTO contract_evidence_v2_current VALUES('ACCIONES','T000','BYMA',?,'INMEDIATA','PPI',?,?,?)", (currency,i,str(i),STAMP))
            c.execute("INSERT INTO contract_evidence_v2_snapshots VALUES(?,'ACCIONES','T000','BYMA',?,'INMEDIATA','PPI',?,?,'{}')", (i,currency,STAMP,str(i)))
    key = json.dumps(["T000", "ACCIONES", "BYMA", "ARS", "INMEDIATA"])
    with Store(database, now=NOW) as s:
        p = Projection(s, {"identity": key})
        assert instrument_market(p).rows[0]["bid"] == "10"
        assert len(contracts(p).rows) == 1 and contracts(p).rows[0]["currency"] == "ARS"


def test_evidence_size_limit_xss_safe_logs_and_identity_deep_links(database, client, monkeypatch):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE financial_instrument_catalog SET description='<script>bad()</script>',metadata_json=? WHERE ticker='T000'", ("x" * 40000,))
    result = client.get("/instrumentos?q=T000")
    assert "<script>bad()" not in result.text and "&lt;script&gt;bad()" in result.text
    key = json.dumps(["T000", "ACCIONES", "BYMA", "ARS", "INMEDIATA"])
    page = client.get("/instrumentos/ficha", params={"identity": key})
    soup = BeautifulSoup(page.text, "html.parser")
    assert all("identity=" in link["href"] for link in soup.select("nav.subnav a"))
    root = database.parent
    (root / "oversize.json").write_bytes(b"x" * (1024 * 1024 + 1))
    (root / "logs").mkdir()
    monkeypatch.setenv("POROTA_SHARED_LOG_DIR", str(root / "logs"))
    (root / "logs" / "observer.log").write_text("token=never-render authorization=private cookie=opaque account_id=12345\n")
    with Store(database, now=NOW) as s:
        p = Projection(s)
        assert p.bounded_file("oversize.json")[1] == "EVIDENCE_SIZE_LIMIT"
        body = logs(p).rows[0]["line"]
        assert "never-render" not in body and "12345" not in body and "[REDACTED]" in body
    assert "never-render" not in client.get("/api/trader/logs/download").text


def test_package_contains_no_order_provider_network_or_mutation_code():
    forbidden = {"requests", "httpx", "aiohttp", "subprocess", "ppi_client", "be_paper_engine", "bd_ppi_readonly_guard"}
    root = Path(__file__).parents[1] / "rc6_trader_dashboard"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] in forbidden for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
    from rc6_trader_dashboard.design_system import SCRIPT, CSS
    assert "document.activeElement" in SCRIPT and "details[open]" in SCRIPT and "interacting()" in SCRIPT
    assert "let auto = false" in SCRIPT
    assert "focus({preventScroll:true})" in SCRIPT and "window.scrollTo(x,y)" in SCRIPT
    assert "prefers-reduced-motion" in CSS
    assert "@media(max-width:1024px)" in CSS and "@media(max-width:600px)" in CSS


def test_derived_metrics_require_fresh_validated_provenance(database):
    metadata = {"iv": "99", "yield": "88", "greeks": {"delta": "9"},
                "metric_provenance": {"yield": {"validated": True, "source": "PPI", "as_of": STAMP}}}
    with sqlite3.connect(database) as c:
        c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='T000'", (json.dumps(metadata),))
    with Store(database, now=NOW) as s:
        row = Projection(s, {"q": "T000"}).catalog().rows[0]
    assert row["metadata"]["yield"] == "88"
    assert "iv" not in row["metadata"] and "greeks" not in row["metadata"]


@pytest.mark.parametrize("provenance,allowed", [
    ({}, False),
    ({"validated": True, "source": "PPI", "as_of": "2026-09-01T12:00:00+00:00"}, False),
    ({"validated": True, "source": "PPI", "as_of": "2099-01-01T12:00:00+00:00"}, False),
    ({"validated": True, "as_of": STAMP}, False),
    ({"validated": True, "source": "PPI", "as_of": STAMP}, True),
])
def test_digest_valid_contract_does_not_validate_derived_metrics(database, provenance, allowed):
    payload = {"state": "AVAILABLE", "strike": "100", "iv": "99", "greeks": {"delta": "9"},
               "metric_provenance": {"iv": provenance, "greeks": provenance}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO contract_evidence_v2_current VALUES('ACCIONES','T000','BYMA','ARS','INMEDIATA','PPI',1,?,?)", (digest, STAMP))
        c.execute("INSERT INTO contract_evidence_v2_snapshots VALUES(1,'ACCIONES','T000','BYMA','ARS','INMEDIATA','PPI',?,?,?)", (STAMP, digest, encoded.decode()))
    key = json.dumps(["T000", "ACCIONES", "BYMA", "ARS", "INMEDIATA"])
    with Store(database, now=NOW) as s:
        row = contracts(Projection(s, {"identity": key})).rows[0]
    assert row["reason"] == "DIGEST_VERIFIED_SOURCE_EVIDENCE"
    assert row["contract"]["strike"] == "100"
    for name in ("iv", "greeks"):
        assert (name in row["contract"]) is allowed
        assert (name in row["metadata"]) is allowed


def test_external_canonical_history_is_read_only_and_cannot_change_ready(database, tmp_path, monkeypatch):
    history = tmp_path / "market_history.db"
    with sqlite3.connect(history) as c:
        c.execute("CREATE TABLE history_canonical_v2(symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,currency TEXT,price_basis TEXT,adjustment_basis TEXT,date TEXT,observed_at TEXT,source TEXT,quality_state TEXT)")
        c.executemany("INSERT INTO history_canonical_v2 VALUES('T000','ACCIONES','BYMA','INMEDIATA','ARS','RAW','RAW_NO_ADJUSTMENT',?,?, 'PPI','FULL_OHLC')", ((f"2026-09-{i:02d}", STAMP) for i in range(1, 10)))
    monkeypatch.setenv("HIST_DB_PATH", str(history))
    before = hashlib.sha256(history.read_bytes()).hexdigest()
    with Store(database, now=NOW) as s:
        p = Projection(s)
        assert p.history().rows[0]["row_count"] == 9
        assert p.counts()["READY"] == 25
    assert hashlib.sha256(history.read_bytes()).hexdigest() == before


def test_detail_names_unique_and_keys_survive_metric_change():
    data = Page("fixture", [{"currency": "ARS", "strategy": "S1", "net_pnl": "1"}, {"currency": "ARS", "strategy": "S2", "net_pnl": "2"}], 2, "AVAILABLE")
    columns = fields("currency|Moneda;strategy|Estrategia;net_pnl|Neto|money")
    first = BeautifulSoup(table(data, "Result", columns), "html.parser")
    data.rows[0]["net_pnl"] = "12"
    second = BeautifulSoup(table(data, "Result", columns), "html.parser")
    names = [x.get_text() for x in first.select(".detail-trigger")]
    assert len(names) == len(set(names))
    assert first.select_one(".row-dialog")["id"] == second.select_one(".row-dialog")["id"]
    assert first.select("details") == []
    assert len(first.select("dialog.row-dialog")) == 3  # two rows and reading status
    assert first.select(".detail-trigger")[-1].get_text() == "Estado de lectura"
    for trigger in first.select(".detail-trigger"):
        dialog = first.find(id=trigger["aria-controls"])
        assert dialog and dialog.name == "dialog" and dialog["aria-labelledby"]


def test_scalar_diagnostic_secrets_and_invalid_amounts_do_not_leak():
    sample = Page("fixture", [{"component": "IOL", "detail": "authorization=example-private-value",
                               "amount": "not-a-number"}], 1, "AVAILABLE")
    html = table(sample, "Reader", fields("component|Fuente;amount|Monto|money"), fields("detail|Diagnóstico"))
    assert "example-private-value" not in html
    assert "[REDACTED] sensitive detail" in html
    assert "not-a-number" not in html and "NO_VERIFICADO" in html


def test_cauciones_have_visible_navigation_and_separate_paper_decisions(client, database):
    placements = client.get("/trading/cauciones")
    assert placements.status_code == 200
    page = BeautifulSoup(placements.text, "html.parser")
    assert page.select_one("#terminal-content")["data-view"] == "trading/cauciones"
    assert page.select_one(".subnav a[aria-current=page]").get_text() == "Cauciones"
    assert len(page.select(".terminal-table")) == 1
    assert "CAU-PAPER-1" in placements.text
    assert "Cash sweep cauciones" in placements.text and "WAITING_WINDOW" in placements.text
    # The fixture deliberately has no request fingerprint or verified cost
    # authority. Show the ledger identity/status but suppress every economic
    # amount until its all-or-none proof is valid.
    assert "CAUCION_COST_PROVENANCE_NOT_VERIFIED" in placements.text
    assert "12,50 %" not in placements.text and "6,25" not in placements.text
    with Store(database, now=NOW) as store:
        unverified = Projection(store).cauciones()
    assert unverified.state == "NO_VERIFICADO"
    assert unverified.rows[0]["paper_id"] == "CAU-PAPER-1"
    assert unverified.rows[0]["status"] == "OPEN"
    assert not any(key in unverified.rows[0] for key in (
        "principal", "annual_rate_fraction", "gross_interest", "total_fees",
        "net_interest", "terms_json"))
    assert "sensitive-value" not in placements.text
    decisions = client.get("/trading/cauciones?caucion_view=decisiones")
    assert decisions.status_code == 200
    assert "NO_ELIGIBLE_OFFER" in decisions.text
    assert "HOLD" in decisions.text and "PLACED_SIMULATED" in decisions.text
    assert len(BeautifulSoup(decisions.text, "html.parser").select(".terminal-table")) == 1
    assert "PPI es identidad primaria" in decisions.text


def test_caucion_fingerprint_is_in_approved_ro_projection_and_valid_costs_render(client, database):
    # A valid modern PAPER position exercises the projection's named-column
    # allowlist. Without request_fingerprint, validate_position rejects it
    # despite a correct ledger row and the dashboard falsely hides the cut.
    terms = json.dumps({
        "instrument_id": "CAU:ARS:1D", "currency": "ARS",
        "annual_rate_fraction": "0.125", "start_date": "2026-10-05",
        "maturity_at": (NOW + timedelta(days=1)).isoformat(),
        "quoted_at": (NOW - timedelta(minutes=1)).isoformat(),
        "available_principal": "100000", "minimum_principal": "1000",
        "principal_step": "1000", "day_count_basis": 365,
        "fee_payment": "MATURITY", "metadata_source": "PPI_PRIMARY_TEST_CONTRACT",
    }, sort_keys=True)
    digest = hashlib.sha256((terms + "|100000").encode()).hexdigest()
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE paper_cauciones ADD COLUMN request_fingerprint TEXT")
        db.execute("""UPDATE paper_cauciones SET request_fingerprint=?,source='PRODUCTION_PAPER',
                   gross_interest='34.25',fee_payment='MATURITY',terms_json=?
                   WHERE paper_id='CAU-PAPER-1'""", (digest, terms))
    with Store(database, now=NOW) as store:
        page = Projection(store).cauciones()
    assert page.state == "AVAILABLE" and page.rows[0]["paper_id"] == "CAU-PAPER-1"
    assert page.rows[0]["request_fingerprint"] == digest
    assert page.rows[0]["net_interest"] == "33.00"
    assert page.rows[0]["broker_account_fees_verified"] is not True
    response = client.get("/trading/cauciones")
    assert response.status_code == 200
    assert "CAU-PAPER-1" in response.text and "12,50 %" in response.text
    assert "sensitive-value" not in response.text


def test_equity_read_interruption_is_not_reported_as_empty_ledger(database):
    with Store(database, now=NOW) as store:
        store.columns("paper_equity_by_currency")
        store.columns("paper_valuation_quality")
        store.connection.set_progress_handler(lambda: 1, 1)
        page = Projection(store).balances()
    assert page.state == "NO_VERIFICADO"
    assert page.rows == [] and page.reason == "CURRENCY_LEDGER_READ_ERROR"


def test_other_aggregates_fail_closed_on_interrupted_reads(database):
    with Store(database, now=NOW) as store:
        for table in ("paper_positions", "candidate_identity_v2", "paper_decisions"):
            store.columns(table)
        store.connection.set_progress_handler(lambda: 1, 1)
        projection = Projection(store)
        assert projection.performance().reason == "PERFORMANCE_READ_ERROR"
        assert family_summary(projection).reason == "FAMILY_SUMMARY_READ_ERROR"
        assert projection.decisions().state == "NO_VERIFICADO"


def test_home_does_not_call_failed_performance_read_an_empty_day(client, monkeypatch):
    from rc6_trader_dashboard.views_home import preview
    from rc6_trader_dashboard.components import timeline
    assert "Sin registros en este scope (0)" in preview(Page("paper_positions", [], 0, "AVAILABLE"), "Posiciones", "/en-vivo/posiciones")
    assert "NO_VERIFICADO" not in timeline([], verified_empty=True)
    assert "NO_VERIFICADO" in timeline([])
    failed = Page("paper_positions", reason="PERFORMANCE_READ_ERROR")
    assert "PERFORMANCE_READ_ERROR" in preview(failed, "Posiciones", "/en-vivo/posiciones")
    monkeypatch.setattr(Projection, "performance", lambda self, today=False: failed)
    response = client.get("/")
    assert response.status_code == 200
    assert "PERFORMANCE_READ_ERROR" in response.text
    assert "Sin cierres medidos en este scope" not in response.text


def test_system_health_and_integrations_keep_distinct_authorities(client):
    health = client.get("/sistema/salud")
    sources = client.get("/sistema/integraciones")
    assert "Salud de runtime y workers" in health.text
    assert "Contador factual; desconocido no equivale a cero" in health.text
    assert "Salud de integración" in sources.text
    assert "Salud de integración" not in health.text
    workers = client.get("/sistema/workers")
    assert "Cauciones · cash sweep" in workers.text and "WINDOW_NOT_OPEN" in workers.text
    source_rows = BeautifulSoup(sources.text, "html.parser").select(".terminal-table tr[data-row]")
    assert len(source_rows) <= 10
    assert "IOL · caché complementario" in source_rows[0].get_text()
    assert "SOURCE_UNAVAILABLE" in source_rows[0].get_text()
    assert "source_sync IOL: LIVE_FRESH" in sources.text  # Scoped diagnostic, not live authority.


def test_json_detail_redacts_secrets_and_reader_root_matches_canonical_default(database):
    calls = []
    def reader(root, **kwargs):
        calls.append(str(root))
        return coherent_cut()
    with Store(database, now=NOW) as s:
        Projection(s, generation_reader=reader).shadow
    from cg_paper_workspace import artifact_root
    assert calls == [str(artifact_root(database) / "dynamic-shadow")]
    page = Page("fixture", [{"features": {"api_key": "do-not-display", "account_id": "private-account", "spread": "0.1"}}], 1, "AVAILABLE")
    html = table(page, "Detail", fields("features|Evidencia"))
    assert "do-not-display" not in html and "private-account" not in html
    assert "[REDACTED]" in html and "spread" in html


def test_stale_committed_shadow_cannot_populate_live_cards_or_tables(database):
    from rc6_trader_dashboard.views_home import attention
    cut = coherent_cut()
    cut["report"]["operational_funnel"] = {"counts": {"HOT": 3, "SIGNAL": 1}}
    seal_cut(cut)
    with Store(database, now=NOW) as store:
        fresh = Projection(store, generation_reader=lambda root, **kwargs: cut)
        assert fresh.shadow_fresh
        assert fresh.counts()["HOT"] == 3
        assert len(shadow_rows(fresh, "opportunities").rows) == 1
    with Store(database, now=NOW + timedelta(minutes=1)) as store:
        stale = Projection(store, generation_reader=lambda root, **kwargs: cut)
        assert stale.shadow["state"] == "COMMITTED_COHERENT_SHADOW"
        assert not stale.shadow_fresh
        assert "HOT" not in stale.counts() and "SIGNAL" not in stale.counts()
        page = shadow_rows(stale, "opportunities")
        assert page.rows == [] and page.reason == "SHADOW_CUT_NOT_FRESH"
        alert_html, count = attention(stale, Page("paper_positions"))
        assert count > 0 and "SHADOW sin corte comprometido fresco" in alert_html


def test_equity_latest_index_migrates_existing_history_without_changing_currency_cut(tmp_path):
    from bt_caucion_paper import init_schema
    path = tmp_path / "existing-paper.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE paper_equity_by_currency(
              id INTEGER PRIMARY KEY AUTOINCREMENT,measured_at TEXT NOT NULL,
              currency TEXT NOT NULL,cash TEXT NOT NULL,exposure TEXT NOT NULL,
              pending_proceeds TEXT NOT NULL,caucion_principal TEXT NOT NULL,
              caucion_accrued TEXT NOT NULL,unrealized_pnl TEXT NOT NULL,
              realized_pnl TEXT NOT NULL,equity TEXT NOT NULL);
        """)
        conn.executemany(
            "INSERT INTO paper_equity_by_currency VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
            ((STAMP, ("ARS", "USD", "USD_MEP", "USD_CCL")[i % 4],
              *(str(i) for _ in range(8))) for i in range(4000)))
    class ExistingStore:
        def connect(self):
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn
    init_schema(ExistingStore())
    query = """SELECT e.id,e.currency,e.equity FROM paper_equity_by_currency e
        JOIN (SELECT currency,MAX(id) id FROM paper_equity_by_currency
        GROUP BY currency) latest ON latest.id=e.id ORDER BY e.currency LIMIT 10"""
    with sqlite3.connect(path) as conn:
        rows = conn.execute(query).fetchall()
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]
        assert rows == [(3997, "ARS", "3996"), (3998, "USD", "3997"),
                        (4000, "USD_CCL", "3999"), (3999, "USD_MEP", "3998")]
        assert any("USING COVERING INDEX idx_paper_equity_currency_latest" in line for line in plan)
        assert not any("USE TEMP B-TREE FOR GROUP BY" in line for line in plan)
    init_schema(ExistingStore())  # Idempotent on an existing PAPER history.


def test_connector_states_distinguish_observed_record_from_expected_cadence(database):
    with sqlite3.connect(database) as conn:
        checks = (
            ("BYMA_WEB", "VERDE", NOW - timedelta(hours=4)),
            ("BYMA_INSTRUMENTS_API", "GRIS", NOW - timedelta(hours=4)),
            ("PPI_PRODUCTION_CATALOG", "AMARILLO", NOW - timedelta(hours=25)),
            ("PPI_PRODUCTION_AUTH", "VERDE", NOW - timedelta(hours=6)),
            ("GEMINI_DECISION", "VERDE", NOW - timedelta(days=47)),
        )
        conn.executemany("INSERT INTO api_health VALUES(?,?,'',?,?,?)",
                         ((name, state, stamp.isoformat(), stamp.isoformat(), name)
                          for name, state, stamp in checks))
    with Store(database, now=NOW) as store:
        rows = {row["component"]: row for row in Projection(store).sources().rows}
    assert rows["BYMA_WEB"]["state"] == "VERDE"
    assert rows["BYMA_WEB"]["freshness"] == "FRESH"
    assert rows["BYMA_INSTRUMENTS_API"]["state"] == "GRIS"
    assert rows["PPI_PRODUCTION_CATALOG"]["state"] == "AMARILLO"
    assert rows["PPI_PRODUCTION_AUTH"]["state"] == "VERDE"
    assert rows["PPI_PRODUCTION_AUTH"]["cadence"] == "OBSERVER_SESSION"
    assert rows["GEMINI_DECISION"]["state"] == "NO_VERIFICADO"
    assert rows["GEMINI_DECISION"]["observed_state"] == "VERDE"
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE api_health SET checked_at=? WHERE component='PPI_PRODUCTION_CATALOG'",
                     ((NOW - timedelta(hours=40)).isoformat(),))
        conn.execute("UPDATE observer_state SET heartbeat_at=? WHERE id=1",
                     ((NOW - timedelta(minutes=4)).isoformat(),))
    with Store(database, now=NOW) as store:
        rows = {row["component"]: row for row in Projection(store).sources().rows}
    assert rows["PPI_PRODUCTION_CATALOG"]["state"] == "NO_VERIFICADO"
    assert rows["PPI_PRODUCTION_CATALOG"]["observed_state"] == "AMARILLO"
    assert rows["PPI_PRODUCTION_AUTH"]["state"] == "NO_VERIFICADO"


def test_home_attention_keeps_shadow_and_primary_source_in_five_item_preview():
    from types import SimpleNamespace
    from rc6_trader_dashboard.views_home import attention
    projection = SimpleNamespace(
        shadow_fresh=False, now=NOW,
        store=SimpleNamespace(query=lambda *_args, **_kwargs: []),
        runtime={"ppi_auth": "ERROR", "heartbeat_freshness": "STALE"},
        risk=lambda *, offset=None: SimpleNamespace(rows=[]),
    )
    positions = SimpleNamespace(rows=[{"symbol": f"T{i}", "freshness": "STALE", "exit_state": "BLOCKED"}
                                      for i in range(10)])
    html, count = attention(projection, positions)
    assert count >= 20
    assert html.index("SHADOW sin corte") < html.index("PPI market data")
    assert "Autenticación PPI sin estado OK" in html
    assert "Heartbeat runtime sin verificación fresca" in html
    assert "T9" not in html  # Detailed position alerts are below the first five.

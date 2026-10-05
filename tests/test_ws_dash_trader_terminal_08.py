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
from rc6_trader_dashboard.datasets import shadow_rows, logs, contracts, instrument_market
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


def test_invalid_financial_numbers_never_become_zero(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE paper_positions SET net_pnl='bad' WHERE paper_id='closed-0'")
    with Store(database, now=NOW) as s:
        row = next(r for r in Projection(s).performance().rows if r["currency"] == "ARS")
    assert row["net_pnl"] is None and row["win_rate"] is None
    assert row["expectancy"] is None


def test_source_scope_score_and_trajectory_semantics(database):
    with Store(database, now=NOW) as s:
        p = Projection(s)
        assert p.positions().rows[0]["mfe"] == "NO_MEDIDO"
        assert p.positions().rows[0]["mae"] == "NO_MEDIDO"
        assert p.positions().rows[0]["freshness"] == "STALE"
        assert p.positions().rows[0]["executable_mark"] == "NO_VERIFICADO"
        source = p.sources().rows[0]
        assert source["scope"] == "IOL:caucion:ARS" and source["state"] == "SOURCE_UNAVAILABLE"
        decision = p.decisions().rows[0]
        assert decision["reason"] == "LIQUIDITY_BLOCKED"
        assert decision["economics"] == "NO_EVAL" and decision["risk"] == "NOT_CALLED"
        assert decision["score_is_probability"] is False
    assert "badge-hot" in badge("HOT") and "badge-pass" not in badge("HOT")
    assert "badge-pass" not in badge("WARM") and "badge-pass" not in badge("DISCOVERY")
    assert "NO PROBABILIDAD" in metric("Score", "78", kind="score")
    assert freshness(STAMP, NOW) == "FRESH"
    assert freshness("2026-10-05 16:00:00", NOW) == "NO_VERIFICADO"
    assert freshness((NOW + timedelta(seconds=1)).isoformat(), NOW) == "NO_VERIFICADO"


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
        {"currency": "ARS", "channel": "NATIVE_FACTUAL", "stages": {"CATALOG_READY": 3, "PAPER_OPENED": 1}},
        {"currency": "USD", "channel": "SHADOW", "stages": {"CATALOG_READY": 99, "PAPER_OPENED": 9}}]}
    seal_cut(cut)
    with Store(database, now=NOW) as s:
        p = Projection(s, generation_reader=lambda *args, **kwargs: cut)
        html = committed_funnel(p)
    assert "ARS / NATIVE_FACTUAL" in html
    assert "<b>3</b>" in html and "<b>99</b>" not in html and "<b>25</b>" not in html


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
        c.execute("CREATE TABLE history_canonical_v2(symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,trading_date TEXT,observed_at TEXT,source_class TEXT,quality_state TEXT)")
        c.executemany("INSERT INTO history_canonical_v2 VALUES('T000','ACCIONES','BYMA','INMEDIATA',?,?, 'PPI','FULL_OHLC')", ((f"2026-09-{i:02d}", STAMP) for i in range(1, 10)))
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
    names = [x.get_text() for x in first.select("summary")]
    assert len(names) == len(set(names))
    assert first.select_one("details.row-detail")["id"] == second.select_one("details.row-detail")["id"]


def test_json_detail_redacts_secrets_and_reader_root_matches_canonical_default(database):
    calls = []
    def reader(root, **kwargs):
        calls.append(str(root))
        return coherent_cut()
    with Store(database, now=NOW) as s:
        Projection(s, generation_reader=reader).shadow
    assert calls == [str(database) + ".shadow"]
    page = Page("fixture", [{"features": {"api_key": "do-not-display", "account_id": "private-account", "spread": "0.1"}}], 1, "AVAILABLE")
    html = table(page, "Detail", fields("features|Evidencia"))
    assert "do-not-display" not in html and "private-account" not in html
    assert "[REDACTED]" in html and "spread" in html

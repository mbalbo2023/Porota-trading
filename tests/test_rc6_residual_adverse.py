"""Adverse regressions against the concrete RC6 entrypoints, without broker I/O."""
import ast
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_derivatives_engine as deriv
import bh_paper_gemini as gemini
import cr_contract_evidence_v2_mass_hf6 as catalog
import fn_critical_github_proxy_rc6 as github
import rc6_functional_health as health
import rc6_preopen as preopen


def test_disk_policy_metadata_matches_executable_contract():
    import yaml
    policy = yaml.safe_load(Path("ops/policy/porota-policy.yaml").read_text())["deployment"]
    gate = json.loads(Path("ops/policy/rc6-disk-housekeeping-v1.json").read_text())["deploy"]
    factors = gate["pretransfer_formula"]
    assert policy["droplet_disk_pretransfer_min_free_bytes"] == "dynamic"
    assert policy["droplet_disk_pretransfer_formula"] == (
        "image_tar_bytes + 2 * image_unpacked_bytes + 2 * bundle_bytes + fixed_headroom_bytes")
    assert factors == {"image_tar_multiplier": 1, "image_unpacked_multiplier": 2,
                       "bundle_multiplier": 2, "fixed_headroom_bytes": 2 * 1024**3}


@pytest.mark.parametrize("count,heartbeat", [
    (None, "2026-10-14T12:00:00+00:00"),
    (0, "2026-10-14T12:00:01+00:00"),
    (0, "2026-10-14T12:00:00"),
])
def test_full_health_rejects_unknown_count_and_untrusted_clock(tmp_path, count, heartbeat):
    path = tmp_path / "observer.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,"
                   "session_state TEXT,ppi_auth TEXT,real_orders_sent INTEGER,heartbeat_at TEXT,"
                   "last_market_data_at TEXT)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER','RUNNING','OPEN','OK',?,?,?)",
                   (count, heartbeat, heartbeat))
    result = health.probe(str(path), now=datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    assert result["status"] == "RED"
    assert health.parse_time("2026-10-14T12:00:00") is None


def test_preopen_observer_count_null_is_red(tmp_path, monkeypatch):
    path = tmp_path / "observer.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,"
                   "session_state TEXT,ppi_auth TEXT,real_orders_sent INTEGER,heartbeat_at TEXT)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER','RUNNING','OPEN','OK',NULL,"
                   "'2026-10-14T12:00:00+00:00')")
    monkeypatch.setattr(preopen, "DB", path)
    monkeypatch.setattr(preopen, "runtime_contracts", lambda connection, now: {"state": "GREEN"})
    assert preopen.observer_db()["state"] == "RED"


@pytest.mark.parametrize("worker", ["intraday_scalping_worker_state",
                                     "paper_caucion_cash_sweep_state", "paper_supervisor_state"])
def test_preopen_rejects_future_worker_heartbeat(worker):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE candidate_identity_v2(instrument_type TEXT,status TEXT,"
               "can_simulate INTEGER)")
    db.execute("INSERT INTO candidate_identity_v2 VALUES('ACCIONES','AVAILABLE',1)")
    for table in ("paper_positions", "paper_cauciones"):
        db.execute(f"CREATE TABLE {table}(id INTEGER)")
    for table in ("intraday_scalping_worker_state", "paper_caucion_cash_sweep_state",
                  "paper_supervisor_state"):
        db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY,heartbeat_at TEXT,"
                   "state TEXT,real_orders_sent INTEGER,routes_json TEXT)")
        stamp = "2026-10-14T12:00:01+00:00" if table == worker else "2026-10-14T12:00:00+00:00"
        db.execute(f"INSERT INTO {table} VALUES(1,?,'RUNNING',0,'[]')", (stamp,))
    result = preopen.runtime_contracts(db, datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
    assert result["state"] == "RED"
    assert result["workers"][{"intraday_scalping_worker_state": "scalping",
                                "paper_caucion_cash_sweep_state": "caucion_cash_sweep",
                                "paper_supervisor_state": "supervisor"}[worker]]["state"] == "RED"


@pytest.mark.parametrize("score", [-1, 2])
def test_paper_gemini_rejects_out_of_range_score(score):
    with pytest.raises(ValueError, match="fuera de rango"):
        gemini.GeminiPaperGate._parse(SimpleNamespace(text=json.dumps(
            {"decision": "APPROVE", "veto": False, "score": score})), "fixture")


def test_macro_verdict_rejects_wrong_type_without_provider_import():
    tree = ast.parse(Path("f_gemini_decision_engine.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "GeminiDecisionEngine")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_validar_veredicto")
    method.decorator_list = []
    scope = {"logger": SimpleNamespace(error=lambda *args: None)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<exact-method>", "exec"), scope)
    validate = scope["_validar_veredicto"]
    for payload in ({"macro_score": True, "veto_risk": False},
                    {"macro_score": "not-a-number", "veto_risk": False},
                    {"macro_score": .5, "veto_risk": 0}):
        assert validate(None, payload)["veto_risk"] is True
    assert validate(None, {"macro_score": .5, "veto_risk": False})["veto_risk"] is False


def test_catalog_bounds_sqlite_read_before_materialization(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_CATALOG_ROWS", 10)
    path = tmp_path / "catalog.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,"
                   "market TEXT,currency TEXT,settlement TEXT,settlement_source TEXT,status TEXT,"
                   "capability TEXT,last_seen_at TEXT,description TEXT,metadata_json TEXT)")
        db.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       [(str(i), "ACCIONES", "BYMA", "ARS", "A-24HS", "PPI", "AVAILABLE",
                         "READY", "", "", "X" * 4000) for i in range(50)])
    sql = []
    class Store:
        def connect(self):
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            db.set_trace_callback(sql.append)
            return db
    with pytest.raises(RuntimeError, match="PPI_CATALOG_BOUND_EXCEEDED"):
        catalog._catalog_rows(Store())
    assert any("LIMIT 11" in query.upper() for query in sql)
    assert all("ORDER BY" not in query.upper() for query in sql)


def test_github_pagination_sees_later_decisions_and_issues():
    calls = []
    issue = {"number": 77, "title": "[POROTA][RED] fixture",
             "body": "HOTFIX_AUTHORIZATION=AWAITING", "state": "open"}
    def api(method, path, payload):
        calls.append(path)
        if path.endswith("/issues/77"):
            return issue
        if "/comments?" in path:
            return [{"body": "unrelated"}] * 100 if "page=" not in path else [
                {"body": github.REJECTED_MARKER}]
        if "state=open" in path:
            return [{"title": "ordinary", "number": i} for i in range(50)] if "page=" not in path else [issue]
        raise AssertionError(path)
    broker = github.GithubIssueBroker(api)
    assert broker.dispatch({"op": "list_open_critical"})[0]["number"] == 77
    with pytest.raises(github.BrokerError, match="ISSUE_ALREADY_DECIDED"):
        broker.dispatch({"op": "add_authorization_comment", "number": 77,
                         "approved": True, "sender_hash": "a" * 16})
    assert not any(path.endswith("/issues/77/comments") for path in calls)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 0])
def test_legacy_derivative_terms_fail_closed(value):
    future = deriv.describe_future("DLR", {"contractMultiplier": value,
        "initialMargin": 100, "expirationDate": "2030-10-15"})
    assert not future.capable
    option = deriv.DerivativeSpec("fixture", "OPCION", strike_source="api",
                                  strike=100, lot_size=100, days_to_expiry=30)
    assert not deriv.assess_option(option, value, 100).capable


def test_expired_future_and_invalid_option_market_inputs_fail_closed():
    assert not deriv.describe_future("DLR", {"contractMultiplier": 1000,
        "initialMargin": 100, "expirationDate": "2000-01-01"}).capable
    for kwargs in ({"underlying_price": float("nan")}, {"spread_pct": float("nan")},
                   {"spread_pct": True}):
        terms = {"underlying_price": 100, "spread_pct": 1}
        terms.update(kwargs)
        option = deriv.DerivativeSpec("fixture", "OPCION", strike_source="api",
                                      strike=100, lot_size=100, days_to_expiry=30)
        assert not deriv.assess_option(option, 1, **terms).capable


def test_legacy_derivative_sizing_rejects_nonfinite_and_invalid_margin():
    option = deriv.DerivativeSpec("fixture", "OPCION", capable=True, lot_size=100)
    assert deriv.size_long_option(10000, option, float("nan"), 1) == 0
    future = deriv.DerivativeSpec("DLR", "FUTURO", capable=True, contract_multiplier=1000)
    assert deriv.size_future(10000, future, 100, float("inf"), 1) == 0
    assert deriv.size_future(10000, future, 100, 99, 1,
                             free_margin_ars=1000, initial_margin_per_contract=float("nan")) == 0

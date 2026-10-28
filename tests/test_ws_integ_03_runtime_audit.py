import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from scripts import rc6_zero_known_error_runtime_audit as audit


def _snapshot():
    now = datetime.now(timezone.utc).isoformat()
    by_family = dict(audit.BASELINE_READY)
    ready = sum(by_family.values())
    workers = {
        "scalping": {"state": "WAITING_MARKET", "heartbeat_at": now,
                     "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
        "caucion_cash_sweep": {"state": "WAITING_WINDOW", "heartbeat_at": now,
                               "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
        "exit_supervisor": {"state": "RUNNING", "heartbeat_at": now,
                            "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
    }
    frozen = json.loads(Path('DASHBOARD_TRUTH_MATRIX_RC6.json').read_text(encoding='utf-8'))
    route_inventory = {"route_count": frozen['route_count'], "registered_paths": frozen['registered_paths'], "routes": []}
    return {
        "database": {
            "observer": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                         "heartbeat_at": now, "ppi_auth": "OK",
                         "process_state": "WAITING_MARKET", "session_state": "MARKET_CLOSED"},
            "readiness": {"source": "candidate_identity_v2", "ready": ready,
                          "by_family": by_family, "rows": []},
            "invalid_candidate_states": 0,
            "residual_candidate_state_violations": 0,
            "generic_pending_residuals": 0,
            "workers": workers,
        },
        "dashboard_truth": {
            "mode": "PRODUCTION_PAPER", "execution": "SIMULATED",
            "real_orders_sent": 0,
            "readiness": {"source": "candidate_identity_v2", "ready": ready},
            "history": {"governs_readiness": False},
            "scalping": {"mode": "ACTIVE_PAPER"},
            "iol": {"allowed_states": sorted(audit.ALLOWED_IOL_STATES)},
        },
        "iol": {"cache_state": "CACHE_FRESH", "last_known_good_at": now},
        "route_inventory": route_inventory,
        "route_matrix_matches_artifact": True,
        "http_status": {path: 200 for path in audit.CORE_GET_PATHS},
    }


def test_runtime_audit_accepts_only_complete_paper_truth(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    audit._assert_snapshot(_snapshot())


@pytest.mark.parametrize("process,session", (
    ("RUNNING", "MARKET_OPEN"),
    ("WAITING_MARKET", "MARKET_CLOSED"),
    ("READY_PREOPEN", "PREOPEN"),
))
def test_observer_exact_phase_is_healthy_in_each_runtime_cycle(monkeypatch, process, session):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    snapshot["database"]["observer"].update(process_state=process, session_state=session)
    audit._assert_snapshot(snapshot)


@pytest.mark.parametrize("process,session,auth,error", (
    ("DEGRADED", "BOOT_CALENDAR", "OK", "OBSERVER_PHASE_UNHEALTHY"),
    ("DEGRADED", "MARKET_OPEN", "OK", "OBSERVER_PHASE_UNHEALTHY"),
    ("WAITING_MARKET", "MARKET_OPEN", "OK", "OBSERVER_PHASE_UNHEALTHY"),
    ("RUNNING", "MARKET_CLOSED", "OK", "OBSERVER_PHASE_UNHEALTHY"),
    ("WAITING_MARKET", "MARKET_CLOSED", "COOLDOWN", "OBSERVER_PPI_AUTH_UNHEALTHY"),
    ("RUNNING", "MARKET_OPEN", "ERROR", "OBSERVER_PPI_AUTH_UNHEALTHY"),
    ("WAITING_MARKET", "MARKET_CLOSED", None, "OBSERVER_PPI_AUTH_UNHEALTHY"),
))
def test_fresh_observer_heartbeat_cannot_hide_unhealthy_cycle(
        monkeypatch, process, session, auth, error):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    snapshot["database"]["observer"].update(
        process_state=process, session_state=session, ppi_auth=auth)
    with pytest.raises(RuntimeError, match=error):
        audit._assert_snapshot(snapshot)


def test_stability_cycle_does_not_retry_degraded_observer(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    bad = _snapshot()
    bad["database"]["observer"].update(
        process_state="DEGRADED", session_state="BOOT_CALENDAR")
    observations = []
    monkeypatch.setattr(audit, "collect", lambda _label: observations.append(1) or bad)
    monkeypatch.setattr(audit.time, "sleep", lambda _: pytest.fail("unsafe retry"))
    with pytest.raises(RuntimeError, match="OBSERVER_PHASE_UNHEALTHY"):
        audit.consistent_snapshot("stability-2")
    assert observations == [1]


def test_readiness_race_retries_exact_equality_and_persistent_mismatch_fails(
        monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    stale = _snapshot()
    stale["dashboard_truth"]["readiness"]["ready"] -= 1
    good = _snapshot()
    snapshots = iter([stale, good])
    monkeypatch.setattr(audit, "collect", lambda _label: next(snapshots))
    monkeypatch.setattr(audit.time, "sleep", lambda _seconds: None)
    assert audit.consistent_snapshot("stability", attempts=2)["dashboard_truth"]["readiness"]["ready"] == good["database"]["readiness"]["ready"]
    monkeypatch.setattr(audit, "collect", lambda _label: stale)
    with pytest.raises(RuntimeError, match="DASHBOARD_READINESS_MISMATCH"):
        audit.consistent_snapshot("stability", attempts=2, retry_seconds=0)


def test_readiness_retry_never_delays_worker_safety_failure(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    bad = _snapshot()
    bad["database"]["workers"]["scalping"]["state"] = "ERROR"
    called = []
    monkeypatch.setattr(audit, "collect", lambda _label: called.append(1) or bad)
    monkeypatch.setattr(audit.time, "sleep", lambda _seconds: pytest.fail("unsafe retry"))
    with pytest.raises(RuntimeError, match="WORKER_STATE_UNHEALTHY"):
        audit.consistent_snapshot("stability")
    assert called == [1]


def test_dashboard_race_does_not_hide_broken_route(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    bad = _snapshot()
    bad["dashboard_truth"]["readiness"]["ready"] -= 1
    bad["http_status"]["/salud"] = 503
    called = []
    monkeypatch.setattr(audit, "collect", lambda _label: called.append(1) or bad)
    monkeypatch.setattr(audit.time, "sleep", lambda _seconds: pytest.fail("unsafe retry"))
    with pytest.raises(AssertionError):
        audit.consistent_snapshot("stability")
    assert called == [1]


def test_marked_partial_candidate_refresh_retries_but_durable_loss_fails(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    partial = _snapshot()
    partial["database"]["readiness"]["ready"] -= 25
    partial["database"]["readiness"]["by_family"]["LETRAS"] -= 25
    partial["dashboard_truth"]["readiness"]["ready"] -= 25
    partial["database"]["readiness"]["refresh"] = {
        "state": "RUNNING", "checked_at": datetime.now(timezone.utc).isoformat()}
    good = _snapshot()
    snapshots = iter([partial, good])
    monkeypatch.setattr(audit, "collect", lambda _label: next(snapshots))
    monkeypatch.setattr(audit.time, "sleep", lambda _seconds: None)
    assert audit.consistent_snapshot("stability", attempts=2) == good

    partial["database"]["readiness"]["refresh"]["state"] = "COMPLETE"
    monkeypatch.setattr(audit, "collect", lambda _label: partial)
    with pytest.raises(AssertionError):
        audit.consistent_snapshot("stability", attempts=2)

    partial["database"]["readiness"]["refresh"]["state"] = "RUNNING"
    partial["database"]["workers"]["scalping"]["state"] = "ERROR"
    with pytest.raises(RuntimeError, match="WORKER_STATE_UNHEALTHY"):
        audit.consistent_snapshot("stability", attempts=2)


def test_runtime_route_contract_tracks_frozen_terminal_manifest(monkeypatch):
    from scripts.rc6_dashboard_route_inventory import inventory

    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    frozen = json.loads(Path("DASHBOARD_TRUTH_MATRIX_RC6.json").read_text(encoding="utf-8"))
    actual = inventory()
    assert actual == frozen
    assert actual['route_count'] == len(actual['routes'])
    assert actual['registered_paths'] == len({row['path'] for row in actual['routes']})
    by_path={row['path']:row for row in actual['routes']}
    assert by_path['/analisis']['canonical_view']=='analitica/anual'
    assert by_path['/aprendizaje']['canonical_view']=='analitica/contrafactuales'
    assert 'counterfactual_learning_rc6.json' in by_path['/analitica/contrafactuales']['datasets']
    assert by_path['/analitica/anual']['canonical_concepts']==['history']
    snapshot = _snapshot()
    snapshot["route_inventory"] = actual
    audit._assert_snapshot(snapshot)
    snapshot["route_inventory"] = dict(actual, registered_paths=actual['registered_paths']-1)
    with pytest.raises(AssertionError):
        audit._assert_snapshot(snapshot)
    snapshot["route_inventory"] = actual
    snapshot["route_matrix_matches_artifact"] = False
    with pytest.raises(AssertionError):
        audit._assert_snapshot(snapshot)


def test_premarket_scalping_waiting_market_is_a_healthy_live_state(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    assert "WAITING_MARKET" in audit.SCALPING_HEALTHY_RUNTIME_STATES
    audit._assert_snapshot(snapshot)


@pytest.mark.parametrize("state", ["LOGIN_COOLDOWN", "LOGIN_ERROR", "DEGRADED", "ERROR", "SECURITY_BLOCK", "STOPPED"])
def test_scalping_nonhealthy_states_remain_fail_closed_with_diagnostic(monkeypatch, state):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    snapshot["database"]["workers"]["scalping"]["state"] = state
    with pytest.raises(RuntimeError, match=rf"WORKER_STATE_UNHEALTHY\|worker=scalping\|state={state}"):
        audit._assert_snapshot(snapshot)


def test_scalping_waiting_market_still_requires_fresh_heartbeat(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    snapshot = _snapshot()
    snapshot["database"]["workers"]["scalping"]["heartbeat_age_seconds"] = 301
    with pytest.raises(RuntimeError, match=r"WORKER_HEARTBEAT_STALE\|worker=scalping\|state=WAITING_MARKET"):
        audit._assert_snapshot(snapshot)


def test_worker_health_contracts_are_distinct_and_fail_closed():
    assert audit.SCALPING_HEALTHY_RUNTIME_STATES == frozenset({"RUNNING", "WAITING_MARKET"})
    assert audit.CASH_SWEEP_HEALTHY_RUNTIME_STATES == frozenset({
        "WAITING_WINDOW", "WAITING_CALENDAR", "HOLD", "PLACED_SIMULATED",
    })
    assert audit.EXIT_SUPERVISOR_HEALTHY_RUNTIME_STATES == frozenset({"RUNNING"})
    assert "WAITING_MARKET" not in audit.CASH_SWEEP_HEALTHY_RUNTIME_STATES


def test_candidate_state_guard_accepts_fail_closed_shadow_and_blocks_unsafe_residuals():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE candidate_identity_v2(
        ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
        can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT
    )""")
    connection.executemany(
        "INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
        [
            ("A","ACCIONES","BYMA","ARS","A-24HS",1,"AVAILABLE","READY","now"),
            ("B","ACCIONES","BYMA","ARS","A-24HS",0,"OBSERVED_SHADOW","NO_PRIMARY","now"),
            ("C","BONOS","BYMA","ARS","A-24HS",0,"AVAILABLE","NEEDS_NOMINAL_UNITS","now"),
            ("D","BONOS","BYMA","ARS","A-24HS",0,"STALE","STALE","now"),
            ("E","FUTUROS","ROFEX","ARS","A-24HS",0,"PAUSED_EXPLICIT","UNRESOLVED","now"),
            ("F","OBLIGACIONES","BYMA","USD","A-24HS",1,"AVAILABLE","READY","now"),
        ],
    )
    assert audit._candidate_state_violations(connection) == (0, 0)

    connection.execute(
        "INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
        ("G","FUTUROS","ROFEX","ARS","A-24HS",0,"STALE","UNRESOLVED","now"),
    )
    assert audit._candidate_state_violations(connection) == (0, 1)

    connection.execute(
        "INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
        ("H","ACCIONES","BYMA","ARS","A-24HS",1,"OBSERVED_SHADOW","BAD","now"),
    )
    assert audit._candidate_state_violations(connection) == (1, 1)
    connection.close()


def test_stability_audit_blocks_ready_regression_and_lkg_erasure(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    baseline = _snapshot()
    regressed = copy.deepcopy(baseline)
    regressed["database"]["readiness"]["ready"] -= 1
    regressed["dashboard_truth"]["readiness"]["ready"] -= 1
    with pytest.raises(AssertionError):
        audit._assert_snapshot(regressed, baseline)
    erased = copy.deepcopy(baseline)
    erased["iol"]["last_known_good_at"] = None
    with pytest.raises(AssertionError):
        audit._assert_snapshot(erased, baseline)


def test_runtime_audit_is_read_only_and_has_no_order_client():
    source = Path("scripts/rc6_zero_known_error_runtime_audit.py").read_text(encoding="utf-8")
    assert "mode=ro" in source and "PRAGMA query_only=ON" in source
    assert "network_order_test_performed" in source
    assert "place_order" not in source and "send_order" not in source


def test_iol_snapshot_accepts_explicit_source_unavailable_fail_safe(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "cache_state": "SOURCE_UNAVAILABLE",
        "refreshed_at": "2026-09-30T03:00:00+00:00",
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "fallback_order": audit.IOL_FALLBACK_ORDER,
        "section_states": {"caucion:ARS": "SOURCE_UNAVAILABLE_NO_LKG"},
    }), encoding="utf-8")
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(tmp_path))
    snapshot = audit._iol_snapshot()
    assert snapshot["cache_state"] == "SOURCE_UNAVAILABLE"
    assert snapshot["last_known_good_at"] is None


def test_iol_snapshot_rejects_fake_cache_fresh_without_lkg(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "cache_state": "CACHE_FRESH",
        "refreshed_at": "2026-09-30T03:00:00+00:00",
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "fallback_order": audit.IOL_FALLBACK_ORDER,
        "section_states": {"caucion:ARS": "SOURCE_UNAVAILABLE"},
    }), encoding="utf-8")
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="IOL_LKG_TIMESTAMP_MISSING"):
        audit._iol_snapshot()


@pytest.mark.parametrize("age,accepted", ((-1, False), (0, True), (240, True), (241, False)))
def test_iol_live_fresh_requires_guardian_240_second_lkg_contract(
        tmp_path, monkeypatch, age, accepted):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "source": "IOL_MCP", "cache_state": "LIVE_FRESH",
        "last_known_good_at": "2026-10-28T15:00:00+00:00",
        "refreshed_at": "2026-10-28T15:00:00+00:00",
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "fallback_order": audit.IOL_FALLBACK_ORDER,
        "section_states": {"caucion:ARS": "LIVE_FRESH"},
    }), encoding="utf-8")
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(tmp_path))
    monkeypatch.setattr(audit, "_age_seconds", lambda _stamp: age)
    if accepted:
        assert audit._iol_snapshot()["cache_state"] == "LIVE_FRESH"
    else:
        with pytest.raises(RuntimeError, match="IOL_LIVE_FRESH_STALE"):
            audit._iol_snapshot()


@pytest.mark.parametrize("stamp", ("unparseable", "2026-10-28T15:00:00"))
def test_iol_live_fresh_rejects_unparseable_or_naive_lkg(tmp_path, monkeypatch, stamp):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "cache_state": "LIVE_FRESH", "last_known_good_at": stamp,
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "fallback_order": audit.IOL_FALLBACK_ORDER,
        "section_states": {"caucion:ARS": "LIVE_FRESH"},
    }), encoding="utf-8")
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="IOL_LIVE_FRESH_TIMESTAMP_INVALID"):
        audit._iol_snapshot()


def test_iol_live_fresh_rejects_rewritten_cache_with_old_sample(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "cache_state": "LIVE_FRESH",
        "last_known_good_at": "2026-09-30T11:00:00+00:00",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "fallback_order": audit.IOL_FALLBACK_ORDER,
        "section_states": {"caucion:ARS": "LIVE_FRESH"},
    }), encoding="utf-8")
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="IOL_LIVE_FRESH_STALE\\|field=last_known_good_at"):
        audit._iol_snapshot()


def test_iol_historical_cache_stays_explicitly_historical(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "cache_state": "CACHE_STALE", "last_known_good_at": "2026-09-30T15:00:00+00:00",
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
        "fallback_order": audit.IOL_FALLBACK_ORDER,
        "section_states": {"caucion:ARS": "CACHE_STALE"},
    }), encoding="utf-8")
    monkeypatch.setenv("POROTA_IOL_SHADOW_ROOT", str(tmp_path))
    assert audit._iol_snapshot()["cache_state"] == "CACHE_STALE"


def test_http_reports_path_status_and_bounded_body(monkeypatch, capsys):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"ok"

    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "token")
    observed = {}

    def open_ok(*_args, **kwargs):
        observed.update(kwargs)
        return Response()

    monkeypatch.setattr(audit, "urlopen", open_ok)
    assert audit._http("/reportes") == (200, b"ok")
    assert observed["timeout"] == audit.DEFAULT_HTTP_TIMEOUT_SECONDS
    output = capsys.readouterr().out
    assert "POROTA_RUNTIME_AUDIT_HTTP_START=/reportes|TIMEOUT=20" in output
    assert "POROTA_RUNTIME_AUDIT_HTTP_RESULT=/reportes|HTTP=200|BYTES=2" in output


def test_http_error_keeps_path_status_and_bounded_body(monkeypatch, capsys):
    error = HTTPError("http://127.0.0.1:8000/salud", 500, "boom", {}, None)
    error.read = lambda: b"failure-body"
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "token")
    monkeypatch.setattr(audit, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    assert audit._http("/salud") == (500, b"failure-body")
    output = capsys.readouterr().out
    assert "POROTA_RUNTIME_AUDIT_HTTP_RESULT=/salud|HTTP=500" in output
    assert "BODY=failure-body" in output


def test_http_transport_error_names_path(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        audit,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(RuntimeError, match=r"path=/salud"):
        audit._http("/salud")


def test_full_universe_route_has_production_sized_timeout(monkeypatch):
    observed = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"ok"

    def open_ok(*_args, **kwargs):
        observed.update(kwargs)
        return Response()

    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "token")
    monkeypatch.setattr(audit, "urlopen", open_ok)
    assert audit._http("/universo-operativo") == (200, b"ok")
    assert observed["timeout"] == 60


def test_timeout_error_names_path_and_effective_budget(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        audit,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("slow")),
    )
    with pytest.raises(
        RuntimeError,
        match=r"DASHBOARD_HTTP_TIMEOUT\|path=/universo-operativo\|seconds=60",
    ):
        audit._http("/universo-operativo")


def test_collect_reports_all_non_200_paths_before_truth_parse(monkeypatch):
    monkeypatch.setattr(
        audit,
        "_http",
        lambda path: ((500, b"boom") if path == "/salud" else (200, b"{}")),
    )
    with pytest.raises(RuntimeError, match=r'DASHBOARD_HTTP_FAILURES=.*"/salud":500'):
        audit.collect("diagnostic")


def test_heavy_dashboard_routes_have_evidence_based_cold_render_budgets():
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/"] == 45
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/vivo"] == 45
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/trading"] == 45
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/historicos"] == 45
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/sistema"] == 45
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/salud"] == 45
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/universo-operativo"] == 60
    assert audit.PATH_HTTP_TIMEOUT_SECONDS["/api/dashboard/truth"] == 60


def test_truth_api_timeout_remains_fail_closed_with_heavy_read_budget(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        audit,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("slow")),
    )
    with pytest.raises(
        RuntimeError,
        match=r"DASHBOARD_HTTP_TIMEOUT\|path=/api/dashboard/truth\|seconds=60",
    ):
        audit._http("/api/dashboard/truth")

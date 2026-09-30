import copy
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
        "scalping": {"state": "RUNNING", "heartbeat_at": now,
                     "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
        "caucion_cash_sweep": {"state": "WAITING_WINDOW", "heartbeat_at": now,
                               "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
        "exit_supervisor": {"state": "RUNNING", "heartbeat_at": now,
                            "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
    }
    route_inventory = {"route_count": 55, "registered_paths": 53, "routes": []}
    return {
        "database": {
            "observer": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                         "heartbeat_at": now},
            "readiness": {"source": "candidate_identity_v2", "ready": ready,
                          "by_family": by_family, "rows": []},
            "invalid_candidate_states": 0,
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
        "iol": {"last_known_good_at": now},
        "route_inventory": route_inventory,
        "route_matrix_matches_artifact": True,
        "http_status": {path: 200 for path in audit.CORE_GET_PATHS},
    }


def test_runtime_audit_accepts_only_complete_paper_truth(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    audit._assert_snapshot(_snapshot())


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
    monkeypatch.setattr(audit, "urlopen", lambda *_args, **_kwargs: Response())
    assert audit._http("/salud") == (200, b"ok")
    output = capsys.readouterr().out
    assert "POROTA_RUNTIME_AUDIT_HTTP_START=/salud" in output
    assert "POROTA_RUNTIME_AUDIT_HTTP_RESULT=/salud|HTTP=200|BYTES=2" in output


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


def test_collect_reports_all_non_200_paths_before_truth_parse(monkeypatch):
    monkeypatch.setattr(
        audit,
        "_http",
        lambda path: ((500, b"boom") if path == "/salud" else (200, b"{}")),
    )
    with pytest.raises(RuntimeError, match=r'DASHBOARD_HTTP_FAILURES=.*"/salud":500'):
        audit.collect("diagnostic")

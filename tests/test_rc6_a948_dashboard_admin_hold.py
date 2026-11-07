"""A948: legacy dashboard administration must not mutate PAPER runtime state.

These are local HTTP boundary checks. They cannot establish droplet reachability.
"""

import hashlib
import hmac
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import HTMLResponse


_TOKEN = "A948_TEST_ONLY_BEARER_TOKEN_40_CHARS_OR_MORE"


@pytest.fixture
def isolated_dashboard(tmp_path, monkeypatch):
    import aa_env_guard as env_guard
    import ac_db
    import ay_dashboard_auth as auth
    import o_dashboard as dashboard

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth, "TOKEN_BEARER", _TOKEN)
    monkeypatch.setattr(auth, "_sesiones", {})
    monkeypatch.setattr(auth, "SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(dashboard, "DASHBOARD_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setattr(dashboard, "ENV_FILE_PATH", str(tmp_path / ".env"), raising=False)
    monkeypatch.setattr(ac_db, "DB_PATH", str(tmp_path / "fixture-only.db"))
    monkeypatch.setattr(ac_db, "_pragmas_applied", False)

    env_file = tmp_path / ".env"
    env_file.write_bytes(
        b"# A948 TEST FIXTURE - no real credentials\n"
        b"ENVIRONMENT=PRODUCTION_PAPER\n"
        b"ORDER_EXECUTION_MODE=confirm\n"
        b"PRODUCTION_AUTO_EXECUTE_CONFIRMED=false\n"
        b"PPI_ACCOUNT_NUMBER=FAKE_ACCOUNT_A948\n"
        b"PPI_API_KEY=FAKE_PPI_SECRET_A948\n"
    )
    return dashboard, env_guard, env_file, tmp_path / "fixture-only.db"


def _headers():
    return {"Authorization": "Bearer " + _TOKEN}


def _valid_legacy_csrf():
    ts = str(int(time.time()))
    digest = hmac.new(_TOKEN.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{digest}"


def _unwrapped_legacy_routes(dashboard):
    """Exercise the real legacy handlers without pre-existing RC6 middleware.

    The live app already intercepts GET/POST /config; this secondary
    regression catches accidental re-exposure if middleware order changes.
    """
    legacy = FastAPI()
    legacy.add_api_route("/legacy-config", dashboard.config_editor, methods=["GET"], response_class=HTMLResponse)
    legacy.add_api_route("/legacy-config", dashboard.config_save, methods=["POST"], response_class=HTMLResponse)
    return legacy


def test_config_get_is_authenticated_read_only_and_never_discloses_env_values(isolated_dashboard):
    dashboard, _, env_file, _ = isolated_dashboard
    with TestClient(dashboard.app) as client:
        assert client.get("/config").status_code == 401
        response = client.get("/config", headers=_headers())

    assert response.status_code == 200
    assert "<main" in response.text
    assert "<h1" in response.text
    # Canonical GET may contain read-only search/filter controls; no admin writes.
    assert 'action="/config"' not in response.text
    assert "action='/config'" not in response.text
    assert 'action="/restart-request"' not in response.text
    assert "action='/restart-request'" not in response.text
    for canary in ("FAKE_ACCOUNT_A948", "FAKE_PPI_SECRET_A948"):
        assert canary not in response.text
    assert env_file.exists()


def test_legacy_config_get_stays_safe_without_canonical_middleware(isolated_dashboard):
    dashboard, _, env_file, _ = isolated_dashboard
    with TestClient(_unwrapped_legacy_routes(dashboard)) as client:
        assert client.get("/legacy-config").status_code == 401
        response = client.get("/legacy-config", headers=_headers())

    assert response.status_code == 200
    assert "GitHub Actions" in response.text
    assert "<main" in response.text
    assert "<h1" in response.text
    assert 'role="status"' in response.text
    assert "<form" not in response.text
    assert "<input" not in response.text
    for canary in ("FAKE_ACCOUNT_A948", "FAKE_PPI_SECRET_A948"):
        assert canary not in response.text
    assert env_file.exists()


@pytest.mark.parametrize("env_value", ["PRODUCTION", "PRODUCTION_PAPER"])
def test_config_post_rejects_even_valid_csrf_without_changing_env_or_queue(isolated_dashboard, env_value):
    dashboard, _, env_file, db_file = isolated_dashboard
    initial_bytes = env_file.read_bytes()
    payload = {
        "csrf_token": _valid_legacy_csrf(),
        "ENVIRONMENT": env_value,
        "ORDER_EXECUTION_MODE": "auto",
        "PRODUCTION_AUTO_EXECUTE_CONFIRMED": "true",
        "PPI_API_KEY": "FORGED_PPI_KEY_A948",
    }
    with TestClient(dashboard.app) as client:
        # Already blocked by bc_dashboard_v163; do not duplicate that guard.
        assert client.post("/config", data=payload).status_code == 405
        canonical = client.post("/config", headers=_headers(), data=payload)

    assert canonical.status_code == 405
    assert env_file.read_bytes() == initial_bytes
    if db_file.exists():
        with sqlite3.connect(db_file) as db:
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='env_change_requests'"
            ).fetchone()
            assert table is None or db.execute("SELECT COUNT(*) FROM env_change_requests").fetchone()[0] == 0


def test_legacy_config_post_denies_malformed_body_before_parsing(isolated_dashboard):
    dashboard, _, env_file, _ = isolated_dashboard
    initial_bytes = env_file.read_bytes()
    with TestClient(_unwrapped_legacy_routes(dashboard)) as client:
        response = client.post(
            "/legacy-config",
            headers={**_headers(), "Content-Type": "multipart/form-data; boundary=A948_MISSING_BOUNDARY"},
            content=b"--A948_MISSING_BOUNDARY\r\nnot-a-valid-form-part",
        )
    assert response.status_code == 403
    assert env_file.read_bytes() == initial_bytes


@pytest.mark.parametrize("env_value", ["PRODUCTION", "PRODUCTION_PAPER"])
def test_legacy_config_post_fails_closed_without_canonical_middleware(isolated_dashboard, env_value):
    dashboard, _, env_file, db_file = isolated_dashboard
    initial_bytes = env_file.read_bytes()
    with TestClient(_unwrapped_legacy_routes(dashboard)) as client:
        assert client.post("/legacy-config", data={"ENVIRONMENT": env_value}).status_code == 401
        response = client.post(
            "/legacy-config", headers=_headers(),
            data={
                "csrf_token": _valid_legacy_csrf(),
                "ENVIRONMENT": env_value,
                "ORDER_EXECUTION_MODE": "auto",
                "PRODUCTION_AUTO_EXECUTE_CONFIRMED": "true",
                "PPI_API_KEY": "FORGED_PPI_KEY_A948",
            },
        )

    assert response.status_code == 403
    assert "GitHub Actions" in response.text
    assert env_file.read_bytes() == initial_bytes
    if db_file.exists():
        with sqlite3.connect(db_file) as db:
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='env_change_requests'"
            ).fetchone()
            assert table is None or db.execute("SELECT COUNT(*) FROM env_change_requests").fetchone()[0] == 0


def test_restart_request_fails_closed_without_creating_telegram_queue(isolated_dashboard, monkeypatch):
    dashboard, env_guard, env_file, _ = isolated_dashboard
    original = env_file.read_bytes()
    request_id = env_guard.register_change(["ORDER_EXECUTION_MODE"], env_switch=False)
    sent = []
    fake_notifier = SimpleNamespace(
        send_telegram_generic_confirmation=lambda *args, **kwargs: sent.append((args, kwargs))
    )
    monkeypatch.setitem(
        sys.modules, "b_notifiers",
        SimpleNamespace(MultiChannelNotifier=lambda: fake_notifier),
    )

    with TestClient(dashboard.app) as client:
        assert client.post("/restart-request", data={"request_id": request_id}).status_code == 401
        response = client.post(
            "/restart-request", headers=_headers(),
            data={"csrf_token": _valid_legacy_csrf(), "request_id": request_id},
        )

    assert response.status_code == 403
    assert "GitHub Actions" in response.text
    assert sent == []
    assert env_guard.get_request(request_id)["status"] == "SAVED"
    assert env_file.read_bytes() == original

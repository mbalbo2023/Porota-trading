import sqlite3
import time

from c_ppi_client import ResilientPPIClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "guard.sqlite"))
    import ac_db
    monkeypatch.setattr(ac_db, "DB_PATH", str(tmp_path / "guard.sqlite"))
    client = ResilientPPIClient.__new__(ResilientPPIClient)
    client.AUTH_RETRY_COOLDOWN_SECONDS = 900
    client.SERVER_RETRY_COOLDOWN_SECONDS = 600
    client.RATE_LIMIT_COOLDOWN_SECONDS = 3600
    client.AUTH_UNKNOWN_COOLDOWN_SECONDS = 3600
    client.LOGIN_MAX_ATTEMPTS_PER_WINDOW = 6
    client.LOGIN_ATTEMPT_WINDOW_SECONDS = 3600
    return client


def test_401_y_403_esperan_quince_minutos(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client._login_failure_policy(Exception("HTTP 401"), "HTTP 401") == (
        "AUTH_REJECTED", 900)
    assert client._login_failure_policy(Exception("HTTP 403"), "HTTP 403") == (
        "AUTH_REJECTED", 900)


def test_504_y_no_json_son_temporales(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client._login_failure_policy(Exception("HTTP 504"), "HTTP 504") == (
        "UPSTREAM_TEMPORARY", 600)
    assert client._login_failure_policy(
        ValueError("Expecting value"), "Respuesta no JSON de PPI (HTTP 200)") == (
        "PROTOCOL_TEMPORARY", 600)


def test_429_conserva_una_hora(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client._login_failure_policy(Exception("HTTP 429"), "HTTP 429") == (
        "RATE_LIMIT", 3600)


def test_error_desconocido_falla_cerrado(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client._login_failure_policy(Exception("HTTP 422"), "HTTP 422") == (
        "CONFIGURATION_ERROR", 3600)


def test_guard_persistente_admite_seis_y_bloquea_el_septimo(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    for _ in range(6):
        assert client._reserve_login_attempt() == (True, 0)
    allowed, retry = client._reserve_login_attempt()
    assert allowed is False
    assert 1 <= retry <= 3600

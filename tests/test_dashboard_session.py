"""Regresiones del acceso web: token inicial, cookie y navegación limpia."""

from pathlib import Path

from fastapi.testclient import TestClient

import ay_dashboard_auth as auth
import o_dashboard as dashboard


def _preparar(monkeypatch):
    token = "T" * 40
    monkeypatch.setattr(auth, "TOKEN_BEARER", token)
    monkeypatch.setattr(auth, "ENTORNO", "SANDBOX")
    monkeypatch.setattr(dashboard, "DASHBOARD_ACCESS_TOKEN", token)
    auth._sesiones.clear()
    return token


def test_el_token_inicial_se_convierte_en_cookie_y_desaparece(monkeypatch):
    token = _preparar(monkeypatch)
    cliente = TestClient(dashboard.app)

    primera = cliente.get(
        "/api/testing/estado?token=" + token,
        follow_redirects=False,
    )

    assert primera.status_code == 303
    assert primera.headers["location"] == "/api/testing/estado"
    cookie = primera.headers["set-cookie"]
    assert "porota_dashboard_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert token not in cookie

    siguiente = cliente.get(primera.headers["location"])
    assert siguiente.status_code == 200


def test_sin_cookie_ni_token_el_panel_sigue_cerrado(monkeypatch):
    _preparar(monkeypatch)
    cliente = TestClient(dashboard.app)
    respuesta = cliente.get("/api/testing/estado")
    assert respuesta.status_code == 401


def test_los_enlaces_internos_no_propagan_tokens():
    fuente = (Path(__file__).resolve().parent.parent / "o_dashboard.py").read_text(
        encoding="utf-8"
    )
    assert "?token=TU-TOKEN" not in fuente
    assert "?token={token}" not in fuente

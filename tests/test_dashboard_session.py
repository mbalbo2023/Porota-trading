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


def test_la_sesion_sobrevive_una_recreacion_del_contenedor(monkeypatch, tmp_path):
    token = _preparar(monkeypatch)
    almacen = tmp_path / "dashboard_sessions.json"
    monkeypatch.setattr(auth, "SESSION_STORE_PATH", str(almacen))
    monkeypatch.setattr(
        auth, "_TOKEN_FINGERPRINT",
        auth.hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )

    sesion = auth.crear_sesion_desde_token(token)
    assert auth.sesion_valida(sesion)
    assert almacen.exists()

    # Simula memoria vacía tras recrear el contenedor.
    auth._sesiones.clear()
    auth._cargar_sesiones()

    assert auth.sesion_valida(sesion)
    assert oct(almacen.stat().st_mode & 0o777) == "0o600"

def test_sandbox_admite_sesion_sin_vencimiento(monkeypatch):
    token = _preparar(monkeypatch)
    monkeypatch.setattr(auth, "SESION_SIN_VENCIMIENTO", True)

    sesion = auth.crear_sesion_desde_token(token)
    assert auth._sesiones[sesion] == 0.0

    monkeypatch.setattr(auth.time, "time", lambda: 9_999_999_999)
    assert auth.sesion_valida(sesion) is True


def test_cookie_persistente_se_renueva_en_sandbox(monkeypatch):
    token = _preparar(monkeypatch)
    monkeypatch.setattr(auth, "SESION_SIN_VENCIMIENTO", True)
    monkeypatch.setattr(auth, "COOKIE_MAX_AGE_SECONDS", 400 * 24 * 3600)
    cliente = TestClient(dashboard.app)

    primera = cliente.get("/?token=" + token, follow_redirects=False)
    assert primera.status_code == 303
    assert "Max-Age=34560000" in primera.headers["set-cookie"]

    siguiente = cliente.get("/")
    assert siguiente.status_code == 200
    assert "Max-Age=34560000" in siguiente.headers["set-cookie"]


def test_produccion_rechaza_sesion_sin_vencimiento(monkeypatch):
    monkeypatch.setattr(auth, "ENTORNO", "PRODUCTION")
    monkeypatch.setattr(auth, "SESION_HORAS", 0)
    problemas = auth.validar_configuracion()
    assert any("solo está permitido" in problema for problema in problemas)

def test_actividad_en_vivo_responde_con_sesion_valida(monkeypatch):
    token = _preparar(monkeypatch)
    cliente = TestClient(dashboard.app)

    entrada = cliente.get("/vivo?token=" + token, follow_redirects=False)
    assert entrada.status_code == 303
    assert entrada.headers["location"] == "/vivo"

    respuesta = cliente.get("/vivo")
    assert respuesta.status_code == 200
    # RC4 convierte /vivo en la vista operacional canónica: operaciones primero,
    # decisiones y scalping después, motores al final.
    assert "1. Operaciones abiertas ahora" in respuesta.text
    assert "3. Decisiones — por qué aceptó o rechazó" in respuesta.text
    assert "4. Scalping" in respuesta.text
    assert "5. Motores / workers" in respuesta.text
    assert "Embudo de rechazos — última hora" not in respuesta.text
    assert respuesta.text.count("id='porota-canonical-nav'") == 1

def test_dashboard_usa_zona_horaria_del_mercado(monkeypatch):
    monkeypatch.setattr(dashboard, "SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
    ahora = dashboard._ahora_local()

    assert ahora.tzinfo is not None
    assert getattr(ahora.tzinfo, "key", None) == "America/Argentina/Buenos_Aires"


def test_vivo_es_alias_del_panel_consolidado(monkeypatch, tmp_path):
    token = _preparar(monkeypatch)
    estado = tmp_path / "startup_state.json"
    estado.write_text(
        '{"estado":"ESPERANDO_APERTURA","modo":null,"mensaje":"Fin de semana"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "STARTUP_STATE_PATH", str(estado))

    consultas = []

    def consulta(sql, params=()):
        consultas.append(sql)
        return []

    monkeypatch.setattr(dashboard, "_query", consulta)
    monkeypatch.setattr(
        dashboard.runtime_status, "read_bot_state",
        lambda: {"alive": True, "state": "WAITING_OPEN", "detail": "Fuera de rueda"},
    )
    cliente = TestClient(dashboard.app)

    entrada = cliente.get("/vivo?token=" + token, follow_redirects=False)
    assert entrada.status_code == 303
    respuesta = cliente.get("/vivo")

    assert respuesta.status_code == 200
    assert "1. Operaciones abiertas ahora" in respuesta.text
    assert "2. Operaciones cerradas recientes" in respuesta.text
    assert "4. Scalping" in respuesta.text
    assert "5. Motores / workers" in respuesta.text
    assert "Embudo de rechazos — última hora" not in respuesta.text
    assert respuesta.text.count("id='porota-canonical-nav'") == 1
    # La ruta heredada conserva sus lecturas SQLite para no saltear la
    # autenticación y renovación de sesión existentes. Son exclusivamente
    # SELECT locales: no implican llamadas a PPI ni capacidad de ordenar.
    assert consultas
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in consultas)
    consulta_senales = next(sql for sql in consultas if "FROM signals" in sql)
    assert "__SISTEMA__" in consulta_senales

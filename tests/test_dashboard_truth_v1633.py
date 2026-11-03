import json

import am_api_health as health
import bb_runtime_status as runtime
import bc_dashboard_v163 as dashboard_ext


def test_manifesto_de_configuracion_solo_guarda_booleanos(tmp_path, monkeypatch):
    target = tmp_path / "presence.json"
    monkeypatch.setattr(runtime, "CONFIG_PRESENCE_PATH", str(target))
    monkeypatch.setenv("PPI_API_SECRET", "secreto-que-no-debe-aparecer")
    runtime.write_config_presence()
    raw = target.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "secreto-que-no-debe-aparecer" not in raw
    assert payload["configured"]["PPI_API_SECRET"] is True
    assert all(isinstance(value, bool) for value in payload["configured"].values())


def test_dashboard_lee_presencia_sin_recibir_el_secreto(tmp_path, monkeypatch):
    target = tmp_path / "presence.json"
    target.write_text(json.dumps({"schema": 1, "configured": {"PPI_API_SECRET": True}}))
    monkeypatch.setattr(runtime, "CONFIG_PRESENCE_PATH", str(target))
    monkeypatch.delenv("PPI_API_SECRET", raising=False)
    assert dashboard_ext._safe_presence_text("PPI_API_SECRET") == "******** (configurada)"
    assert "PPI_API_SECRET" not in dashboard_ext._safe_presence_text("PPI_API_SECRET")


def test_manifesto_ausente_no_afirma_que_falta_una_credencial(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CONFIG_PRESENCE_PATH", str(tmp_path / "missing.json"))
    monkeypatch.delenv("PPI_API_SECRET", raising=False)
    assert dashboard_ext._safe_presence_text("PPI_API_SECRET") == "ESTADO NO DISPONIBLE"


def test_telegram_detenido_es_gris_aunque_haya_fallos_viejos(monkeypatch):
    monkeypatch.setattr(runtime, "read_bot_state", lambda: {"alive": False, "state": "STOPPED"})
    monkeypatch.setattr(runtime, "telegram_activity", lambda limit=100: [
        {"status": "FALLIDO", "timestamp": runtime.now_iso()}])
    result = health.chequear_telegram(None)
    assert result.estado == health.GRIS
    assert "detenido" in result.detalle.lower()


def test_telegram_ultimo_entregado_supera_un_fallo_anterior(monkeypatch):
    monkeypatch.setattr(runtime, "read_bot_state", lambda: {"alive": True, "state": "RUNNING"})
    monkeypatch.setattr(runtime, "telegram_activity", lambda limit=100: [
        {"status": "ENTREGADO", "timestamp": runtime.now_iso()},
        {"status": "FALLIDO", "timestamp": runtime.now_iso()}])
    assert health.chequear_telegram(None).estado == health.VERDE


def test_telegram_ultimo_fallo_activo_es_rojo(monkeypatch):
    monkeypatch.setattr(runtime, "read_bot_state", lambda: {"alive": True, "state": "RUNNING"})
    monkeypatch.setattr(runtime, "telegram_activity", lambda limit=100: [
        {"status": "FALLIDO", "timestamp": runtime.now_iso()},
        {"status": "ENTREGADO", "timestamp": runtime.now_iso()}])
    assert health.chequear_telegram(None).estado == health.ROJO


def test_tema_moderno_se_inyecta_una_sola_vez():
    source = "<html><head><title>x</title></head><body><h1>x</h1></body></html>"
    rendered = dashboard_ext._rewrite_html("/vivo", source)
    rendered = dashboard_ext._rewrite_html("/vivo", rendered)
    assert rendered.count("porota-theme-v1633") == 1
    assert rendered.count("id='porota-top-nav'") == 1
    assert rendered.count("id='porota-page-shell'") == 1
    assert "min-height:44px" in rendered

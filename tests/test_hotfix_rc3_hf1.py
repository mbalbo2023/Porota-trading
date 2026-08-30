"""Regresiones específicas del hotfix de auditoría RC3."""

import importlib
from decimal import Decimal

import be_paper_engine as engine
import bf_production_paper_observer as observer
import bg_paper_dashboard as dashboard
import au_fee_schedule as fees
from bv_paper_runtime import broker_from_environment


def test_runtime_intradiario_no_inicializa_ni_exige_ia(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_AI_GATE_MODE", raising=False)
    broker = broker_from_environment(engine.PaperStore(str(tmp_path / "paper.db")))
    assert broker.ai_mode == "OFF"
    assert broker.ai_gate is None
    assert broker.require_ai is False


def test_observador_activo_no_importa_cliente_ia():
    source = open(observer.__file__, encoding="utf-8").read()
    assert "from bh_paper_gemini import GeminiPaperGate" not in source
    assert "gemini_gate = GeminiPaperGate()" not in source


def test_factibilidad_default_supera_minimo_de_muestras():
    result = observer._sampling_feasibility(observer.ACTIVE_SYMBOL_LIMIT)
    assert result["feasible"] is True
    assert result["estimated_samples_per_window"] >= result["required_samples"]


def test_market_data_fuera_de_rueda_no_degrada_salud(tmp_path, monkeypatch):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    store.state(session_state="MARKET_CLOSED", ppi_auth="OK")
    observer._health(store, "PPI_PRODUCTION_MARKETDATA", "AMARILLO",
                     "Último estado de la rueda", "PPI")
    monkeypatch.setattr(dashboard, "DB_PATH", store.path)
    monkeypatch.setattr(dashboard, "MODE", "PRODUCTION_PAPER")
    item = next(row for row in dashboard._health_components()
                if row["key"] == "PPI_PRODUCTION_MARKETDATA")
    assert item["state"] == "NO_APLICA"
    assert item["applicable"] is False


def test_pulso_porota_calcula_amplitud_sin_indice_externo(tmp_path, monkeypatch):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setattr(dashboard, "DB_PATH", store.path)
    for symbol, first, second in (("GGAL", "100", "102"), ("YPFD", "200", "198")):
        for minute, price in enumerate((first, second)):
            q = engine.Quote(symbol, "ACCIONES", "A-24HS", Decimal(price),
                Decimal(price), Decimal(price), Decimal("10"), Decimal("10"),
                f"2026-08-28T14:0{minute}:00+00:00", currency="ARS", market="BYMA",
                book_at=f"2026-08-28T14:0{minute}:00+00:00",
                trade_at=f"2026-08-28T14:0{minute}:00+00:00", last_kind="TRADE")
            store.add_quote(q)
    proxy = dashboard._porota_leaders_proxy()
    assert len(proxy["returns"]) == 2
    assert proxy["breadth"] == "1 suben / 1 bajan / 0 sin cambio"
    page = dashboard.financial_page()
    assert "Pulso Porota - líderes" in page
    assert "Pendiente de validación PPI" not in page
    assert "S&amp;P Merval" not in page


def test_iva_sobre_prima_no_depende_del_iva_del_derecho(monkeypatch):
    monkeypatch.setitem(fees.ARANCELES, "PRUEBA", fees.Arancel(
        comision=0, derecho=0, derecho_iva=False,
        derecho_sobre_prima=0.01, prima_iva=True))
    assert fees.costo_por_tramo("PRUEBA") == 0.0121

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bd_ppi_readonly_guard import (ProductionMarketReader, ReadOnlyPolicyViolation,
                                   ReadOnlyTransportGuard)
from be_paper_engine import D, PaperBroker, PaperStore, Quote
import bf_production_paper_observer as observer
from bh_paper_gemini import CURRENT_TEXT_MODELS, rank_models


def quote(symbol="GGAL", price="100", minute=0, bid_size="1000", ask_size="1000"):
    at = (datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc) +
          timedelta(minutes=minute)).isoformat()
    price = D(price)
    return Quote(symbol, "ACCIONES", "A-24HS", price, price-D("0.10"),
                 price+D("0.10"), D(bid_size), D(ask_size), at)


def test_v17_compra_reserva_comision_dentro_de_la_caja(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store, initial_cash="1000", risk_pct="1",
                         max_position_pct="1", max_total_exposure_pct="1",
                         slippage_bps="0")
    q = replace(quote(ask_size="100000"), ask=D("100"))
    assert broker._open(q, D("0.8"), {})[0]
    assert broker._cash() >= 0
    assert D(store.open_positions()[0]["quantity"]) == 9


def test_v17_riesgo_incluye_ambas_comisiones_y_slippage_de_salida(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store, initial_cash="1000", risk_pct="0.01",
                         max_position_pct="1", max_total_exposure_pct="1")
    assert broker._open(quote(ask_size="100000"), D("0.8"), {})[0]
    p = store.open_positions()[0]
    qty = D(p["quantity"])
    expected_exit = (D(p["stop_price"]) * (1-broker.slippage)).quantize(D("0.0001"))
    modeled_loss = ((D(p["entry_price"])-expected_exit)*qty +
                    D(p["entry_cost"]) + broker._cost(expected_exit, qty, "ACCIONES"))
    assert modeled_loss <= D("10")


@pytest.mark.parametrize("change", [
    {"bid_size": D("1")}, {"settlement": "INMEDIATA"},
    {"asset_class": "BONOS"}, {"symbol": "OTRO"},
    {"observed_at": "2026-08-25T13:59:59+00:00"},
    {"observed_at": "fecha-invalida"},
])
def test_v17_cierre_no_inventa_liquidez_ni_mezcla_instrumentos(tmp_path, change):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    assert broker._open(quote(), D("0.8"), {})[0]
    p = store.open_positions()[0]
    assert broker._close(p, replace(quote(price="110", minute=1), **change), "TEST") is False
    assert store.open_positions()
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_v17_cierre_idempotente_ante_reintento_con_posicion_vieja(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    assert broker._open(quote(), D("0.8"), {})[0]
    p = store.open_positions()[0]
    closing = quote(price="110", minute=1)
    assert broker._close(p, closing, "TEST") is True
    before = broker._cash()
    assert broker._close(p, closing, "TEST") is False
    assert broker._cash() == before
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 1


def test_v17_no_entrena_senal_con_otro_plazo_o_clase(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    for i in range(8):
        store.add_quote(replace(quote(price=str(100+i), minute=i), settlement="INMEDIATA"))
        store.add_quote(replace(quote(price=str(100+i), minute=i), asset_class="BONOS"))
    q = quote(price="107", minute=8)
    store.add_quote(q)
    action, _, _, features = PaperBroker(store).decide(q)
    assert action == "HOLD"
    assert features["samples"] == 1


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_v17_decimal_no_finito_se_rechaza_como_dato_invalido(value):
    assert D(value, "-1") == D("-1")


def test_v17_sin_tarifario_no_se_inventa_comision(tmp_path, monkeypatch):
    import au_fee_schedule
    def unavailable(_kind):
        raise ValueError("tarifario no disponible")
    monkeypatch.setattr(au_fee_schedule, "costo_por_tramo", unavailable)
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    with pytest.raises(ValueError, match="tarifario"):
        broker._cost(D("100"), D("1"), "ACCIONES")


def test_v17_prioridad_de_abiertas_incluso_fuera_del_universo(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    broker = PaperBroker(store, max_positions=5)
    for i in range(4):
        assert broker._open(quote(symbol=f"ABIERTA{i}"), D("0.8"), {})[0]
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 2)
    selected, _, _, _ = observer._cycle_symbols(store)
    assert {v[0] for v in selected} == {f"ABIERTA{i}" for i in range(4)}


def test_v17_compra_rechaza_puntas_cruzadas(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    assert broker._open(replace(quote(), bid=D("110"), ask=D("100")), D("0.8"), {})[0] is False
    assert store.open_positions() == []


@pytest.mark.parametrize("change", [{"bid": Decimal("NaN")},
                                   {"ask": Decimal("Infinity")},
                                   {"ask_size": Decimal("sNaN")}])
def test_v17_senal_no_calcula_con_puntas_no_finitas(tmp_path, change):
    store = PaperStore(str(tmp_path / "paper.db"))
    for i in range(8):
        store.add_quote(quote(price=str(100+i), minute=i))
    assert PaperBroker(store).decide(replace(quote(), **change))[0] == "HOLD"


def test_guard_permite_solo_host_https_y_rutas_lectura():
    g = ReadOnlyTransportGuard()
    assert g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current")
    assert g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Account/LoginApi",
                   count_login=True)
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Order/Confirm")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("DELETE", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("GET", "http://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("GET", "https://evil.example/api/1.0/MarketData/Current")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Account/LoginApi",
                count_login=True)


def test_guard_permite_catalogo_e_historicos_pero_no_cuenta_ni_ordenes():
    g = ReadOnlyTransportGuard()
    assert g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/SearchInstrument")
    assert g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Search")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/Account/Accounts")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Order/New")


@pytest.mark.parametrize("word", ["Order/", "Budget", "Confirm", "Cancel", "Transfer", "MassCancel"])
def test_observador_no_contiene_capacidad_operativa(word):
    source = (ROOT / "bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert word not in source


def test_ciclo_compra_y_venta_es_solo_paper(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store, initial_cash="1000000", fee_rate="0.001")
    for i, price in enumerate(("100", "100.2", "100.4", "100.6", "100.8", "101", "101.4", "102")):
        q = quote(price=price, minute=i)
        store.add_quote(q)
        broker.on_quote(q)
    positions = store.open_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["paper_id"].startswith("PAPER-")
    assert p["source"] == "PRODUCTION_PAPER"
    assert D(p["entry_price"]) > quote(price="102", minute=7).ask
    closing = quote(price=str(D(p["target_price"]) + 1), minute=9)
    store.add_quote(closing)
    broker.on_quote(closing)
    assert not store.open_positions()
    closed = store.recent_closed(1)[0]
    assert closed["status"] == "CLOSED"
    assert closed["close_reason"] == "TAKE_PROFIT_PAPER"
    with store.connect() as c:
        fills = [dict(r) for r in c.execute("SELECT * FROM paper_fills ORDER BY id")]
        sample = dict(c.execute("SELECT * FROM paper_learning_samples").fetchone())
    assert [f["side"] for f in fills] == ["BUY_SIMULATED", "SELL_SIMULATED"]
    assert sample["label_timestamp"] is not None
    assert sample["outcome"] in {"WIN", "LOSS", "FLAT"}


def test_no_duplica_decision_ni_posicion_en_mismo_ciclo(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store)
    for i in range(8):
        q = quote(price=str(100+i), minute=i)
        store.add_quote(q)
        broker.on_quote(q)
    before = len(store.open_positions())
    q = quote(price="107", minute=7)
    broker.on_quote(q)
    assert len(store.open_positions()) == before == 1


def test_sin_ask_size_no_hay_fill(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store)
    for i in range(8):
        q = quote(price=str(100+i), minute=i, ask_size="0")
        store.add_quote(q)
        broker.on_quote(q)
    assert store.open_positions() == []


def test_patrimonio_paper_limita_posicion_y_exposicion(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store, initial_cash="1000000", risk_pct="0.50",
                         max_position_pct="0.25", max_total_exposure_pct="0.60")
    for i in range(8):
        q = quote(price=str(100+i), minute=i, ask_size="100000")
        store.add_quote(q)
        broker.on_quote(q)
    position = store.open_positions()[0]
    notional = D(position["entry_price"]) * D(position["quantity"])
    assert notional <= D("250000")
    features = json.loads(position["features_json"])
    assert features["initial_capital_ars"] == "1000000"
    assert features["max_position_pct"] == "0.25"


def test_base_operativa_no_se_abre(tmp_path):
    operational = tmp_path / "trading_system.db"
    operational.write_bytes(b"NO TOCAR")
    PaperStore(str(tmp_path / "observer" / "observer_production.db"))
    assert operational.read_bytes() == b"NO TOCAR"


def test_estado_declara_cero_ordenes_reales(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    with store.connect() as c:
        row = dict(c.execute("SELECT * FROM observer_state").fetchone())
    assert row["mode"] == "PRODUCTION_PAPER"
    assert row["real_orders_sent"] == 0


def test_sync_diario_no_se_duplica(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    assert observer._daily_sync_needed(store)
    today = datetime.now(observer.TZ).date().isoformat()
    with store.connect() as connection:
        for source in ("PPI_PRODUCTION_CATALOG", "PPI_PRODUCTION_HISTORY"):
            connection.execute("INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
                               (source, "VERDE", today, today, 1, "ok"))
    assert not observer._daily_sync_needed(store)


def test_busqueda_ppi_envia_ticker_y_name_no_vacios():
    calls = []
    class Market:
        def search_instrument(self, *args):
            calls.append(args)
            return []
    class Client:
        marketdata = Market()
    reader = object.__new__(ProductionMarketReader)
    reader._ProductionMarketReader__authenticated = True
    reader._ProductionMarketReader__client = Client()
    assert reader.search_instruments("GGAL", "ACCIONES", market="BYMA") == []
    assert calls == [("GGAL", "GGAL", "BYMA", "ACCIONES")]
    with pytest.raises(ValueError):
        reader.search_instruments("", "ACCIONES")


def test_fases_de_mercado_impiden_operar_fuera_de_rueda(monkeypatch):
    monkeypatch.setattr(observer, "_business_day", lambda _day: True)
    closed = datetime(2026, 8, 26, 9, 0, tzinfo=observer.TZ)
    preopen = datetime(2026, 8, 26, 10, 50, tzinfo=observer.TZ)
    opened = datetime(2026, 8, 26, 11, 5, tzinfo=observer.TZ)
    after = datetime(2026, 8, 26, 17, 1, tzinfo=observer.TZ)
    assert observer._market_phase(closed) == "CLOSED"
    assert observer._market_phase(preopen) == "PREOPEN"
    assert observer._market_phase(opened) == "OPEN"
    assert observer._market_phase(after) == "CLOSED"


def test_universo_ampliado_mantiene_derivados_solo_contexto(monkeypatch, tmp_path):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({
        "ACCIONES": {"instrument_type": "ACCIONES", "settlement": "A-24HS",
                      "tickers": ["GGAL", "YPFD"]},
        "FUTUROS": {"instrument_type": "FUTUROS", "settlement": "A-24HS",
                     "tickers": ["DLR"]},
    }), encoding="utf-8")
    monkeypatch.setattr(observer, "WATCHLIST_PATH", watchlist)
    candidates = observer._candidate_universe()
    assert any(row[0] == "YPFD" and row[4] for row in candidates)
    assert any(row[1] == "FUTUROS" and not row[4] for row in candidates)


def test_gemini_es_porton_critico_y_persiste_veredicto(tmp_path):
    class Gate:
        def __init__(self, approve):
            self.approve = approve
        def evaluate(self, *_args):
            return {"decision": "APPROVE" if self.approve else "VETO",
                    "score": 0.91, "veto": not self.approve,
                    "reason": "contrato de prueba", "model": "gemini-test", "raw": {}}

    for approve in (False, True):
        store = PaperStore(str(tmp_path / f"observer-{approve}.db"))
        broker = PaperBroker(store, ai_gate=Gate(approve), require_ai=True)
        for i in range(8):
            q = quote(price=str(100+i), minute=i)
            store.add_quote(q)
            broker.on_quote(q)
        assert bool(store.open_positions()) is approve
        with store.connect() as connection:
            ai = dict(connection.execute(
                "SELECT * FROM ai_shadow_evaluations ORDER BY id DESC LIMIT 1").fetchone())
        assert ai["decision"] == ("APPROVE" if approve else "VETO")
        with store.connect() as connection:
            gate = dict(connection.execute(
                "SELECT * FROM trade_gate_evaluations ORDER BY id DESC LIMIT 1").fetchone())
        assert gate["ai_gate"] == ("APPROVE" if approve else "VETO")
        assert gate["final_result"] == ("OPENED_SIMULATED" if approve else "BLOCKED")


def test_gemini_ausente_cierra_el_porton_paper(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store, require_ai=True)
    for i in range(8):
        q = quote(price=str(100+i), minute=i)
        store.add_quote(q)
        broker.on_quote(q)
    assert store.open_positions() == []


def test_gemini_descarta_modelo_retirado_y_prioriza_inventario_real():
    models = rank_models(
        "gemini-2.5-flash-lite", (),
        ["gemini-3.6-flash", "gemini-3.1-flash-lite"],
    )
    assert models == ["gemini-3.6-flash", "gemini-3.1-flash-lite"]
    assert "gemini-2.5-flash-lite" not in models


def test_gemini_sin_inventario_usa_cadena_estable_actual():
    models = rank_models("gemini-2.5-flash-lite", (), None)
    assert tuple(models[:len(CURRENT_TEXT_MODELS)]) == CURRENT_TEXT_MODELS
    assert models[0] == "gemini-3.7-flash"


def test_comando_gemini_no_abre_ni_requiere_ppi(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    class Gate:
        def healthcheck(self):
            return {"ok": True, "model": "gemini-3.7-flash"}
    with store.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO observer_commands(created_at,command,status) VALUES(?,?,?)",
            (datetime.now(timezone.utc).isoformat(), "GEMINI_PREFLIGHT", "RUNNING"),
        )
        command_id = cursor.lastrowid
    assert observer._run_command(store, None, (command_id, "GEMINI_PREFLIGHT"), Gate()) is None
    with store.connect() as connection:
        row = connection.execute(
            "SELECT status,result FROM observer_commands WHERE id=?", (command_id,)
        ).fetchone()
    assert row[0] == "OK"
    assert "gemini-3.7-flash" in row[1]


def test_catalogo_incorpora_cada_instrumento_devuelto(monkeypatch, tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "_candidate_universe",
                        lambda: [("A", "ACCIONES", "A-24HS", "BYMA", True)])
    class Reader:
        def search_instruments(self, ticker, kind, name=None, market="BYMA"):
            assert ticker and name
            return [{"ticker": "GGAL", "instrumentType": "ACCIONES", "market": "BYMA"},
                    {"ticker": "YPFD", "instrumentType": "ACCIONES", "market": "BYMA"}]
    assert observer._download_catalog(Reader(), store) == 2
    with store.connect() as connection:
        values = {row[0] for row in connection.execute(
            "SELECT ticker FROM candidate_universe WHERE status='AVAILABLE'")}
    assert {"GGAL", "YPFD"}.issubset(values)


def test_lote_por_ciclo_rota_sobre_todo_el_universo(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 3)
    with store.connect() as connection:
        for ticker in ("GGAL", "AL30", "AAPL", "YPFD", "PAMP", "BMA"):
            kind = "CEDEARS" if ticker == "AAPL" else "BONOS" if ticker == "AL30" else "ACCIONES"
            connection.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                               (ticker, kind, "A-24HS", "BYMA", 1, "AVAILABLE", "ok", "2026-08-26"))
    first, total, before, after = observer._cycle_symbols(store)
    with store.connect() as connection:
        connection.execute("""INSERT INTO universe_cycle_metrics
          (started_at,finished_at,eligible_total,selected_count,successful_count,failed_count,
           duration_seconds,cursor_before,cursor_after,recommended_limit,detail)
          VALUES('a','b',?,?,?,?,?,?,?,?,?)""",
          (total, len(first), len(first), 0, 1.0, before, after, 3, "test"))
    second, total2, _, _ = observer._cycle_symbols(store)
    assert total2 == total >= 6
    assert {x[0] for x in first} != {x[0] for x in second}


def test_historicos_usan_universo_completo_no_lote_activo(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    with store.connect() as connection:
        for ticker in ("GGAL", "YPFD", "PAMP", "BMA"):
            connection.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                               (ticker, "ACCIONES", "A-24HS", "BYMA", 1, "AVAILABLE", "ok", "2026-08-26"))
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 2)
    monkeypatch.setattr(observer, "HISTORY_BATCH_LIMIT", 100)
    class Reader:
        def history(self, symbol, *_args):
            return [{"date": "2026-08-25", "close": 1}]
    observer._download_histories(Reader(), store)
    with store.connect() as connection:
        covered = connection.execute("SELECT COUNT(*) FROM production_history").fetchone()[0]
    assert covered >= 4

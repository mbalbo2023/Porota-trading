"""Regresiones históricas reutilizadas para validar la composición efectiva RC6."""
import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import D, PaperBroker, PaperStore, Quote
from bq_exit_policy import PaperSessionPolicy
from porota_mode_manager import paper_settings


AT = "2026-09-01T11:00:00-03:00"


def quote(family="ACCIONES", at=AT, price="100", symbol="GGAL"):
    price = D(price)
    return Quote(symbol, family, "A-24HS", price, price-D("0.1"),
                 price+D("0.1"), D("1000"), D("1000"), at,
                 currency="ARS", market="BYMA", metadata_source="RC6_REGRESSION_TEST",
                 book_at=at, trade_at=at, last_kind="TRADE")


def deployed_broker(path, **overrides):
    cfg = paper_settings({})
    values = dict(
        session_policy=PaperSessionPolicy(),
        clock_fn=lambda: AT,
        economics_mode=cfg["PAPER_ECONOMIC_GATE_MODE"],
        min_net_reward_risk=cfg["PAPER_MIN_NET_REWARD_RISK"],
        stop_loss_pct=cfg["PAPER_STOP_LOSS_PCT"],
        target_gain_pct=cfg["PAPER_TARGET_GAIN_PCT"],
        intraday_fee_rebate=cfg["PAPER_INTRADAY_FEE_REBATE"],
    )
    values.update(overrides)
    return PaperBroker(PaperStore(str(path)), **values)


def test_configuracion_desplegada_no_es_un_noop(tmp_path):
    broker = deployed_broker(tmp_path / "composition.db")
    for family in ("ACCIONES", "CEDEARS", "BONOS", "LETRAS"):
        result = broker._economic_diagnostics(quote(family))
        assert result["modeled_tariff"] == "PPI_INTRADAY_REBATE_ON_SMALLER_LEG"
        assert result["passed"], (family, result)
        assert D(result["net_reward_risk"]) >= D("1.20")
    assert broker.economics_mode == "SHADOW"

    # El observer productivo persiste cada snapshot antes de evaluar la entrada.
    # La regresión debe respetar ese contrato para que DailyRisk pueda marcar
    # posiciones ya abiertas sin relajar DAILY_RISK_STALE_MARKS.
    ggal = quote("ACCIONES", symbol="GGAL")
    broker.store.add_quote(ggal)
    assert broker._open(ggal, D(".9"), {})[0]

    aapl = quote("CEDEARS", symbol="AAPL")
    broker.store.add_quote(aapl)
    assert broker._open(aapl, D(".9"), {})[0]


def test_sin_cierre_intradiario_conserva_costo_completo(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "full.db")),
                         intraday_fee_rebate=True,
                         economics_mode="BINDING", min_net_reward_risk="1.20",
                         stop_loss_pct=".02", target_gain_pct=".05")
    result = broker._economic_diagnostics(quote())
    assert not broker.intraday_fee_rebate
    assert result["modeled_tariff"] == "FULL_PER_LEG_CONSERVATIVE_NO_INTRADAY_REBATE"
    assert not result["passed"]


def test_rebate_se_registra_en_fill_y_ledger_intradiario(tmp_path):
    broker = deployed_broker(tmp_path / "ledger.db")
    opened, _, paper_id = broker._open(quote(), D(".9"), {})
    assert opened
    broker.clock_fn = lambda: "2026-09-01T12:00:00-03:00"
    position = broker.store.open_position("GGAL")
    assert broker._close(position, quote(at=broker.clock_fn(), price="105"), "TEST")
    with broker.store.connect() as c:
        fills = list(c.execute("SELECT side,costs FROM paper_fills ORDER BY id"))
        events = c.execute("""SELECT COUNT(*) FROM paper_events
          WHERE paper_id=? AND event_type='PPI_INTRADAY_FEE_REBATE'""",
          (paper_id,)).fetchone()[0]
    assert D(fills[1]["costs"]) < D(fills[0]["costs"])
    assert events == 1
    assert D(broker.store.recent_closed()[0]["net_pnl"]) > 0


def test_derivado_es_hold_explicado_no_data_error(tmp_path):
    broker = deployed_broker(tmp_path / "derivative.db")
    action, _, reason, features = broker.decide(quote("OPCIONES", symbol="GFGC1000"))
    assert action == "HOLD"
    assert "ciclo financiero específico" in reason
    assert features["family"] == "OPCIONES"


def test_parametros_de_riesgo_rc6_siguen_congelados():
    cfg = paper_settings({"PAPER_RISK_PER_TRADE":"0.9",
                          "PAPER_MAX_OPEN_POSITIONS":"99",
                          "PAPER_ECONOMIC_GATE_MODE":"SHADOW"})
    assert cfg["PAPER_RISK_PER_TRADE"] == "0.002"
    assert cfg["PAPER_MAX_OPEN_POSITIONS"] == "5"
    assert cfg["PAPER_ECONOMIC_GATE_MODE"] == "SHADOW"


def test_lector_refresca_latido_durante_el_recorrido(monkeypatch):
    import types
    import bv_paper_runtime as runtime
    fake_observer = types.SimpleNamespace(normalize_quote=lambda *a, **k: None)
    fake_guard = types.SimpleNamespace(retry_read=lambda fn, retries=1: fn(),
                                       session_invalid=lambda exc: False)
    fake_catalog = types.SimpleNamespace(lookup=lambda *a, **k: {})
    monkeypatch.setitem(sys.modules, "bf_production_paper_observer", fake_observer)
    monkeypatch.setitem(sys.modules, "bd_ppi_readonly_guard", fake_guard)
    monkeypatch.setitem(sys.modules, "bu_instrument_catalog", fake_catalog)
    class Store:
        def exit_positions(self):
            return ([{"paper_id":"P1"}, {"paper_id":"P2"}], [])
    class Policy:
        def execution_error(self, position, at):
            return "OUTSIDE_WINDOW"
    beats = []
    assert runtime.collect_exit_books(object(), Store(), Policy(), AT,
                                      beat=lambda: beats.append(1)) == 0
    assert len(beats) == 2

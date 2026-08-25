import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bd_ppi_readonly_guard import ReadOnlyPolicyViolation, ReadOnlyTransportGuard
from be_paper_engine import D, PaperBroker, PaperStore, Quote


def quote(symbol="GGAL", price="100", minute=0, bid_size="1000", ask_size="1000"):
    at = (datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc) +
          timedelta(minutes=minute)).isoformat()
    price = D(price)
    return Quote(symbol, "ACCIONES", "A-24HS", price, price-D("0.10"),
                 price+D("0.10"), D(bid_size), D(ask_size), at)


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

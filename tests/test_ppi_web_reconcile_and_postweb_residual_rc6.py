import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ops"))

import cu_history_store_v2_hf6 as history_v2
import ppi_postweb_residual_manifest_rc6 as postweb
import ppi_web_history_reconcile_rc6 as webrec


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c


def capture(rows):
    return {
        "symbol": "GGAL",
        "instrument_type": "ACCIONES",
        "market": "BYMA",
        "settlement": "A-48HS",
        "source_url": "https://trading.portfoliopersonal.com/Cotizaciones/Acciones",
        "requested_from": "2026-09-01",
        "requested_to": "2026-09-12",
        "rows": rows,
    }


def valid_web_row(close=108):
    return {"fecha":"2026-09-10T17:00:00-03:00","apertura":100,"high":110,"low":95,"cierre":close,"volumen":12345}


def test_web_aliases_reuse_full_ohlc_validator_without_repair(tmp_path):
    out = webrec.reconcile_capture(capture([valid_web_row()]), apply=False)
    assert out["state"] == "DONE_VALID"
    assert out["valid_rows"] == 1
    assert out["rejected_rows"] == 0

    bad = valid_web_row(close=200)
    out2 = webrec.reconcile_capture(capture([bad]), apply=False)
    assert out2["state"] == "DONE_EMPTY"
    assert out2["valid_rows"] == 0
    assert out2["rejection_reasons"] == {"OHLC_INCONSISTENT": 1}


def test_ppi_web_cannot_replace_ppi_api_canonical(tmp_path):
    store = Store(tmp_path / "history.db")
    api = history_v2.Candle(
        symbol="GGAL", instrument_type="ACCIONES", market="BYMA", settlement="A-48HS",
        date="2026-09-10", open=100, high=110, low=95, close=105, volume=1000,
        source="PPI_PRODUCTION_HISTORY", observed_at="2026-09-10T20:00:00+00:00"
    )
    history_v2.append_candle(store, api)
    result = webrec.reconcile_capture(capture([valid_web_row(close=108)]), history_store=store, apply=True)
    assert result["protected_by_precedence"] == 1
    with store.connect() as c:
        row = c.execute("SELECT source,close,source_rank FROM history_canonical_v2").fetchone()
    assert row["source"] == "PPI_PRODUCTION_HISTORY"
    assert row["close"] == 105
    assert row["source_rank"] == 10


def test_ppi_web_outranks_iol_fallback(tmp_path):
    store = Store(tmp_path / "history.db")
    iol = history_v2.Candle(
        symbol="GGAL", instrument_type="ACCIONES", market="BYMA", settlement="A-48HS",
        date="2026-09-10", open=99, high=109, low=94, close=104, volume=900,
        source="IOL", observed_at="2026-09-10T19:00:00+00:00"
    )
    history_v2.append_candle(store, iol)
    result = webrec.reconcile_capture(capture([valid_web_row(close=108)]), history_store=store, apply=True)
    assert result["canonical_updates"] == 1
    with store.connect() as c:
        row = c.execute("SELECT source,close,source_rank FROM history_canonical_v2").fetchone()
    assert row["source"] == "PPI_WEB_HISTORY"
    assert row["close"] == 108
    assert row["source_rank"] == 15


def test_postweb_manifest_only_for_unresolved():
    base = {"symbol":"GGAL","instrument_type":"ACCIONES","market":"BYMA","settlement":"A-48HS"}
    rows = [
        {**base, "state":"DONE_VALID", "provider_rows":10, "valid_rows":10, "rejected_rows":0},
        {**base, "symbol":"YPFD", "state":"DONE_PARTIAL", "provider_rows":10, "valid_rows":8, "rejected_rows":2},
        {**base, "symbol":"PAMP", "state":"DONE_EMPTY", "provider_rows":0, "valid_rows":0, "rejected_rows":0},
        {**base, "symbol":"ALUA", "state":"DONE_EMPTY", "provider_rows":3, "valid_rows":0, "rejected_rows":3},
        {**base, "symbol":"TXAR", "state":"ERROR", "provider_rows":0, "valid_rows":0, "rejected_rows":0},
    ]
    out = postweb.build_manifest(rows)
    assert [x["symbol"] for x in out] == ["YPFD", "PAMP", "ALUA", "TXAR"]
    assert [x["residual_class"] for x in out] == [
        "PPI_WEB_PARTIAL_VALID", "PPI_WEB_NO_ROWS", "PPI_WEB_PROVIDER_INVALID", "PPI_WEB_ERROR"
    ]
    assert all(x["fallback_source"] == "IOL" for x in out)


def test_unknown_web_state_fails_closed():
    row = {"symbol":"GGAL","instrument_type":"ACCIONES","market":"BYMA","settlement":"A-48HS","state":"RUNNING"}
    try:
        postweb.build_manifest([row])
    except ValueError as exc:
        assert "UNKNOWN_WEB_STATE" in str(exc)
    else:
        raise AssertionError("expected fail-closed error")

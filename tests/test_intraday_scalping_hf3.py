"""Contratos offline del scanner intradiario HF3."""
from datetime import datetime, timedelta
import json
from json import JSONDecodeError

import pytest

from be_paper_engine import PaperStore
import bg_paper_dashboard as dashboard
import cf_intraday_scalping as scalping
import bu_instrument_catalog as catalog
import bd_ppi_readonly_guard as readonly


START = datetime.fromisoformat("2026-09-01T10:30:00-03:00")


def payload(count, *, changed=None):
    result = []
    for index in range(count):
        price = 100 + index / 10
        volume = 20 if index % 2 == 0 else 10
        if changed is not None and index == changed:
            volume += 1
        result.append({"date": (START + timedelta(minutes=index)).isoformat(),
                       "price": price, "volume": volume})
    return result


def record(**changes):
    return dict(ticker="GGAL",instrument_type="ACCIONES",market="BYMA",
                currency="ARS",settlement="A-24HS",capability="READY_PAPER_SPOT",
                status="AVAILABLE") | changes


@pytest.fixture
def store(tmp_path):
    value = PaperStore(str(tmp_path / "paper.db"))
    catalog.init_schema(value)
    scalping.init_schema(value)
    return value


def test_payload_observado_es_minuto_precio_volumen_sin_inventar_ohlc():
    points = scalping.normalize_payload(payload(10),
        received_at="2026-09-01T10:40:00-03:00")
    assert len(points) == 10
    assert points[0][1:] == (scalping.Decimal("100.0"), scalping.Decimal("20"))
    assert points[-1][0].endswith("+00:00")


def test_volumen_se_confirma_solo_con_solapamiento_estable_y_minuto_nuevo(store):
    first = scalping.normalize_payload(payload(10),received_at="2026-09-01T10:40:00-03:00")
    result1 = scalping.persist_payload(store,record(),first,
                                       received_at="2026-09-01T10:40:00-03:00")
    assert result1["state"] == "PENDING_LIVE_CONFIRMATION"
    second = scalping.normalize_payload(payload(11),received_at="2026-09-01T10:41:00-03:00")
    result2 = scalping.persist_payload(store,record(),second,
                                       received_at="2026-09-01T10:41:00-03:00")
    assert result2["state"] == "CONFIRMED_INTERVAL_VOLUME"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ppi_intraday_points").fetchone()[0] == 11


def test_modificar_un_minuto_cerrado_frena_el_contrato(store):
    first = scalping.normalize_payload(payload(10),received_at="2026-09-01T10:40:00-03:00")
    scalping.persist_payload(store,record(),first,received_at="2026-09-01T10:40:00-03:00")
    changed = scalping.normalize_payload(payload(11,changed=0),received_at="2026-09-01T10:41:00-03:00")
    result = scalping.persist_payload(store,record(),changed,
                                      received_at="2026-09-01T10:41:00-03:00")
    assert result["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"


def test_rotacion_no_excluye_familias_ni_mercados(store):
    rows = [
        record(),
        record(ticker="AAPLD",instrument_type="CEDEARS",currency="USD_MEP"),
        record(ticker="DLR/NOV26",instrument_type="FUTUROS",market="ROFEX",
               currency="USD",settlement="INMEDIATA",capability="NEEDS_FUTURES_MARGIN_AND_CONTRACT"),
    ]
    with store.connect() as connection:
        for row in rows:
            connection.execute("""INSERT INTO financial_instrument_catalog VALUES(
              ?,?,?,?,?,'PPI_FIELD','test',?,'run','AVAILABLE',?,?)""",
              (row["ticker"],row["instrument_type"],row["market"],row["currency"],row["settlement"],
               "2026-09-01T10:00:00-03:00",row["capability"],json.dumps(row)))
    selected, _, total = scalping.select_batch(store,limit=8)
    assert total == 3
    assert {row["market"] for row in selected} == {"BYMA","ROFEX"}
    assert {row["instrument_type"] for row in selected} == {"ACCIONES","CEDEARS","FUTUROS"}


def test_dashboard_tiene_solapa_y_declara_scanner_sin_fills(monkeypatch, store):
    monkeypatch.setattr(dashboard,"DB_PATH",store.path)
    body = dashboard.scalping_page()
    assert "href='/scalping'>Scalping</a>" in body
    assert "ACTIVE_OBSERVE" in body
    assert "no genera fills" in body
    assert "Órdenes reales" in body
    assert "Economía matemática SHADOW" in dashboard.health_page()


def test_configuracion_rc6_es_shadow_y_reduce_riesgo(monkeypatch, store):
    from bv_paper_runtime import broker_from_environment
    monkeypatch.delenv("PAPER_ECONOMIC_GATE_MODE",raising=False)
    monkeypatch.delenv("PAPER_RISK_PER_TRADE",raising=False)
    monkeypatch.delenv("PAPER_MAX_OPEN_POSITIONS",raising=False)
    broker = broker_from_environment(store)
    assert broker.economics_mode == "SHADOW"
    assert broker.risk_pct == scalping.Decimal("0.002")
    assert broker.max_positions == 5
    import porota_mode_manager as mode
    settings = mode.paper_settings({"PAPER_ECONOMIC_GATE_MODE":"SHADOW",
                                    "PAPER_RISK_PER_TRADE":"0.5"})
    assert settings["PAPER_ECONOMIC_GATE_MODE"] == "SHADOW"
    assert settings["PAPER_RISK_PER_TRADE"] == "0.002"


def test_salud_binding_es_roja_si_una_falla_economica_igual_abrio(monkeypatch, store):
    import bf_production_paper_observer as observer
    monkeypatch.setenv("PAPER_ECONOMIC_GATE_MODE","BINDING")
    observer._support_schema(store)
    with store.connect() as connection:
        connection.execute("""INSERT INTO trade_gate_evaluations
          (evaluated_at,decision_key,symbol,technical_gate,ai_gate,
           patrimonial_gate,final_result,reason,paper_id,detail_json)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (scalping._stamp(datetime.now().astimezone()),"hf3","GGAL","APPROVE","NOT_USED",
           "APPROVE","OPENED_SIMULATED","test",None,
           json.dumps({"economics":{"passed":False}})))
    observer._publish_economic_shadow_health(store)
    with store.connect() as connection:
        row=connection.execute("SELECT state,detail FROM api_health WHERE component='PAPER_ECONOMIC_GATE_SHADOW'").fetchone()
    assert row["state"] == "ROJO"
    assert "BINDING bloquea" in row["detail"]


def test_lectura_vacia_se_reintenta_una_sola_vez_sin_login():
    calls=[]
    def read():
        calls.append(1)
        if len(calls) == 1:
            raise JSONDecodeError("Expecting value", "", 0)
        return {"book":"ok"}
    pauses=[]
    assert readonly.retry_read(read,pause=pauses.append) == {"book":"ok"}
    assert len(calls) == 2 and pauses == [0.35]


def test_unauthorized_invalida_sesion_y_no_se_reintenta():
    calls=[]
    def read():
        calls.append(1)
        raise Exception("Unauthorized")
    with pytest.raises(Exception,match="Unauthorized"):
        readonly.retry_read(read,pause=lambda _:None)
    assert len(calls) == 1
    assert readonly.session_invalid(Exception("Unauthorized")) is True

"""Regresiones de reloj, fuente temporal, ledger y procesos de salidas paper."""
import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore, STRATEGY_VERSION
from bm_exit_supervisor import PositionExitSupervisor, admission_error
from bq_exit_policy import PaperSessionPolicy
from bv_paper_runtime import ChildProcesses, collect_exit_books, run_clock, run_reader
from test_production_paper_v1634 import quote
import bf_production_paper_observer as observer


def key(p):
    return tuple(p[k] for k in ("symbol","asset_class","settlement","currency","market"))


@pytest.fixture
def position(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    q = quote(at="2026-08-28T11:00:00-03:00")
    assert broker._open(q,D("0.8"),{})[0]
    return broker, store.open_positions()[0], q


@pytest.mark.parametrize("changes,code", [
    ({"book_at": None}, "BOOK_TIME_MISSING"),
    ({"book_at": "2026-08-28T10:55:00-03:00"}, "BOOK_STALE"),
    ({"book_at": "2026-08-28T11:00:01-03:00"}, "BOOK_TIME_FUTURE"),
    ({"book_at": "2026-08-28T11:00:00"}, "INVALID_OR_NAIVE_TIMESTAMP"),
    ({"trade_at": None}, "TRADE_TIME_MISSING"),
    ({"last_kind": "MIDPOINT"}, "LAST_IS_NOT_A_TRADE"),
    ({"observed_at": "2026-08-28T11:00:01-03:00"}, "RECEIPT_STALE_OR_FUTURE"),
])
def test_no_rejuvenece_datos_con_hora_de_recepcion(tmp_path,changes,code):
    q = replace(quote(at="2026-08-28T11:00:00-03:00"),**changes)
    at = "2026-08-28T11:00:00-03:00"
    assert q.time_error(at,require_trade=True) == code
    broker = PaperBroker(PaperStore(str(tmp_path / "p.db")),clock_fn=lambda: at)
    assert not broker._open(q,D("0.8"),{})[0]


def test_libro_fresco_permite_salir_con_ultimo_negocio_antiguo(position):
    broker,p,q = position
    q = quote(price="110",at="2026-08-28T11:01:00-03:00")
    q = replace(q,trade_at="2026-08-27T15:00:00-03:00")
    assert q.time_error(q.observed_at,require_trade=True) == "TRADE_STALE"
    assert broker._close(p,q,"TEST")


def test_normalizador_conserva_fechas_y_no_fabrica_negocio():
    source = "2026-08-28T14:00:00Z"
    q = observer.normalize_quote("GGAL","ACCIONES","A-24HS",{},
        {"date": source,"bids":[{"price":99,"quantity":10}],"offers":[{"price":101,"quantity":10}]})
    assert q.last == 0 and q.last_kind == "UNAVAILABLE"
    assert q.book_at == source and q.trade_at is None


def test_senal_excluye_duplicados_y_datos_no_disponibles_al_decidir(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    q = quote()
    for _ in range(9):
        store.add_quote(q)
    store.add_quote(replace(quote(minute=1),observed_at=quote(minute=3).observed_at))
    store.add_quote(replace(quote(minute=2),last_kind="MIDPOINT"))
    assert PaperBroker(store).decide(q)[3]["samples"] == 1
    assert store.signal_prices(q,quote(minute=2).observed_at) == [D(100)]


def test_demora_previa_no_ejecuta_con_cotizacion_vieja_y_ia_off(tmp_path):
    """RC6: la frescura se revalida al admitir, sin depender de IA intradiaria."""
    store = PaperStore(str(tmp_path / "p.db"))
    q = quote(minute=7)
    clock = [q.observed_at]
    broker = PaperBroker(
        store,
        ai_gate=None,
        require_ai=False,
        ai_mode="OFF",
        economics_mode="BINDING",
        clock_fn=lambda: clock[0],
    )
    store.add_quote(q)
    clock[0] = quote(minute=10).observed_at

    opened, reason, paper_id = broker._open(q, D("0.9"), {})

    assert opened is False
    assert paper_id is None
    assert "STALE" in reason
    assert not store.open_positions()


def test_max_hold_sin_datos_persiste_y_se_ejecuta_despues_de_reiniciar(position):
    broker,p,q = position
    at = "2026-08-28T14:01:00-03:00"
    sup = PositionExitSupervisor(broker,clock_fn=lambda:at)
    assert sup.tick({})[0].state == "EXIT_PENDING_NO_QUOTE"
    fresh = quote(at=at)
    restarted = PaperBroker(PaperStore(broker.store.path))
    sup = PositionExitSupervisor(restarted,clock_fn=lambda:at,max_hold_minutes=999)
    result = sup.tick({key(p):fresh})[0]
    assert result.state == "CLOSED" and result.cause == "MAX_HOLD_PAPER"
    assert sup.tick({key(p):fresh}) == []
    with broker.store.connect() as c:
        assert c.execute("SELECT state FROM paper_exit_intents").fetchone()[0] == "CLOSED"
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 1


def test_stop_sin_profundidad_no_se_olvida_cuando_recupera_precio(position):
    broker,p,_ = position
    clock = ["2026-08-28T11:01:00-03:00"]
    sup = PositionExitSupervisor(broker,clock_fn=lambda:clock[0])
    illiquid = quote(price="90",bid_size="0",at=clock[0])
    result = sup.tick({key(p):illiquid})[0]
    assert (result.state,result.cause) == ("EXIT_PENDING_NO_LIQUIDITY","STOP_PAPER")
    assert broker.store.open_positions()
    clock[0] = "2026-08-28T11:02:00-03:00"
    recovered = quote(at=clock[0])
    assert sup.tick({key(p):recovered})[0].state == "CLOSED"
    assert broker.store.recent_closed()[0]["close_reason"] == "STOP_PAPER"


@pytest.mark.parametrize("mode", ["false","true_without_fill","exception"])
def test_callback_sin_fill_nunca_se_declara_cerrado(position,mode):
    broker,p,_ = position
    q = quote(price="90",minute=1,at="2026-08-28T11:01:00-03:00")
    def callback(*args,**kwargs):
        if mode == "exception":
            raise RuntimeError("fixture")
        return mode == "true_without_fill"
    sup = PositionExitSupervisor(broker,clock_fn=lambda:q.observed_at,close_fn=callback)
    v = sup.tick({key(p):q})[0]
    assert v.state == "EXIT_PENDING_EXECUTION"
    assert len(broker.store.open_positions()) == 1
    with broker.store.connect() as c:
        assert c.execute("SELECT attempts FROM paper_exit_intents").fetchone()[0] == 1


@pytest.mark.parametrize("at",["2026-08-28T17:05:00-03:00",
                              "2026-08-29T11:00:00-03:00","2027-08-30T11:00:00-03:00"])
def test_no_fabrica_fill_con_mercado_cerrado_ni_calendario_desconocido(position,at):
    broker,p,_ = position
    broker.session_policy = PaperSessionPolicy()
    q = quote(price="90",at=at)
    sup = PositionExitSupervisor(broker,clock_fn=lambda:at,session_policy=broker.session_policy)
    assert sup.tick({key(p):q})[0].state == "EXIT_PENDING_MARKET_CLOSED"
    assert not broker._close(p,q,"TEST",as_of=at)


def test_1655_sigue_dentro_de_rueda_y_un_stop_puede_cerrar(position):
    broker,p,_ = position
    at = "2026-08-28T16:55:00-03:00"
    broker.session_policy = PaperSessionPolicy()
    q = quote(price="90",at=at)
    sup = PositionExitSupervisor(broker,clock_fn=lambda:at,session_policy=broker.session_policy)
    verdict = sup.tick({key(p):q})[0]
    assert verdict.state == "CLOSED"
    assert verdict.cause == "EOD_PAPER"


def test_eod_no_aplica_a_cauciones_ni_futuros_y_frena_entradas_spot(position):
    broker,p,_ = position
    policy = PaperSessionPolicy()
    at = "2026-08-28T16:30:00-03:00"
    assert policy.admission_error(quote(at=at),at) == "EOD_NO_NEW_ENTRIES"
    assert policy.execution_error(quote(at=at),at) == ""
    assert not policy.exit_due(p | {"asset_class":"CAUCIONES"},at)
    assert not policy.exit_due(p | {"asset_class":"FUTUROS","market":"ROFEX"},at)


def test_option_session_is_t0_and_uses_expiry_day_1530_cutoff(position):
    from bs_instrument_contracts import InstrumentContract
    from dataclasses import replace
    policy=PaperSessionPolicy()
    contract=InstrumentContract(
        "GFGC7000OC","OPCIONES","ARS","BYMA","INMEDIATA",
        D("100"),D("1"),"IOL_OPTIONS_CHAIN+BYMA",
        expires_at="2026-10-16T15:30:00-03:00",
        underlying="GGAL",strike=D("7000"),option_right="CALL")
    q=replace(
        quote(at="2026-10-16T14:59:00-03:00"),
        symbol="GFGC7000OC",asset_class="OPCIONES",settlement="INMEDIATA",
        contract=contract)
    assert policy.execution_error(q,q.observed_at) == ""
    assert policy.admission_error(q,q.observed_at) == ""
    at="2026-10-16T15:00:00-03:00"
    assert policy.admission_error(q,at) == "EOD_NO_NEW_ENTRIES"
    option_position=position[1] | {
        "symbol":"GFGC7000OC","asset_class":"OPCIONES","settlement":"INMEDIATA",
        "features_json":json.dumps({"financial_contract":{
            "expires_at":"2026-10-16T15:30:00-03:00"}}),
        "opened_at":"2026-10-16T11:00:00-03:00",
    }
    assert policy.exit_due(option_position,"2026-10-16T15:20:00-03:00")
    assert policy.execution_error(q,"2026-10-16T15:30:00-03:00") == "OPTION_EXPIRED"


def test_option_without_aware_expiry_never_enters_session(position):
    policy=PaperSessionPolicy()
    p=position[1] | {
        "asset_class":"OPCIONES","settlement":"INMEDIATA",
        "features_json":json.dumps({"financial_contract":{"expires_at":"2026-10-16T15:30:00"}}),
    }
    assert policy.execution_error(p,"2026-10-16T14:00:00-03:00") == "OPTION_EXPIRY_UNAVAILABLE"


def test_supervisor_silencioso_o_posicion_pendiente_bloquea_nuevas_compras(position):
    broker,p,q = position
    assert admission_error(broker.store,q.observed_at) == "EXIT_SUPERVISOR_UNAVAILABLE"
    sup = PositionExitSupervisor(broker,clock_fn=lambda:q.observed_at)
    sup.tick({key(p):q})
    assert admission_error(broker.store,q.observed_at) == "EXIT_READER_UNAVAILABLE"
    with broker.store.connect() as c:
        c.execute("INSERT INTO paper_exit_reader_state VALUES(1,?,'READY','fixture')",(q.observed_at,))
    assert admission_error(broker.store,q.observed_at) == ""
    assert admission_error(broker.store,"2026-08-28T11:00:21-03:00") == "EXIT_SUPERVISOR_STALE"
    sup.tick({})
    assert admission_error(broker.store,q.observed_at) == "OPEN_POSITION_NEEDS_SUPERVISION_OR_EXIT"


def test_lector_salidas_solo_pide_book_y_guarda_fecha_fuente(position,monkeypatch):
    broker,p,q = position
    observer._support_schema(broker.store)
    monkeypatch.setattr(observer,"now_iso",lambda:q.observed_at)
    raw = {"ticker":"GGAL","type":"ACCIONES","market":"BYMA","currency":"Pesos"}
    record = observer.financial_catalog.normalize_record(raw,"A-24HS",q.observed_at,"test")
    with broker.store.connect() as c:
        observer.financial_catalog.persist(c,record)
    class Reader:
        def book(self,symbol,kind,settlement):
            assert (symbol,kind,settlement) == ("GGAL","ACCIONES","A-24HS")
            return {"date":q.book_at,"bids":[{"price":99,"quantity":1000}],"offers":[{"price":101,"quantity":1000}]}
    assert collect_exit_books(Reader(),broker.store,PaperSessionPolicy(),q.observed_at) == 0
    saved = broker.store.latest_quote(p)
    assert saved.book_at == q.book_at and saved.last_kind == "UNAVAILABLE"


def test_reloj_avanza_mientras_cuatro_procesos_hijos_estan_bloqueados(position,monkeypatch):
    broker,p,_ = position
    monkeypatch.setenv("PAPER_MAX_HOLD_MINUTES","180")
    command = [sys.executable,"-c","import time; time.sleep(60)"]
    children = ChildProcesses({"scanner":command,"reader":command,"notifications":command,"candles":command}, startup_grace_seconds=0)
    stop = threading.Event()
    thread = threading.Thread(target=run_clock,args=(broker.store,children,stop),
        kwargs={"clock_fn":lambda:"2026-08-28T14:01:00-03:00","interval":0.02})
    thread.start()
    try:
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            with broker.store.connect() as c:
                result = c.execute("SELECT state FROM paper_exit_intents").fetchone()
            if result:
                break
            time.sleep(.02)
        assert result[0] == "EXIT_PENDING_NO_QUOTE"
        assert len(children.processes) == 4
        assert all(p.poll() is None for p in children.processes.values())
    finally:
        stop.set()
        thread.join(timeout=12)
    assert not thread.is_alive()
    assert all(p.poll() is not None for p in children.processes.values())

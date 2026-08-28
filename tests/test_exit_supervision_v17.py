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


def test_aprobacion_ia_lenta_no_ejecuta_con_libro_viejo(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    clock = [quote(minute=7).observed_at]
    class SlowGate:
        def evaluate(self,*_):
            clock[0] = quote(minute=10).observed_at
            return {"decision":"APPROVE","score":.9,"veto":False,"reason":"fixture"}
    broker = PaperBroker(store,ai_gate=SlowGate(),require_ai=True,clock_fn=lambda:clock[0])
    for i in range(8):
        q = quote(price=str(100+i),minute=i)
        store.add_quote(q)
    broker.on_quote(q)
    assert not store.open_positions()
    with store.connect() as c:
        gate = c.execute("SELECT * FROM trade_gate_evaluations").fetchone()
    assert gate["ai_gate"] == "APPROVE" and gate["final_result"] == "BLOCKED"
    assert "STALE" in gate["reason"]


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


@pytest.mark.parametrize("at",["2026-08-28T16:55:00-03:00","2026-08-28T17:05:00-03:00",
                              "2026-08-29T11:00:00-03:00","2027-08-30T11:00:00-03:00"])
def test_no_fabrica_fill_con_mercado_cerrado_ni_calendario_desconocido(position,at):
    broker,p,_ = position
    broker.session_policy = PaperSessionPolicy()
    q = quote(price="90",at=at)
    sup = PositionExitSupervisor(broker,clock_fn=lambda:at,session_policy=broker.session_policy)
    assert sup.tick({key(p):q})[0].state == "EXIT_PENDING_MARKET_CLOSED"
    assert not broker._close(p,q,"TEST",as_of=at)


def test_eod_no_aplica_a_cauciones_o_derivados_y_frena_entradas(position):
    broker,p,_ = position
    policy = PaperSessionPolicy()
    at = "2026-08-28T16:30:00-03:00"
    assert policy.admission_error(quote(at=at),at) == "EOD_NO_NEW_ENTRIES"
    assert policy.execution_error(quote(at=at),at) == ""
    assert not policy.exit_due(p | {"asset_class":"CAUCIONES"},at)
    assert not policy.exit_due(p | {"asset_class":"FUTUROS","market":"ROFEX"},at)


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


def test_reloj_avanza_mientras_tres_procesos_hijos_estan_bloqueados(position):
    broker,p,_ = position
    command = [sys.executable,"-c","import time; time.sleep(60)"]
    children = ChildProcesses({"scanner":command,"reader":command,"notifications":command})
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
        assert len(children.processes) == 3
        assert all(p.poll() is None for p in children.processes.values())
    finally:
        stop.set()
        thread.join(timeout=12)
    assert not thread.is_alive()
    assert all(p.poll() is not None for p in children.processes.values())
    with broker.store.connect() as c:
        assert c.execute("SELECT state FROM paper_supervisor_state").fetchone()[0] == "STOPPED"


def test_umbral_no_aprende_de_operaciones_de_otra_version(position):
    broker,p,q = position
    assert broker._close(p,quote(price="110",at="2026-08-28T11:01:00-03:00"),"TEST")
    with broker.store.connect() as c:
        row = dict(c.execute("SELECT * FROM paper_positions").fetchone())
        for i in range(6):
            legacy = row | {"paper_id":f"OLD-{i}","strategy_version":"paper-momentum-v1"}
            c.execute("INSERT INTO paper_positions("+",".join(legacy)+") VALUES("+",".join("?" for _ in legacy)+")",tuple(legacy.values()))
    assert broker.threshold() == D("0.62")
    assert broker.store.recent_closed(strategy_version=STRATEGY_VERSION)[0]["paper_id"] == p["paper_id"]


def test_marca_vencida_no_borra_perdida_y_panel_muestra_calidad(position,monkeypatch):
    import bg_paper_dashboard as dashboard
    broker,p,_ = position
    fresh = quote(price="90",at="2026-08-28T11:01:00-03:00")
    values = broker.mark_equity({key(p):fresh},as_of=fresh.observed_at)
    assert values["ARS"]["unrealized_pnl"] < 0
    stale = broker.mark_equity({},as_of="2026-08-28T11:05:00-03:00")
    assert stale["ARS"]["unrealized_pnl"] == values["ARS"]["unrealized_pnl"]
    monkeypatch.setattr(dashboard,"DB_PATH",broker.store.path)
    page = dashboard.motor_page()
    assert "STALE_MARKS" in page and "Supervisión de salidas" in page
    with broker.store.connect() as c:
        assert c.execute("SELECT stale_positions FROM paper_valuation_quality WHERE currency='ARS'").fetchone()[0] == 1


def test_quote_invalida_nueva_no_resucita_libro_anterior(position):
    broker,p,q = position
    broker.store.add_quote(q)
    broker.store.add_quote(replace(q,currency=None,book_at=None))
    latest = broker.store.latest_quote(p)
    assert latest.currency == "UNKNOWN" and latest.book_at is None
    sup = PositionExitSupervisor(broker,clock_fn=lambda:q.observed_at)
    assert sup.tick()[0].state == "WATCH_IDENTITY_MISMATCH"


def test_reinicio_hijo_con_cooldown_sin_reiniciar_el_otro():
    clock = [0]
    created = []
    class FakeProcess:
        code = None
        def poll(self): return self.code
    def spawn(*args,**kwargs):
        p = FakeProcess()
        created.append(p)
        return p
    children = ChildProcesses({"scanner":["scanner"],"reader":["reader"]},spawn=spawn,clock=lambda:clock[0])
    children.poll()
    reader = children.processes["reader"]
    children.processes["scanner"].code = 1
    children.poll()
    clock[0] = 29
    children.poll()
    assert "scanner" not in children.processes
    clock[0] = 30
    children.poll()
    assert len(created) == 3 and children.processes["reader"] is reader


def test_selector_simulacion_usa_runtime_y_una_configuracion_monetaria(tmp_path,monkeypatch):
    import porota_mode_manager as manager
    monkeypatch.setattr(manager,"ROOT",tmp_path)
    monkeypatch.setattr(manager,"DATA",tmp_path / "data")
    (tmp_path / "data/diagnosticos").mkdir(parents=True)
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets/ppi_production.json").write_text("{}")
    monkeypatch.setattr(manager,"env_file",lambda:{"GEMINI_API_KEY":"fixture", "PAPER_INITIAL_CAPITAL_ARS":"50000",
                                                  "PAPER_INITIAL_CAPITAL_USD_MEP":"250"})
    monkeypatch.setattr(manager,"stop_engines",lambda:None)
    monkeypatch.setattr(manager,"start_dashboard",lambda _:None)
    monkeypatch.setattr(manager,"write_mode",lambda *a,**k:None)
    monkeypatch.setattr(manager,"notify",lambda _:"TEST")
    calls = []
    monkeypatch.setattr(manager,"run",lambda *args,**kwargs:calls.append(args))
    manager.simulation()
    assert calls[0][-1] == "bv_paper_runtime.py"
    env_path = Path(calls[0][calls[0].index("--env-file")+1])
    content = env_path.read_text()
    assert "PAPER_INITIAL_CAPITAL_ARS=50000" in content
    assert "PAPER_INITIAL_CAPITAL_USD_MEP=250" in content
    assert "PAPER_INITIAL_CAPITAL_ARS=1000000" not in calls[0]


def test_snapshots_viejos_no_ingresan_al_aprendizaje_por_migracion(tmp_path):
    store = PaperStore(str(tmp_path / "old.db"))
    with store.connect() as c:
        c.execute("""INSERT INTO market_snapshots(source,observed_at,symbol,asset_class,settlement,
          last,bid,ask,bid_size,ask_size,currency,market)
          VALUES('PRODUCTION_PAPER','2026-08-25T14:00:00Z','GGAL','ACCIONES','A-24HS',
          '100','99','101','1000','1000','ARS','BYMA')""")
    q = quote()
    assert store.signal_prices(q,q.observed_at) == []


@pytest.mark.parametrize("login_failure", [False,True])
def test_lector_prevalida_sesion_sin_posiciones_y_no_reintenta_login_en_rafaga(tmp_path,monkeypatch,login_failure):
    import bd_ppi_readonly_guard as guard
    store = PaperStore(str(tmp_path / "p.db"))
    calls, health = [], []
    class Reader:
        def __init__(self,*args,**kwargs): pass
        def login_once(self):
            calls.append("login")
            if login_failure:
                raise RuntimeError("fixture")
        def close(self): calls.append("close")
    class Stop:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self,seconds):
            with store.connect() as c:
                health.append(c.execute("SELECT state FROM paper_exit_reader_state").fetchone()[0])
            self.stopped = True
    monkeypatch.setattr(guard,"ProductionMarketReader",Reader)
    monkeypatch.setattr(observer,"_secret",lambda:("fixture","fixture"))
    monkeypatch.setattr(observer,"_market_phase",lambda:"OPEN")
    run_reader(store,Stop())
    assert calls == ["login","close"]
    assert health == ["COOLDOWN" if login_failure else "READY"]
    with store.connect() as c:
        assert c.execute("SELECT state FROM paper_exit_reader_state").fetchone()[0] == "STOPPED"

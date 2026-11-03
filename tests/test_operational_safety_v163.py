import time
import pytest


class DummyNotifier:
    def __init__(self):
        self.errors = []

    def notify_error(self, message):
        self.errors.append(message)

    def notify_recovery(self, _service):
        pass


def _fake_ppi(error=None):
    calls = {"login": 0}

    class Account:
        def login_api(self, _key, _secret):
            calls["login"] += 1
            if error:
                raise RuntimeError(error)

    class FakePPI:
        def __init__(self, sandbox=True):
            self.account = Account()

    return FakePPI, calls


def test_ppi_rate_limit_hace_un_solo_login(monkeypatch, tmp_path):
    monkeypatch.setenv("PPI_API_KEY", "key-test")
    monkeypatch.setenv("PPI_API_SECRET", "secret-test")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    import c_ppi_client as module
    monkeypatch.setattr(module.ac_db, "DB_PATH", str(tmp_path / "db.sqlite"))

    fake, calls = _fake_ppi("429 API calls quota exceeded maximum admitted 10 per 1h")
    monkeypatch.setattr(module, "PPI", fake)
    monkeypatch.setattr(module.ResilientPPIClient, "_configure_sandbox_sdk", lambda self, candidate: None)
    client = module.ResilientPPIClient(DummyNotifier())

    assert calls["login"] == 1
    assert client.is_authenticated() is False
    assert client.login() is False
    assert client._call_with_retry(lambda: object(), "probe") is None
    assert calls["login"] == 1
    assert client.auth_status()["cooldown_remaining_seconds"] > 3500


def test_ppi_login_exitoso_expone_estado_local(monkeypatch, tmp_path):
    monkeypatch.setenv("PPI_API_KEY", "key-test")
    monkeypatch.setenv("PPI_API_SECRET", "secret-test")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    import c_ppi_client as module
    monkeypatch.setattr(module.ac_db, "DB_PATH", str(tmp_path / "db.sqlite"))

    fake, calls = _fake_ppi()
    monkeypatch.setattr(module, "PPI", fake)
    monkeypatch.setattr(module.ResilientPPIClient, "_configure_sandbox_sdk", lambda self, candidate: None)
    client = module.ResilientPPIClient(DummyNotifier())

    assert calls["login"] == 1
    assert client.is_authenticated() is True
    assert client.auth_status()["state"] == "OK"


def test_health_ppi_no_hace_llamadas_de_red(monkeypatch):
    import am_api_health as health

    class Client:
        def auth_status(self):
            return {"authenticated": True, "state": "OK", "last_call_latency_ms": 12}

        def get_available_balance(self):
            raise AssertionError("el dashboard no debe tocar PPI")

    result = health.chequear_ppi(Client())
    assert result.estado == health.VERDE
    assert result.latencia_ms == 12


def test_scalping_no_usa_yahoo_en_modo_operativo(monkeypatch):
    import e_technical_engine as technical

    monkeypatch.setattr(technical, "YFINANCE_SHADOW_ONLY", True)
    monkeypatch.setattr(technical.yf, "Ticker",
                        lambda *_: (_ for _ in ()).throw(AssertionError("Yahoo no debe llamarse")))
    result = technical.evaluate_technical_scalping("AAPL")
    assert result.data_ok is False
    assert "bloqueado" in result.reason.lower()


def test_websocket_no_arranca_sin_autenticacion(monkeypatch):
    import x_ppi_websocket as ws

    class Client:
        client = object()
        account_number = "1"

        def is_authenticated(self):
            return False

    realtime = ws.PPIRealTimeClient(Client())
    realtime.start()
    assert realtime._threads == []


@pytest.mark.parametrize('enabled', ['false','true'])
def test_ruta_caucion_heredada_no_busca_proxy_ni_devuelve_presupuesto_como_fill(monkeypatch, enabled):
    from c_ppi_client import ResilientPPIClient
    client = ResilientPPIClient.__new__(ResilientPPIClient)
    def forbidden(*_a,**_k):
        raise AssertionError('No debe buscar instrumentos, cotizar, presupuestar ni confirmar')
    for name in ('search_instruments','get_market_data','budget_order','confirm_order'):
        monkeypatch.setattr(client,name,forbidden)
    monkeypatch.setenv('CAUCIONES_AUTO_PLACEMENT',enabled)
    assert client.get_caucion_rate(days=7) is None
    with pytest.raises(NotImplementedError,match='CAUCION_LEGACY_PLACEMENT_BLOCKED'):
        client.place_caucion('TEST_NOT_ACCOUNT',1000.75,days=7)


@pytest.mark.parametrize('scalping,book_state', [(False,'OK'),(True,'OK'),(False,'MISSING'),
    (False,'OTHER_SETTLEMENT'),(False,'CROSSED'),(False,'STALE'),(False,'INVALID')])
def test_motor_heredado_no_supera_datos_incompletos_ni_en_scalping(monkeypatch, scalping, book_state):
    from types import SimpleNamespace as NS
    import j_main as main
    calls = []
    tech = NS(data_ok=True,score_tech=1,reason='TEST',signals={},atr_1h=10)
    monkeypatch.setattr(main,'SCALPING_MODE',scalping)
    monkeypatch.setattr(main,'runtime_config',{'MIN_SCORE_TECH':0,'MIN_SCORE_MACRO':0,'TAKE_PROFIT_ATR_MULT':2})
    monkeypatch.setattr(main.risk_guardian,'is_halted',lambda:False)
    monkeypatch.setattr(main.position_manager,'get_open_positions',lambda:[])
    monkeypatch.setattr(main.position_manager,'get_recent_scalping_trades',lambda:[])
    monkeypatch.setattr(main,'aiops_scalping_validator',lambda _:True)
    monkeypatch.setattr(main.technical_engine,'evaluate_technical',lambda *_a,**_k:tech)
    monkeypatch.setattr(main.technical_engine,'evaluate_technical_scalping',lambda *_a,**_k:tech)
    monkeypatch.setattr(main,'log_ai_decision',lambda *_a,**_k:None)
    monkeypatch.setattr(main,'log_signal',lambda *args,**kwargs:calls.append(args))
    monkeypatch.setattr(main.economics,'load_macro_config',lambda:main.economics.MacroConfig(3.0,'2026-08-28'))
    monkeypatch.setattr(main.economics,'get_ccl_devaluation_pct',lambda *_a,**_k:0.0)
    book = {'bids':[{'price':100}],'offers':[{'price':101}],'settlement_used':'INMEDIATA',
            'epoch_recv':time.time(),'epoch_source':time.time()}
    if book_state=='MISSING': book={}
    elif book_state=='OTHER_SETTLEMENT': book['settlement_used']='A-24HS'
    elif book_state=='CROSSED': book['offers'][0]['price']=99
    elif book_state=='STALE': book['epoch_source']-=31
    elif book_state=='INVALID': book['bids'][0]['price']='NaN'
    ppi = NS(is_authenticated=lambda:True,get_market_data=lambda *_a,**_k:{'price':100,'epoch_recv':time.time(),'epoch_source':time.time()},
             get_book_with_fallback=lambda *_a,**_k:book)
    gemini = NS(evaluate_macro_and_geopolitics=lambda *_a,**_k:{'macro_score':1,'veto_risk':False})
    main.evaluate_instrument(NS(ticker='TEST',asset_class='CEDEARS',instrument_type='CEDEARS',settlement='INMEDIATA'),
                             ppi,gemini,object(),1000,news_cached=[])
    assert len(calls) == 1 and calls[0][1] == ('REJECTED_HURDLE_DATA' if book_state=='OK' else 'REJECTED_BOOK_DATA')


@pytest.fixture
def market_stream(monkeypatch):
    import x_ppi_websocket as ws
    from datetime import datetime, timezone
    clock = [datetime(2026,8,28,14,tzinfo=timezone.utc).timestamp()]
    monkeypatch.setattr(ws.time,'time',lambda:clock[0])
    monkeypatch.setattr(ws,'STREAM_ENABLED',True)
    monkeypatch.setattr(ws,'MAX_TICK_AGE_SECONDS',5)
    monkeypatch.setattr(ws,'_tick_cache',{})
    monkeypatch.setattr(ws,'_stats',dict(ws._stats))
    monkeypatch.setattr(ws,'_persist',lambda *_:None)
    receiver = ws.PPIRealTimeClient.__new__(ws.PPIRealTimeClient)
    receiver.instruments = [('GGAL','ACCIONES','INMEDIATA'),('GGAL','ACCIONES','A-24HS')]
    def tick(**changes):
        return dict(Ticker='GGAL',Type='ACCIONES',Settlement='INMEDIATA',Trade=True,Price=100,
                    Date=datetime.fromtimestamp(clock[0],timezone.utc).isoformat()) | changes
    return ws, receiver, clock, tick


def test_stream_no_mezcla_plazos_y_devuelve_copias(market_stream):
    ws, receiver, clock, tick = market_stream
    receiver._on_market_data(tick())
    receiver._on_market_data(tick(Settlement='A-24HS',Price=200))
    assert ws.get_cached_price('GGAL','ACCIONES') is None
    immediate = ws.get_cached_price('GGAL','ACCIONES','INMEDIATA')
    assert immediate['price'] == 100
    assert ws.get_cached_price('GGAL','ACCIONES','A-24HS')['price'] == 200
    immediate['price']=1
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA')['price']==100


def test_book_no_rejuvenece_negocio_y_desconexion_descarta_cache(market_stream):
    ws, receiver, clock, tick = market_stream
    receiver._on_market_data(tick())
    clock[0] += 6
    receiver._on_market_data(tick(Trade=False,Price=999,Bids=[{'Price':101}]))
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA') is None
    receiver._on_market_data(tick(Price=102))
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA')['price']==102
    receiver._on_market_disconnect()
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA') is None


def test_stream_ignora_atrasados_y_marca_conflicto_hasta_nuevo_negocio(market_stream):
    ws, receiver, clock, tick = market_stream
    old=tick(Price=99)
    clock[0]+=1
    current=tick()
    receiver._on_market_data(current)
    receiver._on_market_data(old)
    receiver._on_market_data(current | {'Price':105})
    receiver._on_market_data(current)
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA') is None
    clock[0]+=1
    receiver._on_market_data(tick(Price=103))
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA')['price']==103
    clock[0]-=2
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA') is None


@pytest.mark.parametrize('change',[{'Date':None},{'Date':'2026-08-28T14:00:00'},
    {'Date':'2026-08-28T14:00:01+00:00'},{'Date':'2026-08-28T13:59:00+00:00'},
    {'Price':'NaN'},{'Price':0},{'Price':float('inf')},{'Settlement':None},{'Trade':None}])
def test_stream_rechaza_datos_invalidos_sin_supuestos(market_stream,change):
    ws, receiver, clock, tick = market_stream
    receiver._on_market_data(tick(**change))
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA') is None


def test_tipo_stream_solo_se_resuelve_desde_suscripcion_inequivoca(market_stream):
    ws, receiver, clock, tick = market_stream
    receiver._on_market_data(tick(Type=None))
    assert ws.get_cached_price('GGAL','ACCIONES','INMEDIATA')['price']==100
    ws._tick_cache.clear()
    receiver.instruments.append(('GGAL','CEDEARS','INMEDIATA'))
    receiver._on_market_data(tick(Type=None))
    assert ws._tick_cache == {}


@pytest.mark.parametrize('payload', [None,[],[{'price':1},{'price':2}],{'price':'NaN'},
    {'price':100,'date':'2026-08-28T13:59:00+00:00'},
    {'price':100,'date':'2026-08-28T14:00:01+00:00'},
    {'price':100,'date':'2026-08-28T14:00:00'},
    {'price':100,'date':'2026-08-28T14:00:00+00:00','settlement':'A-24HS'}])
def test_rest_no_inventa_frescura_o_identidad(market_stream,payload):
    from c_ppi_client import _market_snapshot
    assert _market_snapshot(payload,'GGAL','ACCIONES','INMEDIATA') is None


def test_cliente_prefiere_stream_del_plazo_correcto_y_rest_conserva_fecha(market_stream,monkeypatch):
    from types import SimpleNamespace as NS
    from c_ppi_client import ResilientPPIClient
    ws, receiver, clock, tick = market_stream
    receiver._on_market_data(tick())
    calls=[]
    payload={'price':200,'date':tick()['Date']}
    client=ResilientPPIClient.__new__(ResilientPPIClient)
    client.client=NS(marketdata=NS(current=lambda *args:calls.append(args) or payload))
    client._call_with_retry=lambda fn,*_:fn()
    assert client.get_market_data('GGAL','ACCIONES','INMEDIATA')['price']==100
    assert calls==[]
    result=client.get_market_data('GGAL','ACCIONES','A-24HS')
    assert result['price']==200 and result['epoch_source']==clock[0]
    assert calls==[('GGAL','ACCIONES','A-24HS')]
    assert set(payload)=={'price','date'}


def test_libro_vacio_no_cambia_plazo(market_stream):
    from c_ppi_client import ResilientPPIClient
    client=ResilientPPIClient.__new__(ResilientPPIClient)
    calls=[]
    client.get_book=lambda *args:calls.append(args) or {'bids':[],'offers':[]}
    result=client.get_book_with_fallback('GGAL','ACCIONES','INMEDIATA')
    assert calls==[('GGAL','ACCIONES','INMEDIATA')]
    assert result['settlement_used']=='INMEDIATA'


def test_herramientas_no_suponen_ars_ni_aprueban_orden_con_precio_solo(market_stream,monkeypatch):
    import ah_market_tools as market
    from types import SimpleNamespace as NS
    ws,receiver,clock,tick=market_stream
    receiver._on_market_data(tick())
    monkeypatch.setattr(market,'_ppi_client',NS(get_market_data=lambda *_:{'price':200,'currency':'Dolares billete | MEP'}))
    stream=market.obtener_datos_ppi('GGAL','ACCIONES','INMEDIATA')
    assert stream['moneda']=='DESCONOCIDA' and stream['apto_para_ordenar'] is False
    rest=market.obtener_datos_ppi('GGAL','ACCIONES','A-24HS')
    assert rest['moneda']=='USD_MEP' and rest['apto_para_ordenar'] is False

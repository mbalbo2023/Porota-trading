"""Pruebas de procedencia, revisión temporal y agregación sin volumen inventado."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperStore
from bl_candle_engine import Bar,CandleArchive,Series,SampleMaterializer,migrate_legacy,stamp
from test_production_paper_v1634 import quote
import bf_production_paper_observer as observer

AT='2026-08-28T11:00:00-03:00'
END='2026-08-28T11:01:00-03:00'


@pytest.fixture
def store(tmp_path):
    return PaperStore(str(tmp_path/'paper.db'))


def _prepare_current_history_contract(monkeypatch,tmp_path):
    """Exercise history download only after the RC6 one-time cutoff repair is complete."""
    import cu_history_store_v2_hf6 as history_v2
    from cv_history_store_adapter_hf6 import default_history_store
    import scripts.rc6_history_cutoff_repair_once as cutoff_repair

    monkeypatch.setenv("HIST_DB_PATH",str(tmp_path/'history-v2.db'))
    history_store=default_history_store()
    history_v2.init_schema(history_store)
    cutoff_repair._init_state(history_store)
    with history_store.connect() as c:
        c.execute("""INSERT OR REPLACE INTO rc6_history_cutoff_repair_runs
          (cutoff,state,targets,archive_imports,ppi_queries,complete,failed,updated_at)
          VALUES(?,?,?,?,?,?,?,?)""",
          ('2026-09-21','COMPLETE',0,0,0,0,0,AT))
    monkeypatch.setattr(observer.financial_catalog,'lookup',
                        lambda *_args,**_kwargs:{'market':'BYMA'})
    monkeypatch.setattr(observer,'_history_end_date',
                        lambda now=None: datetime.fromisoformat(AT).date())
    return history_store



def series(**changes):
    return Series(**(dict(symbol='GGAL',asset_class='ACCIONES',market='BYMA',currency='ARS',
        settlement='A-24HS',resolution='1m',source='TEST_PROVIDER',adjustment='RAW',
        adjustment_basis='TEST_TERMS',price_kind='PROVIDER_OHLC',volume_kind='QUANTITY',cash_multiplier='1')|changes))


def bar(**changes):
    return Bar(**(dict(start=AT,open=D(100),high=D(102),low=D(99),close=D(101),
        volume=D(20),quality='COMPLETE')|changes))


def sample_series(**changes):
    return series(source='PRODUCTION_PAPER_SNAPSHOTS',adjustment_basis='OBSERVED_NOMINAL_PRICE',
                  price_kind='TRADE_SAMPLES',volume_kind='UNKNOWN',cash_multiplier='UNKNOWN',**changes)


def rows(store,table):
    with store.connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM '+table)]


@pytest.mark.parametrize('changes',[
    {'currency':'USD_MEP'},{'currency':'USD_CCL'},{'settlement':'INMEDIATA'},
    {'market':'ROFEX'},{'source':'OTHER_PROVIDER'},{'asset_class':'FUTUROS'},
    {'adjustment':'SPLIT'},{'adjustment_basis':'OTHER_TERMS'},{'volume_kind':'NOMINAL'},
    {'cash_multiplier':'0.01'},
])
def test_series_no_se_pisan_por_ticker_y_fecha(store,changes):
    archive=CandleArchive(store)
    a,b=series(),series(**changes)
    archive.put(a,bar(),known_at=END)
    archive.put(b,bar(open=D(99)),known_at=END)
    assert archive.read(a,as_of=END)[0]['open']=='100'
    assert archive.read(b,as_of=END)[0]['open']=='99'
    assert len(archive.inventory())==2


def test_revision_corrige_apertura_sin_anticipar_el_dato(store):
    archive=CandleArchive(store); s=series()
    assert archive.put(s,bar(),known_at=END)
    later='2026-08-28T11:05:00-03:00'
    assert archive.put(s,bar(open=D(99)),known_at=later)
    assert archive.read(s,as_of=END)[0]['open']=='100'
    assert archive.read(s,as_of=later)[0]['open']=='99'
    assert not archive.put(s,bar(open=D(99)),known_at='2026-08-28T11:06:00-03:00')
    assert len(rows(store,'candle_versions'))==2


def test_revision_invalida_no_resucita_version_anterior(store):
    archive=CandleArchive(store); s=series()
    archive.put(s,bar(),known_at=END)
    later='2026-08-28T11:02:00-03:00'
    archive.put(s,bar(quality='CONFLICT'),known_at=later)
    assert len(archive.read(s,as_of=END))==1
    assert archive.read(s,as_of=later)==[]
    assert archive.read(s,as_of=later,qualities=None)[0]['quality']=='CONFLICT'


@pytest.mark.parametrize('changes',[
    {'open':D('NaN')},{'high':D('Infinity')},{'low':D(0)},
    {'high':D(100)},{'volume':D(-1)},{'trades':True},{'samples':-1},
    {'vwap':D(200)},{'synthetic':'false'},{'start':'2026-08-28T11:00:00'},
    {'start':'2026-08-28T11:00:01-03:00'},
])
def test_no_guarda_barras_inconsistentes(store,changes):
    with pytest.raises(ValueError): CandleArchive(store).put(series(),bar(**changes),known_at=END)
    assert rows(store,'candle_versions')==[]


def test_barra_abierta_y_retroactividad_rechazadas(store):
    archive=CandleArchive(store); s=series()
    with pytest.raises(ValueError,match='abierta'): archive.put(s,bar(),known_at=AT)
    archive.put(s,bar(),known_at=END)
    with pytest.raises(ValueError,match='misma'): archive.put(s,bar(open=D(99)),known_at=END)
    archive.put(s,bar(open=D(99)),known_at='2026-08-28T11:03:00-03:00')
    with pytest.raises(ValueError,match='retroactiva'):
        archive.put(s,bar(),known_at='2026-08-28T11:02:00-03:00')


def test_muestras_no_aceptan_volumen_vwap_ni_completitud(store):
    archive=CandleArchive(store)
    with pytest.raises(ValueError): archive.put(sample_series(),bar(),known_at=END)
    with pytest.raises(ValueError):
        archive.put(series(volume_kind='UNKNOWN'),bar(),known_at=END)


def test_cierre_sin_nuevas_lecturas_reinicio_y_dedupe_de_zona_horaria(store):
    q=quote(at=AT)
    store.add_quote(q)
    store.add_quote(replace(q,trade_at='2026-08-28T14:00:00Z',last=D('100.00')))
    worker=SampleMaterializer(store)
    worker.tick(AT)
    assert rows(store,'candle_versions')==[]
    worker=SampleMaterializer(PaperStore(store.path))
    worker.tick('2026-08-28T11:05:00-03:00')
    archive=CandleArchive(store)
    assert archive.read(sample_series(),as_of='2026-08-28T11:05:00-03:00')==[]
    b=archive.read(sample_series(),as_of='2026-08-28T11:05:00-03:00',qualities=('SAMPLED',))[0]
    assert b['samples']==1 and b['volume'] is None and b['trades'] is None and b['vwap'] is None
    assert len(rows(store,'candle_samples'))==2  # Una muestra en cada resolución.


def test_llegada_tardia_reconstruye_ohcl_y_conserva_disponibilidad(store):
    archive=CandleArchive(store)
    store.add_quote(quote(price='102',at='2026-08-28T11:00:30-03:00'))
    SampleMaterializer(store).tick(END)
    before=archive.read(sample_series(),as_of=END,qualities=('SAMPLED',))[0]
    assert before['open']=='102'
    late=replace(quote(price='99',at='2026-08-28T11:02:00-03:00'),trade_at=AT)
    store.add_quote(late)
    SampleMaterializer(store).tick(late.observed_at)
    assert archive.read(sample_series(),as_of=END,qualities=('SAMPLED',))[0]['open']=='102'
    after=archive.read(sample_series(),as_of=late.observed_at,qualities=('SAMPLED',))[0]
    assert (after['open'],after['close'],after['low'],after['high'])==('99','102','99','102')


def test_tiempo_de_negocio_no_se_reemplaza_por_recepcion(store):
    q=replace(quote(at=AT),trade_at='2026-08-28T10:58:20-03:00')
    store.add_quote(q)
    SampleMaterializer(store).tick(END)
    b=CandleArchive(store).read(sample_series(),as_of=END,qualities=('SAMPLED',))[0]
    assert b['start']==stamp('2026-08-28T10:58:00-03:00')
    assert b['known_at']==stamp(END)


def test_no_rellena_minutos_sin_observaciones(store):
    store.add_quote(quote(at=AT))
    store.add_quote(quote(at='2026-08-28T11:02:00-03:00'))
    SampleMaterializer(store).tick('2026-08-28T11:05:00-03:00')
    candles=CandleArchive(store).read(sample_series(),as_of='2026-08-28T11:05:00-03:00',qualities=('SAMPLED',))
    assert len(candles)==2 and all(not b['synthetic'] for b in candles)


def test_misma_marca_temporal_distinto_precio_es_conflicto(store):
    store.add_quote(quote(price='100',at=AT))
    store.add_quote(quote(price='101',at=AT))
    SampleMaterializer(store).tick(END)
    archive=CandleArchive(store)
    assert archive.read(sample_series(),as_of=END,qualities=('SAMPLED',))==[]
    assert archive.read(sample_series(),as_of=END,qualities=None)[0]['quality']=='CONFLICT'


@pytest.mark.parametrize('change',[
    {'last_kind':'MIDPOINT'},{'last_kind':'UNAVAILABLE'},{'currency':None},
    {'trade_at':None},{'trade_at':'2026-08-28T11:00:00'},
    {'trade_at':'2026-08-28T11:03:00-03:00'},{'observed_at':'2026-08-28T11:03:00-03:00'},
    {'last':D('NaN')},
])
def test_lecturas_invalidas_quedan_rechazadas_sin_inventar_fecha(store,change):
    store.add_quote(replace(quote(at=AT),**change))
    result=SampleMaterializer(store).tick(END)
    assert result['cursor']==1 and result['rejected']==1
    assert rows(store,'candle_versions')==[] and len(rows(store,'candle_rejections'))==1


def test_checkpoint_agregados_y_cursor_se_revierten_juntos(store,monkeypatch):
    store.add_quote(quote(at=AT))
    worker=SampleMaterializer(store)
    def fail(*a,**kw): raise RuntimeError('corte antes del commit')
    monkeypatch.setattr(worker.archive,'put',fail)
    with pytest.raises(RuntimeError): worker.tick(END)
    assert rows(store,'candle_samples')==[] and rows(store,'candle_worker_state')==[]
    assert SampleMaterializer(store).tick(END)['cursor']==1


def test_metadatos_de_contrato_corruptos_no_traban_el_checkpoint(store):
    store.add_quote(quote(at=AT))
    with store.connect() as c:
        c.execute("UPDATE market_snapshots SET contract_json='[]'")
    result=SampleMaterializer(store).tick(END)
    assert result['cursor']==1 and result['rejected']==1
    assert rows(store,'candle_rejections')[0]['reason']=='INVALID_CONTRACT_METADATA'


def test_lotes_y_dos_workers_no_duplican_barras(store):
    store.add_quote(quote(at=AT))
    store.add_quote(quote(at='2026-08-28T11:01:00-03:00'))
    assert SampleMaterializer(store).tick(END,batch_size=1)['state']=='BACKLOG'
    now='2026-08-28T11:05:00-03:00'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:SampleMaterializer(store).tick(now),range(2)))
    assert all(r['cursor']==2 for r in results)
    assert len(CandleArchive(store).read(sample_series(),as_of=now,qualities=('SAMPLED',)))==2
    assert sum(r['versions'] for r in results)==2  # 11:01 y la barra 5m.


def test_migracion_legacy_concilia_reintentos_sin_modificar_origen(store,tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE market_historical_ohlcv(symbol TEXT,date TEXT,close REAL,source TEXT)')
        c.execute("INSERT INTO market_historical_ohlcv VALUES('GGAL','2026-08-20',100,'DATA912')")
    original=hashlib.sha256(path.read_bytes()).hexdigest()
    first=migrate_legacy(path,store,recorded_at=AT)
    second=migrate_legacy(path,store,recorded_at=END)
    assert first=={'read':1,'archived_unverified':1,'already_archived':0}
    assert second=={'read':1,'archived_unverified':0,'already_archived':1}
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original
    assert rows(store,'candle_versions')==[]
    raw=rows(store,'historical_raw_archive')[0]
    assert raw['quality']=='LEGACY_UNVERIFIED' and json.loads(raw['body_json'])['close']==100
    missing=tmp_path/'missing.db'
    with pytest.raises(FileNotFoundError): migrate_legacy(missing,store,recorded_at=AT)
    assert not missing.exists()


def history_payload():
    return [{'date':'2026-08-25T17:00:00-03:00','openingPrice':100,'max':105,'min':99,'price':102,'volume':123}]


def test_historial_vacio_no_borra_ultimo_valido_y_raw_se_conserva(store,monkeypatch,tmp_path):
    observer._support_schema(store)
    _prepare_current_history_contract(monkeypatch,tmp_path)
    monkeypatch.setattr(observer,'_historical_targets',lambda _: [('GGAL','ACCIONES','A-24HS')])
    clock=[AT]
    monkeypatch.setattr(observer,'now_iso',lambda:clock[0])
    class Reader:
        payload=history_payload()
        def history(self,*a): return self.payload
    reader=Reader()
    assert observer._download_histories(reader,store)==1
    original=rows(store,'production_history')[0]
    reader.payload=[]; clock[0]=END
    assert observer._download_histories(reader,store)==0
    assert rows(store,'production_history')[0]==original
    assert rows(store,'production_history_attempts')[0]['state']=='EMPTY_OR_INVALID'
    assert len(rows(store,'historical_raw_archive'))==2
    health=next(r for r in rows(store,'api_health') if r['component']=='PPI_PRODUCTION_HISTORY')
    assert health['state']=='ROJO'


@pytest.mark.parametrize('payload',[
    ['error','error'], {'data':history_payload()}, [{'message':'no data'}],
    [history_payload()[0]|{'price':0}], [history_payload()[0]|{'volume':float('nan')}],
    [history_payload()[0]|{'date':'2027-01-01T10:00:00-03:00'}],
    [history_payload()[0]|{'date':'2026-08-25'}],
])
def test_contador_no_confunde_errores_o_futuros_con_barras(payload):
    assert observer._history_count(payload,as_of=AT)==0


def test_duplicados_no_inflan_cobertura():
    assert observer._history_count(history_payload()*3,as_of=AT)==1


def test_descarga_fallida_rota_en_vez_de_bloquear_universo(store,monkeypatch,tmp_path):
    observer._support_schema(store)
    _prepare_current_history_contract(monkeypatch,tmp_path)
    with store.connect() as c:
        for symbol in ('AAA','BBB'):
            c.execute('INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)',
                (symbol,'ACCIONES','A-24HS','BYMA',1,'AVAILABLE','TEST',AT))
    monkeypatch.setattr(observer,'HISTORY_BATCH_LIMIT',1)
    class Reader:
        def history(self,*a): raise TimeoutError('fixture')
    assert observer._historical_targets(store)[0][0]=='AAA'
    observer._download_histories(Reader(),store)
    assert observer._historical_targets(store)[0][0]=='BBB'


def test_panel_no_presenta_muestras_como_cobertura_validada(store,monkeypatch):
    import bg_paper_dashboard as dashboard
    store.add_quote(quote(at=AT))
    SampleMaterializer(store).tick(END)
    monkeypatch.setattr(dashboard,'DB_PATH',store.path)
    page=dashboard.history_page()
    assert 'Archivo incremental de velas' in page and 'TRADE_SAMPLES' in page
    assert 'Históricos cubiertos' not in page and 'no equivale a series validadas' in page

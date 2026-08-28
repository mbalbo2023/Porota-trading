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
from bt_caucion_paper import CaucionOffer, modeled_sale_settlement, pending_proceeds
from bs_instrument_contracts import InstrumentContract
from bs_instrument_contracts import cash_currency
from ca_caucion_allocator import CaucionPolicy, choose
import bu_instrument_catalog as catalog


def quote(symbol="GGAL", price="100", minute=0, bid_size="1000", ask_size="1000", at=None):
    at = at or (datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc) +
          timedelta(minutes=minute)).isoformat()
    price = D(price)
    return Quote(symbol, "ACCIONES", "A-24HS", price, price-D("0.10"),
                 price+D("0.10"), D(bid_size), D(ask_size), at,
                 currency="ARS", market="BYMA", metadata_source="TEST_FIXTURE",
                 book_at=at, trade_at=at, last_kind="TRADE")


@pytest.fixture
def partial_spot(tmp_path):
    def make(settlement='INMEDIATA',currency='ARS',**options):
        broker=PaperBroker(PaperStore(str(tmp_path/(currency+settlement.replace('+','')+'.db'))),
            initial_cash='10000',initial_cash_by_currency={currency:'10000'},**options)
        q=replace(quote(ask_size='100'),settlement=settlement,currency=currency)
        assert broker._open(q,D('.8'),{})[0]
        p=broker.store.open_positions()[0]
        assert D(p['quantity'])==10
        def sell(minute=1,size='40',price='110'):
            return replace(quote(minute=minute,bid_size=size,price=price),settlement=settlement,currency=currency)
        return broker,p,q,sell
    return make


@pytest.mark.parametrize('settlement',['INMEDIATA','A-24HS'])
@pytest.mark.parametrize('currency',['ARS','USD_MEP'])
def test_parcial_caja_creditos_y_costo_original_por_fecha(partial_spot,settlement,currency):
    import cd_spot_ledger as ledger
    b,p,q,sell=partial_spot(settlement,currency)
    before=b._cash(as_of=q.observed_at,currency=currency)
    first=sell()
    assert b._close(p,first,'TEST')
    remaining=b.store.open_positions()[0]
    assert D(remaining['quantity'])==6
    with b.store.connect() as c:
        root=dict(c.execute('SELECT * FROM paper_positions').fetchone())
        sales=ledger.sales(c,p['paper_id'])
        assert root['quantity']==p['quantity'] and root['entry_cost']==p['entry_cost']
        assert D(sales[0]['entry_cost'])+D(remaining['entry_cost'])==D(p['entry_cost'])
        assert c.execute('SELECT label_timestamp FROM paper_learning_samples').fetchone()[0] is None
    cash=b._cash(as_of=first.observed_at,currency=currency)
    assert cash==before+(D(sales[0]['net_proceeds']) if settlement=='INMEDIATA' else 0)
    assert pending_proceeds(b.store,first.observed_at,currency)==(0 if settlement=='INMEDIATA' else D(sales[0]['net_proceeds']))
    assert b._cash(as_of=q.observed_at,currency=currency)==before
    # Una segunda porción produce su propio crédito, no reescribe el primero.
    assert b._close(remaining,sell(2,'100','105'),'TEST')
    final=b.store.recent_closed()[0]
    with b.store.connect() as c:
        rows=ledger.sales(c,p['paper_id'])
        assert len(rows)==2
        assert sum(D(r['entry_cost']) for r in rows)==D(p['entry_cost'])
        assert sum(D(r['net_pnl']) for r in rows)==D(final['net_pnl'])
        assert c.execute('SELECT COUNT(*) FROM paper_sale_receivables').fetchone()[0]==0
    restarted=PaperBroker(PaperStore(b.store.path),initial_cash='10000',initial_cash_by_currency={currency:'10000'})
    assert restarted._cash(as_of='2026-09-01T00:00:00+00:00',currency=currency)==10000+D(final['net_pnl'])
    assert restarted._cash(as_of=first.observed_at,currency=currency)==cash
    assert restarted._cash(as_of=q.observed_at,currency=currency)==before
    with restarted.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_sale_receivables').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM paper_learning_samples WHERE label_timestamp IS NOT NULL').fetchone()[0]==1


def test_parcial_reintento_reinicio_y_concurrencia_no_revenden(partial_spot):
    from concurrent.futures import ThreadPoolExecutor
    b,p,q,sell=partial_spot()
    def close(_):
        other=PaperBroker(PaperStore(b.store.path),initial_cash='10000')
        return other._close(p,sell(),'TEST')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(close,range(2))).count(True)==1
    assert not b._close(p,sell(2),'TEST')  # posición vieja aunque el libro sea nuevo
    remaining=b.store.open_positions()[0]
    assert not b._close(remaining,sell(),'TEST')  # profundidad ya consumida
    assert D(b.store.open_positions()[0]['quantity'])==6
    assert b._close(remaining,sell(2,'100'),'TEST')
    assert not b._close(remaining,sell(3,'100'),'TEST')


@pytest.mark.parametrize('table',['paper_spot_sales','paper_book_consumption','paper_events','paper_notification_outbox'])
def test_parcial_rollback_incluye_caja_muestra_y_aviso(partial_spot,table):
    import sqlite3
    b,p,q,sell=partial_spot()
    before=b._cash(as_of=q.observed_at)
    with b.store.connect() as c:
        counts={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in
                ('paper_fills','paper_book_consumption','paper_events','paper_notification_outbox')}
        c.execute('CREATE TRIGGER fail_partial BEFORE INSERT ON '+table+" BEGIN SELECT RAISE(ABORT,'TEST_PARTIAL'); END")
    with pytest.raises(sqlite3.IntegrityError,match='TEST_PARTIAL'):
        b._close(p,sell(),'TEST')
    assert b._cash(as_of=sell().observed_at)==before
    assert b.store.open_positions()[0]['quantity']==p['quantity']
    with b.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_spot_sales').fetchone()[0]==0
        for t,n in counts.items():assert c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==n
        assert c.execute('SELECT label_timestamp FROM paper_learning_samples').fetchone()[0] is None


@pytest.mark.parametrize('change',[
    "UPDATE paper_spot_sales SET net_proceeds='99999'",
    "UPDATE paper_spot_sales SET entry_cost='99999'",
    "DELETE FROM paper_spot_sales",
    "UPDATE paper_spot_sales SET available_at='2026-08-24T00:00:00+00:00'",
])
def test_parcial_ledger_roto_no_libera_caja(partial_spot,change):
    b,p,q,sell=partial_spot()
    assert b._close(p,sell(),'TEST')
    with b.store.connect() as c:c.execute(change)
    with pytest.raises(ValueError):b._cash(as_of=sell().observed_at)


def test_parcial_futuro_no_financia_operacion_retroactiva(partial_spot):
    b,p,q,sell=partial_spot()
    before=b._cash(as_of=q.observed_at)
    assert b._close(p,sell(),'TEST')
    assert b._cash(as_of=q.observed_at)==before
    with pytest.raises(ValueError,match='CASH_CLOCK_ROLLBACK'):
        b._cash(as_of=q.observed_at,for_execution=True)


def test_parcial_supervisor_conserva_causa_y_confirma_el_ledger(partial_spot):
    from bm_exit_supervisor import PositionExitSupervisor
    b,p,q,sell=partial_spot()
    first=sell(price='90')
    supervisor=PositionExitSupervisor(b,clock_fn=lambda:first.observed_at)
    verdict=supervisor.supervise(p,first,first.observed_at)
    assert verdict.state=='EXIT_PARTIAL' and verdict.cause=='STOP_PAPER'
    remaining=b.store.open_positions()[0]
    recovered=sell(2,'100','120')
    # Callback que promete éxito no borra el remanente.
    liar=PositionExitSupervisor(b,clock_fn=lambda:recovered.observed_at,close_fn=lambda *a,**k:True)
    assert liar.supervise(remaining,recovered,recovered.observed_at).state=='EXIT_PENDING_EXECUTION'
    assert supervisor.supervise(remaining,recovered,recovered.observed_at).state=='CLOSED'
    assert b.store.recent_closed()[0]['close_reason']=='STOP_PAPER'


def test_parcial_resultado_y_costo_de_lotes_no_multiplican_aprendizaje(partial_spot):
    b,p,q,sell=partial_spot()
    with b.store.connect() as c:
        features=json.loads(p['features_json']);features['contract_quantity_step']='2'
        c.execute('UPDATE paper_positions SET features_json=?',(json.dumps(features),))
    p=b.store.open_positions()[0]
    # Capacidad de 3 unidades: solamente un lote de 2.
    assert b._close(p,sell(size='30'),'TEST')
    assert D(b.store.open_positions()[0]['quantity'])==8
    assert b.store.recent_closed()==[]
    assert b._close(b.store.open_positions()[0],sell(2,'80','105'),'TEST')
    final=b.store.recent_closed()[0]
    with b.store.connect() as c:
        sample=dict(c.execute('SELECT * FROM paper_learning_samples').fetchone())
        assert D(sample['net_return_pct'])==D(final['net_pnl'])/(D(p['entry_price'])*10)*100
        assert c.execute('SELECT COUNT(*) FROM paper_learning_samples').fetchone()[0]==1


def test_parcial_corte_diario_persiste_y_permite_reducir_remanente(partial_spot):
    b,p,q,sell=partial_spot(daily_loss_pct='1')
    first=sell(price='80')
    assert b._close(p,first,'STOP_PAPER')
    state=b.daily_risk.evaluate(first.observed_at,quotes={q.symbol:first})['ARS']
    assert state['state']=='LATCHED'
    restarted=PaperBroker(PaperStore(b.store.path),initial_cash='10000',daily_loss_pct='1')
    assert restarted.daily_risk.evaluate(first.observed_at)['ARS']['state']=='LATCHED'
    assert restarted._close(restarted.store.open_positions()[0],sell(2,'100','120'),'STOP_PAPER')
    assert restarted.daily_risk.evaluate(sell(2).observed_at)['ARS']['state']=='LATCHED'


def test_parcial_informes_panel_y_caja_historica_concuerdan(partial_spot,monkeypatch):
    import bi_operational_services as services
    import bg_paper_dashboard as dashboard
    b,p,q,sell=partial_spot()
    services.init_schema(b.store)
    first=sell()
    assert b._close(p,first,'TEST')
    cutoff=sell(2).observed_at
    first_data=services._period_data(b.store,q.observed_at,cutoff)
    assert first_data['closed']==[] and first_data['win_rate'] is None
    assert len(first_data['realizations'])==1
    assert D(first_data['positions'][0]['quantity'])==6
    first_pnl=D(first_data['pnl_by_currency']['ARS'])
    assert first_pnl==D(first_data['realizations'][0]['net_pnl'])
    assert b._close(b.store.open_positions()[0],sell(3,'100','105'),'TEST')
    historical=services._period_data(b.store,q.observed_at,cutoff)
    assert historical==first_data  # ningún cierre/etiqueta futura en el informe
    final=services._period_data(b.store,cutoff,sell(4).observed_at)
    assert len(final['closed'])==1 and len(final['realizations'])==1
    assert D(final['pnl_by_currency']['ARS'])+first_pnl==D(b.store.recent_closed()[0]['net_pnl'])
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    snap=dashboard.snapshot()
    assert snap['spot_state']=='READY' and len(snap['realized'])==2 and len(snap['closed'])==1
    assert dashboard._trade_metrics(snap['closed'])[2]==100
    assert 'Cantidad remanente' in dashboard.motor_page()
    assert 'NO COMPARABLE' in dashboard.financial_page()


@pytest.mark.parametrize('family',['BONOS','LETRAS','ON'])
def test_parcial_renta_fija_conserva_nominal_lote_y_ganancia(tmp_path,family):
    b=PaperBroker(PaperStore(str(tmp_path/'nominal.db')),initial_cash='10000',slippage_bps='0')
    q=replace(quote(ask_size='100'),asset_class=family,settlement='INMEDIATA',ask=D(100))
    spec=InstrumentContract(q.symbol,family,'ARS','BYMA','INMEDIATA',D('.01'),D(2),'TEST_FIXTURE')
    q=replace(q,contract=spec)
    assert b._open(q,D('.8'),{})[0]
    p=b.store.open_positions()[0]
    assert D(p['quantity'])==10
    first=replace(q,bid=D(110),ask=D(111),bid_size=D(30),
                  observed_at=quote(minute=1).observed_at,book_at=quote(minute=1).book_at)
    assert not b._close(p,replace(first,contract=replace(spec,quantity_step=D(1))),'TEST')
    assert b._close(p,first,'TEST')
    remaining=b.store.open_positions()[0]
    assert D(remaining['quantity'])==8
    final=replace(first,bid=D(120),ask=D(121),bid_size=D(100),
                  observed_at=quote(minute=2).observed_at,book_at=quote(minute=2).book_at)
    assert b._close(remaining,final,'TEST')
    closed=b.store.recent_closed()[0]
    assert D(closed['gross_pnl'])==D('1.80')
    assert b._cash(as_of=final.observed_at)==10000+D(closed['net_pnl'])


def test_parcial_cada_venta_conserva_su_fecha_de_liquidacion(partial_spot):
    import cd_spot_ledger as ledger
    b,p,q,sell=partial_spot('A-24HS')
    before=b._cash(as_of=q.observed_at)
    assert b._close(p,sell(),'TEST')
    second=replace(sell(2,'100'),observed_at='2026-08-27T14:00:00+00:00',book_at='2026-08-27T14:00:00+00:00')
    assert b._close(b.store.open_positions()[0],second,'TEST')
    with b.store.connect() as c:rows=ledger.sales(c,p['paper_id'])
    assert rows[0]['available_at']!=rows[1]['available_at']
    assert b._cash(as_of=second.observed_at)==before+D(rows[0]['net_proceeds'])
    assert pending_proceeds(b.store,second.observed_at)==D(rows[1]['net_proceeds'])
    assert b._cash(as_of='2026-09-01T00:00:00+00:00')==before+sum(D(r['net_proceeds']) for r in rows)


def test_parcial_credito_desconocido_no_es_caja_y_valuacion_conserva_patrimonio(partial_spot):
    b,p,q,sell=partial_spot('PLAZO-DESCONOCIDO')
    before=b._cash(as_of=q.observed_at)
    first=sell()
    assert b._close(p,first,'TEST')
    balances=b.mark_equity({q.symbol:first},as_of=first.observed_at)['ARS']
    assert balances['cash']==before and balances['pending_proceeds']>0
    assert balances['exposure']==first.bid*6
    assert balances['equity']==10000+balances['realized_pnl']+balances['unrealized_pnl']
    assert b._cash(as_of='2026-09-01T00:00:00+00:00')==before


def test_parcial_panel_abierto_muestra_remanente_sin_muestra_ficticia(partial_spot,monkeypatch):
    import bg_paper_dashboard as dashboard
    b,p,q,sell=partial_spot()
    assert b._close(p,sell(),'TEST')
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    state=dashboard.snapshot()
    assert len(state['open'])==1 and D(state['open'][0]['quantity'])==6
    assert state['closed']==[] and len(state['realized'])==1
    assert 'Cantidad remanente:</b> 6' in dashboard.motor_page()
    with b.store.connect() as c:c.execute("UPDATE paper_spot_sales SET net_pnl='9999'")
    assert dashboard.snapshot()['spot_state']=='UNAVAILABLE'
    assert 'no interpretar como cero' in dashboard.paper_page()


@pytest.fixture
def shared_spot_book(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path/'liquidity.db')),initial_cash='10000')
    q = quote(ask_size='100',bid_size='100')
    assert broker._open(q,D('.8'),{})[0]
    first = broker.store.open_positions()[0]
    assert D(first['quantity'])==10
    # Dos tenencias de la misma especie son posibles en datos importados.
    second = dict(first,paper_id='PAPER-IMPORTED-SECOND')
    with broker.store.connect() as c:
        c.execute('INSERT INTO paper_positions ('+','.join(second)+') VALUES ('+','.join('?' for _ in second)+')',tuple(second.values()))
    return broker,q,first,second


def test_profundo_spot_consumido_no_se_repone_por_reinicio_o_reintento(shared_spot_book):
    broker,q,first,second=shared_spot_book
    closing=quote(minute=1,ask_size='100',bid_size='100')
    assert broker._close(first,closing,'TEST')
    restarted=PaperBroker(PaperStore(broker.store.path),initial_cash='10000')
    assert not restarted._close(first,closing,'TEST')
    assert not restarted._close(second,closing,'TEST')
    newer=quote(minute=2,ask_size='100',bid_size='100')
    assert restarted._close(second,newer,'TEST')
    with broker.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM paper_book_consumption').fetchone()[0]==3


def test_consumo_spot_concurrente_revalida_dentro_del_lock(shared_spot_book):
    from concurrent.futures import ThreadPoolExecutor
    broker,q,first,second=shared_spot_book
    closing=quote(minute=1,ask_size='100',bid_size='100')
    def close(p):
        worker=PaperBroker(PaperStore(broker.store.path),initial_cash='10000')
        return worker._close(p,closing,'TEST')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(close,[first,second]))
    assert results.count(True)==1
    assert len(broker.store.open_positions())==1


def test_libro_spot_separa_lados_moneda_plazo_y_rechaza_historia_reescrita(shared_spot_book):
    import cc_spot_liquidity as liquidity
    broker,q,first,second=shared_spot_book
    with broker.store.connect() as c:
        assert liquidity.available(c,q,'BUY_SIMULATED',D('.1'))==0
        assert liquidity.available(c,q,'SELL_SIMULATED',D('.1'))==10
        for other in (replace(q,currency='USD_MEP'),replace(q,settlement='INMEDIATA'),replace(q,market='OTHER_TEST_MARKET')):
            assert liquidity.available(c,other,'BUY_SIMULATED',D('.1'))==10
        with pytest.raises(ValueError,match='CONFLICTING_SNAPSHOT'):
            liquidity.available(c,replace(q,ask_size=D(200)),'BUY_SIMULATED',D('.1'))
    closing=quote(minute=1,ask_size='100',bid_size='100')
    assert broker._close(first,closing,'TEST')
    with broker.store.connect() as c:
        with pytest.raises(ValueError,match='OLDER_THAN_CONSUMED'):
            liquidity.available(c,q,'BUY_SIMULATED',D('.1'))


@pytest.mark.parametrize('side',['entry','exit'])
def test_consumo_spot_y_fill_se_revierten_juntos(tmp_path,side):
    import sqlite3
    broker=PaperBroker(PaperStore(str(tmp_path/'atomic-book.db')),initial_cash='10000')
    q=quote()
    if side=='exit':
        assert broker._open(q,D('.8'),{})[0]
        position=broker.store.open_positions()[0]
    with broker.store.connect() as c:
        c.execute("CREATE TRIGGER fail_depth BEFORE INSERT ON paper_book_consumption BEGIN SELECT RAISE(ABORT,'TEST_DEPTH'); END")
    with pytest.raises(sqlite3.IntegrityError,match='TEST_DEPTH'):
        if side=='entry': broker._open(q,D('.8'),{})
        else: broker._close(position,quote(minute=1),'TEST')
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0]==(0 if side=='entry' else 1)
        assert c.execute('SELECT COUNT(*) FROM paper_book_consumption').fetchone()[0]==(0 if side=='entry' else 1)
        assert c.execute('SELECT COUNT(*) FROM paper_sale_receivables').fetchone()[0]==0
    assert len(broker.store.open_positions())==(0 if side=='entry' else 1)


def test_liquidez_heredada_no_se_inventa_y_una_cotizacion_nueva_no_cambia_la_caja(shared_spot_book):
    import cc_spot_liquidity as liquidity
    broker,q,first,second=shared_spot_book
    cash=broker._cash(as_of=q.observed_at)
    with broker.store.connect() as c:
        c.execute('DELETE FROM paper_book_consumption')
        with pytest.raises(ValueError,match='LEGACY_DEPTH_UNKNOWN'):
            liquidity.available(c,q,'BUY_SIMULATED',D('.1'))
        assert liquidity.available(c,quote(minute=1),'BUY_SIMULATED',D('.1'))==100
    assert broker._cash(as_of=q.observed_at)==cash


@pytest.fixture
def real_catalog():
    return json.loads((ROOT / "tests/fixtures/ppi_catalog_20260827.json").read_text())["records"]


@pytest.mark.parametrize("label,expected", [("Pesos", "ARS"), ("Dolares billete | MEP", "USD_MEP"),
                                          ("Dolares divisa | CCL", "USD_CCL"), ("USD", "USD")])
def test_moneda_ppi_conserva_plaza(label, expected):
    assert cash_currency(label) == expected


@pytest.mark.parametrize("label", [None, "", "Dolares", "EUR", "INVENTADA"])
def test_moneda_ambigua_no_se_supone_ars(label):
    with pytest.raises(ValueError):
        cash_currency(label)


def test_diagnostico_real_no_convierte_bono_denominado_usd_en_caja_usd(real_catalog):
    raw = next(r for r in real_catalog if r["ticker"] == "AE38")
    record = catalog.normalize_record(raw, "A-24HS", "2026-08-27T13:45:03Z", "test")
    assert record["currency"] == "ARS"
    assert record["capability"] == "NEEDS_NOMINAL_UNITS"
    future = catalog.normalize_record(real_catalog[-1], "A-24HS", "2026-08-27T13:45:03Z", "test")
    assert future["capability"] == "NEEDS_FUTURES_MARGIN_AND_CONTRACT"
    assert catalog.quote_terms(future)["contract"] is None  # No parsea vencimiento desde descripción.


def test_catalogo_real_preserva_clase_moneda_y_no_duplica_resultados(tmp_path, monkeypatch, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [
        ("FILTRO-A", "ACCIONES", "A-24HS", "BYMA", True),
        ("FILTRO-B", "ACCIONES", "A-24HS", "BYMA", True)])
    class Reader:
        def search_instruments(self, *_args, **_kwargs):
            return real_catalog
    assert observer._download_catalog(Reader(), store) == 12
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidate_universe").fetchone()[0] == 12
        assert c.execute("SELECT COUNT(*) FROM instrument_catalog WHERE instrument_type='FUTUROS'").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM catalog_query_results").fetchone()[0] == 2
        assert not c.execute("SELECT 1 FROM candidate_universe WHERE ticker LIKE 'FILTRO-%'").fetchone()
    assert catalog.lookup(store, "ALUAC", "ACCIONES", "A-24HS")["currency"] == "USD_CCL"
    assert catalog.lookup(store, "AAPLD", "CEDEARS", "A-24HS")["currency"] == "USD_MEP"
    assert ("DLR/AGO26", "FUTUROS", "A-24HS") in observer._eligible_symbols(store)


def test_actualizacion_fallida_no_borra_catalogo_ni_habilita_registros_viejos(tmp_path, monkeypatch, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [("A", "ACCIONES", "A-24HS", "BYMA", True)])
    class Reader:
        def search_instruments(self, *_args, **_kwargs):
            return real_catalog
    observer._download_catalog(Reader(), store)
    class Broken:
        def search_instruments(self, *_args, **_kwargs):
            raise TimeoutError("fixture")
    assert observer._download_catalog(Broken(), store) == 0
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM instrument_catalog").fetchone()[0] == 12
        assert c.execute("SELECT COUNT(*) FROM financial_instrument_catalog WHERE status='STALE'").fetchone()[0] == 12
    metadata = catalog.lookup(store, "AAPL", "CEDEARS", "A-24HS")
    assert catalog.quote_terms(metadata)["opening_block_reason"]


def test_cotizacion_mep_del_catalogo_no_gasta_ars_ni_usd_generico(tmp_path, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    raw = next(r for r in real_catalog if r["ticker"] == "AAPLD")
    metadata = catalog.normalize_record(raw, "INMEDIATA", "2026-08-27T13:45:03Z", "test")
    source_at = observer.now_iso()
    q = observer.normalize_quote("AAPLD", "CEDEARS", "INMEDIATA", {"price": 100, "date": source_at},
                                 {"bid": 99, "ask": 100, "bidsize": 10000, "asksize": 10000, "date": source_at}, metadata=metadata)
    broker = PaperBroker(store, initial_cash="1000000", initial_cash_usd="10000")
    assert q.currency == "USD_MEP" and q.contract.currency == "USD_MEP"
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker._cash(currency="USD") == 10000
    funded = PaperBroker(store, initial_cash_by_currency={"USD_MEP": "10000"})
    assert funded._open(q, D("0.8"), {})[0]
    p = store.open_positions()[0]
    assert p["currency"] == "USD_MEP"
    assert funded._cash() == 1000000
    assert funded._cash(currency="USD_MEP") < 10000
    ccl = replace(q, currency="USD_CCL", contract=replace(q.contract, currency="USD_CCL"))
    assert not funded._close(p, ccl, "TEST")
    closing_at = (datetime.fromisoformat(q.observed_at)+timedelta(minutes=1)).isoformat()
    closing = replace(q, bid=D("110"), ask=D("111"), observed_at=closing_at, book_at=closing_at)
    assert funded._close(p, closing, "TEST")
    closed = store.recent_closed()[0]
    assert funded._cash(currency="USD_MEP", as_of=closing.observed_at) == 10000 + D(closed["net_pnl"])
    assert funded._cash() == 1000000
    assert funded._cash(currency="USD_CCL") == 0
    values = funded.mark_equity({}, as_of=closing.observed_at)
    assert values["ARS"]["realized_pnl"] == 0
    assert values["USD_MEP"]["realized_pnl"] == D(closed["net_pnl"])


def test_senal_no_mezcla_moneda_ni_mercado(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    for i in range(8):
        store.add_quote(replace(quote(minute=i), currency="USD_CCL"))
        store.add_quote(replace(quote(minute=i), market="OTRO"))
    q = quote(minute=9)
    store.add_quote(q)
    assert broker.decide(q)[3]["samples"] == 1


def test_migracion_moneda_preserva_fills_anteriores_sin_reinterpretar_dolares(tmp_path):
    db = str(tmp_path / "legacy.db")
    store = PaperStore(db)
    q = quote(symbol="ALUAC")
    store.add_quote(q)
    assert PaperBroker(store)._open(q, D("0.8"), {})[0]
    original = store.open_positions()[0]
    # Reproduce el esquema anterior, que no identificaba moneda ni plaza.
    with store.connect() as c:
        c.execute('DROP TRIGGER paper_events_notify_v17')  # Tampoco existía en 16.3.5.
        c.execute("DROP INDEX idx_snapshot_identity")
        for table, columns in {"paper_positions": ("currency", "market", "currency_source"),
                               "market_snapshots": ("currency", "market")}.items():
            for column in columns:
                c.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    migrated = PaperStore(db)
    PaperStore(db)  # Reintento de migración sin efectos adicionales.
    p = migrated.open_positions()[0]
    assert p["currency"] == "ARS" and p["currency_source"] == "LEGACY_ASSUMED_ARS"
    assert (p["quantity"], p["entry_cost"], p["entry_price"]) == (original["quantity"], original["entry_cost"], original["entry_price"])
    assert not PaperBroker(migrated)._close(p, replace(q, currency="USD_CCL"), "TEST")
    with migrated.connect() as c:
        assert c.execute("SELECT currency FROM market_snapshots").fetchone()[0] == "UNKNOWN"
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [{"currency": None}, {"market": None}, {"opening_block_reason": "STALE"}])
def test_cotizacion_sin_identidad_confirmada_no_abre(tmp_path, changes):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    q = replace(quote(), **changes)
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker.decide(q)[0] == "HOLD"


def test_metadatos_viejos_se_importan_sin_aprobar_operaciones(tmp_path, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    raw = next(r for r in real_catalog if r["ticker"] == "ALUAC")
    with store.connect() as c:
        c.execute("INSERT INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                  ("ACCIONES", "ALUAC", raw["description"], "BYMA", "A-24HS", "2026-08-27T13:45:03Z", json.dumps(raw)))
    catalog.init_schema(store)
    catalog.init_schema(store)
    record = catalog.lookup(store, "ALUAC", "ACCIONES", "A-24HS")
    assert record["currency"] == "USD_CCL"
    assert record["status"] == "STALE"
    assert catalog.quote_terms(record)["opening_block_reason"]


def test_caucion_mep_no_usa_ccl_ni_usd_sin_plaza(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash_usd="10000",
                         initial_cash_by_currency={"USD_CCL": "10000"})
    offer = caucion_offer(currency="USD_MEP")
    with pytest.raises(ValueError, match="Caja liquidada"):
        broker.place_caucion(offer, "1000", "sin-mep", offer.quoted_at)
    funded = PaperBroker(broker.store, initial_cash_by_currency={"USD_MEP": "2000"})
    funded.place_caucion(offer, "1000", "mep", offer.quoted_at)
    assert funded._cash(as_of=offer.quoted_at, currency="USD_MEP") == 1000
    assert funded._cash(currency="USD_CCL") == 0


def caucion_offer(**changes):
    terms = dict(instrument_id="CAUCION-FIXTURE-ARS-20260831", currency="ARS",
                 annual_rate_fraction=D("0.365"), start_date="2026-08-28",
                 maturity_at="2026-08-31T15:00:00-03:00",
                 quoted_at="2026-08-28T11:00:00-03:00",
                 available_principal=D("100000"), minimum_principal=D("100"),
                 principal_step=D("1"), day_count_basis=365, fee_payment="MATURITY",
                 metadata_source="TEST_FIXTURE_NOT_BROKER", quoted_total_fees=D("1"),
                 fee_quote_principal=D("1000"))
    return CaucionOffer(**(terms | changes))


def caucion_policy(**changes):
    return CaucionPolicy(**(dict(frozen_at='2026-08-28T10:00:00-03:00',currency='ARS',
        reserve_cash=D(1000),maximum_cash_fraction=D('.5'),maximum_principal=D(3000),
        liquidity_deadline='2026-09-01T15:00:00-03:00',maximum_quote_age_seconds=D(60),
        participation=D('.10'),minimum_net_profit=D(0),ranking='NET_PROFIT',
        session_open_at='2026-08-28T11:00:00-03:00',session_close_at='2026-08-28T16:00:00-03:00',
        session_source='TEST_NOT_MARKET_CALENDAR') | changes))


def allocated(tmp_path,offers,policy=None,**kwargs):
    broker = PaperBroker(PaperStore(str(tmp_path/'allocation.db')),initial_cash='10000',daily_loss_pct='1')
    result = broker.allocate_caucion(offers,policy or caucion_policy(),'test',
                                    as_of=kwargs.get('at','2026-08-28T11:00:00-03:00'))
    return broker,result


def test_asignador_compara_neto_no_tasa_mayor_y_conserva_decision(tmp_path):
    cheap = caucion_offer(instrument_id='NETO_MEJOR',annual_rate_fraction=D('.365'),quoted_total_fees=D('.1'))
    costly = caucion_offer(instrument_id='TASA_MAYOR',annual_rate_fraction=D('.50'),quoted_total_fees=D(2))
    broker,result = allocated(tmp_path,[costly,cheap])
    assert result['status']=='PLACED_SIMULATED' and result['selected']['instrument_id']=='NETO_MEJOR'
    assert not result['promotion_allowed'] and not result['data_certified']
    assert D(result['selected']['net_profit'])==D('2.9')
    assert broker._cash(as_of=cheap.quoted_at)==9000
    with broker.store.connect() as c:
        assert json.loads(c.execute('SELECT decision_json FROM paper_caucion_allocations').fetchone()[0])==result


def test_asignador_ranking_explicito_no_presume_reinversion(tmp_path):
    large = caucion_offer(instrument_id='LARGE')  # neto 2, capital 1000
    small = caucion_offer(instrument_id='SMALL',fee_quote_principal=D(500),
                          annual_rate_fraction=D('.5'),quoted_total_fees=D('.1'))
    _,profit = allocated(tmp_path,[small,large])
    # Plan puro, sin reutilizar fondos inmovilizados del primer escenario.
    daily = {'currency':'ARS','state':'READY','daily_pnl':'0','loss_budget':'100'}
    rate = choose([large,small],caucion_policy(ranking='NET_RETURN_PER_DAY'),at=large.quoted_at,
                  cash='10000',risk=daily,participation='.1')
    assert profit['selected']['instrument_id']=='LARGE' and rate['selected']['instrument_id']=='SMALL'


@pytest.mark.parametrize('changes,policy_changes,code',[
    ({'quoted_total_fees':None},{},'EXPLICIT_COST_BUDGET_REQUIRED'),
    ({'quoted_at':'2026-08-28T10:58:59-03:00'},{},'QUOTE_STALE_OR_FUTURE'),
    ({'quoted_at':'2026-08-28T11:00:01-03:00'},{},'QUOTE_STALE_OR_FUTURE'),
    ({'maturity_at':'2026-09-02T15:00:00-03:00'},{},'MATURITY_OUTSIDE_LIQUIDITY_WINDOW'),
    ({'currency':'USD_MEP'},{},'OTHER_CURRENCY'),
    ({'available_principal':D(2000)},{},'DEPTH_EXHAUSTED'),
    ({},{'maximum_principal':D(500)},'PRINCIPAL_CAP'),
    ({},{'reserve_cash':D(9000)},'CASH_RESERVE_OR_FRACTION'),
    ({},{'minimum_net_profit':D(3)},'NET_PROFIT_TOO_LOW'),
    ({'start_date':'2026-08-27'},{},'START_DATE_MISMATCH'),
    ({'metadata_source':'UNKNOWN'},{},'UNKNOWN_CONTRACT_SOURCE'),
])
def test_asignador_rechaza_oferta_con_motivo_sin_colocar(tmp_path,changes,policy_changes,code):
    broker,result = allocated(tmp_path,[caucion_offer(**changes)],caucion_policy(**policy_changes))
    assert result['status']=='HOLD' and result['candidates'][0]['code']==code
    assert not broker.cauciones.positions() and result['paper_id'] is None


def test_asignador_no_escala_presupuesto_ni_ignora_costo_inicial(tmp_path):
    offer = caucion_offer(fee_payment='UPFRONT')
    broker,result = allocated(tmp_path,[offer],caucion_policy(reserve_cash=D(8000)))
    assert result['cash_budget']=='1000' and result['candidates'][0]['code']=='CASH_RESERVE_OR_FRACTION'
    assert not broker.cauciones.positions()  # No inventar otro costo para 999.


@pytest.mark.parametrize('ranking',['NET_PROFIT','NET_RETURN_PER_DAY'])
def test_asignador_reorden_y_duplicados_no_cambian_plan(ranking):
    a,b = caucion_offer(instrument_id='A'),caucion_offer(instrument_id='B')
    args=dict(policy=caucion_policy(ranking=ranking),at=a.quoted_at,cash='10000',
              risk={'currency':'ARS','state':'READY','daily_pnl':'0','loss_budget':'100'},participation='.1')
    assert choose([a,b],**args)==choose([b,a,a],**args)


@pytest.mark.parametrize('change',[{'annual_rate_fraction':D('.5')},{'quoted_total_fees':D('.5')}])
def test_ofertas_contradictorias_no_eligen_el_numero_mas_favorable(tmp_path,change):
    a=caucion_offer()
    broker,result=allocated(tmp_path,[a,replace(a,**change)])
    assert result['status']=='HOLD'
    assert {r['code'] for r in result['candidates']}=={'CONFLICTING_BOOK_OR_BUDGET'}
    assert not broker.cauciones.positions()


def test_asignador_idempotente_reinicio_y_cambio_de_politica(tmp_path):
    offer,policy = caucion_offer(),caucion_policy()
    broker,result=allocated(tmp_path,[offer],policy)
    restarted=PaperBroker(PaperStore(broker.store.path),initial_cash='10000',daily_loss_pct='1')
    assert restarted.allocate_caucion([offer,offer],policy,'test',as_of=offer.maturity_at)==result
    with pytest.raises(ValueError,match='términos diferentes'):
        restarted.allocate_caucion([offer],replace(policy,reserve_cash=D(2000)),'test',as_of=offer.quoted_at)
    assert len(restarted.cauciones.positions())==1


def test_asignador_hold_no_se_reinterpreta_con_otro_reloj(tmp_path):
    offer=caucion_offer(quoted_total_fees=None)
    broker,result=allocated(tmp_path,[offer])
    assert broker.allocate_caucion([offer],caucion_policy(),'test',as_of=offer.maturity_at)==result
    assert result['status']=='HOLD'


def test_asignacion_y_colocacion_comparten_rollback(tmp_path):
    import sqlite3
    broker=PaperBroker(PaperStore(str(tmp_path/'atomic.db')),daily_loss_pct='1')
    with broker.store.connect() as c:
        c.execute("CREATE TRIGGER fail_allocation BEFORE INSERT ON paper_caucion_allocations BEGIN SELECT RAISE(ABORT,'TEST'); END")
    with pytest.raises(sqlite3.IntegrityError):
        broker.allocate_caucion([caucion_offer()],caucion_policy(),'atomic',as_of=caucion_offer().quoted_at)
    assert not broker.cauciones.positions()
    with broker.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_notification_outbox').fetchone()[0]==0


def test_asignador_requiere_riesgo_y_sesion_sin_aplicar_config_defaults(tmp_path):
    broker=PaperBroker(PaperStore(str(tmp_path/'no-risk.db')))
    result=broker.allocate_caucion([caucion_offer()],caucion_policy(),'none',as_of=caucion_offer().quoted_at)
    assert result['code']=='DAILY_RISK_NOT_CONFIGURED'
    broker,result=allocated(tmp_path,[caucion_offer()],at='2026-08-28T16:00:00-03:00')
    assert result['code']=='OUTSIDE_CONFIRMED_SESSION' and not broker.cauciones.positions()


@pytest.mark.parametrize('change',[{'maximum_cash_fraction':0},{'maximum_cash_fraction':'1.01'},
    {'participation':'NaN'},{'reserve_cash':'-1'},{'ranking':'TASA_MAS_ALTA'},
    {'session_source':'UNKNOWN'},{'maximum_principal':'1.001'},
    {'session_close_at':'2026-08-28T10:00:00-03:00'},{'currency':'EUR'}])
def test_politica_caucion_invalida_no_crea_defaults(change):
    with pytest.raises(ValueError): caucion_policy(**change)


def test_dos_colocaciones_no_reponen_misma_profundidad_y_libro_viejo_no_revive(tmp_path):
    broker=PaperBroker(PaperStore(str(tmp_path/'depth.db')),initial_cash='10000')
    offer=caucion_offer(available_principal=D(10000))
    broker.place_caucion(offer,'1000','first',offer.quoted_at)
    with pytest.raises(ValueError,match='participación'):
        broker.place_caucion(offer,'1000','second',offer.quoted_at)
    newer=replace(offer,quoted_at='2026-08-28T11:00:01-03:00')
    broker.place_caucion(newer,'1000','fresh',newer.quoted_at)
    with pytest.raises(ValueError,match='OLDER_BOOK'):
        broker.place_caucion(offer,'1000','old',newer.quoted_at)
    assert len(broker.cauciones.positions())==2


@pytest.mark.parametrize('limiting',['cash','depth'])
def test_asignador_concurrente_no_reutiliza_caja_ni_profundidad(tmp_path,limiting):
    from concurrent.futures import ThreadPoolExecutor
    broker=PaperBroker(PaperStore(str(tmp_path/'concurrent.db')),initial_cash='1500' if limiting=='cash' else '10000',daily_loss_pct='1')
    offer=caucion_offer(available_principal=D(100000 if limiting=='cash' else 10000))
    policy=caucion_policy(reserve_cash=D(0),maximum_cash_fraction=D(1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:broker.allocate_caucion([offer],policy,key,as_of=offer.quoted_at),['a','b']))
    assert sorted(r['status'] for r in results)==['HOLD','PLACED_SIMULATED']
    assert len(broker.cauciones.positions())==1


def test_asignador_costo_contra_limite_no_registra_perdida_hipotetica(tmp_path):
    broker=PaperBroker(PaperStore(str(tmp_path/'daily.db')),initial_cash='10000',daily_loss_pct='.01')
    offer=caucion_offer()
    result=broker.allocate_caucion([offer],caucion_policy(),'daily',as_of=offer.quoted_at)
    assert result['candidates'][0]['code']=='DAILY_RISK_PROJECTED_LOSS'
    assert result['status']=='HOLD' and not broker.cauciones.positions()
    risk=broker.daily_risk.evaluate(offer.quoted_at)['ARS']
    assert risk['state']=='READY' and D(risk['daily_pnl'])==0


def test_asignador_respeta_tope_del_broker_aunque_politica_pida_mas(tmp_path):
    broker,result=allocated(tmp_path,[caucion_offer(available_principal=D(1000))],
                             caucion_policy(participation=D(1)))
    assert result['participation']=='0.1' and result['candidates'][0]['code']=='DEPTH_EXHAUSTED'
    assert not broker.cauciones.positions()


def test_asignador_mep_no_compromete_ars_ni_ccl(tmp_path):
    broker=PaperBroker(PaperStore(str(tmp_path/'mep.db')),initial_cash='10000',
                      initial_cash_by_currency={'USD_MEP':'4000','USD_CCL':'5000'},daily_loss_pct='1')
    offer=caucion_offer(currency='USD_MEP')
    result=broker.allocate_caucion([offer],caucion_policy(currency='USD_MEP'),'mep',as_of=offer.quoted_at)
    assert result['status']=='PLACED_SIMULATED'
    assert broker._cash(offer.quoted_at,'USD_MEP')==3000
    assert broker._cash(offer.quoted_at,'USD_CCL')==5000 and broker._cash(offer.quoted_at)==10000


def test_asignador_identifica_conflicto_con_fotografia_ya_consumida(tmp_path):
    offer=caucion_offer()
    broker,result=allocated(tmp_path,[offer])
    changed=replace(offer,annual_rate_fraction=D('.5'))
    result=broker.allocate_caucion([changed],caucion_policy(),'changed',as_of=offer.quoted_at)
    assert result['status']=='HOLD' and result['candidates'][0]['code']=='CAUCION_CONFLICTING_BOOK'
    assert len(broker.cauciones.positions())==1


def test_asignador_no_reutiliza_clave_de_colocacion_manual(tmp_path):
    broker=PaperBroker(PaperStore(str(tmp_path/'manual.db')),daily_loss_pct='1')
    offer=caucion_offer()
    broker.place_caucion(offer,'1000','same',offer.quoted_at)
    with pytest.raises(ValueError,match='colocación explícita'):
        broker.allocate_caucion([offer],caucion_policy(),'same',as_of=offer.quoted_at)


@pytest.mark.parametrize('state,currency,at,code',[
    ('LATCHED','ARS','2026-08-28T11:00:00-03:00','DAILY_RISK_LATCHED'),
    ('READY','USD_MEP','2026-08-28T11:00:00-03:00','DAILY_RISK_CURRENCY_MISMATCH'),
    ('READY','ARS','2026-08-28T09:00:00-03:00','POLICY_NOT_KNOWN'),
])
def test_plan_sin_admision_valida_no_selecciona(state,currency,at,code):
    result=choose([caucion_offer()],caucion_policy(),at=at,cash='10000',participation='.1',
                  risk={'state':state,'currency':currency,'daily_pnl':'0','loss_budget':'100'})
    assert result['code']==code and result['selected'] is None


@pytest.mark.parametrize("fee_payment", ["MATURITY", "UPFRONT"])
def test_v17_caucion_inmoviliza_capital_y_acredita_una_vez_al_vencer(tmp_path, fee_payment):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store, initial_cash="10000")
    offer = caucion_offer(fee_payment=fee_payment)
    p = broker.place_caucion(offer, "1000", "pedido-1", offer.quoted_at)
    assert p["interest_days"] == 3  # Viernes a lunes; no usar 1 día para el interés.
    assert D(p["gross_interest"]) == 3
    assert broker._cash(as_of=offer.quoted_at) == D("8999" if fee_payment == "UPFRONT" else "9000")
    broker.mark_equity({}, as_of=offer.quoted_at)
    with store.connect() as c:
        assert D(c.execute("SELECT equity FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0]) == 9999
    assert broker.settle_cauciones("2026-08-31T14:59:59-03:00") == []
    assert broker.settle_cauciones(offer.maturity_at) == [p["paper_id"]]
    assert broker._cash(as_of=offer.maturity_at) == 10002
    assert broker.settle_cauciones(offer.maturity_at) == []
    broker.mark_equity({}, as_of=offer.maturity_at)
    with store.connect() as c:
        assert D(c.execute("SELECT equity FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0]) == 10002
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_CAUCION_MATURED'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0  # No inventa compra/venta.


def test_v17_caucion_reintento_persistente_no_duplica_ni_reutiliza_terminos(tmp_path):
    path = str(tmp_path / "paper.db")
    broker = PaperBroker(PaperStore(path), initial_cash="10000")
    offer = caucion_offer()
    original = broker.place_caucion(offer, "1000", "pedido", offer.quoted_at)
    restarted = PaperBroker(PaperStore(path), initial_cash="10000")
    assert restarted.place_caucion(offer, "1000", "pedido", offer.maturity_at)["paper_id"] == original["paper_id"]
    with pytest.raises(ValueError, match="términos diferentes"):
        restarted.place_caucion(replace(offer, annual_rate_fraction=D("0.40")), "1000", "pedido", offer.quoted_at)
    assert restarted._cash(as_of=offer.quoted_at) == 9000
    assert len(restarted.cauciones.positions()) == 1


def test_v17_caucion_no_convierte_pesos_en_dolares_para_financiarse(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1000000")
    offer = caucion_offer(currency="USD")
    with pytest.raises(ValueError, match="Caja liquidada insuficiente"):
        broker.place_caucion(offer, "1000", "usd-sin-caja", offer.quoted_at)
    assert broker._cash() == 1000000
    funded = PaperBroker(broker.store, initial_cash="1000000", initial_cash_usd="2000")
    funded.place_caucion(offer, "1000", "usd", offer.quoted_at)
    assert funded._cash(as_of=offer.quoted_at, currency="USD") == 1000
    funded.settle_cauciones(offer.maturity_at)
    assert funded._cash(as_of=offer.maturity_at, currency="USD") == 2002
    assert funded._cash() == 1000000


@pytest.mark.parametrize("changes, now, reserve, error", [
    ({}, "2026-08-28T11:01:01-03:00", "0", "vencida o futura"),
    ({}, "2026-08-28T10:59:59-03:00", "0", "vencida o futura"),
    ({"available_principal": D("2000")}, "2026-08-28T11:00:00-03:00", "0", "participación"),
    ({"annual_rate_fraction": D("0.001")}, "2026-08-28T11:00:00-03:00", "0", "neto positivo"),
    ({}, "2026-08-28T11:00:00-03:00", "9500", "Caja liquidada"),
    ({"fee_quote_principal": D("2000")}, "2026-08-28T11:00:00-03:00", "0", "otro capital"),
])
def test_v17_caucion_rechaza_datos_y_fondos_incompatibles(tmp_path, changes, now, reserve, error):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="10000")
    with pytest.raises(ValueError, match=error):
        broker.place_caucion(caucion_offer(**changes), "1000", "pedido", now, reserve=reserve)
    assert broker.cauciones.positions() == []
    assert broker._cash() == 10000


@pytest.mark.parametrize("changes", [
    {"annual_rate_fraction": "NaN"}, {"day_count_basis": 0},
    {"maturity_at": "2026-08-31T15:00:00"}, {"maturity_at": "2026-08-28T15:00:00-03:00"},
    {"currency": "EUR"}, {"fee_payment": "UNKNOWN"},
    {"currency": "USD", "quoted_total_fees": None}, {"quoted_total_fees": "Infinity"},
])
def test_v17_caucion_exige_terminos_completos(changes):
    with pytest.raises(ValueError):
        caucion_offer(**changes)


def test_v17_caucion_prorratea_arancel_anual_sin_cobrarlo_por_operacion():
    offer = caucion_offer(quoted_total_fees=None)
    interest, fees, net = offer.economics("1000")
    assert interest == 3
    assert fees == D("0.20")  # 1000 * (0.02 + 0.00045) * 1.21 * 3 / 365.
    assert net == D("2.80")


@pytest.mark.parametrize('payment',['MATURITY','UPFRONT'])
def test_caucion_consulta_pasada_no_usa_apertura_ni_acreditacion_futura(tmp_path,payment):
    broker = PaperBroker(PaperStore(str(tmp_path/'temporal.db')),initial_cash='10000')
    offer = caucion_offer(fee_payment=payment)
    before = '2026-08-28T10:59:00-03:00'
    broker.place_caucion(offer,'1000','temporal',offer.quoted_at)
    broker.settle_cauciones(offer.maturity_at)
    assert broker._cash(as_of=before)==10000
    assert broker._cash(as_of=offer.quoted_at)==D(8999 if payment=='UPFRONT' else 9000)
    assert broker._cash(as_of=offer.maturity_at)==10002
    assert broker.cauciones.valuation(before)==dict(principal=D(0),accrued=D(0),unrealized=D(0),realized=D(0))
    during = broker.cauciones.valuation(offer.quoted_at)
    assert during['principal']==1000 and during['realized']==0 and during['unrealized']==-1
    assert broker.cauciones.valuation(offer.maturity_at)['realized']==2
    # Vencido en el ledger no significa que ya estaba acreditado el viernes.
    assert broker.mark_equity({},as_of=offer.quoted_at)['ARS']['equity']==9999


def test_vencimiento_posterior_al_reloj_no_financia_colocacion_retroactiva(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path/'rollback.db')),initial_cash='2000')
    offer = caucion_offer()
    original = broker.place_caucion(offer,'1000','original',offer.quoted_at)
    broker.settle_cauciones(offer.maturity_at)
    with pytest.raises(ValueError,match='CASH_CLOCK_ROLLBACK'):
        broker.place_caucion(offer,'1000','backdated',offer.quoted_at)
    # Reintentar la misma operación sólo consulta el resultado ya registrado.
    assert broker.place_caucion(offer,'1000','original',offer.quoted_at)['paper_id']==original['paper_id']
    assert len(broker.cauciones.positions())==1


def test_caucion_reloj_vivo_no_acepta_fecha_de_cotizacion_como_hora_actual(tmp_path):
    offer = caucion_offer()
    broker = PaperBroker(PaperStore(str(tmp_path/'clock.db')),initial_cash='10000',
                         clock_fn=lambda:'2026-08-28T11:02:00-03:00')
    with pytest.raises(ValueError,match='vencida o futura'):
        broker.place_caucion(offer,'1000','stale',offer.quoted_at)
    assert not broker.cauciones.positions()


@pytest.mark.parametrize('future_move',['entry','exit'])
def test_movimiento_spot_posterior_bloquea_colocacion_retroactiva(tmp_path,future_move):
    broker = PaperBroker(PaperStore(str(tmp_path/'spot-clock.db')),initial_cash='10000')
    q = quote(at='2026-08-28T11:01:00-03:00' if future_move=='entry' else '2026-08-28T10:59:00-03:00')
    assert broker._open(q,D('.8'),{})[0]
    if future_move=='exit':
        sell = replace(q,bid=D(110),ask=D(111),observed_at='2026-08-28T11:01:00-03:00', book_at='2026-08-28T11:01:00-03:00')
        assert broker._close(broker.store.open_positions()[0],sell,'TEST')
    offer = caucion_offer()
    with pytest.raises(ValueError,match='CASH_CLOCK_ROLLBACK'):
        broker.place_caucion(offer,'1000','retroactive',offer.quoted_at)
    assert not broker.cauciones.positions()


def test_valuacion_historica_no_toma_libro_ni_marca_futura(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path/'future-mark.db')))
    q = quote()
    assert broker._open(q,D('.8'),{})[0]
    p = broker.store.open_positions()[0]
    future = quote(price='200',minute=1)
    broker.store.add_quote(future)
    # El último libro persistido es futuro, pero el suministrado era conocido.
    assert broker.mark_equity({q.symbol:q},as_of=q.observed_at)['ARS']['exposure']==q.bid*D(p['quantity'])
    broker.mark_equity({future.symbol:future},as_of=future.observed_at)
    historical = broker.mark_equity({},as_of=q.observed_at)['ARS']
    assert historical['exposure']==D(p['entry_price'])*D(p['quantity'])
    with broker.store.connect() as c:
        assert c.execute("SELECT state FROM paper_valuation_quality WHERE currency='ARS'").fetchone()[0]=='STALE_MARKS'


def test_caja_y_credito_de_venta_respetan_fecha_de_operacion(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path/'sale-history.db')),initial_cash='10000')
    q = quote(at='2026-08-28T11:00:00-03:00')
    assert broker._open(q,D('.8'),{})[0]
    p = broker.store.open_positions()[0]
    committed = D(p['entry_price'])*D(p['quantity'])+D(p['entry_cost'])
    sell = replace(q,bid=D(110),ask=D(111),observed_at='2026-08-28T11:01:00-03:00', book_at='2026-08-28T11:01:00-03:00')
    assert broker._close(p,sell,'TEST')
    assert broker._cash(as_of='2026-08-28T10:59:00-03:00')==10000
    assert pending_proceeds(broker.store,q.observed_at)==0
    assert broker._cash(as_of=q.observed_at)==10000-committed
    assert broker._cash(as_of=sell.observed_at)==10000-committed
    # Recuperar la posición abierta en ese instante aunque hoy esté CLOSED.
    historical = broker.mark_equity({q.symbol:q},as_of=q.observed_at)['ARS']
    assert historical['exposure']==q.bid*D(p['quantity']) and historical['realized_pnl']==0


def test_caja_no_pagina_cierres_ni_depende_del_reporte_reciente(tmp_path,monkeypatch):
    broker = PaperBroker(PaperStore(str(tmp_path/'unpaged.db')),initial_cash='10000')
    q = replace(quote(),settlement='CI')
    assert broker._open(q,D('.8'),{})[0]
    sell = replace(q,bid=D(110),ask=D(111),observed_at='2026-08-25T14:01:00+00:00', book_at='2026-08-25T14:01:00+00:00')
    assert broker._close(broker.store.open_positions()[0],sell,'TEST')
    pnl = D(broker.store.recent_closed()[0]['net_pnl'])
    monkeypatch.setattr(broker.store,'recent_closed',lambda *a,**k: [])
    assert broker._cash(as_of=sell.observed_at)==10000+pnl


@pytest.mark.parametrize('problem',['missing','foreign_currency','nan'])
def test_venta_sin_liquidacion_valida_no_se_convierte_en_caja(tmp_path,problem):
    broker = PaperBroker(PaperStore(str(tmp_path/'missing-receipt.db')),initial_cash='10000')
    q = replace(quote(),settlement='CI')
    assert broker._open(q,D('.8'),{})[0]
    sell = replace(q,bid=D(110),ask=D(111),observed_at='2026-08-25T14:01:00+00:00', book_at='2026-08-25T14:01:00+00:00')
    assert broker._close(broker.store.open_positions()[0],sell,'TEST')
    with broker.store.connect() as c:
        if problem=='missing':
            c.execute('DELETE FROM paper_sale_receivables')
        elif problem=='foreign_currency':
            c.execute("UPDATE paper_sale_receivables SET currency='USD_MEP'")
        else:
            c.execute("UPDATE paper_sale_receivables SET net_proceeds='NaN'")
    with pytest.raises(ValueError):
        broker._cash(as_of=sell.observed_at)


@pytest.mark.parametrize('age',['NaN','Infinity','-1'])
def test_caucion_frescura_invalida_no_elude_control(tmp_path,age):
    broker = PaperBroker(PaperStore(str(tmp_path/'age.db')))
    offer = caucion_offer()
    with pytest.raises(ValueError):
        broker.cauciones.place(offer,'1000','invalid',offer.quoted_at,
            lambda *args:D(10000),max_quote_age_seconds=age)
    assert not broker.cauciones.positions()


def test_caucion_efectivo_no_finito_rechazado_sin_colocacion(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path/'cash.db')))
    offer = caucion_offer()
    with pytest.raises(ValueError):
        broker.cauciones.place(offer,'1000','invalid',offer.quoted_at,lambda *args:D('NaN'))
    assert not broker.cauciones.positions()


def test_v17_dos_colocaciones_concurrentes_no_gastan_la_misma_caja(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1500")
    offer = caucion_offer()
    def place(request):
        try:
            return broker.place_caucion(offer, "1000", request, offer.quoted_at)["paper_id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(place, ["A", "B"]))
    assert sum(result is not None for result in results) == 1
    assert broker._cash(as_of=offer.quoted_at) == 500


def test_v17_venta_t1_no_es_caja_hasta_liquidacion_modelada(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="10000")
    q = quote(at="2026-08-28T11:00:00-03:00")
    assert broker._open(q, D("0.8"), {})[0]
    position = broker.store.open_positions()[0]
    before = broker._cash(as_of=q.observed_at)
    sell = replace(q, bid=D("110"), ask=D("111"), observed_at='2026-08-28T11:01:00-03:00', book_at='2026-08-28T11:01:00-03:00')
    assert broker._close(position, sell, "TEST")
    assert broker._cash(as_of=sell.observed_at) == before
    assert broker._cash(as_of="2026-08-31T12:00:00-03:00") == before
    assert broker._cash(as_of="2026-09-01T00:00:00-03:00") == 10000 + D(broker.store.recent_closed()[0]["net_pnl"])
    assert pending_proceeds(broker.store, sell.observed_at) > 0
    broker.mark_equity({}, as_of=sell.observed_at)
    with broker.store.connect() as c:
        equity = D(c.execute("SELECT equity FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert equity == 10000 + D(broker.store.recent_closed()[0]["net_pnl"])


def test_v17_calendario_caja_respeta_dia_sin_liquidacion_y_ano_desconocido():
    # Viernes 6/11 no liquida: jueves T+1 pasa al lunes 9.
    assert modeled_sale_settlement("A-24HS", "2026-11-05T11:00:00-03:00").startswith("2026-11-09")
    assert modeled_sale_settlement("A-24HS", "2026-12-30T11:00:00-03:00") is None
    assert modeled_sale_settlement("PLAZO-DESCONOCIDO", "2026-08-28T11:00:00-03:00") is None


@pytest.mark.parametrize("family", ["CAUCIONES", "OPCIONES", "FUTUROS", "FCI", "DESCONOCIDO"])
def test_v17_no_ejecuta_familias_especiales_como_acciones(tmp_path, family):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    assert broker._open(replace(quote(), asset_class=family), D("0.8"), {})[0] is False
    assert broker.store.open_positions() == []


@pytest.mark.parametrize("family", ["BONOS", "LETRAS", "ON"])
def test_v17_renta_fija_dimensiona_por_nominal_y_persiste_factor(tmp_path, family):
    path = str(tmp_path / "paper.db")
    broker = PaperBroker(PaperStore(path), initial_cash="10000", risk_pct="1",
                         max_position_pct="1", max_total_exposure_pct="1", slippage_bps="0")
    q = replace(quote(ask_size="1000000", bid_size="1000000"), asset_class=family,
                ask=D("100"), settlement="INMEDIATA")
    assert broker._open(q, D("0.8"), {})[0] is False  # No inventa el factor VN.
    spec = InstrumentContract(q.symbol, family, "ARS", "BYMA", "INMEDIATA",
                              D("0.01"), D("100"), "TEST_NOT_BROKER")
    q = replace(q, contract=spec)
    assert broker._open(q, D("0.8"), {})[0]
    position = broker.store.open_positions()[0]
    assert D(position["quantity"]) == 9900
    assert broker._cash() == 10000 - 9900 - D(position["entry_cost"])
    broker = PaperBroker(PaperStore(path), initial_cash="10000", risk_pct="1", slippage_bps="0")
    broker.mark_equity({q.symbol: q}, as_of=q.observed_at)
    with broker.store.connect() as c:
        assert D(c.execute("SELECT exposure FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0]) == q.bid * 99
    closing = replace(q, bid=D("110"), ask=D("111"), observed_at='2026-08-25T14:01:00+00:00', book_at='2026-08-25T14:01:00+00:00')
    assert broker._close(position, replace(closing, contract=replace(spec, cash_multiplier=D("1"))), "TEST") is False
    assert broker._close(position, closing, "TEST")
    closed = broker.store.recent_closed()[0]
    assert D(closed["gross_pnl"]) == 990
    assert D(closed["entry_cost"]) == broker._cost(D("1"), D("9900"), family)
    assert D(closed["exit_cost"]) == broker._cost(D("1.1"), D("9900"), family)
    assert broker._cash(as_of=closing.observed_at) == 10000 + D(closed["net_pnl"])


def test_v17_no_cauciona_el_producido_de_una_venta_t1(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1100",
                         risk_pct="1", max_position_pct="1", max_total_exposure_pct="1")
    q = quote(at="2026-08-28T10:59:00-03:00")
    assert broker._open(q, D("0.8"), {})[0]
    assert broker._close(broker.store.open_positions()[0], replace(q, bid=D("110"), ask=D("111"), observed_at='2026-08-28T11:00:00-03:00', book_at='2026-08-28T11:00:00-03:00'), "TEST")
    offer = caucion_offer()
    with pytest.raises(ValueError, match="Caja liquidada insuficiente"):
        broker.place_caucion(offer, "1000", "reusar-venta", offer.quoted_at)


def test_v17_compra_no_reutiliza_capital_colocado_en_caucion(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1050",
                         risk_pct="1", max_position_pct="1", max_total_exposure_pct="1")
    offer = caucion_offer()
    broker.place_caucion(offer, "1000", "inmovilizar", offer.quoted_at)
    q = quote(at=offer.quoted_at)
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker._cash(as_of=offer.quoted_at) == 50


def test_v17_diagnostico_exporta_catalogo_y_copia_json_sin_cuentas(tmp_path):
    import base64
    import subprocess
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    with store.connect() as c:
        c.execute("INSERT INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                  ("FUTUROS", "DLR-FIXTURE", "Contrato de prueba", "A3", "INMEDIATA", "2026-08-28",
                   json.dumps({"ticker": "DLR-FIXTURE", "contractMultiplier": 1000,
                               "initialMargin": 100000, "accountNumber": "NO-EXPORTAR", "api_key": "NO-EXPORTAR"})))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/v17_diagnostico_instrumentos.py"),
                             "--db", store.path, "--clipboard"], check=True, capture_output=True, text=True)
    plain, rest = result.stdout.split("\033]52;c;", 1)
    report = json.loads(plain)
    copied = json.loads(base64.b64decode(rest.split("\a", 1)[0]))
    assert copied == report
    assert "NO-EXPORTAR" not in result.stdout
    assert report["samples"][0]["public_metadata"]["contractMultiplier"] == 1000
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM instrument_catalog").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0


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
            return [{"date":"2026-08-25T17:00:00-03:00","price":1,
                     "openingPrice":1,"max":1,"min":1,"volume":100}]
    observer._download_histories(Reader(), store)
    with store.connect() as connection:
        covered = connection.execute("SELECT COUNT(*) FROM production_history").fetchone()[0]
    assert covered >= 4

"""Prerrequisitos verificables; ninguna fixture habilita trading real."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperStore
from bl_candle_engine import Bar,CandleArchive
from br_backtest_gate import DataRequirements,audit_dataset,marked_drawdown,purged_temporal_split
from test_candle_archive_v17 import series


def daily_archive(tmp_path,**changes):
    store=PaperStore(str(tmp_path/'data.db'))
    archive=CandleArchive(store)
    s=series(resolution='1d',**changes)
    days=['2026-01-31T00:00:00-03:00','2026-02-01T00:00:00-03:00','2026-03-01T00:00:00-03:00']
    for day in days:
        archive.put(s,Bar(day,100,102,99,101,volume=100,quality='COMPLETE'),
                    known_at=datetime.fromisoformat(day)+timedelta(days=1))
    return archive,s,days


def test_tres_meses_distintos_no_equivalen_a_noventa_dias(tmp_path):
    archive,s,days=daily_archive(tmp_path)
    result=audit_dataset(archive,s,DataRequirements(3,90,'QUANTITY'),as_of='2026-03-03T00:00:00-03:00',
        start=days[0],end='2026-03-02T00:00:00-03:00',expected_starts=days)
    assert result['bars']==3 and result['span_days']==30
    assert 'INSUFFICIENT_ACTUAL_TIME_SPAN' in result['issues']
    assert not result['data_checks_passed'] and not result['promotion_allowed']


def test_datos_validos_no_son_aprobacion_de_estrategia(tmp_path):
    archive,s,days=daily_archive(tmp_path)
    result=audit_dataset(archive,s,DataRequirements(3,30,'QUANTITY'),as_of='2026-03-03T00:00:00-03:00',
        start=days[0],end='2026-03-02T00:00:00-03:00',expected_starts=days)
    assert result['data_checks_passed'] and not result['promotion_allowed']


def test_huecos_sesion_desconocida_y_unidad_incompatible(tmp_path):
    archive,s,days=daily_archive(tmp_path)
    kwargs=dict(as_of='2026-03-03T00:00:00-03:00',start=days[0],end='2026-03-02T00:00:00-03:00')
    result=audit_dataset(archive,s,DataRequirements(3,30,'NOMINAL'),**kwargs)
    assert set(result['issues'])=={'SESSION_GRID_NOT_VERIFIED','VOLUME_UNIT_MISMATCH'}
    expected=days+['2026-02-02T00:00:00-03:00']
    result=audit_dataset(archive,s,DataRequirements(3,30,'QUANTITY'),expected_starts=expected,**kwargs)
    assert len(result['missing'])==1 and 'MISSING_EXPECTED_BARS' in result['issues']
    with pytest.raises(ValueError):
        audit_dataset(archive,s,DataRequirements(3,30,'QUANTITY'),expected_starts=days+days,**kwargs)


def test_ultima_revision_invalida_se_rechaza_en_auditoria(tmp_path):
    archive,s,days=daily_archive(tmp_path)
    archive.put(s,Bar(days[0],100,102,99,101,volume=None,quality='UNVERIFIED',synthetic=True),
                known_at='2026-03-02T12:00:00-03:00')
    result=audit_dataset(archive,s,DataRequirements(3,30,'QUANTITY'),as_of='2026-03-03T00:00:00-03:00',
        start=days[0],end='2026-03-02T00:00:00-03:00',expected_starts=days)
    assert 'MISSING_VOLUME' in result['issues'] and 'UNVERIFIED_CONFLICT_OR_SYNTHETIC' in result['issues']


def test_muestras_y_ajuste_desconocido_no_pasan_a_backtest(tmp_path):
    archive,s,days=daily_archive(tmp_path,adjustment='UNKNOWN',adjustment_basis='UNKNOWN')
    result=audit_dataset(archive,s,DataRequirements(3,30,'QUANTITY'),as_of='2026-03-03T00:00:00-03:00',
        start=days[0],end='2026-03-02T00:00:00-03:00',expected_starts=days)
    assert 'INCOMPLETE_IDENTITY_OR_ADJUSTMENT' in result['issues']
    sampled=series(price_kind='TRADE_SAMPLES',volume_kind='UNKNOWN')
    result=audit_dataset(archive,sampled,DataRequirements(1,1,'QUANTITY'),as_of='2026-03-03T00:00:00-03:00',
        start=days[0],end='2026-03-02T00:00:00-03:00')
    assert 'SAMPLES_ARE_NOT_COMPLETE_OHLC' in result['issues']


def sample(key,opened,closed,**changes):
    return dict(paper_id=key,strategy_version='test-v17',currency='ARS',
        features_available_at=opened,opened_at=opened,closed_at=closed,label_available_at=closed)|changes


SPLIT=dict(training_end='2026-08-25T12:00:00-03:00',test_start='2026-08-25T13:00:00-03:00',
    test_end='2026-08-26T17:00:00-03:00',as_of='2026-08-27T17:00:00-03:00',embargo_seconds=3600)


def test_split_purga_etiquetas_tardias_cruces_y_periodo_embargo():
    data=[
        sample('train','2026-08-25T11:00:00-03:00','2026-08-25T11:50:00-03:00'),
        sample('late-label','2026-08-25T11:00:00-03:00','2026-08-25T11:50:00-03:00',label_available_at='2026-08-25T12:01:00-03:00'),
        sample('overlap','2026-08-25T11:00:00-03:00','2026-08-25T13:00:00-03:00'),
        sample('embargo','2026-08-25T12:30:00-03:00','2026-08-25T12:50:00-03:00'),
        sample('test','2026-08-25T13:00:00-03:00','2026-08-25T14:00:00-03:00'),
        sample('unknown-label','2026-08-25T13:00:00-03:00','2026-08-25T14:00:00-03:00',label_available_at='2026-08-28T11:00:00-03:00'),
        sample('test-overrun','2026-08-25T13:00:00-03:00','2026-08-26T17:00:00-03:00'),
    ]
    result=purged_temporal_split(data,**SPLIT)
    assert [r['paper_id'] for r in result['train']]==['train']
    assert [r['paper_id'] for r in result['test']]==['test']
    assert set(result['purged'])=={'late-label','overlap','embargo','unknown-label','test-overrun'}
    assert not result['promotion_allowed'] and result['requires_frozen_model']


@pytest.mark.parametrize('changes',[
    {'features_available_at':'2026-08-25T11:01:00-03:00'},
    {'opened_at':'2026-08-25T11:00:00'}, {'label_available_at':'2026-08-25T11:00:00-03:00'},
])
def test_feature_futura_y_reloj_invalido_no_entran_en_train(changes):
    row=sample('x','2026-08-25T11:00:00-03:00','2026-08-25T11:50:00-03:00',**changes)
    with pytest.raises(ValueError): purged_temporal_split([row],**SPLIT)


@pytest.mark.parametrize('changes',[{'currency':'USD_MEP'},{'strategy_version':'other'},{'paper_id':'a'}])
def test_no_mezcla_monedas_versiones_o_identificadores(changes):
    a=sample('a','2026-08-25T11:00:00-03:00','2026-08-25T11:50:00-03:00')
    b=dict(a,paper_id='b')|changes
    with pytest.raises(ValueError): purged_temporal_split([a,b],**SPLIT)


@pytest.mark.parametrize('bad',[-1,float('nan'),float('inf'),True])
def test_embargo_invalido_no_se_ignora(bad):
    with pytest.raises(ValueError): purged_temporal_split([],**(SPLIT|{'embargo_seconds':bad}))


def point(day,equity,**changes):
    return dict(measured_at=f'2026-08-{day:02d}T11:00:00-03:00',equity=str(equity),
                currency='ARS',quality='CURRENT',external_flow='0')|changes


def test_drawdown_sobre_pico_marcado_no_sobre_capital_inicial():
    points=[point(27,800),point(25,500),point(26,1000)]
    assert marked_drawdown(points,currency='ARS')==Decimal(20)


@pytest.mark.parametrize('changes',[
    {'quality':'STALE_MARKS'},{'currency':'USD_CCL'},{'external_flow':100},
    {'external_flow':None},{'equity':'NaN'},{'measured_at':'2026-08-26T11:00:00'},
])
def test_drawdown_rechaza_curvas_no_conciliadas(changes):
    with pytest.raises(ValueError): marked_drawdown([point(25,500),point(26,400)|changes],currency='ARS')


def test_drawdown_rechaza_inicio_cero_y_tiempos_duplicados():
    with pytest.raises(ValueError): marked_drawdown([point(25,0)],currency='ARS')
    with pytest.raises(ValueError): marked_drawdown([point(25,100)]*2,currency='ARS')

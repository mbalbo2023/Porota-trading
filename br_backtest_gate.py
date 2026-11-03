"""Controles previos de datos y partición temporal; NO aprueba estrategias.

No confunde tres nombres de meses con 90 días, cierres con curva patrimonial,
ni separar resultados a posteriori con entrenar y probar fuera de muestra.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from bl_candle_engine import stamp
from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value


@dataclass(frozen=True)
class DataRequirements:
    minimum_bars: int
    minimum_span_days: int
    volume_kind: str

    def __post_init__(self):
        if (not isinstance(self.minimum_bars,int) or isinstance(self.minimum_bars,bool) or self.minimum_bars<1
            or not isinstance(self.minimum_span_days,int) or isinstance(self.minimum_span_days,bool) or self.minimum_span_days<1):
            raise ValueError('La muestra y su duración deben definirse explícitamente')
        if self.volume_kind not in {'QUANTITY','CONTRACTS','NOMINAL','MONEY'}:
            raise ValueError('Debe especificarse la unidad de volumen requerida')


def audit_dataset(archive,series,requirements,*,as_of,start,end,expected_starts=None):
    """La grilla esperada viene del calendario/sesión verificados del instrumento.

    No completa huecos, no presume lunes a viernes ni exige minutos de otra
    rueda. Sin grilla sólo se informa evidencia parcial, no integridad aprobada.
    """
    begin,finish,at=map(aware_datetime,(start,end,as_of))
    if not begin<finish<=at:
        raise ValueError('Período futuro, vacío o invertido')
    bars=archive.read(series,as_of=at,start=begin,end=finish,qualities=None,include_synthetic=True)
    issues=[]
    if series.price_kind!='PROVIDER_OHLC':
        issues.append('SAMPLES_ARE_NOT_COMPLETE_OHLC')
    if any(getattr(series,key)=='UNKNOWN' for key in ('asset_class','market','currency','settlement','source','adjustment','adjustment_basis','cash_multiplier')):
        issues.append('INCOMPLETE_IDENTITY_OR_ADJUSTMENT')
    if series.volume_kind!=requirements.volume_kind:
        issues.append('VOLUME_UNIT_MISMATCH')
    if any(b['quality']!='COMPLETE' or b['synthetic'] for b in bars):
        issues.append('UNVERIFIED_CONFLICT_OR_SYNTHETIC')
    if any(b['volume'] is None for b in bars):
        issues.append('MISSING_VOLUME')
    if len(bars)<requirements.minimum_bars:
        issues.append('INSUFFICIENT_BARS')
    span=(aware_datetime(bars[-1]['end'])-aware_datetime(bars[0]['start'])).total_seconds()/86400 if bars else 0
    if span<requirements.minimum_span_days:
        issues.append('INSUFFICIENT_ACTUAL_TIME_SPAN')
    missing=unexpected=None
    if expected_starts is None:
        issues.append('SESSION_GRID_NOT_VERIFIED')
    else:
        expected=[stamp(t) for t in expected_starts]
        if len(set(expected))!=len(expected) or any(not stamp(begin)<=t<stamp(finish) for t in expected):
            raise ValueError('Grilla inválida')
        actual={b['start'] for b in bars}
        missing=sorted(set(expected)-actual)
        unexpected=sorted(actual-set(expected))
        if missing: issues.append('MISSING_EXPECTED_BARS')
        if unexpected: issues.append('UNEXPECTED_BARS')
    return {'data_checks_passed':not issues,'promotion_allowed':False,
        'scope':'Sólo integridad de datos; falta validar estrategia, ejecución, costos y holdout',
        'series_id':series.key,'as_of':stamp(at),'bars':len(bars),'span_days':span,
        'issues':issues,'missing':missing,'unexpected':unexpected}


def purged_temporal_split(samples,*,training_end,test_start,test_end,as_of,embargo_seconds):
    """Partición por disponibilidad de features/etiqueta, con purga de cruces.

    El llamador debe congelar el modelo antes del test. Esta función no entrena
    ni llama 'fuera de muestra' a una secuencia de operaciones ya optimizada.
    Intervalos de evaluación [test_start,test_end); cierre exactamente al inicio
    del test se purga del entrenamiento para no compartir ese evento.
    """
    cut,begin,end,now=map(aware_datetime,(training_end,test_start,test_end,as_of))
    if not isinstance(embargo_seconds,(int,float)) or isinstance(embargo_seconds,bool) or embargo_seconds<0:
        raise ValueError('Embargo inválido')
    decimal_value(embargo_seconds,'embargo',nonnegative=True)
    embargo=timedelta(seconds=embargo_seconds)
    if not cut+embargo<=begin<end<=now:
        raise ValueError('Fechas de partición/embargo incompatibles')
    train,test,purged,ids=[],[],[],set()
    versions,currencies=set(),set()
    for sample in samples:
        key=sample.get('paper_id')
        if not isinstance(key,str) or not key or key in ids:
            raise ValueError('Identificador ausente o duplicado')
        ids.add(key)
        version=sample.get('strategy_version')
        if not isinstance(version,str) or not version:
            raise ValueError('Versión de estrategia ausente')
        versions.add(version); currencies.add(cash_currency(sample.get('currency')))
        feature,decision,closed,available=map(aware_datetime,(sample.get('features_available_at'),
            sample.get('opened_at'),sample.get('closed_at'),sample.get('label_available_at')))
        if not feature<=decision<=closed<=available:
            raise ValueError('Feature futura o etiqueta anterior al resultado')
        if available>now:
            purged.append(key)
        elif decision<=cut and closed<begin and available<=cut:
            train.append(sample)
        elif begin<=decision<end and closed<end and available<=now:
            test.append(sample)
        else:
            purged.append(key)
    if len(versions)>1 or len(currencies)>1:
        raise ValueError('No mezclar versiones ni monedas en una evaluación')
    order=lambda s:stamp(s['opened_at'])
    return {'train':sorted(train,key=order),'test':sorted(test,key=order),'purged':purged,
            'promotion_allowed':False,'requires_frozen_model':True}


def marked_drawdown(points,*,currency):
    """Drawdown respecto del máximo patrimonial, incluyendo posiciones abiertas.

    No acepta sólo una lista de PnL cerrados ni marcas vencidas. El llamador debe
    aportar curva neta de flujos externos y costos, con evidencia de valuación.
    """
    currency=cash_currency(currency)
    if not points:
        raise ValueError('Falta curva patrimonial')
    ordered=sorted(points,key=lambda p:stamp(p['measured_at']))
    seen=set(); peak=None; worst=Decimal('0')
    for p in ordered:
        at=stamp(p['measured_at'])
        if at in seen or cash_currency(p.get('currency'))!=currency or p.get('quality')!='CURRENT':
            raise ValueError('Curva duplicada, de otra moneda o sin valuación vigente')
        seen.add(at)
        if p.get('external_flow') is None or decimal_value(p['external_flow'],'flujo')!=0:
            raise ValueError('Requiere curva conciliada sin flujos externos')
        equity=decimal_value(p['equity'],'patrimonio')
        if peak is None:
            if equity<=0: raise ValueError('Capital inicial no positivo')
            peak=equity
        peak=max(peak,equity)
        worst=max(worst,(peak-equity)/peak*100)
    return worst

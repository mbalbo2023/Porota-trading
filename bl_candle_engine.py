"""Archivo versionado y barras de muestras PAPER. No es un feed de negocios.

Una lectura de Current no informa todos los negocios ni su volumen individual.
Las barras muestreadas nunca se rotulan OHLCV completo, ni generan VWAP/dollar
bars. Correcciones se anexan con su disponibilidad real; no reescriben el pasado.
"""
from dataclasses import asdict, dataclass
from datetime import timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
RESOLUTIONS = {'1m':1,'5m':5,'15m':15,'30m':30,'1h':60,'1d':1440}
SERIES_FIELDS = ('symbol','asset_class','market','currency','settlement','resolution',
                 'source','adjustment','adjustment_basis','price_kind','volume_kind','cash_multiplier')


def stamp(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat(timespec='microseconds')


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bounds(value, resolution):
    if resolution not in RESOLUTIONS:
        raise ValueError('Resolución no soportada')
    at = aware_datetime(value).astimezone(TZ)
    minutes = RESOLUTIONS[resolution]
    offset = (at.hour*60+at.minute)//minutes*minutes
    start = at.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(minutes=offset)
    return stamp(start),stamp(start+timedelta(minutes=minutes))


@dataclass(frozen=True)
class Series:
    symbol: str
    asset_class: str
    market: str
    currency: str
    settlement: str
    resolution: str
    source: str
    adjustment: str
    adjustment_basis: str
    price_kind: str
    volume_kind: str
    cash_multiplier: str = 'UNKNOWN'

    def __post_init__(self):
        for name in SERIES_FIELDS:
            value = getattr(self,name)
            if not isinstance(value,str) or not value.strip():
                raise ValueError(f'Falta identidad explícita: {name}')
            literal=name in {'resolution','source','adjustment_basis'} and value.strip().upper()!='UNKNOWN'
            object.__setattr__(self,name,value.strip() if literal else value.strip().upper())
        if self.currency!='UNKNOWN':
            object.__setattr__(self,'currency',cash_currency(self.currency))
        if self.cash_multiplier!='UNKNOWN':
            factor=decimal_value(self.cash_multiplier,'factor monetario',positive=True)
            object.__setattr__(self,'cash_multiplier',format(factor.normalize(),'f'))
        if self.resolution not in RESOLUTIONS:
            raise ValueError('Resolución no soportada')
        if self.adjustment not in {'UNKNOWN','RAW','SPLIT','TOTAL_RETURN'}:
            raise ValueError('Ajuste no identificado')
        if self.adjustment!='UNKNOWN' and self.adjustment_basis=='UNKNOWN':
            raise ValueError('Falta procedencia del tratamiento de ajustes')
        if self.price_kind not in {'PROVIDER_OHLC','TRADE_SAMPLES','MIDQUOTE_SAMPLES'}:
            raise ValueError('Tipo de precio no identificado')
        if self.volume_kind not in {'UNKNOWN','QUANTITY','CONTRACTS','NOMINAL','MONEY'}:
            raise ValueError('Unidad del volumen no identificada')

    @property
    def key(self):
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class Bar:
    start: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    trades: int | None = None
    samples: int = 0
    vwap: Decimal | None = None
    quality: str = 'UNVERIFIED'
    synthetic: bool = False

    def payload(self, series):
        start,end = bounds(self.start,series.resolution)
        if stamp(self.start)!=start:
            raise ValueError('Inicio no alineado con la resolución')
        values = {key:decimal_value(getattr(self,key),key,positive=True) for key in ('open','high','low','close')}
        if not values['low'] <= min(values['open'],values['close']) <= max(values['open'],values['close']) <= values['high']:
            raise ValueError('OHLC inconsistente')
        volume = decimal_value(self.volume,'volumen',nonnegative=True) if self.volume is not None else None
        if volume is not None and series.volume_kind=='UNKNOWN':
            raise ValueError('Volumen sin unidad: conservar en raw, no interpretar')
        for key in ('trades','samples'):
            value=getattr(self,key)
            if value is not None and (not isinstance(value,int) or isinstance(value,bool) or value<0):
                raise ValueError('Cantidad inválida')
        if self.quality not in {'COMPLETE','SAMPLED','CONFLICT','UNVERIFIED','INCOMPLETE'} or not isinstance(self.synthetic,bool):
            raise ValueError('Calidad no identificada')
        if series.price_kind!='PROVIDER_OHLC' and (volume is not None or self.trades is not None or self.vwap is not None or self.quality=='COMPLETE'):
            raise ValueError('Muestras no prueban volumen, negocios ni OHLC completo')
        vwap=decimal_value(self.vwap,'VWAP',positive=True) if self.vwap is not None else None
        if vwap is not None and (not volume or not values['low']<=vwap<=values['high']):
            raise ValueError('VWAP sin volumen o fuera del rango')
        return {'start':start,'end':end,**{k:format(v.normalize(),'f') for k,v in values.items()},
                'volume':str(volume) if volume is not None else None,'trades':self.trades,
                'samples':self.samples,'vwap':str(vwap) if vwap is not None else None,
                'quality':self.quality,'synthetic':self.synthetic}


def init_schema(store):
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS candle_series(
          series_id TEXT PRIMARY KEY, identity_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS candle_versions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, series_id TEXT NOT NULL REFERENCES candle_series(series_id),
          bar_start TEXT NOT NULL, bar_end TEXT NOT NULL, known_at TEXT NOT NULL,
          body_json TEXT NOT NULL, body_hash TEXT NOT NULL,
          UNIQUE(series_id,bar_start,known_at));
        CREATE INDEX IF NOT EXISTS idx_candle_asof ON candle_versions(series_id,bar_start,known_at);
        CREATE TABLE IF NOT EXISTS candle_samples(
          series_id TEXT NOT NULL, event_at TEXT NOT NULL, price TEXT NOT NULL,
          received_at TEXT NOT NULL, snapshot_id INTEGER NOT NULL,
          PRIMARY KEY(series_id,event_at,price));
        CREATE TABLE IF NOT EXISTS candle_dirty(
          series_id TEXT NOT NULL, bar_start TEXT NOT NULL, bar_end TEXT NOT NULL,
          PRIMARY KEY(series_id,bar_start));
        CREATE TABLE IF NOT EXISTS candle_rejections(
          snapshot_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL, reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS candle_worker_state(
          id INTEGER PRIMARY KEY CHECK(id=1), cursor INTEGER NOT NULL DEFAULT 0,
          heartbeat_at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS historical_raw_archive(
          id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, row_key TEXT NOT NULL,
          body_hash TEXT NOT NULL, recorded_at TEXT NOT NULL, quality TEXT NOT NULL,
          body_json TEXT NOT NULL, UNIQUE(origin,row_key,body_hash));
        ''')


class CandleArchive:
    def __init__(self,store):
        self.store=store

    @staticmethod
    def register(c,series):
        c.execute('INSERT OR IGNORE INTO candle_series VALUES(?,?)',(series.key,canonical(asdict(series))))

    def put(self,series,bar,*,known_at,connection=None):
        body=bar.payload(series)
        known=stamp(known_at)
        if known<body['end']:
            raise ValueError('Barra aún abierta: no se publica como cerrada')
        if connection is None:
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                return self.put(series,bar,known_at=known,connection=c)
        c=connection
        self.register(c,series)
        previous=c.execute('''SELECT * FROM candle_versions WHERE series_id=? AND bar_start=?
          ORDER BY known_at DESC,id DESC LIMIT 1''',(series.key,body['start'])).fetchone()
        digest=fingerprint(body)
        if previous:
            if known<previous['known_at']:
                raise ValueError('Revisión retroactiva: disponibilidad anterior a la última versión')
            if digest==previous['body_hash']:
                return False
            if known==previous['known_at']:
                raise ValueError('Dos versiones distintas con la misma disponibilidad')
        c.execute('INSERT INTO candle_versions VALUES(NULL,?,?,?,?,?,?)',
                  (series.key,body['start'],body['end'],known,canonical(body),digest))
        return True

    def read(self,series,*,as_of,start=None,end=None,qualities=('COMPLETE',),include_synthetic=False):
        at=stamp(as_of)
        args=[series.key,at,at]
        filters='v.series_id=? AND v.known_at<=? AND v.bar_end<=?'
        if start is not None:
            filters+=' AND v.bar_start>=?'; args.append(stamp(start))
        if end is not None:
            filters+=' AND v.bar_end<=?'; args.append(stamp(end))
        with self.store.connect() as c:
            rows=c.execute('''SELECT v.* FROM candle_versions v WHERE '''+filters+'''
              AND NOT EXISTS (SELECT 1 FROM candle_versions newer
                WHERE newer.series_id=v.series_id AND newer.bar_start=v.bar_start
                  AND newer.known_at<=? AND newer.known_at>v.known_at)
              ORDER BY v.bar_start''',(*args,at)).fetchall()
        result=[]
        for row in rows:
            body=json.loads(row['body_json'])
            # Filtrar DESPUÉS de seleccionar versión: una corrección inválida
            # no habilita rescatar silenciosamente una versión anterior válida.
            if qualities is not None and body['quality'] not in qualities:
                continue
            if body['synthetic'] and not include_synthetic:
                continue
            result.append(body|{'known_at':row['known_at'],'version_id':row['id'],'series_id':series.key})
        return result

    def inventory(self):
        with self.store.connect() as c:
            rows=c.execute('''SELECT s.identity_json,v.series_id,COUNT(DISTINCT v.bar_start) bars,
              COUNT(*) versions,MIN(v.bar_start) first_bar,MAX(v.bar_end) last_bar,
              MAX(v.known_at) last_known_at FROM candle_versions v JOIN candle_series s USING(series_id)
              GROUP BY v.series_id ORDER BY s.identity_json''').fetchall()
        return [dict(r)|json.loads(r['identity_json']) for r in rows]


class SampleMaterializer:
    """Checkpoint y agregados en una transacción acotada; nunca consulta PPI."""
    def __init__(self,store,resolutions=('1m','5m')):
        if not resolutions or len(set(resolutions))!=len(resolutions) or any(r not in RESOLUTIONS for r in resolutions):
            raise ValueError('Resoluciones inválidas')
        self.store,self.resolutions,self.archive=store,tuple(resolutions),CandleArchive(store)

    def status(self,at,state,detail=''):
        with self.store.connect() as c:
            c.execute('''INSERT INTO candle_worker_state VALUES(1,0,?,?,?)
              ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,
                state=excluded.state,detail=excluded.detail''',(stamp(at),state,detail))

    def tick(self,at,*,batch_size=100):
        now=stamp(at)
        if not 1<=batch_size<=500:
            raise ValueError('Lote fuera del límite')
        accepted=rejected=written=0
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            state=c.execute('SELECT * FROM candle_worker_state WHERE id=1').fetchone()
            if state and now<state['heartbeat_at']:
                raise ValueError('Reloj del materializador retrocedió')
            cursor=state['cursor'] if state else 0
            rows=c.execute('SELECT * FROM market_snapshots WHERE id>? ORDER BY id LIMIT ?',
                           (cursor,batch_size)).fetchall()
            for row in rows:
                try:
                    if row['source']!='PRODUCTION_PAPER':
                        raise ValueError('UNEXPECTED_SNAPSHOT_ORIGIN')
                    if row['last_kind']!='TRADE':
                        raise ValueError('NO_TRADE_SAMPLE')
                    event,received=stamp(row['trade_at']),stamp(row['observed_at'])
                    if not event<=received<=now:
                        raise ValueError('SOURCE_OR_RECEIPT_IN_FUTURE')
                    price=format(decimal_value(row['last'],'precio',positive=True).normalize(),'f')
                    currency=cash_currency(row['currency'])
                    contract=json.loads(row['contract_json']) if row['contract_json'] else {}
                    if not isinstance(contract,dict):
                        raise ValueError('INVALID_CONTRACT_METADATA')
                    factor=str(contract.get('cash_multiplier','UNKNOWN'))
                    if not row['market'] or row['market']=='UNKNOWN' or not row['settlement']:
                        raise ValueError('INCOMPLETE_IDENTITY')
                    for resolution in self.resolutions:
                        series=Series(row['symbol'],row['asset_class'],row['market'],currency,row['settlement'],
                            resolution,'PRODUCTION_PAPER_SNAPSHOTS','RAW','OBSERVED_NOMINAL_PRICE',
                            'TRADE_SAMPLES','UNKNOWN',factor)
                        self.archive.register(c,series)
                        inserted=c.execute('INSERT OR IGNORE INTO candle_samples VALUES(?,?,?,?,?)',
                            (series.key,event,price,received,row['id'])).rowcount
                        if inserted:
                            start,end=bounds(event,resolution)
                            c.execute('INSERT OR IGNORE INTO candle_dirty VALUES(?,?,?)',(series.key,start,end))
                    accepted+=1
                except (ValueError,TypeError,ArithmeticError) as exc:
                    c.execute('INSERT OR IGNORE INTO candle_rejections VALUES(?,?,?)',
                              (row['id'],now,str(exc)[:180]))
                    rejected+=1
                cursor=row['id']
            dirty=c.execute('''SELECT d.*,s.identity_json FROM candle_dirty d JOIN candle_series s USING(series_id)
              WHERE d.bar_end<=? ORDER BY d.bar_end,d.series_id LIMIT ?''',(now,batch_size)).fetchall()
            for pending in dirty:
                series=Series(**json.loads(pending['identity_json']))
                samples=c.execute('''SELECT * FROM candle_samples WHERE series_id=? AND event_at>=?
                  AND event_at<? ORDER BY event_at,received_at,snapshot_id''',
                  (series.key,pending['bar_start'],pending['bar_end'])).fetchall()
                values=[Decimal(s['price']) for s in samples]
                conflict=len({s['event_at'] for s in samples})!=len(samples)
                bar=Bar(pending['bar_start'],values[0],max(values),min(values),values[-1],
                        samples=len(samples),quality='CONFLICT' if conflict else 'SAMPLED')
                written+=int(self.archive.put(series,bar,known_at=now,connection=c))
                c.execute('DELETE FROM candle_dirty WHERE series_id=? AND bar_start=?',
                          (series.key,pending['bar_start']))
            more=c.execute('SELECT 1 FROM market_snapshots WHERE id>? LIMIT 1',(cursor,)).fetchone()
            pending=c.execute('SELECT 1 FROM candle_dirty WHERE bar_end<=? LIMIT 1',(now,)).fetchone()
            state='BACKLOG' if more or pending else 'RUNNING'
            c.execute('''INSERT INTO candle_worker_state VALUES(1,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor,heartbeat_at=excluded.heartbeat_at,
                state=excluded.state,detail=excluded.detail''',
              (cursor,now,state,f'{accepted} lecturas aceptadas; {rejected} rechazadas; {written} versiones'))
        return {'cursor':cursor,'accepted':accepted,'rejected':rejected,'versions':written,'state':state}


def archive_raw(c,*,origin,row_key,payload,recorded_at,quality='UNVERIFIED'):
    body=canonical(payload)
    return c.execute('INSERT OR IGNORE INTO historical_raw_archive VALUES(NULL,?,?,?,?,?,?)',
        (origin,row_key,fingerprint(payload),stamp(recorded_at),quality,body)).rowcount==1


def migrate_legacy(source_path,store,*,recorded_at,batch_size=500):
    """Copia en cuarentena, no convierte datos desconocidos en OHLCV válido.

    El origen se abre read-only. Nunca se supone plazo, zona, moneda, ajuste ni
    unidad del volumen. Sin tabla vieja: cero filas, sin crear una base vacía.
    """
    path=Path(source_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if not 1<=batch_size<=1000:
        raise ValueError('Lote de migración fuera del límite')
    result={'read':0,'archived_unverified':0,'already_archived':0}
    origin=str(path)+'#market_historical_ohlcv'
    source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    try:
        if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_historical_ohlcv'").fetchone():
            return result
        rows=source.execute('SELECT * FROM market_historical_ohlcv ORDER BY symbol,date')
        while batch:=rows.fetchmany(batch_size):
            with store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                for row in batch:
                    payload=dict(row)
                    new=archive_raw(c,origin=origin,row_key=canonical([payload['symbol'],payload['date']]),
                                    payload=payload,recorded_at=recorded_at,quality='LEGACY_UNVERIFIED')
                    result['read']+=1
                    result['archived_unverified' if new else 'already_archived']+=1
    finally:
        source.close()
    return result


def run_worker(store,stop,*,clock_fn):
    worker=SampleMaterializer(store)
    try:
        while not stop.is_set():
            try:
                worker.tick(clock_fn())
            except Exception as exc:
                worker.status(clock_fn(),'ERROR',type(exc).__name__)
            stop.wait(2)
    finally:
        worker.status(clock_fn(),'STOPPED')

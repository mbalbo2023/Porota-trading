"""Política conservadora delegada por el operador: sólo simulación ARS.

No obtiene cotizaciones, no certifica campos PPI, no programa tareas ni envía
órdenes. Ventana/contratos/costos explícitos; faltar evidencia significa HOLD.
La reserva diaria no se recalcula hacia abajo en cada intento.
"""
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
import json

import ak_byma_calendar as calendar
from bl_candle_engine import fingerprint, stamp
from bs_instrument_contracts import aware_datetime, decimal_value
from bt_caucion_paper import TZ, offer_payload
from ca_caucion_allocator import CaucionPolicy, allocate_locked, encoded, normalized_offers

PROFILE = 'caucion-treasury-ars-v17.1'
CURRENCY = 'ARS'
RESERVE_FRACTION = Decimal('.50')
MAX_CALENDAR_DAYS = 4
QUOTE_MAX_SECONDS = Decimal(30)
PARTICIPATION = Decimal('.10')


@dataclass(frozen=True)
class CaucionWindow:
    known_at: str
    opens_at: str
    closes_at: str
    liquidity_deadline: str
    source: str

    def __post_init__(self):
        for key in ('known_at','opens_at','closes_at','liquidity_deadline'):
            object.__setattr__(self,key,stamp(getattr(self,key)))
        if (not isinstance(self.source,str) or not self.source.strip()
                or self.source.strip().upper()=='UNKNOWN'):
            raise ValueError('Falta fuente de sesión/liquidación')
        if not self.known_at <= self.opens_at < self.closes_at < self.liquidity_deadline:
            raise ValueError('Ventana temporal inválida')
        if aware_datetime(self.opens_at).astimezone(TZ).date()!=aware_datetime(self.closes_at).astimezone(TZ).date():
            raise ValueError('La ventana debe pertenecer a una sola rueda')


def init_schema(store):
    with store.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS paper_caucion_treasury_days(
          day TEXT PRIMARY KEY, profile TEXT NOT NULL, frozen_at TEXT NOT NULL,
          reference_cash TEXT NOT NULL, reserve_cash TEXT NOT NULL,
          principal_limit TEXT NOT NULL, window_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_caucion_treasury_attempts(
          request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
          evaluated_at TEXT NOT NULL, day TEXT NOT NULL, result_json TEXT NOT NULL);''')


def window_error(window, at):
    if window is None:
        return 'SESSION_EVIDENCE_MISSING'
    if not isinstance(window,CaucionWindow):
        raise ValueError('Se requiere CaucionWindow o None')
    if not window.opens_at <= stamp(at) < window.closes_at:
        return 'OUTSIDE_SESSION'
    day = at.astimezone(TZ).date()
    if not calendar.es_dia_habil_operativo(day):
        return 'CALENDAR_UNAVAILABLE_OR_CLOSED'
    next_day = day+timedelta(days=1)
    while (next_day-day).days <= MAX_CALENDAR_DAYS:
        if calendar.es_dia_habil_operativo(next_day):
            break
        next_day += timedelta(days=1)
    if (next_day-day).days > MAX_CALENDAR_DAYS:
        return 'NEXT_SETTLEMENT_DAY_OUTSIDE_LIMIT'
    if aware_datetime(window.liquidity_deadline).astimezone(TZ).date()!=next_day:
        return 'DEADLINE_NOT_NEXT_SETTLEMENT_DAY'
    return ''


def allocate_conservative(broker, offers, window, request_id, *, as_of=None):
    """Hasta una colocación por día y ninguna caución ARS previa pendiente.

    HOLD idempotente; para reevaluar una oferta nueva hace falta otra clave.
    La caja se congela sólo en la primera evaluación válida del día. Un cambio
    de sesión/perfil o retroceso de reloj requiere revisión, no un reset.
    """
    if not isinstance(request_id,str) or not request_id.strip():
        raise ValueError('Falta clave de intento de tesorería')
    if window is not None and not isinstance(window,CaucionWindow):
        raise ValueError('Se requiere CaucionWindow o None')
    offers = normalized_offers(offers)
    context = {'profile':PROFILE,'window':asdict(window) if window else None,
               'offers':[offer_payload(o) for o in offers.values()]}
    request_hash = fingerprint(context)
    init_schema(broker.store)
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT * FROM paper_caucion_treasury_attempts WHERE request_id=?',(request_id,)).fetchone()
        if previous:
            if previous['request_hash']!=request_hash:
                raise ValueError('Clave de tesorería reutilizada con datos diferentes')
            return json.loads(previous['result_json'])
        at = aware_datetime(broker.clock_fn() if broker.clock_fn else as_of)
        day = at.astimezone(TZ).date().isoformat()
        result = dict(profile=PROFILE,mode='PAPER_CAUCION_TREASURY',data_certified=False,
            promotion_allowed=False,status='HOLD',code='',at=stamp(at),currency=CURRENCY,
            day=day,reference_cash=None,reserve_cash=None,principal_limit=None,
            current_cash=None,allocation=None,manifest=context)
        code = window_error(window,at)
        latest = c.execute('SELECT MAX(evaluated_at) FROM paper_caucion_treasury_attempts').fetchone()[0]
        if latest and latest>stamp(at):
            code = 'TREASURY_CLOCK_ROLLBACK'
        existing = [dict(r) for r in c.execute('SELECT * FROM paper_cauciones WHERE currency=?',(CURRENCY,))]
        if not code:
            if any(aware_datetime(p['opened_at']).astimezone(TZ).date().isoformat()==day for p in existing):
                code = 'DAILY_PLACEMENT_LIMIT'
            elif any(p['status']=='OPEN' or p['settled_at'] is None for p in existing):
                code = 'EXISTING_CAUCION_EXPOSURE'
        frozen = c.execute('SELECT * FROM paper_caucion_treasury_days WHERE day=?',(day,)).fetchone()
        if not code and frozen and (frozen['profile']!=PROFILE or json.loads(frozen['window_json'])!=context['window']):
            code = 'FROZEN_POLICY_CHANGED'
        if not code and not broker.daily_risk:
            code = 'DAILY_RISK_NOT_CONFIGURED'
        if not code:
            cash = broker._cash(at,CURRENCY,connection=c,for_execution=True)
            result['current_cash'] = str(cash)
            if not frozen:
                if cash < Decimal('.02'):
                    code = 'NO_AVAILABLE_CASH'
                else:
                    reserve = (cash*RESERVE_FRACTION).quantize(Decimal('.01'),rounding=ROUND_CEILING)
                    limit = (cash-reserve).quantize(Decimal('.01'),rounding='ROUND_DOWN')
                    c.execute('INSERT INTO paper_caucion_treasury_days VALUES(?,?,?,?,?,?,?)',
                        (day,PROFILE,stamp(at),str(cash),str(reserve),str(limit),json.dumps(context['window'],sort_keys=True)))
                    frozen = c.execute('SELECT * FROM paper_caucion_treasury_days WHERE day=?',(day,)).fetchone()
        if frozen:
            reference = decimal_value(frozen['reference_cash'],'referencia',positive=True)
            reserve = (reference*RESERVE_FRACTION).quantize(Decimal('.01'),rounding=ROUND_CEILING)
            limit = (reference-reserve).quantize(Decimal('.01'),rounding='ROUND_DOWN')
            if reserve!=decimal_value(frozen['reserve_cash'],'reserva') or limit!=decimal_value(frozen['principal_limit'],'tope'):
                raise ValueError('Presupuesto diario inconsistente')
            result.update(reference_cash=str(reference),reserve_cash=str(reserve),principal_limit=str(limit))
        if not code:
            policy = CaucionPolicy(frozen_at=window.known_at,currency=CURRENCY,
                reserve_cash=reserve,maximum_cash_fraction=Decimal(1),maximum_principal=limit,
                liquidity_deadline=window.liquidity_deadline,maximum_quote_age_seconds=QUOTE_MAX_SECONDS,
                participation=PARTICIPATION,minimum_net_profit=Decimal(0),
                ranking='EARLIEST_MATURITY_NET_RETURN',session_open_at=window.opens_at,
                session_close_at=window.closes_at,session_source=window.source)
            key = 'treasury:'+fingerprint({'request_id':request_id})
            allocation_hash = fingerprint({'policy':encoded(asdict(policy)),'offers':context['offers']})
            allocation = allocate_locked(broker,offers,policy,key,allocation_hash,c,as_of=at)
            result.update(allocation=allocation,status=allocation['status'])
            code = allocation['code']
        result['code'] = code
        c.execute('INSERT INTO paper_caucion_treasury_attempts VALUES(?,?,?,?,?)',
            (request_id,request_hash,stamp(at),day,json.dumps(result,ensure_ascii=False,allow_nan=False)))
        return result

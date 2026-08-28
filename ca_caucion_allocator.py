"""Selección determinista y colocación PAPER, sin red ni programación automática.

Compara presupuestos completos para capitales exactos. No extrapola comisiones,
no certifica datos de PPI y no supone reinversión a la misma tasa en el futuro.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import json

from bl_candle_engine import fingerprint, stamp
from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bt_caucion_paper import CaucionOffer, TZ, book_key, book_payload, money, offer_payload
from bw_daily_risk import loss_limit_crossed

ZERO = Decimal('0')


def encoded(value):
    if isinstance(value, Decimal):
        return format(value.normalize(),'f')
    if isinstance(value, dict):
        return {k:encoded(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):
        return [encoded(v) for v in value]
    return value


@dataclass(frozen=True)
class CaucionPolicy:
    frozen_at: str
    currency: str
    reserve_cash: Decimal
    maximum_cash_fraction: Decimal
    maximum_principal: Decimal
    liquidity_deadline: str
    maximum_quote_age_seconds: Decimal
    participation: Decimal
    minimum_net_profit: Decimal
    ranking: str  # NET_PROFIT o NET_RETURN_PER_DAY, elección explícita.
    session_open_at: str
    session_close_at: str
    session_source: str

    def __post_init__(self):
        for field in ('frozen_at','liquidity_deadline','session_open_at','session_close_at'):
            object.__setattr__(self,field,stamp(getattr(self,field)))
        object.__setattr__(self,'currency',cash_currency(self.currency))
        for field in ('reserve_cash','maximum_cash_fraction','maximum_principal',
                      'maximum_quote_age_seconds','participation','minimum_net_profit'):
            value = decimal_value(getattr(self,field),field,nonnegative=True,
                                  positive=field in {'maximum_cash_fraction','maximum_principal','participation'})
            if field in {'maximum_cash_fraction','participation'} and value > 1:
                raise ValueError('Fracción fuera de (0,1]')
            if field in {'reserve_cash','maximum_principal','minimum_net_profit'} and value != money(value):
                raise ValueError('Importe debe expresarse en centavos')
            object.__setattr__(self,field,value)
        if (self.ranking not in {'NET_PROFIT','NET_RETURN_PER_DAY'} or not self.session_source.strip()
                or self.session_source.strip().upper()=='UNKNOWN'):
            raise ValueError('Falta criterio de comparación o fuente de sesión')
        if not self.frozen_at <= self.session_open_at < self.session_close_at <= self.liquidity_deadline:
            raise ValueError('Ventana de sesión/plazo incompatible con la configuración congelada')

    @property
    def version(self):
        return 'caucion-policy-v17:' + fingerprint(encoded(asdict(self)))[:16]


def normalized_offers(offers):
    unique = {}
    for offer in offers:
        if not isinstance(offer,CaucionOffer):
            raise ValueError('Se requieren ofertas normalizadas, no payloads supuestos')
        unique[fingerprint(offer_payload(offer))] = offer
    return dict(sorted(unique.items()))


def choose(offers, policy, *, at, cash, risk, participation, consumed=None):
    """Plan puro; sólo valida datos aportados, nunca los llama verificados."""
    if not isinstance(policy,CaucionPolicy):
        raise ValueError('Falta política explícita')
    at = aware_datetime(at)
    cash = decimal_value(cash,'caja')
    participation = min(policy.participation,decimal_value(participation,'participación',positive=True))
    offers = normalized_offers(offers)
    consumed = consumed or {}
    budget = max(ZERO,cash-policy.reserve_cash)*policy.maximum_cash_fraction
    result = {'mode':'PAPER_CAUCION_PLAN','promotion_allowed':False,'data_certified':False,
        'at':stamp(at),'policy_version':policy.version,'currency':policy.currency,'cash':cash,
        'cash_budget':budget,'participation':participation,'daily_risk':risk,
        'selected':None,'candidates':[],
        'manifest':{'policy':encoded(asdict(policy)),'offers':[offer_payload(o) for o in offers.values()]}}
    global_error = ('POLICY_NOT_KNOWN' if stamp(at)<policy.frozen_at else
        'OUTSIDE_CONFIRMED_SESSION' if not policy.session_open_at <= stamp(at) < policy.session_close_at else
        'DAILY_RISK_NOT_CONFIGURED' if risk is None else
        'DAILY_RISK_CURRENCY_MISMATCH' if risk.get('currency') != policy.currency else
        'DAILY_RISK_'+risk['state'] if risk['state']!='READY' else '')
    books, conflicts, budgets = {}, set(), {}
    for offer in offers.values():
        key, payload = book_key(offer), book_payload(offer)
        if key in books and books[key] != payload:
            conflicts.add(key)
        books[key] = payload
        budget_key = (key,offer.fee_quote_principal)
        if budget_key in budgets and budgets[budget_key] != offer.quoted_total_fees:
            conflicts.add(key)
        budgets[budget_key] = offer.quoted_total_fees
    for candidate_id, offer in offers.items():
        row = {'candidate_id':candidate_id,'instrument_id':offer.instrument_id,'code':global_error or 'ELIGIBLE'}
        result['candidates'].append(row)
        if global_error:
            continue
        try:
            key = book_key(offer)
            if offer.currency != policy.currency:
                raise ValueError('OTHER_CURRENCY')
            if key in conflicts:
                raise ValueError('CONFLICTING_BOOK_OR_BUDGET')
            if isinstance(consumed.get(key),str):
                raise ValueError(consumed[key])
            if offer.metadata_source.strip().upper()=='UNKNOWN':
                raise ValueError('UNKNOWN_CONTRACT_SOURCE')
            age = Decimal(str((at-aware_datetime(offer.quoted_at)).total_seconds()))
            if not 0 <= age <= policy.maximum_quote_age_seconds:
                raise ValueError('QUOTE_STALE_OR_FUTURE')
            if offer.start_date != at.astimezone(TZ).date().isoformat():
                raise ValueError('START_DATE_MISMATCH')
            if not at < aware_datetime(offer.maturity_at) <= aware_datetime(policy.liquidity_deadline):
                raise ValueError('MATURITY_OUTSIDE_LIQUIDITY_WINDOW')
            if offer.quoted_total_fees is None:
                raise ValueError('EXPLICIT_COST_BUDGET_REQUIRED')
            principal = offer.fee_quote_principal
            if principal > policy.maximum_principal:
                raise ValueError('PRINCIPAL_CAP')
            interest,fees,net = offer.economics(principal)
            used = decimal_value(consumed.get(key,ZERO),'profundidad usada',nonnegative=True)
            if principal + used > offer.available_principal*participation:
                raise ValueError('DEPTH_EXHAUSTED')
            debit = principal + (fees if offer.fee_payment=='UPFRONT' else ZERO)
            if debit > budget:
                raise ValueError('CASH_RESERVE_OR_FRACTION')
            if net <= 0 or net < policy.minimum_net_profit:
                raise ValueError('NET_PROFIT_TOO_LOW')
            if loss_limit_crossed(decimal_value(risk['daily_pnl'],'PnL diario')-fees,ZERO,risk['loss_budget']):
                raise ValueError('DAILY_RISK_PROJECTED_LOSS')
            row.update(principal=principal,gross_interest=interest,fees=fees,net_profit=net,
                cash_debit=debit,net_return_per_day=net/debit/offer.interest_days,
                interest_days=offer.interest_days,maturity_at=stamp(offer.maturity_at))
        except ValueError as exc:
            row['code'] = str(exc)
    eligible = [r for r in result['candidates'] if r['code']=='ELIGIBLE']
    metric = 'net_profit' if policy.ranking=='NET_PROFIT' else 'net_return_per_day'
    if eligible:
        result['selected'] = min(eligible,key=lambda r:(-r[metric],r['interest_days'],r['cash_debit'],r['candidate_id']))
    result['code'] = global_error or ('CANDIDATE_SELECTED' if result['selected'] else 'NO_ELIGIBLE_OFFER')
    result['plan_id'] = fingerprint(encoded(result))
    return encoded(result)


def allocate(broker, offers, policy, request_id, *, as_of=None):
    """Una decisión durable y a lo sumo una colocación simulada por request.

    No hay escáner ni red aquí. Cash/riesgo/selección/colocación se comprueban
    bajo la misma transacción. Un HOLD también es idempotente; nuevo evento,
    nueva clave. No se acepta ejecutar un plan externo sin recalcularlo.
    """
    if not isinstance(policy,CaucionPolicy) or not isinstance(request_id,str) or not request_id.strip():
        raise ValueError('Faltan política explícita o clave de asignación')
    offers = normalized_offers(offers)
    request_hash = fingerprint({'policy':encoded(asdict(policy)),
                               'offers':[offer_payload(o) for o in offers.values()]})
    with broker.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT * FROM paper_caucion_allocations WHERE request_id=?',(request_id,)).fetchone()
        if previous:
            if previous['request_hash'] != request_hash:
                raise ValueError('Clave de asignación reutilizada con términos diferentes')
            return json.loads(previous['decision_json'])
        if c.execute('SELECT 1 FROM paper_cauciones WHERE request_id=?',(request_id,)).fetchone():
            raise ValueError('Clave ya utilizada por una colocación explícita')
        at = broker.clock_fn() if broker.clock_fn else as_of
        if at is None:
            raise ValueError('Falta reloj explícito de la asignación')
        at = aware_datetime(at)
        cash = broker._cash(at,policy.currency,connection=c,for_execution=True)
        risk = broker.daily_risk.evaluate(at,connection=c)[policy.currency] if broker.daily_risk else None
        consumed = {}
        for offer in offers.values():
            try:
                consumed[book_key(offer)] = broker.cauciones.used_principal(offer,connection=c)
            except ValueError as exc:
                consumed[book_key(offer)] = str(exc)
        decision = choose(offers.values(),policy,at=at,cash=cash,risk=risk,
                          participation=broker.participation,consumed=consumed)
        selected, position = decision['selected'], None
        if selected:
            offer = offers[selected['candidate_id']]
            position = broker.cauciones.place(offer,selected['principal'],request_id,at,
                lambda currency,clock,connection: broker._cash(clock,currency,connection=connection,for_execution=True),
                reserve=policy.reserve_cash,participation=min(policy.participation,broker.participation),
                max_quote_age_seconds=policy.maximum_quote_age_seconds,
                admission=lambda connection,currency,clock,fees: broker.daily_risk.projected_admission_error(
                    currency,clock,fees,connection=connection),connection=c)
        decision['paper_id'] = position['paper_id'] if position else None
        decision['status'] = 'PLACED_SIMULATED' if position else 'HOLD'
        c.execute('INSERT INTO paper_caucion_allocations VALUES(?,?,?,?,?)',
            (request_id,request_hash,stamp(at),json.dumps(decision,ensure_ascii=False,allow_nan=False),decision['paper_id']))
        return decision

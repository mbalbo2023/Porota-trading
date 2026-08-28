"""Lectura consistente del historial de asignación PAPER; sin ejecutar ni migrar.

CONSISTENT significa concordancia interna del registro, no conciliación con PPI,
vigencia de la caja/cotización ni certificación de la fuente de mercado.
"""
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from bl_candle_engine import fingerprint, stamp
from bs_instrument_contracts import decimal_value
from bt_caucion_paper import CaucionOffer, offer_payload
from ca_caucion_allocator import CaucionPolicy, encoded


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _json(value):
    def invalid_constant(value):
        raise ValueError('NON_FINITE_JSON')
    return json.loads(value, parse_constant=invalid_constant)


def _validated(row, connection):
    decision = _json(row['decision_json'])
    _require(isinstance(decision, dict), 'INVALID_DECISION')
    _require(decision['mode'] == 'PAPER_CAUCION_PLAN'
             and decision['promotion_allowed'] is False
             and decision['data_certified'] is False, 'INVALID_SCOPE')
    _require(stamp(decision['at']) == stamp(row['evaluated_at']), 'EVALUATION_TIME_MISMATCH')
    manifest = decision['manifest']
    _require(fingerprint(manifest) == row['request_hash'], 'REQUEST_MANIFEST_MISMATCH')
    plan = {k:v for k,v in decision.items() if k not in {'plan_id','paper_id','status'}}
    _require(fingerprint(plan) == decision['plan_id'], 'PLAN_MISMATCH')
    policy = CaucionPolicy(**manifest['policy'])
    _require(policy.version == decision['policy_version']
             and policy.currency == decision['currency'], 'POLICY_MISMATCH')
    cash = decimal_value(decision['cash'], 'cash')
    budget = decimal_value(decision['cash_budget'], 'budget', nonnegative=True)
    _require(budget == max(Decimal(0), cash-policy.reserve_cash)*policy.maximum_cash_fraction,
             'CASH_BUDGET_MISMATCH')
    participation = decimal_value(decision['participation'], 'participation', positive=True)
    _require(participation <= policy.participation, 'PARTICIPATION_MISMATCH')
    _require(isinstance(manifest['offers'], list) and isinstance(decision['candidates'], list),
             'INVALID_CANDIDATES')
    offers = {}
    for payload in manifest['offers']:
        offer = CaucionOffer(**payload)
        _require(offer_payload(offer) == payload, 'NON_CANONICAL_OFFER')
        key = fingerprint(offer_payload(offer))
        _require(key not in offers, 'DUPLICATE_OFFER')
        offers[key] = offer
    candidates = {}
    for candidate in decision['candidates']:
        key = candidate['candidate_id']
        _require(key in offers and key not in candidates, 'CANDIDATE_MISMATCH')
        _require(candidate['instrument_id'] == offers[key].instrument_id
                 and isinstance(candidate['code'], str) and bool(candidate['code']), 'INVALID_CANDIDATE')
        candidates[key] = candidate
        if candidate['code'] == 'ELIGIBLE':
            offer = offers[key]
            principal = decimal_value(candidate['principal'], 'principal', positive=True)
            _require(principal == offer.fee_quote_principal and offer.currency == policy.currency,
                     'ELIGIBLE_TERMS_MISMATCH')
            interest, fees, net = offer.economics(principal)
            debit = principal + (fees if offer.fee_payment == 'UPFRONT' else Decimal(0))
            expected = encoded(dict(gross_interest=interest, fees=fees, net_profit=net,
                cash_debit=debit, net_return_per_day=net/debit/offer.interest_days))
            _require(all(decimal_value(candidate[k], k) == Decimal(v) for k,v in expected.items())
                     and candidate['interest_days'] == offer.interest_days
                     and stamp(candidate['maturity_at']) == stamp(offer.maturity_at), 'ECONOMICS_MISMATCH')
    _require(set(candidates) == set(offers), 'MISSING_CANDIDATES')
    _require(decision['paper_id'] == row['paper_id'], 'PLACEMENT_LINK_MISMATCH')
    selected = decision['selected']
    eligible = [r for r in candidates.values() if r['code'] == 'ELIGIBLE']
    if selected is None:
        _require(decision['status'] == 'HOLD' and row['paper_id'] is None
                 and not eligible and isinstance(decision['code'], str)
                 and decision['code'] and decision['code'] != 'CANDIDATE_SELECTED', 'HOLD_MISMATCH')
        return decision, None
    _require(decision['status'] == 'PLACED_SIMULATED' and row['paper_id']
             and decision['code'] == 'CANDIDATE_SELECTED'
             and selected == candidates[selected['candidate_id']] and selected in eligible,
             'SELECTION_MISMATCH')
    metric = 'net_profit' if policy.ranking == 'NET_PROFIT' else 'net_return_per_day'
    winner = min(eligible, key=lambda r:(-Decimal(r[metric]), r['interest_days'],
                                       Decimal(r['cash_debit']), r['candidate_id']))
    _require(selected == winner, 'RANKING_MISMATCH')
    placement = connection.execute('SELECT * FROM paper_cauciones WHERE paper_id=?', (row['paper_id'],)).fetchone()
    _require(placement is not None, 'MISSING_PLACEMENT')
    placement = dict(placement)
    offer = offers[selected['candidate_id']]
    _require(placement['request_id'] == row['request_id'] and placement['source'] == 'PRODUCTION_PAPER'
             and placement['instrument_id'] == offer.instrument_id and placement['currency'] == offer.currency
             and placement['fee_payment'] == offer.fee_payment
             and placement['interest_days'] == offer.interest_days
             and placement['day_count_basis'] == offer.day_count_basis
             and stamp(placement['opened_at']) == stamp(decision['at'])
             and stamp(placement['maturity_at']) == stamp(offer.maturity_at), 'LEDGER_TERMS_MISMATCH')
    for field, value in (('principal', selected['principal']), ('gross_interest', selected['gross_interest']),
                         ('total_fees', selected['fees']), ('annual_rate_fraction', offer.annual_rate_fraction)):
        _require(decimal_value(placement[field], field) == Decimal(value), 'LEDGER_AMOUNT_MISMATCH')
    _require(offer_payload(CaucionOffer(**_json(placement['terms_json']))) == offer_payload(offer),
             'LEDGER_OFFER_MISMATCH')
    _require((placement['status'] == 'OPEN' and placement['settled_at'] is None)
             or (placement['status'] == 'MATURED' and placement['settled_at'] is not None
                 and stamp(placement['settled_at']) >= stamp(placement['maturity_at'])), 'LEDGER_STATE_MISMATCH')
    return decision, {k:placement[k] for k in ('paper_id','status','opened_at','maturity_at','settled_at')}


def allocation_history(path, *, limit=25, offset=0):
    """Página de decisiones y ledger en una única transacción de sólo lectura.

Orden por instante (no por texto de zona horaria). total y estado se refieren
al historial/página indicados. Nunca interpreta una lectura fallida como vacío.
"""
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('Paginación inválida')
    result = dict(mode='PAPER_CAUCION_AUDIT', promotion_allowed=False, data_certified=False,
                  state='MISSING_DATABASE', total=None, limit=limit, offset=offset,
                  has_more=False, records=[])
    path = Path(path)
    if not path.exists():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            c.execute('BEGIN')
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_caucion_allocations'").fetchone():
                return dict(result, state='MISSING_TABLE')
            result['total'] = c.execute('SELECT COUNT(*) FROM paper_caucion_allocations').fetchone()[0]
            rows = c.execute('''SELECT * FROM paper_caucion_allocations
                ORDER BY julianday(evaluated_at) DESC, request_id DESC LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
            for row in rows:
                record = dict(request_id=row['request_id'], evaluated_at=row['evaluated_at'],
                              state='CONSISTENT', issue=None, decision=None, placement=None)
                try:
                    record['decision'], record['placement'] = _validated(row, c)
                except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError) as exc:
                    # No mostrar cifras parcialmente validadas ni inventar una colocación.
                    record.update(state='INCONSISTENT', issue=str(exc) if isinstance(exc, ValueError)
                                  else 'INVALID_RECORD_SHAPE')
                result['records'].append(record)
            result['has_more'] = offset+len(rows) < result['total']
            result['state'] = ('PARTIAL' if any(r['state'] != 'CONSISTENT' for r in result['records'])
                               else 'READABLE' if rows else 'EMPTY_PAGE' if result['total'] else 'EMPTY')
    except (sqlite3.Error, OSError):
        result.update(state='READ_ERROR', records=[], total=None, has_more=False)
    return result

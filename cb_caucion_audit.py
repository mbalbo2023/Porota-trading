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
from bt_caucion_paper import CaucionOffer, money, offer_payload, validate_position
from ca_caucion_allocator import CaucionPolicy, encoded, selection_key, _paper_tariff_authorized


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _json(value):
    def invalid_constant(value):
        raise ValueError('NON_FINITE_JSON')
    return json.loads(value, parse_constant=invalid_constant)


def _validated(row, connection):
    from rc6_caucion_fee_model import old_public_modeled_fee
    from scripts.rc6_caucion_fee_restatement_apply import read_verified_restated_fees
    from scripts.rc6_caucion_fee_ro_provenance import LEGACY_AUTHORITY
    # The reader validates all six immutable receipts and their original rows
    # in this same SQLite snapshot before exposing even a historical decision.
    # Without the whole set the page suppresses every amount.
    restated = read_verified_restated_fees(connection)
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
    offers, historical = {}, set()
    for payload in manifest['offers']:
        legacy = isinstance(payload, dict) and payload.get('fee_authority') == LEGACY_AUTHORITY
        if legacy:
            _require(row['paper_id'] in restated and decision['status'] == 'PLACED_SIMULATED',
                     'HISTORICAL_FEE_RECEIPT_REQUIRED')
            # Construct only for term validation. Never install the retired fee
            # authority in a current offer, and never price it with today's fee.
            offer = CaucionOffer(**(payload | {'fee_authority': None}))
            canonical = offer_payload(offer) | {'fee_authority': LEGACY_AUTHORITY}
        else:
            offer = CaucionOffer(**payload)
            canonical = offer_payload(offer)
        _require(canonical == payload, 'NON_CANONICAL_OFFER')
        key = fingerprint(payload)
        _require(key not in offers, 'DUPLICATE_OFFER')
        offers[key] = offer
        if legacy:
            historical.add(key)
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
            # An explicit quote fixes the exact principal. A versioned ARS
            # PAPER tariff intentionally has no quote principal: the selector
            # computes it within policy limits and economics() prices it.
            cost_authorized = (principal == offer.fee_quote_principal
                               if offer.quoted_total_fees is not None else
                               offer.fee_quote_principal is None and
                               (key in historical or _paper_tariff_authorized(offer)))
            _require(cost_authorized and offer.currency == policy.currency,
                     'ELIGIBLE_TERMS_MISMATCH')
            if key in historical:
                _require(principal == money(principal) and principal % offer.principal_step == 0
                         and offer.minimum_principal <= principal <= offer.available_principal,
                         'HISTORICAL_PRINCIPAL_INVALID')
                interest = money(principal * offer.annual_rate_fraction * offer.interest_days
                                 / offer.day_count_basis)
                fees = old_public_modeled_fee(principal, offer.interest_days)
                net = interest - fees
            else:
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
    winner = min(eligible, key=lambda r:selection_key(r,policy.ranking))
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
    terms = _json(placement['terms_json'])
    original_offer = next(p for p in manifest['offers'] if fingerprint(p) == selected['candidate_id'])
    if selected['candidate_id'] in historical:
        _require(isinstance(terms, dict) and terms.get('fee_authority') == LEGACY_AUTHORITY,
                 'LEDGER_OFFER_MISMATCH')
        normalized = offer_payload(CaucionOffer(**(terms | {'fee_authority': None}))) | {
            'fee_authority': LEGACY_AUTHORITY}
    else:
        normalized = offer_payload(CaucionOffer(**terms))
    # The placement stores asdict(offer), whereas the decision stores normalized
    # UTC timestamps and decimal strings. Verify semantic canonical equivalence;
    # the historical raw bytes remain fingerprint-sealed by the six receipts.
    _require(normalized == original_offer, 'LEDGER_OFFER_MISMATCH')
    _require((placement['status'] == 'OPEN' and placement['settled_at'] is None)
             or (placement['status'] == 'MATURED' and placement['settled_at'] is not None
                 and stamp(placement['settled_at']) >= stamp(placement['maturity_at'])), 'LEDGER_STATE_MISMATCH')
    result = {k:placement[k] for k in ('paper_id','status','opened_at','maturity_at','settled_at')}
    if selected['candidate_id'] in historical:
        receipt = restated.get(placement['paper_id'])
        _require(receipt is not None and receipt['original_total_fees'] == placement['total_fees']
                 and receipt['broker_account_fees_verified'] == 0,
                 'HISTORICAL_FEE_RECEIPT_MISMATCH')
        effective = decimal_value(receipt['effective_total_fees'], 'costo público', nonnegative=True)
        original = decimal_value(placement['total_fees'], 'costo histórico', nonnegative=True)
        _require(effective >= original, 'HISTORICAL_FEE_COULD_INCREASE_PNL')
        result.update(fee_evidence_scope='PUBLIC_MODEL_PAPER_ONLY_ACCOUNT_UNVERIFIED',
                      original_total_fees=placement['total_fees'],
                      effective_total_fees=receipt['effective_total_fees'],
                      effective_net_profit=encoded(decimal_value(placement['gross_interest'], 'interés')-effective),
                      fee_known_at_utc=receipt['known_at_utc'], fee_model=receipt['model'],
                      broker_account_fees_verified=False)
    else:
        validate_position(placement)
    return decision, result


def allocation_history(path, *, limit=25, offset=0, require_workspace=False):
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
        if require_workspace:
            from cg_paper_workspace import checked_path
            path = checked_path(path)
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            c.execute('BEGIN')
            if require_workspace:
                from cg_paper_workspace import identity_from_connection
                identity_from_connection(c)
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
    except (sqlite3.Error, OSError, ValueError):
        result.update(state='READ_ERROR', records=[], total=None, has_more=False)
    return result

"""RC6 live caucion evaluator for PRODUCTION_PAPER.

Reads only PPI market data through the existing fail-closed allowlist. Persists
observable evaluations but can never route or allocate an order. Promotion
remains HOLD until the complete fee/rights evidence is certified.
"""
from __future__ import annotations
import json
import signal
import threading
import time
from datetime import datetime
from decimal import Decimal

from bd_ppi_readonly_guard import ProductionMarketReader, retry_read, session_invalid, classify_read_error
from bf_production_paper_observer import _secret, _market_phase, _levels, _level, TZ
from be_paper_engine import now_iso
from cg_paper_workspace import runtime_store
from rc6_caucion_contract import parse_ticker, minimum_principal, gross_interest, known_ppi_commission_percent, inside_session, evidence_blocker

ORDER_ROUTING_ALLOWED=False
SETTLEMENT='INMEDIATA'
INTERVAL_SECONDS=300


def ensure_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_caucion_evaluations_rc6(
          ticker TEXT PRIMARY KEY,
          evaluated_at TEXT NOT NULL,
          settlement TEXT NOT NULL,
          currency TEXT NOT NULL,
          term_days INTEGER NOT NULL,
          current_tna TEXT,
          best_bid_tna TEXT,
          best_bid_quantity TEXT,
          minimum_principal TEXT NOT NULL,
          gross_interest_at_minimum TEXT,
          known_ppi_commission_percent TEXT,
          contract_state TEXT NOT NULL,
          quote_state TEXT NOT NULL,
          cost_state TEXT NOT NULL,
          final_state TEXT NOT NULL,
          blocker TEXT NOT NULL,
          detail_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_caucion_evaluator_state_rc6(
          id INTEGER PRIMARY KEY CHECK(id=1),
          heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL,
          detail TEXT NOT NULL,
          evaluated_contracts INTEGER NOT NULL DEFAULT 0,
          readable_contracts INTEGER NOT NULL DEFAULT 0
        );
        """)


def _decimal(value):
    try:
        if value in (None,''): return None
        result=Decimal(str(value))
        return result if result.is_finite() else None
    except Exception:
        return None


def _current_rate(current):
    if not isinstance(current,dict): return None
    return _decimal(current.get('price'))


def _best_bid(book):
    bids=_levels(book,'bid')
    if not bids: return None,None
    price,quantity=_level(bids[0])
    return (_decimal(price),_decimal(quantity))


def _write(store,row):
    fields=(
      'ticker','evaluated_at','settlement','currency','term_days','current_tna',
      'best_bid_tna','best_bid_quantity','minimum_principal','gross_interest_at_minimum',
      'known_ppi_commission_percent','contract_state','quote_state','cost_state',
      'final_state','blocker','detail_json')
    with store.connect() as c:
        c.execute(
          'INSERT OR REPLACE INTO paper_caucion_evaluations_rc6 ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',
          tuple(row.get(f) for f in fields))


def _state(store,state,detail,evaluated=0,readable=0):
    at=now_iso()
    with store.connect() as c:
        c.execute('INSERT OR REPLACE INTO paper_caucion_evaluator_state_rc6 VALUES(1,?,?,?,?,?)',
                  (at,state,str(detail)[:240],int(evaluated),int(readable)))
        c.execute('INSERT OR REPLACE INTO api_health(component,state,detail,checked_at,last_success_at,source) VALUES(?,?,?,?,?,?)',
                  ('CAUCION_LIVE_EVALUATOR','VERDE' if state=='READY' else 'AMARILLO',str(detail)[:240],at,at if state=='READY' else None,'PPI Producción / MarketData read-only'))


def candidate_tickers(store):
    with store.connect() as c:
        rows=c.execute("""SELECT ticker FROM candidate_universe
          WHERE upper(instrument_type)='CAUCIONES' AND upper(status)='AVAILABLE'
          AND upper(settlement)=? ORDER BY ticker""",(SETTLEMENT,)).fetchall()
    return [str(r[0]).upper() for r in rows]


def evaluate_cycle(store,reader,at=None):
    if ORDER_ROUTING_ALLOWED:
        raise RuntimeError('CAUCION_REAL_ORDER_INVARIANT_BROKEN')
    ensure_schema(store)
    now=at or datetime.now(TZ)
    tickers=candidate_tickers(store)
    evaluated=readable=0
    for ticker in tickers:
        evaluated+=1
        identity=parse_ticker(ticker); minimum=minimum_principal(ticker)
        current_rate=bid_rate=bid_quantity=None
        quote_state='UNAVAILABLE'; blocker='QUOTE_UNAVAILABLE'
        detail={'order_routing_allowed':False,'promotion_allowed':False,'source':'PPI_MARKETDATA_READ_ONLY'}
        try:
            current=retry_read(lambda:reader.current(ticker,'CAUCIONES',SETTLEMENT),retries=1)
            book=retry_read(lambda:reader.book(ticker,'CAUCIONES',SETTLEMENT),retries=1)
            current_rate=_current_rate(current); bid_rate,bid_quantity=_best_bid(book)
            quote_state='READABLE' if bid_rate is not None and bid_quantity is not None else 'NO_BID'
            if quote_state=='READABLE': readable+=1
            blocker='NO_BID' if quote_state!='READABLE' else evidence_blocker(ticker)
        except Exception as exc:
            if session_invalid(exc): raise
            blocker=classify_read_error(exc)
            detail['read_error']=blocker
        commission=known_ppi_commission_percent(ticker)
        gross=None
        if bid_rate is not None:
            try: gross=gross_interest(ticker,minimum,bid_rate)
            except Exception as exc: detail['economics_error']=type(exc).__name__
        if not inside_session(now): blocker='OUTSIDE_CAUCION_SESSION'
        row={
          'ticker':ticker,'evaluated_at':now.isoformat(),'settlement':SETTLEMENT,
          'currency':identity.currency_prefix,'term_days':identity.term_days,
          'current_tna':None if current_rate is None else str(current_rate),
          'best_bid_tna':None if bid_rate is None else str(bid_rate),
          'best_bid_quantity':None if bid_quantity is None else str(bid_quantity),
          'minimum_principal':str(minimum),
          'gross_interest_at_minimum':None if gross is None else str(gross),
          'known_ppi_commission_percent':None if commission is None else str(commission),
          'contract_state':'GREEN','quote_state':quote_state,'cost_state':'PARTIAL_EVIDENCE',
          'final_state':'HOLD','blocker':blocker,
          'detail_json':json.dumps(detail,sort_keys=True,separators=(',',':'))}
        _write(store,row)
    _state(store,'READY',f'evaluated={evaluated};readable={readable};promotion=blocked',evaluated,readable)
    return {'evaluated':evaluated,'readable':readable,'promotion_allowed':False}


def run_worker(store,stop,clock=time.monotonic):
    ensure_schema(store)
    reader=None; next_login=0.0
    try:
        while not stop.is_set():
            phase=_market_phase()
            if phase!='OPEN':
                _state(store,'WAITING_MARKET',phase)
                stop.wait(30); continue
            if reader is None:
                if clock()<next_login:
                    _state(store,'COOLDOWN','PPI_LOGIN_COOLDOWN')
                    stop.wait(15); continue
                try:
                    reader=ProductionMarketReader(*_secret(),audit=store.audit_http)
                    reader.login_once()
                except Exception as exc:
                    if reader: reader.close()
                    reader=None; next_login=clock()+300
                    _state(store,'ERROR',classify_read_error(exc))
                    stop.wait(30); continue
            try:
                evaluate_cycle(store,reader)
            except Exception as exc:
                if session_invalid(exc):
                    reader.close(); reader=None; next_login=clock()+60
                    _state(store,'ERROR','PPI_SESSION_INVALID')
                else:
                    _state(store,'DEGRADED',type(exc).__name__)
            stop.wait(INTERVAL_SECONDS)
    finally:
        _state(store,'STOPPED','runtime stopped')
        if reader: reader.close()


def assert_invariants():
    assert ORDER_ROUTING_ALLOWED is False


def main():
    assert_invariants()
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    run_worker(runtime_store(),stop)
    return 0


if __name__=='__main__':
    raise SystemExit(main())

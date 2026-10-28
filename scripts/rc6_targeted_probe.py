#!/usr/bin/env python3
import tempfile
from pathlib import Path
from dataclasses import replace
from be_paper_engine import D, PaperBroker, PaperStore
from tests.test_production_paper_v1634 import quote
from tests.test_daily_risk_outbox_v17 import seed_closed, records, AT

root = Path(tempfile.mkdtemp())

store = PaperStore(str(root / 'risk.db'))
broker = PaperBroker(store, initial_cash='10000', daily_loss_pct='1')
original = broker.admission_error

def interleave(q, at, **kwargs):
    result = original(q, at, **kwargs)
    if not kwargs.get('connection'):
        seed_closed(store, opened=AT, closed=AT)
    return result

broker.admission_error = interleave
print('RISK_OPEN', broker._open(quote(at=AT), D('.8'), {}))
print('RISK_ROWS', records(store, 'paper_daily_risk'))
print('RISK_EVENTS', records(store, 'paper_events'))
print('RISK_OUTBOX', records(store, 'paper_notification_outbox'))
with store.connect() as c:
    print('RISK_TRIGGERS', [dict(r) for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")])

from bs_instrument_contracts import InstrumentContract
at = '2026-08-28T11:00:00-03:00'
base = quote(symbol='AAPLD', at=at, ask_size='10000', bid_size='10000')
contract = InstrumentContract(symbol='AAPLD', family='CEDEARS', settlement='INMEDIATA', currency='USD_MEP', market='BYMA', cash_multiplier=D('1'), quantity_step=D('1'), metadata_source='FIXTURE')
q = replace(base, asset_class='CEDEARS', settlement='INMEDIATA', currency='USD_MEP', market='BYMA', contract=contract)
b = PaperBroker(PaperStore(str(root / 'mep.db')), initial_cash_by_currency={'USD_MEP':'10000'})
print('MEP', b._open(q, D('.8'), {}))

b = PaperBroker(PaperStore(str(root / 'multi.db')), initial_cash='100000', daily_loss_pct='100')
for i in range(5):
    symbol = f'ABIERTA{i}'
    qi = quote(symbol=symbol, at=at, ask_size='1000')
    for p in b.store.open_positions():
        b.store.add_quote(quote(symbol=p['symbol'], at=at, ask_size='1000'))
    print('MULTI', i, b._open(qi, D('.8'), {}))

from tests.test_partial_allocation_integrity_v17 import sold
for target in ('source','allocation'):
    d = root / f'partial-{target}'
    d.mkdir()
    b, p, q, sell = sold(d)
    bad = dict(b.store.open_positions()[0])
    with b.store.connect() as c:
        if target == 'source':
            c.execute("UPDATE paper_spot_sales SET source='OTHER' WHERE paper_id=?", (p['paper_id'],))
        else:
            c.execute("UPDATE paper_spot_sales SET allocation_json='{}' WHERE paper_id=?", (p['paper_id'],))
    b.store.add_quote(quote(symbol=bad['symbol'], minute=2, ask_size='100'))
    good_q = quote(symbol='ALUA', minute=2, ask_size='100')
    print('PARTIAL', target, b._open(good_q, D('.8'), {}))

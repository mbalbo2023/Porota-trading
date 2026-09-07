#!/usr/bin/env bash
set -Eeuo pipefail
OBS=${POROTA_OBSERVER_CONTAINER:-porota_production_observer}

echo '=== RC6 CEPU LIQUIDITY DIAGNOSTIC READ ONLY ==='
echo "UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "LOCAL=$(TZ=America/Argentina/Buenos_Aires date +%Y-%m-%dT%H:%M:%S%z)"
sudo -n docker inspect -f 'OBSERVER={{.State.Running}}|{{.RestartCount}}|{{.HostConfig.ReadonlyRootfs}}|{{.Config.Image}}' "$OBS"

sudo -n docker exec -i "$OBS" python - <<'PY'
import json, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
p='/app/data/paper_v17/observer_v17.db'
tz=ZoneInfo('America/Argentina/Buenos_Aires')
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=20)
c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON')
print('DB_QUICK_CHECK='+str(c.execute('PRAGMA quick_check').fetchone()[0]))
state=c.execute('SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,last_market_data_at FROM observer_state WHERE id=1').fetchone()
print('OBSERVER_STATE='+json.dumps(dict(state) if state else {},sort_keys=True,ensure_ascii=False))

print('--- RECENT GATES CEPU/SUPV ---')
rows=c.execute('''SELECT id,evaluated_at,decision_key,symbol,technical_gate,ai_gate,patrimonial_gate,final_result,reason,paper_id,detail_json
                  FROM trade_gate_evaluations
                  WHERE symbol IN ('CEPU','SUPV')
                  ORDER BY id DESC LIMIT 12''').fetchall()
for r in rows:
    d=dict(r)
    raw=d.pop('detail_json',None)
    try: detail=json.loads(raw or '{}')
    except Exception: detail={'_invalid_detail_json':True}
    keep={k:detail.get(k) for k in (
        'currency','market','settlement','entry_price','contract_cash_multiplier','contract_quantity_step',
        'qty_by_risk','qty_by_cash','qty_by_liquidity','qty_by_position_cap','qty_by_total_cap',
        'max_position_pct','max_total_exposure_pct','book_source_at','trade_source_at','received_at') if k in detail}
    try:
        t=datetime.fromisoformat(str(d['evaluated_at']).replace('Z','+00:00'))
        if t.tzinfo is not None: d['evaluated_local']=t.astimezone(tz).isoformat(timespec='seconds')
    except Exception: pass
    d['detail_selected']=keep
    print('GATE='+json.dumps(d,sort_keys=True,ensure_ascii=False))

print('--- RECENT CEPU MARKET SNAPSHOTS ---')
for r in c.execute('''SELECT id,observed_at,symbol,asset_class,settlement,last,bid,ask,bid_size,ask_size,currency,market,book_at,trade_at,last_kind
                      FROM market_snapshots WHERE symbol='CEPU' ORDER BY id DESC LIMIT 20'''):
    print('SNAP='+json.dumps(dict(r),sort_keys=True,ensure_ascii=False))

print('--- CEPU BOOK CONSUMPTION ---')
if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_book_consumption'").fetchone():
    q='''SELECT b.fill_id,b.book_at,b.side,b.quantity,p.symbol,p.asset_class,p.settlement,p.currency,p.market,f.filled_at
         FROM paper_book_consumption b
         JOIN paper_fills f ON f.id=b.fill_id JOIN paper_positions p ON p.paper_id=f.paper_id
         WHERE p.symbol='CEPU' ORDER BY f.id DESC LIMIT 20'''
    for r in c.execute(q): print('BOOK_USED='+json.dumps(dict(r),sort_keys=True,ensure_ascii=False))

print('--- OPEN POSITIONS / CASH CONTEXT ---')
for r in c.execute("SELECT paper_id,symbol,status,quantity,entry_price,currency,market,opened_at FROM paper_positions WHERE status='OPEN' ORDER BY opened_at"):
    print('OPEN='+json.dumps(dict(r),sort_keys=True,ensure_ascii=False))

c.close()
print('MUTATION=NO')
print('REAL_ORDERS_EXPECTED=0')
PY

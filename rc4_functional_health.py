#!/usr/bin/env python3
"""Five-minute lightweight RC4 functional health snapshot (read-only DB)."""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB=os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')
OUT=Path(os.getenv('RC4_FUNCTIONAL_HEALTH_DIR','/app/data/functional_health'))

def collect(db=DB):
    c=sqlite3.connect(f'file:{db}?mode=ro',uri=True,timeout=5); c.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        observer=dict(c.execute('SELECT * FROM observer_state WHERE id=1').fetchone() or {}) if 'observer_state' in tables else {}
        supervisor=dict(c.execute('SELECT * FROM paper_supervisor_state WHERE id=1').fetchone() or {}) if 'paper_supervisor_state' in tables else {}
        exit_reader=dict(c.execute('SELECT * FROM paper_exit_reader_state WHERE id=1').fetchone() or {}) if 'paper_exit_reader_state' in tables else {}
        open_count=c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0] if 'paper_positions' in tables else 0
        stale=0
        if 'paper_positions' in tables and 'paper_position_marks' in tables:
            stale=c.execute("""SELECT COUNT(*) FROM paper_positions p LEFT JOIN paper_position_marks m USING(paper_id)
              WHERE p.status='OPEN' AND (m.marked_at IS NULL OR
              (julianday('now')-julianday(m.marked_at))*86400>120)""").fetchone()[0]
        risk=[dict(r) for r in c.execute("SELECT * FROM paper_daily_risk ORDER BY evaluated_at DESC LIMIT 8")] if 'paper_daily_risk' in tables else []
        return {'generated_at':datetime.now(timezone.utc).isoformat(),'observer':{k:observer.get(k) for k in ('mode','process_state','session_state','ppi_auth','heartbeat_at','last_market_data_at','real_orders_sent')},
                'supervisor':supervisor,'exit_reader':exit_reader,'open_positions':int(open_count),
                'stale_open_marks':int(stale),'daily_risk':risk,'db_write':False,'execution_allowed':False}
    finally:c.close()

def main():
    OUT.mkdir(parents=True,exist_ok=True); data=collect(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path=OUT/f'functional_health_{stamp}.json'; path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'latest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())

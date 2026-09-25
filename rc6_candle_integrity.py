#!/usr/bin/env python3
"""RC6 candle semantic-integrity audit.

This probe is strictly read-only. It enforces the project distinction between
provider OHLC and sampled trade bars: sampled bars may never claim COMPLETE
OHLCV, trades, volume, or VWAP.
"""
from __future__ import annotations

import json
import os
import sqlite3

DB = os.getenv('PAPER_V17_DB_PATH', '/app/data/paper_v17/observer_v17.db')
MAX_ROWS = 5000


def main() -> int:
    result = {
        'schema': 'POROTA_RC6_CANDLE_INTEGRITY_V1',
        'read_only': True,
        'network_order_test_performed': False,
        'checked_versions': 0,
        'violations': [],
    }
    try:
        c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        # Frequent intraday integrity must stay cheap and read-only. A full
        # PRAGMA quick_check can scan the entire observer DB and has caused
        # the 10-minute probe to overrun its 3-minute systemd timeout during
        # the market session. Full-file integrity is owned by the dedicated
        # postclose full-db-integrity job.
        c.execute('SELECT 1').fetchone()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'candle_series', 'candle_versions', 'candle_worker_state'}
        missing = sorted(required - tables)
        result['db_readable'] = True
        result['full_integrity_check_performed'] = False
        result['integrity_policy'] = 'FULL_SCAN_POSTCLOSE_ONLY'
        result['missing_tables'] = missing
        if missing:
            result['status'] = 'RED'
            c.close()
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 2
        rows = c.execute('''SELECT v.id,v.body_json,s.identity_json
            FROM candle_versions v JOIN candle_series s USING(series_id)
            ORDER BY v.id DESC LIMIT ?''', (MAX_ROWS,)).fetchall()
        for row in rows:
            result['checked_versions'] += 1
            try:
                body = json.loads(row['body_json'])
                identity = json.loads(row['identity_json'])
            except Exception:
                result['violations'].append({'id': row['id'], 'reason': 'INVALID_JSON'})
                continue
            price_kind = str(identity.get('price_kind') or '').upper()
            quality = str(body.get('quality') or '').upper()
            if price_kind != 'PROVIDER_OHLC':
                if quality == 'COMPLETE':
                    result['violations'].append({'id': row['id'], 'reason': 'SAMPLED_MARKED_COMPLETE'})
                for field in ('volume', 'trades', 'vwap'):
                    if body.get(field) is not None:
                        result['violations'].append({'id': row['id'], 'reason': f'SAMPLED_HAS_{field.upper()}'})
        worker = c.execute('SELECT cursor,heartbeat_at,state,detail FROM candle_worker_state WHERE id=1').fetchone()
        dirty = c.execute('SELECT COUNT(*) FROM candle_dirty').fetchone()[0] if 'candle_dirty' in tables else None
        c.close()
        result['worker_state'] = dict(worker) if worker else None
        result['dirty_bars'] = dirty
        result['status'] = 'RED' if result['violations'] else 'GREEN'
    except Exception as exc:
        result.update(status='RED', error=f'{type(exc).__name__}:{exc}')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result['status'] == 'GREEN' else 2


if __name__ == '__main__':
    raise SystemExit(main())

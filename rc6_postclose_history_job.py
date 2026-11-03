#!/usr/bin/env python3
"""RC6 bounded post-close PPI historical reconciliation.

Runs only on audited BYMA business days after the regular PAPER close. It reuses
the production read-only market client and the bounded historical downloader.
A recent historical attempt suppresses another pass for two hours to avoid
hammering PPI. No order method is reachable from this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma
from bd_ppi_readonly_guard import ProductionMarketReader
from bf_production_paper_observer import (_download_histories, _history_cutoff_repair_complete,
                                           _secret, _support_schema)
from cg_paper_workspace import runtime_store
from cv_history_store_adapter_hf6 import default_history_store
from cu_history_store_v2_hf6 import init_schema as init_history_schema

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
MIN_RETRY = timedelta(hours=2)
CLOSE_MINUTE = 17 * 60


def parse(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=TZ)
    except Exception:
        return None


def main() -> int:
    now = datetime.now(TZ)
    base = {
        'schema': 'POROTA_RC6_POSTCLOSE_HISTORY_V1',
        'generated_at_ar': now.isoformat(timespec='seconds'),
        'source': 'PPI_PRODUCTION_READ_ONLY',
        'real_order_capability': 'BLOCKED',
        'network_order_test_performed': False,
    }
    # Both the host ConditionPathExists gate and this container-local gate
    # must be deliberately opened after migration and exact cutoff review.
    if os.getenv('RC6_POSTCLOSE_HISTORY_RECOVERY') != 'APPROVED':
        print(json.dumps(base | {'status': 'BLOCKED_BY_POLICY',
                                 'reason': 'RC6_HISTORY_QUARANTINE',
                                 'history_writes_started': False}, sort_keys=True))
        return 0

    if not byma.es_dia_habil_operativo(now.date()):
        print(json.dumps(base | {'status': 'NOT_DUE', 'reason': 'BYMA_NON_OPERATIONAL_DAY'}, sort_keys=True))
        return 0
    if now.hour * 60 + now.minute < CLOSE_MINUTE + 15:
        print(json.dumps(base | {'status': 'NOT_DUE', 'reason': 'BEFORE_POST_CLOSE_WINDOW'}, sort_keys=True))
        return 0

    store = runtime_store()
    _support_schema(store)
    with store.connect() as c:
        runtime=c.execute('SELECT mode,session_state,real_orders_sent FROM observer_state WHERE id=1').fetchone()
    if (not runtime or str(runtime[0]) != 'PRODUCTION_PAPER'
            or str(runtime[1]) != 'MARKET_CLOSED' or int(runtime[2] or 0) != 0):
        print(json.dumps(base | {'status':'BLOCKED','reason':'RUNTIME_NOT_CLOSED_PAPER_SAFE'},sort_keys=True))
        return 2
    if not _history_cutoff_repair_complete():
        print(json.dumps(base | {'status':'BLOCKED','reason':'HISTORY_CUTOFF_REPAIR_INCOMPLETE'},sort_keys=True))
        return 2
    try:
        init_history_schema(default_history_store())
    except (ValueError, OSError) as exc:
        print(json.dumps(base | {'status':'BLOCKED','reason':type(exc).__name__+':'+str(exc)},sort_keys=True))
        return 2
    with store.connect() as c:
        row = c.execute('SELECT MAX(attempted_at) FROM production_history_attempts').fetchone()
    last = parse(row[0]) if row and row[0] else None
    if last and now - last.astimezone(TZ) < MIN_RETRY:
        print(json.dumps(base | {'status': 'NOT_DUE', 'reason': 'RECENT_HISTORY_ATTEMPT',
                                 'last_attempt_at': last.isoformat()}, sort_keys=True))
        return 0

    reader = None
    try:
        reader = ProductionMarketReader(*_secret(), audit=store.audit_http)
        reader.login_once()
        _download_histories(reader, store, incremental_only=True)
        with store.connect() as c:
            recent = c.execute('''SELECT state,COUNT(*) n FROM production_history_attempts
                                  WHERE julianday(attempted_at)>=julianday(?) GROUP BY state''',
                               ((now - timedelta(minutes=30)).isoformat(),)).fetchall()
            orders = c.execute('SELECT real_orders_sent FROM observer_state WHERE id=1').fetchone()[0]
            sync=c.execute("SELECT status FROM source_sync WHERE source='PPI_PRODUCTION_HISTORY'").fetchone()
        sync_state=str(sync[0]) if sync else 'NO_SYNC_EVIDENCE'
        result = base | {
            'status': ('RED' if int(orders or 0) != 0 or any(str(r[0]) == 'ERROR' for r in recent)
                       else 'GREEN' if sync_state == 'VERDE' else 'PARTIAL'),
            'recent_attempt_states': {str(r[0]): int(r[1]) for r in recent},
            'source_sync_state': sync_state,
            'real_orders_sent': int(orders or 0),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] == 'GREEN' else 2
    except Exception as exc:
        print(json.dumps(base | {'status': 'RED', 'error': f'{type(exc).__name__}:{exc}'},
                         ensure_ascii=False, sort_keys=True))
        return 2
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass


if __name__ == '__main__':
    raise SystemExit(main())

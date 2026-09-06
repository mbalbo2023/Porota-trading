#!/usr/bin/env python3
"""RC6 read-only audit of live-data, candles, historical ingestion and SHADOW learning.

This script opens SQLite databases with mode=ro/query_only, performs no network
calls, does not start scrapers and cannot send orders. It is intended for
post-deploy/preopen evidence collection.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


OBSERVER_DB = Path('/app/data/paper_v17/observer_v17.db')
HISTORY_DB = Path('/app/data/market_history.db')


def connect(path: Path):
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def scalar(c, sql, params=()):
    row = c.execute(sql, params).fetchone()
    return None if row is None else row[0]


def tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def table_count(c, name):
    return int(scalar(c, f'SELECT COUNT(*) FROM "{name}"') or 0)


def grouped(c, name, column, limit=30):
    rows = c.execute(
        f'SELECT "{column}",COUNT(*) n FROM "{name}" GROUP BY "{column}" ORDER BY n DESC LIMIT ?',
        (int(limit),),
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def generic_matching_tables(c, names):
    result = {}
    for name in sorted(names):
        if not any(token in name.lower() for token in ('history','histor','contract','evidence','source_sync','ingest','learning','shadow','candle')):
            continue
        cols = [r[1] for r in c.execute(f'PRAGMA table_info("{name}")')]
        stamp_cols = [x for x in cols if any(k in x.lower() for k in ('_at','date','day','time','updated','checked','observed','known'))]
        item = {'rows': table_count(c, name), 'timestamp_columns': stamp_cols[:8]}
        maxima = {}
        for col in stamp_cols[:4]:
            try:
                maxima[col] = scalar(c, f'SELECT MAX("{col}") FROM "{name}"')
            except sqlite3.Error:
                pass
        if maxima:
            item['maxima'] = maxima
        result[name] = item
    return result


def audit_observer():
    result = {'path': str(OBSERVER_DB), 'exists': OBSERVER_DB.exists()}
    if not OBSERVER_DB.exists():
        result['state'] = 'RED'; return result
    try:
        with connect(OBSERVER_DB) as c:
            qc = scalar(c, 'PRAGMA quick_check')
            ts = tables(c)
            result['quick_check'] = qc
            result['table_count'] = len(ts)
            if 'observer_state' in ts:
                row = c.execute('SELECT * FROM observer_state WHERE id=1').fetchone()
                result['observer_state'] = dict(row) if row else None
            if 'market_snapshots' in ts:
                result['live_market'] = {
                    'rows': table_count(c, 'market_snapshots'),
                    'symbols': int(scalar(c, 'SELECT COUNT(DISTINCT symbol) FROM market_snapshots') or 0),
                    'latest_observed_at': scalar(c, 'SELECT MAX(observed_at) FROM market_snapshots'),
                    'latest_book_at': scalar(c, 'SELECT MAX(book_at) FROM market_snapshots'),
                    'latest_trade_at': scalar(c, 'SELECT MAX(trade_at) FROM market_snapshots'),
                    'opening_holds': int(scalar(c, "SELECT COUNT(*) FROM market_snapshots WHERE opening_block_reason<>''") or 0),
                }
            if 'candle_versions' in ts:
                result['candles'] = {
                    'versions': table_count(c, 'candle_versions'),
                    'series': table_count(c, 'candle_series') if 'candle_series' in ts else 0,
                    'latest_bar_end': scalar(c, 'SELECT MAX(bar_end) FROM candle_versions'),
                    'latest_known_at': scalar(c, 'SELECT MAX(known_at) FROM candle_versions'),
                    'raw_archive_rows': table_count(c, 'historical_raw_archive') if 'historical_raw_archive' in ts else 0,
                }
            if 'candle_worker_state' in ts:
                row = c.execute('SELECT * FROM candle_worker_state WHERE id=1').fetchone()
                result['candle_worker'] = dict(row) if row else None
            if 'paper_decisions' in ts:
                result['paper_decisions'] = {
                    'rows': table_count(c, 'paper_decisions'),
                    'latest': scalar(c, 'SELECT MAX(decided_at) FROM paper_decisions'),
                    'actions': grouped(c, 'paper_decisions', 'action'),
                }
            if 'trade_gate_evaluations' in ts:
                result['trade_gates'] = {
                    'rows': table_count(c, 'trade_gate_evaluations'),
                    'latest': scalar(c, 'SELECT MAX(evaluated_at) FROM trade_gate_evaluations'),
                    'results': grouped(c, 'trade_gate_evaluations', 'final_result'),
                }
            if 'paper_learning_samples' in ts:
                result['paper_learning'] = {
                    'rows': table_count(c, 'paper_learning_samples'),
                    'feature_latest': scalar(c, 'SELECT MAX(feature_timestamp) FROM paper_learning_samples'),
                    'label_latest': scalar(c, 'SELECT MAX(label_timestamp) FROM paper_learning_samples'),
                    'outcomes': grouped(c, 'paper_learning_samples', 'outcome'),
                    'labeled': int(scalar(c, 'SELECT COUNT(*) FROM paper_learning_samples WHERE label_timestamp IS NOT NULL') or 0),
                }
            if 'a3_history_ingest_state_rc6' in ts:
                result['a3_state'] = {
                    'rows': table_count(c, 'a3_history_ingest_state_rc6'),
                    'statuses': grouped(c, 'a3_history_ingest_state_rc6', 'status'),
                    'latest_attempt': scalar(c, 'SELECT MAX(last_attempt_at) FROM a3_history_ingest_state_rc6'),
                    'latest_success': scalar(c, 'SELECT MAX(last_success_at) FROM a3_history_ingest_state_rc6'),
                    'latest_complete_day': scalar(c, 'SELECT MAX(last_complete_day) FROM a3_history_ingest_state_rc6'),
                    'failed': int(scalar(c, "SELECT COUNT(*) FROM a3_history_ingest_state_rc6 WHERE status='FAILED'") or 0),
                }
            if 'history_attempt_ledger_v2' in ts:
                result['history_attempts_observer'] = {
                    'rows': table_count(c, 'history_attempt_ledger_v2'),
                    'states': grouped(c, 'history_attempt_ledger_v2', 'state'),
                    'sources': grouped(c, 'history_attempt_ledger_v2', 'source'),
                    'latest_attempt': scalar(c, 'SELECT MAX(attempted_at) FROM history_attempt_ledger_v2'),
                    'latest_complete': scalar(c, 'SELECT MAX(completed_at) FROM history_attempt_ledger_v2'),
                }
            result['matching_tables'] = generic_matching_tables(c, ts)
            real_orders = int((result.get('observer_state') or {}).get('real_orders_sent') or 0)
            result['state'] = 'GREEN' if qc == 'ok' and real_orders == 0 else 'RED'
    except Exception as exc:
        result['state'] = 'RED'; result['error'] = f'{type(exc).__name__}:{exc}'
    return result


def audit_history():
    result = {'path': str(HISTORY_DB), 'exists': HISTORY_DB.exists()}
    if not HISTORY_DB.exists():
        result['state'] = 'RED'; return result
    try:
        with connect(HISTORY_DB) as c:
            qc = scalar(c, 'PRAGMA quick_check')
            ts = tables(c)
            result['quick_check'] = qc
            result['table_count'] = len(ts)
            if 'history_versions_v2' in ts:
                result['versions_v2'] = {
                    'rows': table_count(c, 'history_versions_v2'),
                    'identities': int(scalar(c, "SELECT COUNT(DISTINCT symbol||'|'||instrument_type||'|'||market||'|'||settlement) FROM history_versions_v2") or 0),
                    'first_day': scalar(c, 'SELECT MIN(date) FROM history_versions_v2'),
                    'latest_day': scalar(c, 'SELECT MAX(date) FROM history_versions_v2'),
                    'latest_observed': scalar(c, 'SELECT MAX(observed_at) FROM history_versions_v2'),
                    'sources': grouped(c, 'history_versions_v2', 'source'),
                }
            if 'history_canonical_v2' in ts:
                result['canonical_v2'] = {
                    'rows': table_count(c, 'history_canonical_v2'),
                    'identities': int(scalar(c, "SELECT COUNT(DISTINCT symbol||'|'||instrument_type||'|'||market||'|'||settlement) FROM history_canonical_v2") or 0),
                    'first_day': scalar(c, 'SELECT MIN(date) FROM history_canonical_v2'),
                    'latest_day': scalar(c, 'SELECT MAX(date) FROM history_canonical_v2'),
                    'latest_observed': scalar(c, 'SELECT MAX(observed_at) FROM history_canonical_v2'),
                    'sources': grouped(c, 'history_canonical_v2', 'source'),
                }
            if 'history_attempt_ledger_v2' in ts:
                result['attempts_v2'] = {
                    'rows': table_count(c, 'history_attempt_ledger_v2'),
                    'states': grouped(c, 'history_attempt_ledger_v2', 'state'),
                    'sources': grouped(c, 'history_attempt_ledger_v2', 'source'),
                    'latest_attempt': scalar(c, 'SELECT MAX(attempted_at) FROM history_attempt_ledger_v2'),
                    'latest_complete': scalar(c, 'SELECT MAX(completed_at) FROM history_attempt_ledger_v2'),
                }
            if 'market_historical_ohlcv' in ts:
                cols = {r[1] for r in c.execute('PRAGMA table_info(market_historical_ohlcv)')}
                item = {'rows': table_count(c, 'market_historical_ohlcv')}
                if 'symbol' in cols:
                    item['symbols'] = int(scalar(c, 'SELECT COUNT(DISTINCT symbol) FROM market_historical_ohlcv') or 0)
                if 'date' in cols:
                    item['first_day'] = scalar(c, 'SELECT MIN(date) FROM market_historical_ohlcv')
                    item['latest_day'] = scalar(c, 'SELECT MAX(date) FROM market_historical_ohlcv')
                result['legacy_history'] = item
            result['matching_tables'] = generic_matching_tables(c, ts)
            result['state'] = 'GREEN' if qc == 'ok' and (
                (result.get('canonical_v2') or {}).get('rows', 0) > 0 or
                (result.get('legacy_history') or {}).get('rows', 0) > 0
            ) else 'RED'
    except Exception as exc:
        result['state'] = 'RED'; result['error'] = f'{type(exc).__name__}:{exc}'
    return result


def shadow_learning():
    try:
        import et_shadow_learning_rc6
        return et_shadow_learning_rc6.collect(OBSERVER_DB)
    except Exception as exc:
        return {'state': 'RED', 'read_only': True, 'detail': f'{type(exc).__name__}:{exc}'}


def main():
    result = {
        'schema': 'POROTA_RC6_DATA_LEARNING_READONLY_AUDIT_V1',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'read_only': True,
        'network_calls_performed': False,
        'orders_performed': False,
        'observer': audit_observer(),
        'history': audit_history(),
        'shadow_learning': shadow_learning(),
    }
    hard_red = result['observer'].get('state') == 'RED' or result['history'].get('state') == 'RED'
    result['status'] = 'RED' if hard_red else 'GREEN'
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 2 if hard_red else 0


if __name__ == '__main__':
    raise SystemExit(main())

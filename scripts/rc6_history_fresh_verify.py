"""Read-only integrity/retention check of an isolated fresh capture.

This checks local evidence mechanics, not provider monetary or PIT authority.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import stat
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from cu_history_store_v2_hf6 import instant
from rc6_data_retention import daily_start
from scripts.rc6_history_fresh_sidecar import (ART, _body, _day, _explicit_currency,
                                              _identity_conflicts, _request_key,
                                              MAX_CLOCK_SKEW_SECONDS,
                                              MAX_ROWS, SOURCES)


def _sha256(path):
    digest = sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path, *, now=None):
    path = Path(path).absolute()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.resolve() != path:
        raise ValueError('FRESH_VERIFY_ALIAS_FORBIDDEN')
    system_now = datetime.now(timezone.utc)
    clock = instant(now or system_now)
    if abs((clock - system_now).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError('FRESH_VERIFY_CLOCK_UNVERIFIED')
    uri = path.as_uri() + '?mode=ro&immutable=1'
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('FRESH_VERIFY_SQLITE_INVALID')
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('FRESH_VERIFY_FK_INVALID')
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if names != {'fresh_generation_rc6', 'fresh_captures_rc6',
                     'fresh_row_revisions_rc6'}:
            raise ValueError('FRESH_VERIFY_SCHEMA_UNVERIFIED')
        generation = connection.execute('SELECT * FROM fresh_generation_rc6').fetchall()
        if len(generation) != 1 or generation[0]['policy'] != 'EVIDENCE_ONLY_365_ART_V1':
            raise ValueError('FRESH_VERIFY_GENERATION_INVALID')
        created = instant(generation[0]['created_at'])
        cutoff = daily_start(clock)
        if generation[0]['cutoff_day'] < cutoff.isoformat():
            raise ValueError('FRESH_VERIFY_RETENTION_OVERDUE')
        counts = {'captures': 0, 'retained_rows': 0, 'quarantined': 0,
                  'request_only_currency': 0, 'row_revisions': 0}
        seen = set()
        request_keys = set()
        for row in connection.execute('SELECT * FROM fresh_captures_rc6'):
            source = row['source']
            identity = tuple(row[field] for field in (
                'symbol', 'instrument_type', 'market', 'currency', 'settlement'))
            if source not in SOURCES or (source, identity) in seen:
                raise ValueError('FRESH_VERIFY_IDENTITY_INVALID')
            seen.add((source, identity))
            request_key = (source, _request_key(source, identity))
            if request_key in request_keys:
                raise ValueError('FRESH_VERIFY_REQUEST_AMBIGUOUS')
            request_keys.add(request_key)
            known = instant(row['first_known_at'])
            if known < created or known > clock:
                raise ValueError('FRESH_VERIFY_PIT_CLOCK_INVALID')
            if (row['requested_from'] < cutoff.isoformat() or
                    row['requested_to'] > known.astimezone(ART).date().isoformat()):
                raise ValueError('FRESH_VERIFY_REQUEST_OUTSIDE_RETENTION')
            if row['readiness_implication'] != 'NONE' or row['execution_allowed'] != 0:
                raise ValueError('FRESH_VERIFY_TRADING_AUTHORITY_FORBIDDEN')
            if row['status'] not in {'QUARANTINED', 'STAGED_REVIEW_REQUIRED'}:
                raise ValueError('FRESH_VERIFY_PROMOTION_FORBIDDEN')
            if row['provider_currency_status'] not in {'REQUEST_ONLY', 'EXPLICIT_ROWS'}:
                raise ValueError('FRESH_VERIFY_CURRENCY_STATUS_UNVERIFIED')
            if row['price_basis_status'] != 'UNVERIFIED':
                raise ValueError('FRESH_VERIFY_BASIS_INVENTED')
            digest = row['canonical_payload_sha256']
            if digest and (len(digest) != 64 or any(x not in '0123456789abcdef' for x in digest)):
                raise ValueError('FRESH_VERIFY_HASH_INVALID')
            retained = json.loads(row['retained_rows_json'])
            if not isinstance(retained, list) or len(retained) > MAX_ROWS or len(retained) != row['retained_rows']:
                raise ValueError('FRESH_VERIFY_ROW_COUNT_INVALID')
            if row['dropped_expired'] < 0 or row['dropped_invalid'] < 0:
                raise ValueError('FRESH_VERIFY_ROW_COUNT_INVALID')
            revisions = connection.execute('''SELECT * FROM fresh_row_revisions_rc6
              WHERE capture_id=? ORDER BY retained_ordinal''', (row['id'],)).fetchall()
            if len(revisions) != len(retained):
                raise ValueError('FRESH_VERIFY_REVISION_LINK_MISSING')
            explicit = 0
            days = {}
            conflict = False
            prior_index = -1
            for ordinal, evidence in enumerate(retained):
                if not isinstance(evidence, dict) or _identity_conflicts(evidence, identity):
                    raise ValueError('FRESH_VERIFY_ROW_IDENTITY_CONFLICT')
                day = _day(evidence, source, known)
                if not cutoff <= day <= known.astimezone(ART).date():
                    raise ValueError('FRESH_VERIFY_ROW_OUTSIDE_RETENTION')
                monetary = _explicit_currency(evidence)
                if monetary is not None and monetary != identity[3]:
                    raise ValueError('FRESH_VERIFY_ROW_CURRENCY_CONFLICT')
                explicit += monetary is not None
                signature = _body(evidence)
                if day in days and days[day] != signature:
                    conflict = True
                days[day] = signature
                revision = revisions[ordinal]
                if (revision['retained_ordinal'] != ordinal or
                        revision['provider_row_index'] <= prior_index or
                        revision['provider_row_index'] >= MAX_ROWS or
                        revision['economic_day'] != day.isoformat() or
                        revision['first_known_at'] != row['first_known_at'] or
                        revision['canonical_row_sha256'] != sha256(signature).hexdigest()):
                    raise ValueError('FRESH_VERIFY_REVISION_LINK_INVALID')
                prior_index = revision['provider_row_index']
            currency_status = ('EXPLICIT_ROWS' if retained and explicit == len(retained)
                               else 'REQUEST_ONLY')
            reason = ('FRESH_DUPLICATE_DAY_CONFLICT' if conflict else
                      'FRESH_REVIEW_REQUIRED' if retained else None)
            status = 'STAGED_REVIEW_REQUIRED' if retained and not conflict else 'QUARANTINED'
            if (row['provider_currency_status'] != currency_status or
                    row['status'] != status or
                    (reason is not None and row['reason'] != reason) or
                    (not retained and row['reason'] not in {
                        'FRESH_NO_VALID_IN_WINDOW_ROWS', 'FRESH_SOURCE_ERROR',
                        'FRESH_RESPONSE_BUDGET_EXCEEDED', 'FRESH_ROW_BUDGET_EXCEEDED',
                        'FRESH_PROVIDER_SHAPE_UNVERIFIED'})):
                raise ValueError('FRESH_VERIFY_STATUS_INCONSISTENT')
            counts['captures'] += 1
            counts['retained_rows'] += len(retained)
            counts['row_revisions'] += len(revisions)
            counts['quarantined'] += int(row['status'] == 'QUARANTINED')
            counts['request_only_currency'] += int(row['provider_currency_status'] == 'REQUEST_ONLY')
    if not counts['captures']:
        raise ValueError('FRESH_VERIFY_EMPTY_GENERATION')
    with sqlite3.connect(uri, uri=True) as connection:
        total_revisions = connection.execute(
            'SELECT COUNT(*) FROM fresh_row_revisions_rc6').fetchone()[0]
    if total_revisions != counts['row_revisions']:
        raise ValueError('FRESH_VERIFY_ORPHAN_REVISION')
    return {'state': 'EVIDENCE_ONLY_NO_GO', 'sidecar_sha256': _sha256(path),
            'cutoff_day': cutoff.isoformat(), **counts,
            'transport_raw_hash_verified': False, 'legacy_pins_verified': False,
            'history_v2_writes': 0, 'ready_paper_implication': 'NONE'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sidecar', type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(verify(args.sidecar), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

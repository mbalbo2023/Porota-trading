#!/usr/bin/env python3
"""A422: counterfactual replay of an ALREADY published RC6 JSON snapshot.

No database, provider, PPI Watch, order, or host writes.  Historical predicates
are counted; a hypothetical upper bound is NEVER instrument readiness.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


SOURCE = Path('/opt/porota-trading/data/market/rc6_instrument_evidence_latest.json')
SOURCE_SCHEMA = 'rc6-full-universe-instrument-evidence-v1'
RECEIPT_SCHEMA = 'rc6.a422.stale-family-counterfactual-readonly.v1'
FAMILIES = ('ACCIONES', 'CEDEARS', 'ETFS', 'BONOS', 'LETRAS', 'OBLIGACIONES',
            'OPCIONES', 'FUTUROS', 'CAUCIONES', 'FCI')
SOURCES = ('PPI', 'IOL', 'BYMA', 'A3', 'A3_CEM', 'CAFCI', 'CNV', 'ROFEX')
MAX_BYTES = 48 * 1024 * 1024
MAX_ROWS = 60_000
SAFE_REASON = re.compile(r'^[A-Z][A-Z0-9_]{1,55}(?::[A-Z][A-Z0-9_]{0,55})?$')
MARKET_ALIASES = {'BCBA': 'BYMA', 'BYMA': 'BYMA', 'ROFEX': 'A3', 'A3': 'A3'}
SETTLEMENT_ALIASES = {
    'T1': 'A-24HS', 'T+1': 'A-24HS', '24HS': 'A-24HS',
    'A-24HS': 'A-24HS', 'T0': 'INMEDIATA', 'T+0': 'INMEDIATA',
    'CI': 'INMEDIATA', 'INMEDIATA': 'INMEDIATA',
}


class EvidenceRejected(Exception):
    """Never include filesystem paths or private values in this exception."""


def _open_bounded(path: Path, *, cap: int = MAX_BYTES) -> tuple[bytes, os.stat_result]:
    if not path.is_absolute() or not path.name or '..' in path.parts:
        raise EvidenceRejected('SNAPSHOT_PATH_UNSAFE')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent = fd = None
    try:
        parent = os.open('/', flags | os.O_DIRECTORY)
        for component in path.parts[1:-1]:
            nxt = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = nxt
        fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise EvidenceRejected('SNAPSHOT_FILE_UNSAFE')
        if not 0 < before.st_size <= cap:
            raise EvidenceRejected('SNAPSHOT_SIZE_INVALID')
        chunks = []
        remaining = cap + 1
        while remaining:
            part = os.read(fd, min(256 * 1024, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or len(raw) > cap or
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise EvidenceRejected('SNAPSHOT_CHANGED')
        return raw, before
    except FileNotFoundError as exc:
        raise EvidenceRejected('SNAPSHOT_NOT_PUBLISHED') from exc
    except (PermissionError, NotADirectoryError, OSError) as exc:
        raise EvidenceRejected('SNAPSHOT_OPEN_UNAVAILABLE') from exc
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def _utc(value: object) -> datetime:
    try:
        if type(value) is not str:
            raise ValueError('timestamp missing')
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError('naive timestamp')
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise EvidenceRejected('SNAPSHOT_GENERATED_CLOCK_INVALID') from exc


def _key_valid(row: dict) -> bool:
    key = row.get('canonical_identity')
    if (not isinstance(key, list) or len(key) != 5 or
            any(type(item) is not str or not item.strip() for item in key)):
        return False
    fields = ('symbol', 'family', 'market', 'currency', 'settlement')
    aliases = ({}, {}, MARKET_ALIASES, {}, SETTLEMENT_ALIASES)
    for i, field in enumerate(fields):
        raw = row.get(field)
        if raw is None or not str(raw).strip():
            return False
        canon = aliases[i].get(str(raw).strip().upper(), str(raw).strip().upper())
        if key[i] != canon:
            return False
    return True


def _reason(row: dict) -> str:
    value = row.get('reason')
    return value if type(value) is str and SAFE_REASON.fullmatch(value) else 'UNCLASSIFIED_REASON'


def _validate_root(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or payload.get('schema') != SOURCE_SCHEMA:
        raise EvidenceRejected('SNAPSHOT_SCHEMA_INVALID')
    instruments = payload.get('instruments')
    universe = payload.get('universe')
    counters = payload.get('counts')
    family_reports = payload.get('families')
    if (not isinstance(instruments, list) or not isinstance(universe, dict) or
            not isinstance(counters, dict) or not isinstance(family_reports, list)):
        raise EvidenceRejected('SNAPSHOT_SHAPE_INVALID')
    total = len(instruments)
    if (not 0 < total <= MAX_ROWS or type(universe.get('catalog_count')) is not int or
            universe.get('catalog_count') != total or
            universe.get('ledger_count') != total or
            universe.get('ledger_complete') is not True):
        raise EvidenceRejected('SNAPSHOT_DENOMINATOR_INVALID')
    if (payload.get('decision_effect') != 'OBSERVE_ONLY' or
            payload.get('real_money_authorized') is not False or
            counters.get('paper_auto_enabled') != 0 or
            not isinstance(payload.get('source_order'), str) or
            not payload['source_order'].startswith('PPI_PRIMARY')):
        raise EvidenceRejected('SNAPSHOT_SAFETY_INVARIANT_INVALID')
    verified = 0
    by_family = Counter()
    ready_by_family = Counter()
    for row in instruments:
        if (not isinstance(row, dict) or row.get('paper_auto_enabled') is not False or
                row.get('entry_authority') is not False or
                row.get('real_money_authorized') is not False or
                type(row.get('paper_contract_ready')) is not bool):
            raise EvidenceRejected('SNAPSHOT_INSTRUMENT_GUARD_INVALID')
        verified += bool(row['paper_contract_ready'])
        by_family[row.get('family')] += 1
        ready_by_family[row.get('family')] += bool(row['paper_contract_ready'])
    if type(counters.get('paper_contract_ready')) is not int or counters['paper_contract_ready'] != verified:
        raise EvidenceRejected('SNAPSHOT_READY_AGGREGATE_DRIFT')
    seen_report = set()
    for item in family_reports:
        if not isinstance(item, dict) or type(item.get('family')) is not str:
            raise EvidenceRejected('SNAPSHOT_FAMILY_REPORT_INVALID')
        label = item['family']
        if label in seen_report:
            raise EvidenceRejected('SNAPSHOT_FAMILY_REPORT_DUPLICATE')
        seen_report.add(label)
        if (item.get('instrument_count') != by_family[label] or
                item.get('paper_contract_ready') != ready_by_family[label] or
                item.get('paper_auto_enabled') != 0):
            raise EvidenceRejected('SNAPSHOT_FAMILY_REPORT_DRIFT')
    if any(by_family[label] > 0 and label not in seen_report for label in FAMILIES):
        raise EvidenceRejected('SNAPSHOT_FAMILY_REPORT_MISSING')
    return instruments


def replay(payload: object, *, now: datetime, source_sha: str,
           source_bytes: int, file_mtime: datetime) -> dict:
    """Partition real rejections; evaluate ONLY necessary-condition bounds.

    A reason `NO_FRESH_STRUCTURED_SOURCE:READY_PAPER_*` is producer metadata,
    not a genuine PAPER-ready instrument. It offers an upper bound under an
    explicitly imaginary valid source, and MUST NOT be promoted to READY.
    """
    if now.tzinfo is None:
        raise EvidenceRejected('CLOCK_NAIVE')
    now = now.astimezone(timezone.utc)
    generated = _utc(payload.get('generated_at') if isinstance(payload, dict) else None)
    age = round((now - generated).total_seconds(), 1)
    if (age < -30 or abs((file_mtime - generated).total_seconds()) > 120):
        raise EvidenceRejected('SNAPSHOT_CLOCK_INCONSISTENT')
    instruments = _validate_root(payload)
    buckets = {label: {'n': 0, 'observed_ready': 0, 'identity_valid': 0,
                       'source_only_if_replaced': 0, 'other_or_unresolved_reason': 0,
                       'source_only_with_existing_exact_structured': 0,
                       'source_only_no_exact_structured': 0,
                       'source_selection_eligible': 0, 'reason_counts': Counter()}
               for label in FAMILIES}
    unknown = 0
    seen_keys: set[tuple[str, ...]] = set()
    for row in instruments:
        family = row.get('family')
        if type(family) is not str or family not in buckets:
            unknown += 1
            continue
        part = buckets[family]
        part['n'] += 1
        part['observed_ready'] += row['paper_contract_ready'] is True
        valid = _key_valid(row)
        key = tuple(row['canonical_identity']) if valid else None
        if valid and key in seen_keys:
            valid = False  # duplicate exact key is never two separate candidates
        if key is not None:
            seen_keys.add(key)
        part['identity_valid'] += valid
        code = _reason(row)
        part['reason_counts'][code] += 1
        views = row.get('sources') if isinstance(row.get('sources'), dict) else {}
        eligible = any(isinstance(views.get(src), dict) and
                       views[src].get('selection_eligible') is True for src in SOURCES)
        part['source_selection_eligible'] += eligible
        structured = any(isinstance(views.get(src), dict) and
                         views[src].get('present') is True and
                         views[src].get('structured') is True and
                         views[src].get('identity_binding') == 'EXACT_FIVE_PART_IDENTITY'
                         for src in SOURCES)
        # The producer labels sources as not fresh; simulation only removes
        # that *one* predicate, without adding quotes, native clocks or fills.
        hypothetical = (valid and code.startswith('NO_FRESH_STRUCTURED_SOURCE:READY_PAPER_'))
        part['source_only_if_replaced'] += hypothetical
        part['other_or_unresolved_reason'] += not hypothetical
        part['source_only_with_existing_exact_structured'] += hypothetical and structured
        part['source_only_no_exact_structured'] += hypothetical and not structured
    result_families = []
    for label in FAMILIES:
        bucket = buckets[label]
        top = bucket.pop('reason_counts')
        bucket['family'] = label
        bucket['reason_counts'] = dict(sorted(top.items(), key=lambda x: (-x[1], x[0]))[:20])
        bucket['unlisted_reasons_count'] = sum(top.values()) - sum(bucket['reason_counts'].values())
        bucket['status'] = 'MISSING_FAMILY_SAMPLE' if bucket['n'] == 0 else 'COUNTERFACTUAL_ONLY'
        result_families.append(bucket)
    return {
        'schema': RECEIPT_SCHEMA, 'read_only': True,
        'status': 'REPLAY_CONTRAFACTUAL_ONLY',
        'market_readiness': 'NO_VERIFICADO', 'causal_pit_validated': False,
        'fees_risk_fills_validated': False, 'runtime_sha_verified': False,
        'new_orders': 0, 'orders_routed': False, 'database_opened': False,
        'provider_calls': 0, 'host_writes': 0,
        'scenario': 'CONDITIONAL_IF_VALID_SOURCE_REPLACED_NOT_A_TRADING_SIGNAL',
        'actual_source_freshness_cannot_be_inferred': True,
        'source_sha256': source_sha, 'source_bytes': source_bytes,
        'source_generated_at_utc': generated.isoformat(),
        'source_age_seconds_at_replay': age,
        'family_denominator': len(FAMILIES),
        'universe_denominator': len(instruments),
        'unmapped_family_count': unknown,
        'source_only_hypothetical_upper_bound': sum(x['source_only_if_replaced'] for x in result_families),
        'not_explained_by_source_only_reason': sum(x['other_or_unresolved_reason'] for x in result_families),
        'families': result_families,
    }


def inspect(path: Path = SOURCE, *, now: datetime | None = None, cap: int = MAX_BYTES) -> dict:
    now = now or datetime.now(timezone.utc)
    report = {
        'schema': RECEIPT_SCHEMA, 'read_only': True,
        'status': 'NO_VERIFICADO', 'reason': 'SNAPSHOT_NOT_INSPECTED',
        'database_opened': False, 'provider_calls': 0,
        'orders_routed': False, 'host_writes': 0,
    }
    try:
        raw, info = _open_bounded(path, cap=cap)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError) as exc:
            raise EvidenceRejected('SNAPSHOT_JSON_INVALID') from exc
        summary = replay(payload, now=now, source_sha=hashlib.sha256(raw).hexdigest(),
                         source_bytes=len(raw),
                         file_mtime=datetime.fromtimestamp(info.st_mtime, timezone.utc))
        report.update(summary)
    except EvidenceRejected as exc:
        report['reason'] = str(exc)
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['host-probe']:
        print('Usage: rc6_a422_stale_family_counterfactual_ro.py host-probe', file=sys.stderr)
        return 64
    result = inspect()
    print(json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True))
    return 0 if result['status'] == 'REPLAY_CONTRAFACTUAL_ONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())

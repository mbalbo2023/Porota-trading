#!/usr/bin/env python3
"""Exact failed COLD2 archive after an explicitly approved canonical drain.

This entry point is not a scheduled task. The promote workflow gates the exact
candidate against a later owner authorization before reaching the host.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import rc6_cold2_quarantine as cold
from scripts import rc6_cold2_successor as successor
from scripts import rc6_deploy_quiescence as drain

ROOT = Path('/opt/porota-trading')
OWNED = drain.OWNED


def bounded_json(path, limit=65536):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('COLD2_RECEIPT_UNSAFE')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('COLD2_RECEIPT_BOUND')
    value = json.loads(raw)
    if type(value) is not dict:
        raise ValueError('COLD2_RECEIPT_SHAPE')
    return value


def ppi_units():
    """Observe unit state only; never call start/stop/restart or touch data."""
    out = subprocess.check_output(['systemctl', 'list-unit-files', '--no-legend'],
                                  text=True, timeout=10)
    if len(out) > 256 * 1024:
        raise ValueError('COLD2_PPI_UNIT_LIST_BOUND')
    names = sorted({row.split()[0] for row in out.splitlines() if row.split()
                    and 'ppi' in row.split()[0].lower() and
                    'watch' in row.split()[0].lower()})
    if len(names) > 16:
        raise ValueError('COLD2_PPI_UNIT_BOUND')
    return {name: tuple(subprocess.run(['systemctl', 'is-'+mode, name],
                    text=True, capture_output=True, timeout=5).stdout.strip()
                    for mode in ('active', 'enabled')) for name in names}


def receipt_for(root, candidate):
    path = root/'data/deploy'/('quiescence-'+candidate+'.json')
    value = bounded_json(path)
    rows = value.get('containers')
    if (value.get('candidate_sha') != candidate or
            value.get('status') != 'HOST_INSTALLATION_STARTED' or
            value.get('installation_started') is not True or
            value.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            type(value.get('real_orders_sent')) is not int or
            value['real_orders_sent'] != 0 or
            value.get('ppi_watch_accessed') is not False or
            type(rows) is not dict or set(rows) != set(OWNED) or
            set(value.get('stop_evidence', {})) != set(OWNED)):
        raise ValueError('COLD2_QUIESCENCE_RECEIPT_INVALID')
    ids = {}
    for name in OWNED:
        row, proof = rows[name], value['stop_evidence'][name]
        if (type(row) is not dict or type(row.get('id')) is not str or
                re.fullmatch('[a-f0-9]{64}', row['id']) is None or
                not isinstance(row.get('image'), str) or
                re.fullmatch('sha256:[a-f0-9]{64}', row['image']) is None or
                type(proof) is not dict or
                proof.get('classification') not in
                    ('CLEAN_EXIT', 'INHERITED_OOM_FLAG_CLEAN_EXIT') or
                (proof.get('after') or {}).get('Status') != 'exited' or
                (proof.get('after') or {}).get('ExitCode') != 0):
            raise ValueError('COLD2_STOP_EVIDENCE_INVALID')
        ids[name] = row['id']
    return value, ids



def verify_complete_queue(root):
    """Attest a later COMPLETE generation without moving or revoking it."""
    shadow, source, _ = cold.paths(root)
    names = {entry.name for entry in source.iterdir()}
    ids = {name[9:-5] for name in names if name.startswith('manifest-')}
    paired = {'queue.json'} | {name for ident in ids
        for name in ('manifest-'+ident+'.json', 'receipt-'+ident+'.json')}
    if (len(names) > 128 or not ids or names != paired or
            any(re.fullmatch('[a-f0-9]{32}', ident) is None for ident in ids)):
        raise ValueError('COLD2_COMPLETE_NAMESPACE_UNSAFE')
    queue = cold.envelope(source/'queue.json', cold=True)
    ident = queue.get('snapshot_id')
    if (queue.get('state') != 'COMPLETE' or ident not in ids or
            type(queue.get('manifest_sha256')) is not str):
        raise ValueError('COLD2_COMPLETE_QUEUE_UNSAFE')
    selected = None
    for other in ids:
        manifest = cold.envelope(source/('manifest-'+other+'.json'), cold=True)
        receipt = cold.envelope(source/('receipt-'+other+'.json'), cold=True)
        digest = cold.digest(manifest)
        if (manifest.get('snapshot_id') != other or
                manifest.get('pack') != other+'.pack' or
                type(manifest.get('pack_bytes')) is not int or
                not 0 < manifest['pack_bytes'] <= 512*1024**2 or
                re.fullmatch('[a-f0-9]{64}', str(manifest.get('pack_sha256'))) is None or
                receipt.get('snapshot_id') != other or
                receipt.get('state') != 'COMPLETE' or
                receipt.get('manifest_sha256') != digest):
            raise ValueError('COLD2_COMPLETE_PAIR_UNSAFE')
        if other == ident:
            selected = manifest
            if queue['manifest_sha256'] != digest:
                raise ValueError('COLD2_COMPLETE_QUEUE_BINDING')
    pointer = cold.envelope(shadow/'CURRENT.json')
    cut_name = pointer.get('file')
    if (not isinstance(cut_name, str) or
            re.fullmatch(r'cut-[a-f0-9]{64}\.json\.gz', cut_name) is None or
            pointer.get('generation_id') != cut_name[4:-8]):
        raise ValueError('COLD2_COMPLETE_CURRENT_UNSAFE')
    cut = cold.envelope(shadow/cut_name, zipped=True)
    reference = cut.get('report', {}).get('computed_report', {})
    if (reference.get('schema') != 'rc6.computed-audit-reference.v2' or
            reference.get('state') != 'PENDING' or
            reference.get('snapshot_id') != ident or
            reference.get('manifest_sha256') != queue['manifest_sha256'] or
            reference.get('deadline_at') != selected.get('deadline_at') or
            reference.get('source_as_of') != selected.get('source_as_of') or
            reference.get('configuration_fingerprint') !=
                selected.get('configuration_fingerprint') or
            type(cut.get('status', {}).get('real_orders_sent')) is not int or
            cut['status']['real_orders_sent'] != 0 or
            cut['status'].get('real_routes') != 'NOT_CALLED'):
        raise ValueError('COLD2_COMPLETE_CUT_UNSAFE')
    return {'state':'COMPLETE_UNTOUCHED','snapshot_id':ident,
            'manifest_sha256':queue['manifest_sha256'],
            'old_cut_revoked':True,'complete_pairs':len(ids)}


def apply(root, candidate):
    if re.fullmatch('[a-f0-9]{40}', candidate) is None:
        raise ValueError('COLD2_CANDIDATE_INVALID')
    folder = root/'data/deploy'
    if folder.is_symlink():
        raise ValueError('COLD2_DEPLOY_ALIAS')
    lock = folder/'.deploy-quiescence.lock'
    if lock.is_symlink() or not lock.is_file():
        raise ValueError('COLD2_QUIESCENCE_LOCK_MISSING')
    with lock.open('rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        baseline = ppi_units()
        receipt, ids = receipt_for(root, candidate)

        def admission():
            latest, current_ids = receipt_for(root, candidate)
            if latest != receipt or current_ids != ids:
                raise ValueError('COLD2_QUIESCENCE_RECEIPT_CHANGED')
            # Archive while the exact owned runtime remains stopped. The
            # intraday path must preserve the full PAPER book byte-for-byte;
            # a stale RUNNING SQLite marker is accepted only with the
            # receipt's clean Docker stop proof.
            intraday = (receipt.get('market_open_suspended') is True and
                        receipt.get('open_paper_carryover') is True)
            if intraday:
                drain.exact_clean_stopped(receipt)
                state = drain.admit(stopped=True, allow_market_open_suspend=True,
                                    allow_open_paper_carryover=True,
                                    stopped_receipt=receipt)
                carry = drain.spot_carryover_snapshot()
                if (carry != receipt.get('spot_carryover') or
                        type(carry.get('open_count')) is not int or
                        not 0 < carry['open_count'] <= 16):
                    raise ValueError('COLD2_INTRADAY_CARRYOVER_CHANGED')
                if state not in {'STOPPED', 'RUNNING'}:
                    raise ValueError('COLD2_STOPPED_PROCESS_REQUIRED')
            else:
                if (receipt.get('market_open_suspended') is True or
                        receipt.get('open_paper_carryover') is True):
                    raise ValueError('COLD2_SESSION_MODE_CHANGED')
                if drain.admit(stopped=True) not in {'STOPPED', 'WAITING_MARKET'}:
                    raise ValueError('COLD2_STOPPED_PROCESS_REQUIRED')
                carry = None
            for name in OWNED:
                row = drain.inspect(name)
                if (row['Id'] != ids[name] or
                        row['Image'] != receipt['containers'][name]['image'] or
                        row['State']['Status'] != 'exited' or
                        row['State']['ExitCode'] != 0):
                    raise ValueError('COLD2_OWNED_ID_CHANGED')
            critical = drain.inspect('porota_critical_approval_rc6')
            if (critical['State']['Status'] != 'running' or
                    (critical['State'].get('Health') or {}).get('Status') != 'healthy' or
                    critical['State']['OOMKilled'] is not False):
                raise ValueError('COLD2_CRITICAL_UNHEALTHY')
            status = cold.envelope(root/'data/paper_v17/artifacts/observer_v17.db'
                                   /'dynamic-shadow/status.json')
            if (status.get('status') != 'FAIL_CLOSED' or
                    status.get('real_routes') != 'NOT_CALLED'):
                raise ValueError('COLD2_SHADOW_SAFETY_CHANGED')
            if ppi_units() != baseline:
                raise ValueError('COLD2_PPI_UNIT_CHANGED')
            return {'status': 'DRAINED', 'owned_stopped': True,
                    'market_closed': not intraday, 'no_positions': not intraday,
                    'market_open_suspended': intraday,
                    'open_paper_carryover': intraday,
                    'spot_carryover': carry,
                    'lock_held': True, 'mode': 'PRODUCTION_PAPER',
                    'real_orders_sent': 0, 'real_routes': 'NOT_CALLED',
                    'order_routing_blocked': True, 'inflight_absent': True,
                    'critical_healthy': True, 'owned_ids': current_ids,
                    'ppi_watch_accessed': False}

        original_archive = root/'data/deploy'/('rc6-failed-cold-'+cold.IDENT)
        if (original_archive.is_dir() and not original_archive.is_symlink() and
                (original_archive/'quarantine.json').exists() and
                not (root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
                     /'cold-audit'/cold.PACK).exists()):
            original = cold.verify_original_archive(root)
        else:
            original = cold.quarantine(root, admission=admission, expected_owned_ids=ids)
        source = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/cold-audit'
        queue_path = source/'queue.json'
        if queue_path.exists() or queue_path.is_symlink():
            queue = cold.envelope(queue_path, cold=True)
            if queue.get('state') == 'COMPLETE':
                later = verify_complete_queue(root)
            elif queue.get('state') == 'FAILED' and queue.get('snapshot_id') != cold.IDENT:
                later = successor.quarantine(root, admission=admission, expected_owned_ids=ids)
            else:
                raise ValueError('COLD2_SUCCESSOR_QUEUE_UNEXPECTED')
        else:
            later = {'state':'NO_ACTIVE_FAILED_SUCCESSOR','old_cut_revoked':True}
        admission()
        if ppi_units() != baseline:
            raise ValueError('COLD2_PPI_UNIT_CHANGED')
        return {'original':original,'successor':later,'old_cut_revoked':True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    args = parser.parse_args()
    result = apply(ROOT, args.candidate_sha)
    print(json.dumps({'status': 'GREEN', 'cold2': result,
                      'real_orders_sent': 0, 'ppi_watch_changed': False},
                     sort_keys=True))


if __name__ == '__main__':
    main()

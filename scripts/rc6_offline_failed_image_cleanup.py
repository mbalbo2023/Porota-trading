#!/usr/bin/env python3
"""Reclaim the exact unreferenced image after run 39370186360 stopped RC6.

This is a pretransfer admission operation. It never starts/stops a process,
changes the ledger, touches PPI Watch, or deletes a referenced image.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess

ROOT = Path('/opt/porota-trading')
FAILED = '48a981cf327a637c0c074879fcdcd17475de8111'
IMAGE = 'sha256:d47a427f8d205f899805967ed825cf2cdd9036a79f72ae24b17116e25cc8e905'
STABLE = 'porota-trading-bot:17.0.0-rc6'
TAGS = (STABLE, 'porota-trading-bot:17.0.0-rc6-candidate-' + FAILED,
        'porota-predeploy-v2:' + FAILED)
OWNED = ('porota_production_observer', 'porota_production_dashboard')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError('OFFLINE_COMMAND_FAILED:' + args[0] + ':' + args[1])
    if len(result.stdout) > 1024 * 1024:
        raise ValueError('OFFLINE_INVENTORY_BOUND')
    return result.stdout.strip()


def docker(*args):
    return command('docker', *args)


def inspect(name):
    rows = json.loads(docker('inspect', name))
    if type(rows) is not list or len(rows) != 1:
        raise ValueError('OFFLINE_DOCKER_INSPECT_SHAPE')
    return rows[0]


def references():
    ids = docker('ps', '-aq', '--no-trunc').splitlines()
    if len(ids) > 128 or any(re.fullmatch('[0-9a-f]{12,64}', cid) is None for cid in ids):
        raise ValueError('OFFLINE_CONTAINER_INVENTORY')
    return {inspect(cid)['Image'] for cid in ids}


def admission():
    receipt_path = ROOT/'data/deploy'/('quiescence-' + FAILED + '.json')
    if receipt_path.is_symlink() or not receipt_path.is_file() or receipt_path.stat().st_size > 65536:
        raise ValueError('OFFLINE_PRIOR_RECEIPT_UNSAFE')
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('candidate_sha') != FAILED or
            receipt.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
            receipt.get('installation_started') is not True or
            receipt.get('inflight') != 'ABSENT_OWNED_RUNTIME_STOPPED' or
            receipt.get('admin_cancel_status') != 'APPLIED_NO_MARKET_SALE' or
            type(receipt.get('admin_cancel_paper_id')) is not str or
            type(receipt.get('real_orders_sent')) is not int or
            receipt['real_orders_sent'] != 0 or
            receipt.get('ppi_watch_accessed') is not False or
            set(receipt.get('containers', {})) != set(OWNED) or
            set(receipt.get('stop_evidence', {})) != set(OWNED)):
        raise ValueError('OFFLINE_PRIOR_RECEIPT_INVALID')
    for name in OWNED:
        value = inspect(name)
        proof = receipt['stop_evidence'][name]
        if (value['Id'] != receipt['containers'][name]['id'] or
                value['Image'] != receipt['containers'][name]['image'] or
                value['State']['Status'] != 'exited' or
                value['State']['ExitCode'] != 0 or
                proof.get('classification') not in
                {'CLEAN_EXIT', 'INHERITED_OOM_FLAG_CLEAN_EXIT'}):
            raise ValueError('OFFLINE_OWNED_IDENTITY_CHANGED')
    critical = inspect('porota_critical_approval_rc6')
    state = critical['State']
    if (state['Status'] != 'running' or
            state.get('Health', {}).get('Status') != 'healthy' or
            state['OOMKilled'] is not False or
            type(critical.get('RestartCount')) is not int or
            critical['RestartCount'] != 0 or critical['Image'] == IMAGE):
        raise ValueError('OFFLINE_CRITICAL_CHANGED')
    capability = docker('exec', 'porota_critical_approval_rc6', 'python', '-c',
        "from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; "
        "h=UnixGithubIssuesClient().health(); "
        "assert h.get('status')=='ok' and h.get('capability')=='issues_only'; "
        "print('RC6_CRITICAL_ISSUES_ONLY=GREEN')")
    if capability != 'RC6_CRITICAL_ISSUES_ONLY=GREEN':
        raise ValueError('OFFLINE_CRITICAL_CAPABILITY')
    db = ROOT/'data/paper_v17/observer_v17.db'
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=.2) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        mode, orders, process, session = c.execute(
            'SELECT mode,real_orders_sent,process_state,session_state FROM observer_state WHERE id=1').fetchone()
        if (mode != 'PRODUCTION_PAPER' or type(orders) is not int or orders != 0 or
                process not in {'STOPPED', 'WAITING_MARKET'} or
                session not in {'CLOSED', 'MARKET_CLOSED'}):
            raise ValueError('OFFLINE_PAPER_STATE_CHANGED')
        for table, status in (('paper_positions', 'OPEN'), ('paper_cauciones', 'OPEN'),
                              ('paper_future_positions', 'ACTIVE')):
            if c.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1', (status,)).fetchone():
                raise ValueError('OFFLINE_POSITION_OPEN')
        row = c.execute('''SELECT p.status,p.close_reason,p.exit_price,p.net_pnl,
          a.authority FROM paper_positions p JOIN paper_admin_cancellations a
          USING(paper_id) WHERE p.paper_id=?''',
          (receipt['admin_cancel_paper_id'],)).fetchone()
        if (row != ('CANCELLED_ADMIN', 'ADMIN_CANCEL_NO_MARKET_EXECUTION', None,
                    None, 'ISSUE624_OPERATOR_REQUEST_20261019') or
                c.execute("SELECT 1 FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",
                          (receipt['admin_cancel_paper_id'],)).fetchone()):
            raise ValueError('OFFLINE_ADMIN_LEDGER_CHANGED')
    return receipt


def ppi_units():
    lines = command('systemctl', 'list-unit-files', '--no-legend').splitlines()
    names = sorted({line.split()[0] for line in lines if line.split() and
                    'ppi' in line.split()[0].lower() and 'watch' in line.split()[0].lower()})
    if len(names) > 16:
        raise ValueError('OFFLINE_PPI_INVENTORY_BOUND')
    return {name: tuple(subprocess.run(['systemctl', 'is-'+mode, name],
                         capture_output=True, text=True, timeout=5).stdout.strip()
                        for mode in ('active', 'enabled')) for name in names}


def eligible_tags():
    value = json.loads(docker('image', 'inspect', IMAGE))[0]
    tags = value.get('RepoTags') or []
    digests = value.get('RepoDigests') or []
    if (value.get('Id') != IMAGE or type(tags) is not list or
            not tags or any(tag not in TAGS for tag in tags) or
            any(re.fullmatch(r'(porota-predeploy-v2|porota-trading-bot)@sha256:[0-9a-f]{64}',
                             digest) is None for digest in digests) or
            IMAGE in references()):
        raise ValueError('OFFLINE_IMAGE_REFERENCED_OR_ALIASED')
    return [tag for tag in TAGS if tag in tags]


def cleanup():
    lock = ROOT/'data/deploy/.deploy-quiescence.lock'
    if lock.is_symlink() or not lock.is_file():
        raise ValueError('OFFLINE_LOCK_UNSAFE')
    with lock.open('rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before_ppi = ppi_units()
        receipt = admission()
        tags = eligible_tags()
        before = shutil.disk_usage('/').free
        removed = []
        for tag in tags:
            if admission() != receipt or ppi_units() != before_ppi or IMAGE in references():
                raise ValueError('OFFLINE_ADMISSION_CHANGED')
            if tag not in eligible_tags():
                raise ValueError('OFFLINE_IMAGE_TAG_CHANGED')
            docker('image', 'rm', tag)  # Never force; Docker also enforces references.
            removed.append(tag)
        if admission() != receipt or ppi_units() != before_ppi:
            raise ValueError('OFFLINE_POSTSTATE_CHANGED')
        return {'status': 'GREEN', 'failed_candidate': FAILED,
                'removed_tags': removed, 'disk_before': before,
                'disk_after': shutil.disk_usage('/').free,
                'real_orders_sent': 0, 'ppi_watch_changed': False}


if __name__ == '__main__':
    print(json.dumps(cleanup(), sort_keys=True))

#!/usr/bin/env python3
"""Stop only the exact RC6 deploy contract writer after CLI timeout/cancel.

Docker does not necessarily stop an auto-remove container when the attached
client is killed. This guard runs before observer restart and again during
failure cleanup; it never touches the observer, dashboard or PPI Watch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import subprocess

ROOT = Path('/opt/porota-trading')
DB = ROOT/'data/paper_v17/observer_v17.db'
CURRENT = ROOT/'data/deploy/CURRENT_STATE_V2.json'
OWNED = ('porota_production_observer', 'porota_production_dashboard')


def docker(*args, allow_missing=False):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=18)
    if result.returncode:
        if allow_missing and 'No such object' in result.stderr:
            return None
        raise RuntimeError('CONTRACT_DOCKER_'+args[0].upper()+'_FAILED')
    if len(result.stdout) > 1024 * 1024:
        raise RuntimeError('CONTRACT_DOCKER_OUTPUT_BOUND')
    return result.stdout.strip()


def inspect(name, *, allow_missing=False):
    try:
        raw = docker('inspect', name, allow_missing=allow_missing)
    except RuntimeError:
        if not allow_missing or not re.fullmatch(r'porota-deploy-contract-[0-9a-f]{40}', name):
            raise
        # An --rm writer can disappear between the attached CLI exit and our
        # inspect. Docker's not-found wording is not a stable API; require a
        # second successful daemon inventory proving the exact name absent.
        listing = docker('ps', '-a', '--no-trunc', '--format', '{{.Names}}|{{.ID}}')
        rows = listing.splitlines()
        if (len(rows) > 128 or any(len(row.split('|')) != 2 or
                                   not re.fullmatch(r'[0-9a-f]{12,64}', row.split('|')[1])
                                   for row in rows) or
                name in {row.split('|')[0] for row in rows}):
            raise RuntimeError('CONTRACT_WRITER_INVENTORY_UNVERIFIED')
        return None
    if raw is None:
        return None
    rows = json.loads(raw)
    if type(rows) is not list or len(rows) != 1:
        raise RuntimeError('CONTRACT_INSPECT_SHAPE')
    return rows[0]


def safety(candidate):
    if re.fullmatch(r'[0-9a-f]{40}', candidate) is None:
        raise RuntimeError('CONTRACT_CANDIDATE_INVALID')
    if CURRENT.is_symlink() or not CURRENT.is_file() or CURRENT.stat().st_size > 65536:
        raise RuntimeError('CONTRACT_STATE_UNSAFE')
    state = json.loads(CURRENT.read_text())
    image = state.get('image_id')
    if (state.get('candidate_sha') != candidate or
            state.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            state.get('mode') != 'PRODUCTION_PAPER' or
            type(state.get('real_orders_sent')) is not int or
            state['real_orders_sent'] != 0 or
            re.fullmatch(r'sha256:[0-9a-f]{64}', str(image)) is None):
        raise RuntimeError('CONTRACT_STATE_MISMATCH')
    with sqlite3.connect(DB.as_uri()+'?mode=ro', uri=True, timeout=.2) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA busy_timeout=200')
        if c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone() != ('PRODUCTION_PAPER', 0):
            raise RuntimeError('CONTRACT_PAPER_UNVERIFIED')
    owned = {name: inspect(name) for name in OWNED}
    if (any(item['Image'] != image for item in owned.values()) or
            owned['porota_production_dashboard']['State']['Running'] is not True):
        raise RuntimeError('CONTRACT_OWNED_IMAGE_MISMATCH')
    return image, owned


def stop(candidate):
    image, owned = safety(candidate)
    name = 'porota-deploy-contract-' + candidate
    target = inspect(name, allow_missing=True)
    if target is None:
        return {'status':'GREEN','writer':'ABSENT','candidate_sha':candidate,
                'real_orders_sent':0,'owned_unchanged':True}
    if (target.get('Name') != '/'+name or target.get('Image') != image or
            target.get('Path') != 'python' or
            target.get('Args') != ['ck_contract_evidence_runner_hf6.py'] or
            target.get('HostConfig',{}).get('AutoRemove') is not True or
            target.get('HostConfig',{}).get('ReadonlyRootfs') is not True or
            target['Id'] in {item['Id'] for item in owned.values()}):
        raise RuntimeError('CONTRACT_WRITER_IDENTITY_MISMATCH')
    if target['State']['Running']:
        docker('stop', '--timeout', '5', target['Id'])
    after = inspect(name, allow_missing=True)
    if after is not None and after['State']['Running']:
        raise RuntimeError('CONTRACT_WRITER_STILL_RUNNING')
    current_image, now = safety(candidate)
    if current_image != image or any(now[n]['Id'] != owned[n]['Id'] or
                                     now[n]['State']['Running'] != owned[n]['State']['Running']
                                     for n in OWNED):
        raise RuntimeError('CONTRACT_OWNED_CHANGED')
    return {'status':'GREEN','writer':'STOPPED_EXACT' if target['State']['Running'] else 'ALREADY_STOPPED',
            'candidate_sha':candidate,'writer_id':target['Id'],
            'real_orders_sent':0,'owned_unchanged':True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    args = parser.parse_args()
    print(json.dumps(stop(args.candidate_sha), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

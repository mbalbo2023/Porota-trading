#!/usr/bin/env python3
"""Bounded read-only forensic probe; emits no book values or credentials."""
import hashlib
import json
from pathlib import Path
import subprocess
import sqlite3
import shutil
import sys

sys.path.insert(0, '/opt/porota-trading')
from scripts import rc6_failed_cold_fix_forward as cold

candidate = 'd1f9691404c4a9145e91a95ca1f2bbf9b137d138'
root = Path('/opt/porota-trading')
deploy = root / 'data/deploy'
shadow = root / 'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
qpath = deploy / ('quiescence-' + candidate + '.json')
if qpath.is_symlink() or qpath.stat().st_size > 65536:
    raise SystemExit('QUIESCENCE_BOUND_RED')
q = json.loads(qpath.read_bytes())
live = cold.enveloped(shadow / 'status.json')
pointer = cold.enveloped(shadow / 'CURRENT.json')
archive = deploy / ('rc6-failed-cold-' + cold.IDENT)
prior = deploy / ('quiescence-' + cold.PRIOR_CANCELLED_SHA + '.json')
inherited = deploy / 'quiescence-83855c3b08647243dcd706ca7ead929378c4c11d.json'
with sqlite3.connect((root/'data/paper_v17/observer_v17.db').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
    db.execute('PRAGMA query_only=ON')
    process,session,mode,orders=db.execute(
        'SELECT process_state,session_state,mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
current_state_path=deploy/'CURRENT_STATE_V2.json'
current_state=json.loads(current_state_path.read_bytes()) if (
    current_state_path.is_file() and not current_state_path.is_symlink()
    and current_state_path.stat().st_size<65536) else {}
images={}
for tag in ('porota-trading-bot:17.0.0-rc6',
            'porota-predeploy-v2:d1f9691404c4a9145e91a95ca1f2bbf9b137d138'):
    raw=subprocess.check_output(['docker','image','inspect',tag],text=True,timeout=5)
    item=json.loads(raw)[0]
    images[tag]={'id':item['Id'],'size':item.get('Size'),
                 'tags':item.get('RepoTags')}
rows=subprocess.check_output(['docker','images','-a','--no-trunc',
    '--format','{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Size}}'],text=True,timeout=8).splitlines()
if len(rows)>128:raise SystemExit('IMAGE_INVENTORY_BOUND_RED')
image_inventory=rows
containers = {}
for name in ('porota_production_observer', 'porota_production_dashboard'):
    raw = subprocess.check_output(['docker', 'inspect', '--format',
        '{{.State.Status}}|{{.Image}}|{{.State.ExitCode}}', name],
        text=True, timeout=5).strip()
    state, image, code = raw.split('|')
    containers[name] = {'status': state, 'image_sha256': image, 'exit_code': int(code)}
caucion = q.get('caucion_carryover')
proof = (q.get('stop_evidence') or {}).get('porota_production_observer') or {}
out = {
    'candidate': candidate,
    'disk_free_bytes': shutil.disk_usage('/').free,
    'images': images,
    'image_inventory': image_inventory,
    'observer_state': {'process':process,'session':session,'mode':mode,'real_orders_sent':orders},
    'current_state': {key:current_state.get(key) for key in
                      ('candidate_sha','validation_status','mode','image_id')},
    'quiescence_status': q.get('status'),
    'installation_started': q.get('installation_started'),
    'pending_caucion': q.get('pending_caucion'),
    'carryover': {'open_count': caucion.get('open_count'),
                  'maturity_at': caucion.get('maturity_at'),
                  'allocation_reconciled': caucion.get('allocation_reconciled'),
                  'audit_source_sha256': caucion.get('audit_source_sha256')}
                 if isinstance(caucion, dict) else None,
    'stop': {'classification': proof.get('classification'),
             'before_status': (proof.get('before') or {}).get('Status'),
             'after_status': (proof.get('after') or {}).get('Status'),
             'finished_at': (proof.get('after') or {}).get('FinishedAt')},
    'containers': containers,
    'archive_exists': archive.is_dir(),
    'archive_receipt_exists': (deploy / ('rc6-cold-fix-forward-' + cold.PRIOR_ARCHIVE_SHA + '.json')).is_file(),
    'current_generation': pointer.get('generation_id'),
    'live_status': {key: live.get(key) for key in
                    ('status', 'error_class', 'error_module', 'error_line',
                     'as_of', 'real_orders_sent', 'real_routes')},
    'same_stop_cancellation': cold.proven_stop_cancellation(live, q),
    'prior_stop_cancellation': cold.proven_prior_stop_cancellation(live, q, prior),
    'inherited_stop_cancellation': cold.proven_inherited_flat_stop_cancellation(
        live, q, inherited),
}
print('RC6_716_COLD_READONLY=' + json.dumps(out, sort_keys=True))

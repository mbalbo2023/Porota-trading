"""A COMPLETE COLD queue is preserved after an earlier FAILED archive."""
import gzip
import json
from pathlib import Path

import pytest

from scripts import rc6_cold2_apply_guard as guard
from scripts import rc6_cold2_quarantine as cold
from scripts import rc6_failed_cold_fix_forward as recovery

FIRST = 'a' * 32
LATEST = 'b' * 32
CUT = 'c' * 64


def write_envelope(path, payload, *, cold_file=False, zipped=False):
    key = 'sha256' if cold_file else 'digest'
    raw = json.dumps({'payload':payload, key:cold.digest(payload)},
                     sort_keys=True).encode()
    path.write_bytes(gzip.compress(raw) if zipped else raw)
    path.chmod(0o600)


def fixture(tmp_path):
    root = tmp_path/'host'
    (root/'data/deploy').mkdir(parents=True)
    (root/'data/deploy/.deploy-quiescence.lock').touch()
    shadow = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    source = shadow/'cold-audit'
    source.mkdir(parents=True, mode=0o700)
    shadow.chmod(0o700)
    manifests = {}
    for ident in (FIRST, LATEST):
        manifest = {'snapshot_id':ident, 'pack':ident+'.pack',
                    'pack_bytes':213835522, 'pack_sha256':'d'*64,
                    'deadline_at':30.0, 'source_as_of':'2026-10-20T10:54:15+00:00',
                    'configuration_fingerprint':'e'*64}
        digest = cold.digest(manifest)
        manifests[ident] = manifest
        write_envelope(source/('manifest-'+ident+'.json'), manifest, cold_file=True)
        write_envelope(source/('receipt-'+ident+'.json'),
                       {'snapshot_id':ident, 'state':'COMPLETE',
                        'manifest_sha256':digest}, cold_file=True)
    write_envelope(source/'queue.json',
                   {'snapshot_id':LATEST,'state':'COMPLETE',
                    'manifest_sha256':cold.digest(manifests[LATEST])}, cold_file=True)
    reference = {'schema':'rc6.computed-audit-reference.v2','state':'PENDING',
                 'snapshot_id':LATEST,'manifest_sha256':cold.digest(manifests[LATEST]),
                 'deadline_at':30.0,'source_as_of':'2026-10-20T10:54:15+00:00',
                 'configuration_fingerprint':'e'*64}
    cut = {'report':{'computed_report':reference},
           'status':{'real_orders_sent':0,'real_routes':'NOT_CALLED'}}
    write_envelope(shadow/('cut-'+CUT+'.json.gz'), cut, zipped=True)
    write_envelope(shadow/'CURRENT.json',
                   {'generation_id':CUT,'file':'cut-'+CUT+'.json.gz'})
    return root, source, shadow, manifests, cut


def test_complete_queue_is_attested_and_left_byte_identical(tmp_path):
    root, source, shadow, _, _ = fixture(tmp_path)
    before = {p:str(p.read_bytes()) for p in list(source.iterdir())+list(shadow.glob('*.json*'))}
    result = guard.verify_complete_queue(root)
    assert result['state'] == 'COMPLETE_UNTOUCHED'
    assert result['snapshot_id'] == LATEST and result['complete_pairs'] == 2
    assert result['old_cut_revoked'] is True
    assert before == {p:str(p.read_bytes()) for p in before}


def test_oct23_complete_queue_and_cancelled_next_capture_remain_distinct(tmp_path):
    root, source, shadow, _, _ = fixture(tmp_path)
    before={p:p.read_bytes() for p in source.iterdir()}
    live={'status':'FAIL_CLOSED','error_class':'ValueError',
          'error_module':'cold_audit','error_line':360,
          'as_of':'2026-10-23T22:18:21.695017+00:00',
          'real_orders_sent':0,'real_routes':'NOT_CALLED'}
    stop={'classification':'CLEAN_EXIT',
          'before':{'Status':'running','StartedAt':'2026-10-22T23:49:15.51107753Z'},
          'after':{'Status':'exited','ExitCode':0,'OOMKilled':False,
                   'FinishedAt':recovery.CLOSED_FLAT_STAGE_STOP_FINISHED}}
    receipt={'candidate_sha':recovery.CLOSED_FLAT_STAGE_STOP_SHA,
             'paused_postclose':False,'intraday_caucion':False,
             'market_open_suspended':False,'open_paper_carryover':False,
             'spot_carryover':None,'caucion_carryover':None,
             'stop_evidence':{'porota_production_observer':stop}}
    write_envelope(shadow/'status.json',live)
    assert recovery.proven_stop_cancellation(live,receipt)
    assert guard.verify_complete_queue(root)['state']=='COMPLETE_UNTOUCHED'
    assert before=={p:p.read_bytes() for p in source.iterdir()}
    assert cold.envelope(shadow/'status.json')['status']=='FAIL_CLOSED'
    assert not recovery.proven_stop_cancellation({**live,'error_line':361},receipt)


@pytest.mark.parametrize('change,reason', [
    ('failed_queue','COLD2_COMPLETE_QUEUE_UNSAFE'),
    ('queue_mismatch','COLD2_COMPLETE_QUEUE_BINDING'),
    ('failed_other','COLD2_COMPLETE_PAIR_UNSAFE'),
    ('unexpected_pack','COLD2_COMPLETE_NAMESPACE_UNSAFE'),
    ('cut_mismatch','COLD2_COMPLETE_CUT_UNSAFE'),
])
def test_changed_or_failed_successor_cannot_be_accepted(tmp_path, change, reason):
    root, source, shadow, manifests, cut = fixture(tmp_path)
    if change == 'failed_queue':
        write_envelope(source/'queue.json',
            {'snapshot_id':LATEST,'state':'FAILED',
             'manifest_sha256':cold.digest(manifests[LATEST])},cold_file=True)
    elif change == 'queue_mismatch':
        write_envelope(source/'queue.json',
            {'snapshot_id':LATEST,'state':'COMPLETE',
             'manifest_sha256':'f'*64},cold_file=True)
    elif change == 'failed_other':
        write_envelope(source/('receipt-'+FIRST+'.json'),
            {'snapshot_id':FIRST,'state':'FAILED',
             'manifest_sha256':cold.digest(manifests[FIRST])},cold_file=True)
    elif change == 'unexpected_pack':
        (source/(LATEST+'.pack')).write_bytes(b'x')
    else:
        cut['report']['computed_report']['snapshot_id'] = FIRST
        write_envelope(shadow/('cut-'+CUT+'.json.gz'),cut,zipped=True)
    with pytest.raises(ValueError, match=reason):
        guard.verify_complete_queue(root)

def test_apply_preserves_complete_queue_under_exact_stopped_admission(tmp_path):
    from unittest.mock import patch
    root, source, shadow, _, _ = fixture(tmp_path)
    archive = root/'data/deploy'/('rc6-failed-cold-'+cold.IDENT)
    archive.mkdir(mode=0o700)
    (archive/'quarantine.json').write_bytes(b'previously verified archive')
    write_envelope(shadow/'status.json',
                   {'status':'FAIL_CLOSED','real_routes':'NOT_CALLED',
                    'real_orders_sent':0})
    candidate = 'f'*40
    ids = {name:str(index+1)*64 for index,name in enumerate(guard.OWNED)}
    receipt = {'containers':{name:{'image':'sha256:'+'a'*64}
                             for name in guard.OWNED}}
    rows = {name:{'Id':ids[name],'Image':'sha256:'+'a'*64,
                  'State':{'Status':'exited','ExitCode':0}}
            for name in guard.OWNED}
    rows['porota_critical_approval_rc6'] = {
        'State':{'Status':'running','OOMKilled':False,
                 'Health':{'Status':'healthy'}}}
    with (patch.object(guard,'receipt_for',return_value=(receipt,ids)),
          patch.object(guard,'ppi_units',return_value={}),
          patch.object(guard.drain,'admit',return_value='STOPPED'),
          patch.object(guard.drain,'inspect',side_effect=lambda name:rows[name]),
          patch.object(guard.cold,'verify_original_archive',
                       return_value={'state':'ALREADY_QUARANTINED','old_cut_revoked':True}),
          patch.object(guard.successor,'quarantine') as quarantine):
        result = guard.apply(root,candidate)
    assert result['successor']['state'] == 'COMPLETE_UNTOUCHED'
    assert result['successor']['snapshot_id'] == LATEST
    quarantine.assert_not_called()
    assert (source/'queue.json').exists()

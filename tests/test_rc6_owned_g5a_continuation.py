"""Cheap transport/revocation counterexamples; no ROOT, quota or G5 launch."""
import copy
from datetime import timedelta
import hashlib
from types import SimpleNamespace

import pytest

import test_rc6_owned_gate_lease as fixtures
from scripts import rc6_owned_gate_lease as leases
from scripts import rc6_owned_g5a_continuation as continuation
from scripts import rc6_material_pr_admission as admission


def pulse_fixture(tmp_path, monkeypatch):
    monitor, api, clock = fixtures.monitor(tmp_path, monkeypatch, gate='Horizon')
    monitor.prelaunch('HorizonOriginal')
    binding = {'candidate_sha': fixtures.SHA, 'candidate_tree': fixtures.TREE,
        'owner_id': admission.SUCCESSOR_OWNER, 'producer': 'Horizon-private-original-cas',
        'attempt_id': '71:1', 'runner_class': 'github-hosted/ubuntu-24.04',
        'workload_fingerprint': 'd' * 64}
    pulse = {'schema': continuation.SCHEMA, 'sequence': 1, 'snapshot': monitor.last_observation,
        'binding': binding, 'namespace_nonce': 'e' * 32, 'new_launch_authorized': False, 'G5_GREEN': False}
    return monitor, api, clock, binding, pulse


def replay(monitor, clock, binding, pulse):
    return continuation.replay_pulse(pulse, read_object=monitor.objects.__getitem__,
        binding=binding, nonce='e'*32, now=clock.wall())


def test_real_original_scope_replayed_documentary_without_mint(tmp_path, monkeypatch):
    monitor, api, clock, binding, pulse = pulse_fixture(tmp_path, monkeypatch)
    result = replay(monitor, clock, binding, pulse)
    assert result['documentary_only'] is True and result['context'] == monitor.context
    with pytest.raises(ValueError, match='NATIVE_AUTHENTIC_FACTORY'):
        continuation.NativeG5AContinuation()
    with pytest.raises(ValueError, match='CURRENT_LIVE_TYPED_AUTHORITY'):
        continuation.validate_continuation(result, binding=binding)


@pytest.mark.parametrize('change', ['nonce', 'Source', 'tree', 'owner', 'GREEN', 'launch', 'sequence',
    'deleted', 'bytes', 'stale', 'future', 'expired'])
def test_documentary_pulse_tampering_cannot_be_accepted(tmp_path, monkeypatch, change):
    monitor, api, clock, binding, pulse = pulse_fixture(tmp_path, monkeypatch)
    if change == 'nonce': pulse['namespace_nonce'] = 'f'*32
    elif change in ('Source', 'tree', 'owner'):
        pulse['binding'] = copy.deepcopy(binding)
        pulse['binding'][{'Source': 'candidate_sha', 'tree': 'candidate_tree', 'owner': 'owner_id'}[change]] = 'f'*40
    elif change == 'GREEN': pulse['G5_GREEN'] = True
    elif change == 'launch': pulse['new_launch_authorized'] = True
    elif change == 'sequence': pulse['sequence'] = True
    elif change == 'deleted': monitor.objects.pop(pulse['snapshot'])
    elif change == 'bytes': monitor.objects[pulse['snapshot']] += b' '
    elif change == 'stale': clock.advance(66)
    elif change == 'future': clock.advance(-1)
    elif change == 'expired': clock.advance(1200)
    with pytest.raises((ValueError, KeyError)):
        replay(monitor, clock, binding, pulse)


@pytest.mark.parametrize('field', ['CONTRACT_REVISION_REVOKED', 'CONTRACT_DECISION_REVOKED',
                                 'RC6_CONTRACT_REVISION_AUTHORIZATION'])
def test_matching_contract_revocation_blocks_same_operation(field):
    approval = SimpleNamespace(source_sha=fixtures.SHA, revision='G5A_BOUNDED_EVIDENCE_V2')
    value = 'REVOKED' if field == 'RC6_CONTRACT_REVISION_AUTHORIZATION' else 'true'
    row = {'body': 'SOURCE_SHA='+fixtures.SHA+'\nCONTRACT_REVISION=G5A_BOUNDED_EVIDENCE_V2\n'+field+'='+value+'\n'}
    with pytest.raises(ValueError, match='OWNER_REVOCATION'):
        continuation.reject_revocations(approval, {471: [row], 473: []})


def test_read_source_control_preserves_identity_and_forbids_hardlink(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path/'control.json'
    path.write_bytes(b'{"native":false}\n'); path.chmod(0o600)
    root = continuation._root_identity(tmp_path)
    before = tuple(getattr(path.stat(), name) for name in admission.STAT11)
    assert continuation._read(path, root_identity=root) == b'{"native":false}\n'
    assert before == tuple(getattr(path.stat(), name) for name in admission.STAT11)
    (tmp_path/'alias.json').hardlink_to(path)
    with pytest.raises(ValueError, match='SINGLELINK_CONTROL'):
        continuation._read(path, root_identity=root)


def test_decision_edit_delete_and_pair_rebinding_are_discriminated():
    bodies = {471: 'Original owner decision\n', 473: 'Paired original decision\n'}
    rows = {str(issue): fixtures.comment(issue, issue, bodies[issue], leases.text_stamp(fixtures.START))
        for issue in bodies}
    approval = SimpleNamespace(approval_url='https://github.com/'+admission.REPO+'/issues/471#issuecomment-471',
        approval_body_sha256=hashlib.sha256(bodies[471].encode()).hexdigest(), paired_comment_id=473,
        paired_body_sha256=hashlib.sha256(bodies[473].encode()).hexdigest())
    reader = lambda path: rows[path.rsplit('/', 1)[1]]
    continuation.validate_decision_documents(approval, reader)
    rows['473']['updated_at'] = leases.text_stamp(fixtures.START+timedelta(seconds=1))
    with pytest.raises(ValueError, match='DECISION_EDITED_DELETED_OR_REBOUND'):
        continuation.validate_decision_documents(approval, reader)

"""Cheap lease/control counterexamples; no financial/material producer runs."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import rc6_owned_gate_lease as lease
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_controlled_native_child_manager as native
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_material_carrier as carrier
from scripts import rc6_architectural_gates as gates

SHA = 'a' * 40
TREE = 'b' * 40
RUN = 71
START = datetime(2026, 10, 8, 18, 45, tzinfo=timezone.utc)


def owner_comment(issue, identifier, begin, expiry, *, sha=SHA, tree=TREE, owner=None):
    owner = owner or admission.SUCCESSOR_OWNER
    body = '\n'.join(('WORKSTREAM_ID=' + admission.WORKSTREAM, 'SESSION_SUCCESSOR=' + owner,
        'WRITE_OWNER=' + owner, 'INTEGRATION_OWNER=' + owner, 'DEPLOY_OWNER=NOT_ACQUIRED', 'RELEASED=false',
        'SOURCE_SHA=' + sha, 'SOURCE_TREE=' + tree, 'SOURCE_LEASE_EXPIRES_UTC=' + lease.text_stamp(expiry),
        'MODE=PRODUCTION_PAPER / SIMULATION', 'real_orders_sent=0')) + '\n'
    return comment(issue, identifier, body, lease.text_stamp(begin))


def comment(issue, identifier, body, stamp):
    return {'id': identifier, 'user': {'login': 'mbalbo2023'}, 'body': body, 'created_at': stamp,
        'updated_at': stamp, 'issue_url': 'https://api.github.com/repos/' + admission.REPO + '/issues/' + str(issue),
        'html_url': 'https://github.com/' + admission.REPO + '/issues/' + str(issue) + '#issuecomment-' + str(identifier)}


class Clock:
    def __init__(self):
        self.elapsed = 0
    def wall(self):
        return START + timedelta(seconds=self.elapsed)
    def mono(self):
        return 1000 + self.elapsed
    def advance(self, seconds):
        self.elapsed += seconds


class ApiFixture:
    """Original admission predicates, locally generated transport documents."""
    def __init__(self, monkeypatch, gate='full-gov311'):
        self.calls = []
        self.gate = gate
        self.documents = {}
        self.rows = {}
        recovery_body = '\n'.join(('WORKSTREAM_ID=' + admission.WORKSTREAM,
            'SESSION=' + admission.RECOVERY_OWNER, 'WRITE_OWNER=' + admission.RECOVERY_OWNER,
            'INTEGRATION_OWNER=' + admission.RECOVERY_OWNER, 'DEPLOY_OWNER=NOT_ACQUIRED', 'RELEASED=false',
            admission.RECONCILED_OLD_OWNER, 'state blob' + admission.RECONCILED_BLOB, admission.USER_STOP_LITERAL)) + '\n'
        successor_body = '\n'.join(('WORKSTREAM_ID=' + admission.WORKSTREAM,
            'SESSION_SUCCESSOR=' + admission.SUCCESSOR_OWNER, 'WRITE_OWNER=' + admission.SUCCESSOR_OWNER,
            'INTEGRATION_OWNER=' + admission.SUCCESSOR_OWNER, 'DEPLOY_OWNER=NOT_ACQUIRED', 'RELEASED=false',
            admission.RECONCILED_BLOB, admission.RECONCILED_RAW_SHA256,
            *(str(anchor[0]) for anchor in admission.RECOVERY_ANCHORS.values()))) + '\n'
        # Only the unit fixture's anchor document digests are replaced. Every
        # production author/issue/source/time/owner/coverage predicate executes.
        monkeypatch.setattr(admission, 'RECOVERY_BODY_SHA256', lease.digest(recovery_body.encode()))
        monkeypatch.setattr(admission, 'SUCCESSOR_BODY_SHA256', lease.digest(successor_body.encode()))
        for issue, (identifier, stamp) in admission.RECOVERY_ANCHORS.items():
            self.documents['/issues/comments/' + str(identifier)] = comment(issue, identifier, recovery_body, stamp)
        for issue, (identifier, stamp) in admission.SUCCESSOR_ANCHORS.items():
            row = comment(issue, identifier, successor_body, stamp)
            self.documents['/issues/comments/' + str(identifier)] = row
            self.rows[issue] = [row]
        self.launch = comment(471, 9001, '\n'.join((
            'RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION=APPROVED', 'WORKSTREAM_ID=' + admission.WORKSTREAM,
            'SOURCE_SHA=' + SHA, 'SOURCE_TREE=' + TREE, 'SESSION_SUCCESSOR=' + admission.SUCCESSOR_OWNER,
            'WRITE_OWNER=' + admission.SUCCESSOR_OWNER, 'INTEGRATION_OWNER=' + admission.SUCCESSOR_OWNER,
            'DEPLOY_OWNER=NOT_ACQUIRED', 'RELEASED=false', 'MODE=PRODUCTION_PAPER / SIMULATION', 'real_orders_sent=0',
            'GATES_AUTHORIZED=' + gate, 'CAUSE_EVIDENCE_URL=https://github.com/' + admission.REPO + '/issues/473#issuecomment-9002',
            'CAPACITY_PEAKS_JSON={}', 'PREREQUISITES_MANIFEST_JSON={"source_sha":"' + SHA + '","source_tree":"' + TREE + '"}',
            admission.BRANCH)) + '\n', '2026-10-08T18:43:00Z')
        self.documents['/issues/comments/9001'] = self.launch
        self.documents['/issues/comments/9002'] = comment(473, 9002, 'Unit control RCA fixture', '2026-10-08T18:42:00Z')
        self.rows[471].append(self.launch)
        self.documents[''] = {'id': admission.REPO_ID, 'full_name': admission.REPO}
        self.documents['/git/ref/heads/' + admission.BRANCH] = {'object': {'sha': SHA}}
        self.documents['/git/commits/' + SHA] = {'sha': SHA, 'tree': {'sha': TREE}}
        self.documents['/pulls/476'] = {'number': 476, 'state': 'open', 'draft': True,
            'user': {'login': 'mbalbo2023'}, 'head': {'sha': SHA, 'ref': admission.BRANCH,
                'repo': {'id': admission.REPO_ID, 'owner': {'login': 'mbalbo2023'}}},
            'base': {'repo': {'id': admission.REPO_ID}, 'ref': admission.BASE, 'sha': admission.BASE_SHA}}
        self.documents['/actions/runs/71/attempts/1'] = {'id': RUN, 'run_attempt': 1, 'head_sha': SHA,
            'status': 'in_progress', 'conclusion': None, 'repository': {'id': admission.REPO_ID, 'full_name': admission.REPO},
            'head_repository': {'id': admission.REPO_ID}, 'path': '.github/workflows/rc6-unified-candidate-tests.yml'}
        ops = lease.wire({'repository_id': admission.REPO_ID, 'repository': admission.REPO,
            'deploy_owner': None, 'real_orders_sent': 0, 'lease': None, 'write_owner': None})
        self.documents['/contents/' + admission.OPS + '?ref=' + admission.OPS_REF.replace('/', '%2F')] = {
            'type': 'file', 'path': admission.OPS, 'encoding': 'base64', 'content': base64.b64encode(ops).decode(),
            'sha': hashlib.sha1(b'blob ' + str(len(ops)).encode() + b'\0' + ops).hexdigest()}
        if gate in admission.DIAGNOSTIC_GATES:
            scope = {'schema': 'porota.rc6.capacity-diagnostic-scope.v1', 'mode': gate,
                'backing_image_bytes': (5 if gate == 'capacity-probe' else 26) * 1024**3,
                'project_hard_limit_bytes': 512 * 1024**2 if gate == 'capacity-probe' else 20 * 1024**3,
                'residual_reserve_bytes': 4 * 1024**3, 'financial_tick_allowed': False, 'qualification_claimed': False}
            prior = None if gate == 'capacity-probe' else {'unit_only_artifact_reference': True}
            body = '\n'.join((
                'RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION=APPROVED', 'WORKSTREAM_ID=' + admission.WORKSTREAM,
                'DIAGNOSTIC_MODE=' + gate, 'SOURCE_WIP=' + admission.DIAGNOSTIC_BRANCH,
                'SOURCE_SHA=' + SHA, 'SOURCE_TREE=' + TREE, 'SESSION_SUCCESSOR=' + admission.SUCCESSOR_OWNER,
                'WRITE_OWNER=' + admission.SUCCESSOR_OWNER, 'INTEGRATION_OWNER=' + admission.SUCCESSOR_OWNER,
                'DEPLOY_OWNER=NOT_ACQUIRED', 'RELEASED=false', 'MODE=PRODUCTION_PAPER / SIMULATION', 'real_orders_sent=0',
                'READ_CONTRACT_SHA256=' + 'd'*64, 'SOURCE_MANIFEST_SHA256=' + 'e'*64,
                'CAPACITY_DIAGNOSTIC_SCOPE_JSON=' + admission.wire(scope).decode().strip(),
                'CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON=' + admission.wire(prior).decode().strip())) + '\n'
            self.launch['body'] = body
            self.documents[''].update(private=False)
            self.documents['/git/ref/heads/' + admission.DIAGNOSTIC_BRANCH] = {'object': {'sha': SHA}}
            self.documents['/git/commits/' + SHA]['parents'] = [{'sha': admission.RECOVERY_HEAD}]
            self.documents['/git/commits/' + admission.RECOVERY_HEAD] = {
                'sha': admission.RECOVERY_HEAD, 'tree': {'sha': admission.RECOVERY_TREE}}
            self.documents['/pulls/476']['head']['sha'] = admission.RECOVERY_HEAD
            self.documents['/pulls/476'].update(merged=False, merged_at=None)
            self.documents['/pulls/477'] = {'number': 477, 'state': 'open', 'draft': True, 'merged': False,
                'merged_at': None, 'user': {'login': 'mbalbo2023'}, 'head': {'sha': admission.GUARDS_HEAD,
                    'ref': 'governance/rc6-error-learning-runner-20261007', 'repo': {'id': admission.REPO_ID}},
                'base': {'ref': admission.BRANCH}}
            self.documents['/actions/runs/71/attempts/1'].update(
                head_branch=admission.DIAGNOSTIC_BRANCH, event='workflow_dispatch')
            self.authority = {'schema': 'porota.rc6.capacity-diagnostic-admission.v1',
                'status': 'ADMITTED_DIAGNOSTIC_NOT_STARTED', 'source_sha': SHA, 'source_tree': TREE,
                'gate': gate, 'owner_session': admission.SUCCESSOR_OWNER, 'launch_receipt_url': self.launch['html_url'],
                'launch_body_sha256': lease.digest(body.encode()), 'source_manifest_sha256': 'e'*64,
                'read_contract_sha256': 'd'*64, 'scope': scope, 'capability_prerequisite': prior,
                'qualification_claimed': False, 'source': {'repository_public_verified': True,
                    'anonymous_source_origin': carrier.ORIGIN}}
        self.renew(START - timedelta(minutes=1))
    def renew(self, begin, *, duration=1200, **kwargs):
        for issue in (471, 473):
            identifier = 10000 + issue + len(self.rows[issue])
            self.rows[issue].append(owner_comment(issue, identifier, begin, begin + timedelta(seconds=duration), **kwargs))
    def get(self, path, deadline):
        assert 0 < deadline - time.monotonic() <= 5
        self.calls.append(path)
        if path.startswith('/issues/471/comments?'):
            return copy.deepcopy(self.rows[471])
        if path.startswith('/issues/473/comments?'):
            return copy.deepcopy(self.rows[473])
        return copy.deepcopy(self.documents[path])


def monitor(tmp_path, monkeypatch, *, gate='full-gov311', real_clock=False):
    tmp_path.chmod(0o700)
    api = ApiFixture(monkeypatch, gate)
    clock = Clock()
    kwargs = {} if real_clock else {'clock': clock.wall, 'monotonic': clock.mono}
    instance = lease.GateLeaseMonitor(tmp_path, source_sha=SHA, source_tree=TREE,
        owner_session=admission.SUCCESSOR_OWNER, gate=gate, launch_receipt_url=api.launch['html_url'],
        run_id=RUN, run_attempt=1, get=api.get,
        diagnostic_binding=admission.diagnostic_control_binding(api.authority)
            if gate in admission.DIAGNOSTIC_GATES else None, **kwargs)
    return instance, api, clock


def kernel(pid=123):
    return {'pid': pid, 'returncode': 0, 'actual_child_reaped': True, 'wait4_reaped_pid': pid,
        'kernel_pre_popen_echild_verified': True, 'owned_children_exhaustion_verified': True,
        'process_group_absent_after_reap': True, 'subreaper_activation_readback_verified': True,
        'subreaper_restore_attempted': True, 'subreaper_restoration_readback_verified': True,
        'remaining_owned_children': [], 'timed_out': False, 'late_observed_main_reap_irreversible_red': False,
        'kernel_wait4_zero_observed_irreversible_red': False, 'residual_descendants_observed': [],
        'owned_group_signal_observations': [], 'supervisor_errors': [], 'wall_seconds': 1,
        'adopted_descendants_reaped': []}


def replay(instance, receipt, actual_kernel):
    # No filesystem or current clock is used by historical semantic replay.
    return lease.replay_lease_evidence(receipt,
        read_object=lambda identifier: instance.objects[identifier], kernel=actual_kernel,
        source_sha=SHA, source_tree=TREE, run_id=RUN, run_attempt=1, expected_label=instance.command_label)


def test_expired_prelaunch_physically_cannot_invoke_producer(tmp_path, monkeypatch):
    instance, _api, clock = monitor(tmp_path, monkeypatch)
    clock.advance(1200)
    sentinel = tmp_path / 'producer-started'
    with pytest.raises(lease.LeaseControlFailure, match='LEASE_EXPIRED'):
        instance.prelaunch('fullGov311')
        sentinel.write_bytes(b'FORBIDDEN')
    assert not sentinel.exists()


def test_renewal_allows_original_long_gate_and_historical_expiration_does_not_reject(tmp_path, monkeypatch):
    instance, api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    deadline = instance.native_deadline
    for index in range(1, 31):
        clock.advance(55)
        if index in (17, 29):
            api.renew(clock.wall() - timedelta(seconds=1))
        instance.progress('poll', 123, 1000, deadline, 0)
    actual = kernel()
    receipt = instance.after_fin(actual)
    assert receipt['status'] == 'GREEN'
    assert len(receipt['lease_coverage']['471']['comment_ids']) >= 2
    clock.advance(86400)
    assert replay(instance, receipt, actual)['past_lease_compared_to_current_time'] is False
    assert receipt['deduplicated_object_bytes'] < 150000


@pytest.mark.parametrize('change', ['foreign', 'edited', 'deleted', 'sha', 'tree', 'deploy', 'run_cancelled', 'attempt'])
def test_control_change_during_native_is_irreversible_red(tmp_path, monkeypatch, change):
    instance, api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    if change == 'foreign':
        api.renew(clock.wall(), owner='FOREIGN_OWNER')
    elif change == 'edited':
        api.rows[473][-1]['updated_at'] = lease.text_stamp(clock.wall())
    elif change == 'deleted':
        api.rows[473].pop()
    elif change == 'sha':
        api.documents['/git/ref/heads/' + admission.BRANCH]['object']['sha'] = 'c' * 40
    elif change == 'tree':
        api.documents['/git/commits/' + SHA]['tree']['sha'] = 'c' * 40
    elif change == 'deploy':
        api.rows[473][-1]['body'] = api.rows[473][-1]['body'].replace('DEPLOY_OWNER=NOT_ACQUIRED', 'DEPLOY_OWNER=FOREIGN')
    elif change == 'run_cancelled':
        api.documents['/actions/runs/71/attempts/1']['status'] = 'completed'
    else:
        api.documents['/actions/runs/71/attempts/1']['run_attempt'] = 2
    clock.advance(55)
    with pytest.raises(lease.LeaseControlFailure):
        instance.progress('poll', 123, 1000, instance.native_deadline, 0)
    assert instance.stop_reason is not None
    result = instance.after_fin(kernel())
    assert result['status'] == 'RED' and result['physical_fin_closed'] is True


def test_api_failure_never_uses_cached_green(tmp_path, monkeypatch):
    instance, _api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    def unavailable(*_args):
        raise OSError('network unavailable')
    instance.get = unavailable
    clock.advance(55)
    with pytest.raises(lease.LeaseControlFailure, match='CONTROL_UNAVAILABLE'):
        instance.progress('poll', 123, 1000, instance.native_deadline, 0)


def test_expiry_is_checked_locally_without_another_http_request(tmp_path, monkeypatch):
    instance, api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    count = len(api.calls)
    clock.advance(1140)
    with pytest.raises(lease.LeaseControlFailure, match='EXPIRED_DURING'):
        instance.progress('poll', 123, 1000, instance.native_deadline, 0)
    assert len(api.calls) == count


def test_renewal_after_expiry_cannot_fill_historical_gap(tmp_path, monkeypatch):
    instance, api, _clock = monitor(tmp_path, monkeypatch)
    api.renew(START + timedelta(minutes=20))
    with pytest.raises(lease.LeaseControlFailure, match='CONTINUITY_GAP'):
        lease.prove_lease_coverage(api.rows, instance.context, START, START + timedelta(minutes=21))


def test_native_green_with_failed_post_fin_readmission_remains_red(tmp_path, monkeypatch):
    instance, _api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    def denied():
        raise ValueError('ACTUAL_SOURCE_REBOUND_AFTER_FIN')
    receipt = instance.after_fin(kernel(), readmit=denied)
    assert receipt['status'] == 'RED' and receipt['post_fin_readmission_verified'] is False
    with pytest.raises(lease.LeaseControlFailure, match='CONTROL_RED'):
        replay(instance, receipt, kernel())


@pytest.mark.parametrize('field,value', [('source_sha', 'c'*40), ('source_tree', 'c'*40), ('run_id', 72), ('run_attempt', 2)])
def test_archived_source_and_run_binding_cannot_be_replaced(tmp_path, monkeypatch, field, value):
    instance, _api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    receipt = instance.after_fin(kernel())
    receipt['context'][field] = value
    with pytest.raises(lease.LeaseControlFailure):
        replay(instance, receipt, kernel())


def test_exact_archived_snapshot_bytes_are_required(tmp_path, monkeypatch):
    instance, _api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    receipt = instance.after_fin(kernel())
    identifier = receipt['snapshots'][0]
    instance.objects[identifier] += b' '
    with pytest.raises(lease.LeaseControlFailure, match='OBJECT_BYTES_CHANGED'):
        replay(instance, receipt, kernel())


def test_fin_unknown_vetoes_any_post_fin_read_or_record(tmp_path, monkeypatch):
    instance, api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    bad = kernel()
    bad['owned_children_exhaustion_verified'] = False
    count = len(api.calls)
    with pytest.raises(lease.LeaseControlFailure, match='FIN_UNKNOWN'):
        instance.after_fin(bad)
    assert len(api.calls) == count and instance.final_path is None


def test_native_callback_failure_keeps_original_term_reap_echild_and_restoration(tmp_path):
    tmp_path.chmod(0o700)
    previous = native.pre_capture_kernel_state()
    callbacks = []
    def failure(stage, pid, _entered, _deadline, _fd):
        callbacks.append((stage, pid))
        raise lease.LeaseControlFailure('OWNED_LEASE_UNIT_CONTROL_RED')
    actual = native.managed_native_child([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(30)'],
        tmp_path, tmp_path/'native.log', dict(os.environ), 60, progress=failure)
    assert callbacks == [('started', actual['pid'])]
    assert native.managed_custody_closed(actual) is True and native.managed_phase_green(actual) is False
    assert actual['wait4_reaped_pid'] == actual['pid'] and actual['remaining_owned_children'] == []
    assert actual['process_group_absent_after_reap'] is True and actual['owned_children_exhaustion_verified'] is True
    assert actual['owned_cleanup_management_bound_seconds'] == 5
    assert 0 <= actual['termination_reap_restore_cleanup_seconds'] <= 5
    assert actual['owned_group_signal_observations'][0]['signal'] == signal.SIGTERM
    assert actual['supervisor_errors'][0]['reason'] == 'OWNED_LEASE_UNIT_CONTROL_RED'
    after = native.pre_capture_kernel_state()
    assert previous['subreaper_state_before'] == after['subreaper_state_after']


def test_authenticated_adapter_forwards_callback_and_preserves_original_fin_authority(tmp_path):
    tmp_path.chmod(0o700)
    binding = {'candidate_sha': SHA, 'candidate_tree': TREE, 'producer': 'full-gov311', 'attempt_id': '71:1',
        'owner_id': admission.SUCCESSOR_OWNER, 'workload_fingerprint': 'd'*64, 'runner_class': 'DIAGNOSTIC'}
    namespace = lifecycle.create_namespace(tmp_path, binding)
    observations = []
    def progress(stage, pid, entered, deadline, fd):
        assert deadline > entered and os.fstat(fd).st_size >= 0
        observations.append((stage, pid))
    actual, fin = lifecycle.execute_owned(namespace, [sys.executable, '-I', '-B', '-c', 'pass'],
        cwd=tmp_path, environ=dict(os.environ), timeout_seconds=60, progress=progress)
    assert observations[0] == ('started', actual['pid']) and native.managed_phase_green(actual) is True
    capture = lifecycle.capture_required_evidence(namespace, fin, tmp_path/'sealed', ['producer-native.log'])
    cleaned = lifecycle.cleanup_namespace(namespace, fin, capture)
    assert cleaned['namespace_removed'] is True and cleaned['actual_owned_fin_closed'] is True


def test_http_total_budget_and_original_alarm_state_are_restored():
    previous = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    with pytest.raises(lease.LeaseControlFailure, match='HTTP_TOTAL_BUDGET_EXPIRED'):
        with lease._http_budget(started + .02):
            time.sleep(.1)
    assert time.monotonic() - started < .1
    assert signal.getsignal(signal.SIGALRM) == previous and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_ownership_objects_are_written_once_across_multiple_commands(tmp_path, monkeypatch):
    first, api, clock = monitor(tmp_path, monkeypatch)
    first.prelaunch('first')
    first.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    first.after_fin(kernel())
    counts = len(list(tmp_path.glob(lease.OBJECT_PREFIX+'*.json')))
    second = lease.GateLeaseMonitor(tmp_path, **first.context, get=api.get, clock=clock.wall, monotonic=clock.mono)
    second.prelaunch('second')
    second.progress('started', 124, clock.mono(), clock.mono() + 5400, 0)
    result = second.after_fin(kernel(124))
    assert result['status'] == 'GREEN'
    assert len(list(tmp_path.glob(lease.OBJECT_PREFIX+'*.json'))) == counts


def test_historical_g6_lease_and_fresh_g7_owner_are_separate_barriers(tmp_path, monkeypatch):
    instance, api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    receipt = instance.after_fin(kernel())
    clock.advance(86400)
    assert replay(instance, receipt, kernel())['historical_launch_and_lease_union_verified'] is True
    with pytest.raises(ValueError, match='LEASE_EXPIRED'):
        admission.latest_writer(api.rows[471], 471, SHA, TREE, admission.SUCCESSOR_OWNER, clock.wall())
    api.renew(clock.wall() - timedelta(seconds=1))
    current = admission.latest_writer(api.rows[471], 471, SHA, TREE, admission.SUCCESSOR_OWNER, clock.wall())
    assert admission.stamp(current['lease_expires_utc']) > clock.wall()


def test_ownership_verifier_replays_exact_archived_objects_without_http(tmp_path, monkeypatch):
    import zipfile
    instance, _api, clock = monitor(tmp_path, monkeypatch)
    instance.prelaunch('fullGov311')
    instance.progress('started', 123, clock.mono(), clock.mono() + 5400, 0)
    actual = kernel()
    receipt = instance.after_fin(actual)
    owner_wire, kernel_wire = lease.wire(receipt), lease.wire(actual)
    index = {'schema': 'porota.rc6.owned-gate-lease-index.v1', 'source_sha': SHA, 'source_tree': TREE,
        'run_id': RUN, 'run_attempt': 1, 'object_prefix': 'controls/' + lease.OBJECT_PREFIX,
        'commands': [{'label': 'fullGov311', 'lease': {'path': 'controls/owner-fullGov311.json',
            'sha256': lease.digest(owner_wire)}, 'kernel': {'path': 'controls/fullGov311.kernel.json',
                'sha256': lease.digest(kernel_wire)}}]}
    archive = tmp_path/'historical.zip'
    with zipfile.ZipFile(archive, 'w') as stored:
        stored.writestr('controls/owner-fullGov311.json', owner_wire)
        stored.writestr('controls/fullGov311.kernel.json', kernel_wire)
        for identifier, raw in instance.objects.items():
            stored.writestr('controls/'+lease.OBJECT_PREFIX+identifier+'.json', raw)
    row = {'gate': 'G6.311', 'python_epoch': '311', 'source_sha': SHA, 'source_tree': TREE,
        'run_id': RUN, 'run_attempt': 1, 'started_utc': lease.text_stamp(START),
        'completed_utc': lease.text_stamp(START + timedelta(seconds=1))}
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Historical leases cannot trigger current HTTP or Source admission')
    monkeypatch.setattr(lease, 'api_get', forbidden)
    monkeypatch.setattr(lease, 'utc', forbidden)
    assert gates.verify_owned_gate_lease_index(index, row, archive)['current_G7_owner_checked_separately'] is True
    index['commands'] = []
    with pytest.raises(ValueError, match='OWNERSHIP_INDEX_SOURCE_RUN_REBOUND'):
        gates.verify_owned_gate_lease_index(index, row, archive)


def test_carrier_lease_red_preserves_original_fin_and_owned_raw_without_green(tmp_path, monkeypatch):
    from types import SimpleNamespace
    tmp_path.chmod(0o700)
    controls = tmp_path/'controls'
    controls.mkdir(mode=0o700)
    api = ApiFixture(monkeypatch)
    real_constructor = lease.GateLeaseMonitor
    origin = time.monotonic()
    def make_monitor(root, **kwargs):
        instance = real_constructor(root, **kwargs, run_id=RUN, run_attempt=1, get=api.get,
            clock=lambda: START + timedelta(seconds=time.monotonic()-origin))
        original_progress = instance.progress
        def changed_at_start(*args):
            api.documents['/git/ref/heads/'+admission.BRANCH]['object']['sha'] = 'c'*40
            # Force the first live callback to make an API observation.
            instance.last_api_poll -= 55
            original_progress(*args)
        instance.progress = changed_at_start
        return instance
    monkeypatch.setattr(carrier.gate_lease, 'GateLeaseMonitor', make_monitor)
    run = carrier.OwnedRunner(vars(native), controls)
    run.lease_context = {key: value for key, value in {
        'source_sha': SHA, 'source_tree': TREE, 'owner_session': admission.SUCCESSOR_OWNER,
        'gate': 'full-gov311', 'launch_receipt_url': api.launch['html_url']}.items()}
    binding = {'candidate_sha': SHA, 'candidate_tree': TREE, 'producer': 'full-gov311', 'attempt_id': '71:1',
        'owner_id': admission.SUCCESSOR_OWNER, 'workload_fingerprint': 'd'*64, 'runner_class': 'DIAGNOSTIC'}
    namespace = lifecycle.create_namespace(tmp_path, binding)
    with pytest.raises(carrier.OwnedLeaseStop, match='LEASE_RED_AFTER_ACTUAL_FIN'):
        run([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(30)'], cwd=tmp_path,
            label='fullGov311', limit=60, namespace=namespace)
    assert run.unknown is False and run.lease_stop['lease']['status'] == 'RED'
    assert native.managed_custody_closed(run.lease_stop['kernel']) is True
    assert native.managed_phase_green(run.lease_stop['kernel']) is False
    args = SimpleNamespace(repo_root=Path(__file__).absolute().parents[1], source_sha=SHA, source_tree=TREE, gate='full-gov311')
    preserved = carrier.preserve_closed_lease_stop(args, run, tmp_path)
    assert Path(preserved['manifest_path']).is_file() and not namespace.path.exists()
    assert 'fullGov311' in run.lease_records
    assert run.lease_stop['lease']['physical_fin_closed'] is True


def test_original_native_manager_bytes_remain_immutable():
    assert hashlib.sha256(lifecycle.DRIVER.read_bytes()).hexdigest() == lifecycle.DRIVER_SHA256


class DiagnosticApiFixture(ApiFixture):
    def __init__(self, monkeypatch, gate='capacity-probe'):
        super().__init__(monkeypatch, gate)
        self.documents[''].update(private=False)
        self.documents['/git/ref/heads/' + admission.DIAGNOSTIC_BRANCH] = {'object': {'sha': SHA}}
        self.documents['/git/commits/' + SHA]['parents'] = [{'sha': admission.RECOVERY_HEAD}]
        self.documents['/git/commits/' + admission.RECOVERY_HEAD] = {
            'sha': admission.RECOVERY_HEAD, 'tree': {'sha': admission.RECOVERY_TREE}}
        self.documents['/pulls/476']['head']['sha'] = admission.RECOVERY_HEAD
        self.documents['/pulls/476'].update(merged=False, merged_at=None)
        self.documents['/pulls/477'] = {'number': 477, 'state': 'open', 'draft': True, 'merged': False,
            'merged_at': None, 'user': {'login': 'mbalbo2023'},
            'head': {'sha': admission.GUARDS_HEAD, 'ref': 'governance/rc6-error-learning-runner-20261007',
                'repo': {'id': admission.REPO_ID}}, 'base': {'ref': admission.BRANCH}}
        self.documents['/actions/runs/71/attempts/1'].update(
            event='workflow_dispatch', head_branch=admission.DIAGNOSTIC_BRANCH)
        image, limit = (5*1024**3, 512*1024**2) if gate == 'capacity-probe' else (26*1024**3, 20*1024**3)
        scope = {'schema': 'porota.rc6.capacity-diagnostic-scope.v1', 'mode': gate,
            'backing_image_bytes': image, 'project_hard_limit_bytes': limit, 'residual_reserve_bytes': 4*1024**3,
            'financial_tick_allowed': False, 'qualification_claimed': False}
        prior = None if gate == 'capacity-probe' else {'run_id': 70, 'artifact_id': 123}
        body = '\n'.join(('RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION=APPROVED',
            'WORKSTREAM_ID=' + admission.WORKSTREAM, 'DIAGNOSTIC_MODE=' + gate,
            'SOURCE_WIP=' + admission.DIAGNOSTIC_BRANCH, 'SOURCE_SHA=' + SHA, 'SOURCE_TREE=' + TREE,
            'SESSION_SUCCESSOR=' + admission.SUCCESSOR_OWNER, 'WRITE_OWNER=' + admission.SUCCESSOR_OWNER,
            'INTEGRATION_OWNER=' + admission.SUCCESSOR_OWNER, 'DEPLOY_OWNER=NOT_ACQUIRED', 'RELEASED=false',
            'MODE=PRODUCTION_PAPER / SIMULATION', 'real_orders_sent=0', 'READ_CONTRACT_SHA256=' + 'c'*64,
            'SOURCE_MANIFEST_SHA256=' + 'd'*64,
            'CAPACITY_DIAGNOSTIC_SCOPE_JSON=' + lease.wire(scope).decode().strip(),
            'CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON=' + lease.wire(prior).decode().strip())) + '\n'
        self.launch = comment(471, 9001, body, '2026-10-08T18:43:00Z')
        self.documents['/issues/comments/9001'] = self.launch
        self.rows[471] = [self.launch if row['id'] == 9001 else row for row in self.rows[471]]
        self.authority = {'schema': 'porota.rc6.capacity-diagnostic-admission.v1',
            'status': 'ADMITTED_DIAGNOSTIC_NOT_STARTED', 'source_sha': SHA, 'source_tree': TREE,
            'owner_session': admission.SUCCESSOR_OWNER, 'gate': gate,
            'launch_receipt_url': self.launch['html_url'], 'launch_body_sha256': lease.digest(body.encode()),
            'source_manifest_sha256': 'd'*64, 'read_contract_sha256': 'c'*64,
            'scope': scope, 'capability_prerequisite': prior, 'qualification_claimed': False,
            'source': {'repository_public_verified': True,
                'anonymous_source_origin': 'https://github.com/' + admission.REPO + '.git'}}


def diagnostic_monitor(tmp_path, monkeypatch, gate='capacity-probe'):
    tmp_path.chmod(0o700)
    api, clock = DiagnosticApiFixture(monkeypatch, gate), Clock()
    instance = lease.GateLeaseMonitor(tmp_path, source_sha=SHA, source_tree=TREE,
        owner_session=admission.SUCCESSOR_OWNER, gate=gate, launch_receipt_url=api.launch['html_url'],
        run_id=RUN, run_attempt=1, get=api.get, clock=clock.wall, monotonic=clock.mono,
        diagnostic_binding=admission.diagnostic_control_binding(api.authority))
    return instance, api, clock


@pytest.mark.parametrize('gate', admission.DIAGNOSTIC_GATES)
def test_diagnostic_live_scope_and_historical_replay_are_api_only(tmp_path, monkeypatch, gate):
    instance, api, clock = diagnostic_monitor(tmp_path, monkeypatch, gate)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Source/Git/event/workspace admission while Native is active')
    monkeypatch.setattr(admission, 'actual_event', forbidden)
    monkeypatch.setattr(admission, 'frozen_source_inventory', forbidden)
    monkeypatch.setattr(admission, 'admit_diagnostic', forbidden)
    monkeypatch.setattr(admission.subprocess, 'run', forbidden)
    instance.prelaunch('quota-diagnosis')
    instance.progress('started', 123, clock.mono(), clock.mono()+10800, 0)
    clock.advance(55)
    instance.progress('poll', 123, 1000, instance.native_deadline, 0)
    receipt = instance.after_fin(kernel())
    clock.advance(86400)
    result = replay(instance, receipt, kernel())
    assert result['historical_launch_and_lease_union_verified'] is True
    assert result['past_lease_compared_to_current_time'] is False
    assert '/pulls/476' in api.calls and '/pulls/477' in api.calls
    assert admission.fresh_diagnostic_source(SHA, TREE, get=lambda path: api.get(path, time.monotonic()+4))[
        'repository_public_verified'] is True


@pytest.mark.parametrize('fault', ['wip', '476', '477', 'private', 'auth_edit', 'contract',
    'manifest', 'foreign', 'run_cancelled', 'rerun', 'ops'])
def test_diagnostic_control_change_stops_and_cannot_credit_fin_as_green(tmp_path, monkeypatch, fault):
    instance, api, clock = diagnostic_monitor(tmp_path, monkeypatch)
    instance.prelaunch('quota-diagnosis')
    instance.progress('started', 123, clock.mono(), clock.mono()+10800, 0)
    if fault == 'wip':
        api.documents['/git/ref/heads/' + admission.DIAGNOSTIC_BRANCH]['object']['sha'] = 'e'*40
    elif fault in ('476', '477'):
        api.documents['/pulls/' + fault]['head']['sha'] = 'e'*40
    elif fault == 'private':
        api.documents['']['private'] = True
    elif fault == 'auth_edit':
        api.launch['updated_at'] = lease.text_stamp(clock.wall())
    elif fault in ('contract', 'manifest'):
        api.launch['body'] = api.launch['body'].replace(('c' if fault == 'contract' else 'd')*64, 'e'*64)
    elif fault == 'foreign':
        api.renew(clock.wall(), owner='FOREIGN_OWNER')
    elif fault == 'ops':
        key = '/contents/'+admission.OPS+'?ref='+admission.OPS_REF.replace('/', '%2F')
        api.documents[key]['sha'] = 'e'*40
    else:
        run = api.documents['/actions/runs/71/attempts/1']
        run['status' if fault == 'run_cancelled' else 'run_attempt'] = 'completed' if fault == 'run_cancelled' else 2
    clock.advance(55)
    with pytest.raises(lease.LeaseControlFailure):
        instance.progress('poll', 123, 1000, instance.native_deadline, 0)
    readmitted = []
    receipt = instance.after_fin(kernel(), readmit=lambda: readmitted.append('after-physical-FIN'))
    assert readmitted == ['after-physical-FIN']
    assert receipt['status'] == 'RED' and receipt['physical_fin_closed'] is True
    assert receipt['post_fin_readmission_verified'] is False


def test_diagnostic_nonroot_issuer_guard_precedes_any_admission_read(monkeypatch):
    monkeypatch.setattr(lease.os, 'getuid', lambda: 0)
    monkeypatch.setattr(lease.os, 'geteuid', lambda: 0)
    with pytest.raises(lease.LeaseControlFailure, match='NONROOT_ISSUER_REQUIRED'):
        lease._require_issuer_actor()


def test_diagnostic_issuer_full_source_barriers_and_control_refs_survive_fin_red(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    api, clock = DiagnosticApiFixture(monkeypatch), Clock()
    admission_file = tmp_path/'admission.json'
    lease._private_write(admission_file, lease.wire(api.authority))
    output = tmp_path/'diagnostic'; output.mkdir(mode=0o700)
    args = SimpleNamespace(source_sha=SHA, source_tree=TREE, mode='capability',
        source_root=admission.ROOT, output=output)
    full_reads = []
    monkeypatch.setattr(lease, '_require_issuer_actor', lambda: None)  # Unit transport, no privileged launch.
    original = lease.GateLeaseMonitor
    monkeypatch.setattr(lease, 'GateLeaseMonitor', lambda root, **kwargs: original(root, **kwargs,
        run_id=RUN, run_attempt=1, get=api.get, clock=clock.wall, monotonic=clock.mono))
    monkeypatch.setenv('RC6_CALIBRATION_ADMISSION_JSON', str(admission_file))
    monkeypatch.setattr(admission, 'admit_diagnostic', lambda **_kwargs: full_reads.append('full-Source') or copy.deepcopy(api.authority))
    issuer = lease.issuer_factory(args, control_root=output/'owned-lease-controls')
    issuer.before_launch('quota-diagnosis')
    issuer('started', 123, clock.mono(), clock.mono()+10800, 0)
    assert full_reads == ['full-Source', 'full-Source']
    with pytest.raises(lease.LeaseControlFailure, match='ACTIVE_NATIVE_CONTROL_READ_FORBIDDEN'):
        issuer.evidence_refs
    api.documents['/pulls/477']['head']['sha'] = 'e'*40
    clock.advance(55)
    with pytest.raises(lease.LeaseControlFailure):
        issuer('poll', 123, 1000, issuer.monitor.native_deadline, 0)
    with pytest.raises(lease.LeaseControlFailure, match='CONTROL_RED_AFTER_ACTUAL_FIN'):
        issuer.after_fin(kernel())
    assert full_reads == ['full-Source']*3 and issuer.receipt['status'] == 'RED'
    assert issuer.physical_fin_closed is True and issuer.evidence_refs
    assert (output/'owned-lease-controls/owner-quota-diagnosis.json').is_file()


def not_started_terminal():
    api_scope = {'backing_image_bytes': 5*1024**3, 'project_hard_limit_bytes': 512*1024**2,
        'residual_reserve_bytes': 4*1024**3}
    authority = {'gate': 'capacity-probe', 'source_sha': SHA, 'source_tree': TREE,
        'read_contract_sha256': 'c'*64, 'scope': api_scope}
    binding = {'candidate_sha': SHA, 'candidate_tree': TREE}
    prerequisites = {'schema': 'porota.rc6.readonly-kernel-quota-prerequisites.v1', 'status': 'NO_VERIFICADO',
        'actual_capability_proved': False, 'qualification_claimed': False, 'module_loading_attempted': False}
    raw = lease.wire(prerequisites)
    terminal = {'schema': 'porota.rc6.capacity-calibration.v1', 'mode': 'capability',
        'source_sha': SHA, 'source_tree': TREE, 'read_contract_sha256': 'c'*64, 'binding': binding,
        'limits': api_scope, 'status': 'BLOQUEADO', 'generation': 'NOT_STARTED', 'inner_namespace_created': False,
        'inner_generation_operations': {'create_namespace': 'NOT_CALLED', 'image_allocation': 'NOT_CALLED',
            'root_worker_launch': 'NOT_CALLED'},
        'actual_capability_proved': False, 'qualification_claimed': False, 'G0_G8_qualification_claimed': False,
        'source_unchanged': True, 'payload_upload_safe': True, 'real_orders_sent': 0, 'real_routes': 'NOT_CALLED',
        'reason':'CALIBRATION_PRIVILEGED_SIGNAL_CUSTODY_UNVERIFIED',
        'privileged_signal_custody':{'status':'NO_VERIFICADO','proved':False,'privileged_launch_authorized':False},
        'ppi_watch': 'UNTOUCHED', 'DEPLOY_OWNER': 'NOT_ACQUIRED', 'issuer_lease': None, 'required_raw': [],
        'kernel_prerequisites': prerequisites, 'kernel_prerequisites_ref': {'path': 'kernel-prerequisites.json',
            'sha256': lease.digest(raw)},
        'cleanup': {'status': 'NOT_STARTED', 'namespace_removed': False, 'actual_inner_FIN_claimed': False}}
    outer = kernel(); outer.update(launcher_management_deadline_seconds=600, owned_cleanup_management_bound_seconds=5)
    return terminal, authority, binding, outer, raw


def test_authentic_early_not_started_preserves_outer_fin_without_fabricating_inner_cleanup():
    terminal, authority, binding, outer, raw = not_started_terminal()
    result = carrier.validate_diagnostic_terminal(terminal, authority, binding, outer, prerequisite_raw=raw)
    assert result['actual_outer_fin_closed'] is True and result['actual_inner_fin_claimed'] is False
    assert result['inner_cleanup_claimed'] is False and result['qualification_claimed'] is False


def test_present_kernel_prerequisite_metadata_still_cannot_authorize_privileged_launch():
    terminal, authority, binding, outer, _raw = not_started_terminal()
    terminal['kernel_prerequisites']['status'] = 'PREREQUISITES_PRESENT'
    raw = lease.wire(terminal['kernel_prerequisites'])
    terminal['kernel_prerequisites_ref']['sha256'] = lease.digest(raw)
    result = carrier.validate_diagnostic_terminal(terminal,authority,binding,outer,prerequisite_raw=raw)
    assert result['generation']=='NOT_STARTED' and result['actual_inner_fin_claimed'] is False
    assert result['qualification_claimed'] is False


@pytest.mark.parametrize('fault',['missing','proved','authorized','green','boolean_alias','reason'])
def test_prerequisite_metadata_cannot_bypass_unproved_privileged_signal_custody(fault):
    terminal, authority, binding, outer, _raw = not_started_terminal()
    terminal['kernel_prerequisites']['status'] = 'PREREQUISITES_PRESENT'
    raw = lease.wire(terminal['kernel_prerequisites'])
    terminal['kernel_prerequisites_ref']['sha256'] = lease.digest(raw)
    if fault=='missing': del terminal['privileged_signal_custody']
    elif fault=='reason': terminal['reason']='CLAIMED_SAFE'
    elif fault=='proved': terminal['privileged_signal_custody']['proved']=True
    elif fault=='authorized': terminal['privileged_signal_custody']['privileged_launch_authorized']=True
    elif fault=='green': terminal['privileged_signal_custody']['status']='GREEN'
    else: terminal['privileged_signal_custody']['proved']=0
    with pytest.raises(ValueError,match='NOT_STARTED_CANNOT_CLAIM'):
        carrier.validate_diagnostic_terminal(terminal,authority,binding,outer,prerequisite_raw=raw)


@pytest.mark.parametrize('fault', ['inner_created', 'cleanup', 'inner_fin', 'source', 'preflight_green',
    'raw_tamper', 'required_raw', 'issuer_fin', 'outer_unknown', 'generation', 'preserved_loop'])
def test_early_or_partial_diagnostic_cannot_fake_started_custody(fault):
    terminal, authority, binding, outer, raw = not_started_terminal()
    if fault == 'inner_created': terminal['inner_namespace_created'] = True
    elif fault == 'cleanup': terminal['cleanup']['namespace_removed'] = True
    elif fault == 'inner_fin': terminal['kernel'] = kernel()
    elif fault == 'source': terminal['source_tree'] = 'e'*40
    elif fault == 'preflight_green': terminal['kernel_prerequisites']['status'] = 'GREEN'
    elif fault == 'raw_tamper': raw += b' '
    elif fault == 'required_raw': terminal['required_raw'] = [{'path': 'fake.raw'}]
    elif fault == 'issuer_fin': terminal['issuer_lease'] = {'status': 'GREEN'}
    elif fault == 'outer_unknown': outer['owned_children_exhaustion_verified'] = False
    elif fault == 'generation': terminal['generation'] = 'STARTED'
    else: terminal['preserved_owned_namespace'] = '/unverified-loop'
    with pytest.raises(ValueError, match='DIAGNOSTIC_'):
        carrier.validate_diagnostic_terminal(terminal, authority, binding, outer, prerequisite_raw=raw)


def test_public_diagnostic_git_has_no_credential_forwarding_or_private_fallback():
    environment = carrier.diagnostic_issuer_environment('/owned/admission.json', {'GH_TOKEN': 'unit-secret',
        'GITHUB_TOKEN': 'second-secret', 'RC6_CALIBRATION_READONLY_GIT_TOKEN': 'forbidden',
        'RC6_READONLY_GIT_TOKEN': 'forbidden', 'GITHUB_RUN_ID': '71', 'RUNNER_TOOL_CACHE': '/pinned',
        'GIT_ASKPASS': '/forbidden'})
    assert environment['GH_TOKEN'] == 'unit-secret' and 'GITHUB_TOKEN' not in environment
    assert 'RC6_CALIBRATION_READONLY_GIT_TOKEN' not in environment and 'RC6_READONLY_GIT_TOKEN' not in environment
    assert 'GIT_ASKPASS' not in environment and environment['GIT_TERMINAL_PROMPT'] == '0'


@pytest.mark.parametrize('label',['project_id_change','high32_project_id_change','high32_setflags','high32_prctl'])
def test_native_errno_true_is_not_the_integer_eperm(label):
    import errno
    checks = {key:{'errno':errno.EPERM,'denied':True} for key in
        ('project_id_change','inheritance_clear','setflags','quota_mutation')}
    checks.update({key:{'return':-1,'errno':errno.EPERM,'denied':True} for key in
        ('high32_project_id_change','high32_setflags','high32_prctl')})
    checks[label]['errno'] = True
    with pytest.raises(ValueError,match='REAL_EPERM'):
        gates.verify_capacity_probe_observations({'actual_positive':True,'same_uid':1001,
            'checks':checks,'source_financial_code_called':False,'project_attributes_unchanged':True},{})


def synthetic_inner_capacity_controls(tmp_path, monkeypatch):
    """Byte-replay fixture only: no loop, mount, quota or capability is executed."""
    import struct
    from scripts import rc6_capacity_calibration as calibration
    instance, api, clock = diagnostic_monitor(tmp_path, monkeypatch)
    instance.prelaunch('quota-diagnosis')
    instance.progress('started', 123, clock.mono(), clock.mono()+300, 0)
    actual = kernel(); actual.update(launcher_management_deadline_seconds=300, owned_cleanup_management_bound_seconds=5)
    issuer = instance.after_fin(actual)
    binding = {'candidate_sha': SHA, 'candidate_tree': TREE, 'attempt_id': '71:1'}
    nonce = '0'*32; limits = {key: api.authority['scope'][key] for key in
        ('backing_image_bytes','project_hard_limit_bytes','residual_reserve_bytes')}
    image = {'st_dev': 10, 'st_ino': 11, 'st_uid': 1001, 'st_gid': 1001, 'st_mode': 0o100600,
        'st_nlink': 1, 'st_size': limits['backing_image_bytes'], 'st_blocks': limits['backing_image_bytes']//512}
    claim = {'namespace_nonce': nonce, 'mount_id': 50, 'marker_sha256': 'e'*64}
    origin = {'image_identity': image, 'original_mount_id': 50, 'namespace_nonce': nonce, 'marker_sha256': 'e'*64}
    loop = {'number': 7, 'backing_device': 10, 'backing_inode': 11, 'autoclear': True, 'backing_origin': origin}
    request = {'source_sha': SHA, 'source_tree': TREE, 'binding': binding, 'limits': limits,
        'code_hashes': {'unit-only': 'f'*64}, 'namespace_receipt': claim, 'image_identity': image,
        'mount_namespace_inode': 99, 'issuer': {'boot_id': 'unit-boot'}, 'mountpoint': '/owned/mount'}
    request_raw = lease.wire(request); request_sha = lease.digest(request_raw)
    birth = {'schema': 'porota.rc6.authenticated-loop-birth.v1', 'binding': binding, 'namespace_nonce': nonce,
        'source_sha': SHA, 'source_tree': TREE, 'request_sha256': request_sha, 'image_identity': image,
        'loop': loop, 'original_mount_id': 50, 'issuer_mount_namespace_inode': 99, 'actor_mount_namespace_inode': 100,
        'actor_pid': 12300, 'actor_start_ticks': '1234', 'boot_id': 'unit-boot',
        'before_readonly_and_privilege_drop': True, 'qualification_claimed': False}
    birth_raw = lease.wire(birth); birth_identity = {'st_ino': 123, 'st_uid': 1001}
    def quota(project, size):
        row = {'project_id': project, 'hard_bytes': size, 'used_bytes': 0, 'hard_inodes': 100000,
            'used_inodes': 0, 'kernel_readback': True}
        for key, name, number in (('native_operation','Q_GETQUOTA',0x800007),('set_native_operation','Q_SETQUOTA',0x800008)):
            row[key] = {'operation': 'quotactl/'+name, 'return': 0, 'errno': 0, 'quota_type': 2,
                'command': (number<<8)|2, 'device': '/dev/loop7', 'project_id': project,
                'actual_mount_namespace_inode': 100}
        return row
    quotas = [quota(1, limits['project_hard_limit_bytes']), quota(2, 1024**2)]
    live = {**quotas[0], 'measurement_method': 'KERNEL_PROJINHERIT_STATFS_PROJECTION',
        'root_initial_readback_unchanged': True,
        'live_projection': {'total_bytes': limits['project_hard_limit_bytes'], 'total_inodes': 100000}}
    physical = {'path': '/owned/mount', 'total_bytes': limits['backing_image_bytes'],
        'available_bytes': limits['backing_image_bytes']-1024**2, 'free_inodes': 100000,
        'total_inodes': 200000, 'fragment_bytes': 4096}
    raw_super = bytearray(1024)
    for offset, value in ((0, 8192),(0x18,2),(0x64,0x2100),(0x240,3),(0x244,4),(0x26C,11)):
        struct.pack_into('<I',raw_super,offset,value)
    struct.pack_into('<H',raw_super,0x38,0xEF53)
    superblock = calibration.decode_ext4_superblock(bytes(raw_super))
    superblock['image_identity_after_nodiscard_mkfs'] = image
    worker = {'loop': loop, 'backing_origin': origin, 'loop_birth_control': {'sha256': lease.digest(birth_raw),
        'identity': birth_identity}, 'actual_own_fin_closed': True, 'required_raw_complete': True,
        'quotas': quotas, 'ext4_superblock': superblock,
        'readonly_operation': {'operation':'mount_setattr','syscall_number':442,'return':0,'errno':0,
            'flags':0x8000,'path':'/','dirfd':-100,'attributes':{'set':1,'clear':0,'propagation':0,'userns_fd':0},
            'attribute_size':32,'actual_mount_namespace_inode':100,'issuer_mount_namespace_inode':99,'backing_origin':origin},
        'mount_operation': {'operation':'mount','return':0,'errno':0,'filesystem_type':'ext4','flags':6,
            'options':'prjquota','device':'/dev/loop7','target':'/owned/mount','actual_mount_namespace_inode':100,
            'backing_origin':origin},
        'quota_format': {'operation':'quotactl/Q_GETFMT','return':0,'errno':0,'format_id':4,'quota_type':2,
            'device':'/dev/loop7','project_id':0,'command':(0x800004<<8)|2,'actual_mount_namespace_inode':100},
        'commands': [{'capacity_before': {'physical': physical, 'project_quota': live}}]}
    controls = {'root-request.json': request_raw, 'loop-birth.json': birth_raw,
        'producer-owned-fin-quota-diagnosis.json': lease.wire({'schema':'porota.rc6.generated-fixture-owned-fin.v1',
            'kernel':actual,'binding':binding,'namespace_nonce':nonce,'manager_sha256':lifecycle.DRIVER_SHA256,
            'actual_owned_fin_closed':True,'phase_green':True,'global_or_other_producer_FIN_claimed':False}),
        'producer-native.log': lease.wire({'schema':'porota.rc6.capacity-worker.v1','request_sha256':request_sha,
            'report':{**worker,'raw_files':[]}})}
    files, preserved = [], {}
    for index, (name, raw) in enumerate(controls.items()):
        member = str(index).zfill(4)+'.raw'
        files.append({'relative_source':name,'capture_file':member,'bytes':len(raw),
            'sha256':lease.digest(raw),'source_identity':birth_identity if name=='loop-birth.json' else {}})
        preserved['diagnostic/sealed-controls/'+member] = raw
    manifest_raw = lease.wire({'schema':'porota.rc6.generated-fixture-required-capture.v1', 'binding':binding,
        'namespace_nonce':nonce,'actual_owned_fin_closed':True,'phase_green':True,
        'kernel_sha256':lease.digest(lease.wire(actual)),'files':files})
    preserved['diagnostic/sealed-controls/manifest.json'] = manifest_raw
    preserved['diagnostic/owned-lease-controls/owner-quota-diagnosis.json'] = lease.wire(issuer)
    preserved.update({'diagnostic/owned-lease-controls/'+lease.OBJECT_PREFIX+key+'.json':raw
        for key,raw in instance.objects.items()})
    for stage in ('prelaunch','post-fin'):
        preserved['diagnostic/owned-lease-controls/'+stage+'-full-admission.json'] = lease.wire(api.authority)
    receipt = {'generation':'STARTED','inner_namespace_created':True,'actual_capability_proved':True,
        'capture_manifest_sha256':lease.digest(manifest_raw),'kernel':actual,'root_request_sha256':request_sha,
        'cleanup':{'binding':binding,'namespace_removed':True,'actual_owned_fin_closed':True,'phase_green':True,
            'capture_manifest_sha256':lease.digest(manifest_raw),'foreign_paths_removed':0,'runtime_paths_authorized':False},
        'source_sha':SHA,'source_tree':TREE,'binding':binding,'limits':limits,'code_hashes':request['code_hashes'],
        'loop_birth_sha256':lease.digest(birth_raw),'loop_finalization':{'original_autoclear_loop_absent':True,
            'global_loop_cleanup_attempted':False},'worker':worker,'issuer_lease':issuer,
        'issuer_evidence_refs':[{'path':name.removeprefix('diagnostic/'),'sha256':lease.digest(raw),'bytes':len(raw)}
            for name,raw in preserved.items() if name.startswith('diagnostic/owned-lease-controls/')]}
    return receipt, preserved


def test_capacity_inner_byte_replay_binds_controls_and_never_claims_actual_quota_execution(tmp_path,monkeypatch):
    receipt, preserved = synthetic_inner_capacity_controls(tmp_path,monkeypatch)
    result = gates.verify_capacity_inner_controls(receipt,preserved)
    assert result == {'original_inner_controls_replayed':True,'qualification_claimed':False}


def test_diagnostic_api_stop_uses_original_tiny_native_fin_and_releases_no_unknown_custody(tmp_path,monkeypatch):
    tmp_path.chmod(0o700)
    api = DiagnosticApiFixture(monkeypatch)
    entered = time.monotonic()
    instance = lease.GateLeaseMonitor(tmp_path,source_sha=SHA,source_tree=TREE,
        owner_session=admission.SUCCESSOR_OWNER,gate='capacity-probe',launch_receipt_url=api.launch['html_url'],
        run_id=RUN,run_attempt=1,get=api.get,clock=lambda:START+timedelta(seconds=time.monotonic()-entered),
        diagnostic_binding=admission.diagnostic_control_binding(api.authority))
    instance.prelaunch('quota-diagnosis')
    def progress(stage,pid,start,deadline,fd):
        if stage=='started':
            api.documents['/pulls/477']['head']['sha']='e'*40
            instance.last_api_poll-=55
        instance.progress(stage,pid,start,deadline,fd)
    actual=native.managed_native_child([sys.executable,'-I','-B','-c','import time;time.sleep(30)'],
        tmp_path,tmp_path/'diagnostic-tiny.log',dict(os.environ),60,progress=progress)
    assert native.managed_custody_closed(actual) is True and native.managed_phase_green(actual) is False
    assert actual['owned_cleanup_management_bound_seconds']==5 and actual['remaining_owned_children']==[]
    assert actual['process_group_absent_after_reap'] is True and actual['owned_children_exhaustion_verified'] is True
    assert actual['owned_group_signal_observations'][0]['signal']==signal.SIGTERM
    source_barriers=[]
    result=instance.after_fin(actual,readmit=lambda:source_barriers.append('AFTER_ACTUAL_FIN'))
    assert source_barriers==['AFTER_ACTUAL_FIN'] and result['status']=='RED'
    assert result['physical_fin_closed'] is True and result['post_fin_readmission_verified'] is False


@pytest.mark.parametrize('fault', ['not_started','manifest','root_request','birth','root_fin','worker','readonly',
    'mount','quota_format','superblock','project_quota','physical_reserve','issuer_raw','issuer_object'])
def test_capacity_inner_boolean_green_cannot_replace_original_native_raw(tmp_path,monkeypatch,fault):
    receipt, preserved = synthetic_inner_capacity_controls(tmp_path,monkeypatch)
    if fault == 'not_started': receipt['generation'] = 'NOT_STARTED'
    elif fault == 'manifest': preserved['diagnostic/sealed-controls/manifest.json'] += b' '
    elif fault in ('root_request','birth','root_fin','worker'):
        index = {'root_request':0,'birth':1,'root_fin':2,'worker':3}[fault]
        preserved['diagnostic/sealed-controls/'+str(index).zfill(4)+'.raw'] += b' '
    elif fault == 'readonly': receipt['worker']['readonly_operation']['errno'] = 1
    elif fault == 'mount': receipt['worker']['mount_operation']['flags'] = 0
    elif fault == 'quota_format': receipt['worker']['quota_format']['format_id'] = 0
    elif fault == 'superblock': receipt['worker']['ext4_superblock']['raw_base64'] = ''
    elif fault == 'project_quota': receipt['worker']['quotas'][0]['native_operation']['return'] = -1
    elif fault == 'physical_reserve': receipt['worker']['commands'][0]['capacity_before']['physical']['available_bytes'] = 0
    elif fault == 'issuer_raw': preserved['diagnostic/owned-lease-controls/owner-quota-diagnosis.json'] += b' '
    else:
        key = next(key for key in preserved if '/owner-object-' in key)
        preserved[key] += b' '
    # These counterexamples supply coherent transport hashes; the native
    # operation/bound predicate itself must reject the false observation.
    if fault in ('readonly','mount','quota_format','superblock','project_quota','physical_reserve'):
        native_raw = lease.wire({'schema':'porota.rc6.capacity-worker.v1',
            'request_sha256':receipt['root_request_sha256'],'report':{**receipt['worker'],'raw_files':[]}})
        preserved['diagnostic/sealed-controls/0003.raw'] = native_raw
        manifest = admission.document(preserved['diagnostic/sealed-controls/manifest.json'])
        manifest['files'][3].update(bytes=len(native_raw),sha256=lease.digest(native_raw))
        preserved['diagnostic/sealed-controls/manifest.json'] = lease.wire(manifest)
        receipt['capture_manifest_sha256'] = lease.digest(lease.wire(manifest))
        receipt['cleanup']['capture_manifest_sha256'] = receipt['capture_manifest_sha256']
    with pytest.raises((ValueError,lease.LeaseControlFailure),match='CAPABILITY_|OWNED_LEASE_'):
        gates.verify_capacity_inner_controls(receipt,preserved)

"""Cheap authenticated transport counterexamples; no producer or provider IO."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import rc6_material_pr_admission as admission
from scripts import rc6_material_carrier as carrier
from scripts import rc6_owned_gate_lease as lease
from tests.test_rc6_owned_gate_lease import ApiFixture, SHA, TREE, RUN, comment, owner_comment


NOW = datetime(2026, 10, 9, 18, 10, tzinfo=timezone.utc)
CANDIDATE_PR = 801  # A synthetic future PR, deliberately unrelated to #476.
CANDIDATE_BRANCH = 'integration/rc6-architecture-closure-20261009'
BASE_TREE = 'c' * 40


class CandidateApi(ApiFixture):
    """Real predicates read locally generated, hash-authenticated documents."""
    def __init__(self, monkeypatch, gate='predeploy'):
        super().__init__(monkeypatch, gate=gate)
        predecessor = '\n'.join(('WRITE_OWNER=' + admission.SUCCESSOR_OWNER,
            'INTEGRATION_OWNER=' + admission.SUCCESSOR_OWNER, 'RELEASED=false', 'DEPLOY_OWNER=NOT_ACQUIRED')) + '\n'
        monkeypatch.setattr(admission, 'PREDECESSOR_BODY_SHA256', lease.digest(predecessor.encode()))
        transfer_fields = {'WORKSTREAM_ID': admission.WORKSTREAM, 'SESSION_SUCCESSOR': admission.CLOSURE_OWNER,
            'WRITE_OWNER': admission.CLOSURE_OWNER, 'INTEGRATION_OWNER': admission.CLOSURE_OWNER,
            'DEPLOY_OWNER': 'NOT_ACQUIRED', 'RELEASED': 'false', 'BRANCH': CANDIDATE_BRANCH,
            'SUCCESSION_KIND': 'ADMINISTRATIVE_EXPLICIT_USER_AUTHORIZATION',
            'PREDECESSOR_OWNER': admission.SUCCESSOR_OWNER, 'PREDECESSOR_RELEASED': 'false',
            'RECONCILIATION_USER_STOP_CONFIRMED': 'true', 'HEAVY_GATES_AUTHORIZED': 'false',
            'G0_G8_QUALIFICATION': 'false', 'FINAL_CANDIDATE_ELIGIBLE': 'false',
            'MODE': 'PRODUCTION_PAPER / SIMULATION', 'real_orders_sent': '0',
            **{'PREDECESSOR_RECEIPT_' + str(issue): str(admission.PREDECESSOR_ANCHORS[issue][0])
               for issue in (471, 473)}}
        transfer = '\n'.join(key + '=' + value for key, value in transfer_fields.items()) + '\n' + \
            admission.EXPLICIT_SINGLE_WRITER_LITERAL + '\n' + admission.RECONCILED_BLOB + '\n' + \
            admission.RECONCILED_RAW_SHA256 + '\n'
        monkeypatch.setattr(admission, 'CLOSURE_BODY_SHA256', lease.digest(transfer.encode()))
        self.transfers = {}
        for issue in (471, 473):
            old_id, old_created = admission.PREDECESSOR_ANCHORS[issue]
            self.documents['/issues/comments/' + str(old_id)] = comment(issue, old_id, predecessor, old_created)
            identifier, created = admission.CLOSURE_ANCHORS[issue]
            row = comment(issue, identifier, transfer, created)
            self.documents['/issues/comments/' + str(identifier)] = row
            self.transfers[issue] = row
            self.rows[issue] = [row]
        launch_fields = {'AUTHORIZATION_SCHEMA': admission.CANDIDATE_AUTHORIZATION_SCHEMA,
            'RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION': 'APPROVED', 'WORKSTREAM_ID': admission.WORKSTREAM,
            'SOURCE_SHA': SHA, 'SOURCE_TREE': TREE, 'SOURCE_PR': str(CANDIDATE_PR),
            'SOURCE_BRANCH': CANDIDATE_BRANCH, 'BASE_BRANCH': admission.BASE,
            'BASE_SHA': admission.BASE_SHA, 'BASE_TREE': BASE_TREE,
            'SESSION_SUCCESSOR': admission.CLOSURE_OWNER, 'WRITE_OWNER': admission.CLOSURE_OWNER,
            'INTEGRATION_OWNER': admission.CLOSURE_OWNER, 'DEPLOY_OWNER': 'NOT_ACQUIRED', 'RELEASED': 'false',
            'MODE': 'PRODUCTION_PAPER / SIMULATION', 'real_orders_sent': '0', 'GATES_AUTHORIZED': gate,
            'CAUSE_EVIDENCE_URL': self.documents['/issues/comments/9002']['html_url'],
            # V1 authentic measured admission remains distinct from verified
            # V2 costs and each actual producer's mandatory live preflight.
            'CAPACITY_PEAKS_JSON': '{"bootstrap":{"schema":"porota.rc6.comparable-capacity-peak.v1"}}',
            'PREREQUISITES_MANIFEST_JSON': json.dumps({'source_sha': SHA, 'source_tree': TREE}),
            'RC6_PREDEPLOY_G7_AUTHORIZATION': 'APPROVED'}
        for issue in (471, 473):
            launch_fields['OWNER_RECEIPT_' + str(issue)] = self.transfers[issue]['html_url']
            launch_fields['OWNER_RECEIPT_' + str(issue) + '_SHA256'] = lease.digest(transfer.encode())
        self.launch['body'] = '\n'.join(key + '=' + value for key, value in launch_fields.items()) + '\n'
        self.launch['created_at'] = self.launch['updated_at'] = lease.text_stamp(NOW - timedelta(minutes=5))
        self.rows[471].append(self.launch)
        self.documents[''].update(private=False)
        self.documents['/git/ref/heads/' + CANDIDATE_BRANCH] = {'object': {'sha': SHA}}
        self.documents['/git/ref/heads/' + admission.BASE] = {'object': {'sha': admission.BASE_SHA}}
        self.documents['/git/commits/' + admission.BASE_SHA] = {'sha': admission.BASE_SHA, 'tree': {'sha': BASE_TREE}}
        pr = copy.deepcopy(self.documents['/pulls/476'])
        pr.update(number=CANDIDATE_PR, draft=(gate != 'predeploy'), updated_at=lease.text_stamp(NOW - timedelta(minutes=4)))
        pr['head']['ref'] = CANDIDATE_BRANCH
        self.documents['/pulls/' + str(CANDIDATE_PR)] = pr
        self.rows[471].append(owner_comment(471, 100471, NOW - timedelta(seconds=30), NOW + timedelta(minutes=19),
                                           owner=admission.CLOSURE_OWNER))
        self.rows[473].append(owner_comment(473, 100473, NOW - timedelta(seconds=30), NOW + timedelta(minutes=19),
                                           owner=admission.CLOSURE_OWNER))
        self.documents['/actions/runs/71/attempts/1']['path'] = '.github/workflows/porota-predeploy-v2.yml'

    def read(self, path):
        self.calls.append(path)
        if path.startswith('/issues/471/comments?'):
            return copy.deepcopy(self.rows[471])
        if path.startswith('/issues/473/comments?'):
            return copy.deepcopy(self.rows[473])
        if path.startswith('/actions/workflows/') and '/runs?' in path:
            return {'total_count': 1, 'workflow_runs': [{'id': RUN, 'head_sha': SHA}]}
        return copy.deepcopy(self.documents[path])


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    api = CandidateApi(monkeypatch)
    event = {'repository': {'id': admission.REPO_ID, 'full_name': admission.REPO},
        'number': CANDIDATE_PR, 'pull_request': copy.deepcopy(api.documents['/pulls/' + str(CANDIDATE_PR)]),
        'sender': {'login': 'mbalbo2023'}, 'action': 'ready_for_review'}
    path = tmp_path / 'event.json'
    path.write_text(json.dumps(event))
    for key, value in {'GITHUB_REPOSITORY': admission.REPO, 'GITHUB_REPOSITORY_ID': str(admission.REPO_ID),
        'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_ID': str(RUN), 'GITHUB_EVENT_NAME': 'pull_request',
        'GITHUB_EVENT_PATH': str(path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(admission, 'api', api.read)
    return api, event, path


def admit(candidate):
    api, _event, _path = candidate
    return admission.admit(source_sha=SHA, source_tree=TREE, gate='predeploy', now=NOW,
        owner_session=admission.CLOSURE_OWNER, launch_receipt_url=api.launch['html_url'])


def test_exact_dynamic_pr_uses_authentic_current_owner_without_granting_g7_or_deploy(candidate):
    result = admit(candidate)
    assert result['status'] == 'ADMITTED_NATIVE_NOT_STARTED'
    assert result['candidate_scope']['pr'] == CANDIDATE_PR
    assert result['candidate_scope']['branch'] == CANDIDATE_BRANCH
    assert result['final_candidate_eligible'] is False and result['material_gates'] == []
    assert result['DEPLOY_OWNER'] == 'NOT_ACQUIRED' and result['real_orders_sent'] == 0
    assert result['ownership_transfer']['predecessor_released_claimed'] is False
    assert result['ownership_transfer']['heavy_authority_from_transfer_claimed'] is False
    assert '/pulls/476' not in candidate[0].calls


@pytest.mark.parametrize('fault', ['fork', 'sender', 'author', 'auto_push'])
def test_untrusted_or_push_event_fails_before_api_reads(candidate, fault):
    api, event, path = candidate
    if fault == 'fork':
        event['pull_request']['head']['repo']['id'] = 42
    elif fault == 'sender':
        event['sender']['login'] = 'another-writer'
    elif fault == 'author':
        event['pull_request']['user']['login'] = 'another-writer'
    else:
        event['action'] = 'synchronize'
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError):
        admit(candidate)
    assert api.calls == []


@pytest.mark.parametrize('fault', ['head', 'tree', 'branch', 'base', 'base_tree', 'event_pr',
    'edited_launch', 'missing_g7', 'wrong_gate', 'wrong_pr', 'owner_hash', 'transfer_edit',
    'predecessor_edit', 'expired_owner', 'edited_owner', 'expired_foreign', 'old_owner'])
def test_unverified_candidate_never_reaches_capacity_or_heavy_work(candidate, monkeypatch, fault):
    api, event, path = candidate
    pr = api.documents['/pulls/' + str(CANDIDATE_PR)]
    if fault == 'head':
        pr['head']['sha'] = 'd' * 40
    elif fault == 'tree':
        api.documents['/git/commits/' + SHA]['tree']['sha'] = 'd' * 40
    elif fault == 'branch':
        pr['head']['ref'] = 'another-branch'
    elif fault == 'base':
        api.documents['/git/ref/heads/' + admission.BASE]['object']['sha'] = 'd' * 40
    elif fault == 'base_tree':
        api.documents['/git/commits/' + admission.BASE_SHA]['tree']['sha'] = 'd' * 40
    elif fault == 'event_pr':
        event['number'] += 1
        event['pull_request']['number'] += 1
        path.write_text(json.dumps(event))
    elif fault == 'edited_launch':
        api.launch['updated_at'] = lease.text_stamp(NOW - timedelta(minutes=4))
    elif fault == 'missing_g7':
        api.launch['body'] = api.launch['body'].replace('RC6_PREDEPLOY_G7_AUTHORIZATION=APPROVED', '')
    elif fault == 'wrong_gate':
        api.launch['body'] = api.launch['body'].replace('GATES_AUTHORIZED=predeploy', 'GATES_AUTHORIZED=BIG-browser')
    elif fault == 'wrong_pr':
        api.launch['body'] = api.launch['body'].replace('SOURCE_PR=801', 'SOURCE_PR=802')
    elif fault == 'owner_hash':
        api.launch['body'] = api.launch['body'].replace('OWNER_RECEIPT_473_SHA256=', 'UNKNOWN_OWNER_HASH=')
    elif fault == 'transfer_edit':
        api.transfers[473]['updated_at'] = lease.text_stamp(NOW)
    elif fault == 'predecessor_edit':
        api.documents['/issues/comments/' + str(admission.PREDECESSOR_ANCHORS[471][0])]['body'] += 'Edited\n'
    elif fault == 'expired_owner':
        api.rows[473][-1] = owner_comment(473, 100473, NOW - timedelta(minutes=21), NOW - timedelta(minutes=1),
                                        owner=admission.CLOSURE_OWNER)
    elif fault == 'edited_owner':
        api.rows[473][-1]['updated_at'] = lease.text_stamp(NOW)
    elif fault == 'expired_foreign':
        api.rows[473].insert(-1, owner_comment(473, 991, NOW - timedelta(minutes=21), NOW - timedelta(minutes=1),
                                              owner='FOREIGN_UNRELEASED_OWNER'))
    else:
        api.launch['body'] = api.launch['body'].replace(admission.CLOSURE_OWNER, admission.SUCCESSOR_OWNER)
    calls = []
    monkeypatch.setattr(admission, 'verify_capacity_comparison_manifest', lambda *a, **k: calls.append('capacity'))
    with pytest.raises(ValueError):
        admit(candidate)
    assert calls == []


def test_administrative_transfer_alone_cannot_authorize_a_candidate(candidate):
    api, _event, _path = candidate
    api.rows[471].remove(api.launch)
    with pytest.raises(ValueError, match='ONE_EXACT_PREPUSH_LAUNCH_AUTHORITY_REQUIRED'):
        admission.admit(source_sha=SHA, source_tree=TREE, gate='predeploy', now=NOW)


def test_api_only_live_monitor_reads_dynamic_candidate_and_current_dual_leases(candidate):
    api, _event, _path = candidate
    result = admission.owned_gate_control_scope(source_sha=SHA, source_tree=TREE,
        launch_receipt_url=api.launch['html_url'], owner_session=admission.CLOSURE_OWNER,
        gate='predeploy', now=NOW, get=api.read, run_id=RUN, run_attempt=1)
    assert result['owner_session'] == admission.CLOSURE_OWNER
    assert set(result['fresh_ownership']) == {'471', '473'}
    assert '/pulls/476' not in api.calls


def test_material_carrier_reuses_dynamic_scope_instead_of_old_pr(candidate, monkeypatch):
    api, _event, _path = candidate
    args = SimpleNamespace(source_sha=SHA, source_tree=TREE, gate='predeploy',
        owner_session=admission.CLOSURE_OWNER, launch_receipt_url=api.launch['html_url'], require_pr_admission=True)
    monkeypatch.setattr(carrier, 'api', lambda *a: pytest.fail('legacy carrier API must not be used'))
    result = carrier.authority(args)
    assert result['PR'] == CANDIDATE_PR and result['branch'] == CANDIDATE_BRANCH
    assert result['candidate_scope']['base_tree'] == BASE_TREE


def test_expired_foreign_owner_is_not_released_in_historical_lease_coverage():
    context = {'owner_session': admission.CLOSURE_OWNER, 'source_sha': SHA, 'source_tree': TREE}
    rows = {issue: [owner_comment(issue, issue, NOW - timedelta(seconds=1), NOW + timedelta(minutes=10),
                                owner=admission.CLOSURE_OWNER)] for issue in (471, 473)}
    foreign = owner_comment(471, 991, NOW - timedelta(minutes=22), NOW - timedelta(minutes=2), owner='FOREIGN_OWNER')
    rows[471].insert(0, foreign)
    with pytest.raises(ValueError, match='FOREIGN_RECORD_OVERLAPS_PRODUCER'):
        lease.prove_lease_coverage(rows, context, NOW, NOW + timedelta(seconds=1))
    foreign['body'] = foreign['body'].replace('RELEASED=false', 'RELEASED=true')
    assert set(lease.prove_lease_coverage(rows, context, NOW, NOW + timedelta(seconds=1))) == {'471', '473'}

"""Authentic released-succession predicates with bounded transport fixtures.

These are economic control regressions, not ownership receipts or material
producers. Production pins and authorship predicates execute unchanged.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
import urllib.parse

import pytest

from scripts import rc6_actions_custody as custody
from scripts import rc6_material_carrier as carrier
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_owned_gate_lease as lease
from tests.test_rc6_candidate_authorization import CandidateApi, CANDIDATE_PR
from tests.test_rc6_owned_gate_lease import RUN, SHA, TREE, comment as transport_comment, kernel, owner_comment, replay


NOW = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
OWNER = 'CODEX_RC6_NATIVE_CERTIFICATION_UNIT'
PREVIOUS = 'CODEX_RC6_ZERO_COST_UNIT'
WORKSTREAM = 'WS-RC6-NATIVE-CERTIFICATION-UNIT'
PREVIOUS_WORKSTREAM = 'WS-RC6-ZERO-COST-UNIT'
BRANCH = 'fix/rc6-native-certification-unit'
INITIAL_SHA, INITIAL_TREE = 'f' * 40, 'e' * 40
PLAN = 'd' * 64


def comment(issue, identifier, body, stamp):
    row=transport_comment(issue,identifier,body,stamp)
    row['user']['id']=123456
    row['url']='https://api.github.com/repos/'+admission.REPO+'/issues/comments/'+str(identifier)
    return row


def body(values):
    return '\n'.join(key + '=' + value for key, value in values.items()) + '\n'


def scoped_row(issue, identifier, created, owner, *, released=False, sha=SHA, tree=TREE,
               workstream=WORKSTREAM):
    values = {'WORKSTREAM_ID': workstream, 'SESSION': owner, 'SESSION_SUCCESSOR': owner,
        'WRITE_OWNER': owner, 'INTEGRATION_OWNER': owner, 'DEPLOY_OWNER': 'NOT_ACQUIRED',
        'RELEASED': 'true' if released else 'false', 'SOURCE_SHA': sha, 'SOURCE_TREE': tree,
        'SOURCE_LEASE_EXPIRES_UTC': lease.text_stamp(created + timedelta(minutes=20)),
        'MODE': 'PRODUCTION_PAPER / SIMULATION', 'real_orders_sent': '0'}
    return comment(issue, identifier, body(values), lease.text_stamp(created))


class ReleasedApi(CandidateApi):
    def __init__(self, monkeypatch, gate='predeploy'):
        super().__init__(monkeypatch, gate=gate)
        self.releases, self.transfers = {}, {}
        transfer = {'WORKSTREAM_ID': WORKSTREAM, 'SESSION': OWNER, 'SESSION_SUCCESSOR': OWNER,
            'WRITE_OWNER': OWNER, 'INTEGRATION_OWNER': OWNER, 'DEPLOY_OWNER': 'NOT_ACQUIRED',
            'RELEASED': 'false', 'BRANCH': BRANCH, 'BASE_SHA': INITIAL_SHA,
            'SOURCE_SHA': INITIAL_SHA, 'SOURCE_TREE': INITIAL_TREE,
            'PREDECESSOR_OWNER': PREVIOUS, 'PREDECESSOR_RELEASED': 'true',
            'PREDECESSOR_RECEIPT_471': '300471', 'PREDECESSOR_RECEIPT_473': '300473',
            'SUCCESSION_KIND': admission.RELEASED_SUCCESSION_KIND,
            'HEAVY_GATES_AUTHORIZED': 'false', 'G0_G8_QUALIFICATION': 'false',
            'FINAL_CANDIDATE_ELIGIBLE': 'false', 'MODE': 'PRODUCTION_PAPER / SIMULATION',
            'real_orders_sent': '0'}
        for issue in (471, 473):
            closure = self.documents['/issues/comments/' + str(admission.CLOSURE_ANCHORS[issue][0])]
            old_release = scoped_row(issue, 200000 + issue, NOW - timedelta(minutes=60),
                admission.CLOSURE_OWNER, released=True, workstream=admission.WORKSTREAM)
            active = scoped_row(issue, 290000 + issue, NOW - timedelta(minutes=50), PREVIOUS,
                sha=INITIAL_SHA, tree=INITIAL_TREE, workstream=PREVIOUS_WORKSTREAM)
            released = scoped_row(issue, 300000 + issue, NOW - timedelta(minutes=30), PREVIOUS,
                released=True, sha=INITIAL_SHA, tree=INITIAL_TREE, workstream=PREVIOUS_WORKSTREAM)
            self.releases[issue] = released
            for row in (old_release, active, released):
                self.documents['/issues/comments/' + str(row['id'])] = row
            self.rows[issue] = [closure, old_release, active, released]
        twin = comment(473, 310473, body(transfer), lease.text_stamp(NOW - timedelta(minutes=20)))
        transfer.update(OWNER_RECEIPT_473=str(twin['id']),
            OWNER_RECEIPT_473_SHA256=custody.digest(twin['body'].encode()))
        first = comment(471, 310471, body(transfer), lease.text_stamp(NOW - timedelta(minutes=20) + timedelta(seconds=1)))
        for issue, row in ((471, first), (473, twin)):
            self.rows[issue].append(row)
            self.documents['/issues/comments/' + str(row['id'])] = row
            self.transfers[issue] = row
        launch = admission.fields(self.launch['body'])
        launch.update(WORKSTREAM_ID=WORKSTREAM, SESSION_SUCCESSOR=OWNER, WRITE_OWNER=OWNER,
            INTEGRATION_OWNER=OWNER, SOURCE_BRANCH=BRANCH)
        for issue in (471, 473):
            row = self.transfers[issue]
            launch['OWNER_RECEIPT_' + str(issue)] = row['html_url']
            launch['OWNER_RECEIPT_' + str(issue) + '_SHA256'] = custody.digest(row['body'].encode())
        self.launch['body'] = body(launch)
        self.launch['created_at'] = self.launch['updated_at'] = lease.text_stamp(NOW - timedelta(minutes=5))
        self.rows[471].append(self.launch)
        for issue in (471, 473):
            row = scoped_row(issue, 400000 + issue, NOW - timedelta(seconds=30), OWNER)
            self.rows[issue].append(row)
            self.documents['/issues/comments/' + str(row['id'])] = row
        self.documents['/git/ref/heads/' + BRANCH] = {'object': {'sha': SHA}}
        self.documents['/pulls/' + str(CANDIDATE_PR)]['head']['ref'] = BRANCH

    def request(self, path):
        return self.read(path)

    def authorize_control(self, operation='RC6_DEVELOPMENT_CHECKS_AUTHORIZATION'):
        key = 'NATIVE_PREREQUISITES_PLAN_SHA256' if operation == 'RC6_NATIVE_PREREQUISITES_AUTHORIZATION' \
            else 'DEVELOPMENT_PLAN_SHA256'
        twin = self.rows[473][-1]
        twin['body'] += operation + '=APPROVED\n' + key + '=' + PLAN + '\n'
        row = self.rows[471][-1]
        row['body'] += operation + '=APPROVED\n' + key + '=' + PLAN + '\n'
        row['body'] += 'OWNER_RECEIPT_473=' + str(twin['id']) + '\n'
        row['body'] += 'OWNER_RECEIPT_473_SHA256=' + custody.digest(twin['body'].encode()) + '\n'
        return row


def test_released_succession_proves_old_history_and_does_not_grant_material(monkeypatch):
    transport = ReleasedApi(monkeypatch)
    anchors = admission.administrative_anchors(OWNER, get=transport.read, now=NOW)
    assert {issue: row['id'] for issue, row in anchors.items()} == {471: 310471, 473: 310473}
    for issue in (471, 473):
        rows = admission.recent(issue, anchors[issue], NOW, get=transport.read)
        assert rows[0]['id'] == admission.CLOSURE_ANCHORS[issue][0]
        latest = admission.latest_writer(rows, issue, SHA, TREE, OWNER, NOW)
        assert latest['latest_status_by_owner'][PREVIOUS]['released'] is True
        assert latest['latest_status_by_owner'][admission.CLOSURE_OWNER]['released'] is True
        assert latest['maximum_lease_seconds'] == 1200
    with pytest.raises(ValueError, match='EXACT_IMMUTABLE_SINGLE_GATE_LAUNCH_RECEIPT_REQUIRED'):
        admission.launch_fields(transport.transfers[471], INITIAL_SHA, INITIAL_TREE, OWNER, 'Horizon',
            get=transport.read, anchors=anchors)


def endpoint_copy(transport,row):
    copy_row=copy.deepcopy(row)
    transport.documents['/issues/comments/'+str(row['id'])]=copy_row
    return copy_row


@pytest.mark.parametrize('kind',['transfer','release471','release473'])
def test_collection_app_client_metadata_does_not_change_comment_identity(monkeypatch,kind):
    transport=ReleasedApi(monkeypatch)
    row=transport.transfers[473] if kind=='transfer' else transport.releases[int(kind[-3:])]
    row['performed_via_github_app']={'id':42,'slug':'authenticated-client','client_id':'list-only'}
    single=endpoint_copy(transport,row)
    del single['performed_via_github_app']['client_id']
    anchors=admission.administrative_anchors(OWNER,get=transport.read,now=NOW)
    assert anchors[471]['id']==310471 and anchors[473]['id']==310473
    assert row['performed_via_github_app']['client_id']=='list-only'


@pytest.mark.parametrize('kind',['transfer','release471','release473'])
@pytest.mark.parametrize('fault',['body','created_at','updated_at','html_url','issue_url','url',
    'author_id','author_login','missing_author_id','app_id','app_slug','app_removed','missing_app_id'])
def test_endpoint_shape_tolerance_never_accepts_rebound_immutable_identity(monkeypatch,kind,fault):
    transport=ReleasedApi(monkeypatch)
    row=transport.transfers[473] if kind=='transfer' else transport.releases[int(kind[-3:])]
    row['performed_via_github_app']={'id':42,'slug':'authenticated-client','client_id':'list-only'}
    single=endpoint_copy(transport,row)
    if fault in ('body','html_url','issue_url','url'):single[fault]+='REBOUND'
    elif fault in ('created_at','updated_at'):single[fault]=lease.text_stamp(NOW)
    elif fault=='author_id':single['user']['id']+=1
    elif fault=='author_login':single['user']['login']='foreign'
    elif fault=='missing_author_id':del single['user']['id']
    elif fault=='app_id':single['performed_via_github_app']['id']+=1
    elif fault=='app_slug':single['performed_via_github_app']['slug']='foreign'
    elif fault=='app_removed':single['performed_via_github_app']=None
    else:del single['performed_via_github_app']['id']
    with pytest.raises(ValueError):
        admission.administrative_anchors(OWNER,get=transport.read,now=NOW)


@pytest.mark.parametrize('fault', ['wrong_author', 'edited_transfer', 'edited_release', 'false_release',
    'release_from_wrong_session', 'release_after_transfer', 'missing_twin', 'twin_digest',
    'release_source', 'release_tree', 'release_pair_base', 'unreviewed_heavy', 'source_base',
    'foreign_reacquired', 'expired_foreign', 'foreign_before_new_anchor', 'edited_old_record',
    'missing_old_anchor', 'different_workstream', 'old_owner_in_new_receipt'])
def test_economic_control_rejects_forged_missing_or_unresolved_ownership_before_start(monkeypatch, fault):
    transport = ReleasedApi(monkeypatch)
    receipt = transport.authorize_control()
    if fault == 'wrong_author':
        transport.transfers[471]['user']['login'] = 'another-writer'
    elif fault == 'edited_transfer':
        transport.transfers[471]['updated_at'] = lease.text_stamp(NOW)
    elif fault == 'edited_release':
        transport.releases[473]['updated_at'] = lease.text_stamp(NOW)
    elif fault == 'false_release':
        transport.releases[473]['body'] = transport.releases[473]['body'].replace('RELEASED=true', 'RELEASED=false')
    elif fault == 'release_from_wrong_session':
        transport.releases[473]['body'] = transport.releases[473]['body'].replace('SESSION_SUCCESSOR=' + PREVIOUS,
            'SESSION_SUCCESSOR=' + OWNER)
    elif fault == 'release_after_transfer':
        transport.releases[473]['created_at'] = transport.releases[473]['updated_at'] = lease.text_stamp(NOW)
    elif fault == 'missing_twin':
        transport.rows[473].remove(transport.transfers[473])
    elif fault == 'twin_digest':
        transport.transfers[471]['body'] = transport.transfers[471]['body'].replace('OWNER_RECEIPT_473_SHA256=',
            'MISSING_PAIR_DIGEST=')
    elif fault == 'release_source':
        transport.releases[473]['body'] = transport.releases[473]['body'].replace(INITIAL_SHA, 'c' * 40)
    elif fault == 'release_tree':
        transport.releases[473]['body'] = transport.releases[473]['body'].replace(INITIAL_TREE, 'c' * 40)
    elif fault == 'release_pair_base':
        transport.releases[473]['body'] += 'BASE_SHA=' + 'c' * 40 + '\n'
    elif fault == 'unreviewed_heavy':
        transport.transfers[471]['body'] = transport.transfers[471]['body'].replace('HEAVY_GATES_AUTHORIZED=false',
            'HEAVY_GATES_AUTHORIZED=true')
    elif fault == 'source_base':
        transport.transfers[471]['body'] = transport.transfers[471]['body'].replace('BASE_SHA=' + INITIAL_SHA,
            'BASE_SHA=' + 'c' * 40)
    elif fault in ('foreign_reacquired', 'expired_foreign', 'foreign_before_new_anchor'):
        owner = PREVIOUS if fault == 'foreign_reacquired' else 'CODEX_FOREIGN_UNKNOWN'
        created = NOW - timedelta(minutes=25) if fault != 'foreign_reacquired' else NOW - timedelta(seconds=40)
        transport.rows[473].insert(-1, scoped_row(473, 350473, created, owner))
    elif fault == 'edited_old_record':
        transport.rows[473][1]['updated_at'] = lease.text_stamp(NOW - timedelta(seconds=10))
    elif fault == 'missing_old_anchor':
        transport.rows[473].pop(0)
    elif fault == 'different_workstream':
        receipt['body'] = receipt['body'].replace(WORKSTREAM, 'WS-RC6-UNAUTHENTICATED')
    else:
        receipt['body'] = receipt['body'].replace(OWNER, admission.CLOSURE_OWNER)
    started = []
    with pytest.raises(ValueError):
        custody.verify_owner(transport, receipt['html_url'], sha=SHA, tree=TREE, owner=OWNER, now=NOW,
            plan_sha256=PLAN, authorization_field='RC6_DEVELOPMENT_CHECKS_AUTHORIZATION')
        started.append('FORBIDDEN')
    assert started == []


@pytest.mark.parametrize('operation', ['RC6_DEVELOPMENT_CHECKS_AUTHORIZATION',
    'RC6_NATIVE_PREREQUISITES_AUTHORIZATION'])
def test_fresh_dual_exact_plan_control_authorization_is_positive(monkeypatch, operation):
    transport = ReleasedApi(monkeypatch)
    row = transport.authorize_control(operation)
    result = custody.verify_owner(transport, row['html_url'], sha=SHA, tree=TREE, owner=OWNER, now=NOW,
        plan_sha256=PLAN, authorization_field=operation)
    assert result == hashlib.sha256(row['body'].encode()).hexdigest()
    assert not any('POST' in call or '/dispatches' in call for call in transport.calls)


@pytest.mark.parametrize('fault', ['economic_cannot_authorize_native', 'missing_plan', 'wrong_plan',
    'twin_not_native', 'twin_plan', 'expired', 'superseded_source'])
def test_native_prerequisite_control_needs_its_own_live_dual_exact_authority(monkeypatch, fault):
    transport = ReleasedApi(monkeypatch)
    row = transport.authorize_control('RC6_NATIVE_PREREQUISITES_AUTHORIZATION')
    plan = PLAN
    if fault == 'economic_cannot_authorize_native':
        for issue in (471, 473):
            transport.rows[issue][-1]['body'] = transport.rows[issue][-1]['body'].replace(
                'RC6_NATIVE_PREREQUISITES_AUTHORIZATION=APPROVED',
                'RC6_DEVELOPMENT_CHECKS_AUTHORIZATION=APPROVED')
    elif fault == 'missing_plan':
        plan = None
    elif fault == 'wrong_plan':
        plan = 'a' * 64
    elif fault == 'twin_not_native':
        transport.rows[473][-1]['body'] = transport.rows[473][-1]['body'].replace(
            'RC6_NATIVE_PREREQUISITES_AUTHORIZATION=APPROVED',
            'RC6_NATIVE_PREREQUISITES_AUTHORIZATION=NOT_GRANTED')
        values = admission.fields(row['body'])
        values['OWNER_RECEIPT_473_SHA256'] = custody.digest(transport.rows[473][-1]['body'].encode())
        row['body'] = body(values)
    elif fault == 'twin_plan':
        transport.rows[473][-1]['body'] = transport.rows[473][-1]['body'].replace(PLAN, 'a' * 64)
    elif fault == 'expired':
        row['body'] = row['body'].replace(lease.text_stamp(NOW + timedelta(minutes=19, seconds=30)),
            lease.text_stamp(NOW))
    else:
        transport.rows[473].append(scoped_row(473, 500473, NOW - timedelta(seconds=1), OWNER, sha='c' * 40))
    with pytest.raises(ValueError):
        custody.verify_owner(transport, row['html_url'], sha=SHA, tree=TREE, owner=OWNER, now=NOW,
            plan_sha256=plan, authorization_field='RC6_NATIVE_PREREQUISITES_AUTHORIZATION')


def test_new_owner_material_scope_retains_exact_canonical_base_and_one_gate(monkeypatch):
    transport = ReleasedApi(monkeypatch)
    result = admission.owned_gate_control_scope(source_sha=SHA, source_tree=TREE, owner_session=OWNER,
        launch_receipt_url=transport.launch['html_url'], gate='predeploy', now=NOW,
        get=transport.read, run_id=RUN, run_attempt=1)
    assert result['owner_session'] == OWNER and result['workstream_id'] == WORKSTREAM
    assert result['DEPLOY_OWNER'] == 'NOT_ACQUIRED' and result['real_orders_sent'] == 0
    transport.launch['body'] = transport.launch['body'].replace('GATES_AUTHORIZED=predeploy',
        'GATES_AUTHORIZED=predeploy,Horizon')
    with pytest.raises(ValueError, match='SINGLE_GATE'):
        admission.owned_gate_control_scope(source_sha=SHA, source_tree=TREE, owner_session=OWNER,
            launch_receipt_url=transport.launch['html_url'], gate='predeploy', now=NOW,
            get=transport.read, run_id=RUN, run_attempt=1)


@pytest.mark.parametrize('explicit', [True, False])
def test_new_owner_full_material_admission_remains_native_not_started(monkeypatch, tmp_path, explicit):
    transport = ReleasedApi(monkeypatch)
    pr = transport.documents['/pulls/' + str(CANDIDATE_PR)]
    pr['updated_at'] = lease.text_stamp(NOW - timedelta(minutes=4))
    event = {'repository': {'id': admission.REPO_ID, 'full_name': admission.REPO},
        'number': CANDIDATE_PR, 'pull_request': copy.deepcopy(pr),
        'sender': {'login': 'mbalbo2023'}, 'action': 'ready_for_review'}
    path = tmp_path / 'event.json'
    path.write_text(json.dumps(event))
    for key, value in {'GITHUB_REPOSITORY': admission.REPO, 'GITHUB_REPOSITORY_ID': str(admission.REPO_ID),
        'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_ID': str(RUN), 'GITHUB_EVENT_NAME': 'pull_request',
        'GITHUB_EVENT_PATH': str(path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(admission, 'api', transport.read)
    result = admission.admit(source_sha=SHA, source_tree=TREE, gate='predeploy', now=NOW,
        owner_session=OWNER if explicit else None,
        launch_receipt_url=transport.launch['html_url'] if explicit else None)
    assert result['owner_session'] == OWNER and result['status'] == 'ADMITTED_NATIVE_NOT_STARTED'
    assert result['ownership_transfer']['kind'] == admission.RELEASED_SUCCESSION_KIND
    assert result['ownership_transfer']['predecessor_released_claimed'] is True
    assert result['ownership_transfer']['heavy_authority_from_transfer_claimed'] is False
    assert result['candidate_scope']['base_branch'] == admission.BASE
    assert result['material_gates'] == [] and result['final_candidate_eligible'] is False


def test_material_carrier_does_not_fall_back_to_historical_pr_or_branch(monkeypatch):
    transport = ReleasedApi(monkeypatch)
    monkeypatch.setattr(admission, 'api', transport.read)
    monkeypatch.setattr(carrier, 'api', lambda *a: pytest.fail('legacy PR476 authority is forbidden'))
    monkeypatch.setenv('GITHUB_RUN_ID', str(RUN))
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    args = SimpleNamespace(source_sha=SHA, source_tree=TREE, gate='predeploy', owner_session=OWNER,
        launch_receipt_url=transport.launch['html_url'], require_pr_admission=True)
    result = carrier.authority(args)
    assert result['PR'] == CANDIDATE_PR and result['branch'] == BRANCH
    assert result['candidate_scope']['base_branch'] == admission.BASE


def test_new_owner_live_monitor_and_archived_replay_keep_dual_lease_union(monkeypatch, tmp_path):
    transport = ReleasedApi(monkeypatch)
    tmp_path.chmod(0o700)
    elapsed = [0]
    original = custody.digest(admission.wire(transport.documents))
    instance = lease.GateLeaseMonitor(tmp_path, source_sha=SHA, source_tree=TREE, owner_session=OWNER,
        gate='predeploy', launch_receipt_url=transport.launch['html_url'], run_id=RUN, run_attempt=1,
        get=transport.get, clock=lambda: NOW + timedelta(seconds=elapsed[0]),
        monotonic=lambda: 1000 + elapsed[0])
    scope = instance.prelaunch('new-owner-control-only')
    assert scope['workstream_id'] == WORKSTREAM
    instance.progress('started', 123, 1000, 6400, 0)
    elapsed[0] += 1
    receipt = instance.after_fin(kernel())
    assert receipt['status'] == 'GREEN' and receipt['qualification_claimed'] is False
    elapsed[0] += 86400
    assert replay(instance, receipt, kernel())['past_lease_compared_to_current_time'] is False
    assert custody.digest(admission.wire(transport.documents)) == original


def test_foreign_ownership_during_producer_cannot_be_erased_by_later_release():
    context = {'owner_session': OWNER, 'source_sha': SHA, 'source_tree': TREE}
    timelines = {issue: [scoped_row(issue, issue, NOW - timedelta(seconds=1), OWNER)]
        for issue in (471, 473)}
    timelines[473] += [scoped_row(473, 10, NOW + timedelta(seconds=1), PREVIOUS),
        scoped_row(473, 11, NOW + timedelta(seconds=2), PREVIOUS, released=True)]
    with pytest.raises(ValueError, match='FOREIGN_RECORD_OVERLAPS_PRODUCER'):
        lease.prove_lease_coverage(timelines, context, NOW, NOW + timedelta(seconds=3), workstream=WORKSTREAM)


@pytest.mark.parametrize('change', ['release', 'source'])
def test_own_scope_revocation_during_producer_cannot_be_erased_by_reacquisition(change):
    context = {'owner_session': OWNER, 'source_sha': SHA, 'source_tree': TREE}
    timelines = {issue: [scoped_row(issue, issue, NOW - timedelta(seconds=1), OWNER)]
        for issue in (471, 473)}
    row = scoped_row(473, 10, NOW + timedelta(seconds=1), OWNER,
        released=change == 'release', sha='c' * 40 if change == 'source' else SHA)
    if change == 'release':
        row['body'] = '\n'.join(line for line in row['body'].splitlines()
            if not line.startswith('SOURCE_LEASE_EXPIRES_UTC=')) + '\n'
    timelines[473] += [row, scoped_row(473, 11, NOW + timedelta(seconds=2), OWNER)]
    with pytest.raises(ValueError, match='OWNER_RELEASE_OVERLAPS_PRODUCER|CURRENT_SOURCE_OR_OWNER_CHANGED'):
        lease.prove_lease_coverage(timelines, context, NOW, NOW + timedelta(seconds=3), workstream=WORKSTREAM)


def own_release_then_reacquisition(*, reacquired=NOW-timedelta(seconds=1)):
    timelines={issue:[scoped_row(issue,1000+issue,NOW-timedelta(minutes=1),OWNER),
        scoped_row(issue,2000+issue,NOW-timedelta(seconds=20),OWNER,released=True)]
        for issue in (471,473)}
    if reacquired is not None:
        for issue,rows in timelines.items():rows.append(scoped_row(issue,3000+issue,reacquired,OWNER))
    context={'owner_session':OWNER,'source_sha':SHA,'source_tree':TREE}
    return timelines,context


@pytest.mark.parametrize('quoted_lease',['unexpired','expired','absent'])
def test_authentic_prior_own_release_requires_new_same_source_lease(quoted_lease):
    timelines,context=own_release_then_reacquisition()
    for rows in timelines.values():
        release=rows[1]
        if quoted_lease=='absent':
            release['body']='\n'.join(line for line in release['body'].splitlines()
                if not line.startswith('SOURCE_LEASE_EXPIRES_UTC='))+'\n'
        elif quoted_lease=='expired':
            release['body']=release['body'].replace(
                lease.text_stamp(NOW+timedelta(minutes=19,seconds=40)),
                lease.text_stamp(NOW-timedelta(seconds=30)))
    coverage=lease.prove_lease_coverage(timelines,context,NOW,NOW+timedelta(seconds=3),workstream=WORKSTREAM)
    assert {issue:facts['comment_ids'] for issue,facts in coverage.items()}=={'471':[3471],'473':[3473]}


@pytest.mark.parametrize('fault',['no_reacquisition','late_reacquisition','one_issue_only'])
def test_released_unexpired_lease_cannot_bridge_a_reacquisition_gap(fault):
    timelines,context=own_release_then_reacquisition(reacquired=
        None if fault=='no_reacquisition' else NOW+timedelta(seconds=1)
        if fault=='late_reacquisition' else NOW-timedelta(seconds=1))
    if fault=='one_issue_only':timelines[473].pop()
    with pytest.raises(ValueError,match='CONTINUITY_GAP'):
        lease.prove_lease_coverage(timelines,context,NOW,NOW+timedelta(seconds=3),workstream=WORKSTREAM)


@pytest.mark.parametrize('fault',['author','edited','session','integration','workstream','mode',
    'real_orders','deploy','expiry','flag','source_syntax','tree_syntax','foreign_active','forged_release'])
def test_prior_release_transition_cannot_hide_malformed_or_foreign_authority(fault):
    timelines,context=own_release_then_reacquisition()
    release=timelines[473][1]
    if fault=='author':release['user']['login']='foreign'
    elif fault=='edited':release['updated_at']=lease.text_stamp(NOW)
    elif fault=='foreign_active':
        timelines[473].insert(-1,scoped_row(473,4000,NOW-timedelta(seconds=10),PREVIOUS))
    elif fault=='forged_release':
        timelines[473].insert(-1,scoped_row(473,4000,NOW-timedelta(seconds=10),PREVIOUS))
        forged=scoped_row(473,4001,NOW-timedelta(seconds=5),PREVIOUS,released=True)
        forged['body']=forged['body'].replace('SESSION_SUCCESSOR='+PREVIOUS,'SESSION_SUCCESSOR='+OWNER)
        timelines[473].insert(-1,forged)
    else:
        key,value={'session':('SESSION_SUCCESSOR',PREVIOUS),'integration':('INTEGRATION_OWNER',PREVIOUS),
            'workstream':('WORKSTREAM_ID','WS-RC6-FOREIGN'), 'mode':('MODE','REAL'),
            'real_orders':('real_orders_sent','1'),'deploy':('DEPLOY_OWNER',OWNER),
            'expiry':('SOURCE_LEASE_EXPIRES_UTC','malformed'),'flag':('RELEASED','unknown'),
            'source_syntax':('SOURCE_SHA','malformed'),'tree_syntax':('SOURCE_TREE','malformed')}[fault]
        fields=admission.fields(release['body']);fields[key]=value;release['body']=body(fields)
    with pytest.raises(ValueError):
        lease.prove_lease_coverage(timelines,context,NOW,NOW+timedelta(seconds=3),workstream=WORKSTREAM)


@pytest.mark.parametrize('offset',[0,1,3])
@pytest.mark.parametrize('quoted_lease',[True,False])
def test_reacquisition_never_erases_own_release_inside_closed_interval(offset,quoted_lease):
    timelines,context=own_release_then_reacquisition()
    release=scoped_row(473,4000,NOW+timedelta(seconds=offset),OWNER,released=True)
    if not quoted_lease:
        release['body']='\n'.join(line for line in release['body'].splitlines()
            if not line.startswith('SOURCE_LEASE_EXPIRES_UTC='))+'\n'
    timelines[473]+=[release,scoped_row(473,4001,NOW+timedelta(seconds=offset,microseconds=1),OWNER)]
    with pytest.raises(ValueError,match='OWNER_RELEASE_OVERLAPS_PRODUCER'):
        lease.prove_lease_coverage(timelines,context,NOW,NOW+timedelta(seconds=3),workstream=WORKSTREAM)


def test_fresh_same_owner_reacquisition_never_authorizes_identical_red_retry(monkeypatch):
    transport=ReleasedApi(monkeypatch)
    for issue in (471,473):
        for row in (scoped_row(issue,500000+issue,NOW-timedelta(seconds=20),OWNER,released=True),
                scoped_row(issue,600000+issue,NOW-timedelta(seconds=1),OWNER)):
            transport.rows[issue].append(row)
            transport.documents['/issues/comments/'+str(row['id'])]=row
    receipt=transport.authorize_control('RC6_NATIVE_PREREQUISITES_AUTHORIZATION')
    assert custody.verify_owner(transport,receipt['html_url'],sha=SHA,tree=TREE,owner=OWNER,now=NOW,
        plan_sha256=PLAN,authorization_field='RC6_NATIVE_PREREQUISITES_AUTHORIZATION')
    monkeypatch.setenv('GITHUB_RUN_ID','72')
    monkeypatch.setattr(admission,'api',lambda path:{'total_count':1,'workflow_runs':[
        {'id':71,'head_sha':SHA,'event':'workflow_dispatch','status':'completed','conclusion':'failure',
            'display_title':'RC6 material native-prerequisites @ '+SHA}]})
    with pytest.raises(ValueError,match='REQUIRES_NEW_EVIDENCED_SHA'):
        admission.dedup_admission(SHA,'native-prerequisites')


def test_successor_history_pagination_does_not_silently_truncate(monkeypatch):
    transport = ReleasedApi(monkeypatch)
    pages = {}
    for issue in (471, 473):
        filler = [comment(issue, 600000 + issue + index * 1000, 'read-only administrative note',
            lease.text_stamp(NOW - timedelta(minutes=15))) for index in range(100)]
        pages[issue] = filler + transport.rows[issue]
    def read(path):
        if '/comments?' in path:
            issue = int(path.split('/issues/')[1].split('/')[0])
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['page'][0])
            return copy.deepcopy(pages[issue][(page-1)*100:page*100])
        return transport.read(path)
    anchors = admission.administrative_anchors(OWNER, get=read, now=NOW)
    assert anchors[471]['id'] == 310471
    def incomplete(path):
        if '/comments?' in path:
            issue = int(path.split('/issues/')[1].split('/')[0])
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['page'][0])
            return [comment(issue, 800000000 + page * 1000 + index, 'bounded but incomplete note',
                lease.text_stamp(NOW - timedelta(minutes=10))) for index in range(100)]
        return transport.read(path)
    with pytest.raises(ValueError, match='OWNERSHIP_PAGINATION_INCOMPLETE_FAIL_CLOSED'):
        admission.administrative_anchors(OWNER, get=incomplete, now=NOW)

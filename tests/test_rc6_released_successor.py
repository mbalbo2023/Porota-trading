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


class ReleasedDiagnosticApi(ReleasedApi):
    """Bounded API fixture exercises real paired-owner/revision predicates.

    No native acceptance object is fabricated and no producer is launched.
    The source/transport fixture is not a claim about an actual GitHub run.
    """
    def __init__(self,monkeypatch,gate='capacity-probe'):
        super().__init__(monkeypatch)
        from scripts import rc6_contract_revisions as revisions
        from scripts import rc6_private_pid_boundary as boundary
        self.gate=gate
        self.contract_digest=boundary.contract_sha256()
        scope={'schema':'porota.rc6.capacity-diagnostic-scope.v1','mode':gate,
            'backing_image_bytes':(5 if gate=='capacity-probe' else 26)*1024**3,
            'project_hard_limit_bytes':512*1024**2 if gate=='capacity-probe' else 20*1024**3,
            'residual_reserve_bytes':4*1024**3,'financial_tick_allowed':False,'qualification_claimed':False}
        self.prior=None
        values={'AUTHORIZATION_SCHEMA':admission.DIAGNOSTIC_AUTHORIZATION_SCHEMA,
            'RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION':'APPROVED','WORKSTREAM_ID':WORKSTREAM,
            'DIAGNOSTIC_MODE':gate,'SOURCE_BRANCH':BRANCH,'SOURCE_SHA':SHA,'SOURCE_TREE':TREE,
            'SESSION_SUCCESSOR':OWNER,'WRITE_OWNER':OWNER,'INTEGRATION_OWNER':OWNER,
            'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false','MODE':'PRODUCTION_PAPER / SIMULATION',
            'real_orders_sent':'0','SOURCE_LEASE_EXPIRES_UTC':lease.text_stamp(NOW+timedelta(minutes=10)),
            'READ_CONTRACT_SHA256':PLAN,'SOURCE_MANIFEST_SHA256':'e'*64,
            'CAPACITY_DIAGNOSTIC_SCOPE_JSON':json.dumps(scope),'CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON':'null',
            'CONTRACT_ACCEPTANCE_SCHEMA':revisions.SCHEMA,'RC6_CONTRACT_REVISION_AUTHORIZATION':'APPROVED',
            'CONTRACT_DECISION_ORIGIN':'EXPLICIT_OWNER_DECISION','CONTRACT_REVISION':'PRIVATE_PID_BOUNDARY_V2',
            'CONTRACT_SCOPE':gate,'CONTRACT_DOCUMENT_SHA256':self.contract_digest,
            'CONTRACT_ACCEPTANCE_LIFECYCLE':admission.DIAGNOSTIC_CONTRACT_LIFECYCLE,
            'CONTRACT_GATES_ALREADY_GREEN':'false','NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED':'false',
            'FINAL_CANDIDATE_ELIGIBLE':'false','DEPLOY_AUTHORIZATION':'NOT_GRANTED'}
        self.twin=comment(473,410473,body(values),lease.text_stamp(NOW-timedelta(seconds=21)))
        values.update(OWNER_RECEIPT_473=str(self.twin['id']),OWNER_RECEIPT_473_SHA256=custody.digest(self.twin['body'].encode()))
        self.launch=comment(471,410471,body(values),lease.text_stamp(NOW-timedelta(seconds=20)))
        for issue,row in ((471,self.launch),(473,self.twin)):
            self.rows[issue].append(row)
            self.documents['/issues/comments/'+str(row['id'])]=row
        self.documents['/git/commits/'+SHA]['parents']=[{'sha':INITIAL_SHA}]
        self.documents['/git/commits/'+INITIAL_SHA]={'sha':INITIAL_SHA,'tree':{'sha':INITIAL_TREE}}
        run={'id':RUN,'head_sha':SHA,'head_branch':BRANCH,'run_attempt':1,
            'event':'workflow_dispatch','status':'in_progress','conclusion':None,
            'repository':{'id':admission.REPO_ID,'full_name':admission.REPO},
            'head_repository':{'id':admission.REPO_ID},'path':'.github/workflows/rc6-unified-candidate-tests.yml',
            'display_title':'RC6 material '+gate+' @ '+SHA}
        self.documents['/actions/runs/'+str(RUN)]=run
        self.documents['/actions/runs/'+str(RUN)+'/attempts/1']=run
        self.documents['/actions/runs/'+str(RUN)+'/attempts/1/jobs?per_page=100']={'total_count':1,'jobs':[
            {'id':800,'run_id':RUN,'status':'in_progress','labels':['ubuntu-24.04'],
             'runner_group_name':'GitHub Actions','runner_name':'GitHub Actions 1'}]}
        self.documents[''].update(private=False)

    def rewrite_pair(self,**changes):
        first=admission.fields(self.launch['body']);twin=admission.fields(self.twin['body'])
        first.update(changes);twin.update(changes)
        self.twin['body']=body(twin)
        first['OWNER_RECEIPT_473_SHA256']=custody.digest(self.twin['body'].encode())
        self.launch['body']=body(first)

    def pins(self):
        anchors=admission.administrative_anchors(OWNER,get=self.read,now=NOW)
        auth,scope,prior=admission.diagnostic_launch_control_fields(self.launch,SHA,TREE,OWNER,self.gate,
            get=self.read,anchors=anchors,now=NOW)
        return {'schema':admission.DIAGNOSTIC_ADMISSION_SCHEMA,'authorization_schema':admission.DIAGNOSTIC_AUTHORIZATION_SCHEMA,
            'status':'ADMITTED_DIAGNOSTIC_NOT_STARTED','source_sha':SHA,'source_tree':TREE,'gate':self.gate,
            'owner_session':OWNER,'qualification_claimed':False,'launch_receipt_url':self.launch['html_url'],
            'launch_body_sha256':custody.digest(self.launch['body'].encode()),
            'source_manifest_sha256':auth['SOURCE_MANIFEST_SHA256'],'read_contract_sha256':auth['READ_CONTRACT_SHA256'],
            'scope':scope,'capability_prerequisite':prior,
            'diagnostic_owner_scope':admission.successor_diagnostic_scope(auth,OWNER,anchors),
            'contract_revision_binding':admission._diagnostic_revision_pointer(auth,self.launch,SHA,TREE,OWNER,self.gate)}


def test_released_v2_diagnostic_has_real_in_process_acceptance_without_native_green(monkeypatch):
    from scripts import rc6_contract_revisions as revisions
    transport=ReleasedDiagnosticApi(monkeypatch)
    receipt=transport.pins()
    auth=admission.fields(transport.launch['body'])
    context=admission._authenticate_successor_revision(auth,transport.launch,SHA,TREE,OWNER,'capacity-probe',
        reader=transport.read,now=NOW)
    assert revisions.validate_revision_approval(context,revision='PRIVATE_PID_BOUNDARY_V2',scope='capacity-probe',
        source_sha=SHA,source_tree=TREE,contract_sha256=transport.contract_digest,now=NOW) is context
    assert receipt['diagnostic_owner_scope']['branch']==BRANCH
    assert receipt['contract_revision_binding']['serialized_pointer_grants_authority'] is False
    assert receipt['qualification_claimed'] is False
    assert not hasattr(context,'native_equivalence_proved') and not hasattr(context,'G0_green')
    with pytest.raises(ValueError,match='AUTHENTICATED_OWNER_ACCEPTANCE'):
        revisions.validate_revision_approval(receipt['contract_revision_binding'],revision='PRIVATE_PID_BOUNDARY_V2',
            scope='capacity-probe',source_sha=SHA,source_tree=TREE,contract_sha256=transport.contract_digest,now=NOW)


@pytest.mark.parametrize('fault',['economic','native_prerequisites','no_contract_acceptance','automatic_origin',
    'scope','source','tree','branch','legacy_branch','digest','edited','twin_operation','twin_digest',
    'twin_mode','deploy','green','expired','no_lifecycle','foreign_active'])
def test_v2_diagnostic_rejects_rebound_automatic_and_one_sided_decisions(monkeypatch,fault):
    transport=ReleasedDiagnosticApi(monkeypatch)
    if fault in ('economic','native_prerequisites'):
        name='RC6_DEVELOPMENT_CHECKS_AUTHORIZATION' if fault=='economic' else 'RC6_NATIVE_PREREQUISITES_AUTHORIZATION'
        transport.launch['body']=transport.launch['body'].replace('RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION',name)
    elif fault=='edited':transport.launch['updated_at']=lease.text_stamp(NOW-timedelta(seconds=19))
    elif fault in ('twin_operation','twin_digest','twin_mode'):
        changes={'twin_operation':('RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION','NOT_GRANTED'),
                 'twin_digest':('CONTRACT_DOCUMENT_SHA256','f'*64),'twin_mode':('MODE','REAL')}
        key,value=changes[fault];fields=admission.fields(transport.twin['body']);fields[key]=value
        transport.twin['body']=body(fields)
        fields=admission.fields(transport.launch['body']);fields['OWNER_RECEIPT_473_SHA256']=custody.digest(transport.twin['body'].encode())
        transport.launch['body']=body(fields)
    elif fault=='foreign_active':
        for issue in (471,473):transport.rows[issue].append(scoped_row(issue,900000+issue,NOW-timedelta(seconds=1),PREVIOUS))
    else:
        changes={'no_contract_acceptance':('RC6_CONTRACT_REVISION_AUTHORIZATION','NOT_GRANTED'),
            'automatic_origin':('CONTRACT_DECISION_ORIGIN','AUTOMATIC_CI'), 'scope':('CONTRACT_SCOPE','capacity-calibration'),
            'source':('SOURCE_SHA','c'*40),'tree':('SOURCE_TREE','c'*40),'branch':('SOURCE_BRANCH','fix/foreign'),
            'legacy_branch':('SOURCE_BRANCH',admission.DIAGNOSTIC_BRANCH),'digest':('CONTRACT_DOCUMENT_SHA256','broken'),
            'deploy':('DEPLOY_AUTHORIZATION','APPROVED'),'green':('CONTRACT_GATES_ALREADY_GREEN','true'),
            'expired':('SOURCE_LEASE_EXPIRES_UTC',lease.text_stamp(NOW)),
            'no_lifecycle':('CONTRACT_ACCEPTANCE_LIFECYCLE','LEGACY_EXPIRES_WITH_COMMENT')}
        key,value=changes[fault];transport.rewrite_pair(**{key:value})
    with pytest.raises(ValueError):transport.pins()


def test_v2_diagnostic_current_branch_lineage_does_not_touch_old_prs(monkeypatch):
    transport=ReleasedDiagnosticApi(monkeypatch);receipt=transport.pins();transport.calls.clear()
    source=admission.fresh_diagnostic_source(SHA,TREE,get=transport.read,candidate=receipt['diagnostic_owner_scope'])
    assert source['lineage_base_sha']==INITIAL_SHA and source['lineage_base_tree']==INITIAL_TREE
    assert source['wip_branch']==BRANCH and source['single_parent_fix_forward_lineage']==[SHA]
    assert not any(path.startswith('/pulls/') for path in transport.calls)
    transport.documents['/git/ref/heads/'+BRANCH]['object']['sha']='c'*40
    with pytest.raises(ValueError,match='BRANCH_HEAD_TREE_REBOUND'):
        admission.fresh_diagnostic_source(SHA,TREE,get=transport.read,candidate=receipt['diagnostic_owner_scope'])


@pytest.mark.parametrize('fault',['merge','base_tree','parent_identity','lineage_limit'])
def test_v2_source_cannot_infer_fix_forward_from_a_ref_or_synthetic_base(monkeypatch,fault):
    transport=ReleasedDiagnosticApi(monkeypatch);receipt=transport.pins()
    if fault=='merge':transport.documents['/git/commits/'+SHA]['parents'].append({'sha':'c'*40})
    elif fault=='base_tree':transport.documents['/git/commits/'+INITIAL_SHA]['tree']['sha']='d'*40
    elif fault=='parent_identity':transport.documents['/git/commits/'+INITIAL_SHA]['sha']='d'*40
    else:transport.documents['/git/commits/'+SHA]['parents']=[{'sha':SHA}]
    with pytest.raises(ValueError):
        admission.fresh_diagnostic_source(SHA,TREE,get=transport.read,candidate=receipt['diagnostic_owner_scope'])


def test_v2_polling_is_api_only_and_keeps_source_contract_branch_and_workstream_pins(monkeypatch):
    transport=ReleasedDiagnosticApi(monkeypatch);receipt=transport.pins();pins=admission.diagnostic_control_binding(receipt)
    def forbidden(*args,**kwargs):pytest.fail('polling must not read checkout/event/Source or mint a new approval')
    monkeypatch.setattr(admission,'actual_event',forbidden)
    monkeypatch.setattr(admission,'frozen_source_inventory',forbidden)
    monkeypatch.setattr(admission,'frozen_private_pid_contract',forbidden)
    monkeypatch.setattr(admission,'_authenticate_successor_revision',forbidden)
    result=admission.owned_diagnostic_control_scope(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
        owner_session=OWNER,gate='capacity-probe',now=NOW,get=transport.read,diagnostic_binding=pins,run_id=RUN,run_attempt=1)
    assert result['workstream_id']==WORKSTREAM and result['source']['wip_branch']==BRANCH
    assert result['qualification_claimed'] is False and result['DEPLOY_OWNER']=='NOT_ACQUIRED'
    assert result['diagnostic_binding']==pins
    pins['contract_revision_binding']['contract_sha256']='f'*64
    with pytest.raises(ValueError,match='PINS_REBOUND'):
        admission.owned_diagnostic_control_scope(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
            owner_session=OWNER,gate='capacity-probe',now=NOW,get=transport.read,diagnostic_binding=pins,run_id=RUN,run_attempt=1)


def test_v2_long_polling_needs_fresh_actual_lease_and_never_renews_old_contract_comment(monkeypatch):
    transport=ReleasedDiagnosticApi(monkeypatch);receipt=transport.pins();pins=admission.diagnostic_control_binding(receipt)
    later=NOW+timedelta(minutes=11)
    original=custody.digest(transport.launch['body'].encode())
    with pytest.raises(ValueError,match='LEASE_EXPIRED'):
        admission.owned_diagnostic_control_scope(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
            owner_session=OWNER,gate='capacity-probe',now=later,get=transport.read,diagnostic_binding=pins,run_id=RUN,run_attempt=1)
    for issue in (471,473):
        row=scoped_row(issue,700000+issue,later-timedelta(seconds=1),OWNER)
        transport.rows[issue].append(row);transport.documents['/issues/comments/'+str(row['id'])]=row
    result=admission.owned_diagnostic_control_scope(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
        owner_session=OWNER,gate='capacity-probe',now=later,get=transport.read,diagnostic_binding=pins,run_id=RUN,run_attempt=1)
    assert result['qualification_claimed'] is False and custody.digest(transport.launch['body'].encode())==original
    with pytest.raises(ValueError,match='LEASE_EXPIRED'):
        admission.diagnostic_launch_control_fields(transport.launch,SHA,TREE,OWNER,'capacity-probe',get=transport.read,now=later)


def test_v2_owner_revocation_cannot_be_hidden_by_later_same_source_lease(monkeypatch):
    transport=ReleasedDiagnosticApi(monkeypatch);receipt=transport.pins();pins=admission.diagnostic_control_binding(receipt)
    revoked=scoped_row(473,710473,NOW-timedelta(seconds=3),OWNER)
    revoked['body']+='CONTRACT_REVISION=PRIVATE_PID_BOUNDARY_V2\nCONTRACT_SCOPE=capacity-probe\nRC6_CONTRACT_REVISION_AUTHORIZATION=REVOKED\n'
    transport.rows[473].append(revoked)
    transport.rows[473].append(scoped_row(473,720473,NOW-timedelta(seconds=1),OWNER))
    with pytest.raises(ValueError,match='CONTRACT_OR_LAUNCH_REVOKED'):
        admission.owned_diagnostic_control_scope(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
            owner_session=OWNER,gate='capacity-probe',now=NOW,get=transport.read,diagnostic_binding=pins,run_id=RUN,run_attempt=1)


def test_v2_bootstrap_metadata_is_not_native_capability_or_green(monkeypatch):
    transport=ReleasedDiagnosticApi(monkeypatch,gate='capacity-calibration')
    with pytest.raises(ValueError,match='ACTUAL_CAPABILITY_ARTIFACT'):transport.pins()
    auth=admission.fields(transport.launch['body'])
    for reference in ({},{'artifact_id':1,'run_id':2,'run_attempt':1},
            {'source_sha':SHA,'source_tree':TREE,'contract_revision':'PRIVATE_PID_BOUNDARY_V2','contract_document_sha256':transport.contract_digest}):
        with pytest.raises(ValueError,match='EXACT_NATIVE_CAPABILITY_ARTIFACT'):
            admission.authenticate_capability_reference(reference,SHA,TREE,auth,reader=lambda path:pytest.fail('metadata-shaped object must not fetch'))


def capability_origin_fixture(transport):
    identifier,artifact_id=61,99
    stamp=lease.text_stamp(NOW)
    reference={'source_sha':SHA,'source_tree':TREE,'contract_revision':'PRIVATE_PID_BOUNDARY_V2',
        'contract_document_sha256':transport.contract_digest,'artifact_id':artifact_id,'run_id':identifier,
        'run_attempt':1,'artifact_digest':'sha256:'+'d'*64,'receipt_member':'diagnostic-result.json','receipt_sha256':'e'*64}
    run={'id':identifier,'head_sha':SHA,'head_branch':BRANCH,'run_attempt':1,'status':'completed','conclusion':'success',
        'path':'.github/workflows/rc6-unified-candidate-tests.yml','event':'workflow_dispatch',
        'display_title':'RC6 material capacity-probe @ '+SHA,'repository':{'id':admission.REPO_ID,'full_name':admission.REPO},
        'head_repository':{'id':admission.REPO_ID},'run_started_at':stamp,'updated_at':stamp}
    artifact={'id':artifact_id,'digest':reference['artifact_digest'],'expired':False,'size_in_bytes':1,
        'workflow_run':{'id':identifier,'head_sha':SHA,'repository_id':admission.REPO_ID,'head_repository_id':admission.REPO_ID},
        'created_at':stamp,'expires_at':'2099-10-10T00:00:00Z'}
    jobs={'total_count':1,'jobs':[{'id':123,'run_id':identifier,'status':'completed','conclusion':'success',
        'labels':['ubuntu-24.04'],'runner_group_name':'GitHub Actions','runner_name':'GitHub Actions 1',
        'started_at':stamp,'completed_at':stamp}]}
    transport.documents.update({'/actions/artifacts/'+str(artifact_id):artifact,'/actions/runs/'+str(identifier):run,
        '/actions/runs/'+str(identifier)+'/attempts/1':run,
        '/actions/runs/'+str(identifier)+'/attempts/1/jobs?per_page=100':jobs})
    return reference,run,artifact,jobs


def test_v2_capability_origin_precheck_explicitly_leaves_native_payload_unproved(monkeypatch):
    transport=ReleasedDiagnosticApi(monkeypatch,gate='capacity-calibration')
    reference,_,_,_=capability_origin_fixture(transport)
    auth=admission.fields(transport.launch['body'])
    result=admission.authenticate_capability_reference(reference,SHA,TREE,auth,reader=transport.read,now=NOW)
    assert result['artifact_origin_authenticated'] is True
    assert result['native_payload_verification_required'] is True
    assert result['capability_proved_from_metadata'] is False and result['qualification_claimed'] is False
    assert not any('/dispatches' in path or '/contents/' in path for path in transport.calls)


@pytest.mark.parametrize('fault',['source','tree','contract','artifact_digest','expired','run_red','run_branch','wrong_gate','alternate_runner'])
def test_v2_capability_origin_requires_exact_source_digest_actions_probe_and_standard_runner(monkeypatch,fault):
    transport=ReleasedDiagnosticApi(monkeypatch,gate='capacity-calibration')
    reference,run,artifact,jobs=capability_origin_fixture(transport)
    auth=admission.fields(transport.launch['body'])
    if fault in ('source','tree','contract'):
        key={'source':'source_sha','tree':'source_tree','contract':'contract_document_sha256'}[fault]
        reference[key]='f'*(64 if fault=='contract' else 40)
    elif fault=='artifact_digest':artifact['digest']='sha256:'+'f'*64
    elif fault=='expired':artifact['expired']=True
    elif fault=='run_red':run['conclusion']='failure'
    elif fault=='run_branch':run['head_branch']=admission.DIAGNOSTIC_BRANCH
    elif fault=='wrong_gate':run['display_title']='RC6 material cheap @ '+SHA
    else:jobs['jobs'][0]['labels']=['self-hosted']
    with pytest.raises(ValueError):
        admission.authenticate_capability_reference(reference,SHA,TREE,auth,reader=transport.read,now=NOW)


def test_v2_full_admission_requires_exact_source_digest_before_returning_a_pointer(monkeypatch,tmp_path):
    from scripts import rc6_architectural_gates as gates
    from scripts import rc6_private_pid_boundary as boundary
    transport=ReleasedDiagnosticApi(monkeypatch)
    inventory={'files':[{'path':boundary.MEMBER,'sha256':custody.digest((admission.ROOT/boundary.MEMBER).read_bytes())}]}
    transport.rewrite_pair(SOURCE_MANIFEST_SHA256=custody.digest(admission.wire(inventory)))
    monkeypatch.setattr(admission,'frozen_source_inventory',lambda *a:inventory)
    monkeypatch.setattr(gates,'productive_contract',lambda repo:PLAN)
    monkeypatch.setattr(admission,'api',transport.read)
    event={'repository':{'id':admission.REPO_ID,'full_name':admission.REPO},'ref':BRANCH,
        'sender':{'login':'mbalbo2023'},'inputs':{'gate':'capacity-probe','source_sha':SHA,'source_tree':TREE,
            'owner_session':OWNER,'launch_receipt_url':transport.launch['html_url']}}
    event_path=tmp_path/'event.json';event_path.write_bytes(admission.wire(event))
    environment={'GITHUB_REPOSITORY':admission.REPO,'GITHUB_REPOSITORY_ID':str(admission.REPO_ID),
        'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_EVENT_PATH':str(event_path),'GITHUB_RUN_ID':str(RUN),
        'GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':SHA,'GITHUB_WORKFLOW_SHA':SHA,'GITHUB_REF':'refs/heads/'+BRANCH,
        'GITHUB_WORKFLOW_REF':admission.REPO+'/.github/workflows/rc6-unified-candidate-tests.yml@refs/heads/'+BRANCH,
        'RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux','RUNNER_ARCH':'X64'}
    for key,value in environment.items():monkeypatch.setenv(key,value)
    receipt=admission.admit_diagnostic(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
        owner_session=OWNER,gate='capacity-probe',now=NOW)
    assert receipt['schema']==admission.DIAGNOSTIC_ADMISSION_SCHEMA and receipt['status']=='ADMITTED_DIAGNOSTIC_NOT_STARTED'
    assert receipt['workstream_id']==WORKSTREAM and receipt['source']['wip_branch']==BRANCH
    assert receipt['material_gates']==[] and receipt['final_candidate_eligible'] is False
    context=admission.reauthenticate_diagnostic_revision(receipt,get=transport.read,now=NOW)
    assert context.source_sha==SHA and context.contract_sha256==boundary.contract_sha256()
    later=NOW+timedelta(minutes=11)
    for issue in (471,473):
        row=scoped_row(issue,800000+issue,later-timedelta(seconds=1),OWNER)
        transport.rows[issue].append(row)
        transport.documents['/issues/comments/'+str(row['id'])]=row
    capture=admission.admit_diagnostic(source_sha=SHA,source_tree=TREE,
        launch_receipt_url=transport.launch['html_url'],owner_session=OWNER,
        gate='capacity-probe',now=later,native_finished=True)
    assert capture['status']=='CAPTURE_ONLY_NATIVE_ALREADY_FINISHED'
    assert admission.diagnostic_control_binding(capture)==admission.diagnostic_control_binding(receipt)
    with pytest.raises(ValueError,match='SOURCE_POINTER_REQUIRED'):
        admission.reauthenticate_diagnostic_revision(capture,get=transport.read,now=later)
    with pytest.raises(ValueError,match='LEASE_EXPIRED'):
        admission.admit_diagnostic(source_sha=SHA,source_tree=TREE,
            launch_receipt_url=transport.launch['html_url'],owner_session=OWNER,
            gate='capacity-probe',now=later)
    for issue in (471,473):
        transport.rows[issue].pop()
        transport.documents.pop('/issues/comments/'+str(800000+issue))
    receipt['contract_revision_binding']['approval_body_sha256']='f'*64
    with pytest.raises(ValueError,match='SOURCE_POINTER_REBOUND'):
        admission.reauthenticate_diagnostic_revision(receipt,get=transport.read,now=NOW)
    transport.rewrite_pair(CONTRACT_DOCUMENT_SHA256='f'*64)
    with pytest.raises(ValueError,match='EXACT_SOURCE_CONTRACT_REBOUND'):
        admission.admit_diagnostic(source_sha=SHA,source_tree=TREE,launch_receipt_url=transport.launch['html_url'],
            owner_session=OWNER,gate='capacity-probe',now=NOW)

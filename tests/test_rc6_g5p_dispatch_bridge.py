"""Offline regressions for the owner-bound canonical Actions activation path."""
from datetime import datetime, timezone
from email.message import Message
import hashlib
import json
import os
import urllib.error
from unittest import TestCase, main
from unittest.mock import patch

from scripts import rc6_g5p_dispatch_bridge as bridge
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_architectural_gates as gates
from scripts.rc6_g5p_model import DOCUMENT_SHA256


class BridgeBodyTests(TestCase):
    def test_junit_summary_rejects_extra_subtest_reports_without_case_identity(self):
        rows = [{'nodeid': 'tests/test_bridge.py::test_a', 'classname': 'tests.test_bridge', 'name': 'test_a'},
                {'nodeid': 'tests/test_bridge.py::test_b', 'classname': 'tests.test_bridge', 'name': 'test_b'}]
        collection = {'items': rows, 'pytest_exit_code': 0}
        execution = {'items': rows, 'pytest_exit_code': 0}
        xml = ('<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0">'
               '<testcase classname="tests.test_bridge" name="test_a" />'
               '<testcase classname="tests.test_bridge" name="test_b" />'
               '</testsuite></testsuites>')
        with self.assertRaisesRegex(ValueError, 'GATE_JUNIT_SUMMARY_MISMATCH'):
            gates.validate_junit(collection, execution, xml.encode())
        self.assertEqual(gates.validate_junit(collection, execution,
            xml.replace('tests="3"', 'tests="2"').encode())['cases'], 2)

    def test_g5p_bridge_requires_fresh_paired_decision_before_dispatch(self):
        now = datetime(2026, 10, 12, 9, 4, tzinfo=timezone.utc)
        source, tree, owner = "a" * 40, "b" * 40, "CODEX_OWNER"
        root = "https://github.com/mbalbo2023/Porota-trading/issues/"
        def fixture(expires="2026-10-12T09:18:00Z", *, claimed_source=None):
            common = "\n".join((
                "CONTRACT_ACCEPTANCE_LIFECYCLE=IMMUTABLE_DECISION_LIVE_OWNER_REVOCATION_V1",
                "RC6_CONTRACT_REVISION_AUTHORIZATION=APPROVED",
                "CONTRACT_REVISION=PAPER_V3_ONLY_G5P_V1",
                "CONTRACT_SCOPE=G5P_V3_MATERIAL",
                "CONTRACT_DOCUMENT_SHA256=" + DOCUMENT_SHA256,
                "SOURCE_SHA=" + (claimed_source or source), "SOURCE_TREE=" + tree,
                "SESSION_SUCCESSOR=" + owner, "SOURCE_LEASE_EXPIRES_UTC=" + expires))
            twin_body = common + "\n"
            first_body = (common + "\nOWNER_RECEIPT_473=16\nOWNER_RECEIPT_473_SHA256=" +
                          hashlib.sha256(twin_body.encode()).hexdigest() + "\n")
            def row(issue, identifier, body):
                return {"id": identifier, "body": body,
                    "created_at": "2026-10-12T09:00:00Z",
                    "updated_at": "2026-10-12T09:00:00Z",
                    "html_url": root + str(issue) + "#issuecomment-" + str(identifier),
                    "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/" + str(issue),
                    "user": {"login": "mbalbo2023"}}
            return {"/issues/comments/15": row(471, 15, first_body),
                    "/issues/comments/16": row(473, 16, twin_body)}
        auth = {"RELEASE_PROFILE": "PAPER_V3_ONLY",
                "G5P_REVISION_APPROVAL_URL": root + "471#issuecomment-15"}
        rows = fixture()
        bridge.fresh_g5p_decision_for_dispatch(auth, source, tree, owner, now,
                                                get=rows.__getitem__)
        for changed in (fixture("2026-10-12T09:03:00Z"),
                        fixture("2026-10-12T09:08:00Z"),
                        fixture(claimed_source="c" * 40)):
            with self.assertRaisesRegex(
                    ValueError, "BRIDGE_G5P_FRESH_PAIRED_DECISION_WITH_QUEUE_MARGIN_REQUIRED"):
                bridge.fresh_g5p_decision_for_dispatch(auth, source, tree, owner, now,
                                                        get=changed.__getitem__)
        changed = fixture()
        changed["/issues/comments/16"]["body"] += "TAMPERED=true\n"
        with self.assertRaisesRegex(ValueError, "BRIDGE_G5P_ACCEPTANCE_PAIR_REBOUND"):
            bridge.fresh_g5p_decision_for_dispatch(auth, source, tree, owner, now,
                                                    get=changed.__getitem__)
        bridge.fresh_g5p_decision_for_dispatch({"RELEASE_PROFILE": "FULL_G5"}, source,
            tree, owner, now, get=lambda path: self.fail("FULL_G5 fetched PAPER decision"))

    def test_bridge_http_failure_receipt_preserves_status_without_secret_or_url(self):
        headers = Message()
        headers['X-RateLimit-Remaining'] = '0'
        headers['X-RateLimit-Reset'] = '1791753600'
        headers['Retry-After'] = '60'
        headers['Authorization'] = 'Bearer secret'
        error = urllib.error.HTTPError('https://api.github.com/private?token=secret',
                                       403, 'secret body', headers, None)
        row = bridge.failure_row(error)
        self.assertEqual(row['reason'], 'BRIDGE_HTTP_STATUS_403')
        self.assertEqual(row['http_status'], 403)
        self.assertEqual(row['rate_limit_remaining'], 0)
        self.assertEqual(row['rate_limit_reset_epoch'], 1791753600)
        self.assertEqual(row['retry_after_seconds'], 60)
        self.assertFalse(row['gate_pass_claimed'])
        self.assertNotIn('secret', json.dumps(row))
        self.assertNotIn('api.github.com', json.dumps(row))
        self.assertEqual(bridge.failure_row(TimeoutError('private'))['reason'],
                         'BRIDGE_TRANSPORT_TIMEOUT')

    def test_bot_dispatch_requires_successful_exact_owner_bridge(self):
        event={'sender':{'login':'github-actions[bot]'},
            'ref':'refs/heads/fix/rc6-g5p-paper-v3-20261011',
            'inputs':{'bridge_run_id':'123','source_sha':'a'*40,'source_tree':'b'*40,
                'launch_receipt_url':'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
                'owner_session':'CODEX_OWNER','gate':'G5P-acquire'}}
        run={'id':123,'run_attempt':1,'event':'pull_request','head_sha':'a'*40,
            'head_branch':'fix/rc6-g5p-paper-v3-20261011',
            'path':'.github/workflows/rc6-unified-candidate-tests.yml',
            'actor':{'login':'mbalbo2023'},'repository':{'id':admission.REPO_ID},
            'head_repository':{'id':admission.REPO_ID},'status':'completed','conclusion':'success',
            'pull_requests':[{'number':498,'head':{'sha':'a'*40},'base':{'ref':admission.BASE}}]}
        jobs={'jobs':[{'name':'Dispatch exact owner-approved stage without gate credit',
                       'conclusion':'success'},
                      {'name':'RC6 G0-G1-cheap-dual @ '+ 'a'*40,'conclusion':'skipped'}]}
        def get(path):
            return run if path=='/actions/runs/123/attempts/1' else jobs
        scope={'pr':498,'branch':'fix/rc6-g5p-paper-v3-20261011','base_branch':admission.BASE}
        with (patch.object(admission,'comment',return_value={'body':'SOURCE_TREE='+'b'*40}),
              patch.object(admission,'administrative_anchors',return_value={}),
              patch.object(admission,'launch_fields',return_value={'SOURCE_PR':'498'}),
              patch.object(admission,'candidate_scope',return_value=scope)):
            result=admission.authenticated_dispatch_bridge(event,'G5P-acquire',get=get,sleep=lambda _:None)
            self.assertEqual(result['bridge_run_id'],123)
            self.assertTrue(result['bridge_only_no_gate_credit'])
            for changed in (dict(run,head_sha='b'*40),dict(run,actor={'login':'github-actions[bot]'}),
                            dict(run,conclusion='failure'),
                            dict(run,pull_requests=[{'number':492,'head':{'sha':'a'*40},
                                'base':{'ref':admission.BASE}}])):
                with self.assertRaises(ValueError):
                    admission.authenticated_dispatch_bridge(event,'G5P-acquire',
                        get=lambda path:changed if '/actions/runs/123/attempts/1'==path else jobs,
                        sleep=lambda _:None)
            with self.assertRaisesRegex(ValueError,'BRIDGE_JOB_OR_CHEAP_GATE_NOT_ISOLATED'):
                admission.authenticated_dispatch_bridge(event,'G5P-acquire',
                    get=lambda path:run if '/actions/runs/123/attempts/1'==path else
                        {'jobs':[jobs['jobs'][0],dict(jobs['jobs'][1],conclusion='success')]},
                    sleep=lambda _:None)
            bad=dict(event,inputs=dict(event['inputs'],source_tree='c'*40))
            with self.assertRaisesRegex(ValueError,'BOT_DISPATCH_EXACT_LAUNCH_TREE_REQUIRED'):
                admission.authenticated_dispatch_bridge(bad,'G5P-acquire',get=get)
        manual={'sender':{'login':'mbalbo2023'},'inputs':{}}
        self.assertIsNone(admission.authenticated_dispatch_bridge(manual,'G5P-acquire',get=get))
        manual['inputs']['bridge_run_id']='123'
        with self.assertRaisesRegex(ValueError,'CANNOT_BORROW_BRIDGE_ORIGIN'):
            admission.authenticated_dispatch_bridge(manual,'G5P-acquire',get=get)

    def test_one_material_stage_and_pointer_required(self):
        body = ("PR technical summary\n"
                "RC6_DISPATCH_GATE=G5P-acquire\n"
                "RC6_DISPATCH_LAUNCH_RECEIPT_URL=https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1\n"
                "RC6_DISPATCH_OWNER_SESSION=CODEX_OWNER\n")
        self.assertEqual(bridge.body_fields(body)["RC6_DISPATCH_GATE"], "G5P-acquire")
        for changed in (body.replace("G5P-acquire", "Horizon"),
                        body + "RC6_DISPATCH_GATE=G5P-certify\n",
                        body.replace("RC6_DISPATCH_OWNER_SESSION=CODEX_OWNER\n", "")):
            with self.assertRaises(ValueError):
                bridge.body_fields(changed)

    def test_dispatch_http_204_is_request_only_and_payload_is_exact(self):
        row = {"status": "READY_TO_REQUEST_DISPATCH_NOT_GATE_PASS",
               "branch": "fix/candidate", "gate": "G5P-acquire",
               "bridge_run_id": 123,
               "source_sha": "a" * 40, "source_tree": "b" * 40,
               "launch_receipt_url": "https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1",
               "owner_session": "CODEX_OWNER"}
        captured = []
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, count): return b""
        def post(request, timeout):
            captured.append((request, timeout))
            return Response()
        with patch.dict(os.environ, {"GH_TOKEN": "test-only"}):
            result = bridge.dispatch(row, opener=post)
        self.assertEqual(result["status"], "DISPATCH_REQUEST_ACCEPTED_NOT_GATE_PASS")
        self.assertFalse(result["G5P_GREEN"])
        self.assertEqual(result["DEPLOY_OWNER"], "NOT_ACQUIRED")
        request, timeout = captured.pop()
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("/actions/workflows/rc6-unified-candidate-tests.yml/dispatches",
                      request.full_url)
        self.assertEqual(json.loads(request.data)["inputs"], {
            "source_sha": "a" * 40, "source_tree": "b" * 40,
            "gate": "G5P-acquire", "launch_receipt_url": row["launch_receipt_url"],
            "owner_session": "CODEX_OWNER", "bridge_run_id": "123"})
        self.assertEqual(timeout, 30)

    def test_bridge_refuses_reopen_without_exact_pr_controls(self):
        event = {"action": "reopened", "pull_request": {"draft": True,
            "body": "RC6_DISPATCH_GATE=G5P-acquire\n"}}
        with self.assertRaises(ValueError):
            bridge.prepare(event_reader=lambda gate: (event, {"sha256": "x"}),
                           checkout=lambda sha: "b" * 40)


class HistoricalOwnerReleaseTests(TestCase):
    def row(self, identifier, minute, body):
        when = f"2026-10-11T17:{minute:02d}:00Z"
        return {"id": identifier, "body": body, "created_at": when,
                "updated_at": when, "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/471",
                "html_url": f"https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-{identifier}",
                "user": {"login": "mbalbo2023"}}

    def test_explicit_bare_release_is_bound_to_prior_session_and_workstream(self):
        foreign = self.row(1, 1, "WORKSTREAM_ID=WS-RC6-OTHER\nSESSION=CHAT_OTHER\nWRITE_OWNER=CHAT_OTHER\nRELEASED=false\n")
        release = self.row(2, 2, "WORKSTREAM_ID=WS-RC6-OTHER\nSESSION=CHAT_OTHER\nWRITE_OWNER=RELEASED\nRELEASED=true\n")
        owner = self.row(3, 3, "WORKSTREAM_ID=WS-RC6-G5P-MATERIAL\nSESSION_SUCCESSOR=CODEX_OWNER\n"
            "WRITE_OWNER=CODEX_OWNER\nINTEGRATION_OWNER=CODEX_OWNER\nDEPLOY_OWNER=NOT_ACQUIRED\n"
            "RELEASED=false\nSOURCE_SHA=" + "a" * 40 + "\nSOURCE_TREE=" + "b" * 40 + "\n"
            "SOURCE_LEASE_EXPIRES_UTC=2026-10-11T17:20:00Z\nMODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        now = datetime(2026, 10, 11, 17, 4, tzinfo=timezone.utc)
        result = admission.latest_writer([foreign, release, owner], 471,
            "a" * 40, "b" * 40, "CODEX_OWNER", now)
        self.assertTrue(result["latest_status_by_owner"]["CHAT_OTHER"]["released"])
        forged = dict(release, body=release["body"].replace("WS-RC6-OTHER", "WS-RC6-IMPOSTOR"))
        with self.assertRaisesRegex(ValueError, "BARE_RELEASE_SESSION_AND_WORKSTREAM"):
            admission.latest_writer([foreign, forged, owner], 471,
                "a" * 40, "b" * 40, "CODEX_OWNER", now)

    def test_historical_maintenance_deploy_owner_requires_explicit_release(self):
        host = self.row(1, 1, "WORKSTREAM_ID=WS-RC6-HOST\nSESSION=CHAT_HOST\n"
            "WRITE_OWNER=CHAT_HOST\nDEPLOY_OWNER=CHAT_HOST\nRELEASED=false\n")
        released = self.row(2, 2, "WORKSTREAM_ID=WS-RC6-HOST\nSESSION=CHAT_HOST\n"
            "WRITE_OWNER=RELEASED\nDEPLOY_OWNER=RELEASED\nRELEASED=true\n")
        owner = self.row(3, 3, "WORKSTREAM_ID=WS-RC6-G5P-MATERIAL\nSESSION_SUCCESSOR=CODEX_OWNER\n"
            "WRITE_OWNER=CODEX_OWNER\nINTEGRATION_OWNER=CODEX_OWNER\nDEPLOY_OWNER=NOT_ACQUIRED\n"
            "RELEASED=false\nSOURCE_SHA=" + "a" * 40 + "\nSOURCE_TREE=" + "b" * 40 + "\n"
            "SOURCE_LEASE_EXPIRES_UTC=2026-10-11T17:20:00Z\nMODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        now = datetime(2026, 10, 11, 17, 4, tzinfo=timezone.utc)
        admission.latest_writer([host, released, owner], 471,
            "a" * 40, "b" * 40, "CODEX_OWNER", now)
        writer_release_only = dict(released, body=released["body"].replace(
            "DEPLOY_OWNER=RELEASED", "DEPLOY_OWNER=NOT_ACQUIRED"))
        with self.assertRaisesRegex(ValueError, "ACTIVE_DEPLOY_OWNER"):
            admission.latest_writer([host, writer_release_only, owner], 471,
                "a" * 40, "b" * 40, "CODEX_OWNER", now)

    def test_exact_separate_sre_scope_can_coexist_but_any_overlap_blocks(self):
        owner=self.row(1,1,"WORKSTREAM_ID=WS-RC6-G5P-MATERIAL\nSESSION_SUCCESSOR=CODEX_OWNER\n"
            "WRITE_OWNER=CODEX_OWNER\nINTEGRATION_OWNER=CODEX_OWNER\nDEPLOY_OWNER=NOT_ACQUIRED\n"
            "RELEASED=false\nSOURCE_SHA="+"a"*40+"\nSOURCE_TREE="+"b"*40+"\n"
            "SOURCE_LEASE_EXPIRES_UTC=2026-10-11T17:20:00Z\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        sre=self.row(2,2,"WORKSTREAM_ID=WS-RC6-SRE-REPAIR-DISK-DEEP-20261011\n"
            "SESSION_SUCCESSOR=WORK_RC6_SRE_20261011_1740Z\n"
            "WRITE_OWNER=WORK_RC6_SRE_20261011_1740Z\n"
            "INTEGRATION_OWNER=WORK_RC6_SRE_20261011_1740Z\n"
            "DEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\n"
            "BRANCH=ops/rc6-sre-deep-20261011\nSOURCE_SHA="+"c"*40+"\n"
            "BASE_SHA="+admission.BASE_SHA+"\n"
            "PR=https://github.com/mbalbo2023/Porota-trading/pull/494\n"
            "PATHS=.github/workflows/rc6-sre-*-20261011.yml,ops/rc6_deploy_critical_control_plane.sh,"
            "scripts/ops/rc6_sre_*,tests/test_rc6_sre_*,docs/ops/RC6_SRE_*\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        now=datetime(2026,10,11,17,3,tzinfo=timezone.utc)
        valid=admission.latest_writer([owner,sre],471,"a"*40,"b"*40,"CODEX_OWNER",now)
        self.assertEqual(valid['comment_id'],1)
        self.assertEqual(valid['authenticated_disjoint_active_writers'][0]['comment_id'],2)
        for changed in (sre['body'].replace('docs/ops/RC6_SRE_*',
                            'scripts/rc6_material_carrier.py'),
                        sre['body'].replace('pull/494','pull/492'),
                        sre['body'].replace('ops/rc6-sre-deep-20261011',
                            'fix/rc6-g5p-paper-v3-20261011')):
            with self.assertRaisesRegex(
                    ValueError,'FOREIGN_ACTIVE_OR_UNKNOWN_WRITER'):
                admission.latest_writer([owner,dict(sre,body=changed)],471,
                    "a"*40,"b"*40,"CODEX_OWNER",now)

    def test_sre_deploy_release_then_scoped_writer_reacquisition(self):
        session="WORK_RC6_SRE_20261011_1740Z"
        workstream="WS-RC6-SRE-REPAIR-DISK-DEEP-20261011"
        prefix=f"WORKSTREAM_ID={workstream}\nSESSION={session}\n"
        active=self.row(1,1,prefix+f"WRITE_OWNER={session}\n"
            f"INTEGRATION_OWNER={session}\nDEPLOY_OWNER={session}\nRELEASED=false\n")
        release=self.row(2,2,prefix+"WRITE_OWNER=RELEASED\n"
            "INTEGRATION_OWNER=NOT_ACQUIRED\nDEPLOY_OWNER=RELEASED\nRELEASED=true\n")
        reacquire=self.row(3,3,prefix+f"SESSION_SUCCESSOR={session}\n"
            f"WRITE_OWNER={session}\nINTEGRATION_OWNER={session}\n"
            "DEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\n"
            "BRANCH=ops/rc6-sre-deep-20261011\nSOURCE_SHA="+"c"*40+"\n"
            "BASE_SHA="+admission.BASE_SHA+"\n"
            "PR=https://github.com/mbalbo2023/Porota-trading/pull/494\n"
            "PATHS=scripts/ops/rc6_sre_*,tests/test_rc6_sre_*,docs/ops/RC6_SRE_*\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        owner=self.row(4,4,"WORKSTREAM_ID=WS-RC6-G5P-MATERIAL\n"
            "SESSION_SUCCESSOR=CODEX_OWNER\nWRITE_OWNER=CODEX_OWNER\n"
            "INTEGRATION_OWNER=CODEX_OWNER\nDEPLOY_OWNER=NOT_ACQUIRED\n"
            "RELEASED=false\nSOURCE_SHA="+"a"*40+"\nSOURCE_TREE="+"b"*40+"\n"
            "SOURCE_LEASE_EXPIRES_UTC=2026-10-11T17:20:00Z\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        rows=[active,release,reacquire,owner]
        now=datetime(2026,10,11,17,5,tzinfo=timezone.utc)
        result=admission.latest_writer(rows,471,"a"*40,"b"*40,"CODEX_OWNER",now)
        self.assertEqual(result['authenticated_disjoint_active_writers'][0]['comment_id'],3)
        with self.assertRaisesRegex(ValueError,'ACTIVE_DEPLOY_OWNER'):
            admission.latest_writer([active,dict(release,body=release['body'].replace(
                'DEPLOY_OWNER=RELEASED','DEPLOY_OWNER=NOT_ACQUIRED')),reacquire,owner],
                471,"a"*40,"b"*40,"CODEX_OWNER",now)
        with self.assertRaisesRegex(ValueError,'FOREIGN_ACTIVE_OR_UNKNOWN_WRITER'):
            admission.latest_writer([active,release,dict(reacquire,body=reacquire['body'].replace(
                'PATHS=scripts/ops/rc6_sre_*,tests/test_rc6_sre_*,docs/ops/RC6_SRE_*\n','')),owner],
                471,"a"*40,"b"*40,"CODEX_OWNER",now)

    def test_exact_historical_sre_apply_release_inherits_only_prior_paths(self):
        session="WORK_RC6_SRE_20261011_1740Z"
        workstream="WS-RC6-SRE-REPAIR-DISK-DEEP-20261011"
        scoped=self.row(6112057220,1,f"WORKSTREAM_ID={workstream}\n"
            f"SESSION_SUCCESSOR={session}\nWRITE_OWNER={session}\n"
            f"INTEGRATION_OWNER={session}\nDEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\n"
            "BRANCH=ops/rc6-sre-deep-20261011\nSOURCE_SHA="+"c"*40+"\n"
            "BASE_SHA="+admission.BASE_SHA+"\n"
            "PR=https://github.com/mbalbo2023/Porota-trading/pull/494\n"
            "PATHS=scripts/ops/rc6_sre_*,tests/test_rc6_sre_*,docs/ops/RC6_SRE_*\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        active=self.row(2,2,f"WORKSTREAM_ID={workstream}\nSESSION={session}\n"
            f"WRITE_OWNER={session}\nINTEGRATION_OWNER={session}\n"
            f"DEPLOY_OWNER={session}\nRELEASED=false\n")
        release_body=(f"WORKSTREAM_ID={workstream}\nSESSION={session}\nSESSION_SUCCESSOR={session}\n"
            f"WRITE_OWNER={session}\nINTEGRATION_OWNER={session}\n"
            "DEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\n"
            "BRANCH=ops/rc6-sre-deep-20261011\nSOURCE_SHA="+"d"*40+"\n"
            "SCOPE=Only SRE source/evidence; no further host mutation authorized by this receipt.\n"
            "APPLY_RUN=38163172961\nAPPLY_ATTEMPT=1\nAPPLY_ARTIFACT=11710093973\n"
            "APPLY_ZIP_SHA256=0287ef958307e29ba9165b99fe3406bd6b05b8bd75ab5b4bb88e09d54bc21ee5\n"
            "APPLY_JSON_SHA256=0042f2d17ed63cbb00004eeec2c1aa5787c5a9e7f3f04d57ab93914d0e8f0680\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\nPAIR_ISSUE=471\n")
        release=self.row(6112200132,3,release_body)
        owner=self.row(4,4,"WORKSTREAM_ID=WS-RC6-G5P-MATERIAL\n"
            "SESSION_SUCCESSOR=CODEX_OWNER\nWRITE_OWNER=CODEX_OWNER\n"
            "INTEGRATION_OWNER=CODEX_OWNER\nDEPLOY_OWNER=NOT_ACQUIRED\n"
            "RELEASED=false\nSOURCE_SHA="+"a"*40+"\nSOURCE_TREE="+"b"*40+"\n"
            "SOURCE_LEASE_EXPIRES_UTC=2026-10-11T17:20:00Z\n"
            "MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n")
        expected=(6112057220,6112200132,hashlib.sha256(release_body.encode()).hexdigest())
        now=datetime(2026,10,11,17,5,tzinfo=timezone.utc)
        with patch.dict(admission._SRE_APPLY_RELEASE,{471:expected}):
            result=admission.latest_writer([scoped,active,release,owner],471,
                "a"*40,"b"*40,"CODEX_OWNER",now)
            self.assertEqual(result['authenticated_sre_deploy_release_records'][0]
                ['prior_scope_comment_id'],6112057220)
            self.assertEqual(result['authenticated_disjoint_active_writers'][0]['comment_id'],6112200132)
            for altered in (release_body.replace('APPLY_ATTEMPT=1','APPLY_ATTEMPT=2'),
                            release_body.replace('no further host mutation','host mutation')):
                with self.assertRaisesRegex(
                        ValueError,'SRE_EXACT_PAIRED_APPLY_RELEASE_REBOUND'):
                    admission.latest_writer([scoped,active,dict(release,body=altered),owner],471,
                        "a"*40,"b"*40,"CODEX_OWNER",now)

    def test_exact_disjoint_sre_successor_releases_old_deploy_owner(self):
        old=admission._SRE_OLD_OWNER
        new=admission._SRE_NEW_OWNER
        workstream='WS-RC6-SRE-REPAIR-DISK-DEEP-20261011'
        paths='scripts/ops/rc6_sre_*,tests/test_rc6_sre_*,docs/ops/RC6_SRE_*'
        scope=self.row(6112057220,1,f'WORKSTREAM_ID={workstream}\n'
            f'SESSION={old}\nSESSION_SUCCESSOR={old}\nWRITE_OWNER={old}\n'
            f'INTEGRATION_OWNER={old}\nDEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\n'
            'BRANCH=ops/rc6-sre-deep-20261011\nSOURCE_SHA='+ 'c'*40+'\n'
            'BASE_SHA='+admission.BASE_SHA+'\n'
            'PR=https://github.com/mbalbo2023/Porota-trading/pull/494\n'
            f'PATHS={paths}\nMODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n')
        active=self.row(2,2,f'WORKSTREAM_ID={workstream}\nSESSION={old}\n'
            f'WRITE_OWNER={old}\nINTEGRATION_OWNER={old}\nDEPLOY_OWNER={old}\nRELEASED=false\n')
        apply_body=(f'WORKSTREAM_ID={workstream}\nSESSION={old}\nSESSION_SUCCESSOR={old}\n'
            f'WRITE_OWNER={old}\nINTEGRATION_OWNER={old}\nDEPLOY_OWNER=NOT_ACQUIRED\n'
            'RELEASED=false\nBRANCH=ops/rc6-sre-deep-20261011\nSOURCE_SHA='+ 'd'*40+'\n'
            'SCOPE=Only SRE source/evidence; no further host mutation authorized by this receipt.\n'
            'APPLY_RUN=38163172961\nAPPLY_ATTEMPT=1\nAPPLY_ARTIFACT=11710093973\n'
            'APPLY_ZIP_SHA256=0287ef958307e29ba9165b99fe3406bd6b05b8bd75ab5b4bb88e09d54bc21ee5\n'
            'APPLY_JSON_SHA256=0042f2d17ed63cbb00004eeec2c1aa5787c5a9e7f3f04d57ab93914d0e8f0680\n'
            'MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\nPAIR_ISSUE=471\n')
        apply=self.row(6112200132,3,apply_body)
        transfer_body=(f'WORKSTREAM_ID={workstream}\nSESSION={old}\nSESSION_SUCCESSOR={new}\n'
            'WRITE_OWNER=RELEASED\nINTEGRATION_OWNER=RELEASED\nDEPLOY_OWNER=RELEASED\n'
            'RELEASED=true\nBRANCH=ops/rc6-sre-deep-20261011\n'
            'SOURCE_SHA=f61d30dce6d86b7bec086a65f8b7a656797b9cf4\n'
            'APPLY_RUN=38163172961\nAPPLY_ARTIFACT=11710093973\n'
            'POST_RUN=38163695961\nPOST_ARTIFACT=11711606030\n'
            'PAPER=PRODUCTION_PAPER\nreal_orders_sent=0\nPAIR_ISSUE=471\n')
        transfer=self.row(6115863183,4,transfer_body)
        successor_body=(f'WORKSTREAM_ID={workstream}\nSESSION={new}\nSESSION_SUCCESSOR={new}\n'
            f'WRITE_OWNER={new}\nINTEGRATION_OWNER={new}\nDEPLOY_OWNER=NOT_ACQUIRED\n'
            'RELEASED=false\nBRANCH=ops/rc6-sre-deep-20261011\n'
            'SOURCE_SHA=f61d30dce6d86b7bec086a65f8b7a656797b9cf4\n'
            'BASE_BRANCH='+admission.BASE+'\nBASE_SHA='+admission.BASE_SHA+'\n'
            'PR=https://github.com/mbalbo2023/Porota-trading/pull/494\n'
            f'PATHS={paths}\nMODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n'
            'PAIR_ISSUE=471\n')
        successor=self.row(6115867410,5,successor_body)
        owner=self.row(4,6,'WORKSTREAM_ID=WS-RC6-G5P-MATERIAL\n'
            'SESSION_SUCCESSOR=CODEX_OWNER\nWRITE_OWNER=CODEX_OWNER\n'
            'INTEGRATION_OWNER=CODEX_OWNER\nDEPLOY_OWNER=NOT_ACQUIRED\nRELEASED=false\n'
            'SOURCE_SHA='+ 'a'*40+'\nSOURCE_TREE='+ 'b'*40+'\n'
            'SOURCE_LEASE_EXPIRES_UTC=2026-10-11T17:20:00Z\n'
            'MODE=PRODUCTION_PAPER / SIMULATION\nreal_orders_sent=0\n')
        now=datetime(2026,10,11,17,7,tzinfo=timezone.utc)
        prior=(6112057220,6112200132,hashlib.sha256(apply_body.encode()).hexdigest())
        succession=(6115863183,hashlib.sha256(transfer_body.encode()).hexdigest(),
                    6115867410,hashlib.sha256(successor_body.encode()).hexdigest())
        with patch.dict(admission._SRE_APPLY_RELEASE,{471:prior}),patch.dict(
                admission._SRE_SUCCESSION,{471:succession}):
            rows=[scope,active,apply,transfer,successor,owner]
            result=admission.latest_writer(rows,471,'a'*40,'b'*40,'CODEX_OWNER',now)
            self.assertTrue(result['latest_status_by_owner'][old]['released'])
            self.assertFalse(result['latest_status_by_owner'][new]['released'])
            self.assertEqual(result['authenticated_disjoint_active_writers'][0]['session'],new)
            with self.assertRaisesRegex(ValueError,'SRE_EXACT_DISJOINT_SUCCESSION_RELEASE_REBOUND'):
                admission.latest_writer([scope,active,apply,dict(transfer,body=transfer_body.replace(
                    f'SESSION={old}',f'SESSION={new}')),successor,owner],471,
                    'a'*40,'b'*40,'CODEX_OWNER',now)
            with self.assertRaisesRegex(ValueError,'SRE_EXACT_DISJOINT_SUCCESSION_WRITER_REBOUND'):
                admission.latest_writer([scope,active,apply,transfer,dict(successor,
                    body=successor_body.replace(paths,'scripts/rc6_material_carrier.py')),owner],471,
                    'a'*40,'b'*40,'CODEX_OWNER',now)
            with self.assertRaisesRegex(ValueError,'SRE_EXACT_DISJOINT_SUCCESSION_WRITER_REBOUND'):
                admission.latest_writer([scope,active,apply,successor,owner],471,
                    'a'*40,'b'*40,'CODEX_OWNER',now)


if __name__ == "__main__":
    main()

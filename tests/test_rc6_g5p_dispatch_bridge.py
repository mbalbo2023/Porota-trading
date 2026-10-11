"""Offline regressions for the owner-bound canonical Actions activation path."""
from datetime import datetime, timezone
import json
import os
from unittest import TestCase, main
from unittest.mock import patch

from scripts import rc6_g5p_dispatch_bridge as bridge
from scripts import rc6_material_pr_admission as admission


class BridgeBodyTests(TestCase):
    def test_one_material_stage_and_pointer_required(self):
        body = ("PR technical summary\n"
                "RC6_DISPATCH_GATE=G5P-acquire\n"
                "RC6_DISPATCH_LAUNCH_RECEIPT_URL=https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1\n"
                "RC6_DISPATCH_OWNER_SESSION=CODEX_OWNER\n")
        self.assertEqual(bridge.body_fields(body)["RC6_DISPATCH_GATE"], "G5P-acquire")
        for changed in (body.replace("G5P-acquire", "Horizon"),
                        body + "RC6_DISPATCH_GATE=G5P-certify\n",
                        body.replace("RC6_DISPATCH_OWNER_SESSION=CODEX_OWNER\n", "")):
            with self.subTest(changed=changed[-45:]), self.assertRaises(ValueError):
                bridge.body_fields(changed)

    def test_dispatch_http_204_is_request_only_and_payload_is_exact(self):
        row = {"status": "READY_TO_REQUEST_DISPATCH_NOT_GATE_PASS",
               "branch": "fix/candidate", "gate": "G5P-acquire",
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
            "owner_session": "CODEX_OWNER"})
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
            with self.subTest(changed=changed),self.assertRaisesRegex(
                    ValueError,'FOREIGN_ACTIVE_OR_UNKNOWN_WRITER'):
                admission.latest_writer([owner,dict(sre,body=changed)],471,
                    "a"*40,"b"*40,"CODEX_OWNER",now)


if __name__ == "__main__":
    main()

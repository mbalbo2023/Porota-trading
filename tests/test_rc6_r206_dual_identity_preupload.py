"""R206 RED-first contracts for a future trusted-main probe publisher.

Offline synthetic evidence only. This file deliberately does not implement a
publisher, execute Docker, invoke Actions, publish artifacts, or release PAPER.
The expected REDs name missing trusted APIs; the two witnesses establish why
R163's upstream-only REST identity and a mutable upload path are insufficient.
"""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import tempfile
import unittest

from scripts import rc6_r163_trusted_postrun_byte_audit as trusted


UPSTREAM_ID = 991
UPSTREAM_ATTEMPT = 2
PUBLISHER_ID = 2002
PUBLISHER_ATTEMPT = 3
MAIN_SHA = "290e831e735e391120a926b8480572cb51dfa768"
UPSTREAM_SHA = "a" * 40
UPSTREAM_TREE = "b" * 40
PUBLISHER_WORKFLOW = ".github/workflows/rc6-r163-postrun-trusted-main.yml"
PUBLISHER_NAME = "RC6 R163 Trusted Main Postrun Bytes (AUDIT ONLY)"


def _existing_upstream_fixture() -> dict:
    """Reuse the actual R163 test fixture, not a second TAR/ZIP parser."""
    fixture = Path(__file__).with_name(
        "test_rc6_r163_trusted_postrun_byte_audit.py"
    )
    return runpy.run_path(str(fixture))["_evidence"]()


def _publisher_receipts(*, attempt: int = PUBLISHER_ATTEMPT) -> tuple[dict, dict]:
    """Literal REST-like publisher identity, intentionally distinct from PR."""
    publisher = {
        "id": PUBLISHER_ID,
        "run_attempt": PUBLISHER_ATTEMPT,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_run",
        "name": PUBLISHER_NAME,
        "path": PUBLISHER_WORKFLOW,
        "head_sha": MAIN_SHA,
        "head_branch": "main",
        "repository": {"full_name": trusted.REPOSITORY},
        "head_repository": {"full_name": trusted.REPOSITORY},
    }
    attempt_record = dict(
        publisher,
        run_attempt=attempt,
        run_started_at="2026-11-12T08:00:00Z",
        created_at="2026-11-12T08:00:01Z",
        updated_at="2026-11-12T08:05:00Z",
    )
    return publisher, attempt_record


class DualIdentityPreuploadContracts(unittest.TestCase):
    def _source_and_publisher(self) -> tuple[dict, dict, dict]:
        upstream = _existing_upstream_fixture()
        # Actual R205 upstream intentionally fails at exit79. It may wake the
        # publisher, but must never be treated as a successful release proof.
        upstream["event"]["workflow_run"]["conclusion"] = "failure"
        upstream["run"]["conclusion"] = "failure"
        upstream["attempt"]["conclusion"] = "failure"
        publisher, publisher_attempt = _publisher_receipts()
        return upstream, publisher, publisher_attempt

    def test_r163_upstream_only_verifier_rejects_a_publisher_run_identity(self) -> None:
        """Current R163 must not silently substitute publisher P/N for PR U/M."""
        upstream = _existing_upstream_fixture()
        upstream["expected_run_id"] = PUBLISHER_ID
        upstream["expected_run_attempt"] = PUBLISHER_ATTEMPT
        with self.assertRaisesRegex(
            trusted.PostrunEvidenceRed, "^EVENT_RUN_MISMATCH$"
        ):
            trusted.verify_postrun_bytes(**upstream)

    def test_r206_trusted_handoff_binds_two_distinct_authenticated_attempts(self) -> None:
        """RED: future publisher must bind U/M candidate and P/N main, not one run."""
        upstream, publisher, publisher_attempt = self._source_and_publisher()
        verify_identity = getattr(trusted, "verify_trusted_publisher_identity", None)
        self.assertIsNotNone(verify_identity, "R206_MISSING_DUAL_IDENTITY_VERIFIER")
        identity = verify_identity(
            upstream_event=upstream["event"],
            upstream_run=upstream["run"],
            upstream_attempt=upstream["attempt"],
            pr=upstream["pr"],
            commit=upstream["commit"],
            publisher_run=publisher,
            publisher_attempt=publisher_attempt,
            expected_upstream_run_id=UPSTREAM_ID,
            expected_upstream_attempt=UPSTREAM_ATTEMPT,
            expected_publisher_run_id=PUBLISHER_ID,
            expected_publisher_attempt=PUBLISHER_ATTEMPT,
        )
        self.assertEqual(identity["candidate_sha"], UPSTREAM_SHA)
        self.assertEqual(identity["candidate_tree_sha"], UPSTREAM_TREE)
        self.assertEqual(identity["upstream_run_id"], UPSTREAM_ID)
        self.assertEqual(identity["upstream_attempt"], UPSTREAM_ATTEMPT)
        self.assertEqual(identity["publisher_main_sha"], MAIN_SHA)
        self.assertEqual(identity["publisher_run_id"], PUBLISHER_ID)
        self.assertEqual(identity["publisher_attempt"], PUBLISHER_ATTEMPT)
        self.assertIs(identity["release_go"], False)

    def test_r206_trusted_handoff_rejects_publisher_attempt_n_minus_one(self) -> None:
        """RED: REST attempt N-1 cannot stand in for publisher attempt N."""
        upstream, publisher, _ = self._source_and_publisher()
        _, replayed_attempt = _publisher_receipts(attempt=PUBLISHER_ATTEMPT - 1)
        verify_identity = getattr(trusted, "verify_trusted_publisher_identity", None)
        self.assertIsNotNone(verify_identity, "R206_MISSING_DUAL_IDENTITY_VERIFIER")
        with self.assertRaisesRegex(
            trusted.PostrunEvidenceRed, "^PUBLISHER_ATTEMPT_MISMATCH$"
        ):
            verify_identity(
                upstream_event=upstream["event"],
                upstream_run=upstream["run"],
                upstream_attempt=upstream["attempt"],
                pr=upstream["pr"],
                commit=upstream["commit"],
                publisher_run=publisher,
                publisher_attempt=replayed_attempt,
                expected_upstream_run_id=UPSTREAM_ID,
                expected_upstream_attempt=UPSTREAM_ATTEMPT,
                expected_publisher_run_id=PUBLISHER_ID,
                expected_publisher_attempt=PUBLISHER_ATTEMPT,
            )

    def test_same_uid_post_seal_inode_swap_changes_later_path_upload_bytes(self) -> None:
        """Local witness: sealed bytes do not bind a later path-based read."""
        public = b"RC6_PUBLIC_SYNTHETIC_ONLY"
        private_canary = b"RC6_FAKE_PRIVATE_CANARY_NEVER_UPLOAD"
        with tempfile.TemporaryDirectory(prefix="rc6-r206-race-") as tmp:
            root = Path(tmp)
            evidence = root / "native.tar"
            replacement = root / "replacement"
            evidence.write_bytes(public)
            with evidence.open("rb") as sealed_handle:
                sealed_inode = os.fstat(sealed_handle.fileno()).st_ino
                preview_sealed = sealed_handle.read()
                replacement.write_bytes(private_canary)
                # Both regular files belong to this process UID; no symlink is
                # required to substitute a previously checked pathname.
                os.replace(replacement, evidence)
                bytes_read_later_by_path = evidence.read_bytes()
                self.assertNotEqual(sealed_inode, evidence.stat().st_ino)
                self.assertEqual(preview_sealed, public)
                self.assertEqual(bytes_read_later_by_path, private_canary)
                self.assertIn(private_canary, bytes_read_later_by_path)

    def test_trusted_preupload_refuses_same_uid_candidate_writer_reachability(self) -> None:
        """RED: preupload must reject a candidate writer that can swap upload paths."""
        with tempfile.TemporaryDirectory(prefix="rc6-r206-boundary-") as tmp:
            assert_isolated = getattr(
                trusted, "assert_trusted_preupload_writer_isolation", None
            )
            self.assertIsNotNone(assert_isolated, "R206_MISSING_PREUPLOAD_WRITER_BOUNDARY")
            with self.assertRaisesRegex(
                trusted.PostrunEvidenceRed, "^PREUPLOAD_SAME_UID_WRITER$"
            ):
                assert_isolated(
                    publisher_uid=os.getuid(),
                    candidate_writer_uid=os.getuid(),
                    publisher_staging_root=Path(tmp),
                    candidate_can_access_staging=True,
                )


if __name__ == "__main__":
    unittest.main()

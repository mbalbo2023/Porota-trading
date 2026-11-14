"""R305 offhost digest witness: no SSH, Docker, sudo or live GitHub call."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import traceback
from contextlib import redirect_stdout
from unittest import TestCase, mock

from scripts import rc6_r305_manifest_witness_offhost as witness


MAIN_SHA = "a" * 40
MAIN_TREE = "b" * 40
CANDIDATE_SHA = "c" * 40
CANDIDATE_TREE = "d" * 40
ROLES = ("approval", "custody", "helper", "profiles", "unit_template")
ARTIFACT = "rc6-r305-root-manifest-digest-witness.json"


def _canonical(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def _manifest(**overrides):
    doc = {
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1",
        "candidate_sha": CANDIDATE_SHA,
        "candidate_tree": CANDIDATE_TREE,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "files": {role: str(index + 1) * 64
                  for index, role in enumerate(ROLES)},
    }
    doc.update(overrides)
    return _canonical(doc)


def _generate(raw=None, **changes):
    content = _manifest() if raw is None else raw
    args = {
        "expected_sha256": hashlib.sha256(content).hexdigest(),
        "main_sha": MAIN_SHA,
        "main_tree": MAIN_TREE,
        "run_id": "43350000000",
        "run_attempt": "1",
        "candidate_tree_getter": lambda sha: CANDIDATE_TREE
                                 if sha == CANDIDATE_SHA else "",
    }
    args.update(changes)
    return witness.build_witness(content, **args)


def _fake_connection(raw, *, status=200):
    response = io.BytesIO(raw)
    response.status = status
    connection = mock.Mock()
    connection.getresponse.return_value = response
    return connection


class RootManifestWitnessTests(TestCase):
    def test_correct_witness_exact_schema_identity_and_digest(self):
        raw = _manifest()
        result = _generate(raw)
        doc = json.loads(result)
        self.assertEqual(set(doc), {
            "schema", "run_id", "run_attempt", "trusted_main_sha",
            "trusted_main_tree", "manifest_schema", "manifest_sha256",
            "candidate_sha", "candidate_tree", "verification_result",
            "paper_mode", "real_orders_allowed",
        })
        self.assertEqual(doc["schema"],
                         "RC6_R305_ROOT_MANIFEST_INDEPENDENT_DIGEST_WITNESS_V1")
        self.assertEqual(doc["run_id"], "43350000000")
        self.assertEqual(doc["run_attempt"], "1")
        self.assertEqual(doc["trusted_main_sha"], MAIN_SHA)
        self.assertEqual(doc["trusted_main_tree"], MAIN_TREE)
        self.assertEqual(doc["manifest_schema"], "RC6_ROOT_INSTALL_MANIFEST_V1")
        self.assertEqual(doc["manifest_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(doc["candidate_sha"], CANDIDATE_SHA)
        self.assertEqual(doc["candidate_tree"], CANDIDATE_TREE)
        self.assertEqual(doc["verification_result"], "PASS")
        self.assertEqual(doc["paper_mode"], "PRODUCTION_PAPER")
        self.assertIs(doc["real_orders_allowed"], False)
        self.assertTrue(result.endswith(b"\n"))

    def test_release_d_oversized_manifest_is_not_root_authority(self):
        with self.assertRaises(witness.WitnessHold):
            _generate(b"{" + b"x" * 101201 + b"}")

    def test_noncanonical_and_duplicate_key_documents_rejected(self):
        raw = _manifest()
        for altered in (
            raw[:-1], raw + b"\n",
            raw.replace(b',', b', ', 1),
            raw.replace(b'"schema":', b'"schema":"RELEASE_D","schema":', 1),
        ):
            with self.subTest(altered=altered[:30]):
                with self.assertRaises(witness.WitnessHold):
                    _generate(altered)

    def test_missing_or_extra_roles_never_pass(self):
        for files in (
            {"approval": "1" * 64},
            {**{r: "1" * 64 for r in ROLES}, "legacy_d": "1" * 64},
        ):
            with self.assertRaises(witness.WitnessHold):
                _generate(_manifest(files=files))

    def test_root_manifest_paper_safety_is_strict(self):
        for changes in (
            {"paper_mode": "LIVE"},
            {"real_orders_allowed": True},
            {"candidate_sha": "x" * 40},
            {"candidate_tree": "x" * 40},
            {"schema": "RC6_RELEASE_D"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(witness.WitnessHold):
                    _generate(_manifest(**changes))

    def test_digest_and_candidate_git_tree_are_independent_checks(self):
        with self.assertRaises(witness.WitnessHold):
            _generate(expected_sha256="f" * 64)
        with self.assertRaises(witness.WitnessHold):
            _generate(candidate_tree_getter=lambda _sha: "e" * 40)
        with self.assertRaises(witness.WitnessHold):
            _generate(candidate_tree_getter=lambda _sha: None)

    def test_failed_git_api_does_not_leak_token_in_exception_chain(self):
        def faulty(_sha):
            raise OSError("SECRET_GITHUB_TOKEN_IN_ERROR")
        with self.assertRaises(witness.WitnessHold) as caught:
            _generate(candidate_tree_getter=faulty)
        self.assertNotIn("SECRET_GITHUB_TOKEN_IN_ERROR",
                         "".join(traceback.format_exception(caught.exception)))

    def test_bad_main_identity_or_attempt_fails_before_git_api(self):
        getter = mock.Mock()
        for change in (
            {"main_sha": "invalid"},
            {"main_tree": "notatree"},
            {"run_id": "0"},
            {"run_attempt": "-1"},
            {"run_attempt": "1.5"},
            {"expected_sha256": "ABCDEF"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(witness.WitnessHold):
                    _generate(candidate_tree_getter=getter, **change)
        getter.assert_not_called()

    def test_fixed_artifact_file_created_atomically_no_overwrite(self):
        raw = _generate()
        with TemporaryDirectory() as folder:
            path = Path(folder) / ARTIFACT
            witness.publish_witness(path, raw)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(witness.WitnessHold):
                witness.publish_witness(path, b"second")
            self.assertEqual(path.read_bytes(), raw)

    def test_dispatch_actor_ref_repository_and_sha_all_required(self):
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
            "GITHUB_ACTOR": "mbalbo2023",
            "GITHUB_TRIGGERING_ACTOR": "mbalbo2023",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": MAIN_SHA,
            "GITHUB_RUN_ID": "43350000000",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_TOKEN": "fixture-gh-token",
            "GITHUB_WORKSPACE": "/tmp/example-checkout",
            "RC6_ROOT_INSTALL_MANIFEST_B64": base64.b64encode(_manifest()).decode(),
            "RC6_ROOT_INSTALL_MANIFEST_SHA256": hashlib.sha256(
                _manifest()).hexdigest(),
        }
        self.assertEqual(witness.validate_dispatch_env(env)["main_sha"], MAIN_SHA)
        for key, bad in (
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REPOSITORY", "forged/repo"),
            ("GITHUB_ACTOR", "untrusted"),
            ("GITHUB_TRIGGERING_ACTOR", "other-operator"),
            ("GITHUB_REF", "refs/heads/product"),
            ("GITHUB_SHA", "invalid"),
            ("GITHUB_RUN_ATTEMPT", "0"),
            ("RC6_ROOT_INSTALL_MANIFEST_B64", "bad%%%base64"),
            ("GITHUB_TOKEN", ""),
        ):
            changed = dict(env, **{key: bad})
            with self.subTest(key=key):
                with self.assertRaises(witness.WitnessHold):
                    witness.validate_dispatch_env(changed)

    def test_github_candidate_tree_fetch_is_fixed_repo_and_bounded(self):
        connection = _fake_connection(json.dumps({
            "sha": CANDIDATE_SHA, "tree": {"sha": CANDIDATE_TREE},
        }).encode())
        with mock.patch("http.client.HTTPSConnection",
                        return_value=connection) as factory:
            self.assertEqual(witness.fetch_candidate_tree(
                CANDIDATE_SHA, "fixture-gh-token"), CANDIDATE_TREE)
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[0], "api.github.com")
        self.assertEqual(
            connection.request.call_args.args[1],
            "/repos/mbalbo2023/Porota-trading/git/commits/" +
            CANDIDATE_SHA,
        )
        self.assertEqual(connection.request.call_args.args[0], "GET")
        self.assertEqual(connection.request.call_args.kwargs["headers"][
            "Authorization"], "Bearer fixture-gh-token")
        self.assertEqual(factory.call_args.kwargs["timeout"], 10)
        connection.close.assert_called_once()

    def test_github_commit_with_utf8_message_still_validates_tree(self):
        payload = json.dumps({
            "sha": CANDIDATE_SHA, "tree": {"sha": CANDIDATE_TREE},
            "message": "corrección de índices PAPER",
        }, ensure_ascii=False).encode("utf-8")
        with mock.patch("http.client.HTTPSConnection",
                        return_value=_fake_connection(payload)):
            self.assertEqual(witness.fetch_candidate_tree(
                CANDIDATE_SHA, "fixture-gh-token"), CANDIDATE_TREE)

    def test_checkout_tree_reads_real_git_sha_and_tree_separately(self):
        checkout = Path(__file__).resolve().parents[1]
        actual_sha = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "--verify", "HEAD"],
            cwd=checkout, text=True).strip()
        actual_tree = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "--verify", "HEAD^{tree}"],
            cwd=checkout, text=True).strip()
        self.assertEqual(witness.checkout_tree(checkout, actual_sha), actual_tree)
        with self.assertRaises(witness.WitnessHold):
            witness.checkout_tree(checkout, "f" * 40)

    def test_api_redirect_never_forwards_bearer_token(self):
        valid = json.dumps({
            "sha": CANDIDATE_SHA, "tree": {"sha": CANDIDATE_TREE},
        }).encode()
        for code in (301, 302, 307, 308):
            connection = _fake_connection(valid, status=code)
            with self.subTest(code=code), \
                 mock.patch("http.client.HTTPSConnection",
                            return_value=connection) as factory:
                with self.assertRaises(witness.WitnessHold):
                    witness.fetch_candidate_tree(CANDIDATE_SHA, "fixture-gh-token")
            factory.assert_called_once()
            connection.request.assert_called_once()
            connection.close.assert_called_once()

    def test_unexpected_cli_exception_is_not_rendered_with_secret(self):
        output = io.StringIO()
        with mock.patch.object(witness, "validate_dispatch_env",
                               side_effect=OSError("secret-do-not-echo")), \
             redirect_stdout(output):
            result = witness.main({})
        self.assertEqual(result, 79)
        self.assertIn("HOLD79", output.getvalue())
        self.assertNotIn("secret-do-not-echo", output.getvalue())

    def test_forged_or_oversized_github_api_response_rejected(self):
        for data in (
            json.dumps({"sha": "e" * 40, "tree": {"sha": CANDIDATE_TREE}}).encode(),
            json.dumps({"sha": CANDIDATE_SHA, "tree": {"sha": "bad"}}).encode(),
            b"x" * 65537,
        ):
            with self.subTest(size=len(data)):
                with mock.patch("http.client.HTTPSConnection",
                                return_value=_fake_connection(data)):
                    with self.assertRaises(witness.WitnessHold):
                        witness.fetch_candidate_tree(CANDIDATE_SHA, "fixture-gh-token")


if __name__ == "__main__":
    import unittest
    unittest.main()

"""OFFHOST provenance checks; external verification is not simulated as GO."""
import hashlib
import io
import json
import tarfile
import unittest
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import rc6_r326_root_bundle_provenance as producer


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture(helper=None):
    sources = {
        "scripts/rc6_oci_host_helper.py": b"#!/usr/bin/python3 -I\n# helper\n",
        "scripts/rc6_runtime_oci_custody.py": b"# custody\n",
        "cf_intraday_scalping.py": b"# observer one\n",
        "co_market_sessions_hf6.py": b"# observer two\n",
    }
    if helper is not None:
        sources["scripts/rc6_oci_host_helper.py"] = helper
    inner = {"schema_version": 1, "status": "GREEN", "file_count": 4,
             "files": [{"path": path, "sha256": sha(raw), "bytes": len(raw)}
                       for path, raw in sorted(sources.items())]}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, raw in {**sources, "POROTA_DEPLOY_BUNDLE_MANIFEST.json": canonical(inner)}.items():
            entry = tarfile.TarInfo(path)
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
    bundle = output.getvalue()
    outer = canonical({**inner, "bundle_sha256": sha(bundle)})
    boundary = producer.ExternalReleaseBoundary(
        candidate_sha="a" * 40, candidate_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64, image_id="sha256:" + "d" * 64,
        release_run_id=101, release_run_attempt=1, release_artifact_id=102,
        r252_status="TRUSTED_RELEASE_BYTES_BOUND", r163_status="BYTES_MATCH_EXPECTED",
        member_sha256=(("porota-deploy-bundle-v2.tgz", sha(bundle)),
                       ("porota-deploy-bundle-v2-manifest.json", sha(outer))),
        git_blobs=tuple((path, hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest())
                        for path, raw in sorted(sources.items())))
    return boundary, bundle, outer


def generated_fixture(boundary):
    context = {"daemon_id": "docker-rootful-rc6", "generation": "e" * 32,
               "root_stage": "/opt/porota-trading", "dashboard_env_sha256": "f" * 64,
               "observer_env_sha256": "1" * 64}
    paper = {"POROTA_RUNTIME_MODE": "PRODUCTION_PAPER", "PPI_ORDERS": "BLOCKED", "REAL_ORDERS_ALLOWED": "false"}
    roles = {"critical": {"environment": paper, "mounts": [], "env_file": None, "env_sha256": None}}
    for name in ("dashboard", "observer"):
        roles[name] = {"environment": paper, "mounts": [],
                       "env_file": "/var/lib/porota-rc6-oci-custody/generations/" + "e" * 32 + "/env/" + name + ".env",
                       "env_sha256": context[name + "_env_sha256"]}
    doc = {"schema_version": 1, "candidate_sha": boundary.candidate_sha,
           "artifact_digest": boundary.artifact_digest, "root_stage": context["root_stage"],
           "image_tag": "porota-trading-bot:17.0.0-rc6-candidate-" + boundary.candidate_sha,
           "observer_source_sha256": {"/app/cf_intraday_scalping.py": sha(b"# observer one\n"),
                                      "/app/co_market_sessions_hf6.py": sha(b"# observer two\n")}, "roles": roles}
    bindings = {"candidate_sha": boundary.candidate_sha, "candidate_tree": boundary.candidate_tree,
                "artifact_digest": boundary.artifact_digest, "image_id": boundary.image_id,
                "daemon_id": context["daemon_id"], "generation": context["generation"]}
    unit = ("\n".join("# " + key + "=" + value for key, value in bindings.items()) +
            "\n[Service]\nExecCondition=/bin/sh -c 'echo RC6_OCI_CUSTODY_HOLD79:CRITICAL_CUTOVER_NOT_AUTHORIZED >&2; exit 79'\n"
            "ExecStart=/usr/local/sbin/porota-rc6-oci-custody critical start-newborn --paper-only --source-sha " +
            boundary.candidate_sha + " --generation " + context["generation"] + "\n").encode()
    return context, canonical(doc), unit


class ProvenanceTest(unittest.TestCase):
    def test_authenticated_tar_still_rejects_duplicate_link_and_traversal_members(self):
        from dataclasses import replace
        boundary, bundle, outer = fixture()
        for name, kind in (("scripts/rc6_oci_host_helper.py", "duplicate"),
                           ("linked.py", "link"), ("../outside.py", "traversal")):
            output = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as original, \
                 tarfile.open(fileobj=output, mode="w:gz") as changed:
                for entry in original:
                    changed.addfile(entry, original.extractfile(entry))
                extra = tarfile.TarInfo(name)
                if kind == "link":
                    extra.type = tarfile.SYMTYPE
                    extra.linkname = "/outside"
                changed.addfile(extra)
            new_bundle = output.getvalue()
            document = json.loads(outer)
            document["bundle_sha256"] = sha(new_bundle)
            new_outer = canonical(document)
            # Model an authenticated but structurally invalid D; no generic
            # verification bypass is added to production for this fixture.
            rebound = replace(boundary, member_sha256=(("porota-deploy-bundle-v2.tgz", sha(new_bundle)),
                ("porota-deploy-bundle-v2-manifest.json", sha(new_outer))))
            with self.subTest(kind=kind), self.assertRaisesRegex(producer.ProvenanceHold, "TAR_MEMBER_SCOPE"):
                producer.extract_bound_sources(rebound, new_bundle, new_outer)

    def test_real_reviewed_r308_or_missing_consumer_is_fail_closed_without_skip(self):
        import scripts
        root = Path(os.environ.get("RC6_R326_TRUSTED_R308_TEST_ROOT", producer._trusted_dependency_root()))
        path = root / "rc6_oci_host_helper.py"
        if not path.is_file():
            with self.assertRaisesRegex(producer.ProvenanceHold, "MISSING_TRUSTED_R308_CONSUMER"):
                producer._trusted_consumer(b"not integrated")
            return
        helper = path.read_bytes()
        self.assertEqual(sha(helper), producer._R271_SHA256)
        boundary, bundle, outer = fixture(helper)
        sources = producer.extract_bound_sources(boundary, bundle, outer)
        context, _, _ = generated_fixture(boundary)
        with patch.object(producer, "_trusted_dependency_root", return_value=root), \
             patch.object(scripts, "__path__", [str(root), *scripts.__path__]):
            consumer = producer._trusted_consumer(helper)
            actual = consumer(candidate_sha=boundary.candidate_sha, candidate_tree=boundary.candidate_tree,
                artifact_digest=boundary.artifact_digest, image_id=boundary.image_id,
                daemon_id=context["daemon_id"], generation=context["generation"], root_stage=context["root_stage"],
                dashboard_env_sha256=context["dashboard_env_sha256"], observer_env_sha256=context["observer_env_sha256"],
                observer_source_sha256={"/app/" + path: sha(sources[path]) for path in
                    ("cf_intraday_scalping.py", "co_market_sessions_hf6.py")}, critical_source_sha=boundary.candidate_sha)
            producer.validate_generated_inputs(boundary, sources, actual.profiles_bytes, actual.unit_template_bytes, context)
            bad = json.loads(actual.profiles_bytes)
            bad["roles"]["critical"]["mounts"] = []
            for profiles, unit in ((canonical(bad), actual.unit_template_bytes),
                    (actual.profiles_bytes, actual.unit_template_bytes + b"ExecStartPre=/bin/sh -c 'unreviewed'\n")):
                with self.assertRaisesRegex(producer.ProvenanceHold, "R308_BYTE_EQUIVALENCE"):
                    producer.validate_generated_inputs(boundary, sources, profiles, unit, context)

    def test_exact_trusted_r308_equivalence_rejects_mount_and_shell_mutations(self):
        boundary, bundle, outer = fixture()
        sources = producer.extract_bound_sources(boundary, bundle, outer)
        context, profiles, unit = generated_fixture(boundary)
        document = json.loads(profiles)
        document["roles"]["critical"]["mounts"] = [{"source": "/reviewed", "target": "/reviewed", "readonly": True}]
        expected_profiles = canonical(document)
        expected = SimpleNamespace(profiles_bytes=expected_profiles, unit_template_bytes=unit,
                                   profiles_sha256=sha(expected_profiles), unit_template_sha256=sha(unit))
        # The slow/external boundary is availability of the separately reviewed
        # producer. The behavior under test is R326's exact-byte comparison.
        with patch.object(producer, "_trusted_consumer", create=True, return_value=lambda **_: expected):
            producer.validate_generated_inputs(boundary, sources, expected_profiles, unit, context)
            document["roles"]["critical"]["mounts"] = []
            for profile_input, unit_input in ((canonical(document), unit),
                    (expected_profiles, unit + b"ExecStartPre=/bin/sh -c 'unreviewed'\n")):
                with self.assertRaisesRegex(producer.ProvenanceHold, "R308_BYTE_EQUIVALENCE"):
                    producer.validate_generated_inputs(boundary, sources, profile_input, unit_input, context)

    def test_profiles_cannot_borrow_a_different_release_source_or_generation(self):
        boundary, bundle, outer = fixture()
        sources = producer.extract_bound_sources(boundary, bundle, outer)
        context = {"daemon_id": "docker-rootful-rc6", "generation": "e" * 32,
                   "root_stage": "/opt/porota-trading", "dashboard_env_sha256": "f" * 64,
                   "observer_env_sha256": "1" * 64}
        for profiles, unit in ((b"{}", b"[Service]\nExecStart=evil\n"),
                               (canonical({"candidate_sha": "0" * 40}), b"")):
            with self.assertRaises(producer.ProvenanceHold):
                producer.validate_generated_inputs(boundary, sources, profiles, unit, context)

    def test_real_trusted_r304_produces_six_immutable_sources_and_freshness_holds(self):
        # Standalone main intentionally lacks R304. Its already-audited additive
        # integration source may be supplied for this one real-boundary test.
        path = os.environ.get("RC6_R326_TRUSTED_R304_TEST_SOURCE")
        if path:
            raw = Path(path).read_bytes()
            self.assertEqual(sha(raw), "6711ad67f4de122488787b5587ff947801afed19fe0df4d551db4e65f638fea9")
            name = "scripts.rc6_r304_root_install_manifest_freeze"
            spec = importlib.util.spec_from_file_location(name, path)
            dependency = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {name: dependency}):
                spec.loader.exec_module(dependency)
        else:
            try:
                dependency = __import__("scripts.rc6_r304_root_install_manifest_freeze", fromlist=["freeze_root_install_manifest"])
            except ImportError:
                with self.assertRaisesRegex(producer.ProvenanceHold, "MISSING_TRUSTED_R304"):
                    producer._trusted_freezer()
                return  # meaningful fail-closed standalone CI, never a skip
        boundary, bundle, outer = fixture()
        context, profiles, unit = generated_fixture(boundary)
        raw = producer.extract_bound_sources(boundary, bundle, outer)
        source = {"helper": raw["helper"], "custody": raw["custody"], "profiles": profiles, "unit_template": unit}
        approval = {"schema_version": 1, "phase": "GENESIS", "candidate_sha": boundary.candidate_sha,
                    "candidate_tree": boundary.candidate_tree, "artifact_digest": boundary.artifact_digest,
                    "image_id": boundary.image_id, "daemon_id": context["daemon_id"], "generation": context["generation"],
                    "run_id": "101", "run_attempt": "1", "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
                    "expires_at": "2026-11-13T02:00:00Z", **{role + "_sha256": sha(value) for role, value in source.items()}}
        arguments = dict(boundary=boundary, bundle_bytes=bundle, outer_manifest_bytes=outer,
                         profiles_bytes=profiles, unit_template_bytes=unit, context=context,
                         approval_intent=approval, now=datetime(2026, 11, 13, 1, tzinfo=timezone.utc))
        expected = SimpleNamespace(profiles_bytes=profiles, unit_template_bytes=unit,
                                   profiles_sha256=sha(profiles), unit_template_sha256=sha(unit))
        with patch.object(producer, "_trusted_freezer", return_value=dependency.freeze_root_install_manifest), \
             patch.object(producer, "_trusted_consumer", return_value=lambda **_: expected):
            first = producer.propose_root_bundle(**arguments)
            second = producer.propose_root_bundle(**arguments)
            self.assertEqual(first.sources, second.sources)
            self.assertEqual(first.write_order[-1], "manifest.json")
            self.assertEqual(set(first.sources), set(first.write_order))
            self.assertEqual(first.manifest_sha256, sha(first.sources["manifest.json"]))
            self.assertFalse(first.host_go)
            self.assertFalse(first.profile_consumer_contract_verified)
            self.assertFalse(first.r309_verified or first.r305_pin_go or first.r321_install_go)
            with self.assertRaises(TypeError):
                first.sources["manifest.json"] = b"changed"
            for update in ({"expires_at": "2026-11-13T00:00:00Z"}, {"artifact_digest": "sha256:" + "0" * 64}):
                with self.assertRaises((producer.ProvenanceHold, dependency.FreezeHold)):
                    producer.propose_root_bundle(**{**arguments, "approval_intent": {**approval, **update}})

    def test_bound_sources_are_extracted_without_importing_candidate_code(self):
        boundary, bundle, outer = fixture()
        result = producer.extract_bound_sources(boundary, bundle, outer)
        self.assertEqual(result["helper"], b"#!/usr/bin/python3 -I\n# helper\n")
        self.assertEqual(set(result), {"helper", "custody", "cf_intraday_scalping.py", "co_market_sessions_hf6.py"})

    def test_missing_boundary_changed_tar_outer_or_git_blob_fails_closed(self):
        from dataclasses import replace
        boundary, bundle, outer = fixture()
        cases = ((None, bundle, outer), (boundary, bundle + b"changed", outer),
                 (boundary, bundle, outer + b"changed"),
                 (replace(boundary, git_blobs=tuple((path, "0" * 40) for path, _ in boundary.git_blobs)), bundle, outer),
                 (replace(boundary, r252_status="UNKNOWN"), bundle, outer))
        for args in cases:
            with self.subTest(kind=type(args[0]).__name__), self.assertRaises(producer.ProvenanceHold):
                producer.extract_bound_sources(*args)

    def test_absent_trusted_r304_never_produces_self_approved_bundle(self):
        with patch.object(producer, "import_module", side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(producer.ProvenanceHold, "MISSING_TRUSTED_R304"):
                producer._trusted_freezer()


if __name__ == "__main__":
    unittest.main()

"""OFFHOST provenance checks; external verification is not simulated as GO."""
import hashlib
import io
import json
import tarfile
import unittest
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
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


def real_bundle_arguments():
    helper = (producer._trusted_dependency_root() / "rc6_oci_host_helper.py").read_bytes()
    boundary, bundle, outer = fixture(helper)
    sources = producer.extract_bound_sources(boundary, bundle, outer)
    context, _, _ = generated_fixture(boundary)
    proposed = producer._trusted_consumer(helper)(
        candidate_sha=boundary.candidate_sha, candidate_tree=boundary.candidate_tree,
        artifact_digest=boundary.artifact_digest, image_id=boundary.image_id,
        daemon_id=context["daemon_id"], generation=context["generation"],
        root_stage=context["root_stage"],
        dashboard_env_sha256=context["dashboard_env_sha256"],
        observer_env_sha256=context["observer_env_sha256"],
        observer_source_sha256={"/app/" + name: sha(sources[name]) for name in
                                ("cf_intraday_scalping.py", "co_market_sessions_hf6.py")},
        critical_source_sha=boundary.candidate_sha)
    raw = {"helper": helper, "custody": sources["custody"],
           "profiles": proposed.profiles_bytes, "unit_template": proposed.unit_template_bytes}
    approval = {"schema_version": 1, "phase": "GENESIS", "candidate_sha": boundary.candidate_sha,
                "candidate_tree": boundary.candidate_tree, "artifact_digest": boundary.artifact_digest,
                "image_id": boundary.image_id, "daemon_id": context["daemon_id"],
                "generation": context["generation"], "run_id": "101", "run_attempt": "1",
                "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
                "expires_at": "2026-11-13T02:00:00Z",
                **{role + "_sha256": sha(value) for role, value in raw.items()}}
    return dict(boundary=boundary, bundle_bytes=bundle, outer_manifest_bytes=outer,
                profiles_bytes=proposed.profiles_bytes, unit_template_bytes=proposed.unit_template_bytes,
                context=context, approval_intent=approval,
                now=datetime(2026, 11, 13, 1, tzinfo=timezone.utc))


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

    def test_real_reviewed_r308_consumer_is_required_in_integrated_main(self):
        import scripts
        root = producer._trusted_dependency_root()
        path = root / "rc6_oci_host_helper.py"
        self.assertTrue(path.is_file(), "integrated trusted-main helper is required")
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

    def test_release_helper_must_equal_trusted_source_before_import(self):
        helper = (producer._trusted_dependency_root() / "rc6_oci_host_helper.py").read_bytes()
        with patch.object(producer, "_execute_reviewed") as imports:
            with self.assertRaisesRegex(producer.ProvenanceHold,
                                        "TRUSTED_R271_D_BLOB_MISMATCH"):
                producer._trusted_consumer(helper + b"\n# unreviewed D helper\n")
        imports.assert_not_called()

    def test_missing_or_altered_local_helper_fails_before_import(self):
        helper = (producer._trusted_dependency_root() / "rc6_oci_host_helper.py").read_bytes()
        for state in ("missing", "altered"):
            with self.subTest(state=state), TemporaryDirectory() as folder:
                root = Path(folder)
                if state == "altered":
                    (root / "rc6_oci_host_helper.py").write_bytes(
                        helper + b"\n# local source drift\n")
                with patch.object(producer, "_trusted_dependency_root", return_value=root), \
                     patch.object(producer, "_execute_reviewed") as imports:
                    with self.assertRaisesRegex(producer.ProvenanceHold,
                                                "MISSING_TRUSTED_R308_CONSUMER"):
                        producer._trusted_consumer(helper)
                imports.assert_not_called()

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
        arguments = real_bundle_arguments()
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
        for update in ({"expires_at": "2026-11-13T00:00:00Z"},
                       {"artifact_digest": "sha256:" + "0" * 64}):
            with self.assertRaisesRegex((producer.ProvenanceHold, RuntimeError),
                                        "ROOT_APPROVAL_R280_CONTRACT_MISMATCH|APPROVAL_D_CONTEXT_BINDING"):
                producer.propose_root_bundle(**{**arguments, "approval_intent": {
                    **arguments["approval_intent"], **update}})

    def test_foreign_namespace_sources_never_execute_before_rejection_or_use(self):
        import scripts
        arguments = real_bundle_arguments()
        names = ("rc6_oci_host_helper", "rc6_r308_root_launch_profile_freeze",
                 "rc6_host_rootowned_installer_contract", "rc6_r304_root_install_manifest_freeze")
        for target in names:
            with self.subTest(target=target), TemporaryDirectory() as folder:
                shadow = Path(folder) / "scripts"
                shadow.mkdir()
                marker = Path(folder) / "executed"
                (shadow / (target + ".py")).write_text(
                    "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('FOREIGN')\n")
                with patch.dict(sys.modules), patch.dict(scripts.__dict__), \
                     patch.object(sys, "path", [folder, *sys.path]):
                    for name in names:
                        sys.modules.pop("scripts." + name, None)
                        scripts.__dict__.pop(name, None)
                    with patch.object(scripts, "__path__", [str(shadow), str(producer._trusted_dependency_root())]):
                        try:
                            producer.propose_root_bundle(**arguments)
                        except (producer.ProvenanceHold, ImportError):
                            pass
                    self.assertFalse(marker.exists(), "foreign source executed before origin check")

    def test_preloaded_modules_and_namespace_getattr_never_supply_dependencies(self):
        arguments = real_bundle_arguments()
        names = ("rc6_oci_host_helper", "rc6_r308_root_launch_profile_freeze",
                 "rc6_host_rootowned_installer_contract", "rc6_r304_root_install_manifest_freeze")
        for target in (*names, "namespace_getattr"):
            with self.subTest(target=target), TemporaryDirectory() as folder:
                marker = Path(folder) / "executed"
                def trap(*_args, **_kwargs):
                    marker.write_text("FOREIGN")
                    raise ImportError("foreign callback")
                package = ModuleType("scripts")
                package.__path__ = [str(producer._trusted_dependency_root())]
                if target == "namespace_getattr":
                    package.__getattr__ = trap
                else:
                    foreign = ModuleType("scripts." + target)
                    # Forged __file__ demonstrates why post-import path checks
                    # cannot authenticate an already-loaded module's code.
                    foreign.__file__ = str(producer._trusted_dependency_root() / (target + ".py"))
                    foreign.__getattr__ = trap
                    package.__dict__[target] = foreign
                with patch.dict(sys.modules):
                    for name in names:
                        sys.modules.pop("scripts." + name, None)
                    sys.modules["scripts"] = package
                    if target != "namespace_getattr":
                        sys.modules["scripts." + target] = foreign
                    before = {key: value for key, value in sys.modules.items()
                              if key == "scripts" or key.startswith("scripts.")}
                    try:
                        producer.propose_root_bundle(**arguments)
                    except (producer.ProvenanceHold, ImportError):
                        pass
                    self.assertFalse(marker.exists(), "preloaded foreign dependency executed")
                    self.assertEqual(before, {key: value for key, value in sys.modules.items()
                                              if key == "scripts" or key.startswith("scripts.")})

    def test_foreign_standard_library_resolution_holds_before_source_execution(self):
        # secrets imports random/hmac transitively; datetime is another import
        # not loaded by R326 itself. No target import is used for validation.
        for target in ("secrets", "random", "hmac", "datetime"):
            with self.subTest(target=target), TemporaryDirectory() as folder:
                marker = Path(folder) / "executed"
                (Path(folder) / (target + ".py")).write_text(
                    "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('FOREIGN')\n")
                with patch.dict(sys.modules), patch.object(sys, "path", [folder, *sys.path]):
                    for name in ("secrets", target):
                        sys.modules.pop(name, None)
                    held = None
                    try:
                        producer._trusted_freezer()
                    except producer.ProvenanceHold as error:
                        held = error
                    except ImportError:
                        pass
                    self.assertFalse(marker.exists(), "foreign stdlib source executed")
                    self.assertIsNotNone(held, "foreign standard library dependency must HOLD")

    def test_preloaded_standard_library_getters_never_execute(self):
        for target in ("secrets", "random", "hmac", "datetime"):
            with self.subTest(target=target), TemporaryDirectory() as folder:
                marker = Path(folder) / "executed"
                def trap(*_args, **_kwargs):
                    marker.write_text("FOREIGN")
                    raise ImportError("foreign stdlib getter")
                foreign = ModuleType(target)
                foreign.__file__ = str(Path(folder) / (target + ".py"))
                foreign.__getattr__ = trap
                with patch.dict(sys.modules):
                    sys.modules.pop("secrets", None)
                    sys.modules[target] = foreign
                    held = None
                    try:
                        producer._trusted_freezer()
                    except producer.ProvenanceHold as error:
                        held = error
                    except ImportError:
                        pass
                    self.assertFalse(marker.exists(), "foreign stdlib getter executed")
                    self.assertIsNotNone(held, "foreign standard library dependency must HOLD")

    def test_changed_release_helper_is_rejected_before_freezer_execution(self):
        arguments = real_bundle_arguments()
        boundary, bundle, outer = fixture(b"#!/usr/bin/python3 -I\n# changed D helper\n")
        arguments.update(boundary=boundary, bundle_bytes=bundle, outer_manifest_bytes=outer)
        with patch.object(producer, "_trusted_freezer", side_effect=AssertionError("executed too early")) as freezer:
            with self.assertRaisesRegex(producer.ProvenanceHold, "TRUSTED_R271_D_BLOB_MISMATCH"):
                producer.propose_root_bundle(**arguments)
        freezer.assert_not_called()

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
        with TemporaryDirectory() as folder, \
             patch.object(producer, "_trusted_dependency_root", return_value=Path(folder)):
            with self.assertRaisesRegex(producer.ProvenanceHold, "MISSING_TRUSTED_R304"):
                producer._trusted_freezer()


if __name__ == "__main__":
    unittest.main()

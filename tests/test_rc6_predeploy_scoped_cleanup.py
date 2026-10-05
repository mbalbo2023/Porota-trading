"""Controlled runner cleanup guards: no real Docker, Actions or deployment."""
import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

from scripts import porota_predeploy_cleanup as cleanup

IMAGE = "sha256:" + "d" * 64
CONTAINER = "e" * 64
FOREIGN = "f" * 64
CONTEXT = {
    "repository": cleanup.REPOSITORY, "workflow_path": cleanup.WORKFLOW,
    "event": "pull_request", "run_id": "471", "run_attempt": "2",
    "candidate_sha": "a" * 40, "candidate_tree": "b" * 40,
}


def metrics(_paths, *, free=4 * 1024**3, inodes=1000):
    return {"1": {"filesystem_device": 1, "measured_paths": ["controlled-filesystem"],
                  "free_bytes": free, "free_inodes": inodes, "total_inodes": 2000}}


class Docker:
    def __init__(self, root):
        self.root = root
        self.images = {}
        self.containers = {}
        self.calls = []
        self.fail = None
        self.leave_image = False
        self.on_remove = None

    def __call__(self, *args, timeout):
        self.calls.append((args, timeout))
        assert 0 < timeout <= 10
        if self.fail is not None and args[:len(self.fail)] == self.fail:
            raise cleanup.CleanupRejected("DOCKER_COMMAND_FAILED")
        if args == ("info", "--format", "{{.DockerRootDir}}"):
            return str(self.root)
        if args[:2] == ("image", "ls"):
            item = args[-1]
            if item.startswith("reference="):
                tag = item.split("=", 1)[1]
                return "\n".join(key for key, value in self.images.items()
                                 if tag in value["RepoTags"])
            assert item.startswith("label=porota.predeploy.owner=")
            owner = item.split("=", 2)[2]
            return "\n".join(key for key, value in self.images.items()
                             if value["Config"]["Labels"].get("porota.predeploy.owner") == owner)
        if args[:2] == ("container", "ls"):
            assert args[-1].startswith("label=porota.predeploy.owner=")
            owner = args[-1].split("=", 2)[2]
            return "\n".join(key for key, value in self.containers.items()
                             if value["Config"]["Labels"].get("porota.predeploy.owner") == owner)
        if args[:2] in (("image", "inspect"), ("container", "inspect")):
            inventory = self.images if args[0] == "image" else self.containers
            return json.dumps([inventory[args[2]]])
        if args[:2] == ("container", "rm"):
            assert args[2] == "--force"
            del self.containers[args[3]]
            return args[3]
        if args[:2] == ("image", "rm"):
            assert args[2] == "--no-prune" and "--force" not in args
            if any(value["Image"] == args[3] for value in self.containers.values()):
                raise cleanup.CleanupRejected("DOCKER_COMMAND_FAILED")
            if not self.leave_image:
                del self.images[args[3]]
            if self.on_remove:
                self.on_remove()
            return args[3]
        raise AssertionError(args)


@pytest.fixture
def owned(tmp_path):
    repo, runner = tmp_path / "source", tmp_path / "runner"
    repo.mkdir(mode=0o755)
    runner.mkdir(mode=0o755)
    daemon = Docker(tmp_path)
    scope, control = cleanup.prepare(repo, runner, CONTEXT, run=daemon, probe=metrics)
    return {"repo": repo, "runner": runner, "scope": scope,
            "control": control, "root": Path(control["private_root"]), "docker": daemon}


def source_claim(owned):
    source = owned["repo"] / cleanup.SOURCE
    source.write_bytes(b'{"scope":"CONTROLLED_CLEANUP_METADATA_ONLY"}\n')
    source.chmod(0o644)
    cleanup.claim_source(owned["scope"], source, hashlib.sha256(source.read_bytes()).hexdigest())
    return source


def built(owned, *, capture=True, container=True):
    source_claim(owned)
    cleanup.begin_build(owned["scope"], run=owned["docker"])
    control, _ = cleanup.load(owned["scope"])
    labels = cleanup.expected_labels(control)
    owned["docker"].images[IMAGE] = {
        "Id": IMAGE, "Config": {"Labels": labels},
        "RepoTags": [control["image_ref"]], "RepoDigests": [],
    }
    if capture:
        cleanup.claim_image(owned["scope"], IMAGE, run=owned["docker"])
    if container:
        owned["docker"].containers[CONTAINER] = {
            "Id": CONTAINER, "Image": IMAGE, "Config": {"Labels": dict(labels)}, "Mounts": []}
    owned["control"], _ = cleanup.load(owned["scope"])
    return owned


def run_cleanup(owned, **kwargs):
    return cleanup.cleanup(owned["scope"], context=CONTEXT,
                           run=owned["docker"], probe=metrics, **kwargs)


def assert_no_removals(daemon):
    assert not any(args[:2] in {("container", "rm"), ("image", "rm")}
                   for args, _ in daemon.calls)


def test_cleanup_removes_only_bound_run_resources_and_measures_actual_delta(owned):
    built(owned)
    foreign = owned["runner"] / "unowned-evidence.json"
    foreign.write_bytes(b"foreign")
    nested = owned["root"] / "image-app"
    nested.mkdir()
    (nested / "Dockerfile").write_bytes(b"controlled image output")
    owned["docker"].containers[FOREIGN] = {
        "Id": FOREIGN, "Image": "sha256:" + "c" * 64,
        "Config": {"Labels": {"porota.predeploy.owner": "0" * 32}}, "Mounts": []}
    probes = iter([metrics([], free=4 * 1024**3), metrics([], free=4 * 1024**3 + 8192)])
    result = cleanup.cleanup(owned["scope"], context=CONTEXT,
                             run=owned["docker"], probe=lambda _paths: next(probes))
    assert result["status"] == "GREEN" and result["failures"] == []
    assert result["removed_image_ids"] == [IMAGE]
    assert result["removed_container_ids"] == [CONTAINER]
    assert result["source_manifest_removed"] is True
    assert result["private_files_removed"] == 2 and result["private_directories_removed"] == 1
    assert not owned["root"].exists() and foreign.read_bytes() == b"foreign"
    assert set(owned["docker"].containers) == {FOREIGN}
    assert result["filesystem_deltas"]["1"]["free_byte_delta"] == 8192
    assert result["filesystem_deltas"]["1"]["reclaimed_free_bytes_observed"] == 8192
    assert all("ancestor=" not in str(args) and "prune" not in args
               and args != ("container", "inspect", FOREIGN)
               for args, _ in owned["docker"].calls)


@pytest.mark.parametrize("operation", [("container", "rm"), ("image", "rm")])
def test_docker_removal_failure_is_red_and_retains_private_evidence(owned, operation):
    built(owned)
    owned["docker"].fail = operation
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "DOCKER_COMMAND_FAILED" in result["failures"]
    assert owned["root"].exists() and (owned["repo"] / cleanup.SOURCE).exists()
    assert IMAGE in owned["docker"].images


def test_successful_command_with_remaining_owned_image_is_red(owned):
    built(owned, container=False)
    owned["docker"].leave_image = True
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "OWN_DOCKER_RESOURCES_REMAIN" in result["failures"]
    assert owned["root"].exists() and IMAGE in owned["docker"].images


@pytest.mark.parametrize("shared", ["tag", "digest", "foreign_reference"])
def test_shared_image_is_retained_without_removing_or_inspecting_foreign_resources(owned, shared):
    built(owned, container=False)
    if shared == "tag":
        owned["docker"].images[IMAGE]["RepoTags"].append("other-owner:preserved")
    elif shared == "digest":
        owned["docker"].images[IMAGE]["RepoDigests"] = ["example/image@sha256:" + "1" * 64]
    else:
        owned["docker"].containers[FOREIGN] = {
            "Id": FOREIGN, "Image": IMAGE,
            "Config": {"Labels": {"porota.predeploy.owner": "0" * 32}}, "Mounts": []}
    result = run_cleanup(owned)
    assert result["status"] == "RED" and IMAGE in owned["docker"].images
    assert owned["root"].exists()
    assert not any(args == ("container", "inspect", FOREIGN)
                   or args[:2] == ("container", "rm") for args, _ in owned["docker"].calls)
    if shared != "foreign_reference":
        assert "SHARED_IMAGE_RETAINED" in result["failures"]
        assert_no_removals(owned["docker"])
    else:
        assert set(owned["docker"].containers) == {FOREIGN}


def test_missing_capture_after_finished_build_requires_complete_owned_image_binding(owned):
    built(owned, capture=False)
    result = run_cleanup(owned)
    assert result["status"] == "GREEN" and result["owner"]["image_id"] is None
    assert result["owner"]["build_phase"] == "STARTED"
    assert result["removed_image_ids"] == [IMAGE] and not owned["root"].exists()


@pytest.mark.parametrize("field", [
    "porota.commit", "porota.tree", "porota.predeploy.run-id",
    "porota.predeploy.run-attempt", "porota.source-manifest-sha256"])
def test_uncaptured_image_with_any_wrong_origin_label_is_retained(owned, field):
    built(owned, capture=False)
    owned["docker"].images[IMAGE]["Config"]["Labels"][field] = "wrong"
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "OWNER_IMAGE_LABEL_MISMATCH" in result["failures"]
    assert IMAGE in owned["docker"].images and owned["root"].exists()
    assert_no_removals(owned["docker"])


def test_early_failure_without_build_cleans_only_private_files_and_claims_no_image(owned):
    (owned["root"] / "diagnostic.json").write_bytes(b"early failed gate")
    result = run_cleanup(owned)
    assert result["status"] == "GREEN"
    assert result["owner"]["build_phase"] == "NOT_STARTED"
    assert result["removed_image_ids"] == result["removed_container_ids"] == []
    assert result["source_manifest_removed"] is False and not owned["root"].exists()
    assert result["filesystem_deltas"]["1"]["reclaimed_free_bytes_observed"] == 0


def test_early_phase_cannot_hide_an_unexpected_owned_image(owned):
    labels = cleanup.expected_labels(owned["control"])
    owned["docker"].images[IMAGE] = {
        "Id": IMAGE, "Config": {"Labels": labels},
        "RepoTags": [owned["control"]["image_ref"]], "RepoDigests": []}
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "UNEXPECTED_OWN_IMAGE_RETAINED" in result["failures"]
    assert_no_removals(owned["docker"])


def test_foreign_candidate_tag_never_grants_cleanup_ownership(owned):
    owned["docker"].images[IMAGE] = {
        "Id": IMAGE, "Config": {"Labels": {"porota.predeploy.owner": "0" * 32}},
        "RepoTags": [owned["control"]["image_ref"]], "RepoDigests": []}
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "CANDIDATE_TAG_FOREIGN_OR_UNBOUND" in result["failures"]
    assert_no_removals(owned["docker"])
    assert not any(args == ("image", "inspect", IMAGE)
                   for args, _ in owned["docker"].calls)
    assert owned["root"].exists()


@pytest.mark.parametrize("change", ["labels", "image", "mount"])
def test_container_binding_is_verified_before_any_removal(owned, change):
    built(owned)
    child = owned["docker"].containers[CONTAINER]
    if change == "labels":
        child["Config"]["Labels"]["porota.tree"] = "wrong"
    elif change == "image":
        child["Image"] = "sha256:" + "1" * 64
    else:
        child["Mounts"] = [{"Source": "/foreign", "Destination": "/app/data"}]
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "CONTAINER_OWNER_BINDING_INVALID" in result["failures"]
    assert_no_removals(owned["docker"])


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "same_device_mount"])
def test_private_namespace_alias_never_deletes_external_data(owned, tmp_path, monkeypatch, alias):
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = outside / "protected"
    payload.write_bytes(b"keep bytes")
    member = owned["root"] / "untrusted"
    if alias == "symlink":
        member.symlink_to(outside, target_is_directory=True)
    elif alias == "hardlink":
        os.link(payload, member)
    else:
        member.mkdir()
        (member / "protected").write_bytes(b"same-device mount witness")
        actual_mount = cleanup.mount_id
        def mounted(descriptor):
            target = os.readlink("/proc/self/fd/" + str(descriptor))
            value = actual_mount(descriptor)
            return value + 1 if target == str(member) else value
        monkeypatch.setattr(cleanup, "mount_id", mounted)
    result = run_cleanup(owned)
    assert result["status"] == "RED" and owned["root"].exists()
    assert payload.read_bytes() == b"keep bytes"
    assert_no_removals(owned["docker"])
    if alias == "same_device_mount":
        assert "PRIVATE_NAMESPACE_MOUNT" in result["failures"]
        assert (member / "protected").read_bytes() == b"same-device mount witness"


def test_changed_source_manifest_is_retained_with_all_private_data(owned):
    built(owned)
    source = owned["repo"] / cleanup.SOURCE
    source.write_bytes(b"replacement owned by a different producer")
    result = run_cleanup(owned)
    assert result["status"] == "RED" and "SOURCE_MANIFEST_CUSTODY_CHANGED" in result["failures"]
    assert source.read_bytes() == b"replacement owned by a different producer"
    assert owned["root"].exists()
    assert_no_removals(owned["docker"])


@pytest.mark.parametrize("field", ["repo_root", "runner_temp", "private_root", "owner_uuid"])
def test_rebound_control_cannot_redirect_cleanup_to_another_owned_directory(owned, tmp_path, field):
    outside = tmp_path / "other-run"
    outside.mkdir()
    protected = outside / cleanup.SOURCE
    protected.write_bytes(b"unrelated source")
    control, details = cleanup.load(owned["scope"])
    control[field] = "0" * 32 if field == "owner_uuid" else str(outside)
    cleanup.publish(owned["scope"], control, previous=details)
    result = run_cleanup(owned)
    assert result["status"] == "RED" and protected.read_bytes() == b"unrelated source"
    assert owned["root"].exists()
    assert_no_removals(owned["docker"])


def configure_cli(owned, monkeypatch):
    monkeypatch.chdir(owned["repo"])
    monkeypatch.setenv("RUNNER_TEMP", str(owned["runner"]))
    monkeypatch.setenv("POROTA_PREDEPLOY_OWNER", str(owned["scope"]))
    monkeypatch.setenv("POROTA_PREDEPLOY_OWNER_UUID", owned["control"]["owner_uuid"])
    monkeypatch.setattr(cleanup, "execution_context", lambda *_args: CONTEXT)


@pytest.mark.parametrize("output", ["wrong_path", "existing", "symlink"])
def test_cli_output_rejection_precedes_cleanup(owned, tmp_path, monkeypatch, capsys, output):
    configure_cli(owned, monkeypatch)
    receipt = Path(owned["control"]["receipt_path"])
    external = tmp_path / "keep"
    external.write_bytes(b"protected output")
    if output == "wrong_path":
        receipt = tmp_path / "unexpected.json"
    elif output == "existing":
        receipt.write_bytes(b"existing receipt")
    else:
        receipt.symlink_to(external)
    def forbidden(*_args, **_kwargs):
        pytest.fail("cleanup ran before output validation")
    monkeypatch.setattr(cleanup, "cleanup", forbidden)
    assert cleanup.main(["cleanup", "--scope", str(owned["scope"]), "--receipt", str(receipt)]) == 1
    assert "RED" in capsys.readouterr().out
    assert owned["root"].exists() and external.read_bytes() == b"protected output"


def test_cli_rejects_uuid_mismatch_before_mutating(owned, monkeypatch, capsys):
    configure_cli(owned, monkeypatch)
    monkeypatch.setenv("POROTA_PREDEPLOY_OWNER_UUID", "0" * 32)
    def forbidden(*_args, **_kwargs):
        pytest.fail("cleanup ran before CLI ownership validation")
    monkeypatch.setattr(cleanup, "cleanup", forbidden)
    assert cleanup.main(["cleanup", "--scope", str(owned["scope"]),
                         "--receipt", owned["control"]["receipt_path"]]) == 1
    assert "OWNER_CLI_ROOT_OR_UUID_MISMATCH" in capsys.readouterr().out
    assert owned["root"].exists()


@pytest.mark.parametrize("free,inodes", [
    (cleanup.MIN_FREE_BYTES - 1, 1000), (4 * 1024**3, 199)])
def test_post_cleanup_capacity_is_checked_without_raising_authorized_bounds(owned, free, inodes):
    result = cleanup.cleanup(owned["scope"], context=CONTEXT, run=owned["docker"],
        probe=lambda paths: metrics(paths, free=free, inodes=inodes))
    assert result["status"] == "RED" and "POST_CLEANUP_CAPACITY_RED" in result["failures"]
    assert not owned["root"].exists()


def test_negative_filesystem_delta_is_not_reported_as_reclaimed_bytes(owned):
    probes = iter([metrics([], free=4 * 1024**3), metrics([], free=4 * 1024**3 - 4096)])
    result = cleanup.cleanup(owned["scope"], context=CONTEXT,
                             run=owned["docker"], probe=lambda _paths: next(probes))
    assert result["status"] == "GREEN"
    assert result["filesystem_deltas"]["1"]["free_byte_delta"] == -4096
    assert result["filesystem_deltas"]["1"]["reclaimed_free_bytes_observed"] == 0


def test_total_deadline_expiry_cannot_certify_success_or_delete_remaining_evidence(owned):
    built(owned, container=False)
    current = [0.0]
    owned["docker"].on_remove = lambda: current.__setitem__(0, cleanup.MAX_SECONDS + 1.0)
    result = run_cleanup(owned, clock=lambda: current[0])
    assert result["status"] == "RED" and "CLEANUP_DEADLINE_EXCEEDED" in result["failures"]
    assert owned["root"].exists() and (owned["repo"] / cleanup.SOURCE).exists()


def test_workflow_private_uuid_root_preserves_primary_tag_build_and_upload_order():
    workflow = yaml.safe_load(Path(".github/workflows/porota-predeploy-v2.yml").read_text())
    job = workflow["jobs"]["artifact-gate"]
    steps = job["steps"]
    locations = {step["name"]: index for index, step in enumerate(steps)}
    initialized = locations["Initialize proven private runner workspace"]
    freeze = locations["Freeze checkout byte provenance before build"]
    build = locations["Build candidate exactly once"]
    primary = locations["Upload predeploy evidence"]
    secondary = locations["Upload small published-primary verification receipts"]
    cleaning = locations["Local cleanup"]
    summary = locations["Predeploy summary"]
    assert initialized < freeze < build < primary < secondary < cleaning < summary
    assert job["env"]["IMAGE"] == "porota-predeploy-v2:${{ github.event.pull_request.head.sha }}"
    assert sum(step.get("run", "").count("docker build") for step in steps) == 1
    assert "begin-build" in steps[build]["run"] and "claim-image" in steps[build]["run"]
    for label in ("porota.predeploy.owner=${POROTA_PREDEPLOY_OWNER_UUID}",
                  "porota.predeploy.run-id=${GITHUB_RUN_ID}",
                  "porota.predeploy.run-attempt=${GITHUB_RUN_ATTEMPT}"):
        assert label in steps[build]["run"]
    assert all("/tmp/porota-" not in step.get("run", "") for step in steps)
    governed = next(step for step in steps
                    if step["name"] == "Governed automatic test discovery and execution")
    assert governed["run"].count(
        'RUNNER_TEMP="$POROTA_PREDEPLOY_TMP" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest') == 2
    assert "RUNNER_TEMP" not in steps[cleaning].get("env", {})
    assert "${{ env.POROTA_PREDEPLOY_TMP }}/porota-predeploy-evidence/" in steps[primary]["with"]["path"]
    assert steps[secondary]["with"]["path"].splitlines() == [
        "${{ env.POROTA_PREDEPLOY_TMP }}/porota-predeploy-published-evidence/*.json",
        "${{ env.POROTA_PREDEPLOY_TMP }}/porota-predeploy-published-evidence/*.xml",
    ]
    assert steps[cleaning]["if"] == "always()" and steps[cleaning]["id"] == "local_cleanup"
    assert "set -Eeuo pipefail" in steps[cleaning]["run"]
    assert "ancestor=" not in steps[cleaning]["run"] and "rm -rf" not in steps[cleaning]["run"]
    assert steps[summary]["if"] == "success() && steps.local_cleanup.outcome == 'success'"
    assert "result['status'] == 'GREEN'" in steps[summary]["run"]
    assert "result['owner']['owner_uuid']" in steps[summary]["run"]
    assert "POROTA_PREDEPLOY_V2=GREEN" in steps[summary]["run"]

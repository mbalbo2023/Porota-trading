"""Cheap resource-admission counterexamples; no cgroup, mount or material run.

Positive readback fixtures are typed unit inputs, never evidence of an actual
1GiB runner or Droplet. Native qualification requires the independent CLI.
"""
import ast
import copy
import json
import os
from pathlib import Path

import pytest

from scripts import rc6_product_resource_compatibility as resource_profile


ROOT = Path(__file__).absolute().parents[1]


@pytest.fixture
def plan():
    return resource_profile.exact_plan()[0]


def cgroup_fixture():
    return {"cpu.max": "100000 100000", "memory.max": str(1024**3),
        "memory.swap.max": "0", "pids.max": "64", "memory.current": "33554432",
        "memory.peak": "67108864", "affinity": [0], "uid": 1001, "euid": 1001,
        "controls_immutable_to_nonroot": True, "processes": [os.getpid()],
        "path": "/sys/fs/cgroup", "membership": "0::/", "mount_id": 50,
        "mount_root": "/", "mountpoint": "/sys/fs/cgroup", "mount_options": ["ro"],
        "cgroup_namespace_inode": 123, "mount_namespace_inode": 456,
        "privilege_status": {"Uid": "1001 1001 1001 1001", "Gid": "1001 1001 1001 1001",
            "Groups": "", "CapInh": "0000000000000000", "CapPrm": "0000000000000000",
            "CapEff": "0000000000000000", "CapBnd": "0000000000000000", "CapAmb": "0000000000000000",
            "NoNewPrivs": "1", "Seccomp": "2"},
        "events": {"oom": 0, "oom_kill": 0, "oom_group_kill": 0, "max": 0}}


def filesystem_fixture():
    node = {"id": 60, "work_root": "/unit_control", "mountpoint": "/unit_control",
        "device_number": 17, "root": "/", "filesystem": "tmpfs",
        "options": ["rw", "nosuid", "nodev"], "source_mount_id": 61,
        "source_options": ["ro"], "total_bytes": 256 * 1024**2,
        "available_bytes": 240 * 1024**2, "total_inodes": 65536,
        "free_inodes": 60000, "nested_writable_mounts": [], "owner_uid": os.geteuid()}
    node["all_mounts"] = [{"id": identifier, "mountpoint": mountpoint, "root": "/",
        "filesystem": filesystem, "options": options, "propagation": [], "super_options": options}
        for identifier, mountpoint, filesystem, options in (
            (1, "/", "ext4", ["ro"]), (50, "/sys/fs/cgroup", "cgroup2", ["ro"]),
            (60, "/unit_control", "tmpfs", ["rw", "nosuid", "nodev"]),
            (61, "/source", "ext4", ["ro"]), (62, "/proc", "proc", ["ro"]),
            (63, "/dev", "devtmpfs", ["ro"]), (64, "/dev/pts", "devpts", ["ro"]))]
    return node


def envelope_fixture():
    return {"failures": [], "cgroup_observation": cgroup_fixture(),
            "filesystem_observation": filesystem_fixture()}


def test_unit_readback_accepts_one_cpu_one_gib_but_does_not_qualify_product_or_disk(plan):
    limits = resource_profile.validate_cgroup(cgroup_fixture(), plan)
    disk = resource_profile.validate_filesystem(filesystem_fixture(), plan)
    assert limits["cpu_limit"] == 1
    assert limits["memory_limit_bytes"] == 1024**3
    assert limits["interpreter_supervisor_children_cache_and_tmpfs_in_same_memory_budget"] is True
    assert disk["aggregate_storage_limit_bytes"] == 256 * 1024**2
    assert disk["persistent_disk_latency_or_durability_qualified"] is False
    assert plan["whole_droplet_memory_qualified"] is False
    assert plan["historical_G4_G5_workloads_changed"] is False


@pytest.mark.parametrize("key,value,reason", [
    ("cpu.max", "max 100000", "CPU_LIMIT_NOT_ENFORCED"),
    ("cpu.max", "400000 100000", "CPU_ABOVE_ONE_CORE"),
    ("cpu.max", "0 100000", "CPU_ABOVE_ONE_CORE"),
    ("cpu.max", "100000 0", "CPU_ABOVE_ONE_CORE"),
    ("cpu.max", "100000 100000 extra", "CPU_MAX_MALFORMED"),
    ("memory.max", "max", "MEMORY_LIMIT_NOT_ENFORCED"),
    ("memory.max", str(2 * 1024**3), "MEMORY_ABOVE_ONE_GIB"),
    ("memory.max", "0", "MEMORY_ABOVE_ONE_GIB"),
    ("memory.swap.max", "max", "SWAP_LIMIT_NOT_ENFORCED"),
    ("memory.swap.max", "1", "SWAP_MUST_BE_ZERO"),
    ("pids.max", "max", "PIDS_LIMIT_NOT_ENFORCED"),
    ("pids.max", "65", "PIDS_LIMIT_TOO_LARGE"),
    ("affinity", [0, 1, 2, 3], "SINGLE_CPU_AFFINITY_REQUIRED"),
    ("affinity", [True], "SINGLE_CPU_AFFINITY_REQUIRED"),
    ("controls_immutable_to_nonroot", False, "NONROOT_CAN_ENLARGE_LIMITS"),
    ("uid", 0, "NONROOT_REQUIRED"),
    ("euid", 0, "NONROOT_REQUIRED"),
    ("memory.peak", str(1024**3 + 1), "MEMORY_ENVELOPE_ALREADY_EXCEEDED"),
    ("processes", [], "CURRENT_CGROUP_MEMBERSHIP_REQUIRED"),
])
def test_nominal_runner_labels_or_taskset_cannot_replace_kernel_limits(plan, key, value, reason):
    node = cgroup_fixture()
    node[key] = value
    with pytest.raises(ValueError, match=reason):
        resource_profile.validate_cgroup(node, plan)


@pytest.mark.parametrize("key,value,reason", [
    ("mountpoint", "/", "EXCLUSIVE_WHOLE_MOUNT_REQUIRED"),
    ("root", "/unbounded_directory", "EXCLUSIVE_WHOLE_MOUNT_REQUIRED"),
    ("filesystem", "ext4", "REQUIRES_SEPARATE_DISK_CONTRACT"),
    ("options", ["rw"], "MOUNT_SECURITY_REQUIRED"),
    ("total_bytes", 25 * 1024**3, "AGGREGATE_FILESYSTEM_CAPACITY_NOT_ENFORCED"),
    ("total_bytes", True, "AGGREGATE_FILESYSTEM_CAPACITY_NOT_ENFORCED"),
    ("total_inodes", 0, "AGGREGATE_INODE_LIMIT_NOT_ENFORCED"),
    ("total_inodes", 65537, "AGGREGATE_INODE_LIMIT_NOT_ENFORCED"),
    ("free_inodes", 6553, "FREE_INODE_RESERVE_REQUIRED"),
    ("available_bytes", 0, "AVAILABLE_BYTES_INVALID"),
    ("source_mount_id", 60, "SOURCE_READONLY_SEPARATE_MOUNT_REQUIRED"),
    ("source_options", ["rw"], "SOURCE_READONLY_SEPARATE_MOUNT_REQUIRED"),
    ("nested_writable_mounts", [70], "NESTED_WRITABLE_QUOTA_ESCAPE"),
    ("owner_uid", os.geteuid() + 1, "OWNED_WORK_ROOT_REQUIRED"),
])
def test_aggregate_budget_cannot_be_claimed_from_directory_or_per_file_limit(plan, key, value, reason):
    node = filesystem_fixture()
    node[key] = value
    with pytest.raises(ValueError, match=reason):
        resource_profile.validate_filesystem(node, plan)


@pytest.mark.parametrize("mountpoint", ["/", "/tmp", "/var/tmp", "/sys/fs/cgroup", "/proc", "/dev", "/dev/pts"])
def test_foreign_writable_mount_is_a_quota_escape_even_outside_work_root(plan, mountpoint):
    node = filesystem_fixture()
    matching = [row for row in node["all_mounts"] if row["mountpoint"] == mountpoint]
    if matching:
        matching[0]["options"] = ["rw"]
    else:
        node["all_mounts"].append({"id": 70, "mountpoint": mountpoint, "root": "/",
            "filesystem": "tmpfs", "options": ["rw"], "propagation": [], "super_options": ["rw"]})
    assert node["nested_writable_mounts"] == []  # The previous guard missed it.
    with pytest.raises(ValueError, match="FOREIGN_WRITABLE_MOUNT_QUOTA_ESCAPE"):
        resource_profile.validate_filesystem(node, plan)


@pytest.mark.parametrize("propagation", ["shared:17", "master:17", "propagate_from:17"])
def test_readonly_mount_can_still_admit_foreign_propagated_overmount_and_is_rejected(plan, propagation):
    node = filesystem_fixture()
    node["all_mounts"][0]["propagation"] = [propagation]
    with pytest.raises(ValueError, match="SHARED_MOUNT_PROPAGATION_ESCAPE"):
        resource_profile.validate_filesystem(node, plan)


def test_partial_mount_inventory_cannot_claim_only_tmpfs_is_writable(plan):
    node = filesystem_fixture()
    node["all_mounts"] = [row for row in node["all_mounts"] if row["mountpoint"] != "/"]
    with pytest.raises(ValueError, match="UNAMBIGUOUS_ROOT_MOUNT_REQUIRED"):
        resource_profile.validate_filesystem(node, plan)


@pytest.mark.parametrize("key,value", [
    ("membership", "0::/current_leaf"), ("mount_root", "/parent_cgroup"),
    ("path", "/sys/fs/cgroup/current_leaf"), ("mount_options", ["rw"]),
])
def test_current_limits_are_insufficient_when_parent_sibling_or_writable_cgroup_is_visible(plan, key, value):
    node = cgroup_fixture()
    node[key] = value
    with pytest.raises(ValueError, match="READONLY_CURRENT_CGROUP_NAMESPACE_ROOT_REQUIRED"):
        resource_profile.validate_cgroup(node, plan)


@pytest.mark.parametrize("key,value,reason", [
    ("Uid", "1001 1001 0 1001", "SAVED_OR_FILESYSTEM_ROOT_ESCAPE"),
    ("Gid", "1001 1001 1001 0", "SAVED_OR_FILESYSTEM_ROOT_ESCAPE"),
    ("Groups", "27", "SUPPLEMENTARY_GROUP_ESCAPE"),
    ("CapInh", "0000000000000001", "CAPABILITY_ESCAPE"),
    ("CapPrm", "0000000000000001", "CAPABILITY_ESCAPE"),
    ("CapEff", "0000000000000001", "CAPABILITY_ESCAPE"),
    ("CapBnd", "0000000000000001", "CAPABILITY_ESCAPE"),
    ("CapAmb", "0000000000000001", "CAPABILITY_ESCAPE"),
    ("NoNewPrivs", "0", "NO_NEW_PRIVILEGES_REQUIRED"),
    ("Seccomp", "0", "SECCOMP_FILTER_MODE_REQUIRED"),
])
def test_original_privilege_drop_invariants_cannot_be_relaxed_for_small_profile(plan, key, value, reason):
    node = cgroup_fixture()
    node["privilege_status"][key] = value
    with pytest.raises(ValueError, match=reason):
        resource_profile.validate_cgroup(node, plan)


def test_seccomp_status_filter_count_does_not_identify_effective_original_rules(plan):
    limits = resource_profile.validate_cgroup(cgroup_fixture(), plan)
    assert limits["privileges"]["seccomp_mode"] == 2
    assert limits["privileges"]["effective_seccomp_rules"] == "NO_VERIFICADO"
    assert plan["effective_seccomp_rules_qualified"] is False


def test_current_namespace_filter_missing_blocks_execute_before_any_consumer_fixture_or_g0_download(tmp_path, monkeypatch):
    monkeypatch.setattr(resource_profile, "exact_source", lambda *args: None)
    monkeypatch.setattr(resource_profile, "observe_cgroup", cgroup_fixture)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    monkeypatch.setattr(resource_profile, "owned_execution", lambda *args: pytest.fail("unproved native filter launched producer"))
    output = tmp_path / "controls"
    with pytest.raises(ValueError, match=resource_profile.FILTER_BLOCKER):
        resource_profile.main(["--execute", "--source-sha", "a" * 40, "--source-tree", "b" * 40,
            "--work-root", str(tmp_path), "--output", str(output)])
    observed = json.loads((output / "readiness.json").read_text())
    assert observed["failures"] == []
    assert observed["execution_blockers"][0]["reason"] == resource_profile.FILTER_BLOCKER
    assert observed["aggregate_storage_security_qualified"] is False
    assert observed["workload_started"] is False
    assert not (output / "native-profile").exists()


def test_private_worker_entry_cannot_bypass_missing_native_namespace_filter(plan, monkeypatch):
    from types import SimpleNamespace
    from scripts import rc6_native_import_provenance as provenance
    monkeypatch.setattr(resource_profile, "exact_source", lambda *args: None)
    monkeypatch.setattr(resource_profile, "observe_cgroup", cgroup_fixture)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    monkeypatch.setattr(provenance, "environment_qualification", lambda *args: pytest.fail("fixture setup admitted by Seccomp=2"))
    with pytest.raises(ValueError, match=resource_profile.FILTER_BLOCKER):
        resource_profile.worker(SimpleNamespace(source_sha="a" * 40, source_tree="b" * 40,
            work_root=Path("/unit_control")), plan)


def test_readiness_exit_remains_red_when_ceiling_is_observed_without_effective_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(resource_profile, "exact_source", lambda *args: None)
    monkeypatch.setattr(resource_profile, "observe_cgroup", cgroup_fixture)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    assert resource_profile.main(["--readiness", "--source-sha", "a" * 40, "--source-tree", "b" * 40,
        "--work-root", str(tmp_path), "--output", str(tmp_path / "controls")]) == 1


def test_complete_topology_and_namespace_identity_are_frozen_after_native_fin():
    before = envelope_fixture()
    after = copy.deepcopy(before)
    after["filesystem_observation"]["all_mounts"][0]["options"] = ["rw"]
    with pytest.raises(ValueError, match="FILESYSTEM_REBOUND_OR_LIMIT_CHANGED"):
        resource_profile.require_same_envelope(before, after)
    after = copy.deepcopy(before)
    after["cgroup_observation"]["cgroup_namespace_inode"] += 1
    with pytest.raises(ValueError, match="CGROUP_MOVED_OR_LIMIT_CHANGED"):
        resource_profile.require_same_envelope(before, after)


def test_readiness_preserves_both_actual_observations_and_stops_before_workload(plan, monkeypatch):
    limits = cgroup_fixture()
    limits["cpu.max"] = "400000 100000"
    fs = filesystem_fixture()
    fs["total_bytes"] = 30 * 1024**3
    monkeypatch.setattr(resource_profile, "observe_cgroup", lambda: limits)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: fs)
    result = resource_profile.readiness("/unit_control", ROOT, plan)
    assert result["status"] == "BLOQUEADO"
    assert result["checks"] == {"cgroup": False, "filesystem": False}
    assert {row["check"] for row in result["failures"]} == {"cgroup", "filesystem"}
    assert result["cgroup_observation"]["cpu.max"] == "400000 100000"
    assert result["filesystem_observation"]["total_bytes"] == 30 * 1024**3
    assert result["workload_started"] is False
    assert result["product_resource_compatibility"] == "NO_VERIFICADO"
    assert result["G0_G8_qualification"] is False


def test_missing_cgroup_and_unmeasurable_filesystem_cannot_be_green(plan, monkeypatch):
    def unknown():
        raise FileNotFoundError(2, "unit-only missing kernel file")
    monkeypatch.setattr(resource_profile, "observe_cgroup", unknown)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: unknown())
    result = resource_profile.readiness("/unit_control", ROOT, plan)
    assert result["status"] == "BLOQUEADO"
    assert len(result["failures"]) == 2
    assert all(row["errno"] == 2 for row in result["failures"])
    assert result["workload_started"] is False


def test_observed_envelope_remains_unqualified_until_authentic_g0_and_actual157(plan, monkeypatch):
    monkeypatch.setattr(resource_profile, "observe_cgroup", cgroup_fixture)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    result = resource_profile.readiness("/unit_control", ROOT, plan)
    assert result["status"] == "ENVELOPE_OBSERVED_EXECUTION_BLOCKED_NATIVE_FILTER_PROOF_MISSING"
    assert result["product_resource_compatibility"] == "NO_VERIFICADO"
    assert result["whole_droplet_compatibility"] == "NO_VERIFICADO"
    assert result["persistent_disk_compatibility"] == "NO_VERIFICADO"
    assert result["workload_started"] is False
    assert result["execution_blockers"][0]["reason"] == resource_profile.FILTER_BLOCKER


@pytest.mark.parametrize("section,key,value", [
    ("cgroup_observation", "cpu.max", "99999 100000"),
    ("cgroup_observation", "path", "/sys/fs/cgroup/another_attempt"),
    ("cgroup_observation", "affinity", [1]),
    ("filesystem_observation", "id", 71),
    ("filesystem_observation", "device_number", 19),
    ("filesystem_observation", "total_bytes", 128 * 1024**2),
    ("filesystem_observation", "source_options", ["rw"]),
])
def test_live_recheck_rejects_changed_cgroup_mount_or_source_binding(section, key, value):
    before = envelope_fixture()
    after = copy.deepcopy(before)
    after[section][key] = value
    with pytest.raises(ValueError, match="MOVED_OR_LIMIT_CHANGED|REBOUND_OR_LIMIT_CHANGED"):
        resource_profile.require_same_envelope(before, after)


@pytest.mark.parametrize("event", ["oom", "oom_kill", "oom_group_kill", "max"])
def test_successful_exit_cannot_hide_kernel_oom_or_hard_memory_limit_hit(event):
    before = envelope_fixture()
    after = copy.deepcopy(before)
    after["cgroup_observation"]["events"][event] = 1
    with pytest.raises(ValueError, match="OOM_OR_HARD_LIMIT_HIT"):
        resource_profile.require_same_envelope(before, after)


def test_live_recheck_cannot_relabel_an_initially_blocked_profile():
    before = envelope_fixture()
    before["failures"] = [{"reason": "UNIT_ONLY_BLOCKED_CPU"}]
    with pytest.raises(ValueError, match="INITIAL_ENVELOPE_NOT_ADMITTED"):
        resource_profile.require_same_envelope(before, envelope_fixture())


def test_dirty_controller_cannot_attach_new_logic_to_old_source_sha(monkeypatch):
    from scripts import rc6_controlled_governed_runner as governed
    def git(_root, *arguments):
        if arguments == ("rev-parse", "HEAD"):
            return ("a" * 40 + "\n").encode()
        if arguments == ("rev-parse", "HEAD^{tree}"):
            return ("b" * 40 + "\n").encode()
        assert arguments == ("status", "--porcelain")
        return b"?? scripts/uncommitted-controller.py\n"
    monkeypatch.setattr(governed, "git", git)
    with pytest.raises(ValueError, match="CLEAN_EXACT_SOURCE_REQUIRED"):
        resource_profile.exact_source("a" * 40, "b" * 40)


def test_mount_parser_authenticates_type_root_options_and_escaped_path():
    rows = resource_profile.mount_rows(
        "60 1 0:17 / /unit\\040control rw,nosuid,nodev - tmpfs tmpfs rw,size=262144k,nr_inodes=65536\n"
        "61 1 0:18 / /source ro - ext4 /dev/loop0 ro\n")
    assert rows[0]["mountpoint"] == "/unit control"
    assert rows[0]["filesystem"] == "tmpfs"
    assert rows[1]["options"] == ["ro"]
    assert resource_profile.containing_mount("/source/path/file", rows)["id"] == 61


def test_overmounted_or_ambiguous_mount_is_not_a_budget():
    rows = [{"mountpoint": "/unit_control", "id": value} for value in (60, 61)]
    with pytest.raises(ValueError, match="OVERMOUNT_OR_AMBIGUOUS"):
        resource_profile.containing_mount("/unit_control", rows)


@pytest.mark.parametrize("key,value", [
    ("memory_maximum_bytes", 2 * 1024**3), ("storage_maximum_bytes", 20 * 1024**3),
    ("cpu_maximum", 4), ("G0_G8_qualification", True),
    ("historical_G4_G5_workloads_changed", True), ("recurring_infrastructure_cost_usd", 84),
    ("persistent_disk_latency_or_durability_qualified", True),
    ("execution_enabled", True), ("effective_seccomp_rules_qualified", True),
    ("aggregate_storage_security_qualified", True),
])
def test_fixed_profile_contract_cannot_silently_expand_costs_or_claim_equivalence(tmp_path, plan, key, value):
    plan[key] = value
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="SCOPE_OR_FIXED_LIMIT_CHANGED"):
        resource_profile.exact_plan(path)


def test_plan_uses_existing_original_consumer_tests_without_shortening_big_or_horizon(plan):
    assert len(plan["suites"]) == 9
    for suite in plan["suites"]:
        name, function = suite.split("::")
        module = ast.parse((ROOT / name).read_bytes())
        assert any(isinstance(node, ast.FunctionDef) and node.name == function for node in module.body)
        assert "big" not in name.lower() and "horizon" not in name.lower()


def native_fixture(plan):
    native = {"schema": "porota.rc6.product-resource-worker.v1", "source_sha": "a" * 40,
        "source_tree": "b" * 40, "plan_sha256": "c" * 64, "pid": 1234,
        "parent_pid": os.getpid(), "before_fixtures": True, "real_orders_sent": 0,
        "product157": {"installed_total": 157, "installed_unique_total": 157, "expected_total": 157},
        "kernel_envelope": envelope_fixture()}
    identities = [(suite.partition("::")[0][:-3].replace("/", "."), suite.partition("::")[2])
                  for suite in plan["suites"]]
    facts = {"cases": len(identities), "identities": identities}
    return native, facts


def verify_native(native, facts, plan):
    return resource_profile.verify_native_profile(native, {"pid": 1234}, facts,
        source_sha="a" * 40, source_tree="b" * 40, plan_sha256="c" * 64,
        plan=plan, envelope=envelope_fixture())


def test_native_fixture_requires_all_original_profile_identities_and_actual_child_pid(plan):
    native, facts = native_fixture(plan)
    verify_native(native, facts, plan)


@pytest.mark.parametrize("key,value", [
    ("source_sha", "d" * 40), ("source_tree", "d" * 40), ("plan_sha256", "d" * 64),
    ("pid", 5678), ("parent_pid", 5678), ("before_fixtures", False), ("real_orders_sent", True),
])
def test_native_worker_claim_cannot_replace_actual_parent_pid_and_frozen_source(plan, key, value):
    native, facts = native_fixture(plan)
    native[key] = value
    with pytest.raises(ValueError, match="ACTUAL_NATIVE_WORKER_BINDING_REQUIRED"):
        verify_native(native, facts, plan)


def test_development_fourteen_packages_cannot_qualify_resource_product157(plan):
    native, facts = native_fixture(plan)
    native["product157"]["installed_total"] = 14
    with pytest.raises(ValueError, match="NATIVE_ACTUAL157_REQUIRED"):
        verify_native(native, facts, plan)


def test_green_subset_cannot_replace_nine_original_consumer_tests(plan):
    native, facts = native_fixture(plan)
    facts["identities"].pop()
    facts["cases"] -= 1
    with pytest.raises(ValueError, match="COMPLETE_ORIGINAL_PROFILE_IDENTITIES_REQUIRED"):
        verify_native(native, facts, plan)


def test_blocked_execute_never_reaches_native_owner_g0_or_product_fixture(tmp_path, plan, monkeypatch):
    monkeypatch.setattr(resource_profile, "exact_source", lambda *args: None)
    limits = cgroup_fixture()
    limits["cpu.max"] = "400000 100000"
    monkeypatch.setattr(resource_profile, "observe_cgroup", lambda: limits)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    monkeypatch.setattr(resource_profile, "owned_execution", lambda *args: pytest.fail("blocked producer launched"))
    output = tmp_path / "controls"
    with pytest.raises(ValueError, match="ENVELOPE_BLOCKED_NO_WORKLOAD_LAUNCHED"):
        resource_profile.main(["--execute", "--source-sha", "a" * 40, "--source-tree", "b" * 40,
            "--work-root", str(tmp_path), "--output", str(output)])
    saved = json.loads((output / "readiness.json").read_text())
    assert saved["status"] == "BLOQUEADO"
    assert saved["workload_started"] is False
    assert not (output / "native-profile").exists()


def test_controls_are_exclusive_and_never_overwrite_foreign_bytes(tmp_path):
    foreign = tmp_path / "readiness.json"
    foreign.write_bytes(b"original evidence")
    with pytest.raises(FileExistsError):
        resource_profile.save_control(tmp_path, {"status": "BLOQUEADO"})
    assert foreign.read_bytes() == b"original evidence"


def test_control_symlink_is_rejected_without_modifying_target(tmp_path):
    target = tmp_path / "original.json"
    target.write_bytes(b"original evidence")
    (tmp_path / "readiness.json").symlink_to(target)
    with pytest.raises(ValueError, match="CONTROL_ALIAS_FORBIDDEN"):
        resource_profile.save_control(tmp_path, {"status": "BLOQUEADO"})
    assert target.read_bytes() == b"original evidence"


def test_resource_job_is_manual_and_has_no_persistent_infrastructure_or_build_path():
    workflow = (ROOT / ".github/workflows/rc6-unified-candidate-tests.yml").read_text()
    resource_job = workflow.split("\n  product-resource-compatibility:\n", 1)[1]
    assert "github.event_name == 'workflow_dispatch'" in resource_job
    assert "inputs.gate == 'product-resource-compatibility'" in resource_job
    assert "inputs.gate != 'product-resource-compatibility'" in workflow
    assert "steps.resource_readiness.outcome == 'success'" in resource_job
    assert "steps.product_profile.outputs.safe_profile_upload == 'true'" in resource_job
    assert "--readiness --source-sha" in resource_job
    assert "--execute --source-sha" in resource_job
    module = ast.parse((ROOT / "scripts/rc6_product_resource_compatibility.py").read_bytes())
    names = {node.func.attr for node in ast.walk(module)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not {"system", "setaffinity", "sched_setaffinity", "rmtree", "rmdir", "unlink"} & names
    # CLI deliberately observes existing kernel controls, and keeps historical
    # native FIN/capture/cleanup implementations instead of rewriting them.
    assert {"execute_owned", "capture_required_evidence", "cleanup_namespace"} <= names

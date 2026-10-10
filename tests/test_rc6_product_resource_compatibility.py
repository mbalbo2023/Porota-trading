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


@pytest.fixture(autouse=True)
def never_install_in_unit_pytest_process(monkeypatch):
    def missing(**kwargs):
        raise ValueError(resource_profile.FILTER_BLOCKER)
    monkeypatch.setattr(resource_profile, "install_current_native_namespace_filter", missing)


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
            "filesystem_observation": filesystem_fixture(),
            "native_namespace_filter": unit_filter_fixture(os.getpid(), os.getppid(), 0)}


def unit_filter_fixture(pid, parent_pid, before, *, inherited=None):
    """Typed captured-control input only; it never authorizes a current process."""
    from scripts import rc6_native_namespace_filter as native
    return {"schema": native.SCHEMA, "source_binding": {"source_sha": "a" * 40,
        "source_tree": "b" * 40, "plan_sha256": "c" * 64},
        "identity": {"pid": pid, "parent_pid": parent_pid, "native_tid": pid,
            "start_ticks": 1, "boot_id": "00000000-0000-0000-0000-000000000000",
            "mount_namespace_inode": 456, "cgroup_namespace_inode": 123},
        "plan": native.build_plan(), "plan_sha256": native.digest(native.canonical(native.build_plan())),
        "filters_before": before, "filters_after": before + 1, "seccomp_load_returncode": 0,
        "native_architecture": native.SUPPORTED_ARCHITECTURES[0], "tsync_requested": True,
        "no_new_privileges": True, "dumpable": 0 if inherited is None else 1,
        "kernel_bpf_bytes_readback_claimed": False, "G0_G8_qualification": False,
        "exported_bpf_sha256": "0" * 64, "rules_sha256": "0" * 64,
        "module_sha256": native.digest(Path(native.__file__).read_bytes()), "exported_bpf_bytes": 8,
        "denial_probes": [{"probe": label, "syscall": name, "number": 42,
            "returncode": -1, "errno": denied_errno}
            for label, name, _, denied_errno in native._probe_specs()], "inherited": inherited}


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
    monkeypatch.setattr(resource_profile, "read_supervisor_filter", lambda *args: {})
    monkeypatch.setattr(provenance, "environment_qualification", lambda *args: pytest.fail("fixture setup admitted by Seccomp=2"))
    with pytest.raises(ValueError, match=resource_profile.FILTER_BLOCKER):
        resource_profile.worker(SimpleNamespace(source_sha="a" * 40, source_tree="b" * 40,
            plan=resource_profile.PLAN, supervisor_filter="/unit_control/supervisor.json",
            supervisor_filter_sha256="0" * 64, work_root=Path("/unit_control")), plan)


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
    assert result["checks"] == {"cgroup": False, "filesystem": False, "native_filter": False}
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


def test_original_cgroup_pressure_file_mount_is_not_a_second_directory():
    # Original native mount shape from38088700559; parser replay only.
    rows = resource_profile.mount_rows(
        '63 61 0:30 / /sys/fs/cgroup ro,nosuid,nodev,noexec,relatime - cgroup2 cgroup2 rw,nsdelegate,memory_recursiveprot\n'
        '64 63 0:30 /system.slice/unit/memory.pressure /sys/fs/cgroup/system.slice/unit/memory.pressure ro,nosuid,nodev,noexec,relatime - cgroup2 cgroup2 rw,nsdelegate,memory_recursiveprot\n')
    assert resource_profile.select_cgroup_directory_mount(rows, 63) == rows[0]
    with pytest.raises(ValueError, match='ACTUAL_CGROUP_DIRECTORY_MOUNT_REQUIRED'):
        resource_profile.select_cgroup_directory_mount(rows, 64)


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'wrong_type', 'wrong_path', 'writable', 'other_writable'])
def test_cgroup_fd_selection_refuses_unproved_or_writable_aliases(defect):
    rows = [{'id': 63, 'filesystem': 'cgroup2', 'mountpoint': '/sys/fs/cgroup', 'options': ['ro'], 'root': '/'}]
    if defect == 'missing': rows[0]['id'] = 62
    elif defect == 'duplicate': rows.append(dict(rows[0]))
    elif defect == 'wrong_type': rows[0]['filesystem'] = 'ext4'
    elif defect == 'wrong_path': rows[0]['mountpoint'] = '/foreign-cgroup'
    elif defect == 'writable': rows[0]['options'] = ['rw']
    elif defect == 'other_writable': rows.append({**rows[0], 'id': 64, 'mountpoint': '/sys/fs/cgroup/memory.pressure', 'options': ['rw']})
    with pytest.raises(ValueError, match='RESOURCE_(ACTUAL_CGROUP_DIRECTORY_MOUNT_REQUIRED|WRITABLE_CGROUP_MOUNT_FORBIDDEN)'):
        resource_profile.select_cgroup_directory_mount(rows, 63)


def test_overmounted_or_ambiguous_mount_is_not_a_budget():
    rows = [{"mountpoint": "/unit_control", "id": value} for value in (60, 61)]
    with pytest.raises(ValueError, match="OVERMOUNT_OR_AMBIGUOUS"):
        resource_profile.containing_mount("/unit_control", rows)


@pytest.mark.parametrize("key,value", [
    ("memory_maximum_bytes", 2 * 1024**3), ("storage_maximum_bytes", 20 * 1024**3),
    ("cpu_maximum", 4), ("G0_G8_qualification", True),
    ("historical_G4_G5_workloads_changed", True), ("recurring_infrastructure_cost_usd", 84),
    ("persistent_disk_latency_or_durability_qualified", True),
    ("execution_enabled", False), ("effective_seccomp_rules_qualified", True),
    ("aggregate_storage_security_qualified", True),
    ("native_current_load_counter_and_denials_required", False),
    ("native_worker_inheritance_before_own_installation_required", False),
    ("supervisor_dumpable_zero_required", False),
    ("native_namespace_filter_plan_sha256", "0" * 64),
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
    from scripts import rc6_native_namespace_filter as native_filter
    parent = envelope_fixture()["native_namespace_filter"]
    parent_sha256 = native_filter.digest(native_filter.canonical(parent))
    child = unit_filter_fixture(1234, os.getpid(), 1, inherited={})
    inheritance = {"schema": native_filter.INHERITANCE_SCHEMA,
        "identity": child["identity"], "parent_identity": parent["identity"],
        "source_binding": parent["source_binding"], "filters_before_own_installation": 1,
        "native_denials_before_own_installation": child["denial_probes"], "G0_G8_qualification": False,
        "fork_exec_inheritance_observed": True, "parent_receipt_sha256": parent_sha256}
    native = {"schema": "porota.rc6.product-resource-worker.v1", "source_sha": "a" * 40,
        "source_tree": "b" * 40, "plan_sha256": "c" * 64, "pid": 1234,
        "parent_pid": os.getpid(), "before_fixtures": True, "real_orders_sent": 0,
        "product157": {"installed_total": 157, "installed_unique_total": 157, "expected_total": 157},
        "kernel_envelope": envelope_fixture(), "parent_filter_sha256": parent_sha256,
        "credential_environment_present": False,
        "native_namespace_filter": unit_filter_fixture(1234, os.getpid(), 1, inherited=inheritance)}
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


@pytest.mark.parametrize("key,value", [
    ("fork_exec_inheritance_observed", False), ("filters_before_own_installation", 4),
    ("parent_receipt_sha256", "0" * 64), ("native_denials_before_own_installation", []),
    ("identity", {}), ("parent_identity", {}), ("G0_G8_qualification", True),
])
def test_status_counter_or_parent_flag_cannot_replace_native_inheritance(plan, key, value):
    native, facts = native_fixture(plan)
    native["native_namespace_filter"]["inherited"][key] = value
    with pytest.raises(ValueError, match="COMPLETE_PARENT_CHILD_INHERITANCE_EVIDENCE_REQUIRED"):
        verify_native(native, facts, plan)


def test_worker_credential_channel_does_not_qualify_a_correct_native_filter(plan):
    native, facts = native_fixture(plan)
    native["credential_environment_present"] = True
    with pytest.raises(ValueError, match="ACTUAL_WORKER_FILTER_LOAD_AND_INHERITANCE_REQUIRED"):
        verify_native(native, facts, plan)


def test_positive_typed_envelope_and_live_filter_receipt_remain_scoped_not_droplet(plan, monkeypatch):
    # Typed unit input only: native installation is witnessed in the separate
    # own-process suite, never minted by this readback fixture.
    proof = envelope_fixture()["native_namespace_filter"]
    monkeypatch.setattr(resource_profile, "require_current_native_namespace_filter", lambda **kwargs: proof)
    monkeypatch.setattr(resource_profile, "observe_cgroup", cgroup_fixture)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    observed = resource_profile.readiness("/unit_control", ROOT, plan)
    assert observed["status"] == "BOUNDED_ENVELOPE_AND_CURRENT_NATIVE_FILTER_OBSERVED"
    assert observed["execution_blockers"] == []
    assert observed["aggregate_storage_security_qualified"] is True
    assert observed["product_resource_compatibility"] == "NO_VERIFICADO"
    assert observed["whole_droplet_compatibility"] == "NO_VERIFICADO"
    assert observed["persistent_disk_compatibility"] == "NO_VERIFICADO"
    assert observed["workload_started"] is False


def test_native_filter_does_not_replace_failed_actual_four_cpu_limit(plan, monkeypatch):
    monkeypatch.setattr(resource_profile, "require_current_native_namespace_filter", lambda **kwargs: {})
    limits = cgroup_fixture()
    limits["cpu.max"] = "400000 100000"
    monkeypatch.setattr(resource_profile, "observe_cgroup", lambda: limits)
    monkeypatch.setattr(resource_profile, "observe_filesystem", lambda *args: filesystem_fixture())
    observed = resource_profile.readiness("/unit_control", ROOT, plan)
    assert observed["status"] == "BLOQUEADO"
    assert observed["aggregate_storage_security_qualified"] is False
    assert observed["failures"][0]["reason"] == "RESOURCE_CPU_ABOVE_ONE_CORE"


def test_supervisor_filter_control_hash_and_alias_are_fail_closed(tmp_path):
    receipt = tmp_path / "supervisor.json"
    proof = envelope_fixture()["native_namespace_filter"]
    resource_profile.exclusive_json(receipt, proof)
    expected = resource_profile.digest(resource_profile.canonical(proof))
    assert resource_profile.read_supervisor_filter(receipt, expected, tmp_path) == proof
    with pytest.raises(ValueError, match="CONTROL_BYTES_REBOUND"):
        resource_profile.read_supervisor_filter(receipt, "0" * 64, tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(receipt)
    with pytest.raises(ValueError, match="OWNED_SUPERVISOR_FILTER_CONTROL_REQUIRED"):
        resource_profile.read_supervisor_filter(alias, expected, tmp_path)


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


def test_resource_control_parent_traversal_rejected_before_mkdir_or_native_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(resource_profile, "exact_source", lambda *args: None)
    (tmp_path / "prefix").mkdir()
    output = tmp_path / "prefix" / ".." / "foreign"
    with pytest.raises(ValueError, match="OUTPUT_OUTSIDE_SOURCE_REQUIRED"):
        resource_profile.main(["--readiness", "--source-sha", "a" * 40, "--source-tree", "b" * 40,
            "--work-root", str(tmp_path), "--output", str(output)])
    assert not (tmp_path / "foreign").exists()


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


def physical_filesystem_fixture():
    """Typed control input; no block device, mount or disk is created."""
    node = filesystem_fixture()
    node["filesystem"] = "ext4"
    node["all_mounts"][2]["filesystem"] = "ext4"
    node["physical_backing"] = {"status": "BLOCK_AND_BACKING_READBACK_OBSERVED",
        "filesystem_device_number": 17, "block_rdev": 17, "block_device_inode": 111,
        "backend": "FULLY_ALLOCATED_LOOP_ON_PHYSICAL_FILESYSTEM",
        "backing_filesystem": "ext4", "backing_bytes": 256 * 1024**2,
        "backing_allocated_bytes": 256 * 1024**2, "backing_device_number": 18,
        "backing_inode": 222, "backing_ancestry": {
            "status": "BLOCK_AND_BACKING_READBACK_OBSERVED", "backend": "KERNEL_PHYSICAL_BLOCK_DEVICE",
            "block_rdev": 18, "filesystem_device_number": 18, "block_device_inode": 333}}
    return node


def metric_fixture(*, clock=10000000, usage=100, path="/sys/fs/cgroup"):
    pressure = resource_profile.parse_pressure("some avg10=0.00 avg60=0.00 avg300=0.00 total=0")
    return {"started_monotonic_ns": clock, "finished_monotonic_ns": clock + 1,
        "cgroup_path": path, "memory.current": 32 * 1024**2, "memory.peak": 64 * 1024**2,
        "memory.swap.current": 0, "cpu.stat": {"usage_usec": usage},
        "memory.stat": {"anon": 20 * 1024**2, "file": 1024**2},
        "memory.events": {"oom": 0, "oom_kill": 0, "oom_group_kill": 0, "max": 0},
        "memory.pressure": copy.deepcopy(pressure), "cpu.pressure": copy.deepcopy(pressure),
        "io.pressure": copy.deepcopy(pressure),
        "self_memory": {"VmRSS": 24 * 1024**2, "VmHWM": 25 * 1024**2, "VmSwap": 0},
        "self_ru_maxrss_bytes": 25 * 1024**2}


def typed_compute_result():
    """Typed, non-native receipt using real pure functions once, no fixtures/DB."""
    unit = "rc6-native-" + "1" * 32 + ".service"
    envelope = cgroup_fixture()
    envelope["membership"] = "0::/system.slice/" + unit
    envelope["path"] += "/system.slice/" + unit
    envelope["cpuset.cpus.effective"] = str(envelope["affinity"][0])
    proof = unit_filter_fixture(os.getpid(), os.getppid(), 0)
    proof["source_binding"].pop("plan_sha256")
    samples = [metric_fixture(path=envelope["path"]),
               metric_fixture(clock=20000000, usage=10100, path=envelope["path"])]
    return {"schema": "porota.rc6.compute-resource-observation.v1", "scope": resource_profile.COMPUTE_SCOPE,
        "status": "PURE_CANDIDATE_CONTROLS_OBSERVED_PENDING_EXTERNAL_FIN", "unit": unit,
        "source_sha": "a" * 40, "source_tree": "b" * 40,
        "plan_sha256": resource_profile.exact_plan(resource_profile.PLAN_V2)[1],
        "identity": proof["identity"], "native_namespace_filter": proof,
        "kernel_envelope_before": envelope, "kernel_envelope_after": copy.deepcopy(envelope),
        "resource_samples": samples, "resource_summary": resource_profile.summarize_metrics(samples),
        "compute_controls": resource_profile.run_original_compute_controls(),
        "source_mount": {"options": ["ro"]}, "source_readonly": True,
        "data_or_fixture_writes": False, "disk_equivalence_claimed": False,
        "persistent_disk_compatibility": "NO_VERIFICADO", "product_resource_compatibility": "NO_VERIFICADO",
        "whole_droplet_compatibility": "NO_VERIFICADO", "representative_full_workload": False,
        "original_G4_G5_started": False, "G0_G8_qualification": False,
        "artifact_transfer_bytes": None, "artifact_transfer_bytes_inferred": False,
        "FIN": "PENDING_ORIGINAL_EXTERNAL_NATIVE_MANAGER", "real_orders_sent": 0}


def verify_typed_compute(result):
    resource_profile.verify_compute_result(result, source_sha="a" * 40, source_tree="b" * 40,
        plan_sha256=resource_profile.exact_plan(resource_profile.PLAN_V2)[1], unit=result["unit"],
        birth={"identity": result["native_namespace_filter"]["identity"]})


def test_v2_preserves_limits_and_original_consumers_requires_owner_and_remains_proposed():
    legacy, _ = resource_profile.exact_plan()
    proposal, _ = resource_profile.exact_plan(resource_profile.PLAN_V2)
    for key in ("cpu_maximum", "memory_maximum_bytes", "swap_maximum_bytes", "pids_maximum",
                "storage_maximum_bytes", "inode_maximum", "minimum_free_inode_fraction"):
        assert proposal[key] == legacy[key]
    assert proposal["suites"] == legacy["suites"] + list(resource_profile.PROTECTED_SUITES)
    assert proposal["owner_contract_acceptance_required"] is True
    assert proposal["original_material_workloads"] == {
        "G4_catalog_count": 12000, "G4_observations": 60000, "G4_factual_paper_exits": 5,
        "G5_cuts": 1202, "G5_storage_maximum_bytes": 512 * 1024**2, "G5_depth": 32,
        "G5_retention_hours": 9, "G5_temporary_retention_hours": 1}
    assert proposal["representative_full_workload"] is False


@pytest.mark.parametrize("key,value", [
    ("storage_maximum_bytes", 512 * 1024**2), ("memory_maximum_bytes", 2 * 1024**3),
    ("owner_contract_acceptance_required", False), ("representative_full_workload", True),
    ("compute_probe_qualification", True), ("compute_probe_maximum_control_bytes", 65537),
])
def test_v2_cannot_raise_original_limit_or_admit_contract_implicitly(tmp_path, key, value):
    proposal, _ = resource_profile.exact_plan(resource_profile.PLAN_V2)
    proposal[key] = value
    path = tmp_path / "altered.json"
    path.write_bytes(resource_profile.canonical(proposal))
    with pytest.raises(ValueError, match="SCOPE_OR_FIXED_LIMIT_CHANGED"):
        resource_profile.exact_plan(path)


def test_v2_cannot_drop_protected_concurrent_risk_exit_or_eod_consumer(tmp_path):
    proposal, _ = resource_profile.exact_plan(resource_profile.PLAN_V2)
    proposal["suites"].pop()
    path = tmp_path / "altered.json"
    path.write_bytes(resource_profile.canonical(proposal))
    with pytest.raises(ValueError, match="COMPLETE_ORIGINAL_PROTECTED_CONSUMERS_REQUIRED"):
        resource_profile.exact_plan(path)


def test_v2_physical_disk_readback_is_scoped_and_never_durability_or_droplet_proof():
    plan, _ = resource_profile.exact_plan(resource_profile.PLAN_V2)
    limits = resource_profile.validate_filesystem(physical_filesystem_fixture(), plan)
    assert limits["persistent_disk_latency_or_durability_qualified"] is False
    assert limits["aggregate_storage_limit_bytes"] == 256 * 1024**2


@pytest.mark.parametrize("key,value,reason", [
    ("status", "UNKNOWN", "BLOCK_AND_BACKING_READBACK_REQUIRED"),
    ("block_rdev", 19, "BLOCK_DEVICE_REBOUND"),
    ("backing_filesystem", "tmpfs", "LOOP_BYTES_OR_FILESYSTEM_REQUIRED"),
    ("backing_allocated_bytes", 4096, "LOOP_BYTES_OR_FILESYSTEM_REQUIRED"),
    ("backing_bytes", 512 * 1024**2, "LOOP_BYTES_OR_FILESYSTEM_REQUIRED"),
    ("backing_ancestry", {"status": "UNKNOWN"}, "LOOP_BACKING_ANCESTRY_UNVERIFIED"),
])
def test_ext4_label_or_sparse_loop_on_tmpfs_cannot_claim_persistent_disk(key, value, reason):
    plan, _ = resource_profile.exact_plan(resource_profile.PLAN_V2)
    disk = physical_filesystem_fixture()
    disk["physical_backing"][key] = value
    with pytest.raises(ValueError, match=reason):
        resource_profile.validate_filesystem(disk, plan)


def test_recursive_loop_ancestry_cannot_hide_tmpfs_or_unknown_virtual_block_device():
    plan, _ = resource_profile.exact_plan(resource_profile.PLAN_V2)
    disk = physical_filesystem_fixture()
    ancestry = disk["physical_backing"]["backing_ancestry"]
    ancestry["backend"] = "FULLY_ALLOCATED_LOOP_ON_PHYSICAL_FILESYSTEM"
    ancestry.update(backing_filesystem="tmpfs", backing_bytes=4096, backing_allocated_bytes=4096,
        backing_device_number=20, backing_inode=444, backing_ancestry={"status": "UNKNOWN"})
    with pytest.raises(ValueError, match="LOOP_BACKING_ANCESTRY_UNVERIFIED"):
        resource_profile.validate_filesystem(disk, plan)


def test_metrics_report_kernel_peak_self_rss_pressure_cpu_sampling_limits_without_extrapolation():
    samples = [metric_fixture(), metric_fixture(clock=20000000, usage=10100)]
    summary = resource_profile.summarize_metrics(samples, kernel_peak_rss_bytes=26 * 1024**2)
    assert summary["memory_kernel_peak_bytes"] == 64 * 1024**2
    assert summary["self_rss_sampled_peak_bytes"] == 24 * 1024**2
    assert summary["owned_worker_kernel_peak_rss_bytes"] == 26 * 1024**2
    assert summary["cpu_sampled_peak_percent"] == 100
    assert summary["cpu_peak_is_sampled_interval_rate_not_continuous_peak"] is True
    assert summary["self_rss_does_not_include_other_processes"] is True
    assert summary["cgroup_memory_includes_cache_and_all_members"] is True


@pytest.mark.parametrize("key,value,reason", [
    ("memory.peak", True, "TYPED_ACTUAL_TELEMETRY_REQUIRED"),
    ("memory.current", 65 * 1024**2, "TYPED_ACTUAL_TELEMETRY_REQUIRED"),
    ("memory.stat", None, "TYPED_ACTUAL_TELEMETRY_REQUIRED"),
    ("memory.events", None, "TYPED_ACTUAL_TELEMETRY_REQUIRED"),
    ("memory.swap.current", 1024, "SWAP_OBSERVED"),
    ("cpu.stat", {"usage_usec": 0}, "CPU_STAT_COUNTER_RECYCLED"),
    ("started_monotonic_ns", 10000000, "TELEMETRY_CLOCK_OR_CGROUP_REBOUND"),
    ("cgroup_path", "/sys/fs/cgroup/foreign", "TELEMETRY_CLOCK_OR_CGROUP_REBOUND"),
    ("memory.pressure", {"some": {"avg10": float("nan"), "avg60": 0, "avg300": 0, "total": 0}},
        "PRESSURE_READBACK_MALFORMED"),
])
def test_missing_malformed_or_recycled_telemetry_fails_closed(key, value, reason):
    samples = [metric_fixture(), metric_fixture(clock=20000000, usage=10100)]
    samples[1][key] = value
    with pytest.raises(ValueError, match=reason):
        resource_profile.summarize_metrics(samples)


@pytest.mark.parametrize("event", ["oom", "oom_kill", "oom_group_kill", "max"])
def test_any_oom_or_hard_memory_hit_invalidates_compute_observation(event):
    samples = [metric_fixture(), metric_fixture(clock=20000000, usage=10100)]
    samples[1]["memory.events"][event] = 1
    with pytest.raises(ValueError, match="OOM_OR_HARD_LIMIT_HIT"):
        resource_profile.summarize_metrics(samples)


def test_real_original_pure_candidate_controls_are_exactly_five_risk_and_two_guard_calls():
    controls = resource_profile.run_original_compute_controls()
    assert controls["actual_capacity_calls"] == 5
    assert controls["actual_emergency_guard_calls"] == 2
    assert controls["repetitions"] == 1
    assert controls["actual_outputs"][5:] == ["", "EMERGENCY_POSITION_CAP"]
    assert controls["fixtures_created"] is False
    assert controls["db_calls"] == controls["network_calls"] == controls["financial_ticks"] == 0
    assert controls["inputs"] == resource_profile.original_compute_controls()[0]


def test_pure_candidate_regression_red_is_discriminated_and_never_relabelled(monkeypatch):
    from dataclasses import replace
    import de_concurrent_risk_capacity_hf6 as candidate
    original = candidate.capacity
    monkeypatch.setattr(candidate, "capacity", lambda *args, **kwargs: replace(original(*args, **kwargs), admitted=False))
    with pytest.raises(ValueError, match="REAL_CANDIDATE_PURE_REGRESSION_RED"):
        resource_profile.run_original_compute_controls()


def test_typed_compute_observation_positive_requires_live_actor_limits_but_does_not_mint_fin():
    result = typed_compute_result()
    verify_typed_compute(result)
    assert result["FIN"] == "PENDING_ORIGINAL_EXTERNAL_NATIVE_MANAGER"
    assert result["product_resource_compatibility"] == "NO_VERIFICADO"
    assert result["representative_full_workload"] is False


@pytest.mark.parametrize("key,value,reason", [
    ("cpu.max", "50000 100000", "EXACT_ONE_CPU_ONE_GIB_REQUIRED"),
    ("memory.max", str(512 * 1024**2), "EXACT_ONE_CPU_ONE_GIB_REQUIRED"),
    ("membership", "0::/system.slice/foreign.service", "OWN_COMPUTE_UNIT_MEMBERSHIP_REQUIRED"),
    ("mount_options", ["rw"], "COMPUTE_READONLY_CURRENT_CGROUP_REQUIRED"),
    ("cgroup_namespace_inode", 789, "OWN_COMPUTE_UNIT_MEMBERSHIP_REQUIRED"),
    ("processes", [], "CURRENT_CGROUP_MEMBERSHIP_REQUIRED"),
    ("cpuset.cpus.effective", "0,1", "KERNEL_SINGLE_CPUSET_REQUIRED"),
    ("cpuset.cpus.effective", "", "KERNEL_SINGLE_CPUSET_REQUIRED"),
])
def test_typed_compute_receipt_cannot_substitute_nominal_unit_or_lower_smaller_limit(key, value, reason):
    result = typed_compute_result()
    result["kernel_envelope_after"][key] = value
    with pytest.raises(ValueError, match=reason):
        verify_typed_compute(result)


@pytest.mark.parametrize("key,value", [
    ("representative_full_workload", True), ("G0_G8_qualification", True),
    ("data_or_fixture_writes", True), ("disk_equivalence_claimed", True),
    ("product_resource_compatibility", "PASS"), ("FIN", "PASS"),
    ("artifact_transfer_bytes_inferred", True), ("real_orders_sent", True),
])
def test_typed_compute_cannot_self_certify_fin_product_or_synthetic_resource_reduction(key, value):
    result = typed_compute_result()
    result[key] = value
    with pytest.raises(ValueError, match="CANNOT_QUALIFY_OR_RELABEL_MATERIAL_WORKLOAD"):
        verify_typed_compute(result)


def test_compute_receipt_cannot_replace_actual_results_with_self_consistent_hashes():
    result = typed_compute_result()
    result["compute_controls"]["actual_outputs"][0]["admitted"] = False
    result["compute_controls"]["actual_outputs_sha256"] = resource_profile.digest(
        resource_profile.canonical(result["compute_controls"]["actual_outputs"]))
    with pytest.raises(ValueError, match="REAL_CANDIDATE_PURE_REGRESSION_RED"):
        verify_typed_compute(result)


def test_compute_receipt_cannot_claim_metrics_from_another_cgroup():
    result = typed_compute_result()
    for row in result["resource_samples"]:
        row["cgroup_path"] = "/sys/fs/cgroup/foreign"
    result["resource_summary"] = resource_profile.summarize_metrics(result["resource_samples"])
    with pytest.raises(ValueError, match="ACTUAL_COMPUTE_TELEMETRY_REQUIRED"):
        verify_typed_compute(result)


def test_compute_consumer_rejects_serialized_json_custody_before_reading_any_capture():
    with pytest.raises(ValueError, match="ACTUAL_IN_PROCESS_WITNESS_REQUIRED"):
        resource_profile.validate_custody_compute_observation({"resource_probe": True}, binding={})


def typed_custody_compute_input(tmp_path, monkeypatch):
    """Unit-only adapter around custody boundary; never generates authority."""
    from scripts import rc6_privileged_custody as privileged
    result = typed_compute_result()
    identity = result["identity"]
    actor = {"compute_resource_observation": result, "drop": {"native_filter": result["native_namespace_filter"]},
        "after_drop": {"pid": identity["pid"], "start_ticks": str(identity["start_ticks"]), "boot_id": identity["boot_id"]}}
    raw = resource_profile.canonical(actor)
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "0001.raw").write_bytes(raw)
    row = {"relative_source": "root-success.native.log", "capture_file": "0001.raw",
           "bytes": len(raw), "sha256": resource_profile.digest(raw)}
    binding = {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40}
    manifest = resource_profile.canonical({"schema": "porota.rc6.generated-fixture-required-capture.v1",
        "files": [row], "binding": binding, "actual_owned_fin_closed": True, "phase_green": True})
    (capture / "manifest.json").write_bytes(manifest)
    fin = {key: True for key in ("pidfd_exit_observed", "PID1_actual_broker_reap_observed",
                               "owned_cgroup_empty_or_removed", "external_PID1_birth_pidfd_ACK_verified")}
    fin["root_actor"] = {"cgroup": "/system.slice/" + result["unit"]}
    success = {"case": "root-success", "actor": actor,
        "raw": {"path": row["relative_source"], "bytes": len(raw), "sha256": row["sha256"]},
        "kernel": {"pid": identity["pid"], "returncode": 0, "actual_child_reaped": True,
            "owned_children_exhaustion_verified": True, "process_group_absent_after_reap": True,
            "peak_rss_bytes": 26 * 1024**2}}
    receipt = {"resource_probe": True, "capture_root": str(capture),
        "capture_manifest_sha256": resource_profile.digest(manifest), "compute_resource_observation": result,
        "guardian_actual_FIN": copy.deepcopy(fin), "controller_actual_FIN": copy.deepcopy(fin),
        "native_root_proof": {"cases": [success] + [{"case": name} for name in (
            "failure-before-drop", "root-hang", "nonroot-hang")]}}
    monkeypatch.setattr(privileged, "validate_witness", lambda *args, **kwargs: receipt)
    return receipt, binding


def test_typed_custody_compute_validation_uses_original_raw_and_external_fin(tmp_path, monkeypatch):
    receipt, binding = typed_custody_compute_input(tmp_path, monkeypatch)
    result = resource_profile.validate_custody_compute_observation(object(), binding=binding)
    assert result["status"] == "PASS_PURE_COMPUTE_OBSERVATION_ONLY"
    assert result["external_ROOT_guardian_and_FIN_verified"] is True
    assert result["original_actor_raw_sha256"] == receipt["native_root_proof"]["cases"][0]["raw"]["sha256"]
    assert result["product_resource_compatibility"] == "NO_VERIFICADO"
    assert result["whole_droplet_compatibility"] == "NO_VERIFICADO"


def test_custody_compute_top_copy_cannot_substitute_original_actor_raw(tmp_path, monkeypatch):
    receipt, binding = typed_custody_compute_input(tmp_path, monkeypatch)
    receipt["compute_resource_observation"] = {"status": "PASS"}
    with pytest.raises(ValueError, match="TOP_COPY_CANNOT_REPLACE_ORIGINAL_ACTOR_RAW"):
        resource_profile.validate_custody_compute_observation(object(), binding=binding)


def test_custody_compute_actor_copy_must_equal_preserved_original_bytes(tmp_path, monkeypatch):
    receipt, binding = typed_custody_compute_input(tmp_path, monkeypatch)
    receipt["native_root_proof"]["cases"][0]["actor"]["after_drop"]["pid"] += 1
    with pytest.raises(ValueError, match="ORIGINAL_ACTOR_RAW_REBOUND"):
        resource_profile.validate_custody_compute_observation(object(), binding=binding)


def test_closed_wrapper_or_unit_timeout_cannot_replace_external_root_fin(tmp_path, monkeypatch):
    receipt, binding = typed_custody_compute_input(tmp_path, monkeypatch)
    receipt["controller_actual_FIN"]["external_PID1_birth_pidfd_ACK_verified"] = False
    with pytest.raises(ValueError, match="EXTERNAL_ROOT_GUARDIAN_FIN_REQUIRED"):
        resource_profile.validate_custody_compute_observation(object(), binding=binding)


@pytest.mark.parametrize("key,value,reason", [
    ("binding", {"candidate_sha": "0" * 40}, "CAPTURE_BINDING_AND_FIN_REQUIRED"),
    ("actual_owned_fin_closed", False, "CAPTURE_BINDING_AND_FIN_REQUIRED"),
    ("capture_file", "..", "ACTOR_CAPTURE_MEMBER_REQUIRED"),
])
def test_compute_capture_cannot_escape_original_member_or_authenticated_fin(tmp_path, monkeypatch, key, value, reason):
    receipt, binding = typed_custody_compute_input(tmp_path, monkeypatch)
    path = Path(receipt["capture_root"]) / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if key == "capture_file":
        manifest["files"][0][key] = value
    else:
        manifest[key] = value
    altered = resource_profile.canonical(manifest)
    path.write_bytes(altered)
    receipt["capture_manifest_sha256"] = resource_profile.digest(altered)
    with pytest.raises(ValueError, match=reason):
        resource_profile.validate_custody_compute_observation(object(), binding=binding)


def test_compute_controller_uses_existing_certified_guardian_only_and_requires_actions(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(ValueError, match="AUTHENTICATED_ACTIONS_OWNER_REQUIRED"):
        resource_profile.prove_compute_probe(output=tmp_path / "probe", source_sha="a" * 40,
            source_tree="b" * 40, owner_session="unit", plan_sha256="c" * 64, owner_check=lambda: None)
    module = ast.parse(Path(resource_profile.__file__).read_bytes())
    controller = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "prove_compute_probe")
    calls = [node for node in ast.walk(controller) if isinstance(node, ast.Call)]
    guardian = [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr == "prove_custody"]
    assert len(guardian) == 1
    assert any(keyword.arg == "resource_probe" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value is True for keyword in guardian[0].keywords)
    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr in ("Popen", "run", "system") for node in calls)

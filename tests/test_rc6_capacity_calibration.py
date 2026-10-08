"""Decoder/control guards only; no root API, quota image or producer is run."""
from __future__ import annotations

import copy
import ctypes
import errno
from pathlib import Path
import stat
from types import SimpleNamespace
import venv

import pytest

from scripts import rc6_capacity_calibration as c


@pytest.mark.parametrize("mode,image,quota", [("capability", 5 * c.GIB, 512 * c.MIB), ("bootstrap", 26 * c.GIB, 20 * c.GIB)])
def test_backing_image_and_project_quota_are_distinct_hard_units(mode, image, quota):
    value = c.limits_for(mode)
    assert value == {"backing_image_bytes": image, "project_hard_limit_bytes": quota,
                     "residual_reserve_bytes": 4 * c.GIB}
    assert c.validate_limits(mode, value) == value


@pytest.mark.parametrize("field", ["backing_image_bytes", "project_hard_limit_bytes", "residual_reserve_bytes"])
@pytest.mark.parametrize("invalid", [True, 0, float("nan"), -1, "5368709120"])
def test_rebound_or_noninteger_quota_blocks(field, invalid):
    value = c.limits_for("capability")
    value[field] = invalid
    with pytest.raises(ValueError, match="CALIBRATION_HARD_LIMIT_REBOUND"):
        c.validate_limits("capability", value)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}'])
def test_json_rejects_ambiguous_or_nonfinite_authority(raw):
    with pytest.raises(ValueError):
        c.decode(raw)


def observation(bound, *, controls=0):
    return {"available_bytes": bound + c.RESERVE + controls, "free_inodes": 10,
            "total_inodes": 100, "fragment_bytes": 4096}


def test_before_image_capacity_charges_controls_and_reserve_separately():
    limit = 5 * c.GIB
    measured = observation(limit, controls=c.CONTROLS)
    c.require_capacity(measured, limit, control_bytes=c.CONTROLS)
    measured["available_bytes"] -= 1
    with pytest.raises(ValueError, match="CALIBRATION_CAPACITY_RESERVE_BLOCKED"):
        c.require_capacity(measured, limit, control_bytes=c.CONTROLS)


def test_less_than_ten_percent_inodes_blocks_before_producer():
    measured = observation(5 * c.GIB)
    measured["free_inodes"] = 9
    with pytest.raises(ValueError, match="CALIBRATION_INODE_RESERVE_BLOCKED"):
        c.require_capacity(measured, 5 * c.GIB)


def test_outer_control_bound_charges_both_capture_copies_before_any_cleanup():
    bound = c.outer_control_peak_bound(4096)
    assert bound["total_bytes"] == sum(bound["components"].values())
    assert bound["components"]["internal_authenticated_capture_bytes"] == 128 * c.MIB
    assert bound["components"]["carrier_authenticated_capture_bytes"] == 128 * c.MIB
    assert bound["premature_cleanup_credit_bytes"] == 0 and bound["future_capture_copies_included"] == 2
    measured = observation(5 * c.GIB, controls=128 * c.MIB)
    with pytest.raises(ValueError, match="CALIBRATION_CAPACITY_RESERVE_BLOCKED"):
        c.require_capacity(measured, 5 * c.GIB, control_bytes=bound["total_bytes"])
    measured = observation(5 * c.GIB, controls=bound["total_bytes"])
    c.require_capacity(measured, 5 * c.GIB, control_bytes=bound["total_bytes"])


@pytest.mark.parametrize("fragment", [True, 0, -1, 65537, float("nan"), "4096"])
def test_unknown_control_allocation_units_block(fragment):
    with pytest.raises(ValueError, match="CALIBRATION_CONTROL_ALLOCATION_UNIT_UNKNOWN"):
        c.outer_control_peak_bound(fragment)


@pytest.mark.parametrize("field", ["available_bytes", "free_inodes", "total_inodes", "fragment_bytes"])
def test_capacity_boolean_is_not_a_measurement(field):
    measured = observation(c.GIB)
    measured[field] = True
    with pytest.raises(ValueError, match="CALIBRATION_CAPACITY_NOT_MEASURED"):
        c.require_capacity(measured, c.GIB)


def test_seccomp_wire_blocks_project_mutation_but_preserves_original_native_fin(monkeypatch):
    names, rules = {}, []
    class NativeFunction:
        def __init__(self, fn):
            self.fn = fn
        def __call__(self, *args):
            return self.fn(*args)
    def resolve(name):
        key = name.decode()
        names.setdefault(key, len(names) + 1)
        return names[key]
    def add(context, action, number, count, comparisons):
        rules.append({"action": action, "name": next(k for k, v in names.items() if v == number),
                      "args": [(comparisons[i].arg, comparisons[i].op, comparisons[i].a, comparisons[i].b)
                               for i in range(count)]})
        return 0
    fake = SimpleNamespace(seccomp_init=NativeFunction(lambda _: 1),
        seccomp_syscall_resolve_name=NativeFunction(resolve), seccomp_rule_add_array=NativeFunction(add),
        seccomp_load=NativeFunction(lambda _: 0), seccomp_release=NativeFunction(lambda _: None))
    monkeypatch.setattr(c.ctypes, "CDLL", lambda *args, **kwargs: fake)
    c.install_seccomp()
    ioctls = [row["args"][0][2] for row in rules if row["name"] == "ioctl"]
    assert ioctls == [c.FSSETXATTR, *c.SETFLAGS]
    assert "quotactl" in names and "quotactl_fd" in names and "mount_setattr" in names
    assert not any(name in names for name in ("wait4", "waitid", "fork", "vfork", "execve", "kill", "prctl-sub-reaper"))
    clones = [row["args"][0] for row in rules if row["name"] == "clone"]
    assert clones == [(0, 7, flag, flag) for flag in c.NAMESPACE_CLONE_FLAGS]
    options = [row["args"][0][2] for row in rules if row["name"] == "prctl"]
    assert 4 in options and 36 not in options and 37 not in options
    assert next(row["action"] for row in rules if row["name"] == "clone3") == 0x00050000 | errno.ENOSYS


def test_unknown_native_syscall_fails_before_filter_load(monkeypatch):
    class Function:
        def __init__(self, fn):
            self.fn = fn
        def __call__(self, *args):
            return self.fn(*args)
    loaded = []
    fake = SimpleNamespace(seccomp_init=Function(lambda _: 1), seccomp_syscall_resolve_name=Function(lambda _: -1),
        seccomp_rule_add_array=Function(lambda *args: 0), seccomp_load=Function(lambda _: loaded.append(True)),
        seccomp_release=Function(lambda _: None))
    monkeypatch.setattr(c.ctypes, "CDLL", lambda *args, **kwargs: fake)
    with pytest.raises(ValueError, match="CALIBRATION_SECCOMP_SYSCALL_UNKNOWN"):
        c.install_seccomp()
    assert loaded == []


def test_loop_configure_transfers_hold_without_autoclear_gap(monkeypatch):
    opened, closed, configured = [], [], []
    def open_(path, flags):
        descriptor = 91 + len(opened)
        opened.append((str(path), descriptor))
        return descriptor
    monkeypatch.setattr(c.os, "open", open_)
    monkeypatch.setattr(c.os, "close", closed.append)
    monkeypatch.setattr(c.os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFBLK if fd == 93 else stat.S_IFREG,
                                                                 st_dev=27, st_ino=888))
    def ioctl(fd, request, *args):
        if request == 0x4C82:
            return 7
        if request == 0x4C0A:
            value = c.LoopConfig.from_buffer_copy(args[0])
            configured.append((value.fd, value.block_size, value.info.flags))
            return 0
        value = c.LoopInfo()
        value.device, value.inode, value.flags = 27, 888, 4
        args[0][:] = bytes(value)
        return 0
    monkeypatch.setattr(c.fcntl, "ioctl", ioctl)
    device, proof, held = c.configure_loop(Path("/UNIT_ONLY/image.ext4"))
    assert device == "/dev/loop7" and proof["autoclear"] is True and held == 93
    assert configured == [(92, 4096, 4)] and closed == [91, 92]
    assert ctypes.sizeof(c.LoopInfo) == 232 and ctypes.sizeof(c.LoopConfig) == 304


def test_busy_loop_is_closed_and_not_retried(monkeypatch):
    descriptors, closed, attempts = iter((91, 92, 93)), [], []
    monkeypatch.setattr(c.os, "open", lambda *args: next(descriptors))
    monkeypatch.setattr(c.os, "close", closed.append)
    monkeypatch.setattr(c.os, "fstat", lambda _: SimpleNamespace(st_mode=stat.S_IFBLK))
    def ioctl(fd, request, *args):
        attempts.append(request)
        if request == 0x4C82:
            return 7
        raise OSError(errno.EBUSY, "UNIT_LOOP_ALREADY_OWNED")
    monkeypatch.setattr(c.fcntl, "ioctl", ioctl)
    with pytest.raises(OSError) as error:
        c.configure_loop(Path("/UNIT_ONLY/image.ext4"))
    assert error.value.errno == errno.EBUSY and attempts == [0x4C82, 0x4C0A]
    assert closed == [93, 91, 92]


def test_existing_or_reused_loop_vetoes_cleanup_without_detaching(monkeypatch):
    monkeypatch.setattr(c.Path, "exists", lambda _: True)
    with pytest.raises(ValueError, match="CALIBRATION_LOOP_CONSUMER_OR_REUSE_UNKNOWN"):
        c.loop_gone({"number": 7, "autoclear": True})


def test_forged_root_request_never_reaches_privileged_setup(monkeypatch):
    touched = []
    monkeypatch.setattr(c, "configure_loop", lambda *args: touched.append(True))
    with pytest.raises(ValueError, match="CALIBRATION_AUTHENTIC_ROOT_REQUEST_REQUIRED"):
        c.root_setup({})
    assert touched == []


@pytest.mark.parametrize("tag", ["shared:7", "master:8", "propagate_from:9"])
def test_kernel_shared_mount_preflight_prevents_any_loop_or_mount(monkeypatch, tag):
    called = []
    monkeypatch.setattr(c, "configure_loop", lambda *args, **kwargs: called.append("loop"))
    monkeypatch.setattr(c, "setup_command", lambda *args, **kwargs: called.append("mount"))
    current = c.os.stat("/proc/self/ns/mnt").st_ino
    original = c.Path.read_text
    def metadata(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            return "31 30 8:1 / / rw,relatime " + tag + " - ext4 /dev/UNIT rw\n"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(c.Path, "read_text", metadata)
    # Synthetic parent identity exercises RED decoding only. This test cannot
    # and does not assert a positive native namespace or a root custody token.
    with pytest.raises(ValueError, match="CALIBRATION_SHARED_PROPAGATION_VETO"):
        c.private_mount_preflight(current + 1)
    assert called == []


def test_root_setup_vetoes_shared_propagation_before_any_mutation(monkeypatch):
    called = []
    # Authentication is isolated only to reach the RED propagation branch.
    # Synthetic UID/ns metadata is not a positive custody or isolation proof.
    monkeypatch.setattr(c, "validate_root_request", lambda _: None)
    monkeypatch.setattr(c.os, "getuid", lambda: 0)
    monkeypatch.setattr(c.os, "geteuid", lambda: 0)
    original_stat, original_read = c.os.stat, c.Path.read_text
    def metadata_stat(path, *args, **kwargs):
        if str(path) == "/proc/self/ns/mnt":
            return SimpleNamespace(st_ino=11)
        return original_stat(path, *args, **kwargs)
    def metadata_read(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            return "31 30 8:1 / / rw shared:7 - ext4 /dev/UNIT rw\n"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(c.os, "stat", metadata_stat)
    monkeypatch.setattr(c.Path, "read_text", metadata_read)
    monkeypatch.setattr(c, "configure_loop", lambda *args, **kwargs: called.append("loop"))
    monkeypatch.setattr(c, "setup_command", lambda *args, **kwargs: called.append("mount"))
    with pytest.raises(ValueError, match="CALIBRATION_SHARED_PROPAGATION_VETO"):
        c.root_setup({"owner_uid": 1001, "mount_namespace_inode": 10})
    assert called == []


def test_same_actual_namespace_prevents_setup_without_argv_assumption(monkeypatch):
    called = []
    monkeypatch.setattr(c, "configure_loop", lambda *args, **kwargs: called.append("loop"))
    monkeypatch.setattr(c, "setup_command", lambda *args, **kwargs: called.append("mount"))
    with pytest.raises(ValueError, match="CALIBRATION_NEW_ROOT_MOUNT_NAMESPACE_REQUIRED"):
        c.private_mount_preflight(c.os.stat("/proc/self/ns/mnt").st_ino)
    assert called == []


def test_private_mountinfo_fixture_is_parser_only_not_native_privilege_proof():
    text = "31 30 8:1 / / rw,relatime - ext4 /dev/UNIT rw\n32 31 0:5 / /proc ro - proc proc ro\n"
    result = c.validate_private_mountinfo(text)
    assert result == {"mount_count": 2, "shared_master_propagate_from_absent": True,
                      "mountinfo_sha256": c.digest(text.encode())}
    assert "actual_mount_namespace_inode" not in result and "kernel_namespace_differs" not in result


@pytest.mark.parametrize("text", ["", "not mountinfo", "31 30 8:1 / / rw - ext4 /dev/UNIT rw EXTRA",
                                  "31 30 8:1 / / rw - ext4 /dev/UNIT rw\n31 30 8:1 / / rw - ext4 /dev/UNIT rw\n"])
def test_unknown_or_ambiguous_mount_census_blocks(text):
    with pytest.raises(ValueError):
        c.validate_private_mountinfo(text)


def test_root_uid_cannot_be_claimed_as_original_nonroot_source_owner(monkeypatch):
    touched = []
    request = {"schema": "porota.rc6.capacity-root-request.v1", "mode": "capability",
               "limits": c.limits_for("capability"), "owner_uid": 0, "owner_gid": 1001,
               "binding": {}, "namespace_receipt": {"schema": "porota.rc6.generated-fixture-consumer-binding.v1",
                                                      "cleanup_authority_granted": False, "binding": {}}}
    monkeypatch.setattr(c, "configure_loop", lambda *args: touched.append(True))
    with pytest.raises(ValueError, match="CALIBRATION_ROOT_NONROOT_ISSUER_REQUIRED"):
        c.root_setup(request)
    assert touched == []


def test_nonroot_cannot_enter_privilege_drop_role(monkeypatch):
    monkeypatch.setattr(c.os, "getuid", lambda: 1001)
    monkeypatch.setattr(c.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(c.ctypes, "CDLL", lambda *args, **kwargs: pytest.fail("NO_PRIVILEGED_SYSCALL_ALLOWED"))
    with pytest.raises(ValueError, match="CALIBRATION_ROOT_SETUP_SINGLE_THREAD_REQUIRED"):
        c.drop_privileges(1001, 1001)


def product_policy():
    return c.decode(c.read(c.ROOT / "ops/policy/rc6-supply-chain-v1.json"))


def test_exact_original157_names_versions_and_four_sdist_classes():
    policy = product_policy()
    packages = [[row["name"], row["version"]] for row in policy["packages"] + policy["build_tools"]]
    assert c.validate_product_closure(packages, policy) is True


@pytest.mark.parametrize("mutation", ["version", "duplicate", "missing", "extra", "sdist"])
def test_package_count_alone_does_not_qualify_original157(mutation):
    policy = product_policy()
    packages = [[row["name"], row["version"]] for row in policy["packages"] + policy["build_tools"]]
    if mutation == "version":
        packages[0][1] += ".UNKNOWN"
    elif mutation == "duplicate":
        packages[0] = packages[1][:]
    elif mutation == "missing":
        packages.pop()
    elif mutation == "extra":
        packages.append(["UNKNOWN", "0"])
    else:
        policy["allowed_sdists"] = ["msgpack"]
    with pytest.raises(ValueError, match="CALIBRATION_(PRODUCT157_NAMES_VERSIONS_REBOUND|ORIGINAL_FOUR_SDISTS_REQUIRED)"):
        c.validate_product_closure(packages, policy)


def test_missing_pinned_interpreters_blocks_without_download(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tmp_path))
    monkeypatch.setattr(c.subprocess, "run", lambda *args, **kwargs: pytest.fail("NO_PYTHON_DOWNLOAD_OR_PRODUCER_ALLOWED"))
    with pytest.raises(ValueError, match="CALIBRATION_PREINSTALLED_PINNED_PYTHON_REQUIRED"):
        c.verify_python(tmp_path / "Python/3.11.16/x64/bin/python3.11", "311")


def test_tool_cache_alias_cannot_escape_pinned_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TOOL_CACHE", str(tmp_path))
    binary = tmp_path / "Python/3.11.16/x64/bin/python3.11"
    binary.parent.mkdir(parents=True)
    target = tmp_path / "foreign-python"
    target.write_bytes(b"UNIT_DECODE_ONLY_NO_INTERPRETER_EXECUTION")
    target.chmod(0o755)
    binary.symlink_to(target)
    with pytest.raises(ValueError, match="CALIBRATION_PINNED_REGULAR_ELF_REQUIRED"):
        c.verify_python(binary, "311")


def test_read_refuses_unowned_alias_payload(tmp_path):
    original, hardlink, symlink = tmp_path / "original", tmp_path / "alias", tmp_path / "symlink"
    original.write_bytes(b"UNIT_EVIDENCE")
    hardlink.hardlink_to(original)
    symlink.symlink_to(original)
    with pytest.raises(ValueError, match="CALIBRATION_PRIVATE_REGULAR_CONTROL_REQUIRED"):
        c.read(hardlink)
    with pytest.raises(ValueError, match="CALIBRATION_NOFOLLOW_PATH_REQUIRED"):
        c.read(symlink)
    assert original.read_bytes() == b"UNIT_EVIDENCE"


def test_actual_tiny_envbuilder_lib64_is_counted_without_following(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    # Original POSIX EnvBuilder, no installer, ensurepip, SDK or financial code.
    venv.EnvBuilder(symlinks=False, with_pip=False).create(project / "venv")
    original_alias = project / "venv/lib64"
    assert original_alias.is_symlink() and original_alias.readlink() == Path("lib")
    result = c.inventory(project)
    rows = [row for row in result["symlinks"] if row["path"] == "venv/lib64"]
    assert rows == [{"path": "venv/lib64", "target": "lib", "target_kind": "OWN_PROJECT", "target_followed": False}]
    assert result["symbolic_targets_followed"] is False and result["retained_entries"] > 5
    assert original_alias.is_symlink() and original_alias.readlink() == Path("lib")


def test_external_symbolic_reference_is_preserved_and_not_followed(tmp_path):
    project, foreign = tmp_path / "project", tmp_path / "foreign"
    project.mkdir()
    foreign.write_bytes(b"FOREIGN_RETAINS_ITS_BYTES")
    alias = project / "alias"
    alias.symlink_to(foreign)
    with pytest.raises(ValueError, match="CALIBRATION_SYMBOLIC_REFERENCE_NOT_OWN_OR_PINNED_READONLY"):
        c.inventory(project)
    assert foreign.read_bytes() == b"FOREIGN_RETAINS_ITS_BYTES" and alias.is_symlink()


def capability_fixture():
    sha, tree, hashes = "a" * 40, "b" * 40, {"scripts/rc6_capacity_calibration.py": "c" * 64}
    receipt = {"schema": c.SCHEMA, "mode": "capability", "status": "GREEN", "actual_capability_proved": True,
        "qualification_claimed": False, "source_sha": sha, "source_tree": tree, "code_hashes": hashes,
        "read_contract_sha256": "e" * 64,
        "real_orders_sent": 0, "limits": c.limits_for("capability"), "kernel": {
        "actual_child_reaped": True, "kernel_pre_popen_echild_verified": True, "owned_children_exhaustion_verified": True,
        "process_group_absent_after_reap": True, "remaining_owned_children": []}, "cleanup": {"namespace_removed": True}}
    origin = {"schema": "porota.rc6.capacity-capability-artifact-origin.v1", "source_sha": sha, "source_tree": tree,
        "read_contract_sha256": "e" * 64,
        "downloaded_exact_bytes_verified": True, "original_outer_native_fin_verified": True,
        "actual_outer_cleanup_verified": True, "source_code_hashes_verified": hashes, "qualification_claimed": False,
        "run_attempt": 1, "producer_receipt_sha256": c.digest(c.wire(receipt)), "artifact_id": 2,
        "run_id": 3, "artifact_digest": "sha256:" + "d" * 64}
    prerequisite = {key: origin[key] for key in ("artifact_id", "run_id", "artifact_digest")}
    return receipt, origin, prerequisite


@pytest.mark.parametrize("field", ["downloaded_exact_bytes_verified", "original_outer_native_fin_verified", "actual_outer_cleanup_verified"])
def test_self_declared_green_without_authenticated_actions_bytes_blocks(field):
    receipt, origin, prior = capability_fixture()
    origin[field] = False
    with pytest.raises(ValueError, match="CALIBRATION_CAPABILITY_ARTIFACT_ORIGIN_REQUIRED"):
        c.validate_capability(receipt, origin, sha=receipt["source_sha"], tree=receipt["source_tree"],
                              code_hashes=receipt["code_hashes"], prerequisite=prior)


def test_workflow_success_without_scope_cleanup_is_not_capability():
    receipt, origin, prior = capability_fixture()
    receipt["cleanup"]["namespace_removed"] = False
    origin["producer_receipt_sha256"] = c.digest(c.wire(receipt))
    with pytest.raises(ValueError, match="CALIBRATION_CAPABILITY_PHYSICAL_FIN_REQUIRED"):
        c.validate_capability(receipt, origin, sha=receipt["source_sha"], tree=receipt["source_tree"],
                              code_hashes=receipt["code_hashes"], prerequisite=prior)


def test_control_plan_and_guards_claim_no_qualification_or_financial_tick():
    plan = c.seccomp_plan()
    assert plan["no_new_privileges"] is True
    assert c.MAX_ENTRIES == 100000 and c.CONTROLS == 128 * c.MIB
    assert c.RESERVE == 4 * c.GIB
    assert "wait4" not in plan["denied_syscalls"]

"""Decoder/control guards only; no root API, quota image or producer is run."""
from __future__ import annotations

import copy
import ctypes
import errno
import os
from pathlib import Path
import stat
import struct
import subprocess
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
    ioctls = [row["args"][0][3] for row in rules if row["name"] == "ioctl"]
    assert ioctls == [c.FSSETXATTR, *c.SETFLAGS]
    assert all(row["args"][0][1:3] == (7, 0xFFFFFFFF) for row in rules if row["name"] == "ioctl")
    assert "quotactl" in names and "quotactl_fd" in names and "mount_setattr" in names
    assert not any(name in names for name in ("wait4", "waitid", "fork", "vfork", "execve", "kill", "prctl-sub-reaper"))
    clones = [row["args"][0] for row in rules if row["name"] == "clone"]
    assert clones == [(0, 7, flag, flag) for flag in c.NAMESPACE_CLONE_FLAGS]
    options = [row["args"][0][3] for row in rules if row["name"] == "prctl"]
    assert all(row["args"][0][1:3] == (7, 0xFFFFFFFF) for row in rules if row["name"] == "prctl")
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


@pytest.mark.parametrize("saved_errno", [errno.EBUSY, errno.EPERM, errno.ENOSYS])
def test_readonly_syscall_failure_preserves_discriminating_native_metadata(monkeypatch, saved_errno):
    calls = []
    def syscall(*args):
        calls.append(args)
        ctypes.set_errno(saved_errno)
        return -1
    monkeypatch.setattr(c.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(syscall=syscall))
    monkeypatch.setattr(c.platform, "machine", lambda: "x86_64")
    origin = {"original_mount_id": 37, "schema": "UNIT_METADATA_ONLY"}
    with pytest.raises(c.CalibrationOperationError, match="CALIBRATION_RECURSIVE_PRIVATE_READONLY_REQUIRED") as error:
        c.mount_all_readonly(backing_origin=origin, issuer_namespace_inode=99)
    details = error.value.details
    assert details["operation"] == "mount_setattr" and details["syscall_number"] == 442
    assert details["return"] == -1 and details["errno"] == saved_errno
    assert details["errno_name"] == errno.errorcode[saved_errno]
    assert details["flags"] == 0x8000 and details["attributes"]["set"] == 1 and details["attribute_size"] == 32
    assert details["issuer_mount_namespace_inode"] == 99 and details["backing_origin"] == origin
    assert details["actual_mount_namespace_inode"] == c.os.stat("/proc/self/ns/mnt").st_ino
    assert details["mountinfo_before_sha256"] and len(calls) == 1
    assert calls[0][:4] == (442, -100, b"/", 0x8000)


def test_readonly_success_decoder_clears_stale_errno_without_native_claim(monkeypatch):
    ctypes.set_errno(errno.EBUSY)
    monkeypatch.setattr(c.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(c.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(syscall=lambda *args: 0))
    record = c.mount_all_readonly()
    assert record["return"] == 0 and record["errno"] == 0
    assert "actual_capability_proved" not in record


def test_transferred_original_image_fd_closes_when_loop_control_open_fails(monkeypatch):
    closed = []
    def open_(*args, **kwargs):
        raise OSError(errno.EACCES, "UNIT_CONTROL_VETO")
    monkeypatch.setattr(c.os, "open", open_)
    monkeypatch.setattr(c.os, "close", closed.append)
    with pytest.raises(OSError) as error:
        c.configure_loop(Path("/UNIT_ONLY/image.ext4"), owned_image_fd=82)
    assert error.value.errno == errno.EACCES and closed == [82]


@pytest.mark.parametrize("returncode", [0, 7])
def test_setup_returncode_preserves_real_waited_echild_control_before_red(monkeypatch, returncode):
    monkeypatch.setattr(c.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=b"UNIT_SETUP_RAW"))
    def exhausted(*args):
        raise ChildProcessError(errno.ECHILD, "UNIT_ONLY_ECHILD_DECODER")
    monkeypatch.setattr(c.os, "waitid", exhausted)
    request = {}
    if returncode:
        with pytest.raises(c.CalibrationOperationError, match="CALIBRATION_OWN_SETUP_COMMAND_FAILED"):
            c.run_setup(request, ["UNIT_NO_COMMAND_EXECUTED"])
    else:
        c.run_setup(request, ["UNIT_NO_COMMAND_EXECUTED"])
    row, = request["setup_commands"]
    assert row["returncode"] == returncode and row["actual_setup_waited_echild"] is True
    assert row["raw_sha256"] == c.digest(b"UNIT_SETUP_RAW")


def test_pending_setup_child_cannot_be_recorded_as_closed(monkeypatch):
    monkeypatch.setattr(c.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b""))
    monkeypatch.setattr(c.os, "waitid", lambda *args: None)
    request = {}
    with pytest.raises(ValueError, match="CALIBRATION_SETUP_CHILD_UNKNOWN"):
        c.run_setup(request, ["UNIT_NO_COMMAND_EXECUTED"])
    assert request.get("setup_commands", []) == []


@pytest.mark.parametrize("denied_errno", [errno.EACCES, errno.EROFS, errno.ENOENT])
def test_original_issuer_root_probe_requires_actual_eacces_not_path_absence(monkeypatch, denied_errno):
    calls = []
    def open_(path, flags, mode):
        calls.append((path, flags, mode))
        raise OSError(denied_errno, "UNIT_ONLY_NO_NATIVE_PROBE")
    monkeypatch.setattr(c.os, "open", open_)
    issuer = c.os.getpid() + 1000
    if denied_errno == errno.EACCES:
        row = c.issuer_root_escape_probe(issuer, Path("/UNIT_OWN_NAMESPACE"))
        assert row["errno"] == errno.EACCES and row["denied"] is True
    else:
        with pytest.raises(ValueError, match="CALIBRATION_ORIGINAL_ISSUER_ROOT_NOT_PROTECTED"):
            c.issuer_root_escape_probe(issuer, Path("/UNIT_OWN_NAMESPACE"))
    assert calls[0][0] == "/proc/" + str(issuer) + "/root/UNIT_OWN_NAMESPACE/issuer-escape-must-not-exist"
    assert calls[0][1] & c.os.O_EXCL and calls[0][1] & c.os.O_NOFOLLOW


def test_original_issuer_root_success_is_escape_and_preserves_its_scope(monkeypatch):
    closed = []
    monkeypatch.setattr(c.os, "open", lambda *args: 91)
    monkeypatch.setattr(c.os, "close", closed.append)
    with pytest.raises(ValueError, match="CALIBRATION_ORIGINAL_ISSUER_ROOT_ESCAPE"):
        c.issuer_root_escape_probe(c.os.getpid() + 1000, Path("/UNIT_OWN_NAMESPACE"))
    assert closed == [91]


@pytest.mark.parametrize("changed", ["mount_id", "namespace_nonce", "st_blocks", "issuer_birth"])
def test_original_mount_opener_negative_binding_closes_every_descriptor(monkeypatch, changed):
    # Auth is isolated exclusively to reach four RED branches. These synthetic
    # kernel/stat rows never produce a positive custody or namespace claim.
    root = Path("/UNIT_ONLY/r6-UNIT")
    binding = {"unit_only": True}
    marker = {"namespace_nonce": "1" * 32, "binding": binding, "mount_id": 37}
    raw = c.wire(marker)
    def metadata(mode, inode, size):
        return SimpleNamespace(st_dev=27, st_ino=inode, st_uid=1001, st_gid=1001, st_mode=mode,
            st_nlink=1, st_size=size, st_blocks=8, st_atime_ns=1, st_mtime_ns=2, st_ctime_ns=3)
    directory = metadata(stat.S_IFDIR | 0o700, 80, 4096)
    marker_stat = metadata(stat.S_IFREG | 0o600, 81, len(raw))
    image_stat = metadata(stat.S_IFREG | 0o600, 82, 5 * c.GIB)
    request = {"namespace_receipt": {"identity": [27, 80, 1001, 1001, 0o700], "mount_id": 37,
                "marker_sha256": c.digest(raw), "namespace_nonce": "1" * 32, "binding": binding},
                "owner_uid": 1001, "owner_gid": 1001, "limits": c.limits_for("capability"),
                "image": str(root / "image.ext4"), "image_identity": c.identity(image_stat)}
    births = iter(({"pid": 123, "start_ticks": "1"}, {"pid": 123, "start_ticks": "2" if changed == "issuer_birth" else "1"}))
    monkeypatch.setattr(c, "validate_root_request", lambda _: root)
    monkeypatch.setattr(c, "issuer_kernel_identity", lambda _: next(births))
    opened, closed = [], []
    def open_(path, flags, *args, **kwargs):
        descriptor = 101 + len(opened)
        opened.append((str(path), flags, kwargs.get("dir_fd"), descriptor))
        return descriptor
    monkeypatch.setattr(c.os, "open", open_)
    monkeypatch.setattr(c.os, "close", closed.append)
    monkeypatch.setattr(c, "descriptor_mount_id", lambda _: 38 if changed == "mount_id" else 37)
    def fstat(fd):
        if fd < 104:
            return directory
        if fd == 104:
            return marker_stat
        result = copy.copy(image_stat)
        if changed == "st_blocks":
            result.st_blocks += 8
        return result
    monkeypatch.setattr(c.os, "fstat", fstat)
    chunks = iter((raw, b""))
    if changed == "namespace_nonce":
        chunks = iter((c.wire({**marker, "namespace_nonce": "2" * 32}), b""))
    monkeypatch.setattr(c.os, "read", lambda *args: next(chunks))
    original_stat = c.os.stat
    monkeypatch.setattr(c.os, "stat", lambda path, **kwargs: marker_stat if path == ".porota-generated-fixture-owner.json"
                        else image_stat if path == "image.ext4" else original_stat(path, **kwargs))
    monkeypatch.setattr(c.Path, "lstat", lambda path: marker_stat if path.name == ".porota-generated-fixture-owner.json" else image_stat)
    with pytest.raises(ValueError, match="CALIBRATION_ISSUER_"):
        c.open_image_on_issuer_mount(request)
    assert sorted(closed) == sorted(row[3] for row in opened)
    assert opened[0][0] == "/proc/123/root" and opened[0][2] is None
    assert all(row[1] & c.os.O_NOFOLLOW for row in opened[1:])
    assert not any("setns" in row[0] or "mount" in row[0] for row in opened)


def test_worker_red_retains_completed_setup_controls_and_operation_errno(tmp_path, monkeypatch, capsys):
    path = tmp_path / "request.json"
    c.publish(path, {"unit_only": "NEGATIVE_DECODER"})
    failure = {"operation": "mount_setattr", "return": -1, "errno": errno.EBUSY,
               "actual_mount_namespace_inode": 21, "backing_origin": {"original_mount_id": 37}}
    setup = {"returncode": 0, "actual_setup_waited_echild": True, "raw_sha256": c.digest(b"UNIT_SETUP_RAW")}
    def red(request):
        request.update(setup_stage="mount_setattr_private_readonly", setup_commands=[setup], backing_origin=failure["backing_origin"])
        raise c.CalibrationOperationError("CALIBRATION_RECURSIVE_PRIVATE_READONLY_REQUIRED", failure)
    monkeypatch.setattr(c, "root_setup", red)
    assert c.root_worker(path) == 1
    row = c.decode(capsys.readouterr().out.encode())["report"]
    assert row["status"] == "BLOQUEADO" and row["actual_capability_proved"] is False
    assert row["operation_failure"] == failure and row["setup_commands"] == [setup]
    assert row["setup_stage"] == "mount_setattr_private_readonly" and row["qualification_claimed"] is False


@pytest.mark.parametrize("mutation,status", [("none", "PREREQUISITES_PRESENT"), ("unknown", "NO_VERIFICADO"),
    ("disabled", "BLOQUEADO"), ("module-not-live", "BLOQUEADO"), ("seccomp", "BLOQUEADO"),
    ("tool", "BLOQUEADO"), ("ext4", "BLOQUEADO")])
def test_readonly_kernel_metadata_cannot_substitute_missing_format(mutation, status):
    config = dict.fromkeys(("CONFIG_QUOTA", "CONFIG_QUOTACTL", "CONFIG_EXT4_FS", "CONFIG_QFMT_V2"), "y")
    filesystems, tools, seccomp = ["ext4"], dict.fromkeys(("sudo", "unshare", "mkfs.ext4")), True
    if mutation == "unknown":
        del config["CONFIG_QFMT_V2"]
    elif mutation == "disabled":
        config["CONFIG_QFMT_V2"] = "n"
    elif mutation == "module-not-live":
        config["CONFIG_QFMT_V2"] = "m"
    elif mutation == "seccomp":
        seccomp = False
    elif mutation == "tool":
        del tools["mkfs.ext4"]
    elif mutation == "ext4":
        filesystems = ["xfs"]
    record = c.evaluate_kernel_prerequisites(config, quota_module_live=False, filesystems=filesystems,
        tools=tools, seccomp_names_known=seccomp)
    assert record["status"] == status and record["actual_capability_proved"] is False
    assert record["module_loading_attempted"] is False and record["qualification_claimed"] is False


def ext4_superblock_fixture():
    raw = bytearray(1024)
    struct.pack_into("<I", raw, 0, 10000)
    struct.pack_into("<H", raw, 0x38, 0xEF53)
    struct.pack_into("<I", raw, 0x18, 2)
    struct.pack_into("<I", raw, 0x64, 0x2100)
    for offset, value in ((0x240, 3), (0x244, 4), (0x26C, 12)):
        struct.pack_into("<I", raw, offset, value)
    return raw


@pytest.mark.parametrize("offset,value", [(0x64, 0x100), (0x64, 0x2000), (0x26C, 0), (0x240, 0), (0x18, 0)])
def test_superblock_decoder_rejects_absent_quota_inums_or_wrong_format(offset, value):
    raw = ext4_superblock_fixture()
    struct.pack_into("<I", raw, offset, value)
    with pytest.raises(ValueError, match="CALIBRATION_EXT4_"):
        c.decode_ext4_superblock(bytes(raw))


def test_superblock_fixture_decodes_bytes_without_claiming_mount_or_kernel_capability():
    raw = bytes(ext4_superblock_fixture())
    record = c.decode_ext4_superblock(raw)
    assert record["prj_quota_inum"] == 12 and record["block_bytes"] == 4096
    assert record["raw_sha256"] == c.digest(raw) and "actual_capability_proved" not in record


@pytest.mark.parametrize("name", ["mount", "quotactl/Q_GETFMT", "quotactl/Q_SETQUOTA", "quotactl/Q_GETQUOTA"])
def test_owned_native_error_preserves_numeric_errno_instead_of_util_text(name):
    def red(*args):
        ctypes.set_errno(errno.ESRCH)
        return -1
    with pytest.raises(c.CalibrationOperationError) as error:
        c.native_operation(name, red, (), metadata={"unit_metadata_only": True})
    record = error.value.details
    assert record["operation"] == name and record["return"] == -1 and record["errno"] == errno.ESRCH
    assert record["errno_name"] == "ESRCH" and record["actual_mount_namespace_inode"] > 0


def test_physical_reserve_is_separate_from_project_hard_limit():
    limit = 512 * c.MIB
    quota = {"hard_bytes": limit, "used_bytes": 4096, "hard_inodes": 100000, "used_inodes": 2, "kernel_readback": True}
    physical = observation(limit - 4096, controls=c.MIB)
    c.require_physical_and_project_capacity(physical, quota, limit)
    # Passing the project statfs projection as physical would hide the reserve.
    project = {**physical, "available_bytes": limit - 4096}
    with pytest.raises(ValueError, match="CALIBRATION_CAPACITY_RESERVE_BLOCKED"):
        c.require_physical_and_project_capacity(project, quota, limit)
    physical["available_bytes"] -= 1
    with pytest.raises(ValueError, match="CALIBRATION_CAPACITY_RESERVE_BLOCKED"):
        c.require_physical_and_project_capacity(physical, quota, limit)


def test_nonroot_live_project_statfs_binds_immutable_root_readback(monkeypatch):
    limit = 512 * c.MIB
    original = {"project_id": 37, "hard_bytes": limit, "hard_inodes": 100000, "used_bytes": 0,
                "used_inodes": 0, "kernel_readback": True}
    observation = {"total_bytes": limit, "total_inodes": 100000, "available_bytes": limit - 8192, "free_inodes": 99998}
    monkeypatch.setattr(c, "filesystem", lambda _: observation)
    result = c.live_project_quota(Path("/UNIT_ONLY/project"), original)
    assert result["used_bytes"] == 8192 and result["used_inodes"] == 2
    assert original["used_bytes"] == 0 and result["measurement_method"] == "KERNEL_PROJINHERIT_STATFS_PROJECTION"
    observation["total_bytes"] += 4096
    with pytest.raises(ValueError, match="CALIBRATION_LIVE_PROJECT_LIMIT_REBOUND"):
        c.live_project_quota(Path("/UNIT_ONLY/project"), original)


def test_partial_red_capture_reads_raw_only_after_closed_own_fin(tmp_path):
    project = tmp_path / "project"
    controls = project / "controls"
    controls.mkdir(parents=True)
    (controls / "failed.log").write_bytes(b"FIRST_ERROR_ORIGINAL_RAW")
    (controls / "failed.kernel.json").write_bytes(b'{"UNIT_CLOSED_FIN_DECODER":true}\n')
    report = {"producer_state": "FIN_CLOSED", "status": "BLOQUEADO", "commands": [
        {"label": "failed", "log": str(controls / "failed.log")}], "actual_capability_proved": False}
    manager = {"pre_capture_kernel_state": lambda: {"kernel_echild_verified": True, "unit_decoder_only": True}}
    c.finalize_nonroot_report({"project": str(project)}, report, manager)
    assert report["required_raw_complete"] and report["actual_own_fin_closed"]
    assert report["status"] == "BLOQUEADO" and report["raw_files"][1]["sha256"] == c.digest(b"FIRST_ERROR_ORIGINAL_RAW")


def test_unknown_nonroot_fin_preserves_image_and_never_reads_payload(monkeypatch):
    monkeypatch.setattr(c, "read", lambda *args: pytest.fail("UNKNOWN_FIN_PAYLOAD_MUST_NOT_BE_READ"))
    manager = {"pre_capture_kernel_state": lambda: pytest.fail("UNKNOWN_FIN_MUST_NOT_BE_SYNTHESIZED")}
    report = {"producer_state": "LAUNCHING", "commands": []}
    c.finalize_nonroot_report({"project": "/UNIT_ONLY/project"}, report, manager)
    assert report["actual_own_fin_closed"] is False and report["required_raw_complete"] is False


def test_missing_first_failure_raw_cannot_authorize_cleanup(tmp_path):
    (tmp_path / "controls").mkdir()
    report = {"producer_state": "FIN_CLOSED", "commands": [{"label": "lost", "log": str(tmp_path / "controls/lost.log")}]}
    c.finalize_nonroot_report({"project": str(tmp_path)}, report,
        {"pre_capture_kernel_state": lambda: {"kernel_echild_verified": True, "unit_decoder_only": True}})
    assert report["required_raw_complete"] is False and report["raw_capture_reason"] == "CALIBRATION_FIRST_FAILURE_REQUIRED_RAW_MISSING"


def test_system_code_reader_allows_real_hardlink_without_cleanup_authority(tmp_path):
    code, alias = tmp_path / "tool", tmp_path / "alias"
    code.write_bytes(b"UNIT_EXECUTABLE_BYTES")
    os.link(code, alias)
    record = c.readonly_system_code(code)
    assert record["sha256"] == c.digest(b"UNIT_EXECUTABLE_BYTES") and record["bytes"] == 21
    assert record["identity_before"]["st_nlink"] == record["identity_after"]["st_nlink"] == 2
    assert record["cleanup_authority_granted"] is False and record["source_financial_contract_modified"] is False
    assert code.exists() and alias.exists()


def test_system_code_reader_preserves_ten_fields_including_allocated_blocks(tmp_path, monkeypatch):
    path = tmp_path / "tool"
    path.write_bytes(b"UNIT_EXECUTABLE_BYTES")
    original, calls = c.os.fstat, []
    def changed(fd):
        result = original(fd)
        calls.append(fd)
        if len(calls) == 2:
            return SimpleNamespace(**{**c.identity(result), "st_blocks": result.st_blocks + 8})
        return result
    monkeypatch.setattr(c.os, "fstat", changed)
    with pytest.raises(ValueError, match="CALIBRATION_SYSTEM_CODE_CHANGED"):
        c.readonly_system_code(path)


def loop_birth_decoder_fixture():
    # All identities here are decoder inputs, never native custody evidence.
    image = dict.fromkeys(("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
                           "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns"), 1)
    claim = {"namespace_nonce": "1" * 32, "mount_id": 37, "marker_sha256": "2" * 64}
    request = {"binding": {"unit_decoder_only": True}, "namespace_receipt": claim, "image_identity": image,
               "source_sha": "a" * 40, "source_tree": "b" * 40, "mount_namespace_inode": 11,
               "issuer": {"boot_id": "UNIT_DECODER_ONLY"}}
    loop = {"number": 0, "backing_device": 1, "backing_inode": 1, "autoclear": True,
            "backing_origin": {"image_identity": image, "original_mount_id": 37,
                               "namespace_nonce": claim["namespace_nonce"], "marker_sha256": claim["marker_sha256"]}}
    value = {"schema": "porota.rc6.authenticated-loop-birth.v1", "binding": request["binding"],
             "namespace_nonce": claim["namespace_nonce"], "source_sha": request["source_sha"],
             "source_tree": request["source_tree"], "request_sha256": "3" * 64,
             "image_identity": image, "original_mount_id": 37, "issuer_mount_namespace_inode": 11,
             "actor_mount_namespace_inode": 12, "actor_pid": 1, "actor_start_ticks": "1",
             "boot_id": "UNIT_DECODER_ONLY", "before_readonly_and_privilege_drop": True,
             "qualification_claimed": False, "loop": loop}
    return request, copy.deepcopy(value)


@pytest.mark.parametrize("field,value", [("request_sha256", "4" * 64), ("namespace_nonce", "5" * 32),
    ("actor_pid", True), ("actor_start_ticks", "0"), ("actor_mount_namespace_inode", 11),
    ("actor_mount_namespace_inode", 0), ("original_mount_id", 38), ("qualification_claimed", True)])
def test_loop_birth_decoder_rejects_rebinding_and_false_actor_identity(field, value):
    request, birth = loop_birth_decoder_fixture()
    birth[field] = value
    with pytest.raises(ValueError, match="CALIBRATION_LOOP_BIRTH_BINDING_REBOUND"):
        c.validate_loop_birth(birth, request, "3" * 64)


@pytest.mark.parametrize("mutation", ["image-bool", "backing-bool", "loop-bool", "autoclear", "marker"])
def test_loop_birth_decoder_rejects_boolean_inodes_and_foreign_loop(mutation):
    request, birth = loop_birth_decoder_fixture()
    if mutation == "image-bool":
        birth["image_identity"]["st_ino"] = True
    elif mutation == "backing-bool":
        birth["loop"]["backing_inode"] = True
    elif mutation == "loop-bool":
        birth["loop"]["number"] = True
    elif mutation == "autoclear":
        birth["loop"]["autoclear"] = False
    else:
        birth["loop"]["backing_origin"]["marker_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="CALIBRATION_LOOP_BIRTH_"):
        c.validate_loop_birth(birth, request, "3" * 64)


def test_allocated_backing_reserve_cannot_be_replaced_by_sparse_same_size_image(tmp_path):
    image = tmp_path / "image.ext4"
    with image.open("wb") as stream:
        stream.truncate(8192)
    request = {"image": str(image), "image_identity": c.identity(image.lstat()),
               "limits": {"backing_image_bytes": 8192}}
    assert image.lstat().st_blocks * 512 < 8192
    with pytest.raises(ValueError, match="CALIBRATION_OWN_BACKING_RESERVATION_RELEASED"):
        c.own_image_superblock(request)


def test_setup_timeout_preserves_raw_prefix_and_vetoes_complete_raw(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["UNIT_NO_COMMAND_EXECUTED"], 300, output=b"FIRST_ERROR_TIMEOUT_PREFIX")
    monkeypatch.setattr(c.subprocess, "run", timeout)
    def actual_wait_fixture(*args):
        raise ChildProcessError(errno.ECHILD, "UNIT_NEGATIVE_TIMEOUT_CENSUS")
    monkeypatch.setattr(c.os, "waitid", actual_wait_fixture)
    request = {}
    with pytest.raises(c.CalibrationOperationError, match="CALIBRATION_SETUP_TIMEOUT_RAW_PARTIAL"):
        c.run_setup(request, ["UNIT_NO_COMMAND_EXECUTED"])
    row = request["setup_commands"][0]
    assert row["timed_out"] and row["raw_complete"] is False
    assert row["raw_sha256"] == c.digest(b"FIRST_ERROR_TIMEOUT_PREFIX") and row["returncode"] is None


def test_unknown_issuer_fin_cannot_read_controls_or_promote_report():
    class UnknownIssuer:
        receipt = None
        @property
        def evidence_refs(self):
            raise ValueError("OWNED_DIAGNOSTIC_ACTIVE_NATIVE_CONTROL_READ_FORBIDDEN")
    report = {"status": "BLOQUEADO", "payload_upload_safe": False}
    c.attach_issuer_evidence(report, UnknownIssuer())
    assert report["issuer_evidence_state"] == "NO_VERIFICADO_NOT_READ" and report["payload_upload_safe"] is False
    assert "issuer_evidence_refs" not in report


@pytest.mark.parametrize("metadata_error", [False, True])
def test_early_missing_kernel_metadata_never_creates_image_or_inner_fin(tmp_path, monkeypatch, metadata_error, all_present=False):
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    source = tmp_path / "source"
    source.mkdir()
    for member in c.CODE_MEMBERS:
        path = source / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(c.read(c.ROOT / member))
    def git(*argv):
        return subprocess.run(["git", "-C", str(source), *argv], check=True, capture_output=True).stdout.strip().decode()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=RC6 Unit Fixture", "-c", "user.email=rc6-unit@example.invalid", "commit", "-qm", "UNIT_FIXTURE_ONLY")
    sha, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    binding = {"candidate_sha": sha, "candidate_tree": tree, "producer": "capacity-probe", "attempt_id": "UNIT_RED_ONLY",
               "owner_id": "UNIT_RED_ONLY", "runner_class": "github-hosted/ubuntu-24.04", "workload_fingerprint": "1" * 64}
    namespace = owned.create_namespace(tmp_path, binding)
    claim_path, binding_path, admission_path = (tmp_path / name for name in ("claim.json", "binding.json", "admission.json"))
    c.publish(claim_path, owned.namespace_receipt(namespace))
    c.publish(binding_path, binding)
    c.publish(admission_path, {"schema": "porota.rc6.capacity-diagnostic-admission.v1", "status": "ADMITTED_DIAGNOSTIC_NOT_STARTED",
        "gate": "capacity-probe", "source_sha": sha, "source_tree": tree, "owner_session": "UNIT_RED_ONLY",
        "qualification_claimed": False, "material_gates": [], "real_orders_sent": 0, "mode": "PRODUCTION_PAPER / SIMULATION",
        "real_routes": "NOT_CALLED", "ppi_watch": "UNTOUCHED", "DEPLOY_OWNER": "NOT_ACQUIRED", "read_contract_sha256": "2" * 64,
        "scope": {"schema": "porota.rc6.capacity-diagnostic-scope.v1", "mode": "capacity-probe", **c.limits_for("capability"),
                  "financial_tick_allowed": False, "qualification_claimed": False}, "actions_origin": {"run": {"id": 123}}})
    for name, value in {"RC6_CALIBRATION_ADMISSION_JSON": str(admission_path), "GITHUB_RUN_ID": "123",
                        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading"}.items():
        monkeypatch.setenv(name, value)
    # Synthetic actor/Actions metadata reaches only this RED branch; the
    # namespace and Git pins are real. This never proves native admission.
    monkeypatch.setattr(c, "os", SimpleNamespace(**{**vars(os), "getuid": lambda: 1001, "geteuid": lambda: 1001,
        "posix_fallocate": lambda *args: pytest.fail("NO_IMAGE_ALLOCATION_ALLOWED")}))
    pins, original_pin = [], c.source_pin
    def pin(*args):
        original_pin(*args)
        pins.append(args)
    monkeypatch.setattr(c, "source_pin", pin)
    def unavailable(mode):
        if metadata_error:
            raise PermissionError(errno.EACCES, "UNIT_CONFIG_UNAVAILABLE")
        return {"schema": "porota.rc6.readonly-kernel-quota-prerequisites.v1",
                "status": "PREREQUISITES_PRESENT" if all_present else "NO_VERIFICADO",
                "actual_capability_proved": False, "qualification_claimed": False, "module_loading_attempted": False}
    monkeypatch.setattr(c, "kernel_prerequisites", unavailable)
    monkeypatch.setattr(c, "filesystem", lambda *args: pytest.fail("NO_INNER_FILESYSTEM_PRODUCER_ALLOWED"))
    monkeypatch.setattr(c, "root_worker", lambda *args: pytest.fail("NO_PRIVILEGED_ROOT_WORKER_ALLOWED"))
    for name in ("create_namespace", "execute_owned"):
        monkeypatch.setattr(owned, name, lambda *args, **kwargs: pytest.fail("NO_INNER_GENERATION_ALLOWED"))
    args = SimpleNamespace(source_root=source, source_sha=sha, source_tree=tree, mode="capability",
                           binding_json=binding_path, namespace_receipt=claim_path, output=namespace.path / "diagnostic")
    # Neither caller attributes nor environment can authenticate signal custody.
    args.privileged_launch_authorized, args.privileged_signal_custody_proved = True, True
    monkeypatch.setenv("RC6_PRIVILEGED_LAUNCH_AUTHORIZED", "true")
    monkeypatch.setenv("RC6_PRIVILEGED_SIGNAL_CUSTODY_PROVED", "true")
    assert c.main(args, progress=lambda *args: pytest.fail("NO_NATIVE_PROGRESS_EXPECTED")) == 1
    receipt = c.decode(c.read(args.output / "calibration.json"))
    assert receipt["generation"] == "NOT_STARTED" and receipt["inner_namespace_created"] is False
    assert receipt["inner_generation_operations"] == dict.fromkeys(("create_namespace", "image_allocation", "root_worker_launch"), "NOT_CALLED")
    assert receipt["cleanup"]["actual_inner_FIN_claimed"] is False and receipt["actual_capability_proved"] is False
    assert receipt["source_unchanged"] and receipt["required_raw"] == [] and "kernel" not in receipt
    assert c.digest(c.read(args.output / "kernel-prerequisites.json")) == receipt["kernel_prerequisites_ref"]["sha256"]
    assert pins == [(source, sha, tree), (source, sha, tree)]
    assert receipt["privileged_signal_custody"] == {"status": "NO_VERIFICADO", "proved": False,
                                                    "privileged_launch_authorized": False}
    assert receipt["reason"] == "CALIBRATION_PRIVILEGED_SIGNAL_CUSTODY_UNVERIFIED"
    if all_present:
        assert receipt["kernel_prerequisites"]["status"] == "PREREQUISITES_PRESENT"


def test_all_kernel_prerequisites_present_cannot_authorize_privileged_signal_custody(tmp_path, monkeypatch):
    test_early_missing_kernel_metadata_never_creates_image_or_inner_fin(tmp_path, monkeypatch, False, all_present=True)


def test_privileged_signal_custody_block_constant_cannot_be_mutated():
    assert dict(c.PRIVILEGED_SIGNAL_CUSTODY) == {"status": "NO_VERIFICADO", "proved": False,
                                             "privileged_launch_authorized": False}
    with pytest.raises(TypeError):
        c.PRIVILEGED_SIGNAL_CUSTODY["privileged_launch_authorized"] = True

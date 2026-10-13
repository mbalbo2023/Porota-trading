"""Own NONROOT kernel proofs only; no ROOT, namespaces, devices or quota setup."""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import rc6_native_namespace_filter as native
from scripts import rc6_root_actor_seal as seal

ROOT = Path(__file__).absolute().parents[1]


def own_process(code, tmp_path):
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1])\n" + code, str(ROOT), str(tmp_path)],
        cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=20, check=False, env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr.decode()
    assert len(result.stdout) < 128 * 1024
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def observed(tmp_path_factory):
    return own_process("""from scripts import rc6_root_actor_seal as s
import os,json
r=s.install_nonroot_test_seal(owner_uid=os.getuid(),owner_gid=os.getgid())
assert s.assert_current_seal(owner_uid=os.getuid(),owner_gid=os.getgid())==r
os.setresuid(os.getuid(),os.getuid(),os.getuid())
os.setresgid(os.getgid(),os.getgid(),os.getgid())
print(json.dumps({'receipt':r,'uid':os.getuid(),'gid':os.getgid()}))
""", tmp_path_factory.mktemp("root-seal-own-nonroot"))


def test_distinct_root_contract_preserves_original_plan_and_manager_bytes():
    from scripts import rc6_capacity_calibration as calibration
    assert native.build_plan() == calibration.seccomp_plan()
    assert hashlib.sha256(native.DRIVER.read_bytes()).hexdigest() == native.DRIVER_SHA256
    plan = seal.build_plan(1000, 1001)
    assert plan["setresuid_only"] == [1000] * 3 and plan["setresgid_only"] == [1001] * 3
    assert plan["setgroups_only_count"] == 0 and plan["prctl_capbset_drop_range"] == [0, 63]
    assert plan["prctl_dumpable_only"] == 0 and plan["quota_bootstrap_allowed"] is False
    assert not {"fork", "vfork", "execve", "wait4", "waitid", "kill", "setsid"} & set(plan["denied_syscalls"])
    assert not {36, 37} & set(plan["denied_prctl_options"])
    assert not {21, 16, 27, 17} & set(plan["retained_root_capabilities"])
    plan["denied_syscalls"].append("wait4")
    assert "wait4" not in seal.build_plan(1000, 1001)["denied_syscalls"]


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 0xFFFFFFFF, 1.5, "1000"])
def test_owner_sentinels_and_noninteger_values_are_not_drop_authority(value):
    with pytest.raises(ValueError, match="EXACT_NONROOT_OWNER"):
        seal.build_plan(value, 1000)
    with pytest.raises(ValueError, match="EXACT_NONROOT_OWNER"):
        seal.build_plan(1000, value)


def test_actual_filter_and_exact_owner_setters_in_own_nonroot_process(observed):
    receipt = observed["receipt"]
    seal.validate_evidence(receipt, owner_uid=observed["uid"], owner_gid=observed["gid"])
    assert receipt["scope"] == "NONROOT_TEST_ONLY" and receipt["boundary"] is None
    assert receipt["ROOT_custody_certified"] is False and receipt["G0_G8_qualification"] is False
    assert receipt["filters_after"] == receipt["filters_before"] + 1 and receipt["dumpable"] == 0
    assert receipt["seccomp_load_returncode"] == 0 and receipt["tsync_requested"] is True
    assert receipt["kernel_bpf_bytes_readback_claimed"] is False
    names = {row["syscall"] for row in receipt["denial_probes"]}
    assert {"mount", "setns", "unshare", "clone", "clone3", "socket", "socketpair", "ptrace",
        "process_vm_readv", "process_vm_writev", "pidfd_getfd", "setresuid", "setresgid", "setgroups", "capset"} <= names


@pytest.mark.parametrize("change", [
    {"ROOT_custody_certified": True}, {"scope": "ROOT_POST_SETUP_ONLY"}, {"quota_bootstrap_allowed": True},
    {"G0_G8_qualification": True}, {"seccomp_load_returncode": False}, {"filters_after": 999},
    {"dumpable": 1}, {"denial_probes": []}, {"kernel_bpf_bytes_readback_claimed": True},
    {"boundary": {"private": True}}, {"exported_bpf_sha256": "fake"},
])
def test_captured_test_controls_cannot_invent_root_or_weaken_native_evidence(observed, change):
    altered = {**copy.deepcopy(observed["receipt"]), **change}
    with pytest.raises(ValueError):
        seal.validate_evidence(altered, owner_uid=observed["uid"], owner_gid=observed["gid"])


def test_json_or_seccomp2_never_authorizes_current_process(monkeypatch, observed):
    monkeypatch.setattr(seal, "_LIVE", None)
    monkeypatch.setattr(native, "_status", lambda: {"Seccomp": "2", "NoNewPrivs": "1", "Seccomp_filters": "5"})
    with pytest.raises(ValueError, match="CURRENT_PROCESS_INSTALLATION_MISSING"):
        seal.assert_current_seal(owner_uid=observed["uid"], owner_gid=observed["gid"])


def test_root_entry_is_rejected_before_any_root_reduction_in_nonroot_child(tmp_path):
    result = own_process("""from scripts import rc6_root_actor_seal as s
import os,json
s._reduce_root_capabilities=lambda *a: (_ for _ in ()).throw(AssertionError('ROOT mutation called'))
try:
 s.install_root_actor_seal(owner_uid=os.getuid(),owner_gid=os.getgid(),source_binding={'source_sha':'a'*40,'source_tree':'b'*40},
  host_pid_namespace_inode=1,host_mount_namespace_inode=1,host_device_number=1,host_devpts_device_number=1,
  control_root=sys.argv[2])
except ValueError as e:
 print(json.dumps({'reason':str(e)}))
""", tmp_path)
    assert result["reason"] == "ROOT_SEAL_ACTUAL_ROOT_REQUIRED"


def test_fork_exec_inheritance_real_denials_and_original_fin_under_seal(tmp_path):
    worker = """import sys,json,os
sys.path.insert(0,sys.argv[1])
from scripts import rc6_root_actor_seal as s
p=json.loads(sys.argv[2])
try:
 s.assert_current_seal(owner_uid=os.getuid(),owner_gid=os.getgid())
 raise AssertionError('inherited JSON admitted live process')
except ValueError: pass
r=s.install_nonroot_test_seal(owner_uid=os.getuid(),owner_gid=os.getgid(),inherited=p)
print(json.dumps(r))
"""
    result = own_process("""from scripts import rc6_root_actor_seal as s
from scripts import rc6_native_namespace_filter as n
import json,os,runpy,ctypes
p=s.install_nonroot_test_seal(owner_uid=os.getuid(),owner_gid=os.getgid())
rd,wr=os.pipe()
pid=os.fork()
if pid==0:
 os.close(rd)
 f=s.install_nonroot_test_seal(owner_uid=os.getuid(),owner_gid=os.getgid(),inherited=p)
 os.write(wr,json.dumps({'inherited':f['inherited'] is not None,'dumpable':f['dumpable']}).encode())
 os._exit(0)
os.close(wr)
fork=json.loads(os.read(rd,65536));os.close(rd)
assert os.waitpid(pid,0)[1]==0
assert n.digest(n.DRIVER.read_bytes())==n.DRIVER_SHA256
m=runpy.run_path(str(n.DRIVER))
log=Path(sys.argv[2])/'own-native.log'
k=m['managed_native_child']([sys.executable,'-I','-B','-c',WORKER,sys.argv[1],json.dumps(p)],Path(sys.argv[2]),log,
 {'PATH':os.environ.get('PATH','')},10,terminate_grace=2,progress_poll=5)
assert m['managed_custody_closed'](k) and m['managed_phase_green'](k),k
w=json.loads(log.read_bytes())
s.validate_inheritance_evidence(w,p,owner_uid=os.getuid(),owner_gid=os.getgid())
assert s.assert_current_seal(owner_uid=os.getuid(),owner_gid=os.getgid())==p
print(json.dumps({'parent':p,'worker':w,'fork':fork,'kernel':k,'uid':os.getuid(),'gid':os.getgid()}))
""".replace("import json,os,runpy,ctypes", "import json,os,runpy,ctypes\nfrom pathlib import Path").replace("WORKER", repr(worker)), tmp_path)
    seal.validate_inheritance_evidence(result["worker"], result["parent"], owner_uid=result["uid"], owner_gid=result["gid"])
    assert result["fork"] == {"inherited": True, "dumpable": 0}
    assert result["worker"]["inherited"]["inherited_dumpable_observed"] == 1
    assert result["worker"]["dumpable"] == 0
    assert result["kernel"]["actual_child_reaped"] is True
    assert result["kernel"]["subreaper_restoration_readback_verified"] is True
    altered = copy.deepcopy(result["worker"])
    altered["inherited"]["parent_receipt_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="COMPLETE_INHERITANCE"):
        seal.validate_inheritance_evidence(altered, result["parent"], owner_uid=result["uid"], owner_gid=result["gid"])


def test_original_manager_term2_and_actual_killpg_remain_available_under_seal(tmp_path):
    result = own_process("""from scripts import rc6_root_actor_seal as s
from scripts import rc6_native_namespace_filter as n
import json,os,runpy,signal
from pathlib import Path
s.install_nonroot_test_seal(owner_uid=os.getuid(),owner_gid=os.getgid())
m=runpy.run_path(str(n.DRIVER))
k=m['managed_native_child']([sys.executable,'-I','-B','-c',
 'import signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("READY",flush=True);signal.pause()'],
 Path(sys.argv[2]),Path(sys.argv[2])/'own-hang.log',{'PATH':os.environ.get('PATH','')},
 .7,terminate_grace=2,progress_poll=5)
assert m['managed_custody_closed'](k),k
assert k['timed_out'] is True and k['returncode']==-signal.SIGKILL,k
assert all(any(row['signal']==number and row['outcome']=='SENT'
 for row in k['owned_group_signal_observations']) for number in (signal.SIGTERM,signal.SIGKILL)),k
s.assert_current_seal(owner_uid=os.getuid(),owner_gid=os.getgid())
print(json.dumps(k))
""", tmp_path)
    assert result["actual_child_reaped"] is True and result["process_group_absent_after_reap"] is True
    assert result["subreaper_restoration_readback_verified"] is True


def test_foreign_descriptor_is_rejected_without_closing_or_touching_it(monkeypatch):
    monkeypatch.setattr(os, "listdir", lambda _: ["0", "1", "2", "9"])
    monkeypatch.setattr(os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFCHR | 0o600,
        st_dev=1, st_ino=2, st_rdev=os.makedev(1, 3)))
    with pytest.raises(ValueError, match="INHERITED_HOST_DESCRIPTOR_FORBIDDEN"):
        seal._descriptors()


def test_unreviewed_architecture_and_unknown_syscall_fail_before_kernel_load(monkeypatch):
    class Library:
        released = False
        def seccomp_arch_native(self): return 123
    with pytest.raises(ValueError, match="ARCHITECTURE_UNREVIEWED"):
        seal._compile(Library(), seal.build_plan(1000, 1000))
    lib = Library()
    lib.seccomp_arch_native = lambda: native.SUPPORTED_ARCHITECTURES[0]
    lib.seccomp_init = lambda _: 123
    lib.seccomp_attr_set = lambda *a: 0
    lib.seccomp_syscall_resolve_name = lambda _: -1
    lib.seccomp_release = lambda _: setattr(lib, "released", True)
    with pytest.raises(ValueError, match="SYSCALL_UNKNOWN:mount"):
        seal._compile(lib, seal.build_plan(1000, 1000))
    assert lib.released is True


@pytest.fixture
def private_boundary(tmp_path, monkeypatch):
    """Parser/readback models only; no ROOT entry, mount or native admission."""
    control = tmp_path / "own"
    control.mkdir(mode=0o700)
    mount_rows = [
        "1 0 0:1 / / ro - ext4 /dev/root ro",
        "2 1 0:2 / /proc ro - proc proc ro",
        "3 1 0:3 / /dev ro - tmpfs tmpfs rw,size=4096k,nr_inodes=64",
        "4 1 0:4 / /sys/fs/cgroup ro - cgroup2 cgroup ro",
        "5 1 0:5 / " + str(control) + " rw - ext4 /dev/root rw",
        "6 3 0:6 / /dev/pts ro - devpts devpts rw,mode=000,ptmxmode=000",
    ]
    device_stats = {"/dev": SimpleNamespace(st_dev=os.makedev(0, 3), st_mode=stat.S_IFDIR | 0o755),
                    "/dev/pts": SimpleNamespace(st_dev=os.makedev(0, 6), st_mode=stat.S_IFDIR | 0o755)}
    monkeypatch.setattr(seal, "_read", lambda _: "\n".join(mount_rows))
    identity = {"pid": 1, "parent_pid": 0, "native_tid": 1, "start_ticks": 10,
                "pid_namespace_inode": 20, "mount_namespace_inode": 30, "cgroup_namespace_inode": 40,
                "boot_id": "11111111-1111-1111-1111-111111111111"}
    monkeypatch.setattr(seal, "_identity", lambda: identity)
    monkeypatch.setattr(native, "_status", lambda: {"Pid": "1", "Tgid": "1", "NSpid": "1"})
    monkeypatch.setattr(os, "getpid", lambda: 1)
    monkeypatch.setattr(os, "readlink", lambda _: "1")
    monkeypatch.setattr(seal, "_device_inventory", lambda: {"entries": 0, "character_nodes": [],
        "block_devices_absent": True, "foreign_tty_and_loop_control_nodes_absent": True})
    monkeypatch.setattr(seal, "_descriptors", lambda: [])
    actual_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda path, *a, **k: device_stats[str(path)]
        if str(path) in device_stats else actual_stat(path, *a, **k))
    arguments = {"owner_uid": os.getuid(), "owner_gid": os.getgid(), "control_root": control,
        "host_pid_namespace_inode": 10, "host_mount_namespace_inode": 11,
        "host_device_number": 100, "host_devpts_device_number": 101, "initial": True}
    return SimpleNamespace(rows=mount_rows, devices=device_stats, arguments=arguments, identity=identity)


@pytest.mark.parametrize("foreign", ["/", "/tmp", "/var/tmp", "/sys/fs/cgroup", "/dev", "/dev/pts"])
def test_metadata_readback_refuses_foreign_writable_mounts_before_root_actions(private_boundary, foreign):
    model = private_boundary
    model.rows[:] = [line.replace(" ro -", " rw -") if line.split()[4] == foreign else line
                    for line in model.rows]
    if foreign not in {line.split()[4] for line in model.rows}:
        model.rows.append("7 1 0:7 / " + foreign + " rw - tmpfs tmpfs rw")
    with pytest.raises(ValueError, match="FOREIGN_WRITABLE_MOUNT_FORBIDDEN"):
        seal._boundary(**model.arguments)


def test_private_devpts_readback_and_host_provenance_are_explicit_without_root_credit(private_boundary):
    boundary = seal._boundary(**private_boundary.arguments)
    assert boundary["host_devpts_device_number"] == 101
    assert boundary["devpts_mount"]["device"] == os.makedev(0, 6)
    assert boundary["devpts_mount"]["target"] == "/dev/pts"
    assert boundary["private_devpts_readonly"] is True
    assert boundary["host_boundary_provenance_requires_caller_live_custody"] is True
    seal._validate_boundary_evidence(boundary, private_boundary.identity, os.getuid(), os.getgid())
    assert "ROOT_custody_certified" not in boundary


@pytest.mark.parametrize("status,self_link", [
    ({"Pid": "900", "Tgid": "900", "NSpid": "900 1"}, "900"),
    ({"Pid": "1", "Tgid": "1", "NSpid": "900 1"}, "1"),
    ({"Pid": "1", "Tgid": "1"}, "1"),
    ({"Pid": "1", "Tgid": "1", "NSpid": "1"}, "900"),
])
def test_host_or_unproved_proc_view_cannot_use_own_namespace_inode_as_private_view_proof(
        private_boundary, monkeypatch, status, self_link):
    monkeypatch.setattr(native, "_status", lambda: status)
    monkeypatch.setattr(os, "readlink", lambda _: self_link)
    with pytest.raises(ValueError, match="PRIVATE_PROC_PID_VIEW_REQUIRED"):
        seal._boundary(**private_boundary.arguments)


@pytest.mark.parametrize("value", [None, False, True, 0, -1, 1.5, "101"])
def test_untyped_or_unknown_host_devpts_cannot_admit_boundary(private_boundary, value):
    with pytest.raises(ValueError, match="AUTHENTICATED_HOST_BOUNDARY_REQUIRED"):
        seal._boundary(**{**private_boundary.arguments, "host_devpts_device_number": value})


@pytest.mark.parametrize("change,signature", [
    ("host_devpts", "PRIVATE_DEVPTS_REQUIRED"),
    ("wrong_device_readback", "PRIVATE_DEVPTS_REQUIRED"),
    ("symlink", "PRIVATE_DEVPTS_REQUIRED"),
    ("wrong_type", "SINGLE_PRIVATE_DEVPTS_REQUIRED"),
    ("covered_devpts", "SINGLE_PRIVATE_DEVPTS_REQUIRED"),
    ("foreign_devpts_alias", "SINGLE_PRIVATE_DEVPTS_REQUIRED"),
    ("covered_proc", "PRIVATE_PROC_READONLY_REQUIRED"),
    ("foreign_proc_alias", "FOREIGN_PROC_ALIAS_FORBIDDEN"),
    ("duplicate_mount_id", "MOUNT_IDENTITY_AMBIGUOUS"),
])
def test_host_devpts_and_covered_or_aliased_views_are_rejected(private_boundary, change, signature):
    model = private_boundary
    if change == "host_devpts":
        model.arguments["host_devpts_device_number"] = os.makedev(0, 6)
    elif change in ("wrong_device_readback", "symlink"):
        model.devices["/dev/pts"] = SimpleNamespace(st_dev=999 if change == "wrong_device_readback" else 6,
            st_mode=stat.S_IFLNK if change == "symlink" else stat.S_IFDIR)
    elif change == "wrong_type":
        model.rows[-1] = model.rows[-1].replace("- devpts", "- tmpfs")
    elif change == "covered_devpts":
        model.rows.append("7 3 0:7 / /dev/pts ro - devpts devpts ro")
    elif change == "foreign_devpts_alias":
        model.rows.append("7 1 0:101 / /foreign-pts ro - devpts devpts ro")
    elif change == "covered_proc":
        model.rows.append("7 1 0:7 / /proc ro - proc proc ro")
    elif change == "foreign_proc_alias":
        model.rows.append("7 1 0:7 / /foreign-proc ro - proc proc ro")
    else:
        model.rows[-1] = model.rows[-1].replace("6 3 ", "2 3 ")
    with pytest.raises(ValueError, match=signature):
        seal._boundary(**model.arguments)


@pytest.mark.parametrize("change", ["host_device", "host_devpts", "mount_alias", "public_boolean",
                                   "foreign_control", "writable_pts", "host_pid_ns", "host_mount_ns"])
def test_captured_public_boundary_cannot_rebind_devices_mounts_or_controls(private_boundary, change):
    boundary = seal._boundary(**private_boundary.arguments)
    if change == "host_device":
        boundary["host_device_number"] = boundary["device_mount"]["device"]
    elif change == "host_devpts":
        boundary["host_devpts_device_number"] = boundary["devpts_mount"]["device"]
    elif change == "mount_alias":
        boundary["devpts_mount"]["id"] = boundary["proc_mount"]["id"]
    elif change == "public_boolean":
        boundary = {"private_devpts_readonly": True, "host_boundary_provenance_requires_caller_live_custody": True}
    elif change == "foreign_control":
        boundary["control_identity"][2] += 1
    elif change in ("host_pid_ns", "host_mount_ns"):
        key = "pid_namespace_inode" if change == "host_pid_ns" else "mount_namespace_inode"
        boundary["host_" + key] = private_boundary.identity[key]
    else:
        boundary["devpts_mount"]["options"] = ["rw"]
    with pytest.raises(ValueError):
        seal._validate_boundary_evidence(boundary, private_boundary.identity, os.getuid(), os.getgid())


@pytest.mark.parametrize("change", ["host_devpts", "control_inode", "private_pts_mount_id", "public_boolean"])
def test_child_cannot_replace_parents_host_witness_or_owned_control_mount(private_boundary, change):
    parent = seal._boundary(**private_boundary.arguments)
    child = copy.deepcopy(parent)
    seal._require_inherited_boundary(child, parent)
    if change == "host_devpts":
        child["host_devpts_device_number"] += 1
    elif change == "control_inode":
        child["control_identity"][1] += 1
    elif change == "private_pts_mount_id":
        child["devpts_mount"]["id"] += 100
    else:
        child = {"private_devpts_readonly": True}
    if change != "public_boolean":
        seal._validate_boundary_evidence(child, private_boundary.identity, os.getuid(), os.getgid())
    with pytest.raises(ValueError, match="INHERITED_BOUNDARY_REBOUND"):
        seal._require_inherited_boundary(child, parent)


@pytest.mark.parametrize("mode,rdevice,expected", [
    (stat.S_IFBLK, os.makedev(7, 0), "FOREIGN_BLOCK_DEVICE_VISIBLE"),
    (stat.S_IFCHR, os.makedev(10, 237), "FOREIGN_CHARACTER_DEVICE_VISIBLE"),
    (stat.S_IFCHR, os.makedev(136, 1), "FOREIGN_CHARACTER_DEVICE_VISIBLE"),
])
def test_device_inventory_rejects_host_loop_and_tty_nodes_without_opening_them(monkeypatch, mode, rdevice, expected):
    node = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=mode, st_rdev=rdevice))
    monkeypatch.setattr(Path, "iterdir", lambda _: iter([node]))
    with pytest.raises(ValueError, match=expected):
        seal._device_inventory()


def test_device_inventory_has_a_finite_metadata_bound(monkeypatch):
    node = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=stat.S_IFREG, st_rdev=0))
    monkeypatch.setattr(Path, "iterdir", lambda _: iter([node] * (seal.MAX_DEV_ENTRIES + 1)))
    with pytest.raises(ValueError, match="DEVICE_COUNT_BOUND"):
        seal._device_inventory()


def test_live_token_cannot_rebind_source_namespace_or_owner(tmp_path):
    result = own_process("""from scripts import rc6_root_actor_seal as s
import os,json
b={'source_sha':'a'*40,'source_tree':'b'*40}
r=s.install_nonroot_test_seal(owner_uid=os.getuid(),owner_gid=os.getgid(),source_binding=b)
errors=[]
for kwargs in ({'owner_uid':os.getuid()+1,'owner_gid':os.getgid(),'source_binding':b},
 {'owner_uid':os.getuid(),'owner_gid':os.getgid(),'source_binding':{'source_sha':'c'*40,'source_tree':'b'*40}}):
 try:
  s.assert_current_seal(**kwargs)
  raise AssertionError('rebound source/owner accepted')
 except ValueError as e: errors.append(str(e))
s._identity=lambda: {**r['identity'],'mount_namespace_inode':r['identity']['mount_namespace_inode']+1}
try:
 s.assert_current_seal(owner_uid=os.getuid(),owner_gid=os.getgid(),source_binding=b)
 raise AssertionError('rebound namespace accepted')
except ValueError as e: errors.append(str(e))
print(json.dumps(errors))
""", tmp_path)
    assert len(result) == 3
    assert result[1:] == ["ROOT_SEAL_CURRENT_PROCESS_OR_SOURCE_REBOUND"] * 2

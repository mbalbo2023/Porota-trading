"""Cheap own NONROOT process witnesses; never ROOT, quotas or material fixtures."""
import copy
import ctypes
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import rc6_native_namespace_filter as native

ROOT = Path(__file__).absolute().parents[1]


def own_process(code, tmp_path):
    command = [sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1])\n" + code, str(ROOT)]
    completed = subprocess.run(command, cwd=tmp_path, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
    assert completed.returncode == 0, completed.stderr.decode()
    assert len(completed.stdout) < 128 * 1024
    return json.loads(completed.stdout)


def test_plan_is_fresh_exact_original_contract_and_keeps_fork_exec_fin():
    from scripts import rc6_capacity_calibration as calibration
    assert native.build_plan() == calibration.seccomp_plan()
    plan = native.build_plan()
    plan["denied_syscalls"].append("wait4")
    assert "wait4" not in native.build_plan()["denied_syscalls"]
    assert not {"fork", "vfork", "wait4", "waitid", "execve", "kill"} & set(native.DENIED_SYSCALLS)
    assert 36 not in native.PRCTL_DENIED and 37 not in native.PRCTL_DENIED


def test_native_current_process_load_counter_program_hash_and_safe_denials(tmp_path):
    receipt = own_process("""from scripts import rc6_native_namespace_filter as n
import json
r=n.install_filter()
assert n.assert_current_filter()==r
assert n.install_filter()==r
print(json.dumps(r))
""", tmp_path)
    native.validate_evidence(receipt)
    assert receipt["filters_after"] == receipt["filters_before"] + 1
    assert receipt["identity"]["pid"] != os.getpid()
    assert receipt["seccomp_load_returncode"] == 0
    assert receipt["tsync_requested"] is True
    assert receipt["dumpable"] == 0
    assert receipt["kernel_bpf_bytes_readback_claimed"] is False
    names = {row["syscall"] for row in receipt["denial_probes"]}
    assert {"mount", "umount2", "unshare", "setns", "clone", "clone3", "quotactl", "quotactl_fd", "ioctl", "prctl",
            "ptrace", "process_vm_readv", "process_vm_writev"} <= names
    assert all(row["returncode"] == -1 and row["errno"] in (errno.EPERM, errno.ENOSYS)
               for row in receipt["denial_probes"])
    assert receipt["source_binding"] is None
    assert receipt["G0_G8_qualification"] is False


def test_native_fork_exec_inherits_denials_then_worker_loads_own_layer_and_fin_still_works(tmp_path):
    child = """import sys,json
sys.path.insert(0,sys.argv[1])
from scripts import rc6_native_namespace_filter as n
parent=json.loads(sys.argv[2])
try:
 n.assert_current_filter()
 raise AssertionError('JSON/counter admitted current process')
except ValueError as e:
 assert str(e)=='NATIVE_FILTER_CURRENT_PROCESS_INSTALLATION_MISSING'
r=n.install_filter(inherited=parent)
assert n.assert_current_filter()==r
print(json.dumps(r))
"""
    code = """from scripts import rc6_native_namespace_filter as n
import subprocess,json,sys,os,ctypes
r=n.install_filter()
readfd,writefd=os.pipe()
pid=os.fork()
if pid==0:
 os.close(readfd)
 f=n.install_filter(inherited=r)
 os.write(writefd,json.dumps({'dumpable':f['dumpable'],'inherited':f['inherited'] is not None}).encode())
 os._exit(0)
os.close(writefd)
fork_result=json.loads(os.read(readfd,65536))
os.close(readfd)
assert os.waitpid(pid,0)[1]==0
p=subprocess.run([sys.executable,'-I','-B','-c',CHILD,sys.argv[1],json.dumps(r)],
 stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
assert p.returncode==0,p.stderr.decode()
w=json.loads(p.stdout)
assert w['filters_before']==r['filters_after']
assert w['filters_after']==r['filters_after']+1
libc=ctypes.CDLL(None)
assert libc.prctl(36,1,0,0,0)==0
v=ctypes.c_int()
assert libc.prctl(37,ctypes.byref(v),0,0,0)==0 and v.value==1
assert libc.prctl(36,0,0,0,0)==0
print(json.dumps({'supervisor':r,'worker':w,'fork':fork_result,'owned_children_reaped':True}))
""".replace("CHILD", repr(child))
    result = own_process(code, tmp_path)
    parent, worker = result["supervisor"], result["worker"]
    native.validate_evidence(parent)
    native.validate_evidence(worker)
    native.validate_inheritance_evidence(worker, parent)
    inheritance = worker["inherited"]
    assert inheritance["fork_exec_inheritance_observed"] is True
    assert inheritance["parent_receipt_sha256"] == native.digest(native.canonical(parent))
    assert inheritance["identity"]["parent_pid"] == parent["identity"]["pid"]
    assert inheritance["filters_before_own_installation"] == parent["filters_after"]
    assert len(inheritance["native_denials_before_own_installation"]) == len(parent["denial_probes"])
    assert parent["dumpable"] == 0 and worker["dumpable"] == 1
    assert result["fork"] == {"dumpable": 0, "inherited": True}
    assert result["owned_children_reaped"] is True


def test_serialized_counter_or_flag_never_admits_current_process(monkeypatch):
    monkeypatch.setattr(native, "_LIVE", None)
    with pytest.raises(ValueError, match="CURRENT_PROCESS_INSTALLATION_MISSING"):
        native.assert_current_filter()


def test_native_private_installation_cannot_rebind_source(tmp_path):
    result = own_process("""from scripts import rc6_native_namespace_filter as n
import json
b={'source_sha':'a'*40,'source_tree':'b'*40}
r=n.install_filter(source_binding=b)
try:
 n.assert_current_filter(source_binding={'source_sha':'c'*40,'source_tree':'b'*40})
 raise AssertionError('source rebound')
except ValueError as e:
 print(json.dumps({'reason':str(e)}))
""", tmp_path)
    assert result["reason"] == "NATIVE_FILTER_SOURCE_BINDING_REBOUND"


def test_native_success_return_without_real_filter_load_fails_before_any_receipt(tmp_path):
    result = own_process("""from scripts import rc6_native_namespace_filter as n
import json
real=n._library()
class FakeLoad:
 def __getattr__(self,name):
  return (lambda context:0) if name=='seccomp_load' else getattr(real,name)
n._library=lambda:FakeLoad()
try:
 n.install_filter()
 raise AssertionError('native zero return falsely admitted missing filter')
except ValueError as e:
 assert str(e)=='NATIVE_FILTER_NATIVE_INSTALLATION_COUNTER_NOT_INCREMENTED'
try:
 n.assert_current_filter()
 raise AssertionError('missing filter registered as live')
except ValueError as e:
 print(json.dumps({'reason':str(e)}))
""", tmp_path)
    assert result["reason"] == "NATIVE_FILTER_CURRENT_PROCESS_INSTALLATION_MISSING"


def test_cheap_probe_captures_real_native_owned_fin_and_keeps_product_root_and_quota_unqualified(tmp_path):
    destination = tmp_path / "native-proof"
    result = own_process("from scripts import rc6_native_namespace_filter as n\nimport json\n"
        "print(json.dumps(n.run_cheap_probe(output=" + repr(str(destination)) + ")))", tmp_path)
    assert result["status"] == "PASS_NATIVE_FILTER_ONLY"
    assert result["original_native_fin"] is True
    assert result["kernel"]["actual_child_reaped"] is True
    assert result["kernel"]["owned_children_exhaustion_verified"] is True
    assert result["kernel"]["remaining_owned_children"] == []
    assert result["worker"]["identity"]["pid"] == result["kernel"]["pid"]
    assert result["source_binding"] is None  # no frozen Source claim in this local test
    assert all(result[key] is False for key in ("complete_nonroot_privilege_drop_qualified",
        "aggregate_storage_security_qualified", "product157_executed",
        "product_fixture_consumers_executed", "ROOT_executed_by_this_probe", "G0_G8_qualification"))
    assert result["recurring_infrastructure_cost_usd"] == 0
    assert result["worker_raw_sha256"] == native.digest((destination / "worker-native.json").read_bytes())
    assert json.loads((destination / "probe.json").read_bytes()) == result
    assert sum(row.stat().st_size for row in destination.iterdir()) < 128 * 1024


def compiler_fixture(*, resolve_unknown=False):
    resolved, rules, released, attributes = {}, [], [], []

    def resolve(raw):
        name = raw.decode()
        resolved.setdefault(name, len(resolved) + 1)
        return -1 if resolve_unknown else resolved[name]

    def add(context, action, number, count, comparisons):
        rules.append({"name": next(key for key, value in resolved.items() if value == number),
            "action": action, "args": [(comparisons[index].arg, comparisons[index].op,
                comparisons[index].a, comparisons[index].b) for index in range(count)]})
        return 0

    def export(context, descriptor):
        os.write(descriptor, b"\0" * 8)
        return 0

    lib = SimpleNamespace(seccomp_arch_native=lambda: native.SUPPORTED_ARCHITECTURES[0],
        seccomp_init=lambda action: 1, seccomp_attr_set=lambda context, key, value: attributes.append((key, value)) or 0,
        seccomp_syscall_resolve_name=resolve, seccomp_rule_add_array=add, seccomp_export_bpf=export,
        seccomp_release=lambda context: released.append(context),
        seccomp_version=lambda: ctypes.pointer(native.Version(2, 6, 0)))
    return lib, resolved, rules, released, attributes


def test_compiled_native_rules_preserve_all_historical_masks_and_native_fin_calls():
    lib, names, rules, released, attributes = compiler_fixture()
    context, program = native._compiled_context(lib)
    lib.seccomp_release(context)
    assert released == [context]
    assert attributes == [(3, 1), (4, 1)]
    assert all(row["action"] == native.ERRNO | errno.EPERM
        for row in rules if row["name"] != "clone3")
    assert next(row["action"] for row in rules if row["name"] == "clone3") == native.ERRNO | errno.ENOSYS
    assert [row["args"] for row in rules if row["name"] == "clone"] == [
        [(0, native.MASKED_EQ, flag, flag)] for flag in native.NAMESPACE_CLONE_FLAGS]
    assert [row["args"] for row in rules if row["name"] == "ioctl"] == [
        [(1, native.MASKED_EQ, 0xFFFFFFFF, request)] for request in (native.FSSETXATTR, *native.SETFLAGS)]
    assert [row["args"] for row in rules if row["name"] == "prctl"] == [
        [(0, native.MASKED_EQ, 0xFFFFFFFF, option)] for option in native.PRCTL_DENIED]
    assert set(native.DENIED_SYSCALLS) <= names.keys()
    assert not {"fork", "vfork", "wait4", "waitid", "execve", "kill"} & names.keys()
    assert program["rule_count"] == len(rules)


def test_unknown_native_syscall_releases_context_without_loading_a_partial_filter():
    lib, _, rules, released, _ = compiler_fixture(resolve_unknown=True)
    with pytest.raises(ValueError, match="SYSCALL_UNKNOWN"):
        native._compiled_context(lib)
    assert released == [1]
    assert rules == []


@pytest.fixture
def evidence(tmp_path):
    return own_process("from scripts import rc6_native_namespace_filter as n\nimport json\n"
                       "print(json.dumps(n.install_filter()))", tmp_path)


@pytest.mark.parametrize("key,value", [
    ("plan", {}), ("plan_sha256", "0" * 64), ("seccomp_load_returncode", False),
    ("filters_before", True), ("filters_after", 0), ("tsync_requested", False),
    ("no_new_privileges", False), ("kernel_bpf_bytes_readback_claimed", True),
    ("native_architecture", 0), ("dumpable", False), ("G0_G8_qualification", True),
    ("denial_probes", []), ("exported_bpf_bytes", 7), ("exported_bpf_sha256", "fake"),
])
def test_captured_evidence_rejects_counter_plan_bpf_or_native_denial_lies(evidence, key, value):
    changed = copy.deepcopy(evidence)
    changed[key] = value
    with pytest.raises(ValueError, match="NATIVE_FILTER_"):
        native.validate_evidence(changed)


def test_effective_capabilities_or_root_are_rejected_before_native_installation(monkeypatch):
    monkeypatch.setattr(native.os, "getuid", lambda: 0)
    monkeypatch.setattr(native.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native, "_library", lambda: pytest.fail("ROOT called native installer"))
    with pytest.raises(ValueError, match="NONROOT_ZERO_EFFECTIVE_CAPABILITIES_REQUIRED"):
        native.install_filter()


@pytest.mark.parametrize("binding", [{}, {"source_sha": "a" * 40},
    {"source_sha": "a" * 40, "source_tree": "b" * 40, "qualified": True}])
def test_source_binding_does_not_accept_a_caller_authorization_flag(binding):
    with pytest.raises(ValueError, match="EXACT_SOURCE_BINDING_REQUIRED"):
        native.install_filter(source_binding=binding)


@pytest.mark.parametrize("raw", [b'{"status":"RED","status":"PASS"}', b'{"number":NaN}', b'{"number":Infinity}'])
def test_strict_native_stdout_cannot_hide_duplicate_fields_or_nonfinite_values(raw):
    with pytest.raises(ValueError, match="NATIVE_FILTER_"):
        native.decode(raw)


def test_native_probe_rejects_parent_traversal_before_installer_or_any_write(tmp_path, monkeypatch):
    (tmp_path / "prefix").mkdir()
    destination = tmp_path / "prefix" / ".." / "foreign"
    monkeypatch.setattr(native, "install_filter", lambda **kwargs: pytest.fail("noncanonical path reached installer"))
    with pytest.raises(ValueError, match="CHEAP_OUTPUT_OUTSIDE_SOURCE_REQUIRED"):
        native.run_cheap_probe(output=destination)
    assert not (tmp_path / "foreign").exists()

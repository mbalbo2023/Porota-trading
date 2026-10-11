"""Real sealed-memfd tests with dummy bytes; no systemd, ROOT or material run.

Positive issuer/reader authority adapters are explicitly typed unit inputs.
They cannot authenticate a native controller or mint approval. Only the
unprivileged memfd/read-only-file mechanisms execute here, with no real token.
"""
import copy
import errno
import fcntl
import json
import os
from pathlib import Path
import stat

import pytest

from scripts import rc6_ephemeral_api_credential as credential


SHA, TREE = "a" * 40, "b" * 40
BINDING = {"candidate_sha": SHA, "candidate_tree": TREE,
           "producer": "UNIT_READONLY_CREDENTIAL", "owner_id": "unit-owner"}
DUMMY = b"unit-only-dummy-credential"
UNIT = "rc6-native-" + "1" * 32 + ".service"


@pytest.fixture
def own_context(monkeypatch):
    """Typed authority, actual current PID/inodes/fd/seals; no native admission."""
    def typed_authority(sha, tree, binding, owner_check):
        owner_check()
        return credential._kernel_identity()
    monkeypatch.setattr(credential, "_validate_issuer_authority", typed_authority)
    context = credential.create(DUMMY, source_sha=SHA, source_tree=TREE,
                                binding=BINDING, owner_check=lambda: None)
    yield context
    state = credential._ISSUERS[id(context)]
    if not state["closed"]:
        credential.close(context)


@pytest.fixture
def root_copy(tmp_path, monkeypatch):
    """Regular dummy file models systemd's copy; authority/mount remain typed."""
    path = tmp_path / credential.NAME
    path.write_bytes(DUMMY)
    path.chmod(0o400)
    monkeypatch.setattr(credential, "_root_controller", lambda **kwargs: credential._kernel_identity())
    monkeypatch.setattr(credential, "_readonly_mount", lambda path: None)
    monkeypatch.setattr(credential, "_credential_path", lambda unit: path)
    actual_uid, actual_gid = os.getuid(), os.getgid()
    monkeypatch.setattr(credential, "_copy_owner_is_root", lambda details:
                        details.st_uid == actual_uid and details.st_gid == actual_gid)
    monkeypatch.setattr(credential, "_READS", set())
    return path


@pytest.fixture
def typed_root_ids(monkeypatch):
    """Typed ROOT-role input; never changes this process's actual credentials."""
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(credential.os, name, lambda: 0)
    real_listdir = os.listdir
    monkeypatch.setattr(credential.os, "listdir", lambda path:
        ["unit-thread"] if path == "/proc/self/task" else real_listdir(path))


def read_dummy():
    return credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
        binding=BINDING, controller_proof={}, controller_request={})


def validate_context(context, **kwargs):
    return credential.validate(context, source_sha=kwargs.get("source_sha", SHA),
        source_tree=kwargs.get("source_tree", TREE), binding=kwargs.get("binding", BINDING),
        owner_check=kwargs.get("owner_check", lambda: None))


def test_real_memfd_is_sealed_cloexec_600_and_never_stored_in_workspace(own_context):
    metadata = validate_context(own_context)
    descriptor = metadata["fd"]
    details = os.fstat(descriptor)
    assert stat.S_ISREG(details.st_mode)
    assert stat.S_IMODE(details.st_mode) == 0o600
    assert details.st_nlink == 0
    assert fcntl.fcntl(descriptor, credential.F_GET_SEALS) == credential.SEALS
    assert fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
    assert os.pread(descriptor, len(DUMMY), 0) == DUMMY
    assert os.readlink("/proc/self/fd/" + str(descriptor)) == "/memfd:" + credential.NAME + " (deleted)"


@pytest.mark.parametrize("attack", ["write", "grow", "shrink", "add_seal"])
def test_actual_kernel_denies_all_mutations_of_sealed_memfd(own_context, attack):
    descriptor = own_context.metadata["fd"]
    with pytest.raises(OSError) as raised:
        if attack == "write":
            os.pwrite(descriptor, b"x", 0)
        elif attack == "grow":
            os.ftruncate(descriptor, len(DUMMY) + 1)
        elif attack == "shrink":
            os.ftruncate(descriptor, 0)
        else:
            fcntl.fcntl(descriptor, credential.F_ADD_SEALS, credential.F_SEAL_FUTURE_WRITE)
    assert raised.value.errno == errno.EPERM
    assert os.pread(descriptor, len(DUMMY), 0) == DUMMY


def test_metadata_and_loadcredential_property_never_expose_data_or_token_digest(own_context):
    metadata = own_context.metadata
    assert set(metadata) == {"pid", "start_ticks", "boot_id", "uid", "gid", "fd", "name", "bytes"}
    property_value = credential.load_credential_property(own_context, source_sha=SHA, source_tree=TREE,
        binding=BINDING, owner_check=lambda: None)
    assert property_value == "LoadCredential=" + credential.NAME + ":/proc/" + str(os.getpid()) + "/fd/" + str(metadata["fd"])
    assert DUMMY.decode() not in json.dumps(metadata) + property_value + repr(own_context)
    assert not any("digest" in key or "sha" in key or "token" in key for key in metadata)


@pytest.mark.parametrize("payload", [b"", b"x" * 65537, "dummy-string", None])
def test_unbounded_or_non_byte_input_is_rejected_before_memfd(payload):
    with pytest.raises(credential.CredentialError, match="BOUNDED_IN_MEMORY_BYTES_REQUIRED"):
        credential._create_sealed_memfd(payload)


def test_context_json_or_forged_object_does_not_authorize_a_source_fd(own_context):
    with pytest.raises(credential.CredentialError, match="ACTUAL_IN_PROCESS_CONTEXT_REQUIRED"):
        validate_context(own_context.metadata)
    with pytest.raises(credential.CredentialError, match="SYNTHETIC_CONTEXT_FORBIDDEN"):
        credential.CredentialContext(object(), descriptor=own_context.metadata["fd"],
            identity=credential._kernel_identity(), source_sha=SHA, source_tree=TREE, binding=BINDING)


@pytest.mark.parametrize("key", ["candidate_sha", "owner_id", "producer"])
def test_registered_binding_cannot_change_while_same_memfd_remains_open(own_context, key):
    binding = copy.deepcopy(BINDING)
    binding[key] = "0" * 40 if key == "candidate_sha" else "foreign"
    with pytest.raises(credential.CredentialError, match="BINDING_REQUIRED|SOURCE_OR_BINDING_REBOUND"):
        validate_context(own_context, binding=binding)


def test_caller_cannot_mutate_registered_metadata_or_binding(own_context):
    metadata = own_context.metadata
    metadata["fd"] += 1
    assert own_context.metadata["fd"] != metadata["fd"]
    assert validate_context(own_context)["bytes"] == len(DUMMY)


def test_process_birth_rebound_never_uses_inherited_context(own_context, monkeypatch):
    actual = credential._kernel_identity()
    changed = {**actual, "pid": actual["pid"] + 1}
    with monkeypatch.context() as patched:
        patched.setattr(credential, "_kernel_identity", lambda: changed)
        with pytest.raises(credential.CredentialError, match="ISSUER_PROCESS_BIRTH_REBOUND"):
            validate_context(own_context)
    assert validate_context(own_context)["pid"] == actual["pid"]


def test_cloexec_removal_blocks_launch_and_cleanup_closes_only_own_memfd(own_context):
    descriptor = own_context.metadata["fd"]
    fcntl.fcntl(descriptor, fcntl.F_SETFD, 0)
    with pytest.raises(credential.CredentialError, match="LIVE_ORIGINAL_SEALED_CLOEXEC_MEMFD_REQUIRED"):
        validate_context(own_context)
    credential.close(own_context)
    with pytest.raises(OSError) as raised:
        os.fstat(descriptor)
    assert raised.value.errno == errno.EBADF


def test_recycled_descriptor_cannot_be_used_or_closed_as_credential(own_context, tmp_path):
    descriptor = own_context.metadata["fd"]
    backup = os.dup(descriptor)
    foreign_path = tmp_path / "foreign-control"
    foreign_path.write_bytes(b"foreign-control")
    foreign = os.open(foreign_path, os.O_RDWR | os.O_CLOEXEC)
    try:
        os.dup2(foreign, descriptor, inheritable=False)
        with pytest.raises(credential.CredentialError, match="ISSUER_DESCRIPTOR_IDENTITY_REBOUND|DESCRIPTOR_OS_ERROR"):
            validate_context(own_context)
        with pytest.raises(credential.CredentialError, match="CLEANUP_FOREIGN_DESCRIPTOR_FORBIDDEN"):
            credential.close(own_context)
        assert os.pread(descriptor, 15, 0) == b"foreign-control"
    finally:
        os.dup2(backup, descriptor, inheritable=False)
        os.close(backup)
        os.close(foreign)
    assert validate_context(own_context)["bytes"] == len(DUMMY)


def test_mode_mutation_invalidates_loadcredential_but_own_memfd_can_be_closed(own_context):
    descriptor = own_context.metadata["fd"]
    os.fchmod(descriptor, 0o640)
    with pytest.raises(credential.CredentialError, match="ISSUER_DESCRIPTOR_IDENTITY_REBOUND"):
        validate_context(own_context)
    credential.close(own_context)
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_closed_context_is_single_use_and_never_reopens_a_recycled_fd(own_context):
    credential.close(own_context)
    with pytest.raises(credential.CredentialError, match="CONTEXT_ALREADY_CLOSED"):
        validate_context(own_context)
    with pytest.raises(credential.CredentialError, match="CONTEXT_ALREADY_CLOSED"):
        credential.close(own_context)


def test_native_creation_does_not_infer_nonroot_authority_from_a_context(monkeypatch):
    monkeypatch.setattr(credential.os, "getuid", lambda: 0)
    with pytest.raises(credential.CredentialError, match="SINGLE_THREAD_NONROOT_ISSUER_REQUIRED"):
        credential.create(DUMMY, source_sha=SHA, source_tree=TREE, binding=BINDING, owner_check=lambda: None)


def test_dumpable_issuer_cannot_expose_memfd_to_same_uid_peers(monkeypatch):
    monkeypatch.setattr(credential.os, "getuid", lambda: 1001)
    monkeypatch.setattr(credential.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(credential.os, "getgid", lambda: 1001)
    monkeypatch.setattr(credential.os, "getegid", lambda: 1001)
    monkeypatch.setattr(credential.os, "listdir", lambda path: ["1"])
    monkeypatch.setattr(credential, "_dumpable", lambda: 1)
    monkeypatch.setattr(credential, "_create_sealed_memfd", lambda data: pytest.fail("insecure issuer received fd"))
    with pytest.raises(credential.CredentialError, match="ISSUER_DUMPABLE_ZERO_REQUIRED"):
        credential.create(DUMMY, source_sha=SHA, source_tree=TREE, binding=BINDING, owner_check=lambda: None)


def test_token_or_secret_field_is_never_accepted_as_public_binding():
    binding = {**BINDING, "api_token": "dummy"}
    with pytest.raises(credential.CredentialError, match="SECRET_BINDING_FORBIDDEN"):
        credential._exact_binding(SHA, TREE, binding)


def test_root_dummy_copy_is_read_once_closed_then_wipeable(root_copy):
    before = set(os.listdir("/proc/self/fd"))
    value = read_dummy()
    assert type(value) is bytearray and value == DUMMY
    assert set(os.listdir("/proc/self/fd")) == before
    credential.wipe(value)
    assert value == bytearray(len(DUMMY))
    with pytest.raises(credential.CredentialError, match="ROOT_SINGLE_USE_REQUIRED"):
        read_dummy()


@pytest.mark.parametrize("mode", [0o600, 0o440, 0o444, 0o000])
def test_readonly_copy_mode_must_be_root_only_and_owner_unwritable(root_copy, mode):
    root_copy.chmod(mode)
    with pytest.raises(credential.CredentialError, match="ROOT_READONLY_COPY_IDENTITY_REQUIRED"):
        read_dummy()


def test_readonly_copy_hardlink_is_rejected(root_copy):
    os.link(root_copy, root_copy.with_name("extra-link"))
    with pytest.raises(credential.CredentialError, match="ROOT_READONLY_COPY_IDENTITY_REQUIRED"):
        read_dummy()


def test_readonly_copy_alias_rejected_before_any_content_read(root_copy, monkeypatch):
    alias = root_copy.with_name("alias")
    alias.symlink_to(root_copy)
    monkeypatch.setattr(credential, "_credential_path", lambda unit: alias)
    with pytest.raises(credential.CredentialError, match="READONLY_COPY_ALIAS_FORBIDDEN"):
        read_dummy()


def test_writable_mount_cannot_be_inferred_readonly_from_a_400_file(tmp_path, monkeypatch):
    from scripts import rc6_product_resource_compatibility as resource_profile
    monkeypatch.setattr(resource_profile, "containing_mount", lambda *args: {"options": ["rw"]})
    with pytest.raises(credential.CredentialError, match="ACTUAL_READONLY_SYSTEMD_MOUNT_REQUIRED"):
        credential._readonly_mount(tmp_path / "dummy")


@pytest.mark.parametrize("expected", [0, True, 65537, len(DUMMY) + 1])
def test_copied_length_cannot_be_unbounded_or_nominal(root_copy, expected):
    with pytest.raises(credential.CredentialError, match="EXACT_BOUNDED_COPY_BYTES_REQUIRED|READONLY_COPY_IDENTITY_REQUIRED"):
        credential.read_once(unit=UNIT, expected_bytes=expected, source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof={}, controller_request={})


def test_single_use_remains_consumed_after_read_failure(root_copy):
    root_copy.chmod(0o600)
    with pytest.raises(credential.CredentialError, match="ROOT_READONLY_COPY_IDENTITY_REQUIRED"):
        read_dummy()
    root_copy.chmod(0o400)
    with pytest.raises(credential.CredentialError, match="ROOT_SINGLE_USE_REQUIRED"):
        read_dummy()


def test_replaced_copy_during_read_is_rejected_and_partial_buffer_wiped(root_copy, monkeypatch):
    real_read = os.read
    changed = False
    wiped = []
    real_wipe = credential.wipe
    def attack(descriptor, length):
        nonlocal changed
        value = real_read(descriptor, length)
        if value and not changed:
            changed = True
            replacement = root_copy.with_name("replacement")
            replacement.write_bytes(DUMMY)
            replacement.chmod(0o400)
            replacement.replace(root_copy)
        return value
    def spy_wipe(value):
        real_wipe(value)
        wiped.append(bytearray(value))
    monkeypatch.setattr(credential.os, "read", attack)
    monkeypatch.setattr(credential, "wipe", spy_wipe)
    with pytest.raises(credential.CredentialError, match="ROOT_COPY_CHANGED_OR_INCOMPLETE"):
        read_dummy()
    assert wiped == [bytearray(len(DUMMY))]


def test_nonroot_reader_is_denied_before_controller_or_filesystem(monkeypatch):
    monkeypatch.setattr(credential.os, "getuid", lambda: 1001)
    monkeypatch.setattr(credential, "_credential_path", lambda unit: pytest.fail("NONROOT accessed ROOT credential path"))
    with pytest.raises(credential.CredentialError, match="AUTHENTICATED_ROOT_ACTOR_REQUIRED"):
        read_dummy()


def test_reader_verifies_actual_original_controller_before_opening_any_copy(monkeypatch, typed_root_ids):
    from scripts import rc6_privileged_custody as privileged
    def rejected(*args):
        raise ValueError("UNIT_ORIGINAL_NATIVE_CONTROLLER_REJECTED")
    monkeypatch.setattr(privileged, "verify_live_controller", rejected)
    monkeypatch.setattr(credential, "_credential_path", lambda unit: pytest.fail("unproved controller opened credential"))
    request = {"source_sha": SHA, "source_tree": TREE, "binding": BINDING}
    with pytest.raises(ValueError, match="ORIGINAL_NATIVE_CONTROLLER_REJECTED"):
        credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof={}, controller_request=request)


def test_foreign_unit_cannot_use_an_authentic_own_controller_request(monkeypatch, typed_root_ids):
    from scripts import rc6_privileged_custody as privileged
    monkeypatch.setattr(privileged, "verify_live_controller", lambda *args: {"cgroup": "/system.slice/foreign.service"})
    request = {"source_sha": SHA, "source_tree": TREE, "binding": BINDING}
    with pytest.raises(credential.CredentialError, match="ACTUAL_CONTROLLER_UNIT_REBOUND"):
        credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof={}, controller_request=request)


def test_root_source_module_must_match_authenticated_request_hash(monkeypatch, typed_root_ids):
    from scripts import rc6_privileged_custody as privileged
    monkeypatch.setattr(privileged, "verify_live_controller", lambda *args: {"cgroup": "/system.slice/" + UNIT})
    request = {"source_sha": SHA, "source_tree": TREE, "binding": BINDING,
               "code_hashes": {credential.MEMBER: "0" * 64}}
    with pytest.raises(credential.CredentialError, match="AUTHENTICATED_SOURCE_MODULE_REQUIRED"):
        credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof={}, controller_request=request)


def test_immutable_buffer_does_not_claim_python_heap_erasure():
    with pytest.raises(credential.CredentialError, match="MUTABLE_TRANSIENT_BUFFER_REQUIRED"):
        credential.wipe(DUMMY)


def test_module_has_no_unit_launcher_token_environment_or_recursive_cleanup():
    import ast
    module = ast.parse(Path(credential.__file__).read_bytes())
    attributes = {node.func.attr for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not {"Popen", "run", "system", "rmtree", "unlink", "rmdir", "setenv", "putenv"} & attributes
    assert not any(isinstance(node, ast.Attribute) and node.attr == "environ" for node in ast.walk(module))


def typed_broker_oracle(monkeypatch):
    """Typed private-PID1 source proof, no ROOT namespace or native unit."""
    from hashlib import sha256
    from types import SimpleNamespace
    from scripts import rc6_privileged_custody as privileged
    boot = "00000000-0000-0000-0000-000000000000"
    broker = {"pid": 1, "parent_pid": 0, "start_ticks": "10", "boot_id": boot,
        "uids": [0] * 4, "gids": [0] * 4, "namespace_pids": [1], "cgroup": "0::/system.slice/" + UNIT,
        "pid_namespace_inode": 101, "capabilities": {key: 0 for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")}}
    worker = {**copy.deepcopy(broker), "pid": 2, "parent_pid": 1, "start_ticks": "11", "namespace_pids": [2]}
    guardian = {**copy.deepcopy(broker), "pid": 999, "parent_pid": 1, "pid_namespace_inode": 202,
                "namespace_pids": [999]}
    request = {"source_sha": SHA, "source_tree": TREE, "binding": BINDING,
        "code_hashes": {credential.MEMBER: sha256(Path(credential.__file__).read_bytes()).hexdigest()}}
    proof = {"guardian_native_mapping": {"guardian_actual": guardian}, "pid_boundary": {"local": broker}}
    monkeypatch.setattr(privileged, "_external_controller", lambda *args: guardian)
    monkeypatch.setattr(privileged, "_private_identity", lambda *args: {"local": broker})
    monkeypatch.setattr(privileged, "kernel_process", lambda pid: copy.deepcopy(broker if pid == 1 else worker))
    monkeypatch.setattr(credential.os, "getpid", lambda: 1)
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(credential.os, name, lambda: 0)
    real_listdir = os.listdir
    monkeypatch.setattr(credential.os, "listdir", lambda path:
        ["unit-thread"] if path == "/proc/self/task" else real_listdir(path))
    real_stat = os.stat
    monkeypatch.setattr(credential.os, "stat", lambda path, *args, **kwargs:
        SimpleNamespace(st_ino=303 if str(path) == "/proc/2/ns/mnt" else 404)
        if str(path) in ("/proc/2/ns/mnt", "/proc/self/ns/mnt") else real_stat(path, *args, **kwargs))
    return proof, request, broker, worker


def test_broker_oracle_is_a_separate_source_role_and_does_not_relax_worker_default(root_copy):
    with pytest.raises(credential.CredentialError, match="SOURCE_FIXED_READER_ROLE_REQUIRED"):
        credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof={}, controller_request={}, role="GENERIC_ROOT_ORACLE")
    with pytest.raises(credential.CredentialError, match="WORKER_ROLE_CANNOT_ASSUME_BROKER_ORACLE"):
        credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof={}, controller_request={}, worker_birth={})


def test_real_pidfd_live_readback_rejects_a_pid_label_substitution():
    descriptor = os.pidfd_open(os.getpid(), 0)
    try:
        credential._pidfd_live(descriptor, os.getpid())
        with pytest.raises(credential.CredentialError, match="WORKER_PIDFD_TARGET_REBOUND"):
            credential._pidfd_live(descriptor, os.getpid() + 1)
    finally:
        os.close(descriptor)


def test_ordinary_file_descriptor_cannot_be_a_worker_liveness_proof(tmp_path):
    path = tmp_path / "ordinary"
    path.write_bytes(b"unit")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(credential.CredentialError, match="REAL_WORKER_PIDFD_REQUIRED"):
            credential._pidfd_live(descriptor, os.getpid())
    finally:
        os.close(descriptor)


def test_typed_broker_oracle_uses_native_identity_and_worker_pidfd_before_secret_read(monkeypatch):
    proof, request, _, worker = typed_broker_oracle(monkeypatch)
    issued, closed, checked = [], [], []
    monkeypatch.setattr(credential.os, "pidfd_open", lambda pid, flags: issued.append(pid) or 900)
    monkeypatch.setattr(credential.fcntl, "fcntl", lambda fd, operation, *args: fcntl.FD_CLOEXEC)
    monkeypatch.setattr(credential, "_pidfd_live", lambda fd, pid: checked.append((fd, pid)))
    monkeypatch.setattr(credential.os, "close", lambda fd: closed.append(fd))
    _, observer, pid = credential._root_broker_oracle(unit=UNIT, source_sha=SHA, source_tree=TREE,
        binding=BINDING, controller_proof=proof, controller_request=request, worker_birth=worker, worker_pidfd=None)
    assert issued == [2] and observer == 900 and pid == 2 and checked == [(900, 2)]
    # The reader, rather than this authority helper, closes after actual read.
    assert closed == []


@pytest.mark.parametrize("key,value,reason", [
    ("pid", 1, "PRESEAL_ROOT_WORKER_BIRTH_REQUIRED"),
    ("parent_pid", 99, "PRESEAL_ROOT_WORKER_BIRTH_REQUIRED"),
    ("uids", [1001] * 4, "PRESEAL_ROOT_WORKER_BIRTH_REQUIRED"),
    ("gids", [1001] * 4, "PRESEAL_ROOT_WORKER_BIRTH_REQUIRED"),
    ("cgroup", "0::/system.slice/foreign.service", "PRESEAL_ROOT_WORKER_BIRTH_REQUIRED"),
    ("pid_namespace_inode", 202, "PRESEAL_ROOT_WORKER_BIRTH_REQUIRED"),
    ("start_ticks", "999", "ROOT_WORKER_KERNEL_BIRTH_REBOUND"),
])
def test_oracle_cannot_read_before_actual_own_preseal_worker_birth(monkeypatch, key, value, reason):
    proof, request, _, worker = typed_broker_oracle(monkeypatch)
    claim = {**copy.deepcopy(worker), key: value}
    monkeypatch.setattr(credential.os, "pidfd_open", lambda *args: pytest.fail("unborn or foreign worker admitted"))
    with pytest.raises(credential.CredentialError, match=reason):
        credential._root_broker_oracle(unit=UNIT, source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof=proof, controller_request=request, worker_birth=claim, worker_pidfd=None)


def test_oracle_cannot_read_when_actual_broker_differs_from_source_controller(monkeypatch):
    proof, request, _, worker = typed_broker_oracle(monkeypatch)
    proof["pid_boundary"]["local"] = {**proof["pid_boundary"]["local"], "start_ticks": "999"}
    with pytest.raises(credential.CredentialError, match="ACTUAL_PRIVATE_BROKER_REBOUND"):
        credential._root_broker_oracle(unit=UNIT, source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof=proof, controller_request=request, worker_birth=worker, worker_pidfd=None)


def test_oracle_worker_must_have_executed_original_private_mount_setup(monkeypatch):
    from types import SimpleNamespace
    proof, request, _, worker = typed_broker_oracle(monkeypatch)
    monkeypatch.setattr(credential.os, "stat", lambda *args, **kwargs: SimpleNamespace(st_ino=303))
    with pytest.raises(credential.CredentialError, match="WORKER_EXEC_PRIVATE_MOUNT_REQUIRED"):
        credential._root_broker_oracle(unit=UNIT, source_sha=SHA, source_tree=TREE,
            binding=BINDING, controller_proof=proof, controller_request=request, worker_birth=worker, worker_pidfd=None)


def test_oracle_closes_extra_pidfd_before_returning_token_to_caller(root_copy, monkeypatch):
    observer = os.pidfd_open(os.getpid(), 0)
    identity = credential._kernel_identity()
    monkeypatch.setattr(credential, "_root_broker_oracle", lambda **kwargs: (identity, observer, os.getpid()))
    value = credential.read_once(unit=UNIT, expected_bytes=len(DUMMY), source_sha=SHA, source_tree=TREE,
        binding=BINDING, controller_proof={}, controller_request={}, role=credential.BROKER_ORACLE_ROLE, worker_birth={})
    assert value == DUMMY
    credential.wipe(value)
    with pytest.raises(OSError) as raised:
        os.fstat(observer)
    assert raised.value.errno == errno.EBADF


def test_oracle_does_not_close_the_original_manager_pidfd(monkeypatch):
    proof, request, _, worker = typed_broker_oracle(monkeypatch)
    calls, closes = [], []
    def typed_fcntl(fd, operation, *args):
        calls.append((fd, operation, args))
        return 900 if operation == fcntl.F_DUPFD_CLOEXEC else fcntl.FD_CLOEXEC
    monkeypatch.setattr(credential.fcntl, "fcntl", typed_fcntl)
    monkeypatch.setattr(credential, "_pidfd_live", lambda *args: None)
    monkeypatch.setattr(credential.os, "close", lambda fd: closes.append(fd))
    _, observer, _ = credential._root_broker_oracle(unit=UNIT, source_sha=SHA, source_tree=TREE,
        binding=BINDING, controller_proof=proof, controller_request=request, worker_birth=worker, worker_pidfd=800)
    assert observer == 900 and calls[0] == (800, fcntl.F_DUPFD_CLOEXEC, (3,))
    assert closes == []

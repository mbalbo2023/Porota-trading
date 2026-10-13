"""Source-fixed G5A ROOT setup and same-process bounded original acquisition.

This entrypoint belongs to the existing privileged native manager. It does not
launch a unit, obtain a bearer, reconstruct admission from JSON, qualify G5 or
grant cleanup. The proposed contract needs explicit owner acceptance. Its ROOT
worker receives authenticated API documents from the private PID1 broker and
never receives the broker's credential bytes.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import stat
import struct
import sys
import time

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5_contract as g5

MEMBER = "scripts/rc6_g5a_native_runner.py"
REQUEST_SCHEMA = "porota.rc6.g5a-root-request.v1"
WORKER_SCHEMA = "porota.rc6.g5a-native-worker.v1"
OPERATION_SCOPE = "G5A_EVIDENCE_ACQUISITION_ONLY"
SHA40, SHA64 = re.compile(r"[0-9a-f]{40}\Z"), re.compile(r"[0-9a-f]{64}\Z")
GIB, MIB = 1024**3, 1024**2
TRUSTED_API_SECONDS = 300
ANCESTOR_SEAL_SECONDS = 5
HORIZON_SECONDS = 21600
HORIZON_CYCLE_SECONDS = 30
NATIVE_FIN_SECONDS = 5
MAX_REQUEST = 4 * MIB
MAX_METADATA = 4 * MIB
MAX_RAW = calibration.MAX_RAW
MAX_WORKER_WIRE = calibration.MAX_WORKER_WIRE
STREAM_MAGIC = b"RC6G5ARAW2\0"
STREAM_CHUNK = 512 * 1024
MAX_STREAM_BYTES = 20 * GIB
MAX_FRAME_HEADER = 65536
MAX_STREAM_ENTRIES = calibration.MAX_ENTRIES
LIMITS = {"backing_image_bytes": 26 * GIB,
    "project_hard_limit_bytes": 20 * GIB, "residual_reserve_bytes": 4 * GIB}
QUOTAS = {"workload_hard_bytes": 20 * GIB, "workload_hard_inodes": 100000,
    "archive_hard_bytes": 512 * MIB, "archive_hard_inodes": 32768,
    "probe_hard_bytes": MIB, "probe_hard_inodes": 32768}
SOURCE_LOCKS = ("requirements.lock.txt", "requirements.build.lock.txt",
    "ops/policy/rc6-supply-chain-v1.json")
G5A_MEMBERS = (MEMBER, "scripts/rc6_g5_contract.py",
    "scripts/rc6_g5a_source_context.py", "scripts/rc6_g5a_carrier.py",
    "scripts/rc6_owned_g5a_continuation.py", "scripts/rc6_owned_gate_lease.py",
    "scripts/rc6_material_horizon_producer.py", "scripts/rc6_material_horizon.py",
    "scripts/rc6_cas_original_comparison.py", "scripts/rc6_architectural_gates.py",
    "scripts/porota_artifact_http.py",
    "scripts/rc6_archive_reader_review.py", "scripts/rc6_actions_custody.py",
    "scripts/rc6_material_carrier.py", "scripts/rc6_issue465_stress.py",
    "rc6_shadow_runtime/archive_components.py", "rc6_shadow_runtime/archive_physical_model.py",
    "rc6_shadow_runtime/archive_format_contract.py", "rc6_shadow_runtime/archive_namespace.py",
    "rc6_shadow_runtime/exact_page_storage.py", "rc6_shadow_runtime/exact_binary_storage.py",
    "rc6_shadow_runtime/retention.py", "rc6_shadow_runtime/persistence.py", "rc6_shadow_runtime/worker.py")
CODE_MEMBERS = tuple(sorted(set(calibration.CODE_MEMBERS) | set(G5A_MEMBERS)))
REQUEST_FIELDS = frozenset({"schema", "operation_scope", "limits", "g5a_quotas",
    "owner_uid", "owner_gid", "source_root", "source_sha", "source_tree", "binding",
    "namespace_receipt", "image", "image_identity", "mountpoint", "mount_namespace_inode",
    "code_hashes", "loop_birth_path", "loop_birth_identity", "loop_birth_pending_sha256",
    "host_PID1", "host_boundary", "issuer", "boundary_revision", "boundary_host_proc_device",
    "require_quota_ancestor_seal", "g5a_contract_sha256", "g5a_revision_approval_url",
    "g5a_revision_owner", "g5a_prerequisites_manifest_path", "g5a_prerequisites_manifest_sha256",
    "g5a_oracle_metadata_path", "source_repo", "source_index", "source_index_sha256",
    "python311", "interpreter_manifest_path", "interpreter_manifest_sha256",
    "cas_comparison_manifest_path", "cas_comparison_manifest_sha256", "filter_binding",
    "original_workload_root"})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def canonical_path(value):
    require(type(value) is str and bool(value) and "\x00" not in value,
            "G5A_RUNNER_LITERAL_PATH_REQUIRED")
    path = Path(value)
    require(path.is_absolute() and str(path) == value == os.path.abspath(value)
            and not any(node.is_symlink() for node in (path, *path.parents)),
            "G5A_RUNNER_PATH_ALIAS_FORBIDDEN")
    return path


def expected_paths(request):
    namespace = canonical_path(request["namespace_receipt"]["path"])
    mount = namespace / "mount"
    producer = mount / "project"
    database_parent = producer / "data/paper_v17"
    return {"namespace": namespace, "mount": mount, "producer": producer,
        "control": mount / "control", "probe": mount / "edquot-probe",
        "archive": database_parent / "artifacts/observer_v17.db/dynamic-shadow-archive",
        "oracle_metadata": namespace / "g5a-owner-oracle.json",
        "prerequisites": namespace / "g5a-prerequisite-evidence",
        "ancestor_seal": namespace / "quota-ancestor-seal.json"}


def validate_request_shape(request):
    """Pure fail-closed schema validation; it grants no native authority."""
    require(type(request) is dict and set(request) == REQUEST_FIELDS
            and request.get("schema") == REQUEST_SCHEMA
            and request.get("operation_scope") == OPERATION_SCOPE,
            "G5A_RUNNER_EXACT_SOURCE_FIXED_ROOT_REQUEST_REQUIRED")
    require(type(request["limits"]) is dict and request["limits"] == LIMITS
            and all(type(value) is int for value in request["limits"].values())
            and type(request["g5a_quotas"]) is dict and request["g5a_quotas"] == QUOTAS
            and all(type(value) is int for value in request["g5a_quotas"].values()),
            "G5A_RUNNER_ORIGINAL_BOOTSTRAP_AND_ARCHIVE_LIMITS_REQUIRED")
    require(type(request["owner_uid"]) is int and request["owner_uid"] > 0
            and type(request["owner_gid"]) is int and request["owner_gid"] > 0,
            "G5A_RUNNER_ACTUAL_NONROOT_ISSUER_REQUIRED")
    require(SHA40.fullmatch(str(request["source_sha"]))
            and SHA40.fullmatch(str(request["source_tree"]))
            and request["g5a_contract_sha256"] == g5.CONTRACT_SHA256,
            "G5A_RUNNER_EXACT_SOURCE_AND_PROPOSED_CONTRACT_REQUIRED")
    binding = request["binding"]
    require(type(binding) is dict and set(binding) == {"candidate_sha", "candidate_tree", "producer",
        "attempt_id", "owner_id", "workload_fingerprint", "runner_class"}
        and binding["candidate_sha"] == request["source_sha"]
        and binding["candidate_tree"] == request["source_tree"]
        and binding["producer"] == "Horizon-private-original-cas"
        and binding["runner_class"] == "github-hosted/ubuntu-24.04"
        and all(type(value) is str and value for value in binding.values())
        and request["g5a_revision_owner"] == binding["owner_id"],
        "G5A_RUNNER_ORIGINAL_WORKLOAD_OWNER_SOURCE_BINDING_REQUIRED")
    require(type(request["namespace_receipt"]) is dict
            and request["namespace_receipt"].get("schema") == "porota.rc6.generated-fixture-consumer-binding.v1"
            and request["namespace_receipt"].get("cleanup_authority_granted") is False
            and request["namespace_receipt"].get("binding") == binding
            and re.fullmatch(r"[0-9a-f]{32}", str(request["namespace_receipt"].get("namespace_nonce", ""))),
            "G5A_RUNNER_ORIGINAL_OWN_NAMESPACE_BINDING_REQUIRED")
    paths = expected_paths(request)
    require(canonical_path(request["image"]) == paths["namespace"] / "image.ext4"
            and canonical_path(request["mountpoint"]) == paths["mount"]
            and canonical_path(request["loop_birth_path"]) == paths["namespace"] / "loop-birth.json"
            and canonical_path(request["g5a_oracle_metadata_path"]) == paths["oracle_metadata"],
            "G5A_RUNNER_FOREIGN_RESOURCE_VETO")
    require(canonical_path(request["original_workload_root"]) == paths["producer"],
            "G5A_RUNNER_ORIGINAL_WORKLOAD_ROOT_REBOUND")
    source, repository = canonical_path(request["source_root"]), canonical_path(request["source_repo"])
    require(source != repository and not source.is_relative_to(repository)
            and not repository.is_relative_to(source)
            and not source.is_relative_to(paths["namespace"])
            and not paths["namespace"].is_relative_to(source)
            and not repository.is_relative_to(paths["namespace"])
            and not paths["namespace"].is_relative_to(repository),
            "G5A_RUNNER_SOURCE_REPOSITORY_NAMESPACE_OVERLAP")
    for field in ("source_index", "python311", "interpreter_manifest_path",
                  "cas_comparison_manifest_path", "g5a_prerequisites_manifest_path"):
        path = canonical_path(request[field])
        require(not path.is_relative_to(paths["namespace"])
                and not path.is_relative_to(source), "G5A_RUNNER_INPUT_CONTROL_NAMESPACE_OVERLAP")
    require(Path(request["source_index"]).name == "source.index.json",
            "G5A_RUNNER_COMPLETE_SOURCE_INDEX_REQUIRED")
    for field in ("loop_birth_pending_sha256", "source_index_sha256", "interpreter_manifest_sha256",
                  "cas_comparison_manifest_sha256", "g5a_prerequisites_manifest_sha256"):
        require(type(request[field]) is str and SHA64.fullmatch(request[field]),
                "G5A_RUNNER_EXACT_INPUT_DIGEST_REQUIRED")
    require(type(request["filter_binding"]) is dict
            and set(request["filter_binding"]) == {"source_sha", "source_tree", "plan_sha256"}
            and request["filter_binding"]["source_sha"] == request["source_sha"]
            and request["filter_binding"]["source_tree"] == request["source_tree"]
            and type(request["filter_binding"]["plan_sha256"]) is str
            and SHA64.fullmatch(request["filter_binding"]["plan_sha256"]),
            "G5A_RUNNER_EXACT_NATIVE_FILTER_BINDING_REQUIRED")
    require(request["boundary_revision"] == "PRIVATE_PID_BOUNDARY_V2"
            and request["require_quota_ancestor_seal"] is True
            and type(request["boundary_host_proc_device"]) is int and request["boundary_host_proc_device"] > 0
            and type(request["mount_namespace_inode"]) is int and request["mount_namespace_inode"] > 0,
            "G5A_RUNNER_ORIGINAL_NATIVE_PRIVATE_PID_CUSTODY_REQUIRED")
    require(type(request["code_hashes"]) is dict and set(request["code_hashes"]) == set(CODE_MEMBERS)
            and all(type(value) is str and SHA64.fullmatch(value) for value in request["code_hashes"].values()),
            "G5A_RUNNER_CLOSED_SOURCE_CODE_HASHES_REQUIRED")
    require(type(request["g5a_revision_approval_url"]) is str and re.fullmatch(
        r"https://github\.com/mbalbo2023/Porota-trading/issues/471#issuecomment-[1-9][0-9]*",
        request["g5a_revision_approval_url"]), "G5A_RUNNER_CANONICAL_OWNER_DECISION_URL_REQUIRED")
    g5.quota_setup_request(binding, archive_path=paths["archive"])
    return paths


def read_input(path, *, expected_sha256=None, maximum=MAX_METADATA, owner=None, mode=None):
    path = canonical_path(str(path))
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum
            and (owner is None or (info.st_uid, info.st_gid) == owner)
            and (mode is None or stat.S_IMODE(info.st_mode) == mode),
            "G5A_RUNNER_INPUT_FILE_CUSTODY_REQUIRED")
    raw = calibration.read(path, maximum)
    require(expected_sha256 is None or calibration.digest(raw) == expected_sha256,
            "G5A_RUNNER_INPUT_BYTES_REBOUND")
    return raw


def validate_source_inputs(request):
    """Bind preexisting inputs; actual PRODUCT157 qualification is before fixtures."""
    source = canonical_path(request["source_root"])
    require(source == ROOT and Path(__file__).absolute() == source / MEMBER,
            "G5A_RUNNER_EXECUTED_EXPORTED_SOURCE_REQUIRED")
    for member, expected in request["code_hashes"].items():
        require(calibration.digest(read_input(source / member, maximum=calibration.CONTROLS)) == expected,
                "G5A_RUNNER_EXECUTED_SOURCE_MEMBER_REBOUND")
    index_raw = read_input(request["source_index"], expected_sha256=request["source_index_sha256"])
    pin = calibration.decode(index_raw)
    require(type(pin) is dict and pin.get("schema") == "rc6.complete-archive-source-pin.v1"
            and pin.get("source_sha") == request["source_sha"] and pin.get("source_tree") == request["source_tree"]
            and type(pin.get("overlay_count")) is int and pin["overlay_count"] == 0
            and type(pin.get("files")) is dict and set(request["code_hashes"]) <= set(pin["files"])
            and all(pin["files"][name] == value for name, value in request["code_hashes"].items()),
            "G5A_RUNNER_COMPLETE_EXPORTED_SOURCE_PIN_REBOUND")
    interpreter_raw = read_input(request["interpreter_manifest_path"],
        expected_sha256=request["interpreter_manifest_sha256"])
    interpreter = calibration.decode(interpreter_raw)
    binary = canonical_path(request["python311"])
    require(type(interpreter) is dict and interpreter.get("schema") == "rc6.g5a-preexisting-product157-interpreter.v1"
            and interpreter.get("source_sha") == request["source_sha"]
            and interpreter.get("source_tree") == request["source_tree"]
            and interpreter.get("binding") == request["binding"]
            and interpreter.get("python_epoch") == "311" and interpreter.get("python_version") == "3.11.16"
            and platform.python_version() == "3.11.16" and str(binary) == sys.executable
            and interpreter.get("executable") == sys.executable
            and interpreter.get("prefix") == sys.prefix and interpreter.get("base_prefix") == sys.base_prefix
            and sys.prefix != sys.base_prefix and binary.is_relative_to(Path(sys.prefix))
            and type(interpreter.get("installed_distribution_count")) is int
            and interpreter["installed_distribution_count"] == 157,
            "G5A_RUNNER_PREEXISTING_SAME_PID_PRODUCT311_INTERPRETER_REQUIRED")
    binary_info = binary.lstat()
    require(stat.S_ISREG(binary_info.st_mode) and binary_info.st_nlink == 1
            and (binary_info.st_uid, binary_info.st_gid) == (request["owner_uid"], request["owner_gid"])
            and stat.S_IMODE(binary_info.st_mode) == 0o755
            and interpreter.get("identity") == calibration.identity(binary_info)
            and interpreter.get("executable_sha256") == calibration.digest(read_input(binary, maximum=128 * MIB)),
            "G5A_RUNNER_PREEXISTING_INTERPRETER_NATIVE_BYTES_REBOUND")
    require(interpreter.get("source_lock_sha256") == {
        name: calibration.digest(read_input(source / name)) for name in SOURCE_LOCKS},
        "G5A_RUNNER_PREEXISTING_INTERPRETER_SOURCE_LOCK_REBOUND")
    manifest_raw = read_input(request["g5a_prerequisites_manifest_path"],
        expected_sha256=request["g5a_prerequisites_manifest_sha256"])
    comparison_raw = read_input(request["cas_comparison_manifest_path"],
        expected_sha256=request["cas_comparison_manifest_sha256"], maximum=256 * 1024)
    return calibration.decode(manifest_raw), calibration.decode(comparison_raw)


@contextmanager
def trusted_api_deadline():
    """One300-second trusted input budget inside the original21600 seconds."""
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    require(previous_handler == signal.SIG_DFL and previous_timer == (0.0, 0.0),
            "G5A_RUNNER_FRESH_OWN_TRUSTED_INPUT_TIMER_REQUIRED")
    def expired(signum, frame):
        raise TimeoutError("G5A_RUNNER_TRUSTED_API300_DEADLINE_EXCEEDED")
    entered = time.monotonic()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, TRUSTED_API_SECONDS)
    try:
        yield entered + TRUSTED_API_SECONDS
        require(time.monotonic() - entered <= TRUSTED_API_SECONDS,
                "G5A_RUNNER_TRUSTED_API300_DEADLINE_EXCEEDED")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def wait_quota_ancestor_seal(request, proof):
    """Starts only AFTER the broker's CLOSE ACK, never covers API acquisition."""
    path = expected_paths(request)["ancestor_seal"]
    deadline = time.monotonic() + ANCESTOR_SEAL_SECONDS
    while not os.path.lexists(path):
        require(time.monotonic() < deadline, "G5A_RUNNER_QUOTA_ANCESTOR_SEAL_NOT_READY")
        time.sleep(0.01)
    seal = calibration.decode(read_input(path, maximum=65536, owner=(0, 0), mode=0o600))
    rebound = calibration.decode(calibration.wire(proof))
    rebound["quota_ancestor_seal"] = seal
    from scripts import rc6_privileged_custody as privileged
    privileged.verify_live_controller(rebound, request)
    return rebound


def producer_arguments(request):
    """Fixed original workload and original producer; never arbitrary argv."""
    paths = expected_paths(request)
    return [str(ROOT / "scripts/rc6_material_horizon_producer.py"),
        "--source-repo", request["source_repo"], "--source-root", request["source_root"],
        "--source-sha", request["source_sha"], "--source-tree", request["source_tree"],
        "--source-index", request["source_index"], "--root", str(paths["producer"]),
        "--catalog-count", "1200", "--ticks", "1201",
        "--owned-fin", str(paths["control"] / "producer-owned-fin.json"),
        "--cas-comparison-manifest", request["cas_comparison_manifest_path"]]


def _run_original_producer(request, authority):
    from scripts import rc6_material_horizon_producer as producer
    require(Path(producer.__file__).absolute() == ROOT / "scripts/rc6_material_horizon_producer.py",
            "G5A_RUNNER_ORIGINAL_PRODUCER_IMPORT_ORIGIN_REBOUND")
    before = os.getpid()
    original_arguments = sys.argv
    sys.argv = producer_arguments(request)
    try:
        producer.main(g5a=authority, expected_binding=request["binding"])
    finally:
        sys.argv = original_arguments
        require(os.getpid() == before, "G5A_RUNNER_ORIGINAL_PRODUCER_SAME_PID_REQUIRED")
    # Reader/recovery fixtures run on the Actions parent only after this
    # producer and both actual ROOT/outer managers have reached FIN.
    capture_control_raw(request)


def _own_echild():
    try:
        os.waitid(os.P_ALL, 0, os.WNOHANG | os.WNOWAIT | os.WEXITED)
    except ChildProcessError as error:
        require(error.errno == errno.ECHILD, "G5A_RUNNER_NATIVE_ECHILD_UNKNOWN")
        return {"kernel_echild_verified": True, "errno": error.errno}
    raise ValueError("G5A_RUNNER_CHILDREN_REMAIN_NO_PAYLOAD_POSTCAPTURE")


def capture_control_raw(request):
    """Actual small control bytes after native producerFIN; never wholearchive proof."""
    paths = expected_paths(request)
    echild = _own_echild()
    fin_path = paths["control"] / "producer-owned-fin.json"
    fin_raw = read_input(fin_path, maximum=MIB, owner=(request["owner_uid"], request["owner_gid"]))
    fin = calibration.decode(fin_raw)
    from scripts import rc6_material_horizon_producer as producer
    require(fin.get("schema") == "rc6.native-horizon-owned-fin.v1"
            and fin.get("pid") == os.getpid() and fin.get("parent_pid") == os.getppid()
            and fin.get("entry_module") == "scripts.rc6_material_horizon_producer"
            and fin.get("entry_member") == "scripts/rc6_material_horizon_producer.py"
            and fin.get("source_sha") == request["source_sha"] and fin.get("source_tree") == request["source_tree"]
            and fin.get("source_root") == request["source_root"]
            and fin.get("source_index_sha256") == request["source_index_sha256"]
            and fin.get("native_finalizer_closed") is True
            and producer.native_finalizer_closed(fin.get("child_infrastructure_finalization")),
            "G5A_RUNNER_ACTUAL_ORIGINAL_PRODUCER_FIN_REQUIRED")
    candidates = [("producer-owned-fin.json", fin_path, fin_raw),
        ("native-horizon-result.json", paths["producer"] / "result.json", None),
        ("g5a-native-physical-control.json", paths["control"] / "g5a-native-physical-control.json", None)]
    comparison = paths["control"] / "original-cas-comparison"
    for name in ("comparison-result.json.gz", "g5a-ledger.json.gz", "g5a-quota.json.gz",
                 "g5b-writer-binding.json.gz", "g5b-original-frames-manifest.json.gz"):
        path = comparison / name
        if os.path.lexists(path):
            candidates.append((name, path, None))
    raw_files, total = [], 0
    for name, path, captured in candidates:
        raw = captured if captured is not None else read_input(path, maximum=MAX_RAW,
            owner=(request["owner_uid"], request["owner_gid"]))
        require(path.is_relative_to(paths["mount"]), "G5A_RUNNER_RAW_FOREIGN_PATH_VETO")
        total += len(raw)
        require(total <= MAX_RAW, "G5A_RUNNER_ORIGINAL_CONTROL_TRANSFER_BYTES_EXCEEDED")
        raw_files.append({"path": name, "bytes": len(raw), "sha256": calibration.digest(raw),
            "relative_source": path.relative_to(paths["mount"]).as_posix()})
    return {"raw_files": raw_files, "raw_total_bytes": total,
        "native_producer_fin": fin, "own_kernel_after_producer": echild,
        "raw_scope": "ACTUAL_NATIVE_CONTROL_REFERENCES_BOUND_TO_BINARY_SOURCE_STREAM",
        "whole_archive_capture_complete": False, "external_ROOT_FIN_claimed": False}


def _stable_node(info):
    return {name: getattr(info, name) for name in (
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
        "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")}


def source_stream_inventory(request):
    """Walk only three own trees afterFIN; observe stored bytes, never forecast ZIP."""
    _own_echild()
    paths = expected_paths(request)
    mount = paths["mount"]
    mount_info = mount.lstat()
    owner = (request["owner_uid"], request["owner_gid"])
    queue, inventory = [paths[name] for name in ("producer", "control", "probe")], []
    allocated = payload = 0
    while queue:
        path = queue.pop()
        canonical_path(str(path))
        require(path.is_relative_to(mount), "G5A_RUNNER_STREAM_FOREIGN_PATH_VETO")
        info = path.lstat()
        require(info.st_dev == mount_info.st_dev and (info.st_uid, info.st_gid) == owner
                and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)),
                "G5A_RUNNER_STREAM_NATIVE_OWN_FILESYSTEM_CUSTODY_REQUIRED")
        name = path.relative_to(mount).as_posix()
        require(PurePosixPath(name).as_posix() == name and ".." not in PurePosixPath(name).parts,
                "G5A_RUNNER_STREAM_RELATIVE_MEMBER_REQUIRED")
        require(len(inventory) < MAX_STREAM_ENTRIES, "G5A_RUNNER_ORIGINAL_RETAINED_ENTRY_LIMIT")
        allocated += info.st_blocks * 512
        if stat.S_ISDIR(info.st_mode):
            require(stat.S_IMODE(info.st_mode) == 0o700, "G5A_RUNNER_STREAM_OWN_DIRECTORY700_REQUIRED")
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
            try:
                before = _stable_node(os.fstat(descriptor))
                children = os.listdir(descriptor)
                require(len(children) <= MAX_STREAM_ENTRIES
                        and before == _stable_node(info) == _stable_node(os.fstat(descriptor)) == _stable_node(path.lstat()),
                        "G5A_RUNNER_STREAM_DIRECTORY_REBOUND")
            finally:
                os.close(descriptor)
            queue.extend(path / child for child in sorted(children, reverse=True))
            inventory.append({"kind": "DIRECTORY", "name": name, "bytes": 0, "identity": _stable_node(info)})
        else:
            require(info.st_nlink == 1 and stat.S_IMODE(info.st_mode) in (0o400, 0o600, 0o644),
                    "G5A_RUNNER_STREAM_OWN_SINGLELINK_REGULAR_REQUIRED")
            payload += info.st_size
            require(payload <= MAX_STREAM_BYTES and allocated <= MAX_STREAM_BYTES,
                    "G5A_RUNNER_ORIGINAL20GIB_RAW_PHYSICAL_LIMIT")
            inventory.append({"kind": "FILE", "name": name, "bytes": info.st_size,
                              "identity": _stable_node(info)})
    require(allocated <= MAX_STREAM_BYTES, "G5A_RUNNER_ORIGINAL20GIB_RAW_PHYSICAL_LIMIT")
    return {"schema": "rc6.g5a-own-source-stream-inventory.v2", "entries": sorted(inventory, key=lambda row: row["name"]),
        "payload_bytes": payload, "allocated_bytes": allocated, "retained_entries": len(inventory),
        "source_sha": request["source_sha"], "source_tree": request["source_tree"],
        "binding": request["binding"], "data_substitution_performed_by_streamer": False,
        "storage_reduction_extrapolated": False, "qualification_claimed": False}


def _write_all(output, raw):
    offset = 0
    while offset < len(raw):
        written = output.write(memoryview(raw)[offset:])
        require(type(written) is int and 0 < written <= len(raw) - offset,
                "G5A_RUNNER_NATIVE_RAW_STREAM_SHORT_WRITE")
        offset += written


def _frame_header(output, value):
    raw = calibration.wire(value)
    require(0 < len(raw) <= MAX_FRAME_HEADER, "G5A_RUNNER_RAW_FRAME_HEADER_BOUND")
    _write_all(output, struct.pack(">I", len(raw)))
    _write_all(output, raw)
    return 4 + len(raw)


def stream_source_raw(request, inventory, report, output):
    """Pinned own reads to the original manager stdout; no uploader/launcher."""
    require(os.getuid() == os.geteuid() == request["owner_uid"] > 0
            and os.getgid() == os.getegid() == request["owner_gid"] > 0,
            "G5A_RUNNER_STREAM_NONROOT_OWNER_REQUIRED")
    _own_echild()
    current = source_stream_inventory(request)
    require(current == inventory, "G5A_RUNNER_RAW_INVENTORY_REBOUND")
    _write_all(output, STREAM_MAGIC)
    total = len(STREAM_MAGIC)
    paths = expected_paths(request)
    for entry in inventory["entries"]:
        path = paths["mount"] / entry["name"]
        require(_stable_node(path.lstat()) == entry["identity"], "G5A_RUNNER_STREAM_NODE_REBOUND")
        if entry["kind"] == "DIRECTORY":
            total += _frame_header(output, {"schema": "rc6.g5a-source-raw-frame.v2", **entry})
            require(total <= MAX_STREAM_BYTES, "G5A_RUNNER_ORIGINAL20GIB_STREAM_LIMIT")
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
        try:
            require(_stable_node(os.fstat(descriptor)) == entry["identity"],
                    "G5A_RUNNER_STREAM_HELD_FILE_REBOUND")
            hasher, count = hashlib.sha256(), 0
            while block := os.read(descriptor, STREAM_CHUNK):
                count += len(block); hasher.update(block)
                require(count <= entry["bytes"], "G5A_RUNNER_STREAM_ACTUAL_BYTES_EXCEEDED")
            require(count == entry["bytes"] and _stable_node(os.fstat(descriptor)) == entry["identity"]
                    == _stable_node(path.lstat()), "G5A_RUNNER_STREAM_HASH_PASS_REBOUND")
            total += _frame_header(output, {"schema": "rc6.g5a-source-raw-frame.v2", **entry,
                "sha256": hasher.hexdigest()})
            require(total + count <= MAX_STREAM_BYTES, "G5A_RUNNER_ORIGINAL20GIB_STREAM_LIMIT")
            os.lseek(descriptor, 0, os.SEEK_SET)
            transferred, stream_hash = 0, hashlib.sha256()
            while block := os.read(descriptor, STREAM_CHUNK):
                transferred += len(block); stream_hash.update(block)
                require(transferred <= count, "G5A_RUNNER_STREAM_TRANSFER_BYTES_EXCEEDED")
                _write_all(output, block)
            require(transferred == count and stream_hash.digest() == hasher.digest()
                    and _stable_node(os.fstat(descriptor)) == entry["identity"] == _stable_node(path.lstat()),
                    "G5A_RUNNER_STREAM_NATIVE_CONTENT_CHANGED")
            total += transferred
        finally:
            os.close(descriptor)
    report_raw = calibration.wire(report)
    require(len(report_raw) <= MAX_RAW, "G5A_RUNNER_ORIGINAL_CONTROL_FRAME_LIMIT")
    total += _frame_header(output, {"schema": "rc6.g5a-source-raw-frame.v2", "kind": "WORKER_CONTROL",
        "name": "g5a-worker-report.json", "bytes": len(report_raw), "sha256": calibration.digest(report_raw)})
    require(total + len(report_raw) <= MAX_STREAM_BYTES, "G5A_RUNNER_ORIGINAL20GIB_STREAM_LIMIT")
    _write_all(output, report_raw); total += len(report_raw)
    total += _frame_header(output, {"schema": "rc6.g5a-source-raw-frame.v2", "kind": "END",
        "source_sha": request["source_sha"], "source_tree": request["source_tree"],
        "binding": request["binding"], "data_entries": len(inventory["entries"]),
        "data_payload_bytes": inventory["payload_bytes"], "bytes_before_end": total,
        "whole_current_own_namespace_streamed": True, "external_ROOT_FIN_claimed": False,
        "G5_GREEN": False, "qualification_claimed": False})
    require(total <= MAX_STREAM_BYTES, "G5A_RUNNER_ORIGINAL20GIB_STREAM_LIMIT")
    output.flush()
    return total


def _write_native_control(request, authority):
    control = authority.capture_native_control(expected_binding=request["binding"])
    path = expected_paths(request)["control"] / "g5a-native-physical-control.json"
    calibration.publish_bytes(path, calibration.wire(control))
    return control


def acquire_same_process(request, proof, *, original_request_sha256):
    """The only native path. Opaque native factories remain the admission guards."""
    paths = validate_request_shape(request)
    require(os.getresuid() == os.getresgid() == (0, 0, 0)
            and len(os.listdir("/proc/self/task")) == 1,
            "G5A_RUNNER_ORIGINAL_SINGLE_ROOT_WORKER_REQUIRED")
    require(not any(name in os.environ for name in
        ("GITHUB_TOKEN", "GH_TOKEN", "RC6_READONLY_GIT_TOKEN", "ACTIONS_RUNTIME_TOKEN")),
        "G5A_RUNNER_BEARER_ENVIRONMENT_FORBIDDEN")
    require(request["mount_namespace_inode"] != os.stat("/proc/self/ns/mnt").st_ino,
            "G5A_RUNNER_NEW_PRIVATE_MOUNT_NAMESPACE_REQUIRED")
    prerequisites_manifest, comparison = validate_source_inputs(request)
    require(comparison.get("producer_namespace_root") == str(paths["producer"])
            and comparison.get("source_sha") == request["source_sha"]
            and comparison.get("source_tree") == request["source_tree"],
            "G5A_RUNNER_ORIGINAL_COMPARISON_SOURCE_NAMESPACE_REBOUND")
    # Adding the digest of the actual request is a live controller handoff;
    # callers cannot inject it into the exact input schema above.
    request["original_request_sha256"] = original_request_sha256
    request["privileged_custody_live"] = proof
    trusted_controller = g5.observe_trusted_api_controller(request, proof)
    metadata = calibration.decode(read_input(paths["oracle_metadata"], owner=(0, 0), mode=0o600))
    reader = g5.RootOwnerOracleReader.open(metadata, trusted_controller=trusted_controller)
    from scripts import rc6_owned_g5a_continuation as continuation_module
    with trusted_api_deadline() as deadline:
        preparation = g5.prepare_g5a_trusted(trusted_controller=trusted_controller, reader=reader,
            approval_url=request["g5a_revision_approval_url"], owner_session=request["g5a_revision_owner"],
            prerequisites_manifest=prerequisites_manifest, evidence_root=paths["prerequisites"], source_root=ROOT)
        continuation = continuation_module.bind_continuation(preparation=preparation,
            trusted_controller=trusted_controller, reader=reader, binding=request["binding"],
            monitor_root=paths["namespace"] / "g5a-owner-monitor")
        reader.close(deadline=deadline)
    trusted_inputs = g5.validate_trusted_g5a_preparation(preparation, binding=request["binding"],
                                       prerequisites_manifest=prerequisites_manifest)
    sealed_proof = wait_quota_ancestor_seal(request, proof)
    request["privileged_custody_live"] = sealed_proof
    # These are distinct Source-fixed G5A helpers. Legacy capability/bootstrap
    # modes and their setup receipt are never renamed or repurposed here.
    prepared = calibration.g5a_trusted_quota_setup(request, preparation=preparation)
    custody = g5.observe_live_g5a_custody(prepared, sealed_proof)
    workload_quota = g5.observe_root_workload_quota(root=paths["producer"], control_root=paths["control"],
        probe_path=paths["probe"], device=prepared["device"], binding=request["binding"])
    quota = g5.open_native_project_quota(paths["archive"], device=prepared["device"],
        binding=request["binding"], probe_path=paths["probe"])
    quota.close_before_seal()
    calibration.g5a_seal_quota_setup(prepared, preparation=preparation)
    from scripts import rc6_private_pid_boundary as private_pid
    private_boundary = private_pid.quota_actor_observation({"request": prepared, "proof": sealed_proof})
    quota.reopen_after_seal(binding=request["binding"], filter_binding=request["filter_binding"])
    enforcement = g5.prove_native_quota_enforcement(quota, binding=request["binding"],
                                                   filter_binding=request["filter_binding"])
    continuation_module.activate_continuation(continuation, custody=custody, binding=request["binding"],
                                              filter_binding=request["filter_binding"])
    authority = g5.authenticate_g5a(approval=preparation.approval, quota=quota, custody=custody,
        binding=request["binding"], filter_binding=request["filter_binding"],
        prerequisites_manifest=prerequisites_manifest, evidence_root=paths["prerequisites"],
        source_root=ROOT, trusted_preparation=preparation, workload_quota=workload_quota,
        continuation=continuation)
    try:
        _write_native_control(request, authority)
        request["producer_path_started"] = True
        _run_original_producer(request, authority)
        controls = capture_control_raw(request)
        return {"status": "EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN",
            "private_pid_boundary": private_boundary, "quota_enforcement": enforcement,
            "g5a_native_capacity_pair": trusted_inputs["g5a_native_capacity_pair"],
            "phases": prepared.get("phases", []), **controls}
    finally:
        quota.close()


def root_worker(request_path, *, privileged_custody_proof=None):
    """Original controller callback; no independent CLI or alternative actor."""
    request_raw = read_input(request_path, maximum=MAX_REQUEST)
    request = calibration.decode(request_raw)
    request_digest = calibration.digest(request_raw)
    report = {"status": "BLOQUEADO", "operation_scope": OPERATION_SCOPE,
        "qualification_claimed": False, "G5_GREEN": False, "G0_G8_qualification_claimed": False,
        "native_v4_write_enabled": False, "runtime_validated": False, "artifact_validated": False,
        "external_ROOT_FIN_claimed": False, "whole_archive_capture_complete": False,
        "real_orders_sent": 0, "real_routes": "NOT_CALLED", "required_raw_complete": False}
    try:
        validate_request_shape(request)
        require(canonical_path(str(request_path)) == expected_paths(request)["namespace"] / "root-request.json",
                "G5A_RUNNER_ORIGINAL_OWN_REQUEST_PATH_REQUIRED")
        read_input(request_path, expected_sha256=request_digest, maximum=MAX_REQUEST,
            owner=(request["owner_uid"], request["owner_gid"]), mode=0o600)
        require(privileged_custody_proof is not None, "G5A_RUNNER_ORIGINAL_CONTROLLER_PROOF_REQUIRED")
        require(canonical_path(str(privileged_custody_proof)) == expected_paths(request)["namespace"] / "live-root-controller.json",
                "G5A_RUNNER_ORIGINAL_OWN_CONTROLLER_PATH_REQUIRED")
        proof = calibration.decode(read_input(privileged_custody_proof, maximum=2 * MIB, owner=(0, 0), mode=0o600))
        require(proof.get("calibration_request_sha256") == request_digest,
                "G5A_RUNNER_ORIGINAL_CONTROLLER_REQUEST_BYTES_REBOUND")
        # Input-schema validation precedes this private live handoff field.
        report.update(acquire_same_process(request, proof, original_request_sha256=request_digest))
        report["required_raw_complete"] = True
    except BaseException as error:
        reason = str(error).split(":", 1)[0]
        report.update(status="BLOQUEADO", reason=reason if re.fullmatch(r"[A-Z0-9_]{1,160}", reason)
            else "G5A_RUNNER_NATIVE_FAILURE", error_class=type(error).__name__,
            oserror_errno=error.errno if isinstance(error, OSError) else None,
            setup_stage=request.get("setup_stage"), loop=request.get("loop"),
            phases=request.get("phases", []), setup_commands=request.get("setup_commands", []))
        if "producer_path_started" in request:
            try:
                report.update(capture_control_raw(request))
                report["required_raw_complete"] = True
            except BaseException as capture_error:
                report["raw_capture_error"] = type(capture_error).__name__
    wrapped = {"schema": WORKER_SCHEMA, "request_sha256": request_digest,
        "source_sha": request.get("source_sha"), "source_tree": request.get("source_tree"),
        "binding": request.get("binding"), "report": report}
    if request.get("producer_path_started") is True and report.get("required_raw_complete") is True:
        inventory = source_stream_inventory(request)
        wrapped["source_stream_inventory_sha256"] = calibration.digest(calibration.wire(inventory))
        wrapped["source_stream_retained_entries"] = inventory["retained_entries"]
        wrapped["source_stream_payload_bytes"] = inventory["payload_bytes"]
        stream_source_raw(request, inventory, wrapped, sys.stdout.buffer)
        return 0 if report["status"] == "EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN" else 1
    raw = calibration.wire(wrapped)
    require(len(raw) <= MAX_WORKER_WIRE, "G5A_RUNNER_ORIGINAL_WORKER_WIRE_LIMIT")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return 0 if report["status"] == "EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN" else 1

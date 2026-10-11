"""Opaque NONROOT authority for an exact Git-backed G5A Source export.

This proves a physical Source correspondence only. It grants no ROOT admission,
contract acceptance, native FIN, cleanup, workload equivalence or GREEN gate.
The issuer stays in its original clean Git checkout; actors execute the export.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).absolute().parents[1]
MEMBER = "scripts/rc6_g5a_source_context.py"
FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
          "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
BINDING_FIELDS = frozenset(("candidate_sha", "candidate_tree", "producer", "attempt_id", "owner_id",
                            "workload_fingerprint", "runner_class"))
MAX_INDEX = 4 * 1024**2
_EXPORTS = {}


def require(condition, signature):
    if not condition:
        raise ValueError(signature)


def _wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _path(value):
    path = Path(value)
    require(path.is_absolute() and str(path) == str(value) == os.path.abspath(value)
        and ".." not in path.parts and not any(item.is_symlink() for item in (path, *path.parents)),
        "G5A_EXPORT_LITERAL_NOFOLLOW_PATH_REQUIRED")
    return path


def _identity(info):
    return [getattr(info, key) for key in FIELDS]


def _birth():
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    require(os.getresuid() == (os.getuid(),) * 3 and os.getuid() > 0
        and os.getresgid() == (os.getgid(),) * 3 and os.getgid() > 0
        and len(os.listdir("/proc/self/task")) == 1, "G5A_EXPORT_ACTUAL_SINGLE_NONROOT_ISSUER_REQUIRED")
    return {"pid": os.getpid(), "start_ticks": fields[19], "uid": os.getuid(), "gid": os.getgid(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def _read_index(path):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= MAX_INDEX
        and (before.st_uid, before.st_gid) == (os.getuid(), os.getgid())
        and stat.S_IMODE(before.st_mode) in (0o400, 0o600, 0o644), "G5A_EXPORT_OWN_BOUNDED_INDEX_REQUIRED")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        require(_identity(before) == _identity(os.fstat(descriptor)), "G5A_EXPORT_INDEX_OPEN_REBOUND")
        raw, total = [], 0
        while True:
            part = os.read(descriptor, min(65536, MAX_INDEX + 1 - total))
            if not part: break
            total += len(part); raw.append(part)
            require(total <= MAX_INDEX, "G5A_EXPORT_INDEX_BYTES_EXCEEDED")
        require(total == before.st_size and _identity(before) == _identity(os.fstat(descriptor))
            == _identity(path.lstat()), "G5A_EXPORT_INDEX_CHANGED")
        return b"".join(raw), _identity(before)
    finally:
        os.close(descriptor)


def _observe(*, source_repo, source_root, index, source_sha, source_tree, binding):
    from scripts import rc6_material_horizon_producer as producer
    birth = _birth()
    repo, source, index_path = _path(source_repo), _path(source_root), _path(index)
    require(repo == ROOT and Path(__file__).absolute() == repo / MEMBER,
        "G5A_EXPORT_ISSUER_EXECUTED_ORIGINAL_GIT_SOURCE_REQUIRED")
    require(source != repo and not source.is_relative_to(repo) and not repo.is_relative_to(source)
        and not index_path.is_relative_to(source) and not index_path.is_relative_to(repo)
        and index_path.name == "source.index.json", "G5A_EXPORT_REPOSITORY_SOURCE_INDEX_OVERLAP")
    require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) for value in (source_sha, source_tree))
        and type(binding) is dict and set(binding) == BINDING_FIELDS
        and all(type(value) is str and 0 < len(value) <= 512 for value in binding.values())
        and binding["candidate_sha"] == source_sha and binding["candidate_tree"] == source_tree
        and binding["producer"] == "Horizon-private-original-cas"
        and binding["runner_class"] == "github-hosted/ubuntu-24.04", "G5A_EXPORT_EXACT_CANDIDATE_BINDING_REQUIRED")
    require(all(stat.S_ISDIR(path.lstat().st_mode) and (path.lstat().st_uid, path.lstat().st_gid)
        == (birth["uid"], birth["gid"]) and not stat.S_IMODE(path.lstat().st_mode) & 0o022 for path in (repo, source)),
        "G5A_EXPORT_OWN_SOURCE_ROOTS_REQUIRED")
    raw, index_identity = _read_index(index_path)
    pin = producer.unique_object  # use the original duplicate/nonfinite JSON guards
    parsed = json.loads(raw, object_pairs_hook=pin,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("G5A_EXPORT_NONFINITE_INDEX")))
    previous = {key: os.environ.get(key) for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")}
    try:
        os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0=str(repo))
        require(producer.git_bytes(repo, "rev-parse", "HEAD").decode().strip() == source_sha
            and producer.git_bytes(repo, "rev-parse", "HEAD^{tree}").decode().strip() == source_tree
            and not producer.git_bytes(repo, "status", "--porcelain=v1", "--untracked-files=all"),
            "G5A_EXPORT_ACTUAL_ORIGINAL_GIT_CLEAN_PIN_REQUIRED")
        inventory, raw_git = producer.verify_source(repo, source, source_sha, source_tree, parsed)
    finally:
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
    roots = {str(path): {key: getattr(path.lstat(), key) for key in FIELDS if key != "st_atime_ns"}
        for path in (repo, source)}
    nodes, queue = {".": _identity(source.lstat())}, [source]
    while queue:
        directory = queue.pop()
        for path in producer.paths_at(directory):
            info = path.lstat()
            require(not path.is_symlink() and (info.st_uid, info.st_gid) == (birth["uid"], birth["gid"])
                and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                "G5A_EXPORT_PHYSICAL_SOURCE_OWNER_OR_ALIAS_REBOUND")
            nodes[path.relative_to(source).as_posix()] = _identity(info)
            if stat.S_ISDIR(info.st_mode): queue.append(path)
    require(inventory["files"].get(MEMBER) == hashlib.sha256(producer.raw(repo / MEMBER)).hexdigest()
        and roots == {str(path): {key: getattr(path.lstat(), key) for key in FIELDS if key != "st_atime_ns"}
            for path in (repo, source)}
        and index_identity == _identity(index_path.lstat()), "G5A_EXPORT_SOURCE_CHANGED_DURING_NATIVE_OBSERVATION")
    return {"source_repo": str(repo), "source_root": str(source), "index": str(index_path),
        "source_sha": source_sha, "source_tree": source_tree, "binding": binding, "issuer": birth,
        "index_identity": index_identity, "index_sha256": hashlib.sha256(raw).hexdigest(),
        "roots": roots, "nodes": nodes, "inventory": inventory, "raw_git": raw_git}


class ExportedSourceContext:
    __slots__ = ()
    def __init__(self, *args, **kwargs):
        raise TypeError("G5A_EXPORT_NATIVE_FACTORY_ONLY")
    def __reduce__(self):
        raise TypeError("G5A_EXPORT_SERIALIZATION_FORBIDDEN")


def observe_export(*, source_repo, source_root, index, source_sha, source_tree, binding):
    """Observe a clean original Git pin and every physical exported Source blob."""
    observed = _observe(source_repo=source_repo, source_root=source_root, index=index,
        source_sha=source_sha, source_tree=source_tree, binding=binding)
    context = object.__new__(ExportedSourceContext)
    _EXPORTS[id(context)] = (context, _wire(observed))
    return context


def validate(context, *, source_repo, source_root, index, source_sha, source_tree, binding):
    registered = _EXPORTS.get(id(context))
    require(type(context) is ExportedSourceContext and registered is not None and registered[0] is context,
        "G5A_EXPORT_OPAQUE_CURRENT_NATIVE_CONTEXT_REQUIRED")
    original = json.loads(registered[1])
    require(original["issuer"] == _birth() and all(original[key] == str(value) for key, value in
        (("source_repo", source_repo), ("source_root", source_root), ("index", index)))
        and original["source_sha"] == source_sha and original["source_tree"] == source_tree
        and original["binding"] == binding, "G5A_EXPORT_REGISTERED_SOURCE_OR_ISSUER_REBOUND")
    current = _observe(source_repo=source_repo, source_root=source_root, index=index,
        source_sha=source_sha, source_tree=source_tree, binding=binding)
    require(_wire(current) == registered[1], "G5A_EXPORT_PHYSICAL_INODE_OR_ALL_BYTES_REBOUND")
    return {key: current[key] for key in ("source_repo", "source_root", "index", "source_sha", "source_tree", "index_sha256")}


validate_export = validate

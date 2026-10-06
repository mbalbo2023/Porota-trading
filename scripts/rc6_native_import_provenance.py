"""Bounded native import provenance, separate from business/resource success.

Preparation qualifies source and exact language-runtime factories. Observations
are bounded import/exec events and original import-loader outcomes, never call
profiling. Rare opaque exec events inspect one caller frame and retain exact
code-object evidence; arbitrary opaque code remains unverified.
"""
from __future__ import annotations

import _imp
import __future__
import ast
import hashlib
from importlib import _bootstrap, machinery
import importlib.metadata
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import sysconfig
import threading
import time
import types

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_RECORDS = 2048
MAX_TEXT = 4096
_INSTALLATION_EVENT = "rc6.native_import_provenance.installation"
STATS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
         "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")

# Only import machinery is observed. Original results/exceptions are delegated
# unchanged; no tracing/profiling hook or per-function interception is installed.
_PROTOCOL_LOCK = threading.RLock()
_PROTOCOL_OWNERS = []
_ORIGINAL_FIND = _bootstrap._find_and_load
_ORIGINAL_LOAD = _bootstrap._load_unlocked
_EXTENSION_LOADER = machinery.ExtensionFileLoader
_FUTURE_FLAGS = sum(getattr(__future__, name).compiler_flag for name in __future__.all_feature_names)
_TYPE_FIELDS = {name:type.__dict__[name] for name in ("__dict__", "__module__", "__name__", "__qualname__")}
_IMPORT_ERROR_NAME = ImportError.__dict__["name"]


def _static_type_field(cls, name):
    # Calling the actual built-in descriptor bypasses descriptors supplied by
    # a foreign metaclass; type.__getattribute__ alone does not provide that.
    return _TYPE_FIELDS[name].__get__(cls, type(cls))


def _static_type_text(cls, name):
    value = _static_type_field(cls, name)
    return value if type(value) is str and len(value) <= MAX_TEXT else None


def _protocol_owners():
    with _PROTOCOL_LOCK:
        return tuple(owner for owner in _PROTOCOL_OWNERS if owner.active and owner.native_pid == os.getpid())


def _observed_find(name, import_):
    owners = _protocol_owners()
    tokens = [(owner, owner._attempt_start(name)) for owner in owners]
    result, failure = None, None
    try:
        result = _ORIGINAL_FIND(name, import_)
        return result
    except BaseException as error:
        failure = error
        raise
    finally:
        for owner, token in tokens:
            owner._attempt_end(token, result, failure)


def _observed_load(spec):
    owners = _protocol_owners()
    tokens = [(owner, owner._loader_start(spec)) for owner in owners]
    result, failure = None, None
    try:
        result = _ORIGINAL_LOAD(spec)
        return result
    except BaseException as error:
        failure = error
        raise
    finally:
        for owner, token in tokens:
            owner._loader_end(token, result, failure)


def _attach_protocol(owner):
    with _PROTOCOL_LOCK:
        if (not any(_bootstrap._find_and_load is item for item in (_ORIGINAL_FIND, _observed_find))
                or not any(_bootstrap._load_unlocked is item for item in (_ORIGINAL_LOAD, _observed_load))):
            raise ValueError("IMPORT_PROVENANCE_IMPORT_MACHINERY_OWNER_CHANGED")
        _PROTOCOL_OWNERS.append(owner)
        _bootstrap._find_and_load, _bootstrap._load_unlocked = _observed_find, _observed_load


def _detach_protocol(owner):
    with _PROTOCOL_LOCK:
        _PROTOCOL_OWNERS[:] = [item for item in _PROTOCOL_OWNERS if item is not owner]
        if not _PROTOCOL_OWNERS:
            if _bootstrap._find_and_load is _observed_find:
                _bootstrap._find_and_load = _ORIGINAL_FIND
            elif _bootstrap._find_and_load is not _ORIGINAL_FIND:
                owner.counts["errors"] += 1
            if _bootstrap._load_unlocked is _observed_load:
                _bootstrap._load_unlocked = _ORIGINAL_LOAD
            elif _bootstrap._load_unlocked is not _ORIGINAL_LOAD:
                owner.counts["errors"] += 1


def _code_children(code):
    pending = [code]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(value for value in current.co_consts if type(value) is types.CodeType)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("IMPORT_PROVENANCE_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(wire):
    return json.loads(wire, object_pairs_hook=_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("IMPORT_PROVENANCE_NONFINITE_JSON")))


def _canonical(value):
    path = Path(value).absolute()
    if path.resolve(strict=True) != path or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("IMPORT_PROVENANCE_CANONICAL_PATH_REQUIRED")
    return path


def _capture(path):
    before = Path(path).lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("IMPORT_PROVENANCE_REGULAR_SINGLELINK_REQUIRED")
    identity = tuple(getattr(before, key) for key in STATS)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    digest, blob, pieces, size = hashlib.sha256(), hashlib.sha1(
        b"blob "+str(before.st_size).encode()+b"\0"), [], 0
    try:
        if tuple(getattr(os.fstat(fd), key) for key in STATS) != identity:
            raise ValueError("IMPORT_PROVENANCE_SOURCE_CHANGED")
        while block := os.read(fd, 1024*1024):
            digest.update(block); blob.update(block); pieces.append(block); size += len(block)
        if (size != before.st_size or tuple(getattr(os.fstat(fd), key) for key in STATS) != identity
                or tuple(getattr(Path(path).lstat(), key) for key in STATS) != identity):
            raise ValueError("IMPORT_PROVENANCE_SOURCE_CHANGED")
    finally:
        os.close(fd)
    return b"".join(pieces), {"sha256":digest.hexdigest(), "blob_id":blob.hexdigest(),
                             "mode":"100"+format(stat.S_IMODE(before.st_mode), "03o")}


def _git(repository, *arguments, payload=None):
    return subprocess.check_output(["git", "--no-replace-objects", "-c", "protocol.allow=never", "-C", str(repository), *arguments],
        input=payload, env={**os.environ,"GIT_NO_LAZY_FETCH":"1","GIT_NO_REPLACE_OBJECTS":"1","GIT_OPTIONAL_LOCKS":"0"})


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _sys_path_snapshot():
    paths, overflow = [], 0
    for item in sys.path:
        if len(paths)>=MAX_RECORDS or not isinstance(item,str) or len(item)>MAX_TEXT:
            overflow += 1
        else:
            paths.append(item)
    return paths, overflow


def _lock_rows(wire):
    rows, current = {}, None
    for line in wire.decode().splitlines():
        value = line.strip().removesuffix("\\").strip()
        if not value or value.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", value)
        if match:
            name = _normalize(match[1])
            if name in rows:
                raise ValueError("IMPORT_PROVENANCE_DUPLICATE_LOCK_PIN")
            rows[name] = {"version":match[2],"hashes":set()}; current = name
        elif current is not None and re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", value):
            rows[current]["hashes"].add(value.removeprefix("--hash=sha256:"))
        else:
            raise ValueError("IMPORT_PROVENANCE_UNSUPPORTED_LOCK_RECORD")
    return rows


def environment_qualification(source):
    """Actual installed metadata and source locks; not installed-byte proof."""
    policy_wire, policy_identity = _capture(Path(source)/"ops/policy/rc6-supply-chain-v1.json")
    policy, expected, locks = _json(policy_wire), {}, {}
    for key, filename in (("packages","requirements.lock.txt"),("build_tools","requirements.build.lock.txt")):
        wire, identity = _capture(Path(source)/filename); rows = _lock_rows(wire)
        configured = {_normalize(row["name"]):row for row in policy[key]}
        if len(configured) != len(policy[key]) or set(configured) != set(rows):
            raise ValueError("IMPORT_PROVENANCE_POLICY_LOCK_NAMES")
        for name, row in rows.items():
            hashes = {item["sha256"] for item in configured[name]["distributions"]}
            if name in expected or row["version"] != configured[name]["version"] or not row["hashes"] or row["hashes"] != hashes:
                raise ValueError("IMPORT_PROVENANCE_POLICY_LOCK_VERSIONS_HASHES")
            expected[name] = row["version"]
        locks[filename] = {"sha256":identity["sha256"],"pins":len(rows)}
    installed, count = {}, 0
    for distribution in importlib.metadata.distributions():
        name = _normalize(distribution.metadata["Name"]); count += 1
        if name in installed:
            raise ValueError("IMPORT_PROVENANCE_DUPLICATE_INSTALLED_NAME")
        installed[name] = distribution.version
    libc_name, libc_version = platform.libc_ver()
    if (policy.get("schema") != "rc6.hashed-distribution-lock.v1" or len(expected) != 157
            or count != 157 or len(installed) != 157 or installed != expected
            or platform.python_version() not in ("3.11.16","3.12.14") or platform.system() != "Linux"
            or platform.machine() != "x86_64" or libc_name != "glibc"
            or tuple(map(int,libc_version.split("."))) < (2,36) or sys.prefix == sys.base_prefix):
        raise ValueError("IMPORT_PROVENANCE_ACTUAL_FROZEN157_REQUIRED")
    return {"schema":"rc6.native-import-environment.v1","scope":"INSTALLED_METADATA_AND_SOURCE_HASH_LOCKS_NOT_WHEEL_BYTES_OR_IMAGE",
            "python":platform.python_version(),"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,
            "installed_total":count,"installed_unique_total":len(installed),"expected_total":len(expected),
            "distributions":[{"name":name,"version":version} for name,version in sorted(installed.items())],
            "locks":locks,"policy_sha256":policy_identity["sha256"],"os":platform.system(),
            "architecture":platform.machine(),"libc":[libc_name,libc_version]}


def prepare_binding(*, source_root, source_repo, source_sha, source_tree, source_index):
    """Before fixtures: bind the complete physical namespace to local raw Git."""
    root, repository, index = map(_canonical, (source_root,source_repo,source_index))
    wire, identity = _capture(index); pin = _json(wire)
    if (not HEX40.fullmatch(source_sha) or not HEX40.fullmatch(source_tree)
            or pin.get("schema") != "rc6.complete-archive-source-pin.v1"
            or pin.get("source_sha") != source_sha or pin.get("source_tree") != source_tree
            or type(pin.get("overlay_count")) is not int or pin["overlay_count"] != 0
            or any(not isinstance(pin.get(key),dict) for key in ("files","modes","blob_ids"))):
        raise ValueError("IMPORT_PROVENANCE_FULL_SOURCE_BINDING_REQUIRED")
    if _git(repository,"for-each-ref","--format=%(refname)","refs/replace").strip():
        raise ValueError("IMPORT_PROVENANCE_GIT_REPLACE_REF")
    commit = _git(repository,"cat-file","commit",source_sha)
    if (hashlib.sha1(b"commit "+str(len(commit)).encode()+b"\0"+commit).hexdigest() != source_sha
            or not commit.startswith(b"tree "+source_tree.encode()+b"\n")
            or hashlib.sha256(commit).hexdigest() != pin.get("raw_git_commit_sha256")):
        raise ValueError("IMPORT_PROVENANCE_RAW_GIT_COMMIT_TREE")
    tracked, directories, tree_ids = {}, set(), {source_tree}
    for row in _git(repository,"ls-tree","-r","-t","--full-tree","-z",source_sha).split(b"\0"):
        if not row:
            continue
        metadata, encoded = row.split(b"\t",1); mode, kind, blob = metadata.decode().split()
        name = encoded.decode(); path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name or not HEX40.fullmatch(blob):
            raise ValueError("IMPORT_PROVENANCE_RAW_GIT_PATH")
        if kind == "tree" and mode == "040000":
            directories.add(name); tree_ids.add(blob)
        elif kind == "blob" and mode in ("100644","100755") and name not in tracked:
            tracked[name] = {"mode":mode,"blob_id":blob}
        else:
            raise ValueError("IMPORT_PROVENANCE_RAW_GIT_MEMBER")
    bodies = _git(repository,"cat-file","--batch",payload=("\n".join(sorted(tree_ids))+"\n").encode())
    offset = 0
    for identifier in sorted(tree_ids):
        end = bodies.find(b"\n",offset); header = bodies[offset:end].split()
        if end<offset or len(header)!=3 or header[:2] != [identifier.encode(),b"tree"]:
            raise ValueError("IMPORT_PROVENANCE_RAW_TREE_HEADER")
        size=int(header[2]); offset=end+1; body=bodies[offset:offset+size]; offset+=size
        if (size<0 or len(body)!=size or bodies[offset:offset+1]!=b"\n"
                or hashlib.sha1(b"tree "+str(size).encode()+b"\0"+body).hexdigest()!=identifier):
            raise ValueError("IMPORT_PROVENANCE_RAW_TREE_HASH")
        offset+=1
    if offset!=len(bodies):
        raise ValueError("IMPORT_PROVENANCE_RAW_TREE_TRAILING_BYTES")
    actual, actual_directories, pending = {}, set(), [root]
    while pending:
        parent = pending.pop()
        descriptor = os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOATIME|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            before=tuple(getattr(os.fstat(descriptor),key) for key in STATS)
            names = os.listdir(descriptor)
            if (len(names)>32768 or tuple(getattr(os.fstat(descriptor),key) for key in STATS)!=before
                    or tuple(getattr(parent.lstat(),key) for key in STATS)!=before):
                raise ValueError("IMPORT_PROVENANCE_DIRECTORY_CHANGED_OR_UNBOUNDED")
        finally:
            os.close(descriptor)
        for name in names:
            path = parent/name; info = path.lstat(); relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o755:
                    raise ValueError("IMPORT_PROVENANCE_SOURCE_DIRECTORY_MODE")
                actual_directories.add(relative); pending.append(path)
            else:
                _, actual[relative] = _capture(path)
    if (actual_directories != directories or set(actual) != set(tracked)
            or set(actual) != set(pin["files"]) or set(actual) != set(pin["modes"]) or set(actual) != set(pin["blob_ids"])
            or any(row != {"sha256":pin["files"][name],"mode":pin["modes"][name],"blob_id":pin["blob_ids"][name]}
                   or any(row[key] != tracked[name][key] for key in ("mode","blob_id")) for name,row in actual.items())):
        raise ValueError("IMPORT_PROVENANCE_PHYSICAL_WHOLE_SOURCE_MISMATCH")
    if Path(__file__).resolve() != root/"scripts/rc6_native_import_provenance.py":
        raise ValueError("IMPORT_PROVENANCE_HELPER_NOT_FROM_PINNED_SOURCE")
    qualification = environment_qualification(root)
    sys_paths, path_overflow = _sys_path_snapshot()
    if path_overflow:
        raise ValueError("IMPORT_PROVENANCE_SYS_PATH_RECORD_BOUND")
    return {"schema":"rc6.native-import-source-binding.v1","source_root":str(root),"source_sha":source_sha,
            "source_tree":source_tree,"source_index_sha256":identity["sha256"],"source_file_count":len(actual),
            "files":actual,"parent_environment_before_fixtures":qualification,"prepared_by_pid":os.getpid(),
            "sys_path_before_fixtures":sys_paths,"scope":"RAW_GIT_PHYSICAL_NAMESPACE_AND_INSTALLED157_BEFORE_FIXTURES"}


class NativeImportObserver:
    """Two retained-module snapshots and a bounded transient audit window."""
    def __init__(self, binding, *, role, record_limit=MAX_RECORDS):
        if type(record_limit) is not int or not 1 <= record_limit <= MAX_RECORDS:
            raise ValueError("IMPORT_PROVENANCE_RECORD_BOUND_INVALID")
        self.binding, self.root, self.role = binding, Path(binding["source_root"]), role
        self.record_limit, self.lock, self.active = record_limit, threading.RLock(), False
        self.stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
        self.sites = sorted({Path(sysconfig.get_path(key)).resolve(strict=True) for key in ("purelib","platlib")})
        if sys.prefix == sys.base_prefix or any(not path.is_relative_to(Path(sys.prefix).resolve()) for path in self.sites):
            raise ValueError("IMPORT_PROVENANCE_ACTUAL_VENV_ROOTS_REQUIRED")
        self.counts = {"import":0,"exec":0,"import_filename_none":0,"opaque_exec":0,"overflow":0,"errors":0}
        self.records, self.seen, self.pending_names = [], set(), set()
        self.native_pid = os.getpid()
        self.installing, self.installation_verified, self.installation_receptions = False, False, 0
        self._installation_nonce = None
        self.evidence_keys, self.opaque_references = set(), {}
        self.attempts, self.loads, self.attempt_stacks, self.load_stacks = [], [], {}, {}
        self.attempt_rows, self.load_rows = {}, {}
        self.unfinished_protocol_calls = 0
        self.synthetic_modules = {}
        self.factories, self.runtime_sources, self.frozen_codes = {}, {}, {}
        self.extension_methods = {}
        self._prepare_runtime_sources()
        self.finalization_failed = False
        self.before = self.snapshot()
        self.installed_at = time.monotonic()
        try:
            _attach_protocol(self)
            self.installing, self._installation_nonce = True, object()
            sys.addaudithook(self.audit)
            # An existing hook may veto addaudithook with RuntimeError without
            # raising here. Only this owned nonce during installation proves
            # reception; the sentinel is never an import/exec record.
            sys.audit(_INSTALLATION_EVENT, self._installation_nonce)
            if self.installation_receptions != 1:
                raise ValueError("IMPORT_PROVENANCE_AUDIT_HOOK_INSTALLATION_UNVERIFIED")
            self.installation_verified = True
            self.active = True
        except BaseException:
            self.deactivate()
            raise
        finally:
            self.installing, self._installation_nonce = False, None

    def _prepare_runtime_sources(self):
        # Reads/compilation occur before installing this observer. No module is
        # executed, and these are compiler references, not binary authentication.
        for module, relative, qualified, payload, mode in (
                ("collections", "collections/__init__.py", "namedtuple", "code", "eval"),
                ("dataclasses", "dataclasses.py", "_create_fn", "txt", "exec"),
                ("typing", "typing.py", "_DeprecatedType.__getattribute__", None, None)):
            path = self.stdlib / relative
            wire, identity = _capture(path)
            reference = compile(wire, str(path), "exec", dont_inherit=True, optimize=sys.flags.optimize)
            code = next((item for item in _code_children(reference) if item.co_qualname == qualified), None)
            if code is None and module != "typing":
                raise ValueError("IMPORT_PROVENANCE_RUNTIME_FACTORY_SOURCE_MISSING")
            self.runtime_sources[module] = {"path":str(path), **identity}
            lines = set()
            if payload:
                node = next(item for item in ast.walk(ast.parse(wire))
                            if isinstance(item, ast.FunctionDef) and item.name == qualified)
                lines = {item.lineno for item in ast.walk(node)
                         if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == mode}
            self.factories[module] = {"code":code,"qualified":qualified,"payload":payload,"mode":mode,"lines":lines}
        for name in _imp._frozen_module_names():
            if len(self.frozen_codes) >= MAX_RECORDS:
                raise ValueError("IMPORT_PROVENANCE_FROZEN_REFERENCE_BOUND")
            if _imp.is_frozen(name):
                code = _imp.get_frozen_object(name)
                if type(code) is types.CodeType:
                    self.frozen_codes.setdefault(code.co_filename, []).append((name, code))
        bootstrap = _imp.get_frozen_object("_frozen_importlib")
        original = {item.co_name:item for item in _code_children(bootstrap)
                    if item.co_name in ("_find_and_load", "_load_unlocked", "_find_and_load_unlocked")}
        self.import_failure_reference = original["_find_and_load_unlocked"]
        if any(type(function) is not types.FunctionType or function.__globals__ is not vars(_bootstrap)
               or function.__code__ != original.get(name)
               or function.__code__.co_filename != original[name].co_filename
               for name, function in (("_find_and_load", _ORIGINAL_FIND), ("_load_unlocked", _ORIGINAL_LOAD))):
            raise ValueError("IMPORT_PROVENANCE_ORIGINAL_IMPORT_MACHINERY_UNVERIFIED")
        external = _imp.get_frozen_object("_frozen_importlib_external")
        methods = {item.co_qualname:item for item in _code_children(external)
                   if item.co_qualname in ("ExtensionFileLoader.create_module", "ExtensionFileLoader.exec_module")}
        for name in ("create_module", "exec_module"):
            function = vars(_EXTENSION_LOADER).get(name)
            if (type(function) is not types.FunctionType
                    or function.__code__ != methods.get("ExtensionFileLoader."+name)):
                raise ValueError("IMPORT_PROVENANCE_ORIGINAL_EXTENSION_LOADER_UNVERIFIED")
            self.extension_methods[name] = function

    def _admit(self, kind, key):
        identity = (kind, key)
        if identity in self.evidence_keys:
            return True
        if len(self.evidence_keys) >= self.record_limit:
            self.counts["overflow"] += 1
            return False
        self.evidence_keys.add(identity)
        return True

    def _qualified_runtime_module(self, name):
        module = sys.modules.get(name)
        if type(module) is not types.ModuleType:
            return None
        values = types.ModuleType.__getattribute__(module, "__dict__")
        expected = self.runtime_sources.get(name)
        spec = values.get("__spec__")
        if (expected is None or values.get("__file__") != expected["path"]
                or type(spec) is not machinery.ModuleSpec or spec.origin != expected["path"]):
            return None
        return values

    def _typing_alias(self, name, alias):
        if name not in ("typing.io", "typing.re"):
            return None
        values = self._qualified_runtime_module("typing")
        if values is None:
            return None
        metaclass = values.get("_DeprecatedType")
        field = name.removeprefix("typing.")
        if (type(metaclass) is not type or type(alias) is not metaclass or values.get(field) is not alias
                or _static_type_text(alias, "__module__") != "typing"
                or _static_type_text(alias, "__name__") != name
                or _static_type_text(alias, "__qualname__") != field):
            return None
        method = _static_type_field(metaclass, "__dict__").get("__getattribute__")
        if (self.factories["typing"]["code"] is None or type(method) is not types.FunctionType or method.__globals__ is not values
                or method.__code__ != self.factories["typing"]["code"]
                or method.__code__.co_filename != self.runtime_sources["typing"]["path"]):
            return None
        return {"origin":"NONMODULE_PUBLIC_TYPING_ALIAS","path":self.runtime_sources["typing"]["path"],
                "parent_module":"typing","alias_identity":hex(id(alias)),"actual_metaclass_identity":hex(id(metaclass)),
                "parent_source_sha256":self.runtime_sources["typing"]["sha256"],
                "scope":"EXACT_RETAINED_PARENT_ALIAS_TYPE_RELATIONSHIP_NOT_CREATION_TIME_OR_BINARY_AUTHENTICATION"}

    def _opaque_evidence(self, code, frame):
        key = (id(code), id(frame.f_code), id(frame.f_globals), frame.f_lineno)
        known = self.opaque_references.get(key)
        if known is not None:
            return key, known[2]
        if not self._admit("opaque_code", key):
            return key, {"status":"UNVERIFIED","reason":"CODE_OBJECT_RECORD_BOUND"}
        row = {"status":"UNVERIFIED","reason":"NO_EXACT_RUNTIME_FACTORY_OR_FROZEN_CODE",
               "actual_code_identity":hex(id(code))}
        if type(code) is types.CodeType:
            for name, reference in self.frozen_codes.get(code.co_filename, ()):
                if code == reference and code.co_filename == reference.co_filename:
                    row = {"status":"QUALIFIED_FROZEN_CODE","interpreter_frozen_name":name,
                           "actual_code_identity":hex(id(code)),"reference_code_identity":hex(id(reference)),
                           "code_sha256":hashlib.sha256(marshal.dumps(code)).hexdigest(),
                           "scope":"EXACT_INTERPRETER_FROZEN_CODE_REFERENCE_NOT_EXECUTABLE_BINARY_AUTHENTICATION"}
                    break
            if row["status"] == "UNVERIFIED":
                for module, definition in self.factories.items():
                    if not definition["payload"]:
                        continue
                    values = self._qualified_runtime_module(module)
                    function = values.get(definition["qualified"]) if values else None
                    if (type(function) is not types.FunctionType or function.__code__ is not frame.f_code
                            or frame.f_globals is not values or frame.f_code != definition["code"]
                            or frame.f_code.co_filename != self.runtime_sources[module]["path"]
                            or frame.f_lineno not in definition["lines"]):
                        continue
                    text = frame.f_locals.get(definition["payload"])
                    if type(text) is not str or len(text) > MAX_TEXT or len(text.encode()) > MAX_TEXT:
                        row["reason"] = "RUNTIME_FACTORY_TEXT_NOT_BOUNDED"
                        self.counts["overflow"] += 1
                        break
                    reference = compile(text, code.co_filename, definition["mode"],
                                        flags=frame.f_code.co_flags & _FUTURE_FLAGS, dont_inherit=True,
                                        optimize=sys.flags.optimize)
                    if code != reference or code.co_filename != "<string>":
                        row["reason"] = "RUNTIME_FACTORY_ACTUAL_CODE_MISMATCH"
                        break
                    row = {"status":"QUALIFIED_RUNTIME_FACTORY_CODE","factory":module+"."+definition["qualified"],
                           "factory_callsite_line":frame.f_lineno,"actual_factory_code_identity":hex(id(frame.f_code)),
                           "actual_function_identity":hex(id(function)),"actual_code_identity":hex(id(code)),
                           "factory_source":self.runtime_sources[module],
                           "generated_source_sha256":hashlib.sha256(text.encode()).hexdigest(),
                           "code_sha256":hashlib.sha256(marshal.dumps(code)).hexdigest(),
                           "scope":"EXACT_FACTORY_CODE_GLOBALS_CALLSITE_AND_COMPILED_GENERATION_INPUT; LOCAL_RUNTIME_EVIDENCE"}
                    break
        # Strong references prevent id reuse during the active window. They are
        # released on deactivation; only bounded JSON evidence survives.
        self.opaque_references[key] = (code, frame.f_code, row, frame.f_globals)
        return key, row

    def _attempt_start(self, name):
        with self.lock:
            try:
                if not self.active or self.native_pid != os.getpid():
                    return None
                if type(name) is not str or len(name) > MAX_TEXT:
                    self.counts["overflow"] += 1
                    return None
                thread = threading.get_ident()
                if sum(map(len, self.attempt_stacks.values())) >= self.record_limit:
                    self.counts["overflow"] += 1
                    return None
                row = {"module":name,"thread_identity":thread,"loader_records":[],"outcome":"IN_PROGRESS"}
                self.attempt_stacks.setdefault(thread, []).append(row)
                return row
            except BaseException:
                self.counts["errors"] += 1
                return None

    def _attempt_end(self, row, result, failure):
        if row is None:
            return
        with self.lock:
            try:
                if not self.active or self.native_pid != os.getpid():
                    return
                stack = self.attempt_stacks.get(row["thread_identity"], [])
                if not stack or stack[-1] is not row:
                    self.counts["errors"] += 1
                else:
                    stack.pop()
                    if not stack:
                        self.attempt_stacks.pop(row["thread_identity"], None)
                if failure is None:
                    row["outcome"] = "RETURNED_MODULE" if type(result) is types.ModuleType else "RETURNED_NONMODULE"
                else:
                    row["outcome"] = "MODULE_NOT_FOUND" if type(failure) is ModuleNotFoundError else "IMPORT_EXCEPTION"
                    row["error_class"] = _static_type_text(type(failure), "__name__")
                    error_name = _IMPORT_ERROR_NAME.__get__(failure, type(failure)) if isinstance(failure, ImportError) else None
                    row["error_name"] = error_name if type(error_name) is str and len(error_name) <= MAX_TEXT else None
                    row["core_missing_raiser_verified"] = False
                    if type(failure) is ModuleNotFoundError:
                        # Bound the exceptional denial path only. A user finder
                        # constructing the same exception class is not a core
                        # optional-lookup outcome and receives no exemption.
                        trace = failure.__traceback__
                        for _ in range(8):
                            if trace is None or trace.tb_next is None:
                                break
                            trace = trace.tb_next
                        if trace is not None and trace.tb_next is None:
                            row["core_missing_raiser_verified"] = (
                                trace.tb_frame.f_code == self.import_failure_reference
                                and trace.tb_frame.f_code.co_filename == self.import_failure_reference.co_filename
                                and trace.tb_frame.f_globals is vars(_bootstrap))
                key = (row["module"], row["outcome"], row.get("error_class"), row.get("error_name"),
                       row.get("core_missing_raiser_verified"),
                       tuple(row["loader_records"]))
                if self._admit("import_attempt", key):
                    if key not in self.attempt_rows:
                        row["occurrences"] = 0
                        self.attempt_rows[key] = row
                        self.attempts.append(row)
                    self.attempt_rows[key]["occurrences"] += 1
            except BaseException:
                self.counts["errors"] += 1

    def _loader_start(self, spec):
        with self.lock:
            try:
                if not self.active or self.native_pid != os.getpid():
                    return None
                if type(spec) is not machinery.ModuleSpec or type(spec.name) is not str or len(spec.name) > MAX_TEXT:
                    self.counts["errors"] += 1
                    return None
                origin = spec.origin if type(spec.origin) is str and len(spec.origin) <= MAX_TEXT else None
                if type(spec.origin) is str and len(spec.origin) > MAX_TEXT:
                    self.counts["overflow"] += 1
                thread = threading.get_ident()
                if sum(map(len, self.load_stacks.values())) >= self.record_limit:
                    self.counts["overflow"] += 1
                    return None
                loader_module = _static_type_text(type(spec.loader), "__module__")
                loader_name = _static_type_text(type(spec.loader), "__qualname__")
                row = {"module":spec.name,"origin":origin,"thread_identity":thread,
                       "loader_type":loader_module+"."+loader_name if loader_module is not None and loader_name is not None else "UNVERIFIED_CLASS_METADATA",
                       "outcome":"IN_PROGRESS"}
                if row["loader_type"] == "UNVERIFIED_CLASS_METADATA":
                    self.counts["errors"] += 1
                if len(row["loader_type"]) > MAX_TEXT:
                    self.counts["overflow"] += 1
                    return None
                extension = (type(spec.loader) is _EXTENSION_LOADER
                             and spec.name in ("charset_normalizer.cd", "charset_normalizer.md"))
                if extension:
                    for method, reference in self.extension_methods.items():
                        bound = getattr(spec.loader, method)
                        if (type(bound) is not types.MethodType or bound.__self__ is not spec.loader
                                or bound.__func__ is not reference):
                            extension = False
                            row["synthetic_module_lineage"] = "UNVERIFIED_EXTENSION_METHOD_IDENTITY"
                            break
                before = dict(sys.modules) if extension and len(sys.modules) <= self.record_limit else None
                if extension and before is None:
                    self.counts["overflow"] += 1
                token = {"row":row,"spec":spec,"loader":spec.loader,"before":before,
                         "concurrent":bool(self.load_stacks and thread not in self.load_stacks)}
                for other_thread, others in self.load_stacks.items():
                    if other_thread != thread:
                        for other in others:
                            other["concurrent"] = True
                self.load_stacks.setdefault(thread, []).append(token)
                return token
            except BaseException:
                self.counts["errors"] += 1
                return None

    def _loader_end(self, token, result, failure):
        if token is None:
            return
        with self.lock:
            try:
                if not self.active or self.native_pid != os.getpid():
                    return
                row, spec = token["row"], token["spec"]
                stack = self.load_stacks.get(row["thread_identity"], [])
                if not stack or stack[-1] is not token:
                    self.counts["errors"] += 1
                else:
                    stack.pop()
                    if not stack:
                        self.load_stacks.pop(row["thread_identity"], None)
                row["outcome"] = "LOADER_RETURNED" if failure is None else "LOADER_EXCEPTION"
                if failure is not None:
                    row["error_class"] = _static_type_text(type(failure), "__name__")
                before = token["before"]
                if before is None or failure is not None or type(result) is not types.ModuleType:
                    return
                values = types.ModuleType.__getattribute__(result, "__dict__")
                qualified = (not token["concurrent"] and values.get("__spec__") is spec
                             and values.get("__loader__") is token["loader"]
                             and values.get("__file__") == row["origin"]
                             and self._origin(row["origin"])["origin"] == "ACTUAL_VENV")
                if not qualified:
                    row["synthetic_module_lineage"] = "UNVERIFIED_EXTENSION_PARENT_OR_CONCURRENT_CREATION"
                    return
                created = []
                for name, module in tuple(sys.modules.items()):
                    if name in before or type(name) is not str or len(name) > MAX_TEXT or type(module) is not types.ModuleType:
                        continue
                    fields = types.ModuleType.__getattribute__(module, "__dict__")
                    if fields.get("__file__") or fields.get("__spec__") or fields.get("__path__"):
                        continue
                    if name in self.synthetic_modules:
                        # The deepest observed extension load owns creation;
                        # enclosing imports cannot relabel its provider.
                        continue
                    # Versioned Cython types require actual parent attributes of
                    # precisely that registered type, not a name-prefix rule.
                    referenced_types = []
                    for value in values.values():
                        cls = type(value)
                        class_name = _static_type_text(cls, "__name__")
                        if (_static_type_text(cls, "__module__") == name and class_name is not None
                                and fields.get(class_name) is cls):
                            referenced_types.append(class_name)
                    runtime_shape = (name == "cython_runtime" and fields.get("__name__") == name
                                     and type(fields.get("line_trace")) is bool
                                     and set(fields) <= {"__name__","__doc__","__package__","__loader__","__spec__","line_trace"})
                    if not referenced_types and not runtime_shape:
                        continue
                    if not self._admit("extension_created_registry_object", (name, id(module))):
                        continue
                    proof = {"origin":"EXTENSION_CREATED_RUNTIME_MODULE","path":row["origin"],
                             "extension_module":row["module"],"actual_object_identity":hex(id(module)),
                             "extension_object_identity":hex(id(result)),"loader_type":row["loader_type"],
                             "referenced_type_attributes":sorted(set(referenced_types)),
                             "runtime_shape_observed":runtime_shape,"thread_identity":row["thread_identity"],
                             "scope":"EXACT_REGISTRY_OBJECT_CREATED_INSIDE_UNCONTENDED_ORIGINAL_EXTENSION_LOAD; NOT_BINARY_AUTHENTICATION"}
                    self.synthetic_modules[name] = (module, result, spec, token["loader"], proof)
                    created.append(name)
                row["synthetic_module_lineage"] = {"created":created,"concurrent":False}
            except BaseException:
                self.counts["errors"] += 1
            finally:
                if self.active and self.native_pid == os.getpid():
                    self._retain_loader(token["row"])

    def _retain_loader(self, row):
        try:
            key = (row["module"], row["origin"], row["loader_type"], row["outcome"], row.get("error_class"))
            if self._admit("selected_loader", key):
                if key not in self.load_rows:
                    row["occurrences"] = 0
                    self.load_rows[key] = len(self.loads)
                    self.loads.append(row)
                index = self.load_rows[key]
                self.loads[index]["occurrences"] += 1
                for attempt in self.attempt_stacks.get(row["thread_identity"], ()):
                    if index not in attempt["loader_records"]:
                        attempt["loader_records"].append(index)
        except BaseException:
            self.counts["errors"] += 1

    def _origin(self, filename):
        if not isinstance(filename,str) or not filename or len(filename) > MAX_TEXT or filename.startswith("<"):
            return {"origin":"UNLOCATABLE","path":filename if isinstance(filename,str) and len(filename)<=MAX_TEXT else None}
        path = Path(filename).absolute().resolve(strict=False)
        if path.is_relative_to(self.root):
            relative = path.relative_to(self.root).as_posix(); pinned = self.binding["files"].get(relative)
            return {"path":str(path),"origin":"PINNED_SOURCE" if pinned else "UNPINNED_SOURCE",
                    "relative_path":relative,**(pinned or {})}
        if any(path.is_relative_to(site) for site in self.sites):
            return {"path":str(path),"origin":"ACTUAL_VENV"}
        if path.is_relative_to(self.stdlib) and "site-packages" not in path.parts:
            return {"path":str(path),"origin":"STDLIB"}
        return {"path":str(path),"origin":"ALIEN"}

    def snapshot(self):
        modules, unresolved, overflow, origin_count = {}, [], 0, 0
        for name, module in sorted(tuple(sys.modules.items())):
            if origin_count+len(unresolved) >= self.record_limit:
                overflow += 1; continue
            if not isinstance(name,str) or len(name)>MAX_TEXT:
                overflow += 1; continue
            if not isinstance(module,types.ModuleType):
                alias = self._typing_alias(name, module)
                if alias:
                    modules[name] = [alias]; origin_count += 1
                else:
                    unresolved.append({"module":name,"reason":"NOT_A_MODULE_OBJECT"})
                continue
            values = types.ModuleType.__getattribute__(module,"__dict__")
            filename, spec = values.get("__file__"), values.get("__spec__")
            if spec is not None and type(spec) is not machinery.ModuleSpec:
                unresolved.append({"module":name,"reason":"SPEC_TYPE_NOT_OBSERVABLE_WITHOUT_CALLBACK"})
                continue
            origin = spec.origin if spec is not None else None
            if not filename and origin == "built-in" and name in sys.builtin_module_names:
                modules[name] = [{"origin":"BUILTIN","path":None}]; origin_count+=1; continue
            if not filename and origin == "frozen":
                modules[name] = [{"origin":"FROZEN_SPEC","path":None}]; origin_count+=1; continue
            namespace = values.get("__path__",())
            paths = [filename] if filename else []
            if not filename:
                for index,item in enumerate(namespace):
                    if origin_count+len(unresolved)+index >= self.record_limit:
                        overflow += 1; break
                    paths.append(item)
            if filename and isinstance(origin,str) and origin not in {"built-in","frozen"} and origin != filename:
                unresolved.append({"module":name,"reason":"FILE_SPEC_ORIGIN_MISMATCH"})
                paths.append(origin)
            if not paths:
                captured = self.synthetic_modules.get(name)
                if captured and captured[0] is module and sys.modules.get(captured[4]["extension_module"]) is captured[1]:
                    parent = types.ModuleType.__getattribute__(captured[1], "__dict__")
                    if (parent.get("__spec__") is captured[2] and parent.get("__loader__") is captured[3]
                            and parent.get("__file__") == captured[4]["path"]):
                        modules[name] = [captured[4]]; origin_count += 1
                        continue
                unresolved.append({"module":name,"reason":"ORIGIN_NOT_OBSERVABLE_NO_EXACT_EXTENSION_CREATION_LINEAGE"})
                continue
            rows = []
            for filename in paths:
                if origin_count+len(unresolved)>=self.record_limit:
                    overflow+=1; break
                row = self._origin(filename)
                if row["origin"] == "UNPINNED_SOURCE" and not values.get("__file__"):
                    path = PurePosixPath(row["relative_path"])
                    if any(path in PurePosixPath(key).parents for key in self.binding["files"]):
                        row["origin"] = "PINNED_SOURCE_NAMESPACE"
                rows.append(row)
                origin_count+=1
            modules[name] = rows
        sys_paths, path_overflow = _sys_path_snapshot()
        return {"schema":"rc6.native-retained-modules.v1","native_pid":os.getpid(),"parent_pid":os.getppid(),
            "role":self.role,"modules":modules,"retained_module_count":len(modules),"unresolved":unresolved,
            "overflow_count":overflow+path_overflow,"origin_record_count":origin_count,"sys_path":sys_paths,"executable":sys.executable,"prefix":sys.prefix,
            "base_prefix":sys.base_prefix,"stdlib":str(self.stdlib),"actual_venv_sites":list(map(str,self.sites)),
            "scope":"RETAINED_MODULE_LOCATIONS_AT_ONE_BOUNDARY_NOT_TRANSIENT_EXECUTION_CLOSURE"}

    def audit(self, event, arguments):
        if event == _INSTALLATION_EVENT:
            if (self.installing and self.native_pid == os.getpid()
                    and len(arguments) == 1 and arguments[0] is self._installation_nonce):
                self.installation_receptions += 1
            return
        if not self.active or self.native_pid != os.getpid() or event not in ("import","exec"):
            return
        with self.lock:
            if not self.active:
                return
            try:
                self.counts[event] += 1
                module, filename = (arguments[0],arguments[1]) if event == "import" else (None,arguments[0].co_filename)
                opaque = None
                if event == "import" and filename is None:
                    self.counts["import_filename_none"] += 1
                    if (isinstance(module,str) and len(module)<=MAX_TEXT
                            and (module in self.pending_names or len(self.pending_names)<self.record_limit)):
                        self.pending_names.add(module)
                    else:
                        self.counts["overflow"] += 1
                if event == "exec" and (not isinstance(filename,str) or filename.startswith("<")):
                    self.counts["opaque_exec"] += 1
                if (module is not None and (not isinstance(module,str) or len(module)>MAX_TEXT)
                        or filename is not None and (not isinstance(filename,str) or len(filename)>MAX_TEXT)):
                    self.counts["overflow"] += 1; return
                if event == "exec" and (not isinstance(filename,str) or filename.startswith("<")):
                    # One caller frame on the rare denial branch; no stack walk
                    # or function-call profiler. A code object is classified once
                    # for this actual factory/callsite, never by filename alone.
                    opaque_key, opaque = self._opaque_evidence(arguments[0], sys._getframe(1))
                key = (event,module,filename,opaque_key if opaque is not None else None)
                if key in self.seen:
                    return
                if not self._admit("audit_event", key):
                    return
                self.seen.add(key)
                row = {"event":event,"module":module,"filename":filename}
                if opaque is not None:
                    row["runtime_code_provenance"] = opaque
                self.records.append(row)
            except BaseException:
                self.counts["errors"] += 1

    def initial_receipt(self):
        return {"schema":"rc6.native-import-proof.v1","status":"IN_PROGRESS_NOT_CLOSED","native_pid":os.getpid(),
            "parent_pid":os.getppid(),"source_sha":self.binding["source_sha"],"source_tree":self.binding["source_tree"],
            "source_index_sha256":self.binding["source_index_sha256"],"boundary_before":self.before,
            "audit_hook_installation":{"verified":self.installation_verified,"receptions":self.installation_receptions,
                "event":_INSTALLATION_EVENT,"scope":"OWNED_NONCE_RECEIVED_EXACTLY_ONCE_DURING_INSTALLATION_ONLY"},
            "installed_at_monotonic":self.installed_at,"transient_closure_verified":False}

    def deactivate(self):
        """Audit hooks cannot be removed; every ownership exit makes this inert."""
        with self.lock:
            self.active = False
            self.unfinished_protocol_calls += sum(map(len, self.attempt_stacks.values())) + sum(map(len, self.load_stacks.values()))
            self.attempt_stacks.clear()
            self.load_stacks.clear()
            self.opaque_references.clear()
            self.synthetic_modules.clear()
        _detach_protocol(self)

    def _resolved_attempt(self, name):
        allowed = {"PINNED_SOURCE", "STDLIB", "ACTUAL_VENV", "BUILTIN", "FROZEN_SPEC"}
        candidates = [row for row in self.attempts if row["module"] == name]
        if not candidates:
            return None
        outcomes = []
        for row in candidates:
            if (row["outcome"] == "MODULE_NOT_FOUND" and row.get("error_name") == name
                    and row.get("core_missing_raiser_verified") is True and not row["loader_records"]):
                outcomes.append({"basis":"ACTUAL_MODULE_NOT_FOUND_BEFORE_ANY_SELECTED_LOADER", **row})
                continue
            selected = [self.loads[index] for index in row["loader_records"] if self.loads[index]["module"] == name]
            if not selected:
                return None
            observed = []
            for loader in selected:
                origin = loader.get("origin")
                path = self._origin(origin) if origin not in ("built-in", "frozen") else {"origin":"BUILTIN" if origin == "built-in" else "FROZEN_SPEC", "path":None}
                matched = [event for event in self.records
                           if event["filename"] == origin and event["event"] in ("exec", "import")]
                if path["origin"] not in allowed or not matched:
                    return None
                observed.append({"loader":loader,"observed_origin":path,
                                 "execution_event_basis":"ACTUAL_MATCHING_IMPORT_OR_EXEC_EVENT"})
            outcomes.append({"basis":"ACTUAL_SELECTED_LOADER_AND_OBSERVED_EXECUTION_WITH_RECORDED_OUTCOME",
                             "import_attempt":row,"selected_loaders":observed})
        return {"module":name,"basis":"ACTUAL_IMPORT_MACHINERY_OUTCOMES_NOT_FINAL_ABSENCE", "outcomes":outcomes}

    def finish(self):
        try:
            after = self.snapshot()
        except BaseException:
            with self.lock:
                self.finalization_failed = True
                self.counts["errors"] += 1
            raise
        finally:
            self.deactivate()
            with self.lock:
                closed_at = time.monotonic()
                records, counts, pending = list(self.records),dict(self.counts),set(self.pending_names)
        observations, resolved, lifecycle_resolved, unresolved = [], [], [], []
        for record in records:
            row = dict(record)
            runtime = record.get("runtime_code_provenance")
            if runtime and runtime["status"] in ("QUALIFIED_RUNTIME_FACTORY_CODE", "QUALIFIED_FROZEN_CODE"):
                row["observed_origin"] = {"origin":runtime["status"],"path":record["filename"],"scope":"QUALIFIED_LOCAL_RUNTIME_CREATION_NOT_FILE_OR_BINARY_AUTHENTICATION"}
            else:
                row["observed_origin"] = self._origin(record["filename"]) if record["filename"] is not None else None
            observations.append(row)
        for name in sorted(pending):
            rows = after["modules"].get(name)
            if rows and all(row["origin"] in {"PINNED_SOURCE","PINNED_SOURCE_NAMESPACE","STDLIB","ACTUAL_VENV","BUILTIN","FROZEN_SPEC",
                                              "NONMODULE_PUBLIC_TYPING_ALIAS","EXTENSION_CREATED_RUNTIME_MODULE"} for row in rows):
                resolved.append({"module":name,"basis":"ACTUAL_RETAINED_MODULE_AT_FINAL_BOUNDARY","origins":rows})
            else:
                outcome = self._resolved_attempt(name)
                if outcome is not None:
                    lifecycle_resolved.append(outcome)
                else:
                    unresolved.append(name)
        invalid = [{"phase":phase,"module":name,**row} for phase,graph in (("BEFORE",self.before),("AFTER",after))
            for name,rows in graph["modules"].items() for row in rows if row["origin"] in {"ALIEN","UNPINNED_SOURCE"}]
        invalid += [row for row in observations if row["observed_origin"] and row["observed_origin"]["origin"] in {"ALIEN","UNPINNED_SOURCE"}]
        opaque_unresolved = [row for row in observations if row.get("runtime_code_provenance", {}).get("status") == "UNVERIFIED"]
        missing = (not self.installation_verified or self.finalization_failed or counts["errors"] or counts["overflow"] or opaque_unresolved or unresolved
            or self.unfinished_protocol_calls
            or self.before["unresolved"] or after["unresolved"] or self.before["overflow_count"] or after["overflow_count"]
            or any(row["origin"]=="UNLOCATABLE" for graph in (self.before,after) for rows in graph["modules"].values() for row in rows)
            or any(row["observed_origin"] is not None and row["observed_origin"]["origin"]=="UNLOCATABLE" for row in observations))
        status = "BLOCKED_ALIEN_OR_UNPINNED" if invalid else "UNVERIFIED_LIMITS" if missing else "VERIFIED_OBSERVED_WINDOW"
        return {**self.initial_receipt(),"status":status,"boundary_after":after,"closed_at_monotonic":closed_at,
            "event_counts":counts,"event_records":observations,"filename_none_resolved_by_retained_boundary":resolved,
            "filename_none_resolved_by_import_lifecycle":lifecycle_resolved,
            "filename_none_unresolved":unresolved,"invalid":invalid,"record_limit":self.record_limit,
            "import_attempt_outcomes":list(self.attempts),"selected_loader_outcomes":list(self.loads),
            "unfinished_import_protocol_calls":self.unfinished_protocol_calls,"shared_evidence_record_count":len(self.evidence_keys),
            "runtime_factory_source_qualification":self.runtime_sources,
            "opaque_exec_unresolved_records":len(opaque_unresolved),
            "transient_closure_verified":status=="VERIFIED_OBSERVED_WINDOW",
            "scope":"AUDIT_IMPORT_EXEC_FROM_INSTALLATION_THROUGH_FINAL_BOUNDARY; SNAPSHOTS_ARE_RETAINED_ONLY",
            "limits":["Bootstrap before hook installation and final IPC/emission/process teardown after this boundary are outside this window.",
                "Unknown opaque code, unresolved imports/registry objects, active import calls, observer errors and truncation prevent a closed-window claim.",
                "Observed paths and code.co_filename are local interpreter metadata, not cryptographic authentication of executed code or installed binaries.",
                "Exact runtime factory/frozen-code and extension registry relationships are bounded local evidence, not binary authentication.",
                "No function execution counts, call profiling, full-stack sampler, GC callback or threshold change; opaque denial inspects one caller frame."]}

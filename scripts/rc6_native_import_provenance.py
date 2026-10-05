"""Bounded native import provenance, separate from business/resource success.

Only preparation and two module boundaries inspect files. The audit callback
counts import/exec events and retains bounded strings; it never hashes files,
samples stacks, profiles calls, requests GC or changes a deadline.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
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
STATS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
         "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")


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
        self.finalization_failed = False
        self.before = self.snapshot()
        self.installed_at = time.monotonic()
        sys.addaudithook(self.audit)
        self.active = True

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
                unresolved.append({"module":name,"reason":"NOT_A_MODULE_OBJECT"}); continue
            values = types.ModuleType.__getattribute__(module,"__dict__")
            filename, spec = values.get("__file__"), values.get("__spec__")
            origin = getattr(spec,"origin",None)
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
                unresolved.append({"module":name,"reason":"ORIGIN_NOT_OBSERVABLE"}); continue
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
        if not self.active or event not in ("import","exec"):
            return
        with self.lock:
            if not self.active:
                return
            try:
                self.counts[event] += 1
                module, filename = (arguments[0],arguments[1]) if event == "import" else (None,arguments[0].co_filename)
                if event == "import" and filename is None:
                    self.counts["import_filename_none"] += 1
                    if isinstance(module,str) and len(module)<=MAX_TEXT and len(self.pending_names)<self.record_limit:
                        self.pending_names.add(module)
                    else:
                        self.counts["overflow"] += 1
                if event == "exec" and (not isinstance(filename,str) or filename.startswith("<")):
                    self.counts["opaque_exec"] += 1
                if (module is not None and (not isinstance(module,str) or len(module)>MAX_TEXT)
                        or filename is not None and (not isinstance(filename,str) or len(filename)>MAX_TEXT)):
                    self.counts["overflow"] += 1; return
                key = (event,module,filename)
                if key in self.seen:
                    return
                if len(self.records)>=self.record_limit:
                    self.counts["overflow"] += 1; return
                self.seen.add(key); self.records.append({"event":event,"module":module,"filename":filename})
            except BaseException:
                self.counts["errors"] += 1

    def initial_receipt(self):
        return {"schema":"rc6.native-import-proof.v1","status":"IN_PROGRESS_NOT_CLOSED","native_pid":os.getpid(),
            "parent_pid":os.getppid(),"source_sha":self.binding["source_sha"],"source_tree":self.binding["source_tree"],
            "source_index_sha256":self.binding["source_index_sha256"],"boundary_before":self.before,
            "installed_at_monotonic":self.installed_at,"transient_closure_verified":False}

    def deactivate(self):
        """Audit hooks cannot be removed; every ownership exit makes this inert."""
        with self.lock:
            self.active = False

    def finish(self):
        try:
            after = self.snapshot()
        except BaseException:
            with self.lock:
                self.finalization_failed = True
                self.counts["errors"] += 1
            raise
        finally:
            with self.lock:
                self.active = False; closed_at = time.monotonic()
                records, counts, pending = list(self.records),dict(self.counts),set(self.pending_names)
        observations, resolved, unresolved = [], [], []
        for record in records:
            row = dict(record)
            row["observed_origin"] = self._origin(record["filename"]) if record["filename"] is not None else None
            observations.append(row)
        for name in sorted(pending):
            rows = after["modules"].get(name)
            if rows and all(row["origin"] in {"PINNED_SOURCE","PINNED_SOURCE_NAMESPACE","STDLIB","ACTUAL_VENV","BUILTIN","FROZEN_SPEC"} for row in rows):
                resolved.append({"module":name,"basis":"ACTUAL_RETAINED_MODULE_AT_FINAL_BOUNDARY","origins":rows})
            else:
                unresolved.append(name)
        invalid = [{"phase":phase,"module":name,**row} for phase,graph in (("BEFORE",self.before),("AFTER",after))
            for name,rows in graph["modules"].items() for row in rows if row["origin"] in {"ALIEN","UNPINNED_SOURCE"}]
        invalid += [row for row in observations if row["observed_origin"] and row["observed_origin"]["origin"] in {"ALIEN","UNPINNED_SOURCE"}]
        missing = (self.finalization_failed or counts["errors"] or counts["overflow"] or counts["opaque_exec"] or unresolved
            or self.before["unresolved"] or after["unresolved"] or self.before["overflow_count"] or after["overflow_count"]
            or any(row["origin"]=="UNLOCATABLE" for graph in (self.before,after) for rows in graph["modules"].values() for row in rows)
            or any(row["observed_origin"] is not None and row["observed_origin"]["origin"]=="UNLOCATABLE" for row in observations))
        status = "BLOCKED_ALIEN_OR_UNPINNED" if invalid else "UNVERIFIED_LIMITS" if missing else "VERIFIED_OBSERVED_WINDOW"
        return {**self.initial_receipt(),"status":status,"boundary_after":after,"closed_at_monotonic":closed_at,
            "event_counts":counts,"event_records":observations,"filename_none_resolved_by_retained_boundary":resolved,
            "filename_none_unresolved":unresolved,"invalid":invalid,"record_limit":self.record_limit,
            "transient_closure_verified":status=="VERIFIED_OBSERVED_WINDOW",
            "scope":"AUDIT_IMPORT_EXEC_FROM_INSTALLATION_THROUGH_FINAL_BOUNDARY; SNAPSHOTS_ARE_RETAINED_ONLY",
            "limits":["Bootstrap before hook installation and final IPC/emission/process teardown after this boundary are outside this window.",
                "Unknown/opaque exec, unresolved filenameNone, observer errors and truncation prevent a closed-window claim.",
                "Observed paths and code.co_filename are local interpreter metadata, not cryptographic authentication of executed code or installed binaries.",
                "No function execution counts, call profiling, stack sampler, GC callback or threshold change."]}

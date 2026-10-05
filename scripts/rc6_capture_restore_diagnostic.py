"""Offline, bounded profiling of a copied real committed cut; no acceptance.

Only the child imports product code. Source data is opened with NOATIME and is
never passed to SQLite. Timers wrap coarse functions, not each JSON token.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import faulthandler
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import resource
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import traceback
from urllib.parse import unquote, urlsplit


INTERPRETER = "/workspace/venv_rc6_frozen311/bin/python"
PRODUCT_SHA = "400a677c7a2d94e52fcc7e1c598f94a66862253c"
WATCHDOG_SECONDS = 60
MAX_RSS = 2 * 1024**3
COPY_LIMIT = 512 * 1024**2
RESERVE = 2 * 1024**3
MAX_OUTPUT = 1024**2
MAX_ENTRIES = 35000
ROOTS = ("data/paper_v17/artifacts/observer_v17.db/dynamic-shadow",
         "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow.authority")
STATS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
         "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
MUTABLE = {"cross_payload_hashes", "evidence_retention", "status", "observation_status",
           "report_digest", "checkpoint_digest"}


def _stat(info):
    return {key: getattr(info, key) for key in STATS}


def _absolute(path):
    path = Path(path).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("DIAGNOSTIC_SOURCE_ALIAS_FORBIDDEN")
    normalized = Path(os.path.abspath(path))
    if any(parent.is_symlink() for parent in (normalized, *normalized.parents)):
        raise ValueError("DIAGNOSTIC_SOURCE_ALIAS_FORBIDDEN")
    return normalized


def _file(path, *, maximum=None, destination=None, dir_fd=None, expected=None, git_blob=False):
    """Hash the complete file and optionally copy, without changing source stats."""
    if dir_fd is None:
        path = _absolute(path)
    before = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if expected is not None and _stat(before) != expected:
        raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("DIAGNOSTIC_SOURCE_ALIAS_FORBIDDEN")
    if maximum is not None and before.st_size > maximum:
        raise ValueError("DIAGNOSTIC_SOURCE_SIZE_LIMIT")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK
    fd = os.open(path, flags, dir_fd=dir_fd)
    output = None
    digest, count = hashlib.sha256(), 0
    blob = hashlib.sha1(b"blob "+str(before.st_size).encode()+b"\0") if git_blob else None
    try:
        if _stat(os.fstat(fd)) != _stat(before):
            raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
        if destination is not None:
            out_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             stat.S_IMODE(before.st_mode))
            try:
                os.fchmod(out_fd, stat.S_IMODE(before.st_mode))
                output = os.fdopen(out_fd, "wb")
            except BaseException:
                os.close(out_fd)
                raise
        while count <= before.st_size:
            raw = os.read(fd, min(1024**2, before.st_size-count+1))
            if not raw:
                break
            count += len(raw)
            if count > before.st_size:
                raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
            digest.update(raw)
            if blob is not None:
                blob.update(raw)
            if output is not None:
                _space(Path(destination).parent, pending=len(raw))
                output.write(raw)
        if (count != before.st_size or _stat(os.fstat(fd)) != _stat(before)
                or _stat(os.stat(path, dir_fd=dir_fd, follow_symlinks=False)) != _stat(before)):
            raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
    finally:
        try:
            if output is not None:
                output.close()
        finally:
            os.close(fd)
    result = {"sha256": digest.hexdigest(), "stats": _stat(before)}
    if blob is not None:
        result["git_blob"] = blob.hexdigest()
    return result


@contextmanager
def _directory(path, *, dir_fd=None, expected=None):
    if dir_fd is None:
        path = _absolute(path)
    before = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("DIAGNOSTIC_SOURCE_ALIAS_FORBIDDEN")
    if expected is not None and _stat(before) != expected:
        raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME,
                 dir_fd=dir_fd)
    try:
        if _stat(os.fstat(fd)) != _stat(before):
            raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
        yield fd, before
        if (_stat(os.fstat(fd)) != _stat(before)
                or _stat(os.stat(path, dir_fd=dir_fd, follow_symlinks=False)) != _stat(before)):
            raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
    finally:
        os.close(fd)


def snapshot(root):
    """NoAtime dirfd enumeration plus full hashes and all eleven selected stats."""
    root = _absolute(root)
    result = {}
    def visit(path, relative, parent_fd=None):
        if len(result) >= MAX_ENTRIES:
            raise ValueError("DIAGNOSTIC_SOURCE_ENTRY_LIMIT")
        info = os.stat(path, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
            result[relative] = _file(path, dir_fd=parent_fd, expected=_stat(info))
            return
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("DIAGNOSTIC_SOURCE_ALIAS_FORBIDDEN")
        with _directory(path, dir_fd=parent_fd, expected=_stat(info)) as (fd, _):
            names = os.listdir(fd)
            if len(names)+len(result) > MAX_ENTRIES:
                raise ValueError("DIAGNOSTIC_SOURCE_ENTRY_LIMIT")
            result[relative] = {"stats": _stat(info)}
            for name in sorted(names):
                visit(name, relative+"/"+name if relative != "." else name, fd)
    visit(root, ".")
    return result


def _space(path, *, pending=0):
    available = os.statvfs(path)
    if (available.f_bavail*available.f_frsize-pending < RESERVE or not available.f_files
            or available.f_favail*100 < available.f_files*10):
        raise ValueError("DIAGNOSTIC_PRIVATE_SPACE_REJECTED")


def _write(path, value):
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode()+b"\n"
    if len(raw) > MAX_OUTPUT:
        raise ValueError("DIAGNOSTIC_OUTPUT_LIMIT")
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def _reason(error):
    value = str(error)
    if re.fullmatch(r"(?:SHADOW|DIAGNOSTIC|SOURCE|RETENTION|FUNNEL)_[A-Z0-9_]{1,140}", value):
        return value
    return type(error).__name__


def _preflight(source, pin):
    installed, required, locks = {}, {}, {}
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    for distribution in importlib.metadata.distributions():
        name = normalize(distribution.metadata["Name"])
        if name in installed:
            raise ValueError("DIAGNOSTIC_DUPLICATE_DISTRIBUTION")
        installed[name] = distribution.version
    for name in ("requirements.lock.txt", "requirements.build.lock.txt"):
        raw = (source/name).read_bytes()
        count = 0
        for line in raw.decode().splitlines():
            content = line.strip().removesuffix("\\").strip()
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", content)
            if match is None:
                if "==" in content and not content.startswith("#"):
                    raise ValueError("DIAGNOSTIC_LOCK_FORMAT_REJECTED")
                continue
            key, value = normalize(match[1]), match[2]
            if key in required:
                raise ValueError("DIAGNOSTIC_DUPLICATE_LOCK_PIN")
            required[key] = value; count += 1
        locks[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "pins": count}
    passed = (sys.executable == INTERPRETER and platform.python_version() == "3.11.16"
        and len(installed) == len(required) == 157 and installed == required
        and locks["requirements.lock.txt"]["pins"] == 154
        and locks["requirements.build.lock.txt"]["pins"] == 3
        and pin["source_sha"] == PRODUCT_SHA and type(pin["overlay_count"]) is int
        and pin["overlay_count"] == 0)
    result = {"passed": passed, "interpreter": sys.executable, "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version, "distribution_count": len(installed), "locks": locks,
        "distributions": [{"name": key, "version": value} for key,value in sorted(installed.items())],
        "scope": "NAMES_AND_VERSIONS_NOT_DISTRIBUTION_BYTE_PROOF"}
    if not passed:
        raise ValueError("DIAGNOSTIC_FROZEN157_PREFLIGHT_REJECTED")
    return result


def _source_inventory(source, pin):
    result = {}
    for name, expected_sha in pin["files"].items():
        path = source/name
        record = _file(path, git_blob=True)
        info = record["stats"]
        mode = "100"+format(stat.S_IMODE(info["st_mode"]), "03o")
        blob = record["git_blob"]
        if record["sha256"] != expected_sha or mode != pin["modes"][name] or blob != pin["blob_ids"][name]:
            raise ValueError("DIAGNOSTIC_PRODUCT_SOURCE_MISMATCH")
        result[name] = {"sha256": record["sha256"], "mode": mode, "git_blob": blob}
    actual = {str(path.relative_to(source)) for path in source.rglob("*") if path.is_file()}
    if actual != set(pin["files"]):
        raise ValueError("DIAGNOSTIC_PRODUCT_NAMESPACE_MISMATCH")
    return result


def _copy(data, before, private):
    evidence = data/ROOTS[0]
    roots = tuple(data/name for name in ROOTS)
    selected = {}
    for root in roots:
        prefix = str(root.relative_to(data))
        for name, record in before.items():
            if name == prefix or name.startswith(prefix+"/"):
                selected[name] = record
    if any(str(root.relative_to(data)) not in selected for root in roots):
        raise ValueError("DIAGNOSTIC_COMMITTED_ROOTS_MISSING")
    unit = os.statvfs(private).f_frsize
    missing_parents = {str(parent) for name in selected for parent in Path(name).parents
                       if str(parent) not in selected and str(parent) != "."}
    forecast = (sum(max(row["stats"]["st_blocks"]*512,
        ((row["stats"]["st_size"]+unit-1)//unit)*unit, unit) for row in selected.values())
        + (len(missing_parents)+1)*unit)
    if forecast > COPY_LIMIT:
        raise ValueError("DIAGNOSTIC_COPY_QUOTA_REACHED")
    _space(private, pending=forecast)
    copies = {}
    for name, record in sorted(selected.items(), key=lambda item: (item[0].count("/"), item[0])):
        target, info = private/name, record["stats"]
        if stat.S_ISDIR(info["st_mode"]):
            target.mkdir(parents=True, mode=stat.S_IMODE(info["st_mode"]), exist_ok=False)
            target.chmod(stat.S_IMODE(info["st_mode"]))
            continue
        # Pin every ancestor to the original DATA inventory. Absolute leaf
        # opens alone would still follow a replaced parent directory.
        with _directory(data, expected=before["."]["stats"]) as (root_fd, _):
            with _copy_ancestors(root_fd, Path(name).parts[:-1], before) as directory:
                copies[name] = _file(Path(name).name, dir_fd=directory,
                                     expected=info, destination=target)
        if copies[name] != record:
            raise ValueError("DIAGNOSTIC_SOURCE_CHANGED")
    measured = snapshot(private)
    occupied = sum(max(row["stats"]["st_size"], row["stats"]["st_blocks"]*512) for row in measured.values())
    if occupied > COPY_LIMIT:
        raise ValueError("DIAGNOSTIC_COPY_QUOTA_REACHED")
    return private/evidence.relative_to(data), {"forecast_bytes": forecast, "allocated_or_logical_bytes": occupied,
        "file_hashes": {name: record["sha256"] for name,record in copies.items()},
        "source_control_bytes_rewritten": False}


@contextmanager
def _copy_ancestors(directory, names, before, prefix=""):
    if not names:
        yield directory
        return
    relative = prefix+"/"+names[0] if prefix else names[0]
    with _directory(names[0], dir_fd=directory, expected=before[relative]["stats"]) as (child, _):
        with _copy_ancestors(child, names[1:], before, relative) as leaf:
            yield leaf


@contextmanager
def _offline(data):
    attempts = {"network": 0, "source_sqlite": 0}
    previous = []
    def replace(owner, name, value):
        previous.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)
    def forbidden(*args, **kwargs):
        attempts["network"] += 1
        raise ValueError("DIAGNOSTIC_NETWORK_FORBIDDEN")
    for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            replace(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        replace(socket, name, forbidden)
    connect = sqlite3.connect
    def guarded(database, *args, **kwargs):
        raw = os.fsdecode(database)
        if raw not in (":memory:", ""):
            parsed = unquote(urlsplit(raw).path) if raw.startswith("file:") else raw
            if _absolute(parsed).is_relative_to(data):
                attempts["source_sqlite"] += 1
                raise ValueError("DIAGNOSTIC_SOURCE_SQLITE_FORBIDDEN")
        return connect(database, *args, **kwargs)
    replace(sqlite3, "connect", guarded)
    replace(sqlite3.dbapi2, "connect", guarded)
    try:
        yield attempts
    finally:
        for owner, name, value in reversed(previous):
            setattr(owner, name, value)


def _child(args, source, attempts):
    raw, data = Path(args.raw), _absolute(args.data)
    sys.path.insert(0, str(source))
    from rc6_shadow_runtime import packed_storage as packed, persistence, serialization
    state = {"schema": "rc6.capture-restore-native-diagnostic.v1", "pid": os.getpid(),
        "phase": args.phase, "timers": {}, "current_stage": "IMPORTS_COMPLETED", "diagnostic_only": True,
        "acceptance_complete": False, "worker_tick_called": False, "provider_called": False,
        "publisher_alias_layout_recreated": False, "gc_thresholds_before": gc.get_threshold(),
        "original_cycle_budget_seconds": 90, "diagnostic_watchdog_seconds": WATCHDOG_SECONDS,
        "original_lab_source_capture_budget_seconds": 0.25,
        "checkpoint_mutable_alias_sharing_enabled": False}
    def progress():
        state["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        _write(raw/"native-progress.json", state)
    def timer(owner, name, *, labels=False):
        original = getattr(owner, name)
        def measured(*items, **kwargs):
            label = getattr(owner, "__name__", type(owner).__name__)+"."+name
            if labels:
                field = kwargs.get("name", items[2] if len(items) > 2 else "")
                label += ":"+str(field)[:160]
            elif name in {"_bytes", "_payload"}:
                label += ":"+Path(items[1]).name
            metrics = state["timers"].setdefault(label, {"calls": 0, "wall_seconds": 0., "cpu_seconds": 0.})
            metrics["calls"] += 1
            wall, cpu = time.monotonic(), time.process_time()
            try:
                return original(*items, **kwargs)
            finally:
                metrics["wall_seconds"] += time.monotonic()-wall
                metrics["cpu_seconds"] += time.process_time()-cpu
        setattr(owner, name, measured)
    for owner,name in ((persistence.EvidenceFiles,"_bytes"),(persistence.EvidenceFiles,"_payload"),
        (persistence,"_json"),(packed,"unpack_wire"),(serialization,"_loads"),
        (serialization,"_shape"),(serialization,"canonical_metrics"),(packed._CaptureBuilder,"__init__"),
        (packed._CaptureBuilder,"_named"),(packed.PreparedPackedStorage,"metrics")):
        timer(owner,name)
    timer(packed._CaptureBuilder,"capture",labels=True)
    started, cpu = time.monotonic(), time.process_time()
    stack = (raw/"samples.log").open("wb")
    faulthandler.dump_traceback_later(10, repeat=True, file=stack)
    try:
        files = persistence.EvidenceFiles(Path(args.private))
        state["current_stage"] = "SEALED_RESTART_READER" if args.phase == "restore" else "SEALED_WIRE_READER"
        progress()
        cut = files.read_writer_generation(checkpoint=args.phase == "restore")
        state.update(pointer=cut["pointer"], manifest_sha256=cut["pointer"]["manifest_sha256"],
            as_of=cut["manifest"]["as_of"], export_verification_level=cut["export_contract"]["verification_level"],
            verified_payloads=cut["export_contract"]["verified_payloads"])
        if args.phase == "restore":
            checkpoint = cut["checkpoint"]
            state["checkpoint_schema"] = checkpoint.get("schema")
            state["restored_source_identity"] = checkpoint.get("source_identity")
            state["same_generation"] = checkpoint.get("generation_id") == cut["pointer"]["generation_id"]
            if not state["same_generation"]:
                raise ValueError("DIAGNOSTIC_CHECKPOINT_BINDING_MISMATCH")
        else:
            state["current_stage"] = "REPORT_LOGICAL_DECODE"
            progress()
            member = files._bytes(files.root/("gen-"+cut["pointer"]["generation_id"])/"report.json.gz")
            if hashlib.sha256(member).hexdigest() != cut["manifest"]["files"]["report"]["sha256"]:
                raise ValueError("DIAGNOSTIC_MEMBER_DIGEST_MISMATCH")
            report, proof = files._payload("report.json.gz",member,details=True)
            persistence._semantic_safety({"report":report}); persistence._semantic_sources(report)
            if (proof["payload_digest"] != cut["manifest"]["files"]["report"]["payload_digest"]
                    or any(report.get(key) != value for key,value in cut["export_contract"]["role_headers"]["report"].items())):
                raise ValueError("DIAGNOSTIC_REPORT_BINDING_MISMATCH")
            del member
            state["current_stage"] = "PREPARE_REAL_DECODED_REPORT"
            progress()
            prepared = packed.PreparedPackedStorage(report,mutable=MUTABLE,
                durable_limit=files.payload_limit-128,expansion_limit=persistence.EXPANDED_PAYLOAD_LIMIT,
                cache={},shape_memo={})
            actual_sha, actual_size = prepared.metrics(report)
            if (actual_sha,actual_size) != (proof["payload_digest"],proof["logical_bytes"]):
                raise ValueError("DIAGNOSTIC_CAPTURE_DIGEST_MISMATCH")
            state.update(report_schema=report.get("schema"),report_phase=report.get("phase"),
                logical_sha256=actual_sha,logical_bytes=actual_size,
                capture_input="REAL_LOGICAL_REPORT_NATIVE_DECODE_ALIAS_LAYOUT_MAY_DIFFER_FROM_PUBLISHER")
        state["native_completed"] = True
    except BaseException as error:
        state.update(native_completed=False,error_class=type(error).__name__,reason=_reason(error),
            exception_frames=[{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                              for frame in traceback.extract_tb(error.__traceback__)[-64:]])
    finally:
        faulthandler.cancel_dump_traceback_later(); stack.close()
        state.update(wall_seconds=time.monotonic()-started,cpu_seconds=time.process_time()-cpu,
            gc_thresholds_after=gc.get_threshold(),attempts=attempts)
        imports,alien = [],[]
        for name,module in sorted(sys.modules.items()):
            if not getattr(module,"__file__",None): continue
            path = Path(module.__file__).resolve()
            expected = source.joinpath(*name.split("."))
            if path.is_relative_to(source) or expected.with_suffix(".py").is_file() or (expected/"__init__.py").is_file():
                row = {"module":name,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                imports.append(row)
                if not path.is_relative_to(source): alien.append(row)
        state.update(product_imports=imports,alien_product_imports=alien)
        progress(); _write(raw/"native-result.json",state)
    return int(not state.get("native_completed") or bool(alien) or any(attempts.values()))


def _monitor(command, source, raw, metadata):
    """Kill and reap the diagnostic child on its independent resource bound."""
    started, peak, reason = time.monotonic(), 0, None
    process = None
    try:
        with (raw/"child.log").open("wb") as log:
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE":"1", "PYTHONPATH":"",
                           "PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}
            environment.pop("PYTHONHOME", None)
            process = subprocess.Popen(command, cwd=source, stdout=log, stderr=subprocess.STDOUT,
                                       env=environment)
            metadata["native_pid"] = process.pid
            _write(raw/"wrapper-started.json", metadata)
            while process.poll() is None:
                try:
                    fields = Path(f"/proc/{process.pid}/status").read_text().splitlines()
                    peak = max(peak, max((int(line.split()[1])*1024 for line in fields
                               if line.startswith(("VmRSS:", "VmHWM:"))), default=0))
                except FileNotFoundError:
                    pass
                if time.monotonic()-started >= WATCHDOG_SECONDS:
                    reason = "DIAGNOSTIC_WATCHDOG_EXHAUSTED"
                if peak > MAX_RSS:
                    reason = "DIAGNOSTIC_RSS_REACHED"
                for name, limit in (("samples.log", 65536), ("child.log", MAX_OUTPUT),
                                    ("native-progress.json", MAX_OUTPUT), ("native-result.json", MAX_OUTPUT)):
                    if (raw/name).exists() and (raw/name).stat().st_size > limit:
                        reason = "DIAGNOSTIC_OUTPUT_LIMIT"
                if reason:
                    process.kill()
                    process.wait()
                    break
                time.sleep(0.05)
        return {"native_returncode": process.returncode, "watchdog_reason": reason,
                "native_child_elapsed_seconds": time.monotonic()-started,
                "native_sampled_peak_rss_bytes": peak}
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()


def _run_parent(args, source, data, raw, pin, checks, attempts):
    raw.mkdir(parents=True, mode=0o700)
    _space(raw)
    _write(raw/"entry-preflight.json", checks)
    private = raw/"private-copy"
    metadata = {"schema":"rc6.capture-restore-diagnostic-wrapper.v1", "diagnostic_only":True,
        "source_sha":pin["source_sha"], "source_tree":pin["source_tree"], "source_files":len(pin["files"]),
        "source_tar_sha256":pin["tar_sha256"],
        "source_index_sha256":hashlib.sha256(args.source_index.read_bytes()).hexdigest(),
        "profiler_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "host":socket.gethostname(), "platform":platform.platform(), "pid":os.getpid(), "native_pid":None,
        "uid":os.getuid(), "gid":os.getgid(), "source_data":str(data), "private_copy":str(private),
        "raw":str(raw), "parent_argv":sys.argv, "phase":args.phase,
        "original_cycle_budget_seconds":90, "original_lab_source_capture_budget_seconds":0.25,
        "diagnostic_watchdog_seconds":WATCHDOG_SECONDS, "maximum_rss_bytes":MAX_RSS,
        "copy_limit_bytes":COPY_LIMIT, "space_reserve_bytes":RESERVE, "minimum_free_inode_percent":10,
        "copy_filesystem_dev":raw.stat().st_dev,
        "acceptance_complete":False, "publisher_cpu_claim":False, "publisher_alias_layout_recreated":False,
        "worker_tick_called":False, "source_sqlite_allowed":False, "gc_policy_changed":False,
        "source_sha_modes_blobs_unchanged":None, "source_data_all11stats_hashes_unchanged":None,
        "private_copy_all11stats_hashes_unchanged":None, "diagnostic_completed":False}
    started = time.monotonic()
    source_before = before = private_before = None
    failed = False
    try:
        source_before = _source_inventory(source, pin)
        before = snapshot(data)
        _write(raw/"source-data-before.json", before)
        private.mkdir(mode=0o700)
        evidence, copied = _copy(data, before, private)
        private_before = snapshot(private)
        command = [INTERPRETER, "-B", "-u", str(Path(__file__).absolute()), "--phase", args.phase,
            "--source-index", str(args.source_index.absolute()), "--source", str(source),
            "--data", str(data), "--raw", str(raw), "--private", str(evidence)]
        metadata.update(copy=copied, command=command,
            source_component_sha256={name: pin["files"][name] for name in
                ("rc6_shadow_runtime/packed_storage.py", "rc6_shadow_runtime/serialization.py",
                 "rc6_shadow_runtime/persistence.py", "rc6_shadow_runtime/projection.py")})
        _write(raw/"wrapper-before.json", metadata)
        metadata.update(_monitor(command, source, raw, metadata))
        failed = bool(metadata["native_returncode"] != 0 or metadata["watchdog_reason"])
        native_path = raw/"native-result.json"
        if native_path.exists() and native_path.stat().st_size <= MAX_OUTPUT:
            native = json.loads(native_path.read_bytes())
            if (native.get("peak_rss_bytes", MAX_RSS+1) > MAX_RSS
                    or native.get("gc_thresholds_before") != native.get("gc_thresholds_after")):
                failed = True
        metadata["diagnostic_completed"] = not failed
    except BaseException as error:
        failed = True
        metadata.update(error_class=type(error).__name__, reason=_reason(error),
            exception_frames=[{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                              for frame in traceback.extract_tb(error.__traceback__)[-64:]])
    finally:
        for label, original, root in (("source", source_before, source), ("source-data", before, data),
                                       ("private-copy", private_before, private)):
            if original is None:
                continue
            key = {"source":"source_sha_modes_blobs_unchanged", "source-data":"source_data_all11stats_hashes_unchanged",
                   "private-copy":"private_copy_all11stats_hashes_unchanged"}[label]
            try:
                current = _source_inventory(root, pin) if label == "source" else snapshot(root)
                if label != "source":
                    _write(raw/(label+"-after.json"), current)
                metadata[key] = original == current
                failed = failed or original != current
            except BaseException as error:
                failed = True
                metadata.setdefault("custody_errors", {})[label] = {"error_class":type(error).__name__, "reason":_reason(error)}
        metadata.update(diagnostic_completed=bool(metadata["diagnostic_completed"] and not failed),
                        total_parent_elapsed_seconds=time.monotonic()-started, parent_attempts=attempts)
        _write(raw/"wrapper-final.json", metadata)
        print(json.dumps(metadata, sort_keys=True))
    return int(failed or any(attempts.values()))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",required=True,choices=("restore","capture-report"))
    parser.add_argument("--source-index",required=True,type=Path)
    parser.add_argument("--source",required=True,type=Path)
    parser.add_argument("--data",required=True,type=Path)
    parser.add_argument("--raw",required=True,type=Path)
    parser.add_argument("--private",type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    os.umask(0o077)
    source=_absolute(args.source); data=_absolute(args.data); raw=_absolute(args.raw)
    if (raw.is_relative_to(data) or source.is_relative_to(data) or data.is_relative_to(raw)
            or raw.is_relative_to(source) or source.is_relative_to(raw) or data.is_relative_to(source)):
        raise ValueError("DIAGNOSTIC_SOURCE_OUTPUT_OVERLAP")
    pin=json.loads(args.source_index.read_bytes())
    checks=_preflight(source,pin)
    if args.private is not None:
        private = _absolute(args.private)
        if not private.is_relative_to(raw/"private-copy"):
            raise ValueError("DIAGNOSTIC_PRIVATE_PATH_REJECTED")
        with _offline(data) as attempts:
            return _child(args,source,attempts)
    if raw.exists(): raise ValueError("DIAGNOSTIC_NEW_OUTPUT_REQUIRED")
    with _offline(data) as attempts:
        return _run_parent(args, source, data, raw, pin, checks, attempts)


if __name__ == "__main__":
    raise SystemExit(main())

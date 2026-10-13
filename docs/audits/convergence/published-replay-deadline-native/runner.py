"""External whole-Git selected-module focal harness; no Docker, installer or remote API.

Socket/SQL observations cover this Python process and known executable clients.
They are not a kernel network namespace or a transitive child attestation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import time
import traceback
import uuid

SUPPORT_SHA = "f4e99413c0e933e7a7468156c6fea937790fcdda15f1e772e99ea7fd71b50a21"
MODULE = "tests/test_rc6_convergence_sre_published_evidence.py"
SCOPE = "SELECTED_MODULE_CONTROLLED_CLOCK_SUBPROCESS; NO_DOCKER_REMOTE_API_FULL_GOV_OR_IMAGE_ACCEPTANCE"


def load_support(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SUPPORT_SHA:
        raise ValueError("EXTERNAL_SUPPORT_SOURCE_HASH_MISMATCH")
    specification = importlib.util.spec_from_file_location("replay_focal_source_support", path)
    support = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = support
    specification.loader.exec_module(support)
    support.capture(path)
    return support


def check_interpreter(support):
    support.require(sys.platform == "linux" and sys.version_info[:3] == (3, 11, 16)
        and os.getuid() == os.geteuid() != 0 and hasattr(os, "wait4"),
        "LITERAL_FROZEN31116_NONROOT_LINUX_WAIT4_REQUIRED")


def source_imports(support, root, pin, infrastructure):
    allowed_libraries = {Path(sysconfig.get_path(key)).resolve() for key in
                         ("stdlib", "platstdlib", "purelib", "platlib")}
    result = {"source": [], "external_infrastructure": [], "libraries": [],
              "no_physical_origin": [], "unexpected": []}
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        origin = getattr(module, "__file__", None)
        if origin is None:
            result["no_physical_origin"].append(name)
            continue
        path = Path(os.path.abspath(origin))
        if path.is_relative_to(root):
            expected = pin["files"].get(path.relative_to(root).as_posix())
            raw, _ = support.capture(path)
            support.require(expected is not None and support.digest(raw) == expected["sha256"],
                            "ACTUAL_SOURCE_IMPORT_BYTE_BINDING_MISMATCH:"+name)
            result["source"].append({"module": name, "path": str(path.relative_to(root)),
                                     "sha256": support.digest(raw)})
        elif path in infrastructure:
            raw, _ = support.capture(path)
            support.require(support.digest(raw) == infrastructure[path], "INFRASTRUCTURE_IMPORT_CHANGED")
            result["external_infrastructure"].append({"module": name, "path": str(path),
                                                       "sha256": support.digest(raw)})
        elif any(path.resolve().is_relative_to(location) for location in allowed_libraries):
            result["libraries"].append({"module": name, "path": str(path),
                                        "bytes_authenticated": False})
        else:
            result["unexpected"].append({"module": name, "path": str(path)})
    support.require(not result["unexpected"], "UNEXPECTED_PHYSICAL_IMPORT_ORIGIN")
    return result


def child(args, support):
    root, raw_root = support.safe_path(args.source_root), support.safe_path(args.output_root)/"raw"
    os.chdir(root)
    before = support.source_pin(root, args.source_sha, args.source_tree)
    closure = support.installed_closure(root)
    launch = json.loads(support.capture(raw_root/"launch.json")[0])
    infrastructure = {Path(row["path"]): row["sha256"] for row in launch["infrastructure"]}
    support.require(support.digest(support.capture(__file__)[0]) == infrastructure[Path(__file__)],
                    "CHILD_HARNESS_CHANGED")
    support.publish(raw_root/"child.source-before.index.json", support.canonical(before))
    support.publish(raw_root/"child.prefixture157.json", support.canonical(closure))
    observations = {"inet_attempts": [], "sqlite_attempts": [], "forbidden_executable_attempts": [],
                    "subprocess_executable_counts": {}}
    def reject_network(label):
        observations["inet_attempts"].append(label)
        raise RuntimeError("FOCAL_NETWORK_OPERATION_FORBIDDEN")
    def observe(event, arguments):
        if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
            reject_network(event)
        if event in ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg") and \
                getattr(arguments[0], "family", None) in (socket.AF_INET, socket.AF_INET6):
            reject_network(event)
        if event == "sqlite3.connect":
            observations["sqlite_attempts"].append(event)
            raise RuntimeError("FOCAL_SQLITE_OPERATION_FORBIDDEN")
        if event == "subprocess.Popen":
            executable = os.path.basename(os.fsdecode(arguments[0]))
            counts = observations["subprocess_executable_counts"]
            counts[executable] = counts.get(executable, 0)+1
            if executable in ("docker", "curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "socat", "telnet"):
                observations["forbidden_executable_attempts"].append(executable)
                raise RuntimeError("FOCAL_DOCKER_OR_NETWORK_EXECUTABLE_FORBIDDEN")
    sys.addaudithook(observe)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
        def blocked_dns(*_args, _name=name, **_kwargs):
            reject_network("socket."+_name)
        setattr(socket, name, blocked_dns)
    for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg", "bind"):
        if not hasattr(socket.socket, name):
            continue
        original = getattr(socket.socket, name)
        def blocked_inet(instance, *values, _name=name, _original=original, **options):
            if instance.family in (socket.AF_INET, socket.AF_INET6):
                reject_network("socket.socket."+_name)
            return _original(instance, *values, **options)
        setattr(socket.socket, name, blocked_inet)
    def blocked_sqlite(*_args, **_kwargs):
        observations["sqlite_attempts"].append("sqlite3.connect")
        raise RuntimeError("FOCAL_SQLITE_OPERATION_FORBIDDEN")
    sqlite3.connect = blocked_sqlite
    sqlite3.dbapi2.connect = blocked_sqlite
    import pytest
    from _pytest.junitxml import mangle_test_address
    items = []
    class Observer:
        def pytest_collection_finish(self, session):
            for item in session.items:
                address = mangle_test_address(item.nodeid)
                items.append({"nodeid": item.nodeid, "classname": ".".join(address[:-1]), "name": address[-1]})
            support.require(items and all(row["nodeid"].startswith(MODULE+"::") for row in items),
                            "SELECTED_MODULE_DISCOVERY_MISMATCH")
            support.publish(raw_root/"collection-before-fixtures.json", support.canonical({
                "phase": "pytest_collection_finish_before_fixtures", "items": items,
                "count": len(items), "execution_id": launch["execution_id"]}))
    argv = ["-q", MODULE, "-p", "no:cacheprovider", "-o", "pythonpath=.", "-o", "junit_family=legacy",
            "--junitxml="+str(raw_root/"focal.xml"), "--basetemp="+str(raw_root/"pytest-private")]
    rc = int(pytest.main(argv, plugins=[Observer()]))
    from scripts import rc6_convergence_provenance as verifier
    junit = verifier.capture_junit(raw_root/"focal.xml")
    suite, cases = verifier.governed_junit_cases(junit)
    executed_identities = Counter((case.get("classname"), case.get("name")) for case in cases)
    collected_identities = Counter((row["classname"], row["name"]) for row in items)
    support.require(executed_identities == collected_identities and all(value == 1 for value in
                    collected_identities.values()), "COLLECTED_EXECUTED_REAL_CASES_MISMATCH")
    counts = {name: int(suite.get(name, "-1")) for name in ("tests", "failures", "errors", "skipped")}
    if rc == 0:
        _nodes, count = verifier.executed_cases(junit)
        support.require(count == len(items), "EXECUTED_UNIQUE_CASE_COUNT_MISMATCH")
    imports = source_imports(support, root, before, infrastructure)
    after = support.source_pin(root, args.source_sha, args.source_tree)
    atime = support.compare_source(before, after)
    support.publish(raw_root/"child.source-after.index.json", support.canonical(after))
    support.require(not observations["inet_attempts"] and not observations["sqlite_attempts"]
        and not observations["forbidden_executable_attempts"], "FOCAL_OFFLINE_OR_SQL_GUARD_VIOLATION")
    report = {"schema": "rc6.published-replay-deadline-focal.v1", "scope": SCOPE,
        "execution_id": launch["execution_id"], "pid": os.getpid(), "source_sha": args.source_sha,
        "source_tree": args.source_tree, "status": "GREEN" if rc == 0 else "RED", "pytest_exit_code": rc,
        "pytest_main_invocations": 1, "selected_module": MODULE, "pytest_arguments": argv,
        "collected": len(items), "executed": len(cases), "junit_counts": counts,
        "junit_sha256": junit.sha256, "junit_bytes": len(junit.data), "items": items,
        "metadata157_and_hash_lock_platform_actions_before_fixtures": closure,
        "source_byte_mode_blob_stable_custody_and_namespace_unchanged": True, "overlay_count": 0,
        "source_atime_changes_observed_separately": atime,
        "source_data_db_all_stat_custody_claimed": False, "actual_imports": imports, **observations,
        "network_observation_scope": "THIS_PYTHON_PROCESS_PLUS_KNOWN_CLIENT_EXECUTABLES; NOT_KERNEL_OR_TRANSITIVE",
        "docker_executed": False, "remote_api_executed": False, "virtual_clock_controls_are_real_waits": False,
        "full_governed_suite_or_artifact_acceptance": False}
    support.publish(raw_root/"focal.receipt.json", support.canonical(report))
    print(json.dumps({"status": report["status"], "cases": len(cases), "scope": SCOPE}, sort_keys=True), flush=True)
    return rc


def parent(args, support):
    output = support.safe_path(args.output_root)
    repo = support.safe_path(args.git_repo)
    support.require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(repo),
                    "FRESH_EXTERNAL_OUTPUT_REQUIRED")
    output.mkdir(mode=0o700)
    raw = output/"raw"; raw.mkdir(mode=0o700)
    source = output/"source"
    environment = dict(os.environ)
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", GIT_NO_LAZY_FETCH="1")
    environment.pop("PYTEST_ADDOPTS", None)
    support.require(not any(key.startswith(("POROTA_", "PAPER_")) or key in ("DATA_DIR", "HIST_DB_PATH")
        for key in environment), "OPERATIONAL_ENVIRONMENT_FORBIDDEN")
    subprocess.run(["git", "--no-replace-objects", "-c", "protocol.file.allow=always", "-c", "protocol.http.allow=never",
        "-c", "protocol.https.allow=never", "-c", "protocol.ssh.allow=never", "-c", "core.hooksPath=/dev/null",
        "clone", "--shared", "--no-checkout", str(repo), str(source)], check=True, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    support.git(source, "config", "core.hooksPath", "/dev/null")
    support.git(source, "checkout", "--detach", args.source_sha)
    before = support.source_pin(source, args.source_sha, args.source_tree)
    closure = support.installed_closure(source)
    support.publish(raw/"parent.source-before.index.json", support.canonical(before))
    support.publish(raw/"parent.prefixture157.json", support.canonical(closure))
    infrastructure = []
    for original, name in ((Path(__file__), "runner.py"), (Path(args.support), "source_support.py")):
        data, _ = support.capture(original)
        target = raw/name; support.publish(target, data)
        infrastructure.append({"path": str(target), "bytes": len(data), "sha256": support.digest(data)})
    execution = uuid.uuid4().hex
    command = [sys.executable, "-I", "-B", str(raw/"runner.py"), "--child", "--git-repo", str(repo),
        "--source-root", str(source), "--source-sha", args.source_sha, "--source-tree", args.source_tree,
        "--output-root", str(output), "--support", str(raw/"source_support.py"), "--timeout-seconds", str(args.timeout_seconds)]
    launch = {"schema": "rc6.published-replay-deadline-focal-launch.v1", "execution_id": execution,
        "command": command, "source_sha": args.source_sha, "source_tree": args.source_tree,
        "uid": os.getuid(), "euid": os.geteuid(), "interpreter": sys.executable,
        "python_version": list(sys.version_info[:3]), "infrastructure": infrastructure,
        "scope": SCOPE, "timeout_seconds": args.timeout_seconds, "native_fixture_started": False,
        "source_files": len(before["files"]), "preflight157_status": closure["status"]}
    support.publish(raw/"launch.json", support.canonical(launch))
    support.compare_source(before, support.source_pin(source, args.source_sha, args.source_tree))
    started = time.monotonic()
    descriptor = os.open(raw/"focal.log", os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "wb", buffering=0) as log:
        process = subprocess.Popen(command, cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print(json.dumps({"event": "NATIVE_START", "pid": process.pid, "uid": os.geteuid(),
            "source_sha": args.source_sha, "source_tree": args.source_tree, "source_files": len(before["files"]),
            "interpreter": sys.executable, "output": str(output), "command": command}, sort_keys=True), flush=True)
        timed_out = False
        terminate_at = None
        while True:
            waited, status, usage = os.wait4(process.pid, os.WNOHANG)
            if waited:
                process.returncode = os.waitstatus_to_exitcode(status)
                break
            now = time.monotonic()
            if now-started > args.timeout_seconds and not timed_out:
                timed_out = True; terminate_at = now
                os.killpg(process.pid, signal.SIGTERM)
            elif timed_out and now-terminate_at > 5:
                os.killpg(process.pid, signal.SIGKILL)
            time.sleep(0.05)
        log.flush(); os.fsync(log.fileno())
    wall = time.monotonic()-started
    after = support.source_pin(source, args.source_sha, args.source_tree)
    atime = support.compare_source(before, after)
    support.publish(raw/"parent.source-after.index.json", support.canonical(after))
    resources = {"schema": "rc6.published-replay-deadline-focal-kernel.v1", "execution_id": execution,
        "pid": process.pid, "exit_code": process.returncode, "timed_out": timed_out,
        "elapsed_wall_seconds": wall, "cpu_user_seconds": usage.ru_utime, "cpu_system_seconds": usage.ru_stime,
        "peak_rss_bytes": usage.ru_maxrss*1024, "source_sha": args.source_sha, "source_tree": args.source_tree,
        "source_files": len(before["files"]), "source_bytes_modes_blobs_stable_custody_unchanged": True,
        "source_overlay_count": 0, "source_atime_changes_observed_separately": atime,
        "resource_scope": "ACTUAL_SINGLE_CHILD_PID_AND_KERNEL_ACCOUNTED_REAPED_DESCENDANTS; NOT_CONCURRENT_RSS_SUM",
        "scope": SCOPE, "raw_log_sha256": support.digest(support.capture(raw/"focal.log")[0])}
    support.publish(raw/"kernel.resources.json", support.canonical(resources))
    print(json.dumps({"event": "NATIVE_END", "pid": process.pid, "exit_code": process.returncode,
        "timed_out": timed_out, "elapsed_wall_seconds": wall, "peak_rss_bytes": usage.ru_maxrss*1024,
        "output": str(output)}, sort_keys=True), flush=True)
    return process.returncode if not timed_out else 124


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-repo", required=True)
    parser.add_argument("--source-root")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--support", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--child", action="store_true")
    arguments = parser.parse_args()
    os.umask(0o022)
    helper = load_support(arguments.support)
    check_interpreter(helper)
    try:
        sys.exit(child(arguments, helper) if arguments.child else parent(arguments, helper))
    except Exception as error:
        print("FOCAL_HARNESS_OR_GUARD_RED: "+type(error).__name__+": "+str(error), file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)

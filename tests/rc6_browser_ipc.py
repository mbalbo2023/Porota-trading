"""Bounded stdio transport for an isolated, locked browser product process.

This module imports only the standard library. Playwright belongs to the driver;
financial writers/readers and dashboard handlers belong to the child process.
"""
from contextlib import ExitStack
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import selectors
import socket
import sqlite3
import stat
import subprocess
import sys
from threading import Event, Lock, Thread
from time import monotonic, perf_counter
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

PROTOCOL = "rc6.browser-product-ipc.v1"
MAX_FRAME = 4 * 1024**2
PRODUCT_REQUEST_SECONDS = 1.0
HEALTH_REQUEST_SECONDS = 2.0
ROOT = Path(__file__).resolve().parents[1]


class GateFailure(AssertionError):
    def __init__(self, gate, details=None):
        super().__init__(gate)
        self.details = details or {}


class ProductDiagnosticWindowEnded(Exception):
    """A protocol control event, distinct from a native handler failure."""


def require(value, gate, details=None):
    if not value:
        raise GateFailure(gate, details)


def normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def environment_receipt(root, *, product=False, executable=None, python_version=None):
    """Read installed metadata before importing or constructing native fixtures."""
    installed = {}
    for distribution in metadata.distributions():
        name = normalized_name(distribution.metadata["Name"])
        require(name not in installed, "PRODUCT_DUPLICATE_DISTRIBUTION")
        installed[name] = distribution.version
    expected = {}
    for filename in ("requirements.lock.txt", "requirements.build.lock.txt"):
        for line in (root / filename).read_text().splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)==([^\\\s]+)", line)
            if match:
                name, version = match.groups()
                name = normalized_name(name)
                require(name not in expected or expected[name] == version, "PRODUCT_LOCK_CONTRADICTION")
                expected[name] = version
    receipt = {"role": "PRODUCT" if product else "BROWSER_DRIVER", "sys_executable": sys.executable,
               "python": platform.python_version(), "installed_count": len(installed),
               "installed": installed, "locked_count": len(expected),
               "metadata_sha256": hashlib.sha256(json.dumps(installed, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest(),
               "missing": sorted(set(expected) - set(installed)),
               "extra": sorted(set(installed) - set(expected)),
               "wrong_versions": {key: {"locked": value, "installed": installed.get(key)}
                   for key, value in expected.items() if key in installed and installed[key] != value}}
    if product:
        require(os.path.abspath(sys.executable) == os.path.abspath(str(executable)), "PRODUCT_INTERPRETER_MISMATCH")
        require(Path(executable).absolute().parent.parent.resolve() == Path(sys.prefix).resolve(),
                "PRODUCT_CANONICAL_PREFIX_MISMATCH")
        supported = {"3.11", "3.12"}
        actual_python = ".".join(map(str, sys.version_info[:2]))
        require(actual_python in supported and (python_version is None or python_version == actual_python)
                and len(expected) == 157
                and installed == expected, "PRODUCT_LOCKED_ENVIRONMENT_REQUIRED", receipt)
    return receipt


def protected_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "SOURCE_MEMBER_ALIAS_OR_TYPE_FORBIDDEN")
        value = stream.read()
        after = os.fstat(stream.fileno())
        require((info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode)
                and len(value) == info.st_size, "SOURCE_MEMBER_CHANGED_DURING_READ")
        return value


def source_inventory(root):
    require(not any(path.is_symlink() for path in (root, *root.parents)), "SOURCE_ALIAS_FORBIDDEN")
    result = {}
    for folder, directories, files in os.walk(root, followlinks=False):
        # Worktree guards may use the complete checkout. Frozen archives have
        # no Git/cache members, and final index equality rejects any extra file.
        directories[:] = sorted(name for name in directories if name != ".git")
        for directory in directories:
            require(not (Path(folder) / directory).is_symlink(), "SOURCE_ALIAS_FORBIDDEN")
        for name in sorted(files):
            path = Path(folder) / name
            if path == root / ".git":
                continue
            raw = protected_bytes(path)
            result[str(path.relative_to(root))] = {"sha256": hashlib.sha256(raw).hexdigest(),
                "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                "bytes": len(raw), "mode": stat.S_IMODE(path.stat().st_mode)}
    return result


def git_tree_digest(inventory):
    nested = {}
    for path, member in inventory.items():
        parts = Path(path).parts
        node = nested
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (b"100755" if member["mode"] & 0o111 else b"100644", member["git_blob"])
    def digest(node):
        entries = []
        for name, value in node.items():
            encoded = name.encode()
            if isinstance(value, dict):
                mode, checksum, key = b"40000", digest(value), encoded + b"/"
            else:
                mode, checksum = value
                key = encoded
            entries.append((key, mode + b" " + encoded + b"\0" + bytes.fromhex(checksum)))
        raw = b"".join(value for _, value in sorted(entries))
        return hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    return digest(nested)


def read_source_index(path, root):
    raw = json.loads(protected_bytes(path))
    if raw.get("schema") == "rc6.complete-archive-source-pin.v1":
        require(type(raw.get("overlay_count")) is int and raw["overlay_count"] == 0,
                "WHOLE_SOURCE_WITHOUT_OVERLAYS_REQUIRED")
        require(type(raw.get("files")) is dict and type(raw.get("modes")) is dict
                and type(raw.get("blob_ids")) is dict and set(raw["files"]) == set(raw["modes"]) == set(raw["blob_ids"]),
                "WHOLE_SOURCE_INDEX_CONTRACT")
        require(all(value in {"100644", "100755"} for value in raw["modes"].values()),
                "WHOLE_SOURCE_MODES_MISMATCH")
        return {"source_sha": raw["source_sha"], "candidate_tree_sha": raw["source_tree"],
            "archive_sha256": raw["tar_sha256"], "source_file_hashes": raw["files"],
            "source_file_modes": {key: int(value[-3:], 8) for key, value in raw["modes"].items()},
            "source_blob_ids": raw["blob_ids"], "overlays": [], "extracted_root": str(root),
            "index_schema": raw["schema"], "raw_git_commit_sha256_declared": raw["raw_git_commit_sha256"]}
    return raw


def source_start(root, index_path=None):
    inventory = source_inventory(root)
    index = read_source_index(index_path, root) if index_path else None
    if index is not None:
        require(index.get("overlays") == [] and Path(index["extracted_root"]).resolve() == root,
                "WHOLE_SOURCE_WITHOUT_OVERLAYS_REQUIRED")
        require({key: value["sha256"] for key, value in inventory.items()} == index["source_file_hashes"],
                "WHOLE_SOURCE_HASHES_MISMATCH")
        require(all(type(index.get(key)) is str and re.fullmatch(r"[0-9a-f]{40}", index[key])
                    for key in ("source_sha", "candidate_tree_sha"))
                and git_tree_digest(inventory) == index["candidate_tree_sha"], "WHOLE_SOURCE_GIT_TREE_MISMATCH")
        modes = index.get("source_file_modes")
        if modes is not None:
            require({key: value["mode"] for key, value in inventory.items()} == modes, "WHOLE_SOURCE_MODES_MISMATCH")
        if "source_blob_ids" in index:
            require({key: value["git_blob"] for key, value in inventory.items()} == index["source_blob_ids"],
                    "WHOLE_SOURCE_GIT_TREE_MISMATCH")
    return inventory, index


def imported_source(root, inventory):
    closure, unexpected = [], []
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if path.is_relative_to(root):
            relative = str(path.relative_to(root))
            checksum = hashlib.sha256(protected_bytes(path)).hexdigest()
            closure.append({"module": name, "path": relative, "sha256": checksum,
                "matches_archived_blob": checksum == inventory.get(relative, {}).get("sha256")})
        elif str(path).startswith("/workspace/") and not path.is_relative_to(Path(sys.prefix).resolve()):
            unexpected.append(str(path))
    return closure, unexpected


def output_guard(output, database=None, root=None, source_root=ROOT, gate="OUTPUT_MUST_BE_NEW_AND_OUTSIDE_SOURCES"):
    resolved = output.resolve()
    require(not output.exists() and not any(member.is_symlink() for member in (output, *output.parents))
            and not resolved.is_relative_to(source_root), gate)
    if database is not None and root is not None:
        require(not any(member.is_symlink() for path in (database, root) for member in (path, *path.parents)),
                "SOURCE_ALIAS_FORBIDDEN")
        root, database = root.resolve(), database.resolve()
        require(not resolved.is_relative_to(root) and not resolved.is_relative_to(Path(str(root) + ".authority"))
                and resolved not in {Path(str(database) + suffix) for suffix in ("", "-wal", "-shm", "-journal")}, gate)


def source_sqlite_guard(database, connect, calls):
    members = [Path(str(database.resolve()) + suffix) for suffix in ("", "-wal", "-shm", "-journal")]
    def guarded(database_arg, *args, **kwargs):
        raw = os.fsdecode(database_arg)
        raw = unquote(urlsplit(raw).path) if raw.startswith("file:") else raw
        path = Path(raw).resolve() if raw != ":memory:" else None
        same = path in members if path is not None else False
        if path is not None and path.exists():
            same = same or any(member.exists() and path.samefile(member) for member in members)
        if same:
            calls.append("SOURCE_BLOCKED")
            raise GateFailure("SQLITE_OPENED_SOURCE")
        calls.append("PRIVATE_COPY_OR_MEMORY")
        return connect(database_arg, *args, **kwargs)
    return guarded


def parse_frame(raw):
    require(0 < len(raw) < MAX_FRAME and raw.endswith(b"\n"), "PRODUCT_IPC_FRAME_BUDGET_OR_FRAMING")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "PRODUCT_IPC_DUPLICATE_KEY")
            result[key] = value
        return result
    def no_constant(_value):
        raise GateFailure("PRODUCT_IPC_NONFINITE_JSON")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=no_constant)
    require(type(value) is dict and value.get("protocol") == PROTOCOL
            and type(value.get("id")) is int and value["id"] >= 0, "PRODUCT_IPC_PROTOCOL_OR_ID")
    return value


def frame(value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    require(len(raw) < MAX_FRAME, "PRODUCT_IPC_FRAME_BUDGET_OR_FRAMING")
    return raw


class ProductClient:
    """One child, no response cache, bounded frames and joined cleanup."""
    def __init__(self, executable, *, root=ROOT, index=None, diagnostic=False, python_version=None,
                 diagnostic_deadline=None):
        self.executable = Path(executable).absolute()
        self.root, self.index, self.diagnostic = root.resolve(), index, diagnostic
        self.python_version = python_version
        self.diagnostic_deadline = diagnostic_deadline
        self.sequence, self.pending, self.last_elapsed = 0, bytearray(), None
        self.last_frame_bytes, self.process, self.finish_receipt = 0, None, None
        self.stderr_bytes, self.stderr_hash = 0, hashlib.sha256()
        self.lock, self.stderr_stop = Lock(), Event()
        self.driver_guards, self.driver_network, self.driver_sqlite = ExitStack(), [], []

    def __enter__(self):
        self.source_before, self.source_index = source_start(self.root, self.index)
        self.driver_environment = environment_receipt(self.root)
        def no_network(*_args, **_kwargs):
            self.driver_network.append("BLOCKED")
            raise GateFailure("DRIVER_NETWORK_FORBIDDEN")
        def no_sqlite(*_args, **_kwargs):
            self.driver_sqlite.append("BLOCKED")
            raise GateFailure("DRIVER_SQLITE_FORBIDDEN")
        for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                            (socket.socket, "sendto"), (socket, "create_connection"), (socket, "getaddrinfo")):
            self.driver_guards.enter_context(patch.object(owner, name, no_network))
        self.driver_guards.enter_context(patch.object(sqlite3, "connect", no_sqlite))
        command = [str(self.executable), "-I", "-B", str(self.root / "tests/ci_rc6_browser_product.py"),
                   "--expected-python", str(self.executable)]
        if self.index:
            command.extend(("--index", str(self.index.absolute())))
        if self.diagnostic:
            command.append("--diagnostic")
            if self.diagnostic_deadline is not None:
                command.extend(("--diagnostic-deadline", str(self.diagnostic_deadline)))
        if self.python_version is not None:
            command.extend(("--expected-python-version", self.python_version))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        environment.pop("PYTHONPATH", None)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.root, env=environment, bufsize=0)
            self.stderr_thread = Thread(target=self._drain_stderr, name="rc6-browser-product-stderr")
            self.stderr_thread.start()
            ready = self._read(monotonic() + 20)
            require(ready["id"] == 0, "PRODUCT_IPC_STARTUP_ID")
            self._successful(ready)
            self.product_environment = ready["result"]["environment"]
            require(self.product_environment["role"] == "PRODUCT"
                    and self.product_environment["installed_count"] == 157,
                    "PRODUCT_LOCKED_ENVIRONMENT_REQUIRED")
        except BaseException:
            self.close()
            self.driver_guards.close()
            raise
        return self

    def _drain_stderr(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stderr, selectors.EVENT_READ)
            while True:
                if selector.select(.1):
                    raw = os.read(self.process.stderr.fileno(), 64 * 1024)
                    if not raw:
                        return
                    self.stderr_bytes += len(raw)
                    self.stderr_hash.update(raw)
                elif self.stderr_stop.is_set():
                    return

    def _read(self, deadline):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while True:
                newline = self.pending.find(b"\n")
                if newline >= 0:
                    raw = bytes(self.pending[:newline + 1])
                    del self.pending[:newline + 1]
                    self.last_frame_bytes = len(raw)
                    return parse_frame(raw)
                require(len(self.pending) < MAX_FRAME, "PRODUCT_IPC_FRAME_BUDGET_OR_FRAMING")
                remaining = deadline - monotonic()
                require(remaining > 0 and selector.select(remaining), "PRODUCT_IPC_DEADLINE")
                raw = os.read(self.process.stdout.fileno(), min(64 * 1024, MAX_FRAME - len(self.pending)))
                require(raw, "PRODUCT_IPC_PREMATURE_EOF")
                self.pending.extend(raw)

    def _successful(self, response):
        require(type(response.get("ok")) is bool, "PRODUCT_IPC_RESPONSE_CONTRACT")
        if not response["ok"]:
            failure = response.get("error", {})
            require(type(failure) is dict and type(failure.get("gate")) is str
                    and type(failure.get("details")) is dict and type(failure.get("error_class")) is str,
                    "PRODUCT_IPC_ERROR_CONTRACT")
            if failure["gate"] == "DIAGNOSTIC_WINDOW_ENDED":
                raise ProductDiagnosticWindowEnded()
            raise GateFailure(failure["gate"], failure.get("details"))
        require(type(response.get("result")) is dict, "PRODUCT_IPC_RESULT_CONTRACT")
        return response["result"]

    def request(self, operation, *, timeout=20, **payload):
        with self.lock:
            begin = perf_counter()
            self.sequence += 1
            request = frame({"protocol": PROTOCOL, "id": self.sequence, "op": operation, **payload})
            try:
                self.process.stdin.write(request)
                self.process.stdin.flush()
                response = self._read(monotonic() + timeout)
                require(response["id"] == self.sequence, "PRODUCT_IPC_RESPONSE_ID")
                return self._successful(response)
            except (BrokenPipeError, OSError) as error:
                raise GateFailure("PRODUCT_IPC_PROCESS_DIED") from error
            finally:
                self.last_elapsed = perf_counter() - begin

    def render(self, path, params):
        result = self.request("render", timeout=5, path=path, params=params)
        require(type(result.get("html")) is str and type(result.get("headers")) is dict
                and all(type(key) is str and type(value) is str for key, value in result["headers"].items())
                and result.get("path") == path and result.get("filters") == params
                and type(result.get("pointer")) is dict and type(result.get("source_cut")) is str,
                "PRODUCT_IPC_NATIVE_RENDER_CONTRACT")
        require(self.last_elapsed <= PRODUCT_REQUEST_SECONDS, "RENDER_EXCEEDS_REQUEST_BUDGET",
            {"path": path, "filters": params, "elapsed_seconds": self.last_elapsed,
             "response_json_bytes": self.last_frame_bytes, "html_bytes": result.get("html_bytes"),
             "native_elapsed_seconds": result.get("native_elapsed_seconds")})
        result["request_wall_seconds_including_ipc_html_json"] = self.last_elapsed
        result["response_json_bytes"] = self.last_frame_bytes
        return result

    def health(self):
        result = self.request("health", timeout=5)
        require(self.last_elapsed <= HEALTH_REQUEST_SECONDS, "HEALTH_EXCEEDS_REQUEST_BUDGET")
        return result

    def close(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        self.stderr_stop.set()
        if getattr(self, "stderr_thread", None) is not None and self.stderr_thread.ident is not None:
            self.stderr_thread.join()
        self.pending.clear()
        self.process.stdout.close()
        self.process.stderr.close()

    def __exit__(self, exc_type, exc, traceback):
        finish_error = None
        try:
            if self.process.poll() is None:
                self.finish_receipt = self.request("finish", timeout=20)
        except BaseException as error:
            finish_error = error
        finally:
            self.close()
            self.driver_guards.close()
            self.source_after = source_inventory(self.root)
        require(self.source_before == self.source_after, "WHOLE_SOURCE_HASHES_OR_MODES_CHANGED")
        if finish_error is not None and exc is None:
            raise finish_error
        if self.finish_receipt is not None:
            require(self.finish_receipt["source_proof_pass"], "PRODUCT_SOURCE_OR_CUSTODY_PROOF_FAILED")
            require(not self.driver_network and not self.driver_sqlite, "DRIVER_NETWORK_OR_SQLITE_ATTEMPTED")
        elif exc is None:
            raise GateFailure("PRODUCT_FINISH_PROOF_MISSING")
        return False

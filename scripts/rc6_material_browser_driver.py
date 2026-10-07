#!/usr/bin/env python3
"""Prepare an isolated RC6 DRIVER4 using hash-pinned official wheels.

Infrastructure only: never run the LARGE browser gate or import the product.
The caller owns source/PRODUCT157 custody and must provide the original
managed_native_child callback. Every stage closes that kernel barrier before
this helper reads stage payload. No system Chromium, executable/node override,
apt, product mutation, credentials, browser launch or live orders are used.
"""
from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import datetime
import email.parser
import hashlib
import importlib
from importlib import metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

PINS = (
    {"name": "playwright", "version": "1.62.0",
     "filename": "playwright-1.62.0-py3-none-manylinux1_x86_64.whl",
     "bytes": 47748926,
     "sha256": "ba33bae6a13b3d9d354c751cb618af357d20fe1d57767cbcce52079bbef17ad3",
     "metadata_sha256": "a1443e21066f73be70ae0f5c21f6c61a830776104052e281ef5559475377071e",
     "url": "https://files.pythonhosted.org/packages/43/6b/b24aebc2b04bffcb342bccf96e287c78b363e1615bed5cea97500cc0393a/playwright-1.62.0-py3-none-manylinux1_x86_64.whl",
     "active_requires_dist": ["pyee<14,>=13", "greenlet<4.0.0,>=3.1.1"]},
    {"name": "pyee", "version": "13.0.1",
     "filename": "pyee-13.0.1-py3-none-any.whl", "bytes": 15659,
     "sha256": "af2f8fede4171ef667dfded53f96e2ed0d6e6bd7ee3bb46437f77e3b57689228",
     "metadata_sha256": "7512d063d60cb405124a6c5fdd2c688d78a4c6b6841b8eeb00a59474ef59d43c",
     "url": "https://files.pythonhosted.org/packages/a0/c4/b4d4827c93ef43c01f599ef31453ccc1c132b353284fc6c87d535c233129/pyee-13.0.1-py3-none-any.whl",
     "active_requires_dist": ["typing-extensions"]},
    {"name": "greenlet", "version": "3.5.5",
     "filename": "greenlet-3.5.5-cp311-cp311-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl",
     "bytes": 624562,
     "sha256": "74cc6df89ec5302337adc9cf096221cbed2510fd444b0e0f1586cf0470740864",
     "metadata_sha256": "16de87de070796e2e1f18019f963c3ba5972bb9bfca1b856b83096bf73c61f38",
     "url": "https://files.pythonhosted.org/packages/51/2d/f2c928218ac52f26d7a2c188c171d1b7e728b23782cb3347e7b4fce1493a/greenlet-3.5.5-cp311-cp311-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl",
     "active_requires_dist": []},
    {"name": "typing-extensions", "version": "4.16.0",
     "filename": "typing_extensions-4.16.0-py3-none-any.whl", "bytes": 45571,
     "sha256": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
     "metadata_sha256": "b05084ca1d50879865178d9fff9fabeab61bdfb1f361bfbde95421ffc8f9be46",
     "url": "https://files.pythonhosted.org/packages/49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl",
     "active_requires_dist": []},
)
BROWSERS_JSON_SHA256 = "f306eed529599b1eaf2f8a85db9de2b23e1a3fe36c2b66434b7c9434fb627a99"
# Observed extracted ELF bytes from the prior closed official preparation;
# these are not claimed to be official hashes of deleted download ZIP archives.
BINARIES = {
    "chromium": ("chromium-1234/chrome-linux64/chrome", 290614600,
                 "0b20b130e7edd9dd51873be867761295fe0cfad490c2b9a64f95bd3cfc08fa71"),
    "chromium-headless-shell": ("chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell",
                              196975952, "e11fc9ce65c96313476f7ee9844b6fb6a9220fb048693cfe9eee00acf4170a9f"),
    "ffmpeg": ("ffmpeg-1011/ffmpeg-linux", 5101056,
               "460d44f3416005662f528d4b92e7b94ace924e8a0288106d3803b73c56eaadc8"),
}
FIELDS11 = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
FORBIDDEN_FS = {0x01021994, 0x858458F6}  # tmpfs / ramfs: infrastructure must be real disk too.


class DriverPreparationError(RuntimeError):
    def __init__(self, reason, receipt_path=None):
        super().__init__(reason)
        self.receipt_path = receipt_path


def require(condition, reason):
    if not condition:
        raise DriverPreparationError(reason)


def normalize(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def _stat(st):
    return {key: getattr(st, key) for key in FIELDS11}


def _regular(path):
    st = Path(path).lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.geteuid(),
            "OWNED_REGULAR_NO_ALIAS_FILE_REQUIRED")
    return st


def _directory(path, *, owned=False, private=False):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "ABSOLUTE_CANONICAL_PATH_REQUIRED")
    for part in (path, *path.parents):
        require(stat.S_ISDIR(part.lstat().st_mode), "DIRECTORY_SYMLINK_OR_ALIAS_FORBIDDEN")
    st = path.lstat()
    if owned:
        require(st.st_uid == os.geteuid(), "OWNED_DIRECTORY_REQUIRED")
    if private:
        require(stat.S_IMODE(st.st_mode) == 0o700, "PRIVATE_0700_DIRECTORY_REQUIRED")
    return st


def _read(path, *, cap=128 * 1024 * 1024):
    path = Path(path)
    before = _regular(path)
    require(before.st_size <= cap, "READ_CAPTURE_BOUND_EXCEEDED")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        require(_stat(os.fstat(descriptor)) == _stat(before), "INPUT_CHANGED_BEFORE_READ")
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, cap + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            require(size <= cap, "READ_CAPTURE_BOUND_EXCEEDED")
        require(_stat(os.fstat(descriptor)) == _stat(before), "INPUT_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    require(_stat(path.lstat()) == _stat(before) and size == before.st_size,
            "INPUT_CHANGED_AFTER_READ")
    return b"".join(chunks)


def _hash_file(path):
    path = Path(path)
    before = _regular(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    digest, size = hashlib.sha256(), 0
    try:
        require(_stat(os.fstat(descriptor)) == _stat(before), "HASH_INPUT_CHANGED_BEFORE_READ")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        require(_stat(os.fstat(descriptor)) == _stat(before), "HASH_INPUT_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    require(_stat(path.lstat()) == _stat(before) and size == before.st_size,
            "HASH_INPUT_CHANGED_AFTER_READ")
    return {"sha256": digest.hexdigest(), "bytes": size, "stat11": _stat(before)}


def _publish(path, value):
    path = Path(path)
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    except BaseException:
        raise
    return hashlib.sha256(raw).hexdigest()


def _disk_type(path):
    class StatFS(ctypes.Structure):
        _fields_ = [("f_type", ctypes.c_long), ("padding", ctypes.c_byte * 248)]
    observed = StatFS()
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.statfs(os.fsencode(path), ctypes.byref(observed)) == 0,
            "FILESYSTEM_TYPE_OBSERVATION_REQUIRED")
    fs_type = observed.f_type & 0xFFFFFFFF
    require(fs_type not in FORBIDDEN_FS, "TMPFS_RAMFS_DRIVER_NAMESPACE_FORBIDDEN")
    return fs_type


def _scrubbed_env(output):
    # No inherited token, cookie, operational, provider, proxy, node, executable,
    # download-host or Python-path variable can cross into preparation children.
    return {"PATH": os.defpath, "HOME": str(output / "home"),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "CI": "true",
            "TMPDIR": str(output / "tmp"), "RUNNER_TEMP": str(output / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "PIP_CONFIG_FILE": "/dev/null", "PIP_NO_CACHE_DIR": "1",
            "PLAYWRIGHT_BROWSERS_PATH": str(output / "browser-cache")}


def _kernel_closed(kernel, limit):
    if not isinstance(kernel, dict):
        return False
    pid = kernel.get("pid")
    positive = (type(pid) is int and pid > 0 and
                kernel.get("actual_child_reaped") is True and
                kernel.get("wait4_reaped_pid") == pid and
                kernel.get("kernel_pre_popen_echild_verified") is True and
                kernel.get("owned_children_exhaustion_verified") is True and
                kernel.get("process_group_absent_at_main_reap") is True and
                kernel.get("process_group_absent_after_reap") is True and
                kernel.get("subreaper_activation_readback_verified") is True and
                kernel.get("subreaper_restore_attempted") is True and
                kernel.get("subreaper_restoration_readback_verified") is True and
                kernel.get("remaining_owned_children") == [] and
                kernel.get("owned_cleanup_management_bound_seconds") == 5 and
                kernel.get("launcher_management_deadline_seconds") == limit)
    negatives = (kernel.get("timed_out") is False and
                 kernel.get("late_observed_main_reap_irreversible_red") is False and
                 kernel.get("kernel_wait4_zero_observed_irreversible_red") is False and
                 kernel.get("residual_descendants_observed") == [] and
                 kernel.get("owned_group_signal_observations") == [] and
                 kernel.get("supervisor_errors") == [])
    cleanup = kernel.get("termination_reap_restore_cleanup_seconds")
    reap = kernel.get("actual_launch_to_pid_reap_wall_seconds")
    observations = kernel.get("kernel_owned_child_observations")
    descendants = kernel.get("adopted_descendants_reaped")
    return bool(positive and negatives and type(cleanup) in (int, float) and
                0 <= cleanup <= 5 and type(reap) in (int, float) and 0 <= reap <= limit and
                isinstance(observations, list) and
                any(x.get("phase") == "before_popen_after_subreaper_activation" and
                    x.get("outcome") == "ECHILD" and x.get("errno") == 10 for x in observations) and
                any(x.get("phase") == "after_main_pid_reap" and x.get("outcome") == "ECHILD" and
                    x.get("errno") == 10 for x in observations) and
                isinstance(descendants, list) and all(x.get("exit_code") == 0 for x in descendants))


def _offline_audit():
    observed = {"inet_operation_attempts": [], "inet_constructor_requests": [],
                "subprocess_names": [], "witnessed": False}
    witness = object()

    def audit(event, args):
        if event == "rc6.driver.offline.witness" and args == (witness,):
            observed["witnessed"] = True
        if event == "socket.__new__" and args[1] in (socket.AF_INET, socket.AF_INET6):
            observed["inet_constructor_requests"].append(int(args[1]))
        if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr") or (
                event in ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg") and
                getattr(args[0], "family", None) in (socket.AF_INET, socket.AF_INET6)):
            observed["inet_operation_attempts"].append(event)
            raise DriverPreparationError("OFFLINE_VERIFICATION_INET_OPERATION_FORBIDDEN")
        if event == "subprocess.Popen":
            name = os.path.basename(os.fsdecode(args[0]))
            observed["subprocess_names"].append(name)
            require(name in ("node", "ldd"), "ONLY_NODE_LOOKUP_AND_LDD_PREPARATION_ALLOWED")
    sys.addaudithook(audit)
    sys.audit("rc6.driver.offline.witness", witness)
    require(observed["witnessed"], "PYTHON_OFFLINE_AUDIT_WITNESS_REQUIRED")
    return observed


def _bootstrap(config):
    require(platform.python_version() == "3.11.16" and platform.python_implementation() == "CPython"
            and sys.platform == "linux" and platform.machine() == "x86_64",
            "EXACT_NATIVE_CPYTHON_31116_LINUX_X86_64_REQUIRED")
    require(os.path.abspath(sys.executable) == config["python311"] and
            Path(sys.prefix).is_absolute() and sys.prefix != sys.base_prefix,
            "NORMAL_PRODUCT157_PREFIX_EXECUTABLE_REQUIRED")
    require(not Path(sys.prefix).is_relative_to(Path(config["source_root"])),
            "PRODUCT157_MUST_BE_EXTERNAL_TO_SOURCE")
    installed = {}
    for dist in metadata.distributions():
        name = normalize(dist.metadata["Name"])
        require(name not in installed, "DUPLICATE_PRODUCT_METADATA_FORBIDDEN")
        installed[name] = dist.version
    require(len(installed) == 157, "EXACT_PRODUCT157_METADATA_COUNT_REQUIRED")
    return {"status": "PREPARATION_ONLY", "python": platform.python_version(),
            "sys_executable": sys.executable, "sys_prefix": sys.prefix,
            "sys_base_prefix": sys.base_prefix, "installed": installed,
            "metadata_count": 157, "product_modules_imported": False,
            "product_record_custody_is_callers_separate_required_proof": True}


def _wheel_metadata(raw, pin):
    require(hashlib.sha256(raw).hexdigest() == pin["metadata_sha256"],
            "OFFICIAL_WHEEL_METADATA_HASH_MISMATCH")
    msg = email.parser.BytesParser().parsebytes(raw)
    require(normalize(msg["Name"]) == pin["name"] and msg["Version"] == pin["version"],
            "OFFICIAL_WHEEL_METADATA_IDENTITY_MISMATCH")
    active = []
    for requirement in msg.get_all("Requires-Dist", []):
        if ";" not in requirement:
            active.append(requirement)
        else:
            # The complete metadata bytes are pinned. Its only conditional
            # dependencies belong to extras explicitly not requested by DRIVER4.
            require(re.search(r'extra\s*==\s*"(?:dev|docs|test)"', requirement) is not None,
                    "UNEXPECTED_ACTIVE_DEPENDENCY_MARKER")
    require(active == pin["active_requires_dist"], "PINNED_ACTIVE_DRIVER_CLOSURE_MISMATCH")
    return {"name": pin["name"], "version": pin["version"],
            "metadata_sha256": pin["metadata_sha256"], "active_requires_dist": active,
            "requires_python": msg["Requires-Python"]}


def _download(config):
    output = Path(config["output_root"])
    rows = []
    for pin in PINS:
        path = output / "wheelhouse" / pin["filename"]
        digest, size = hashlib.sha256(), 0
        # One request per pinned wheel, default certificate validation. No
        # credentials or proxy settings are inherited; no blind retry is made.
        request = urllib.request.Request(pin["url"], method="GET")
        with urllib.request.urlopen(request, timeout=45) as response:
            require(response.status == 200 and response.geturl() == pin["url"],
                    "OFFICIAL_PINNED_WHEEL_RESPONSE_REQUIRED")
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                    0o600), "wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= pin["bytes"], "OFFICIAL_WHEEL_SIZE_BOUND_EXCEEDED")
                    digest.update(chunk)
                    stream.write(chunk)
        require(size == pin["bytes"] and digest.hexdigest() == pin["sha256"],
                "OFFICIAL_WHEEL_DOWNLOAD_HASH_OR_SIZE_MISMATCH")
        # ZIP and metadata validation only; installation remains offline.
        with zipfile.ZipFile(io.BytesIO(_read(path))) as wheel:
            names = wheel.namelist()
            require(len(names) == len(set(names)) and
                    all(not x.startswith("/") and ".." not in Path(x).parts for x in names),
                    "OFFICIAL_WHEEL_PATH_ALIAS_FORBIDDEN")
            matches = [x for x in names if x.endswith(".dist-info/METADATA")]
            require(len(matches) == 1, "ONE_OFFICIAL_WHEEL_METADATA_REQUIRED")
            metarow = _wheel_metadata(wheel.read(matches[0]), pin)
            if pin["name"] == "playwright":
                browsers = wheel.read("playwright/driver/package/browsers.json")
                require(hashlib.sha256(browsers).hexdigest() == BROWSERS_JSON_SHA256,
                        "OFFICIAL_BROWSERS_JSON_HASH_MISMATCH")
        rows.append({**metarow, "filename": pin["filename"], "actual_download_sha256": digest.hexdigest(),
                     "actual_download_bytes": size, "official_url": pin["url"]})
    return {"status": "PREPARATION_ONLY", "wheels": rows, "download_attempts": 4,
            "active_closure": {"playwright": ["pyee", "greenlet"], "pyee": ["typing-extensions"],
                               "greenlet": [], "typing-extensions": []},
            "network_use": "OFFICIAL_HASH_PINNED_WHEEL_PREPARATION_ONLY"}


def _driver_record(config):
    output, driver = Path(config["output_root"]), Path(config["output_root"]) / "driver-env"
    require(platform.python_version() == "3.11.16" and os.path.abspath(sys.executable) == str(driver / "bin/python")
            and Path(sys.prefix) == driver and sys.prefix != sys.base_prefix,
            "NORMAL_EXACT_DRIVER311_PREFIX_REQUIRED")
    distributions, installed = {}, {}
    for dist in metadata.distributions():
        name = normalize(dist.metadata["Name"])
        require(name not in installed, "DUPLICATE_DRIVER_METADATA_FORBIDDEN")
        installed[name], distributions[name] = dist.version, dist
    require(installed == {x["name"]: x["version"] for x in PINS}, "EXACT_FOUR_DRIVER_DISTRIBUTIONS_REQUIRED")
    inventory, metarows = [], []
    for pin in PINS:
        dist = distributions[pin["name"]]
        distpath = Path(dist._path)
        require(distpath.is_relative_to(driver), "DRIVER_METADATA_ORIGIN_MISMATCH")
        _directory(distpath, owned=True)
        meta = _wheel_metadata(_read(distpath / "METADATA"), pin)
        record = _read(distpath / "RECORD")
        metarows.append({**meta, "dist_info": str(distpath), "record_sha256": hashlib.sha256(record).hexdigest()})
        for relative, encoded, size in csv.reader(io.StringIO(record.decode())):
            # RECORD script entries legitimately contain ../../../bin. Normalize
            # those components first, then reject every actual ancestor symlink
            # and any resulting path outside the owned DRIVER prefix.
            path = Path(os.path.abspath(dist.locate_file(relative)))
            require(path.is_relative_to(driver), "DRIVER_RECORD_OUTSIDE_PREFIX")
            _directory(path.parent, owned=True)
            row = _hash_file(path)
            if encoded:
                algorithm, expected = encoded.split("=", 1)
                actual = base64.urlsafe_b64encode(bytes.fromhex(row["sha256"])).decode().rstrip("=")
                require(algorithm == "sha256" and actual == expected and row["bytes"] == int(size),
                        "INSTALLED_DRIVER_RECORD_BYTES_MISMATCH")
            else:
                require(path == distpath / "RECORD" and size == "", "ONLY_RECORD_SELF_MAY_BE_HASHLESS")
            inventory.append({**row, "distribution": pin["name"], "path": str(path),
                              "relative_record_path": relative, "record_hash_verified": bool(encoded)})
    require(len({x["path"] for x in inventory}) == len(inventory), "DUPLICATE_DRIVER_RECORD_PATH_FORBIDDEN")
    return installed, metarows, inventory


def _driver_verify(config):
    observed = _offline_audit()
    output = Path(config["output_root"])
    installed, metarows, inventory = _driver_record(config)
    bypath = {x["path"]: x for x in inventory}
    module_rows = []
    for name in ("playwright", "pyee", "greenlet", "typing_extensions", "greenlet._greenlet"):
        module = importlib.import_module(name)
        path = Path(module.__file__)
        require(str(path) in bypath and _hash_file(path)["sha256"] == bypath[str(path)]["sha256"],
                "NORMAL_DRIVER_MODULE_ORIGIN_RECORD_MISMATCH")
        module_rows.append({"module": name, "path": str(path), "sha256": bypath[str(path)]["sha256"]})
    from playwright._impl._driver import compute_driver_executable
    node, cli = map(Path, compute_driver_executable())
    require(node == output / "driver-env/lib/python3.11/site-packages/playwright/driver/node"
            and str(node) in bypath and str(cli) in bypath, "NORMAL_PACKAGED_NODE_CLI_BINDING_REQUIRED")
    browsers = _read(cli.parent / "browsers.json")
    require(hashlib.sha256(browsers).hexdigest() == BROWSERS_JSON_SHA256,
            "INSTALLED_BROWSERS_JSON_HASH_MISMATCH")
    selection = {x["name"]: x for x in json.loads(browsers)["browsers"]
                 if x["name"] in ("chromium", "chromium-headless-shell", "ffmpeg")}
    require(set(selection) == {"chromium", "chromium-headless-shell", "ffmpeg"} and
            all(selection[x]["revision"] == "1234" and selection[x]["browserVersion"] == "151.0.7922.34"
                for x in ("chromium", "chromium-headless-shell")) and selection["ffmpeg"]["revision"] == "1011",
            "ORIGINAL_BROWSER_REVISIONS_REQUIRED")
    package = json.loads(_read(cli.parent / "package.json"))
    require(package["version"] == "1.62.0", "ACTUAL_PLAYWRIGHT_CORE_VERSION_MISMATCH")
    node_process = subprocess.run([str(node), "--version"], cwd=output, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
    require(node_process.returncode == 0 and node_process.stdout.strip() == b"v24.18.1",
            "ACTUAL_PINNED_NODE_VERSION_REQUIRED")
    require(not observed["inet_operation_attempts"], "DRIVER_VERIFICATION_NETWORK_ATTEMPTED")
    record_sha = _publish(output / "receipts/driver-record-inventory.json", inventory)
    return {"status": "PREPARATION_ONLY", "installed": installed, "installed_metadata": metarows,
            "module_origins": module_rows, "record_entries": len(inventory), "record_inventory_sha256": record_sha,
            "normal_node": {"path": str(node), **bypath[str(node)], "version": "v24.18.1"},
            "normal_cli": {"path": str(cli), "sha256": bypath[str(cli)]["sha256"]},
            "browsers_json_sha256": BROWSERS_JSON_SHA256, "payloads": selection,
            "network_audit": observed, "network_audit_scope": "THIS_PYTHON_PROCESS_AFTER_WITNESS; NOT_KERNEL_OR_BINARY_TRANSITIVE",
            "no_browser_launch_called": True, "product_source_imports": False}


def _sanitize(line):
    def url(match):
        split = urllib.parse.urlsplit(match.group(0))
        host = split.hostname or "REDACTED_HOST"
        port = ":" + str(split.port) if split.port else ""
        return urllib.parse.urlunsplit((split.scheme, host + port, split.path, "", "")) + (
            "[URL_COMPONENTS_REDACTED]" if split.query or split.fragment or split.username or split.password else "")
    line = re.sub(r"https?://[^\s<>\"']+", url, line)
    line = re.sub(r"(?i)(authorization|proxy-authorization|cookie|set-cookie|token|secret|password)\s*[:=].*",
                  "[SENSITIVE_FIELD_REDACTED]", line)
    if re.match(r"^\s*[A-Za-z][A-Za-z0-9-]*:\s", line) and not line.lstrip().startswith(("Error:", "Warning:", "Note:")):
        return "[HEADER_LINE_REDACTED]\n"
    return line


def _browser_install(config):
    output = Path(config["output_root"])
    command = [str(output / "driver-env/bin/python"), "-I", "-B", "-m", "playwright", "install", "chromium"]
    process = subprocess.Popen(command, cwd=output, env=_scrubbed_env(output), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    aborted, total, signals = False, 0, []
    descriptor = os.open(output / "logs/browser-install-sanitized.log",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as log:
        while True:
            line = process.stdout.readline(65537)
            if not line:
                break
            require(len(line) <= 65536, "INSTALL_LOG_LINE_BOUND_EXCEEDED")
            sanitized = _sanitize(line.decode("utf-8", errors="replace")).encode()
            total += len(sanitized)
            require(total <= 8 * 1024 * 1024, "SANITIZED_INSTALL_LOG_BOUND_EXCEEDED")
            log.write(sanitized)
            log.flush()
            # The CLI has its own CDN fallbacks. Preserve the first failure and
            # stop this exact child, rather than silently turning retries green.
            lower = line.lower()
            if not aborted and any(x in lower for x in (b"error: download", b"failed to download", b"certificate", b"timed out", b"econnreset", b"enotfound")):
                aborted = True
                try:
                    process.send_signal(2)
                    signals.append({"pid": process.pid, "signal": 2, "outcome": "SENT"})
                except ProcessLookupError:
                    signals.append({"pid": process.pid, "signal": 2, "outcome": "ESRCH"})
    returncode = process.wait()
    require(returncode == 0 and not aborted, "OFFICIAL_BROWSER_INSTALL_FAILED_NO_BLIND_RETRY")
    return {"status": "PREPARATION_ONLY", "official_cli_returncode": returncode,
            "original_command": command, "browser_install_network_declared": True,
            "download_host_override": False, "first_download_error_observed": aborted,
            "owned_cli_signal_observations": signals, "sanitized_log_bytes": total,
            "raw_download_archives_retained": False, "raw_download_archive_digests_not_claimed": True}


def _browser_verify(config):
    output, cache = Path(config["output_root"]), Path(config["output_root"]) / "browser-cache"
    observed = _offline_audit()
    from playwright.sync_api import sync_playwright
    # Query the normal packaged Node over its own pipe. No browser is launched.
    with sync_playwright() as playwright:
        normal_lookup = Path(playwright.chromium.executable_path)
    require(normal_lookup == cache / BINARIES["chromium"][0], "NORMAL_CHROMIUM_CACHE_LOOKUP_REQUIRED")
    require({p.name for p in cache.iterdir() if stat.S_ISDIR(p.lstat().st_mode)} ==
            {".links", "chromium-1234", "chromium_headless_shell-1234", "ffmpeg-1011"},
            "ONLY_PINNED_BROWSER_CACHE_DIRECTORIES_ALLOWED")
    files, directories, total = {}, {}, 0
    for path in sorted(cache.rglob("*")):
        st = path.lstat()
        require(st.st_uid == os.geteuid() and st.st_dev == cache.lstat().st_dev,
                "BROWSER_CACHE_OWNERSHIP_OR_DEVICE_MISMATCH")
        relative = str(path.relative_to(cache))
        if stat.S_ISDIR(st.st_mode):
            directories[relative] = _stat(st)
        else:
            row = _hash_file(path)
            files[relative] = row
            total += row["bytes"]
    inventory_sha = _publish(output / "receipts/browser-cache-inventory.json",
                             {"files": files, "directories": directories, "total_bytes": total})
    bindings = []
    for role, (relative, size, expected_hash) in BINARIES.items():
        path, row = cache / relative, files.get(relative)
        require(row is not None and row["bytes"] == size and row["sha256"] == expected_hash
                and stat.S_IMODE(path.lstat().st_mode) == 0o755,
                "ACTUAL_EXTRACTED_BROWSER_BINARY_BYTES_OR_MODE_MISMATCH")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
        try:
            require(os.read(descriptor, 4) == b"\x7fELF", "ELF_BROWSER_ASSET_REQUIRED")
        finally:
            os.close(descriptor)
        linked = subprocess.run(["/usr/bin/ldd", str(path)], cwd=output, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False)
        require(len(linked.stdout) <= 128 * 1024, "LDD_LOG_BOUND_EXCEEDED")
        text = linked.stdout.decode("utf-8", errors="replace")
        static_binary = "not a dynamic executable" in text or "statically linked" in text
        require((linked.returncode == 0 or static_binary) and "not found" not in text,
                "MISSING_HOST_LIBRARY_NO_APT_OR_SYSTEM_OVERRIDE")
        log = output / "logs" / ("ldd-" + role + ".log")
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(linked.stdout)
        bindings.append({"role": role, "path": str(path), "sha256": row["sha256"], "bytes": size,
                         "mode": path.lstat().st_mode, "ldd_returncode": linked.returncode,
                         "ldd_static": static_binary, "ldd_log_sha256": hashlib.sha256(linked.stdout).hexdigest(),
                         "download_zip_digest_not_claimed": True})
    # Installed wheel files must remain valid after the official browser install.
    installed, metarows, record = _driver_record(config)
    before = json.loads(_read(output / "receipts/driver-record-inventory.json"))
    strip_atime = lambda rows: [{**x, "stat11": {k: v for k, v in x["stat11"].items() if k != "st_atime_ns"}} for x in rows]
    require(strip_atime(record) == strip_atime(before), "DRIVER_RECORD_CHANGED_DURING_BROWSER_INSTALL")
    require(not observed["inet_operation_attempts"], "BROWSER_LOOKUP_INET_OPERATION_ATTEMPTED")
    return {"status": "PREPARATION_ONLY", "normal_full_chromium_lookup": str(normal_lookup),
            "normal_headless_shell_physical_binding": str(cache / BINARIES["chromium-headless-shell"][0]),
            "binaries": bindings, "cache_inventory_sha256": inventory_sha,
            "cache_files": len(files), "cache_directories": len(directories), "cache_bytes": total,
            "driver_record_entries_unchanged": len(record), "driver_record_atime_disclosed_separately": True,
            "driver_atime_before_after": [{"path": a["path"], "before": a["stat11"]["st_atime_ns"],
                                          "after": b["stat11"]["st_atime_ns"]} for a, b in zip(before, record)],
            "network_audit": observed, "network_audit_scope": "THIS_VERIFIER_PYTHON_AFTER_WITNESS; NOT_KERNEL_BINARY_OR_TRANSITIVE",
            "browser_gate_run": False, "browser_launch_called": False, "browser_runtime_pid_bound": False,
            "ldd_is_not_a_binary_runtime_capability_proof": True}


def _worker(stage, config_path):
    config = json.loads(_read(Path(config_path)))
    output = Path(config["output_root"])
    _directory(output, owned=True, private=True)
    functions = {"bootstrap-before": _bootstrap, "bootstrap-after": _bootstrap,
                 "download-wheels": _download, "verify-driver": _driver_verify,
                 "install-browser": _browser_install, "verify-browser": _browser_verify}
    require(stage in functions, "UNKNOWN_DRIVER_PREPARATION_STAGE")
    try:
        result = functions[stage](config)
        result.update({"stage": stage, "gate_qualified": False, "browser_gate_run": False,
                       "product_modules_imported": False, "real_orders_sent": 0})
        sha = _publish(output / "receipts" / (stage + ".json"), result)
        print(json.dumps({"stage": stage, "status": "PREPARATION_ONLY", "sha256": sha}), flush=True)
        return 0
    except BaseException as error:
        # Do not serialize exception text, headers, environment, signed URLs,
        # cookies or credentials into native logs or receipts.
        _publish(output / "receipts" / (stage + "-red.json"),
                 {"stage": stage, "status": "RED", "exception_class": type(error).__name__,
                  "reason": str(error) if isinstance(error, DriverPreparationError) else "NATIVE_PREPARATION_FAILURE",
                  "gate_qualified": False, "browser_gate_run": False, "real_orders_sent": 0})
        print(json.dumps({"stage": stage, "status": "RED", "exception_class": type(error).__name__}), flush=True)
        return 1


def prepare(*, source_root: Path, output_root: Path, python311: Path, owned_run):
    """Return DRIVER paths and an immutable receipt after all original FINs.

    owned_run(argv, *, cwd, label, limit, env) must return the original
    managed_native_child kernel55325 in {returncode, kernel, log_path}.
    The caller separately pins Git Source and PRODUCT157 RECORD before/after.
    Installation is infrastructure, not any material acceptance gate.
    """
    source, output, python311 = Path(source_root), Path(output_root), Path(python311)
    source_stat = _directory(source, owned=True)
    require(output.is_absolute() and not output.is_relative_to(source) and not source.is_relative_to(output),
            "DRIVER_NAMESPACE_MUST_BE_EXTERNAL_TO_SOURCE")
    parent_stat = _directory(output.parent, owned=True)
    require(parent_stat.st_dev == source_stat.st_dev, "ORIGINAL_SAME_FILESYSTEM_DRIVER_REQUIRED")
    fs_type = _disk_type(output.parent)
    require(python311.is_absolute() and python311.name == "python", "EXPLICIT_PRODUCT311_EXECUTABLE_REQUIRED")
    require(not python311.is_relative_to(source) and not python311.is_relative_to(output),
            "BOOTSTRAP_PRODUCT157_MUST_REMAIN_EXTERNAL")
    output.mkdir(mode=0o700)  # No exist_ok: a previous run can never be overwritten.
    output_stat = _directory(output, owned=True, private=True)
    require(output_stat.st_dev == source_stat.st_dev, "DRIVER_DEVICE_CHANGED")
    for name in ("tmp", "home", "wheelhouse", "browser-cache", "logs", "receipts"):
        (output / name).mkdir(mode=0o700)
    config = {"source_root": str(source), "output_root": str(output), "python311": str(python311)}
    _publish(output / "config.json", config)
    lock = "--only-binary=:all:\n" + "".join(x["name"] + "==" + x["version"] + " --hash=sha256:" +
                                              x["sha256"] + "\n" for x in PINS)
    lock_path = output / "driver4.lock.txt"
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(lock.encode())
    helper, stages, child_payload = Path(__file__).absolute(), [], {}
    env = _scrubbed_env(output)
    driver_python = output / "driver-env/bin/python"

    def run(label, argv, limit=180):
        observed = owned_run(argv, cwd=output, label="driver-" + label, limit=limit, env=env)
        require(isinstance(observed, dict), "ORIGINAL_OWNED_RUN_RESULT_REQUIRED")
        kernel = observed.get("kernel")
        closed = _kernel_closed(kernel, limit)
        control = {"stage": label, "kernel": kernel, "postread_barrier_closed": closed,
                   "returncode": observed.get("returncode"), "log_path": str(observed.get("log_path", "")),
                   "gate_qualified": False, "browser_gate_run": False}
        _publish(output / "receipts" / (label + "-kernel.json"), control)
        stages.append(control)
        # In particular, no Source, logs, RECORD, output or stage JSON reads
        # follow missing/RED physical controls, even if returncode happens to 0.
        require(closed, "DRIVER_STAGE_PHYSICAL_FIN_UNKNOWN_OR_RED")
        require(observed.get("returncode") == 0 and kernel.get("returncode") == 0,
                "DRIVER_PREPARATION_STAGE_RED_NO_RETRY")

    def worker(label, interpreter, limit=180):
        run(label, [str(interpreter), "-I", "-B", str(helper), "--worker-stage", label,
                    "--config", str(output / "config.json")], limit)
        payload = json.loads(_read(output / "receipts" / (label + ".json")))
        require(payload.get("status") == "PREPARATION_ONLY" and payload.get("stage") == label
                and payload.get("gate_qualified") is False, "TRUTHFUL_PREPARATION_RECEIPT_REQUIRED")
        child_payload[label] = payload

    receipt_path = output / "driver-preparation.json"
    try:
        worker("bootstrap-before", python311)
        worker("download-wheels", python311, 600)
        run("venv", [str(python311), "-I", "-B", "-m", "venv", "--without-pip", str(output / "driver-env")])
        run("offline-install", [str(python311), "-I", "-B", "-m", "pip", "--isolated",
            "--disable-pip-version-check", "--python", str(driver_python), "install", "--no-index",
            "--find-links", str(output / "wheelhouse"), "--no-deps", "--require-hashes", "--no-compile",
            "--no-cache-dir", "--report", str(output / "receipts/pip-install-report.json"), "-r", str(lock_path)])
        worker("verify-driver", driver_python)
        worker("install-browser", driver_python, 1800)
        worker("verify-browser", driver_python, 300)
        worker("bootstrap-after", python311)
        require(child_payload["bootstrap-before"] == {**child_payload["bootstrap-after"], "stage": "bootstrap-before"},
                "PRODUCT157_METADATA_CHANGED_DURING_DRIVER_PREPARATION")
        receipt = {"status": "DRIVER_ASSETS_PREPARED_ONLY", "gate_qualified": False,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "driver_python": str(driver_python), "browsers_path": str(output / "browser-cache"),
            "mode": "PRODUCTION_PAPER / SIMULATION", "real_orders_sent": 0, "PPI_Watch_touched": False,
            "source_product_imports": False, "source_whole_physical_namespace_proof_owned_by_caller": True,
            "product157_record_custody_owned_by_caller": True, "product157_metadata_before_after_equal": True,
            "namespace": {"path": str(output), "stat11": _stat(output_stat), "filesystem_type": fs_type,
                          "same_device_as_source": True},
            "stages": stages, "payloads": child_payload,
            "driver4_lock_sha256": hashlib.sha256(lock.encode()).hexdigest(),
            "network_preparation": "DECLARED_OFFICIAL_DOWNLOADS_ONLY; NOT_A_NATIVE_GATE_INET_EXEMPTION",
            "browser_launch_called": False, "browser_gate_run": False, "runtime_pid_bound": False,
            "deleted_browser_download_archive_hashes_not_claimed": True,
            "no_binary_kernel_or_transitive_network_isolation_claim": True}
        manifest_sha = _publish(receipt_path, receipt)
        return {"receipt_path": receipt_path, "driver_python": driver_python,
                "browsers_path": output / "browser-cache", "manifest_sha256": manifest_sha,
                "driver_record_inventory_sha256": child_payload["verify-driver"]["record_inventory_sha256"],
                "browser_cache_inventory_sha256": child_payload["verify-browser"]["cache_inventory_sha256"]}
    except BaseException as error:
        _publish(receipt_path, {"status": "RED_DRIVER_PREPARATION", "exception_class": type(error).__name__,
                  "reason": str(error) if isinstance(error, DriverPreparationError) else "NATIVE_PREPARATION_FAILURE",
                  "stages": stages, "gate_qualified": False, "browser_gate_run": False,
                  "live_or_unknown_stage_payload_read": False, "blind_retry": False, "real_orders_sent": 0})
        raise DriverPreparationError("DRIVER_PREPARATION_RED", receipt_path) from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-stage", required=True,
                        choices=("bootstrap-before", "bootstrap-after", "download-wheels", "verify-driver",
                                 "install-browser", "verify-browser"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    return _worker(args.worker_stage, args.config)


if __name__ == "__main__":
    raise SystemExit(main())

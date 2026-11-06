#!/usr/bin/env python3
"""R1 host-only scanner triage: metadata and bounded marker counts, zero provider GET.

Streamed via SSH stdin by a main-branch diagnostic Action. The host never
receives repository writes and the runner receives only a small, strictly
validated enum/count receipt. Docker inspect/logs are observation, not a
runtime policy or PPI quote attestation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time


SCHEMA = "rc6.r1.ppi-scanner-host-metadata-ro.v1"
CONTAINER = "porota_production_observer"
SIDE_CAR = Path("/opt/porota-trading/data/paper_v17/artifacts/observer_v17.db/ppi-budget/global.sqlite")
SCANNER_RECEIPT = Path("/opt/porota-trading/data/market/rc6_ppi_scanner_primary_latest.json")
MARKERS = (
    "PPI_SCANNER_PRIMARY_RECEIPT",
    "PPI_SCANNER_PRIMARY_CAPTURE_ERROR",
    "PPI_SCANNER_PRIMARY_PUBLISH_ERROR",
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$")
FILE_STATES = {"ABSENT", "REGULAR_SINGLE_LINK", "UNSAFE_OR_ALIAS", "UNAVAILABLE"}
ENV_MODES = {"APPROVED", "SHADOW", "OFF", "INVALID", "UNSET"}
MOUNT_STATES = {"EXPECTED_BIND", "ABSENT", "OTHER", "UNAVAILABLE"}
SECRET_MOUNTS = {"EXACT_FILE_MOUNT", "GENERIC_DIRECTORY_MOUNT", "NO_MOUNT", "UNAVAILABLE"}
LOG_STATES = {"OBSERVED", "NO_VERIFICADO"}
AGE_STATES = {"LT_5M", "LT_1H", "OLDER_1H", "UNKNOWN"}
REASONS = {"OBSERVED_ONLY_NOT_PAPER_GO", "IMAGE_PIN_MISMATCH", "DOCKER_METADATA_UNAVAILABLE",
           "HOST_PROBE_UNAVAILABLE", "LOGS_UNAVAILABLE", "HOST_RECEIPT_INVALID"}
MAX_DOCKER_METADATA = 32 * 1024
MAX_DOCKER_LOGS = 256 * 1024
MAX_HOST_RECEIPT = 4096


def file_kind(path: Path) -> str:
    """Only lstat file and ancestor names; do not follow links or open SQLite."""
    try:
        for part in (path, *path.parents):
            if part.is_symlink():
                return "UNSAFE_OR_ALIAS"
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            return "REGULAR_SINGLE_LINK"
        return "UNSAFE_OR_ALIAS"
    except FileNotFoundError:
        return "ABSENT"
    except OSError:
        return "UNAVAILABLE"


def age_band(path: Path) -> str:
    if file_kind(path) != "REGULAR_SINGLE_LINK":
        return "UNKNOWN"
    try:
        age = time.time() - path.lstat().st_mtime
    except OSError:
        return "UNKNOWN"
    if age < 0:
        return "UNKNOWN"
    return "LT_5M" if age < 300 else "LT_1H" if age < 3600 else "OLDER_1H"


def safe_env_policy(raw: str) -> dict:
    """Classify launch Config.Env values in memory, never emit env values/keys."""
    entries = json.loads(raw)
    if not isinstance(entries, list) or len(entries) > 512:
        raise ValueError("ENV_SHAPE")
    selected = {}
    for entry in entries:
        if not isinstance(entry, str) or len(entry) > 4096 or "=" not in entry:
            raise ValueError("ENV_ENTRY")
        key, value = entry.split("=", 1)
        if key not in ("POROTA_DYNAMIC_CAPACITY_MODE", "POROTA_CAPACITY_POLICY_PATH",
                       "PPI_PRODUCTION_SECRET_FILE"):
            continue
        if key in selected:
            raise ValueError("ENV_DUPLICATE")
        selected[key] = value
    mode = selected.get("POROTA_DYNAMIC_CAPACITY_MODE")
    launch_mode = ("UNSET" if mode is None else mode.upper()
                   if mode.upper() in ("APPROVED", "SHADOW", "OFF") else "INVALID")
    return {
        "launch_capacity_mode": launch_mode,
        "policy_path_selector": "OVERRIDE_PRESENT" if "POROTA_CAPACITY_POLICY_PATH" in selected else "DEFAULT",
        "ppi_secret_path_selector": "OVERRIDE_PRESENT" if "PPI_PRODUCTION_SECRET_FILE" in selected else "DEFAULT",
        # Docker Config.Env and PID1 initial env do not attest effective runtime memory.
        "effective_runtime_policy": "NO_VERIFICADO",
    }


def safe_mounts(raw: str) -> dict:
    mounts = json.loads(raw)
    if not isinstance(mounts, list) or len(mounts) > 100:
        raise ValueError("MOUNTS_SHAPE")
    has_expected_root = False
    data_alias_or_conflict = False
    seen_data_destinations: set[str] = set()
    secret = "NO_MOUNT"
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError("MOUNT_ENTRY")
        dest = mount.get("Destination")
        source = mount.get("Source")
        kind = mount.get("Type")
        if not isinstance(dest, str) or not isinstance(source, str) or not isinstance(kind, str):
            raise ValueError("MOUNT_FIELDS")
        if len(dest) > 1024 or len(source) > 1024:
            raise ValueError("MOUNT_BOUND")
        if dest in ("/app/data", "/app/data/market"):
            if dest in seen_data_destinations:
                data_alias_or_conflict = True
            seen_data_destinations.add(dest)
        if dest == "/app/data":
            # The exact root is an ownership hint, never permission to mutate it.
            # Duplicate/misordered mounts must not override a conflicting role.
            if kind == "bind" and source == "/opt/porota-trading/data":
                has_expected_root = True
            else:
                data_alias_or_conflict = True
        elif dest == "/app/data/market":
            if kind != "bind" or source != "/opt/porota-trading/data/market":
                data_alias_or_conflict = True
        if dest == "/run/secrets/ppi_production.json":
            secret = "EXACT_FILE_MOUNT"
        elif dest == "/run/secrets" and secret != "EXACT_FILE_MOUNT":
            secret = "GENERIC_DIRECTORY_MOUNT"
    data = "OTHER" if data_alias_or_conflict else "EXPECTED_BIND" if has_expected_root else "ABSENT"
    return {"runtime_data_mount": data, "ppi_secret_mount": secret}


def marker_counts(raw: bytes) -> dict:
    """Count only three exact tokens; never parse, preserve or emit payloads."""
    if len(raw) > MAX_DOCKER_LOGS or b"\x00" in raw:
        raise ValueError("LOGS_BOUND")
    result = dict.fromkeys(MARKERS, 0)
    for line in raw.decode("utf-8", errors="replace").splitlines():
        for marker in MARKERS:
            if re.search(r"(?:^|\s)" + marker + r"=", line):
                result[marker] += 1
                break
    return result


def _docker(*args: str, timeout: int = 8, max_bytes: int = MAX_DOCKER_METADATA,
            include_stderr: bool = False) -> bytes:
    # Drain both pipes incrementally; subprocess.run(capture_output=True) can
    # consume unbounded memory *before* a post-hoc length check on Docker logs.
    child = subprocess.Popen(("docker", *args), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(child.stdout, selectors.EVENT_READ, stdout)
            sel.register(child.stderr, selectors.EVENT_READ, stderr)
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("DOCKER_TIMEOUT")
                for ready, _ in sel.select(remaining):
                    chunk = os.read(ready.fileobj.fileno(), 8192)
                    if not chunk:
                        sel.unregister(ready.fileobj)
                        continue
                    ready.data.extend(chunk)
                    if len(stdout) + len(stderr) > max_bytes:
                        raise ValueError("DOCKER_BOUND")
        if child.wait(timeout=max(0, deadline - time.monotonic())) != 0:
            raise ValueError("DOCKER_EXIT")
        if stderr and not include_stderr:
            raise ValueError("DOCKER_STDERR")
        return bytes(stdout + (stderr if include_stderr else b""))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


def default_receipt(expected_product_sha: str, expected_image_id: str) -> dict:
    return {
        "schema": SCHEMA, "status": "NO_VERIFICADO", "reason": "HOST_PROBE_UNAVAILABLE",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_product_sha": expected_product_sha, "expected_image_id": expected_image_id,
        # Expected SHA is *input*, not a runtime SHA observation. Docker image
        # identity is independently observable, but its source provenance is not.
        "product_runtime_sha_attested": False,
        "image_pin_verified": False, "container_running": False,
        "launch_capacity_mode": "UNSET", "policy_path_selector": "DEFAULT",
        "ppi_secret_path_selector": "DEFAULT", "effective_runtime_policy": "NO_VERIFICADO",
        "runtime_data_mount": "UNAVAILABLE", "ppi_secret_mount": "UNAVAILABLE",
        "budget_sidecar_kind": "UNAVAILABLE", "scanner_receipt_kind": "UNAVAILABLE",
        "budget_sidecar_age_band": "UNKNOWN", "scanner_receipt_age_band": "UNKNOWN",
        "log_marker_state": "NO_VERIFICADO", "marker_counts": dict.fromkeys(MARKERS, 0),
        "source_freshness_attested": False, "paper_ready": False,
        "probe_provider_gets": 0, "probe_orders_sent": 0,
        "probe_trading_db_opened": False,
        "probe_ppi_watch_mutations": 0, "probe_docker_mutations": 0,
    }


def host_probe(expected_product_sha: str, expected_image_id: str) -> dict:
    if not SHA40.fullmatch(expected_product_sha) or not IMAGE_ID.fullmatch(expected_image_id):
        raise ValueError("PIN_INVALID")
    receipt = default_receipt(expected_product_sha, expected_image_id)
    try:
        image = _docker("inspect", "--format", "{{.Image}}", CONTAINER).decode().strip()
        running = _docker("inspect", "--format", "{{.State.Running}}", CONTAINER).decode().strip()
        if image != expected_image_id or running != "true":
            receipt["reason"] = "IMAGE_PIN_MISMATCH"
            return receipt
        receipt["image_pin_verified"] = True
        receipt["container_running"] = True
        env = _docker("inspect", "--format", "{{json .Config.Env}}", CONTAINER).decode()
        mounts = _docker("inspect", "--format", "{{json .Mounts}}", CONTAINER).decode()
        receipt.update(safe_env_policy(env))
        receipt.update(safe_mounts(mounts))
        receipt["budget_sidecar_kind"] = file_kind(SIDE_CAR)
        receipt["scanner_receipt_kind"] = file_kind(SCANNER_RECEIPT)
        receipt["budget_sidecar_age_band"] = age_band(SIDE_CAR)
        receipt["scanner_receipt_age_band"] = age_band(SCANNER_RECEIPT)
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        receipt["reason"] = "DOCKER_METADATA_UNAVAILABLE"
        return receipt
    try:
        # No in-container invocation; logs stay only in bounded host memory.
        logs = _docker("logs", "--since", "45m", "--tail", "600", "--timestamps",
                       CONTAINER, timeout=12, max_bytes=MAX_DOCKER_LOGS,
                       include_stderr=True)
        receipt["marker_counts"] = marker_counts(logs)
        receipt["log_marker_state"] = "OBSERVED"
        receipt["status"] = "OBSERVED_ONLY"
        receipt["reason"] = "OBSERVED_ONLY_NOT_PAPER_GO"
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        receipt["reason"] = "LOGS_UNAVAILABLE"
    return receipt


def validate_receipt(obj: dict, expected_product_sha: str, expected_image_id: str) -> dict:
    template = default_receipt(expected_product_sha, expected_image_id)
    if not isinstance(obj, dict) or set(obj) != set(template):
        raise ValueError("RECEIPT_KEYS")
    if obj["schema"] != SCHEMA or obj["expected_product_sha"] != expected_product_sha or obj[
        "expected_image_id"] != expected_image_id:
        raise ValueError("RECEIPT_PINS")
    if obj["status"] not in ("OBSERVED_ONLY", "NO_VERIFICADO") or obj["reason"] not in REASONS:
        raise ValueError("RECEIPT_STATUS")
    if not isinstance(obj["observed_at_utc"], str) or not UTC_STAMP.fullmatch(obj["observed_at_utc"]):
        raise ValueError("RECEIPT_TIME")
    try:
        observed = datetime.fromisoformat(obj["observed_at_utc"].replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - observed).total_seconds()
    except (ValueError, OverflowError):
        raise ValueError("RECEIPT_TIME") from None
    # Host observation time, NOT market-data freshness. Reject false time claims.
    if not -120 <= age_seconds <= 900:
        raise ValueError("RECEIPT_TIME_WINDOW")
    for key in ("product_runtime_sha_attested", "image_pin_verified", "container_running",
                "source_freshness_attested", "paper_ready", "probe_trading_db_opened"):
        if type(obj[key]) is not bool:
            raise ValueError("RECEIPT_BOOL")
    if (obj["product_runtime_sha_attested"] or obj["source_freshness_attested"] or
            obj["paper_ready"] or obj["probe_trading_db_opened"]):
        raise ValueError("RECEIPT_UNSAFE")
    for key in ("probe_provider_gets", "probe_orders_sent", "probe_ppi_watch_mutations",
                "probe_docker_mutations"):
        if type(obj[key]) is not int or obj[key] != 0:
            raise ValueError("RECEIPT_ZERO")
    if obj["launch_capacity_mode"] not in ENV_MODES or obj["policy_path_selector"] not in (
        "DEFAULT", "OVERRIDE_PRESENT") or obj["ppi_secret_path_selector"] not in (
        "DEFAULT", "OVERRIDE_PRESENT") or obj["effective_runtime_policy"] != "NO_VERIFICADO":
        raise ValueError("RECEIPT_POLICY")
    if obj["runtime_data_mount"] not in MOUNT_STATES or obj["ppi_secret_mount"] not in SECRET_MOUNTS:
        raise ValueError("RECEIPT_MOUNT")
    if obj["budget_sidecar_kind"] not in FILE_STATES or obj["scanner_receipt_kind"] not in FILE_STATES:
        raise ValueError("RECEIPT_FILE")
    if obj["budget_sidecar_age_band"] not in AGE_STATES or obj["scanner_receipt_age_band"] not in AGE_STATES:
        raise ValueError("RECEIPT_AGE")
    if obj["log_marker_state"] not in LOG_STATES or not isinstance(obj["marker_counts"], dict) or set(
        obj["marker_counts"]) != set(MARKERS):
        raise ValueError("RECEIPT_MARKERS")
    for value in obj["marker_counts"].values():
        if type(value) is not int or not 0 <= value <= 600:
            raise ValueError("RECEIPT_COUNT")
    if obj["log_marker_state"] == "NO_VERIFICADO" and any(obj["marker_counts"].values()):
        raise ValueError("RECEIPT_UNVERIFIED_COUNT")
    if obj["status"] == "OBSERVED_ONLY" and not (obj["image_pin_verified"] and obj[
        "container_running"] and obj["log_marker_state"] == "OBSERVED" and
            obj["reason"] == "OBSERVED_ONLY_NOT_PAPER_GO"):
        raise ValueError("RECEIPT_NO_IMAGE")
    if obj["status"] == "NO_VERIFICADO" and obj["reason"] == "OBSERVED_ONLY_NOT_PAPER_GO":
        raise ValueError("RECEIPT_STATUS_REASON")
    return obj


def main() -> int:
    if sys.argv[1:] == ["--host-probe"]:
        expected_sha = os.environ.get("RC6_DIAG_EXPECTED_PRODUCT_SHA", "")
        expected_image = os.environ.get("RC6_DIAG_EXPECTED_IMAGE_ID", "")
        try:
            reply = host_probe(expected_sha, expected_image)
        except (OSError, ValueError, TypeError):
            reply = default_receipt(expected_sha, expected_image)
        print(json.dumps(reply, sort_keys=True, separators=(",", ":")))
        return 0 if reply["status"] == "OBSERVED_ONLY" else 2
    if sys.argv[1:] == ["--validate-runner"]:
        expected_sha = os.environ.get("RC6_DIAG_EXPECTED_PRODUCT_SHA", "")
        expected_image = os.environ.get("RC6_DIAG_EXPECTED_IMAGE_ID", "")
        payload = Path(os.environ["RC6_DIAG_RECEIPT_PATH"])
        raw = payload.read_bytes() if payload.is_file() and payload.stat().st_size <= MAX_HOST_RECEIPT else b""
        try:
            if os.environ.get("RC6_DIAG_SSH_RC") != "0":
                raise ValueError("SSH_NOT_GREEN")
            reply = validate_receipt(json.loads(raw), expected_sha, expected_image)
        except (ValueError, TypeError, KeyError, UnicodeError):
            reply = default_receipt(expected_sha, expected_image)
            reply["reason"] = "HOST_RECEIPT_INVALID"
        # Sanitized only. Even a malformed remote stdout never enters artifacts.
        output = Path(os.environ["RC6_DIAG_SANITIZED_PATH"])
        output.write_text(json.dumps(reply, sort_keys=True, separators=(",", ":")) + "\n")
        print("RC6_R1_SCANNER_METADATA=" + reply["status"] + "|reason=" + reply["reason"] +
              "|probe_ppi_gets=0|probe_db_opened=0|probe_docker_mutations=0|probe_watch_mutations=0")
        return 0 if reply["status"] == "OBSERVED_ONLY" else 2
    print("RC6_R1_SCANNER_METADATA=NO_VERIFICADO|reason=INVALID_MODE")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

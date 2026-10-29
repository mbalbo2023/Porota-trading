"""Fail-closed, release-bound operator authority for RC6 PAPER admissions.

The authorization is deliberately stored *outside* the writable /app/data
mount. Only a root-operated, GitHub-Actions-verified host transition may write
the receipt; the observer sees the directory through a read-only bind mount.
This module never places an order or enables a trading route.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
OPERATOR_DIR_HOST = Path("/opt/porota-operator")
OPERATOR_DIR_RUNTIME = Path("/app/operator")
RECEIPT_NAME = "paper-rearm-v1.json"
MARKER_HOST = Path("/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json")
MARKER_RUNTIME = Path("/app/data/deploy/CURRENT_STATE_V2.json")
FROZEN_HOST = Path("/opt/porota-trading/data/deploy/porota-frozen-candidate.json")
FROZEN_RUNTIME = Path("/app/data/deploy/porota-frozen-candidate.json")
REARM_SCHEMA = "RC6_OPERATOR_PAPER_REARM_V1"
REARM_STATUS = "PAPER_REARM_AUTHORIZED"
SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_AUTHORITY_AGE = timedelta(days=30)
MAX_JSON_BYTES = 16 * 1024


def _distinct_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("OPERATOR_REARM_DUPLICATE_KEY")
        result[key] = value
    return result


def _strict_object(raw):
    def reject_constant(_):
        raise ValueError("OPERATOR_REARM_NONFINITE")

    value = json.loads(raw, object_pairs_hook=_distinct_pairs,
                       parse_constant=reject_constant)
    if type(value) is not dict:
        raise ValueError("OPERATOR_REARM_NOT_OBJECT")
    return value


def _read_regular(path, bound):
    """Never follow links, accept hard links or race a size-limited read."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        observed = os.fstat(fd)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
                or not 0 < observed.st_size <= bound):
            raise ValueError("OPERATOR_REARM_FILE_UNSAFE")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(bound + 1)
        if len(raw) != observed.st_size:
            raise ValueError("OPERATOR_REARM_FILE_CHANGED")
        return raw
    finally:
        os.close(fd)


def _root_owned_receipt(directory):
    """Proof that a PAPER container cannot forge the authority through DATA.

    Both directory and file must be root-owned and not writable by group or
    others; mount readonly is an additional independent defense.
    """
    directory = Path(directory)
    parent = os.lstat(directory)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0
            or parent.st_mode & 0o022):
        raise ValueError("OPERATOR_REARM_DIRECTORY_UNTRUSTED")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        file_fd = os.open(RECEIPT_NAME, os.O_RDONLY | os.O_NOFOLLOW,
                          dir_fd=fd)
        try:
            observed = os.fstat(file_fd)
            if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != 0
                    or observed.st_nlink != 1 or observed.st_mode & 0o022
                    or not 0 < observed.st_size <= MAX_JSON_BYTES):
                raise ValueError("OPERATOR_REARM_RECEIPT_UNTRUSTED")
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                raw = handle.read(MAX_JSON_BYTES + 1)
            if len(raw) != observed.st_size:
                raise ValueError("OPERATOR_REARM_RECEIPT_CHANGED")
            return _strict_object(raw)
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def _timestamp(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError("OPERATOR_REARM_TIMESTAMP_INVALID")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() != moment.astimezone(TZ).utcoffset():
        raise ValueError("OPERATOR_REARM_TIMESTAMP_NOT_ART")
    return moment.astimezone(TZ)


def evaluate_rearm(*, now, context="runtime", directory=None, marker_path=None,
                   frozen_path=None, candidate_sha=None, candidate_tree_sha=None,
                   read_receipt=_root_owned_receipt):
    """Verify immutable operator consent AND the exact validated PAPER release.

    No fallback from observer's environment identity to mutable DATA is allowed.
    Host preopen uses the frozen candidate tree and deployed validated marker;
    the observer also independently pins the image's SHA/tree via Docker ENV.
    Returns (authorized, reason, summary), never an execution capability.
    """
    if context not in {"host", "runtime"}:
        return False, "OPERATOR_REARM_CONTEXT_INVALID", None
    if not isinstance(now, datetime) or now.tzinfo is None:
        return False, "OPERATOR_REARM_CLOCK_INVALID", None
    at = now.astimezone(TZ)
    if at.date().isoformat() < "2026-10-29":
        return False, "OPERATOR_REARM_HISTORICAL_DAY", None
    if directory is None:
        directory = OPERATOR_DIR_HOST if context == "host" else OPERATOR_DIR_RUNTIME
    if marker_path is None:
        marker_path = MARKER_HOST if context == "host" else MARKER_RUNTIME
    if frozen_path is None:
        frozen_path = FROZEN_HOST if context == "host" else FROZEN_RUNTIME
    if context == "runtime":
        # These are injected into the immutable candidate image at build time.
        # Accept no mutable environment override for paths or operator source.
        candidate_sha = candidate_sha if candidate_sha is not None else os.getenv("POROTA_BUILD_SHA")
        candidate_tree_sha = (candidate_tree_sha if candidate_tree_sha is not None
                              else os.getenv("POROTA_CANDIDATE_TREE_SHA"))
    try:
        receipt = read_receipt(directory)
        marker = _strict_object(_read_regular(marker_path, 65536))
        frozen = _strict_object(_read_regular(frozen_path, 16384))
        source_sha = frozen.get("candidate_sha")
        tree_sha = frozen.get("candidate_tree_sha")
        image_id = marker.get("image_id")
        if (not isinstance(source_sha, str) or not SHA.fullmatch(source_sha)
                or not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha)
                or not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id)):
            raise ValueError("OPERATOR_REARM_CANDIDATE_INVALID")
        if context == "runtime" and (candidate_sha != source_sha
                                     or candidate_tree_sha != tree_sha):
            raise ValueError("OPERATOR_REARM_IMAGE_BUILD_MISMATCH")
        if context == "host" and (candidate_sha not in {None, source_sha}
                                  or candidate_tree_sha not in {None, tree_sha}):
            raise ValueError("OPERATOR_REARM_HOST_CANDIDATE_MISMATCH")
        if (marker.get("candidate_sha") != source_sha
                or marker.get("validation_status") != "VALIDATED_RUNTIME"
                or marker.get("mode") != "PRODUCTION_PAPER"
                or type(marker.get("real_orders_sent")) is not int
                or marker["real_orders_sent"] != 0
                or marker.get("real_order_routes") != "NOT_CALLED"
                or marker.get("ppi_watch_untouched") is not True
                or frozen.get("real_order_capability_required") != "BLOCKED"
                or frozen.get("paper_mode_required") != "PRODUCTION_PAPER"
                or frozen.get("build_once") is not True):
            raise ValueError("OPERATOR_REARM_RUNTIME_NOT_VALIDATED_PAPER0")
        if (set(receipt) != {
                "schema", "status", "candidate_sha", "candidate_tree_sha", "image_id",
                "deploy_run_id", "deploy_run_attempt", "runtime_validation_run_id",
                "operator_comment_id", "operator_github_login", "rearm_run_id",
                "issued_at_ar", "expires_at_ar", "paper_mode", "real_orders_sent",
                "real_order_routes", "ppi_watch_untouched"}
                or receipt.get("schema") != REARM_SCHEMA or receipt.get("status") != REARM_STATUS
                or receipt.get("candidate_sha") != source_sha
                or receipt.get("candidate_tree_sha") != tree_sha
                or receipt.get("image_id") != image_id
                or receipt.get("paper_mode") != "PRODUCTION_PAPER"
                or type(receipt.get("real_orders_sent")) is not int
                or receipt["real_orders_sent"] != 0
                or receipt.get("real_order_routes") != "NOT_CALLED"
                or receipt.get("ppi_watch_untouched") is not True
                or receipt.get("operator_github_login") != "mbalbo2023"
                or any(type(receipt.get(k)) is not int or receipt[k] < 1
                       for k in ("deploy_run_id", "deploy_run_attempt",
                                 "runtime_validation_run_id", "operator_comment_id",
                                 "rearm_run_id"))):
            raise ValueError("OPERATOR_REARM_AUTHORITY_MISMATCH")
        issued = _timestamp(receipt.get("issued_at_ar"))
        expiry = _timestamp(receipt.get("expires_at_ar"))
        if (not issued <= at < expiry
                or not timedelta(seconds=1) <= expiry - issued <= MAX_AUTHORITY_AGE):
            raise ValueError("OPERATOR_REARM_AUTHORITY_EXPIRED")
        return True, "OPERATOR_REARM_AUTHORIZED", {
            "candidate_sha": source_sha, "candidate_tree_sha": tree_sha,
            "image_id": image_id, "operator_comment_id": receipt["operator_comment_id"],
            "rearm_run_id": receipt["rearm_run_id"], "expires_at_ar": expiry.isoformat(),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM", None


def install_rearm(*, candidate_sha, candidate_tree_sha, deploy_run_id,
                  deploy_run_attempt, runtime_validation_run_id, operator_comment_id,
                  rearm_run_id, now=None, directory=OPERATOR_DIR_HOST,
                  marker_path=MARKER_HOST, frozen_path=FROZEN_HOST):
    """Atomic PAPER-only authority transition after Actions authenticates #624.

    Must run as root while holding the existing RC6 deploy mutex. The caller
    verifies the unedited exact owner comment and successful canonical Deploy
    V2 run before SSH; this host half also attests the frozen runtime again.
    """
    if os.geteuid() != 0:
        raise ValueError("OPERATOR_REARM_ROOT_REQUIRED")
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if (not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha)
            or not isinstance(candidate_tree_sha, str) or not SHA.fullmatch(candidate_tree_sha)
            or any(type(i) is not int or i < 1 for i in (
                deploy_run_id, deploy_run_attempt, runtime_validation_run_id,
                operator_comment_id, rearm_run_id))):
        raise ValueError("OPERATOR_REARM_INSTALL_INPUT_INVALID")
    marker = _strict_object(_read_regular(marker_path, 65536))
    frozen = _strict_object(_read_regular(frozen_path, 16384))
    image_id = marker.get("image_id")
    if (frozen.get("candidate_sha") != candidate_sha
            or frozen.get("candidate_tree_sha") != candidate_tree_sha
            or not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id)
            or marker.get("candidate_sha") != candidate_sha
            or marker.get("validation_status") != "VALIDATED_RUNTIME"
            or marker.get("mode") != "PRODUCTION_PAPER"
            or type(marker.get("real_orders_sent")) is not int
            or marker["real_orders_sent"] != 0
            or marker.get("real_order_routes") != "NOT_CALLED"
            or marker.get("ppi_watch_untouched") is not True
            or frozen.get("real_order_capability_required") != "BLOCKED"
            or frozen.get("paper_mode_required") != "PRODUCTION_PAPER"
            or frozen.get("build_once") is not True):
        raise ValueError("OPERATOR_REARM_INSTALL_RUNTIME_UNVERIFIED")
    directory = Path(directory)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("OPERATOR_REARM_DIRECTORY_ALIAS")
    directory.mkdir(mode=0o755, exist_ok=True)
    st = os.lstat(directory)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022
            or st.st_mode & 0o555 != 0o555):
        raise ValueError("OPERATOR_REARM_DIRECTORY_UNTRUSTED")
    destination = directory / RECEIPT_NAME
    if destination.exists() or destination.is_symlink():
        previous = _root_owned_receipt(directory)
        if (previous.get("candidate_sha") == candidate_sha
                and previous.get("operator_comment_id") == operator_comment_id
                and previous.get("rearm_run_id") == rearm_run_id):
            authorized, _, _ = evaluate_rearm(
                now=now, context="host", directory=directory,
                marker_path=marker_path, frozen_path=frozen_path)
            if authorized:
                return previous  # exact idempotent replay, no timestamp extension
        if previous.get("candidate_sha") == candidate_sha:
            raise ValueError("OPERATOR_REARM_DUPLICATE_CANDIDATE_AUTHORITY")
    issued = now.isoformat(timespec="seconds")
    expires = (now + timedelta(days=14)).isoformat(timespec="seconds")
    receipt = {
        "schema": REARM_SCHEMA, "status": REARM_STATUS,
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "image_id": image_id, "deploy_run_id": deploy_run_id,
        "deploy_run_attempt": deploy_run_attempt,
        "runtime_validation_run_id": runtime_validation_run_id,
        "operator_comment_id": operator_comment_id,
        "operator_github_login": "mbalbo2023", "rearm_run_id": rearm_run_id,
        "issued_at_ar": issued, "expires_at_ar": expires,
        "paper_mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
        "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True,
    }
    fd, temporary = tempfile.mkstemp(prefix=".paper-rearm-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump(receipt, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    verified, _, _ = evaluate_rearm(now=now, context="host", directory=directory,
                                     marker_path=marker_path, frozen_path=frozen_path)
    if not verified:
        raise ValueError("OPERATOR_REARM_INSTALL_VERIFY_RED")
    return receipt


def command_line(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-tree-sha", required=True)
    parser.add_argument("--deploy-run-id", type=int, required=True)
    parser.add_argument("--deploy-run-attempt", type=int, required=True)
    parser.add_argument("--runtime-validation-run-id", type=int, required=True)
    parser.add_argument("--operator-comment-id", type=int, required=True)
    parser.add_argument("--rearm-run-id", type=int, required=True)
    args = parser.parse_args(argv)
    from scripts import rc6_deploy_host_mutex as mutex
    # No stale INFLIGHT/CONTRACT_ONLY attempt can be silently bypassed by an
    # operator transition. The same root flock serializes all host writers.
    lock_fd = mutex.open_lock()
    try:
        if mutex._read_barrier(mutex.LOCK_DIR) is not None:
            raise ValueError("OPERATOR_REARM_DEPLOY_BARRIER_NOT_IDLE")
        receipt = install_rearm(
            candidate_sha=args.candidate_sha,
            candidate_tree_sha=args.candidate_tree_sha,
            deploy_run_id=args.deploy_run_id,
            deploy_run_attempt=args.deploy_run_attempt,
            runtime_validation_run_id=args.runtime_validation_run_id,
            operator_comment_id=args.operator_comment_id,
            rearm_run_id=args.rearm_run_id)
        # Reactivation is of a TIMER only. A PAPER order still requires the
        # independently fresh same-day v2 T-10 receipt and exact contract.
        unit = "porota-preopen-rc6.timer"
        result = subprocess.run(["systemctl", "enable", "--now", unit],
                                capture_output=True, text=True, timeout=20,
                                check=False)
        if result.returncode:
            raise RuntimeError("OPERATOR_REARM_PREOPEN_TIMER_ENABLE_RED")
        for operation, expected in (("is-enabled", "enabled"), ("is-active", "active")):
            check = subprocess.run(["systemctl", operation, unit],
                                   capture_output=True, text=True, timeout=10,
                                   check=False)
            if check.returncode or check.stdout.strip() != expected:
                raise RuntimeError("OPERATOR_REARM_TIMER_POSTVERIFY_RED")
        print(json.dumps({"status": "OPERATOR_REARM_AUTHORIZED",
                          "candidate_sha": receipt["candidate_sha"],
                          "operator_comment_id": receipt["operator_comment_id"],
                          "rearm_run_id": receipt["rearm_run_id"],
                          "paper_admission": "NO_GO_UNTIL_FRESH_T_MINUS_10", "real_orders_sent": 0}))
        return 0
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(command_line())

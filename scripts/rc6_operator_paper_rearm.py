"""Fail-closed, persistent operator intent for RC6 PAPER admissions.

Operator ACTIVE/PAUSED intent belongs to the operator, not to a release or a
timer. The intent persists until an explicit, owner-authenticated change;
each admission still independently requires the *currently installed* exact
validated PAPER/0 release and its fresh T-10 gate. The root-owned receipt is
outside writable /app/data and read-only inside the observer. This module
never places an order or enables a real-order route.
"""
from __future__ import annotations

import argparse
import hashlib
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
LEGACY_REARM_SCHEMA = "RC6_OPERATOR_PAPER_REARM_V1"
REARM_SCHEMA = "RC6_OPERATOR_PAPER_INTENT_V2"
REARM_STATUS = "PAPER_REARM_AUTHORIZED"
PAUSE_STATUS = "PAPER_PAUSED_BY_OPERATOR"
HISTORY_DIR_NAME = "paper-intent-history"
SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
# Legacy V1 expiration was a *historical issuance format*, never a reason to
# revoke a verified owner's consent automatically. New V2 records have no TTL.
LEGACY_ISSUANCE_MAX_AGE = timedelta(days=30)
MAX_JSON_BYTES = 16 * 1024

_ACTIVE_KEYS = frozenset({
    "schema", "status", "candidate_sha", "candidate_tree_sha", "image_id",
    "deploy_run_id", "deploy_run_attempt", "runtime_validation_run_id",
    "operator_comment_id", "operator_github_login", "rearm_run_id",
    "issued_at_ar", "paper_mode", "real_orders_sent", "real_order_routes",
    "ppi_watch_untouched",
})
_PAUSED_KEYS = frozenset({
    "schema", "status", "candidate_sha", "candidate_tree_sha",
    "operator_comment_id", "operator_github_login", "rearm_run_id",
    "issued_at_ar",
})


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


def _root_owned_receipt(directory, *, with_raw=False):
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
            receipt = _strict_object(raw)
            return (receipt, raw) if with_raw else receipt
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


def _validated_intent(receipt, at):
    """Validate provenance, including V1's original issuance, without a TTL.

    V1 is a previously Actions-authorized, root-owned owner receipt. Its
    expires_at_ar remains immutable legacy evidence, not a command to revoke
    the operator. Only a new, explicit PAUSE can revoke an ACTIVE intent.
    """
    schema = receipt.get("schema")
    status = receipt.get("status")
    if (receipt.get("operator_github_login") != "mbalbo2023"
            or type(receipt.get("operator_comment_id")) is not int
            or receipt["operator_comment_id"] < 1
            or type(receipt.get("rearm_run_id")) is not int
            or receipt["rearm_run_id"] < 1
            or not isinstance(receipt.get("candidate_sha"), str)
            or not SHA.fullmatch(receipt["candidate_sha"])
            or not isinstance(receipt.get("candidate_tree_sha"), str)
            or not SHA.fullmatch(receipt["candidate_tree_sha"])):
        raise ValueError("OPERATOR_INTENT_PROVENANCE_INVALID")
    issued = _timestamp(receipt.get("issued_at_ar"))
    if issued > at:
        raise ValueError("OPERATOR_INTENT_FUTURE_ISSUANCE")
    if schema == REARM_SCHEMA and status == PAUSE_STATUS:
        if set(receipt) != _PAUSED_KEYS:
            raise ValueError("OPERATOR_PAUSE_RECORD_INVALID")
        return status
    if (status != REARM_STATUS
            or schema not in {REARM_SCHEMA, LEGACY_REARM_SCHEMA}
            or set(receipt) != (_ACTIVE_KEYS | {"expires_at_ar"}
                                    if schema == LEGACY_REARM_SCHEMA else _ACTIVE_KEYS)
            or not isinstance(receipt.get("image_id"), str)
            or not IMAGE_ID.fullmatch(receipt["image_id"])
            or receipt.get("paper_mode") != "PRODUCTION_PAPER"
            or type(receipt.get("real_orders_sent")) is not int
            or receipt["real_orders_sent"] != 0
            or receipt.get("real_order_routes") != "NOT_CALLED"
            or receipt.get("ppi_watch_untouched") is not True
            or any(type(receipt.get(k)) is not int or receipt[k] < 1
                   for k in ("deploy_run_id", "deploy_run_attempt",
                             "runtime_validation_run_id"))):
        raise ValueError("OPERATOR_INTENT_ACTIVE_RECORD_INVALID")
    if schema == LEGACY_REARM_SCHEMA:
        expiry = _timestamp(receipt.get("expires_at_ar"))
        if not timedelta(seconds=1) <= expiry - issued <= LEGACY_ISSUANCE_MAX_AGE:
            raise ValueError("OPERATOR_INTENT_LEGACY_ISSUANCE_INVALID")
    return status


def trusted_operator_pause(*, now, context="host", directory=None):
    """Public, fail-closed provenance of an explicit V2 owner PAUSE.

    Uses the SAME root-owned, no-follow read for JSON and its exact byte hash;
    never trusts an old A176 pause as proof of a new operator action. Safe for
    preopen reporting even when current release and marker are degraded.
    """
    if context not in {"host", "runtime"} or not isinstance(now, datetime) or now.tzinfo is None:
        return None
    if directory is None:
        directory = OPERATOR_DIR_HOST if context == "host" else OPERATOR_DIR_RUNTIME
    try:
        receipt, raw = _root_owned_receipt(directory, with_raw=True)
        if _validated_intent(receipt, now.astimezone(TZ)) != PAUSE_STATUS:
            return None
        return {
            "status": PAUSE_STATUS,
            "receipt_sha256": hashlib.sha256(raw).hexdigest(),
            "operator_comment_id": receipt["operator_comment_id"],
            "rearm_run_id": receipt["rearm_run_id"],
            "issued_at_ar": receipt["issued_at_ar"],
            "candidate_sha": receipt["candidate_sha"],
            "candidate_tree_sha": receipt["candidate_tree_sha"],
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _validated_paper_release(*, marker_path, frozen_path, context,
                             candidate_sha=None, candidate_tree_sha=None):
    """Current release proof, independently re-checked for every PAPER use."""
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
    return source_sha, tree_sha, image_id


def evaluate_rearm(*, now, context="runtime", directory=None, marker_path=None,
                   frozen_path=None, candidate_sha=None, candidate_tree_sha=None,
                   read_receipt=_root_owned_receipt):
    """Verify persistent operator intent AND current validated PAPER release.

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
        if _validated_intent(receipt, at) == PAUSE_STATUS:
            # Keep the historical downstream PAPER hold reason unchanged;
            # PAUSE provenance is explicit in the root-owned receipt status.
            return False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM", None
        source_sha, tree_sha, image_id = _validated_paper_release(
            marker_path=marker_path, frozen_path=frozen_path, context=context,
            candidate_sha=candidate_sha, candidate_tree_sha=candidate_tree_sha)
        # The root-owned operator decision is durable across releases. Its
        # original V1/V2 SHA/image identify the *approval provenance*, never
        # the currently installed code. The release check above is exact.
        return True, "OPERATOR_REARM_AUTHORIZED", {
            "candidate_sha": source_sha, "candidate_tree_sha": tree_sha,
            "image_id": image_id, "operator_comment_id": receipt["operator_comment_id"],
            "rearm_run_id": receipt["rearm_run_id"],
            "intent_since_ar": receipt["issued_at_ar"],
            "operator_intent_schema": receipt["schema"],
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM", None


def _operator_directory(directory):
    """Create/verify a root-controlled intent location outside writable DATA."""
    directory = Path(directory)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("OPERATOR_REARM_DIRECTORY_ALIAS")
    directory.mkdir(mode=0o755, exist_ok=True)
    st = os.lstat(directory)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022
            or st.st_mode & 0o555 != 0o555):
        raise ValueError("OPERATOR_REARM_DIRECTORY_UNTRUSTED")
    return directory


def _previous_intent(directory, at):
    destination = directory / RECEIPT_NAME
    if not destination.exists() and not destination.is_symlink():
        return None
    previous = _root_owned_receipt(directory)
    _validated_intent(previous, at)
    return previous


def _archive_previous(directory):
    """Durably archive via temp+rename; never purge or overwrite old evidence.

    A crash during writing a temp does not create a final digest-name partial
    that would block a later safety PAUSE. An already-corrupt final digest
    entry is preserved and a fresh recovery copy is created alongside it.
    """
    destination = directory / RECEIPT_NAME
    if not destination.exists() and not destination.is_symlink():
        return
    raw = _read_regular(destination, MAX_JSON_BYTES)
    archive_dir = directory / HISTORY_DIR_NAME
    if archive_dir.is_symlink():
        raise ValueError("OPERATOR_INTENT_HISTORY_DIRECTORY_UNSAFE")
    archive_dir.mkdir(mode=0o755, exist_ok=True)
    st = os.lstat(archive_dir)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise ValueError("OPERATOR_INTENT_HISTORY_DIRECTORY_UNSAFE")
    digest = hashlib.sha256(raw).hexdigest()
    archive = archive_dir / (digest + ".json")
    if os.path.lexists(archive):
        try:
            observed = os.lstat(archive)
            if (stat.S_ISREG(observed.st_mode) and observed.st_uid == 0
                    and not observed.st_mode & 0o022
                    and _read_regular(archive, MAX_JSON_BYTES) == raw):
                return  # same complete digest already recorded
        except (OSError, ValueError):
            pass  # preserve damaged evidence; commit a recovery copy
    fd, temporary = tempfile.mkstemp(prefix=".history-staging-", dir=archive_dir)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(archive):
            # Unique destination; an old corrupt digest path is never removed.
            archive = archive_dir / (digest + "-recovered-" + Path(temporary).name[1:] + ".json")
        if os.path.lexists(archive):
            raise ValueError("OPERATOR_INTENT_HISTORY_RECOVERY_COLLISION")
        os.rename(temporary, archive)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    fd = os.open(archive_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _commit_intent(directory, receipt):
    """Archive existing proof, then publish one atomic root-owned state."""
    _archive_previous(directory)
    destination = directory / RECEIPT_NAME
    fd, temporary = tempfile.mkstemp(prefix=".paper-intent-", dir=directory)
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


def _require_new_owner_action(previous, operator_comment_id, rearm_run_id):
    if (operator_comment_id <= previous["operator_comment_id"]
            or rearm_run_id <= previous["rearm_run_id"]):
        raise ValueError("OPERATOR_INTENT_OLD_OWNER_ACTION_REPLAY")


def install_rearm(*, candidate_sha, candidate_tree_sha, deploy_run_id,
                  deploy_run_attempt, runtime_validation_run_id, operator_comment_id,
                  rearm_run_id, now=None, directory=OPERATOR_DIR_HOST,
                  marker_path=MARKER_HOST, frozen_path=FROZEN_HOST):
    """Atomic ACTIVE intent after Actions authenticates exact #624 owner GO.

    Must run as root under the existing RC6 deploy mutex. Unlike V1, this
    action never issues an expiration, and a new source/tree is not a new
    consent event. PAUSED->ACTIVE requires a newer verified owner action.
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
    try:
        _, _, image_id = _validated_paper_release(
            marker_path=marker_path, frozen_path=frozen_path, context="host",
            candidate_sha=candidate_sha, candidate_tree_sha=candidate_tree_sha)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("OPERATOR_REARM_INSTALL_RUNTIME_UNVERIFIED") from error
    directory = _operator_directory(directory)
    previous = _previous_intent(directory, now)
    if previous is not None:
        if (previous["status"] == REARM_STATUS
                and previous["operator_comment_id"] == operator_comment_id
                and previous["candidate_sha"] == candidate_sha
                and previous["candidate_tree_sha"] == candidate_tree_sha
                and previous["rearm_run_id"] <= rearm_run_id):
            return previous  # same owner GO retry, no new consent/TTL
        _require_new_owner_action(previous, operator_comment_id, rearm_run_id)
    receipt = {
        "schema": REARM_SCHEMA, "status": REARM_STATUS,
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "image_id": image_id, "deploy_run_id": deploy_run_id,
        "deploy_run_attempt": deploy_run_attempt,
        "runtime_validation_run_id": runtime_validation_run_id,
        "operator_comment_id": operator_comment_id,
        "operator_github_login": "mbalbo2023", "rearm_run_id": rearm_run_id,
        "issued_at_ar": now.isoformat(timespec="seconds"),
        "paper_mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
        "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True,
    }
    _commit_intent(directory, receipt)
    verified, _, _ = evaluate_rearm(now=now, context="host", directory=directory,
                                     marker_path=marker_path, frozen_path=frozen_path)
    if not verified:
        raise ValueError("OPERATOR_REARM_INSTALL_VERIFY_RED")
    return receipt


def install_pause(*, candidate_sha, candidate_tree_sha, operator_comment_id,
                  rearm_run_id, now=None, directory=OPERATOR_DIR_HOST):
    """Explicit, durable owner PAUSE; succeeds without a healthy runtime.

    The #624 comment and exact current product SHA/tree are authenticated by
    GitHub Actions. A safety-reducing PAUSE can be recorded even when marker,
    Docker image, PPI provider or T-10 receipt is unavailable. It never
    claims that any current runtime status has been checked.
    """
    if os.geteuid() != 0:
        raise ValueError("OPERATOR_PAUSE_ROOT_REQUIRED")
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if (not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha)
            or not isinstance(candidate_tree_sha, str) or not SHA.fullmatch(candidate_tree_sha)
            or type(operator_comment_id) is not int or operator_comment_id < 1
            or type(rearm_run_id) is not int or rearm_run_id < 1):
        raise ValueError("OPERATOR_PAUSE_INPUT_INVALID")
    directory = _operator_directory(directory)
    previous = _previous_intent(directory, now)
    if previous is not None:
        if (previous["status"] == PAUSE_STATUS
                and previous["operator_comment_id"] == operator_comment_id
                and previous["candidate_sha"] == candidate_sha
                and previous["candidate_tree_sha"] == candidate_tree_sha):
            if rearm_run_id == previous["rearm_run_id"]:
                return previous
            if rearm_run_id < previous["rearm_run_id"]:
                raise ValueError("OPERATOR_INTENT_OLD_OWNER_ACTION_REPLAY")
            # Same immutable owner PAUSE re-run on a newer Actions run: keep
            # PAUSED, record the new run and archive its predecessor. A failed
            # systemctl step must remain safely retryable without fresh GO.
        else:
            _require_new_owner_action(previous, operator_comment_id, rearm_run_id)
    receipt = {
        "schema": REARM_SCHEMA, "status": PAUSE_STATUS,
        "candidate_sha": candidate_sha, "candidate_tree_sha": candidate_tree_sha,
        "operator_comment_id": operator_comment_id,
        "operator_github_login": "mbalbo2023", "rearm_run_id": rearm_run_id,
        "issued_at_ar": now.isoformat(timespec="seconds"),
    }
    _commit_intent(directory, receipt)
    observed = _root_owned_receipt(directory)
    if observed != receipt or _validated_intent(observed, now) != PAUSE_STATUS:
        raise ValueError("OPERATOR_PAUSE_INSTALL_VERIFY_RED")
    return receipt


def command_line(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pause", action="store_true",
                        help="Persist explicit #624 owner PAUSE (default: REARM)")
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-tree-sha", required=True)
    parser.add_argument("--deploy-run-id", type=int)
    parser.add_argument("--deploy-run-attempt", type=int)
    parser.add_argument("--runtime-validation-run-id", type=int)
    parser.add_argument("--operator-comment-id", type=int, required=True)
    parser.add_argument("--rearm-run-id", type=int, required=True)
    args = parser.parse_args(argv)
    if not args.pause and any(value is None for value in (
            args.deploy_run_id, args.deploy_run_attempt,
            args.runtime_validation_run_id)):
        parser.error("REARM requires exact canonical deploy/runtime run IDs")
    from scripts import rc6_deploy_host_mutex as mutex
    # The same root flock serializes every operator and deploy host writer.
    lock_fd = mutex.open_lock()
    try:
        if args.pause:
            # Publish the negative intent FIRST. An already minted T-10
            # receipt cannot override a PAUSE, even if systemctl fails.
            # A safety reduction is allowed despite a stale deploy barrier;
            # the shared mutex still prevents concurrent host mutation.
            receipt = install_pause(
                candidate_sha=args.candidate_sha,
                candidate_tree_sha=args.candidate_tree_sha,
                operator_comment_id=args.operator_comment_id,
                rearm_run_id=args.rearm_run_id)
            unit = "porota-preopen-rc6.timer"
            result = subprocess.run(["systemctl", "disable", "--now", unit],
                                    capture_output=True, text=True, timeout=20,
                                    check=False)
            if result.returncode:
                raise RuntimeError("OPERATOR_PAUSE_PREOPEN_TIMER_DISABLE_RED_INTENT_PERSISTED")
            for operation, expected in (("is-enabled", "disabled"),
                                        ("is-active", "inactive")):
                check = subprocess.run(["systemctl", operation, unit],
                                       capture_output=True, text=True,
                                       timeout=10, check=False)
                # systemctl returns nonzero for the desired disabled/inactive
                # state; require exact output instead of exit code zero.
                if check.stdout.strip() != expected:
                    raise RuntimeError("OPERATOR_PAUSE_TIMER_POSTVERIFY_RED_INTENT_PERSISTED")
            authorized, reason, _ = evaluate_rearm(
                now=datetime.now(TZ), context="host")
            observed = _root_owned_receipt(OPERATOR_DIR_HOST)
            if (authorized or reason != "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM"
                    or observed.get("status") != PAUSE_STATUS):
                raise RuntimeError("OPERATOR_PAUSE_HOST_POSTVERIFY_RED")
            print(json.dumps({"status": PAUSE_STATUS,
                              "candidate_sha": receipt["candidate_sha"],
                              "operator_comment_id": receipt["operator_comment_id"],
                              "rearm_run_id": receipt["rearm_run_id"],
                              "paper_admission": "NO_GO_OPERATOR_PAUSE",
                              "real_order_routes_enabled": False}))
            return 0
        # No stale INFLIGHT/CONTRACT_ONLY attempt can be bypassed by an
        # ACTIVE transition. PAUSE, in contrast, always reduces authority.
        if mutex._read_barrier(mutex.LOCK_DIR) is not None:
            raise ValueError("OPERATOR_REARM_DEPLOY_BARRIER_NOT_IDLE")
        # Enable/verify the timer BEFORE installing the operator authority.
        # A failed systemd transition must never leave a new positive receipt.
        # An enabled timer without authority remains safely NO_GO.
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
        # Publish the positive authority last, only after systemd reaches the
        # required state while the same host mutex remains exclusively held.
        receipt = install_rearm(
            candidate_sha=args.candidate_sha,
            candidate_tree_sha=args.candidate_tree_sha,
            deploy_run_id=args.deploy_run_id,
            deploy_run_attempt=args.deploy_run_attempt,
            runtime_validation_run_id=args.runtime_validation_run_id,
            operator_comment_id=args.operator_comment_id,
            rearm_run_id=args.rearm_run_id)
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

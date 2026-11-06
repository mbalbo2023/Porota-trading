#!/usr/bin/env python3
"""A724: bounded physical *diagnosis*, never recovery, of one failed Deploy V2.

This script is streamed from a reviewed GitHub main workflow into a PRIVATE,
read-only mount namespace. It deliberately imports no runtime code, opens no
providers, executes no trading route, writes no host file and NEVER retires a
deploy barrier. Only allowlisted technical metadata leaves the host.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import io
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import zlib


SCHEMA = "rc6.a724.postfailed-physical-readonly.v1"
ROOT = Path("/opt/porota-trading")
DATA = ROOT / "data"
DEPLOY = DATA / "deploy"
SHADOW = DATA / "paper_v17/artifacts/observer_v17.db/dynamic-shadow"
BOOK = DATA / "paper_v17/observer_v17.db"
BARRIER = Path("/run/lock/porota-rc6-deploy-v2-attempt.json")
MUTEX = Path("/run/lock/porota-rc6-deploy-v2.lock")
OWNED = ("porota_production_observer", "porota_production_dashboard",
         "porota_critical_approval_rc6")
ORIGINAL_ID = "89e7f81dbc9b4a628bdc30edf86a17b0"
HISTORICAL_COLD2_ID = "7dc99ccd8f7c47849ca3705ae1cb2c49"
HISTORICAL_COLD2_MANIFEST = "7657b124c222993f9b34471626d0c740508ee783e7fcd8ce07c85ad9495f23f3"
ORIGINAL_CUT = "b6687dbca06ab97bc9c93d3f7940c61bba577f68d4ff4b9f887332638c0b7cb8"
HISTORICAL_COLD2_CUT = "af86116a3a64a9214cfde7ccfc1e04d3165270cf6d6f6ae2b4f4ce6e13c89ad9"
IOL_OAUTH_TIMER = "porota-iol-oauth-health-rc6.timer"
TIMERS = (
    "porota-runtime-evidence-rc6.timer",
    "porota-candle-integrity-rc6.timer",
    "porota-fast-functional-health-rc6.timer",
    "porota-market-watch-silence-rc6.timer",
    "porota-full-db-integrity-rc6.timer",
    "porota-preopen-rc6.timer",
    "porota-introspection-publish-rc6.timer",
    "porota-introspection-rc6.timer",
    "porota-iol-shadow-collector-rc6.timer",
    "porota-rc6-candle-inmarket-verify.timer",
    "porota-scheduler-export-rc6.timer",
    "porota-snapshot-postclose-rc6.timer",
    "porota-snapshot-preopen-rc6.timer",
    "porota-byma-morning-watch-rc6.timer",
    IOL_OAUTH_TIMER,
    "porota-local-eod-history-rc6.timer",
)
HEX40 = re.compile(r"[a-f0-9]{40}")
HEX32 = re.compile(r"[a-f0-9]{32}")
HEX64 = re.compile(r"[a-f0-9]{64}")
IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
CANDIDATE_TAG = re.compile(
    r"(?:porota-predeploy-v2:|porota-trading-bot:17\.0\.0-rc6-candidate-)[a-f0-9]{40}")
POROTA_DIGEST = re.compile(r"(?:porota-predeploy-v2|porota-trading-bot)@sha256:[a-f0-9]{64}")
CHECKS = {"marker", "containers", "barrier", "quiescence", "book",
          "cold", "scheduler", "images", "readonly_mount"}
MAX_JSON = 65536


class Hold(ValueError):
    """A local classification failure; never expose its dynamic input."""


def _reg(path: Path, limit: int, *, readonly: bool = False,
         root_only: bool = False) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 1 or before.st_size > limit or
                root_only and (before.st_uid != 0 or stat.S_IMODE(before.st_mode) != 0o600)):
            raise Hold("FILE_IDENTITY_HOLD")
        if readonly and not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
            raise Hold("DATA_MOUNT_NOT_READONLY")
        raw = os.read(fd, limit + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (
                before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns):
            raise Hold("FILE_RACE_OR_LIMIT")
        return raw
    finally:
        os.close(fd)


def _json(path: Path, limit: int = MAX_JSON, *, readonly: bool = True,
          root_only: bool = False) -> tuple[dict, str]:
    raw = _reg(path, limit, readonly=readonly, root_only=root_only)
    value = json.loads(raw)
    if type(value) is not dict:
        raise Hold("JSON_OBJECT_REQUIRED")
    return value, hashlib.sha256(raw).hexdigest()


def _canonical(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True,
                      default=str, ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _envelope(path: Path, *, cold: bool = False, gzip_cut: bool = False) -> dict:
    raw = _reg(path, 8 * 1024**2 if gzip_cut else 256 * 1024, readonly=True)
    if gzip_cut:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
            raw = fh.read(8 * 1024**2 + 1)
        if len(raw) > 8 * 1024**2:
            raise Hold("CUT_DECOMPRESSION_LIMIT")
    value = json.loads(raw)
    key = "sha256" if cold else "digest"
    if (type(value) is not dict or set(value) != {"payload", key} or
            type(value.get("payload")) is not dict or
            type(value.get(key)) is not str or
            not HEX64.fullmatch(value[key]) or
            not hmac.compare_digest(hashlib.sha256(_canonical(value["payload"])).hexdigest(),
                                    value[key])):
        raise Hold("COLD_ENVELOPE_HASH_HOLD")
    return value["payload"]


def _utc(stamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (TypeError, AttributeError, ValueError, OverflowError):
        return None


def _command(args: list[str], *, timeout: float = 4, limit: int = 8192) -> str:
    result = subprocess.run(args, capture_output=True, text=True,
                            check=False, timeout=timeout)
    if result.returncode or len(result.stdout) > limit:
        raise Hold("READONLY_COMMAND_UNAVAILABLE")
    return result.stdout.strip()


def _marker(path: Path, previous: str, failed_deploy: str, candidate: str) -> dict:
    result = {"state": "NO_VERIFICADO", "relation": "UNKNOWN", "deploy_sha": None,
              "candidate_sha": None, "image_id": None, "validation_status": None,
              "paper_zero": None, "real_routes_not_called": None}
    try:
        value, _ = _json(path)
        if (value.get("schema_version") != 2 or
                not isinstance(value.get("deploy_sha"), str) or
                not HEX40.fullmatch(value["deploy_sha"]) or
                not isinstance(value.get("candidate_sha"), str) or
                not HEX40.fullmatch(value["candidate_sha"]) or
                not isinstance(value.get("image_id"), str) or
                not IMAGE.fullmatch(value["image_id"]) or
                value.get("validation_status") not in
                {"VALIDATED_RUNTIME", "DEPLOYED_VALIDATION_PENDING"}):
            return result
        result.update(state="OBSERVED", deploy_sha=value["deploy_sha"],
                      candidate_sha=value["candidate_sha"],
                      image_id=value["image_id"],
                      validation_status=value["validation_status"],
                      paper_zero=(value.get("mode") == "PRODUCTION_PAPER" and
                                  type(value.get("real_orders_sent")) is int and
                                  value["real_orders_sent"] == 0),
                      real_routes_not_called=value.get("real_order_routes") == "NOT_CALLED")
        result["relation"] = (
            "R1_LAST_GO" if value["deploy_sha"] == previous else
            "FAILED_R2" if value["deploy_sha"] == failed_deploy and
                           value["candidate_sha"] == candidate else
            "OTHER_EXACT_SHA")
    except (OSError, Hold, ValueError, TypeError, KeyError):
        pass
    return result


def _containers(marker_id: str | None, expected_r2_image: str) -> list[dict]:
    result = []
    for name in OWNED:
        row = {"name": name, "state": "NO_VERIFICADO", "running": None,
               "health": "NO_VERIFICADO", "image_id": None,
               "image_matches_marker": None, "image_matches_r2_input": None}
        try:
            # Go template reads ONLY status/health and image identity, no Env,
            # labels, logs, network config or mount paths.
            data = _command(["docker", "inspect", "--format",
                             '{{json .State.Status}}|{{json .State.Running}}|'
                             '{{if .State.Health}}{{json .State.Health.Status}}'
                             '{{else}}"ABSENT"{{end}}|{{json .Image}}', name])
            status, running, health, image = [json.loads(x) for x in data.split("|")]
            if (status not in {"running", "exited", "created", "paused",
                               "restarting", "dead"} or type(running) is not bool or
                    health not in {"healthy", "unhealthy", "starting", "ABSENT"} or
                    not isinstance(image, str) or not IMAGE.fullmatch(image)):
                raise Hold("CONTAINER_FIELDS_INVALID")
            row.update(state=status.upper(), running=running,
                       health=health.upper(), image_id=image,
                       image_matches_marker=image == marker_id if marker_id else None,
                       image_matches_r2_input=image == expected_r2_image)
        except (Hold, ValueError, OSError, subprocess.TimeoutExpired):
            pass
        result.append(row)
    return result


def _barrier(path: Path, mutex: Path, candidate: str, run_id: str) -> dict:
    result = {"state": "NO_VERIFICADO", "relation": "NO_VERIFICADO",
              "phase": None, "status": None, "flock_idle": None}
    fd = None
    try:
        # Opening an existing lock O_RDONLY cannot create/alter it; the
        # original mutator opens this inode O_RDWR|O_CREAT.
        fd = os.open(mutex, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise Hold("LOCK_IDENTITY_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            result["flock_idle"] = True
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            result["flock_idle"] = False
        try:
            value, _ = _json(path, 1024, readonly=False, root_only=True)
        except FileNotFoundError:
            result.update(state="ABSENT", relation="ABSENT", status="ABSENT")
            return result
        phase, status = value.get("phase"), value.get("status")
        if (set(value) != {"candidate", "run", "attempt", "phase", "status"} or
                type(phase) is not str or not re.fullmatch("[a-z_]{2,32}", phase) or
                status not in {"IDLE", "INFLIGHT", "CONTRACT_ONLY"} or
                not isinstance(value.get("candidate"), str) or
                not HEX40.fullmatch(value["candidate"]) or
                not isinstance(value.get("run"), str) or
                not re.fullmatch("[1-9][0-9]*", value["run"]) or
                not isinstance(value.get("attempt"), str) or
                not re.fullmatch("[1-9][0-9]*", value["attempt"])):
            raise Hold("BARRIER_INVALID")
        result.update(state="OBSERVED", phase=phase, status=status,
                      relation="FAILED_R2_ATTEMPT" if value["candidate"] == candidate
                              and value["run"] == run_id else "OTHER_ATTEMPT")
    except (OSError, Hold, ValueError, TypeError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return result


def _stop_status_consistent(proof: object, live: dict) -> bool | None:
    """Compare lifecycle status only; Docker container ID/image NOT proven."""
    if not isinstance(proof, dict):
        return None
    after = proof.get("after")
    if not isinstance(after, dict):
        return None
    return (proof.get("classification") in
            {"CLEAN_EXIT", "INHERITED_OOM_FLAG_CLEAN_EXIT", "CANDIDATE_CLEAN_EXIT"} and
            after.get("Status") == "exited" and
            type(after.get("ExitCode")) is int and after["ExitCode"] == 0 and
            after.get("OOMKilled") is False and
            live.get("state") == "EXITED")


def _quiescence(path: Path, candidate: str, containers: list[dict]) -> dict:
    result = {"state": "NO_VERIFICADO", "candidate_matches": None,
              "status": None, "installation_started": None,
              "inflight": None, "paper_zero": None, "watch_untouched": None,
              "paused_postclose": None, "intraday_caucion": None,
              "pending_caucion": None, "market_open_suspended": None,
              "open_paper_carryover": None, "spot_carryover_absent": None,
              "caucion_carryover_absent": None,
              "closed_flat_flags": None, "stop_evidence_status_consistent": None,
              "receipt_sha256": None}
    try:
        row, digest = _json(path)
        if (not isinstance(row.get("candidate_sha"), str) or
                not HEX40.fullmatch(row["candidate_sha"]) or
                row.get("status") not in
                {"DRAINED", "HOST_INSTALLATION_STARTED",
                 "FIX_FORWARD_VALIDATION_REQUIRED", "ADMIN_DRAINED_PENDING",
                 "IN_PROGRESS", "PREPARED", "NO_VERIFICADO"}):
            return result
        keys = ("paused_postclose", "intraday_caucion", "pending_caucion",
                "market_open_suspended", "open_paper_carryover")
        booleans = {key: row.get(key) if type(row.get(key)) is bool else None
                    for key in keys}
        flat = (all(booleans[key] is False for key in
                    ("paused_postclose", "intraday_caucion", "pending_caucion",
                     "open_paper_carryover")) and
                row.get("spot_carryover") is None and
                row.get("caucion_carryover") is None)
        stop = row.get("stop_evidence") or {}
        checks = [_stop_status_consistent(stop.get(name), live) for name, live
                  in zip(OWNED[:2], containers[:2])]
        result.update(state="OBSERVED", candidate_matches=row["candidate_sha"] == candidate,
                      status=row["status"],
                      installation_started=row.get("installation_started") if
                      type(row.get("installation_started")) is bool else None,
                      inflight=row.get("inflight") if row.get("inflight") in
                      {"ABSENT_OWNED_RUNTIME_STOPPED", "PRESENT", "NO_VERIFICADO"} else None,
                      paper_zero=(type(row.get("real_orders_sent")) is int and
                                  row["real_orders_sent"] == 0),
                      watch_untouched=row.get("ppi_watch_accessed") is False,
                      spot_carryover_absent=row.get("spot_carryover") is None,
                      caucion_carryover_absent=row.get("caucion_carryover") is None,
                      closed_flat_flags=flat,
                      stop_evidence_status_consistent=(
                          all(checks) if None not in checks else None),
                      receipt_sha256=digest, **booleans)
    except (OSError, Hold, ValueError, TypeError, KeyError):
        pass
    return result


def _book(path: Path) -> dict:
    result = {"state": "NO_VERIFICADO", "mode_paper": None,
              "orders_zero": None, "process_state": None, "session_state": None,
              "spot_open": None, "caucion_open": None,
              "futures_active": None, "fci_not_redeemed": None,
              "exposure_flat": None}
    try:
        if not path.is_file() or path.is_symlink():
            raise Hold("DB_IDENTITY_HOLD")
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise Hold("DB_NOT_READONLY")
        uri = path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=.2)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=200")
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            state = db.execute("SELECT mode,real_orders_sent,process_state,session_state "
                               "FROM observer_state WHERE id=1").fetchone()
            if state is None:
                raise Hold("OBSERVER_STATE_ABSENT")
            allowed_process = {"RUNNING", "STOPPED", "WAITING_MARKET", "DEGRADED", "STARTING"}
            allowed_session = {"CLOSED", "MARKET_CLOSED", "OPEN", "MARKET_OPEN",
                               "BOOT_CALENDAR", "CHECKING"}
            if state[2] not in allowed_process or state[3] not in allowed_session:
                raise Hold("BOOK_PHASE_UNRECOGNIZED")
            counts = {}
            for key, table, predicate in (
                ("spot_open", "paper_positions", "status='OPEN'"),
                ("caucion_open", "paper_cauciones", "status='OPEN'"),
                ("futures_active", "paper_future_positions", "status='ACTIVE'"),
                ("fci_not_redeemed", "paper_family_lifecycle",
                 "family='FCI' AND state!='REDEEMED'")):
                count = db.execute("SELECT COUNT(*) FROM " + table +
                                   " WHERE " + predicate).fetchone()[0]
                if type(count) is not int or count < 0 or count > 1_000_000:
                    raise Hold("BOOK_COUNT_BOUND")
                counts[key] = count
            result.update(state="READ_ONLY_OBSERVED",
                          mode_paper=state[0] == "PRODUCTION_PAPER",
                          orders_zero=(type(state[1]) is int and state[1] == 0),
                          process_state=state[2], session_state=state[3],
                          exposure_flat=all(value == 0 for value in counts.values()),
                          **counts)
    except (sqlite3.Error, OSError, Hold, ValueError, TypeError, KeyError):
        pass
    return result


def _pack_metadata(pack: Path, manifest: dict, owner_uid: int) -> str:
    try:
        info = pack.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) & 0o077):
            return "PACK_TYPE_LINK_MODE"
        if info.st_uid != owner_uid:
            return "PACK_OWNER"
        if (type(manifest.get("pack_bytes")) is not int or
                manifest["pack_bytes"] != info.st_size):
            return "PACK_BYTES"
        identity = manifest.get("pack_identity")
        if (type(identity) is not list or len(identity) != 5 or
                any(type(part) is not int for part in identity)):
            return "IDENTITY_SHAPE"
        if identity[:2] != [info.st_dev, info.st_ino]:
            return "PACK_DEV_INO"
        if identity[2] != info.st_size:
            return "PACK_IDENTITY_SIZE"
        if identity[3] != info.st_mtime_ns:
            return "PACK_MTIME"
        if identity[4] != info.st_ctime_ns:
            return "PACK_CTIME"
        return "METADATA_BOUND_NOT_HASHED"
    except (OSError, TypeError, ValueError):
        return "PACK_UNVERIFIED"


def _historical_cold2_pack(deploy: Path, *,
                           expected_manifest: str = HISTORICAL_COLD2_MANIFEST) -> str:
    """Independently inspect historical archive metadata, never pack payload.

    It is NOT the CURRENT-bound successor. Never combine these causes into a
    single repair predicate or infer eligibility for Docker/DB cleanup.
    """
    archive = deploy / ("rc6-failed-cold-" + HISTORICAL_COLD2_ID)
    try:
        if not archive.exists():
            return "ABSENT"
        info = archive.lstat()
        if (not stat.S_ISDIR(info.st_mode) or
                stat.S_IMODE(info.st_mode) & 0o077 or
                archive.is_symlink()):
            return "ARCHIVE_DIRECTORY_UNSAFE"
        names = {part.name for part in archive.iterdir()}
        if names != {HISTORICAL_COLD2_ID + ".pack",
                     "manifest-" + HISTORICAL_COLD2_ID + ".json",
                     "receipt-" + HISTORICAL_COLD2_ID + ".json",
                     "queue.json", "quarantine.json"}:
            return "ARCHIVE_NAMESPACE_NOT_EXACT"
        manifest = _envelope(archive /
            ("manifest-" + HISTORICAL_COLD2_ID + ".json"), cold=True)
        receipt = _envelope(archive /
            ("receipt-" + HISTORICAL_COLD2_ID + ".json"), cold=True)
        queue = _envelope(archive / "queue.json", cold=True)
        quarantine = _envelope(archive / "quarantine.json")
        sha = hashlib.sha256(_canonical(manifest)).hexdigest()
        if (sha != expected_manifest or
                manifest.get("snapshot_id") != HISTORICAL_COLD2_ID or
                receipt.get("snapshot_id") != HISTORICAL_COLD2_ID or
                receipt.get("state") != "FAILED" or
                receipt.get("manifest_sha256") != sha or
                queue.get("state") != "FAILED" or
                queue.get("snapshot_id") != HISTORICAL_COLD2_ID or
                queue.get("manifest_sha256") != sha or
                quarantine.get("state") != "QUARANTINED_FAILED" or
                quarantine.get("snapshot_id") != HISTORICAL_COLD2_ID or
                quarantine.get("manifest_sha256") != sha or
                quarantine.get("old_cut_revoked") is not True):
            return "HISTORICAL_SMALL_RECEIPT_NOT_BOUND"
        return _pack_metadata(archive / (HISTORICAL_COLD2_ID + ".pack"),
                              manifest, info.st_uid)
    except (OSError, Hold, ValueError, TypeError, KeyError):
        return "NO_VERIFICADO"


def _cold(shadow: Path, deploy: Path) -> dict:
    result = {"state": "NO_VERIFICADO", "current_generation": None,
              "current_snapshot": None, "live_status": None,
              "current_cut_hash_bound": None, "current_cut_paper_zero": None,
              "live_paper_zero": None, "live_routes_not_called": None,
              "failed_queue_state": None, "failed_queue_bound": None,
              "pack_metadata": "NO_VERIFICADO",
              "historical_cold2_pack_metadata_cause": _historical_cold2_pack(deploy),
              "pack_hash_verified": False, "failed_computed_report_hash_verified": False,
              "successor_distinct_from_revoked": None,
              "successor_predicate": "NO_VERIFICADO",
              "original_revoked_cut_absent": None,
              "historical_cold2_revoked_cut_absent": None}
    try:
        pointer = _envelope(shadow / "CURRENT.json")
        generation = pointer.get("generation_id")
        if (type(generation) is not str or not HEX64.fullmatch(generation) or
                pointer.get("file") != "cut-" + generation + ".json.gz"):
            raise Hold("CURRENT_POINTER_INVALID")
        cut = _envelope(shadow / pointer["file"], gzip_cut=True)
        manifest = cut.get("manifest")
        source = cut.get("report", {}).get("computed_report", {})
        if (type(manifest) is not dict or type(source) is not dict or
                manifest.get("generation_id") != generation or
                manifest.get("sequence") != pointer.get("sequence")):
            raise Hold("CURRENT_CUT_INVALID")
        bound = (type(pointer.get("manifest_sha256")) is str and
                 HEX64.fullmatch(pointer["manifest_sha256"]) and
                 hmac.compare_digest(hashlib.sha256(_canonical(manifest)).hexdigest(),
                                     pointer["manifest_sha256"]))
        snapshot = source.get("snapshot_id")
        if type(snapshot) is not str or not HEX32.fullmatch(snapshot):
            snapshot = None
        live = _envelope(shadow / "status.json")
        result.update(state="OBSERVED", current_generation=generation,
                      current_snapshot=snapshot,
                      current_cut_hash_bound=bool(bound),
                      current_cut_paper_zero=(cut.get("status", {}).get("real_orders_sent") == 0 and
                                              cut.get("status", {}).get("real_routes") == "NOT_CALLED"),
                      live_status=live.get("status") if live.get("status") in
                      {"FAIL_CLOSED", "SHADOW_OBSERVING"} else "OTHER",
                      live_paper_zero=(type(live.get("real_orders_sent")) is int and
                                       live["real_orders_sent"] == 0),
                      live_routes_not_called=live.get("real_routes") == "NOT_CALLED",
                      original_revoked_cut_absent=not (shadow /
                          ("cut-" + ORIGINAL_CUT + ".json.gz")).exists(),
                      historical_cold2_revoked_cut_absent=not (shadow /
                          ("cut-" + HISTORICAL_COLD2_CUT + ".json.gz")).exists())
        distinct = snapshot not in (None, ORIGINAL_ID, HISTORICAL_COLD2_ID) and (
            generation not in (ORIGINAL_CUT, HISTORICAL_COLD2_CUT))
        result["successor_distinct_from_revoked"] = distinct
        if not distinct:
            result["successor_predicate"] = "REVOKED_OR_UNIDENTIFIED"
            return result
        source_dir = shadow / "cold-audit"
        archive_dir = deploy / ("rc6-failed-cold-" + snapshot)
        queue_path = source_dir / "queue.json"
        try:
            queue = _envelope(queue_path, cold=True)
        except FileNotFoundError:
            result["successor_predicate"] = "NO_CURRENT_FAILED_QUEUE"
            return result
        result["failed_queue_state"] = (queue.get("state") if queue.get("state") in
                                        {"FAILED", "PENDING", "COMPLETE"} else "OTHER")
        if queue.get("snapshot_id") != snapshot or queue.get("state") != "FAILED":
            result["successor_predicate"] = "QUEUE_NOT_BOUND"
            return result
        # Match the actual source (or exact completed archive) identity. Never
        # choose an unknown/ambiguous source. No large pack is opened/hashed.
        manifest = _envelope(source_dir / ("manifest-" + snapshot + ".json"), cold=True)
        receipt = _envelope(source_dir / ("receipt-" + snapshot + ".json"), cold=True)
        manifest_sha = hashlib.sha256(_canonical(manifest)).hexdigest()
        bound = (manifest.get("snapshot_id") == snapshot and
                 manifest.get("pack") == snapshot + ".pack" and
                 manifest.get("epoch") == "rc6.audit-actual-values.v1" and
                 queue.get("manifest_sha256") == manifest_sha and
                 receipt.get("manifest_sha256") == manifest_sha and
                 receipt.get("snapshot_id") == snapshot and
                 receipt.get("state") == "FAILED" and
                 source.get("manifest_sha256") == manifest_sha and
                 source.get("state") == "PENDING")
        result["failed_queue_bound"] = bool(bound)
        if not bound:
            result["successor_predicate"] = "MANIFEST_RECEIPT_NOT_BOUND"
            return result
        # Owner must agree with the private source directory itself. The
        # earlier A667 diagnostic attributed this exact check to PACK_OWNER.
        owner = source_dir.lstat().st_uid
        meta = _pack_metadata(source_dir / (snapshot + ".pack"), manifest, owner)
        result["pack_metadata"] = meta
        result["successor_predicate"] = (
            "PACK_METADATA_REJECTED" if meta != "METADATA_BOUND_NOT_HASHED" else
            "NO_VERIFICADO_PACK_AND_REPORT_HASH_REQUIRED")
    except (OSError, Hold, ValueError, TypeError, KeyError, gzip.BadGzipFile,
            EOFError, zlib.error):
        pass
    return result


def _systemd(name: str) -> dict:
    item = {"name": name, "active": "NO_VERIFICADO",
            "enabled": "NO_VERIFICADO", "entered_at": None}
    try:
        raw = _command(["systemctl", "show", name, "--no-pager",
                        "--property=ActiveState,UnitFileState,ActiveEnterTimestamp"],
                       timeout=3, limit=2048)
        props = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        active = props.get("ActiveState")
        enabled = props.get("UnitFileState")
        if active not in {"active", "inactive", "failed", "activating", "deactivating"}:
            return item
        item["active"] = active.upper()
        item["enabled"] = (enabled.upper() if enabled in
                           {"enabled", "disabled", "static", "masked"} else "NO_VERIFICADO")
        stamp = props.get("ActiveEnterTimestamp", "")
        # Bounded public timestamp only: do not echo untrusted journal text.
        if re.fullmatch(r"[A-Za-z]{3} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC", stamp):
            item["entered_at"] = stamp[4:]
    except (OSError, Hold, ValueError, subprocess.TimeoutExpired):
        pass
    return item


def _scheduler(path: Path, candidate: str, timers: list[dict]) -> dict:
    result = {"state": "NO_VERIFICADO", "receipt_status": None,
              "candidate_matches": None, "attempted_count": None,
              "suspended_timer_count": None, "preserved_iol_guard": None,
              "iol_timer_active": None, "iol_activation_after_suspend": None,
              "unexpected_active_timers": [], "receipt_sha256": None}
    try:
        receipt, digest = _json(path)
        names = receipt.get("timers")
        attempted = receipt.get("attempted")
        if (not isinstance(names, list) or len(names) != 16 or
                set(names) != set(TIMERS) or
                not isinstance(attempted, list) or
                any(x not in TIMERS for x in attempted) or
                len(attempted) != len(set(attempted))):
            raise Hold("SCHEDULER_RECEIPT_NOT_EXACT")
        status = receipt.get("status")
        if status not in {"SUSPENDED", "RESTORED", "SUSPENDING"}:
            status = "OTHER"
        preserved = receipt.get("preserved_iol_timer") == IOL_OAUTH_TIMER
        timer = next(value for value in timers if value["name"] == IOL_OAUTH_TIMER)
        entered = timer.get("entered_at")
        started = _utc(receipt.get("recorded_at"))
        newer = None
        if entered and started:
            seen = _utc(entered.replace(" UTC", "+00:00"))
            newer = seen > started if seen else None
        unexpected = [value["name"] for value in timers if
                      value.get("active") == "ACTIVE" and
                      not (value["name"] == IOL_OAUTH_TIMER and preserved)]
        result.update(state="OBSERVED", receipt_status=status,
                      candidate_matches=receipt.get("candidate_sha") == candidate,
                      attempted_count=len(attempted), suspended_timer_count=len(names),
                      preserved_iol_guard=preserved,
                      iol_timer_active=timer.get("active") == "ACTIVE",
                      iol_activation_after_suspend=newer,
                      unexpected_active_timers=unexpected, receipt_sha256=digest)
    except (OSError, Hold, ValueError, TypeError, KeyError, StopIteration):
        pass
    return result


def _image_manifest_bound(candidate: str, expected_image: str) -> bool:
    """Require current RO installed frozen manifest to bind candidate/image."""
    try:
        frozen, _ = _json(DEPLOY / "porota-frozen-candidate.json", 32768)
        return (frozen.get("candidate_sha") == candidate and
                frozen.get("image_id") == expected_image and
                type(frozen.get("candidate_tree_sha")) is str and
                bool(HEX40.fullmatch(frozen["candidate_tree_sha"])) and
                type(frozen.get("image_tar_sha256")) is str and
                bool(HEX64.fullmatch(frozen["image_tar_sha256"])))
    except (OSError, Hold, ValueError, TypeError, KeyError):
        return False


def _images(candidate: str, expected_image: str) -> tuple[list[dict], int, int]:
    """Never expose OTHER/Boca ID, tag, digest or individual image size.

    Only an *exact R2 candidate image* additionally bound to the current
    host-frozen manifest can be published individually. All other unreferenced
    images are represented by a redacted COUNT, even if Porota-looking.
    Nothing here grants cleanup authorization.
    """
    try:
        frozen_bound = _image_manifest_bound(candidate, expected_image)
        containers = _command(["docker", "ps", "-aq", "--no-trunc"],
                              timeout=5, limit=8192).splitlines()
        image_ids = _command(["docker", "image", "ls", "-aq", "--no-trunc"],
                             timeout=5, limit=8192).splitlines()
        if len(containers) > 128 or len(image_ids) > 128 or any(
                re.fullmatch("[a-f0-9]{12,64}", c) is None for c in containers):
            raise Hold("IMAGE_INVENTORY_BOUND")
        referenced = set()
        for cid in containers:
            ref = _command(["docker", "inspect", "--format", "{{.Image}}", cid])
            if not IMAGE.fullmatch(ref):
                raise Hold("CONTAINER_IMAGE_ID_UNSAFE")
            referenced.add(ref)
        unreferenced = []
        total_unreferenced = 0
        for iid in sorted(set(image_ids)):
            if not IMAGE.fullmatch(iid):
                raise Hold("IMAGE_ID_UNSAFE")
            if iid in referenced:
                continue
            total_unreferenced += 1
            fields = _command(["docker", "image", "inspect", "--format",
                               "{{json .Id}}|{{json .RepoTags}}|"
                               "{{json .RepoDigests}}|{{json .Size}}", iid]).split("|")
            if len(fields) != 4:
                raise Hold("IMAGE_FIELDS_UNSAFE")
            identity, tags, digests, size = (json.loads(part) for part in fields)
            if (identity != iid or type(size) is not int or not 0 <= size < 10**12 or
                    tags is not None and not isinstance(tags, list) or
                    digests is not None and not isinstance(digests, list)):
                raise Hold("IMAGE_METADATA_INVALID")
            tags, digests = tags or [], digests or []
            if any(not isinstance(value, str) or len(value) > 255
                   for value in tags + digests):
                raise Hold("IMAGE_ALIAS_INVALID")
            candidate_tags_valid = bool(tags) and all(
                CANDIDATE_TAG.fullmatch(t) for t in tags)
            digest_match = all(POROTA_DIGEST.fullmatch(d) for d in digests)
            repos = {tag.split(":", 1)[0] for tag in tags}
            digest_repos = {d.split("@", 1)[0] for d in digests}
            exact_candidate_tags = bool(tags) and all(
                tag.endswith(candidate) for tag in tags)
            proven = (candidate_tags_valid and exact_candidate_tags and iid == expected_image and
                      frozen_bound and bool(digests) and digest_match and
                      digest_repos <= repos)
            if not proven:
                # No ID, no individual size, no foreign/mixed alias. Protect
                # every unrecognized container/image, including Boca.
                continue
            safe_tags = [t for t in tags if CANDIDATE_TAG.fullmatch(t)]
            safe_digests = [d for d in digests if POROTA_DIGEST.fullmatch(d)]
            unreferenced.append({
                "image_id": iid, "size_upper_bytes": size,
                "tags_allowlisted": safe_tags[:8],
                "digests_allowlisted": safe_digests[:8],
                "noncandidate_alias_count": len(tags) - len(safe_tags),
                "nonporota_digest_count": len(digests) - len(safe_digests),
                "class": "CANDIDATE_METADATA_ONLY_NOT_CLEANUP_GO",
                "referenced_by_container": False,
            })
        return unreferenced[:16], total_unreferenced, (
            total_unreferenced - len(unreferenced[:16]))
    except (OSError, Hold, ValueError, TypeError, subprocess.TimeoutExpired):
        return [], -1, -1


def _readonly_mount() -> bool:
    try:
        fd = os.open(DATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY)
        finally:
            os.close(fd)
    except OSError:
        return False


def observe(*, root: Path, previous: str, failed_deploy: str,
            candidate: str, expected_image: str, run_id: str) -> dict:
    global DATA, DEPLOY, SHADOW, BOOK
    if (not all(HEX40.fullmatch(x) for x in (previous, failed_deploy, candidate)) or
            not IMAGE.fullmatch(expected_image) or
            not re.fullmatch(r"[1-9][0-9]*", run_id)):
        raise Hold("REQUEST_INVALID")
    # Test-only root injection; production is pinned to /opt/porota-trading.
    DATA = root / "data"
    DEPLOY = DATA / "deploy"
    SHADOW = DATA / "paper_v17/artifacts/observer_v17.db/dynamic-shadow"
    BOOK = DATA / "paper_v17/observer_v17.db"
    readonly = _readonly_mount()
    if not readonly:
        return {"schema": SCHEMA, "state": "NO_VERIFICADO",
                "reason": "READONLY_DATA_MOUNT_UNPROVEN", "read_only": True,
                "mutations": 0, "provider_gets": 0, "orders_called": False,
                "ppi_watch_touched": False}
    marker = _marker(DEPLOY / "CURRENT_STATE_V2.json",
                     previous, failed_deploy, candidate)
    containers = _containers(marker.get("image_id"), expected_image)
    barrier_first = _barrier(BARRIER, MUTEX, candidate, run_id)
    quiescence = _quiescence(DEPLOY / ("quiescence-" + candidate + ".json"),
                             candidate, containers)
    book = _book(BOOK)
    cold = _cold(SHADOW, DEPLOY)
    timers = [_systemd(name) for name in TIMERS]
    scheduler = _scheduler(DEPLOY / ("scheduler-suspend-" + candidate + ".json"),
                           candidate, timers)
    images, count, redacted = _images(candidate, expected_image)
    barrier_last = _barrier(BARRIER, MUTEX, candidate, run_id)
    barrier_stable = barrier_first == barrier_last
    now = datetime.now(timezone.utc)
    return {
        "schema": SCHEMA, "state": "DIAGNOSTIC_ONLY",
        "read_only": True, "mount_ro_verified": True,
        "mutations": 0, "provider_gets": 0, "orders_called": False,
        "ppi_watch_touched": False,
        "generated_at_utc": now.isoformat(),
        "generated_at_art": now.astimezone(timezone(-timedelta(hours=3))).isoformat(),
        "expected_previous_deploy_sha": previous,
        "failed_deploy_sha": failed_deploy, "failed_candidate_sha": candidate,
        "failed_expected_image_id": expected_image, "failed_run_id": run_id,
        "marker": marker, "containers": containers,
        "barrier": barrier_last, "barrier_snapshot_stable": barrier_stable,
        "quiescence": quiescence, "book": book, "cold": cold,
        "scheduler": scheduler, "timers": timers,
        "unreferenced_images": images, "unreferenced_total": count,
        "unreferenced_redacted_count": redacted,
        # Deliberately NEVER infer runtime validated from one snapshot.
        "runtime_running_verified": False,
        "recovery_authorized": False, "paper_admission_authorized": False,
    }


def sanitize(raw: str, expected: dict[str, str]) -> dict:
    """Independent runner allowlist check; never print raw SSH JSON/errors."""
    fail = {"schema": SCHEMA, "state": "NO_VERIFICADO", "read_only": True,
            "reason": "HOST_DIAGNOSTIC_UNAVAILABLE", "mutations": 0,
            "provider_gets": 0, "orders_called": False,
            "ppi_watch_touched": False}
    try:
        if len(raw.encode()) > 32768:
            return fail
        obj = json.loads(raw)
        if (type(obj) is not dict or obj.get("schema") != SCHEMA or
                obj.get("state") != "DIAGNOSTIC_ONLY" or
                obj.get("read_only") is not True or
                obj.get("mount_ro_verified") is not True or
                obj.get("mutations") != 0 or obj.get("provider_gets") != 0 or
                obj.get("orders_called") is not False or
                obj.get("ppi_watch_touched") is not False or
                obj.get("runtime_running_verified") is not False or
                obj.get("recovery_authorized") is not False or
                obj.get("paper_admission_authorized") is not False or
                any(obj.get(key) != expected[key] for key in expected) or
                set(obj) != {
                  "schema", "state", "read_only", "mount_ro_verified",
                  "mutations", "provider_gets", "orders_called",
                  "ppi_watch_touched", "generated_at_utc", "generated_at_art",
                  "expected_previous_deploy_sha", "failed_deploy_sha",
                  "failed_candidate_sha", "failed_expected_image_id", "failed_run_id",
                  "marker", "containers", "barrier", "barrier_snapshot_stable",
                  "quiescence", "book", "cold", "scheduler", "timers",
                  "unreferenced_images", "unreferenced_total",
                  "unreferenced_redacted_count",
                  "runtime_running_verified", "recovery_authorized",
                  "paper_admission_authorized"}):
            return fail
        a, b = (_utc(obj.get(k)) for k in ("generated_at_utc", "generated_at_art"))
        if (a is None or b is None or abs((a-b).total_seconds()) > 1 or
                abs((a-datetime.now(timezone.utc)).total_seconds()) > 300):
            return fail
        if (datetime.fromisoformat(obj["generated_at_utc"]).utcoffset() != timedelta(0) or
                datetime.fromisoformat(obj["generated_at_art"]).utcoffset() !=
                timedelta(hours=-3)):
            return fail
        if (not isinstance(obj["containers"], list) or
                [row.get("name") for row in obj["containers"]] != list(OWNED) or
                not isinstance(obj["timers"], list) or
                [row.get("name") for row in obj["timers"]] != list(TIMERS) or
                type(obj["barrier_snapshot_stable"]) is not bool or
                type(obj["unreferenced_total"]) is not int or
                not -1 <= obj["unreferenced_total"] <= 128 or
                type(obj["unreferenced_redacted_count"]) is not int or
                not -1 <= obj["unreferenced_redacted_count"] <= 128 or
                (obj["unreferenced_total"] >= 0 and
                 obj["unreferenced_redacted_count"] !=
                 obj["unreferenced_total"] - len(obj["unreferenced_images"])) or
                not isinstance(obj["unreferenced_images"], list) or
                len(obj["unreferenced_images"]) > 16 or
                not all(type(row) is dict and IMAGE.fullmatch(row.get("image_id", ""))
                        for row in obj["unreferenced_images"])):
            return fail
        for name in ("marker", "barrier", "quiescence", "book", "cold", "scheduler"):
            if type(obj[name]) is not dict or obj[name].get("state") not in {
                    "NO_VERIFICADO", "OBSERVED", "ABSENT", "READ_ONLY_OBSERVED"}:
                return fail
        allowed = {
            "marker": {"state", "relation", "deploy_sha", "candidate_sha",
                       "image_id", "validation_status", "paper_zero",
                       "real_routes_not_called"},
            "barrier": {"state", "relation", "phase", "status", "flock_idle"},
            "quiescence": {"state", "candidate_matches", "status",
                           "installation_started", "inflight", "paper_zero",
                           "watch_untouched", "paused_postclose",
                           "intraday_caucion", "pending_caucion",
                           "market_open_suspended", "open_paper_carryover",
                           "spot_carryover_absent", "caucion_carryover_absent",
                           "closed_flat_flags", "stop_evidence_status_consistent",
                           "receipt_sha256"},
            "book": {"state", "mode_paper", "orders_zero", "process_state",
                     "session_state", "spot_open", "caucion_open",
                     "futures_active", "fci_not_redeemed", "exposure_flat"},
            "cold": {"state", "current_generation", "current_snapshot",
                     "live_status", "current_cut_hash_bound",
                     "current_cut_paper_zero", "live_paper_zero",
                     "live_routes_not_called", "failed_queue_state",
                     "failed_queue_bound", "pack_metadata",
                     "historical_cold2_pack_metadata_cause", "pack_hash_verified",
                     "failed_computed_report_hash_verified",
                     "successor_distinct_from_revoked", "successor_predicate",
                     "original_revoked_cut_absent",
                     "historical_cold2_revoked_cut_absent"},
            "scheduler": {"state", "receipt_status", "candidate_matches",
                          "attempted_count", "suspended_timer_count",
                          "preserved_iol_guard", "iol_timer_active",
                          "iol_activation_after_suspend",
                          "unexpected_active_timers", "receipt_sha256"},
        }
        if any(set(obj[k]) != allowed[k] for k in allowed):
            return fail
        def optional_pattern(value, pattern):
            return value is None or (type(value) is str and
                                     pattern.fullmatch(value) is not None)
        def optional_bool(value):
            return value is None or type(value) is bool
        def optional_enum(value, choices):
            return value is None or type(value) is str and value in choices
        def optional_count(value):
            return value is None or (type(value) is int and 0 <= value <= 1_000_000)
        marker = obj["marker"]
        if (marker["relation"] not in {"UNKNOWN", "R1_LAST_GO", "FAILED_R2",
                                       "OTHER_EXACT_SHA"} or
                not optional_pattern(marker["candidate_sha"], HEX40) or
                not optional_pattern(marker["deploy_sha"], HEX40) or
                not optional_pattern(marker["image_id"], IMAGE) or
                not optional_enum(marker["validation_status"],
                    {"VALIDATED_RUNTIME", "DEPLOYED_VALIDATION_PENDING"}) or
                not optional_bool(marker["paper_zero"]) or
                not optional_bool(marker["real_routes_not_called"])):
            return fail
        phases = {"reconcile", "classify", "offline_preclean", "preclean",
                  "suspend", "drain", "transfer", "promotion", "flat_book",
                  "verify_carryover", "timers_restore", "timers_restored",
                  "backup_retire", "finalize", "cleanup_contract",
                  "cleanup_recover", "cleanup_admin_resume", "cleanup_restore",
                  "cleanup_payload", "cleanup_final", "cleanup_presuspend"}
        barrier = obj["barrier"]
        if (barrier["relation"] not in
                {"NO_VERIFICADO", "ABSENT", "FAILED_R2_ATTEMPT", "OTHER_ATTEMPT"} or
                not optional_enum(barrier["phase"], phases) or
                not optional_enum(barrier["status"],
                    {"ABSENT", "IDLE", "INFLIGHT", "CONTRACT_ONLY"}) or
                not optional_bool(barrier["flock_idle"])):
            return fail
        quiescence = obj["quiescence"]
        quiescence_bools = (
            "candidate_matches", "installation_started", "paper_zero",
            "watch_untouched", "paused_postclose", "intraday_caucion",
            "pending_caucion", "market_open_suspended",
            "open_paper_carryover", "spot_carryover_absent",
            "caucion_carryover_absent", "closed_flat_flags",
            "stop_evidence_status_consistent")
        if (not optional_enum(quiescence["status"],
                    {"DRAINED", "HOST_INSTALLATION_STARTED",
                     "FIX_FORWARD_VALIDATION_REQUIRED", "ADMIN_DRAINED_PENDING",
                     "IN_PROGRESS", "PREPARED", "NO_VERIFICADO"}) or
                not optional_enum(quiescence["inflight"],
                    {"ABSENT_OWNED_RUNTIME_STOPPED", "PRESENT", "NO_VERIFICADO"}) or
                not optional_pattern(quiescence["receipt_sha256"], HEX64) or
                any(not optional_bool(quiescence[k]) for k in quiescence_bools)):
            return fail
        book = obj["book"]
        if (not optional_enum(book["process_state"],
                {"RUNNING", "STOPPED", "WAITING_MARKET", "DEGRADED", "STARTING"}) or
                not optional_enum(book["session_state"],
                {"CLOSED", "MARKET_CLOSED", "OPEN", "MARKET_OPEN",
                 "BOOT_CALENDAR", "CHECKING"}) or
                any(not optional_bool(book[k]) for k in
                    ("mode_paper", "orders_zero", "exposure_flat")) or
                any(not optional_count(book[k]) for k in
                    ("spot_open", "caucion_open", "futures_active",
                     "fci_not_redeemed"))):
            return fail
        cold = obj["cold"]
        pack_causes = {"NO_VERIFICADO", "PACK_TYPE_LINK_MODE", "PACK_OWNER",
                       "PACK_BYTES", "IDENTITY_SHAPE", "PACK_DEV_INO",
                       "PACK_IDENTITY_SIZE", "PACK_MTIME", "PACK_CTIME",
                       "METADATA_BOUND_NOT_HASHED", "PACK_UNVERIFIED"}
        historical_causes = pack_causes | {
            "ABSENT", "ARCHIVE_DIRECTORY_UNSAFE", "ARCHIVE_NAMESPACE_NOT_EXACT",
            "HISTORICAL_SMALL_RECEIPT_NOT_BOUND"}
        successor_classes = {"NO_VERIFICADO", "REVOKED_OR_UNIDENTIFIED",
                             "NO_CURRENT_FAILED_QUEUE", "QUEUE_NOT_BOUND",
                             "MANIFEST_RECEIPT_NOT_BOUND",
                             "PACK_METADATA_REJECTED",
                             "NO_VERIFICADO_PACK_AND_REPORT_HASH_REQUIRED"}
        cold_bools = (
            "current_cut_hash_bound", "current_cut_paper_zero",
            "live_paper_zero", "live_routes_not_called",
            "failed_queue_bound", "pack_hash_verified",
            "failed_computed_report_hash_verified",
            "successor_distinct_from_revoked",
            "original_revoked_cut_absent",
            "historical_cold2_revoked_cut_absent")
        if (not optional_pattern(cold["current_generation"], HEX64) or
                not optional_pattern(cold["current_snapshot"], HEX32) or
                not optional_enum(cold["live_status"],
                    {"FAIL_CLOSED", "SHADOW_OBSERVING", "OTHER"}) or
                not optional_enum(cold["failed_queue_state"],
                    {"FAILED", "PENDING", "COMPLETE", "OTHER"}) or
                cold["pack_metadata"] not in pack_causes or
                cold["historical_cold2_pack_metadata_cause"] not in historical_causes or
                cold["successor_predicate"] not in successor_classes or
                any(not optional_bool(cold[k]) for k in cold_bools) or
                cold["pack_hash_verified"] is not False or
                cold["failed_computed_report_hash_verified"] is not False):
            return fail
        scheduler = obj["scheduler"]
        unexpected = scheduler["unexpected_active_timers"]
        if (not optional_enum(scheduler["receipt_status"],
                    {"SUSPENDED", "SUSPENDING", "RESTORED", "OTHER"}) or
                not optional_pattern(scheduler["receipt_sha256"], HEX64) or
                any(not optional_bool(scheduler[k]) for k in
                    ("candidate_matches", "preserved_iol_guard",
                     "iol_timer_active", "iol_activation_after_suspend")) or
                any(not optional_count(scheduler[k]) for k in
                    ("attempted_count", "suspended_timer_count")) or
                type(unexpected) is not list or len(set(unexpected)) != len(unexpected) or
                any(type(name) is not str or name not in TIMERS
                    for name in unexpected)):
            return fail
        container_fields = {"name", "state", "running", "health", "image_id",
                            "image_matches_marker", "image_matches_r2_input"}
        for row in obj["containers"]:
            if (type(row) is not dict or set(row) != container_fields or
                    row["name"] not in OWNED or
                    row["state"] not in {"NO_VERIFICADO", "RUNNING", "EXITED",
                                         "CREATED", "PAUSED", "RESTARTING", "DEAD"} or
                    row["health"] not in {"NO_VERIFICADO", "HEALTHY", "UNHEALTHY",
                                          "STARTING", "ABSENT"} or
                    not optional_pattern(row["image_id"], IMAGE) or
                    any(not optional_bool(row[k]) for k in
                        ("running", "image_matches_marker",
                         "image_matches_r2_input"))):
                return fail
        timer_fields = {"name", "active", "enabled", "entered_at"}
        stamp_re = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} "
                              r"[0-9]{2}:[0-9]{2}:[0-9]{2} UTC")
        for row in obj["timers"]:
            if (type(row) is not dict or set(row) != timer_fields or
                    row["name"] not in TIMERS or
                    row["active"] not in {"NO_VERIFICADO", "ACTIVE", "INACTIVE",
                                          "FAILED", "ACTIVATING", "DEACTIVATING"} or
                    row["enabled"] not in {"NO_VERIFICADO", "ENABLED", "DISABLED",
                                           "STATIC", "MASKED"} or
                    not optional_pattern(row["entered_at"], stamp_re)):
                return fail
        image_fields = {"image_id", "size_upper_bytes", "tags_allowlisted",
                        "digests_allowlisted", "noncandidate_alias_count",
                        "nonporota_digest_count", "class",
                        "referenced_by_container"}
        for row in obj["unreferenced_images"]:
            if (set(row) != image_fields or
                    row["image_id"] != expected["failed_expected_image_id"] or
                    type(row["size_upper_bytes"]) is not int or
                    not 0 <= row["size_upper_bytes"] < 10**12 or
                    type(row["noncandidate_alias_count"]) is not int or
                    type(row["nonporota_digest_count"]) is not int or
                    row["noncandidate_alias_count"] < 0 or
                    row["nonporota_digest_count"] < 0 or
                    row["referenced_by_container"] is not False or
                    row["class"] != "CANDIDATE_METADATA_ONLY_NOT_CLEANUP_GO" or
                    type(row["tags_allowlisted"]) is not list or
                    type(row["digests_allowlisted"]) is not list or
                    len(row["tags_allowlisted"]) > 8 or
                    len(row["digests_allowlisted"]) > 8 or
                    not row["tags_allowlisted"] or
                    not row["digests_allowlisted"] or
                    any(type(tag) is not str or not CANDIDATE_TAG.fullmatch(tag)
                        or not tag.endswith(expected["failed_candidate_sha"])
                        for tag in row["tags_allowlisted"]) or
                    any(type(dig) is not str or not POROTA_DIGEST.fullmatch(dig)
                        for dig in row["digests_allowlisted"]) or
                    not {dig.split("@", 1)[0]
                         for dig in row["digests_allowlisted"]} <= {
                         tag.split(":", 1)[0]
                         for tag in row["tags_allowlisted"]}):
                return fail
        # Guard against accidentally publishing arbitrary paths/strings or
        # provider payloads through future source changes. Only expected keys
        # and bounded allowlisted scalar shapes are accepted.
        def shape(value, depth=0):
            if depth > 7:
                return False
            if type(value) is dict:
                return (len(value) <= 32 and all(type(k) is str and
                        re.fullmatch(r"[a-z0-9_]{1,48}", k) and shape(v, depth+1)
                        for k,v in value.items()))
            if type(value) is list:
                return len(value) <= 32 and all(shape(v, depth+1) for v in value)
            return value is None or type(value) in (bool, int) and abs(value) < 10**13 or (
                type(value) is str and len(value) <= 128 and
                re.fullmatch(r"[A-Za-z0-9_:.+/@\- ]*", value) is not None)
        if not shape(obj):
            return fail
        # Never pass the received JSON object straight to the public artifact.
        # Reconstruct each subtree from an exact, already-validated schema.
        clean = {k: obj[k] for k in (
            "schema", "state", "read_only", "mount_ro_verified", "mutations",
            "provider_gets", "orders_called", "ppi_watch_touched",
            "generated_at_utc", "generated_at_art",
            "expected_previous_deploy_sha", "failed_deploy_sha",
            "failed_candidate_sha", "failed_expected_image_id", "failed_run_id",
            "barrier_snapshot_stable", "unreferenced_total",
            "unreferenced_redacted_count", "runtime_running_verified",
            "recovery_authorized", "paper_admission_authorized")}
        for key in allowed:
            clean[key] = {field: obj[key][field] for field in sorted(allowed[key])}
        clean["containers"] = [
            {field: row[field] for field in sorted(container_fields)}
            for row in obj["containers"]]
        clean["timers"] = [
            {field: row[field] for field in sorted(timer_fields)}
            for row in obj["timers"]]
        clean["unreferenced_images"] = [
            {field: row[field] for field in sorted(image_fields)}
            for row in obj["unreferenced_images"]]
        return clean
    except (ValueError, KeyError, TypeError, UnicodeError, OverflowError):
        return fail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", action="store_true")
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    expected = {
        "expected_previous_deploy_sha": os.getenv("RC6_RO_PREVIOUS_SHA", ""),
        "failed_deploy_sha": os.getenv("RC6_RO_FAILED_DEPLOY_SHA", ""),
        "failed_candidate_sha": os.getenv("RC6_RO_FAILED_CANDIDATE_SHA", ""),
        "failed_expected_image_id": os.getenv("RC6_RO_FAILED_IMAGE_ID", ""),
        "failed_run_id": os.getenv("RC6_RO_FAILED_RUN_ID", ""),
    }
    if args.host == args.sanitize:
        return 2
    if args.host:
        try:
            result = observe(
                root=ROOT, previous=expected["expected_previous_deploy_sha"],
                failed_deploy=expected["failed_deploy_sha"],
                candidate=expected["failed_candidate_sha"],
                expected_image=expected["failed_expected_image_id"],
                run_id=expected["failed_run_id"])
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0 if result["state"] == "DIAGNOSTIC_ONLY" else 2
        except (OSError, ValueError, Hold, TypeError, KeyError):
            print(json.dumps({"schema": SCHEMA, "state": "NO_VERIFICADO",
                "reason": "HOST_SNAPSHOT_UNAVAILABLE", "read_only": True,
                "mutations": 0, "provider_gets": 0, "orders_called": False,
                "ppi_watch_touched": False}, sort_keys=True))
            return 2
    # Runner input is private: only a validated and sanitized allowlist can
    # reach stdout, Actions summary or the uploaded public artifact.
    raw = sys.stdin.read(32769)
    result = sanitize(raw, expected)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["state"] == "DIAGNOSTIC_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())

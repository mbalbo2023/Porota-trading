#!/usr/bin/env python3
"""A727 staged, exact-candidate-only Docker image recovery after failed R2.

The script is streamed from a reviewed GitHub Actions checkout; it does not
write itself to the Droplet. `plan` never mutates the host. `apply` requires the
exact SHA256 of a previously observed plan, the global host mutex, a closed
PAPER/0 ledger and unchanged Docker/container/PPI Watch evidence. It can only
remove tags, digests and an image ID for an *unreferenced* pinned candidate.

Neither stage touches volumes, SQLite, backups, caches, apt/journal, systemd,
PPI endpoints, PPI Watch, or any container (including stopped containers).
"""

from __future__ import annotations

import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys


SCHEMA = "rc6.a727.candidate-image-gc.v1"
ROOT = Path("/opt/porota-trading")
LOCK = Path("/run/lock/porota-rc6-deploy-v2.lock")
BARRIER = Path("/run/lock/porota-rc6-deploy-v2-attempt.json")
DB = ROOT / "data/paper_v17/observer_v17.db"
DEPLOY = ROOT / "data/deploy"
OWNED = ("porota_production_observer", "porota_production_dashboard",
         "porota_critical_approval_rc6")
STABLE = "porota-trading-bot:17.0.0-rc6"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
CID = re.compile(r"[0-9a-f]{12,64}")
TAG = re.compile(r"(porota-predeploy-v2:|porota-trading-bot:17\.0\.0-rc6-candidate-)([0-9a-f]{40})")
DIGEST = re.compile(r"(porota-predeploy-v2|porota-trading-bot)@sha256:([0-9a-f]{64})")


class Hold(ValueError):
    """Fail closed, without publishing Docker's untrusted stdout/stderr."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _free_inodes() -> int:
    st = os.statvfs("/")
    if st.f_files < 1 or not 0 <= st.f_favail <= st.f_files:
        raise Hold("INODE_INVENTORY_UNPROVEN")
    return int(st.f_favail)


def _file_json(path: Path, *, owner_root: bool = False) -> tuple[dict, str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size < 1 or before.st_size > 65536 or
                owner_root and (before.st_uid != 0 or stat.S_IMODE(before.st_mode) != 0o600)):
            raise Hold("FILE_IDENTITY_UNPROVEN")
        raw = os.read(fd, 65537)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or
                (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)):
            raise Hold("FILE_CHANGED")
        data = json.loads(raw)
        if type(data) is not dict:
            raise Hold("JSON_SHAPE")
        return data, hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _docker(*args: str) -> str:
    cp = subprocess.run(["docker", *args], capture_output=True, text=True,
                        check=False, timeout=12)
    if cp.returncode or len(cp.stdout) > 131072:
        raise Hold("DOCKER_OPERATION_FAILED")
    return cp.stdout.strip()


def _image_details(identity: str) -> dict:
    fields = _docker("image", "inspect", "--format",
                     "{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .Size}}",
                     identity).split("|")
    if len(fields) != 4:
        raise Hold("IMAGE_FIELDS_INVALID")
    iid, tags, digests, size = map(json.loads, fields)
    if (iid != identity or not IMAGE_ID.fullmatch(iid) or
            tags is not None and type(tags) is not list or
            digests is not None and type(digests) is not list or
            type(size) is not int or size < 0 or size > 10**12):
        raise Hold("IMAGE_METADATA_INVALID")
    tags, digests = tags or [], digests or []
    if (len(tags) > 16 or len(digests) > 16 or
            any(type(x) is not str or len(x) > 255 for x in tags + digests) or
            len(tags) != len(set(tags)) or len(digests) != len(set(digests))):
        raise Hold("IMAGE_ALIAS_BOUNDS")
    return {"id": iid, "tags": sorted(tags), "digests": sorted(digests), "size_upper": size}


def _docker_inventory() -> tuple[set[str], list[str]]:
    cids = _docker("ps", "-aq", "--no-trunc").splitlines()
    images = _docker("image", "ls", "-aq", "--no-trunc").splitlines()
    if (len(cids) > 128 or len(images) > 128 or
            any(not CID.fullmatch(cid) for cid in cids) or
            any(not IMAGE_ID.fullmatch(iid) for iid in images)):
        raise Hold("DOCKER_INVENTORY_BOUND")
    references = set()
    for cid in cids:  # Includes STOPPED and third-party containers.
        image = _docker("inspect", "--format", "{{.Image}}", cid)
        if not IMAGE_ID.fullmatch(image):
            raise Hold("CONTAINER_REFERENCE_INVALID")
        references.add(image)
    return references, sorted(set(images))


def _container(name: str) -> dict:
    output = _docker("inspect", "--format",
                     "{{json .Id}}|{{json .Image}}|{{json .State.Status}}|"
                     "{{json .State.ExitCode}}|{{json .State.OOMKilled}}|"
                     "{{json .RestartCount}}|"
                     '{{if .State.Health}}{{json .State.Health.Status}}{{else}}"ABSENT"{{end}}',
                     name)
    fields = output.split("|")
    if len(fields) != 7:
        raise Hold("CONTAINER_FIELDS_INVALID")
    cid, iid, state, exit_code, oom, restarts, health = map(json.loads, fields)
    if (not isinstance(cid, str) or not CID.fullmatch(cid) or
            not isinstance(iid, str) or not IMAGE_ID.fullmatch(iid) or
            state not in {"running", "exited"} or type(exit_code) is not int or
            type(oom) is not bool or type(restarts) is not int or
            health not in {"ABSENT", "healthy", "unhealthy", "starting"}):
        raise Hold("CONTAINER_FIELDS_UNPROVEN")
    return {"id": cid, "image": iid, "state": state, "exit_code": exit_code,
            "oom": oom, "restarts": restarts, "health": health}


def _book() -> dict:
    if DB.is_symlink() or not DB.is_file():
        raise Hold("BOOK_FILE_UNPROVEN")
    # Only a read-only SQLite connection. No write PRAGMAs, VACUUM, WAL delete.
    with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=200")
        row = db.execute("SELECT mode,real_orders_sent,process_state,session_state "
                         "FROM observer_state WHERE id=1").fetchone()
        if (row is None or row[0] != "PRODUCTION_PAPER" or
                type(row[1]) is not int or row[1] != 0 or
                row[2] not in {"STOPPED", "WAITING_MARKET"} or
                row[3] not in {"CLOSED", "MARKET_CLOSED"}):
            raise Hold("BOOK_NOT_CLOSED_PAPER_ZERO")
        counts = {}
        for field, table, where in (
                ("spot", "paper_positions", "status='OPEN'"),
                ("caucion", "paper_cauciones", "status='OPEN'"),
                ("futures", "paper_future_positions", "status='ACTIVE'"),
                ("fci", "paper_family_lifecycle", "family='FCI' AND state!='REDEEMED'")):
            n = db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
            if type(n) is not int or n != 0:
                raise Hold("BOOK_EXPOSURE_NONZERO_OR_UNPROVEN")
            counts[field] = n
    return {"mode": row[0], "orders": row[1], "process": row[2],
            "session": row[3], "exposure": counts}


def _watch() -> dict[str, tuple[str, str]]:
    cmd = subprocess.run(["systemctl", "list-unit-files", "--no-legend"],
                         capture_output=True, text=True, check=False, timeout=7)
    if cmd.returncode or len(cmd.stdout) > 131072:
        raise Hold("WATCH_UNIT_INVENTORY_UNAVAILABLE")
    names = sorted({row.split()[0] for row in cmd.stdout.splitlines()
                    if row.split() and "ppi" in row.split()[0].lower() and
                    "watch" in row.split()[0].lower()})
    if len(names) > 16:
        raise Hold("WATCH_UNIT_INVENTORY_BOUND")
    states = {}
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9@_.-]{1,128}", name):
            raise Hold("WATCH_UNIT_INVALID")
        results = []
        for kind in ("is-active", "is-enabled"):
            p = subprocess.run(["systemctl", kind, name], capture_output=True,
                               text=True, check=False, timeout=5)
            value = p.stdout.strip()
            if len(value) > 32 or not re.fullmatch("[a-z-]{2,32}", value):
                raise Hold("WATCH_STATUS_INVALID")
            results.append(value)
        states[name] = tuple(results)
    return states


def _admission(candidate: str, failed: str, run_id: str, previous: str) -> dict:
    barrier, barrier_hash = _file_json(BARRIER, owner_root=True)
    if (barrier.get("candidate") != failed or str(barrier.get("run")) != run_id or
            barrier.get("status") != "IDLE" or
            set(barrier) != {"candidate", "run", "attempt", "phase", "status"}):
        raise Hold("FAILED_ATTEMPT_BARRIER_NOT_IDLE")
    marker, marker_hash = _file_json(DEPLOY / "CURRENT_STATE_V2.json")
    if (marker.get("deploy_sha") != previous or
            marker.get("validation_status") != "VALIDATED_RUNTIME" or
            marker.get("mode") != "PRODUCTION_PAPER" or
            type(marker.get("real_orders_sent")) is not int or
            marker.get("real_orders_sent") != 0 or
            marker.get("real_order_routes") != "NOT_CALLED" or
            not IMAGE_ID.fullmatch(str(marker.get("image_id", "")))):
        raise Hold("LAST_VALIDATED_RUNTIME_MARKER_INVALID")
    stable = _docker("image", "ls", "-q", "--no-trunc", STABLE).splitlines()
    if stable != [marker["image_id"]]:
        raise Hold("STABLE_IMAGE_MARKER_MISMATCH")
    containers = {name: _container(name) for name in OWNED}
    for name in OWNED:
        row = containers[name]
        if row["image"] != marker["image_id"] or row["oom"] or row["restarts"] != 0:
            raise Hold("PROTECTED_RUNTIME_IDENTITY_MISMATCH")
    for name in OWNED[:2]:
        row = containers[name]
        if row["state"] != "exited" or row["exit_code"] != 0:
            raise Hold("STOPPED_RUNTIME_STATE_MISMATCH")
    critical = containers[OWNED[2]]
    if critical["state"] != "running" or critical["health"] != "healthy":
        raise Hold("CRITICAL_APPROVAL_NOT_HEALTHY")
    quiescence, qhash = _file_json(DEPLOY / ("quiescence-" + failed + ".json"))
    if (quiescence.get("candidate_sha") != failed or
            quiescence.get("status") != "FIX_FORWARD_VALIDATION_REQUIRED" or
            quiescence.get("installation_started") is not True or
            quiescence.get("inflight") != "ABSENT_OWNED_RUNTIME_STOPPED" or
            type(quiescence.get("real_orders_sent")) is not int or
            quiescence["real_orders_sent"] != 0 or
            quiescence.get("ppi_watch_accessed") is not False or
            set(quiescence.get("containers", {})) != set(OWNED[:2]) or
            set(quiescence.get("stop_evidence", {})) != set(OWNED[:2])):
        raise Hold("QUIESCENCE_RECEIPT_UNPROVEN")
    for name in OWNED[:2]:
        identity = quiescence["containers"][name]
        proof = quiescence["stop_evidence"][name]
        after = proof.get("after") if isinstance(proof, dict) else None
        if (not isinstance(identity, dict) or
                identity.get("id") != containers[name]["id"] or
                identity.get("image") != containers[name]["image"] or
                not isinstance(after, dict) or after.get("Status") != "exited" or
                type(after.get("ExitCode")) is not int or after["ExitCode"] != 0 or
                after.get("OOMKilled") is not False or
                proof.get("classification") not in
                {"CLEAN_EXIT", "INHERITED_OOM_FLAG_CLEAN_EXIT", "CANDIDATE_CLEAN_EXIT"}):
            raise Hold("QUIESCENCE_STOP_IDENTITY_NOT_PROVEN")
    # APPLY always requires an independently readable closed-flat PAPER book.
    # PLAN separately inventories Docker even when that proof is unavailable.
    try:
        book = {"state": "VERIFIED_CLOSED_FLAT", "value": _book()}
    except sqlite3.Error as exc:
        raise Hold("BOOK_READONLY_UNAVAILABLE") from exc
    return {"barrier_hash": barrier_hash, "marker_hash": marker_hash,
            "quiescence_hash": qhash, "containers": containers,
            "book": book, "watch": _watch(), "candidate": candidate}


def _candidate_owner(image: dict) -> str | None:
    tags, digests = image["tags"], image["digests"]
    # A tag from ANY other candidate or foreign application is a hard NO_GO.
    candidates = []
    for value in tags:
        match = TAG.fullmatch(value)
        if match is None:
            return None
        candidates.append(match.group(2))
    if not candidates or len(set(candidates)) != 1:
        return None
    repos = {t.rsplit(":", 1)[0] for t in tags}
    if not all((m := DIGEST.fullmatch(d)) is not None and m.group(1) in repos
               for d in digests):
        return None
    return candidates[0]


def _target(image: dict, candidate: str) -> bool:
    return _candidate_owner(image) == candidate


APPLY_HOLDS = frozenset({"NONE", "BOOK_UNVERIFIED", "MARKER_UNVERIFIED",
                         "STOP_UNVERIFIED", "BARRIER_UNVERIFIED",
                         "RUNTIME_UNVERIFIED", "WATCH_UNVERIFIED",
                         "OTHER_UNVERIFIED"})


def _classify_apply_hold(exc: Exception) -> str:
    """Closed enum only: no raw path, SQLite, Docker or provider output."""
    code = str(exc) if isinstance(exc, Hold) else ""
    if code.startswith("BOOK_") or isinstance(exc, sqlite3.Error):
        return "BOOK_UNVERIFIED"
    if code in {"LAST_VALIDATED_RUNTIME_MARKER_INVALID", "STABLE_IMAGE_MARKER_MISMATCH"}:
        return "MARKER_UNVERIFIED"
    if code in {"QUIESCENCE_RECEIPT_UNPROVEN", "QUIESCENCE_STOP_IDENTITY_NOT_PROVEN",
                "STOPPED_RUNTIME_STATE_MISMATCH"}:
        return "STOP_UNVERIFIED"
    if code in {"FAILED_ATTEMPT_BARRIER_NOT_IDLE"}:
        return "BARRIER_UNVERIFIED"
    if code.startswith("WATCH_"):
        return "WATCH_UNVERIFIED"
    if code in {"PROTECTED_RUNTIME_IDENTITY_MISMATCH", "CRITICAL_APPROVAL_NOT_HEALTHY",
                "CONTAINER_FIELDS_INVALID", "CONTAINER_FIELDS_UNPROVEN"}:
        return "RUNTIME_UNVERIFIED"
    return "OTHER_UNVERIFIED"


def _probe_apply(candidate: str, failed: str, run_id: str, previous: str) -> tuple[dict | None, str]:
    """No mutation. Failure merely labels APPLY as held; PLAN still returns."""
    try:
        return _admission(candidate, failed, run_id, previous), "NONE"
    except (OSError, Hold, ValueError, sqlite3.Error, subprocess.TimeoutExpired,
            subprocess.SubprocessError) as exc:
        return None, _classify_apply_hold(exc)


def make_plan(candidate: str, failed: str, run_id: str, previous: str) -> dict:
    # Crucial separation: exact Docker inventory is independent of ledger,
    # marker and quiescence. These are APPLY-only proof obligations.
    references, image_ids = _docker_inventory()
    selected = []
    discovered = set()
    for identity in image_ids:
        if identity in references:
            continue
        details = _image_details(identity)
        owner = _candidate_owner(details)
        if owner is not None:
            discovered.add(owner)
        if owner == candidate:
            selected.append(details)
    if len(selected) > 8 or len(discovered) > 8:
        raise Hold("CANDIDATE_IMAGE_LIMIT")
    safety, apply_hold = _probe_apply(candidate, failed, run_id, previous)
    if safety is not None and safety["containers"][OWNED[2]]["image"] not in references:
        safety, apply_hold = None, "RUNTIME_UNVERIFIED"
    # No Docker objects from non-candidate identities enter the plan.
    plan = {"schema": SCHEMA, "candidate_sha": candidate,
            "failed_candidate_sha": failed, "failed_run_id": run_id,
            "previous_deploy_sha": previous,
            "safety": safety,
            "apply_hold": apply_hold,
            "discovered_candidate_shas": sorted(discovered),
            "targets": sorted(selected, key=lambda x: x["id"])}
    plan["plan_sha256"] = _sha(plan)
    return plan


def perform(*, mode: str, candidate: str, failed: str, run_id: str,
            previous: str, expected_digest: str = "") -> dict:
    if (mode not in {"plan", "apply"} or
            any(not HEX40.fullmatch(v) for v in (candidate, failed, previous)) or
            not re.fullmatch("[1-9][0-9]*", run_id) or
            mode == "apply" and not HEX64.fullmatch(expected_digest)):
        raise Hold("REQUEST_PINS_INVALID")
    if LOCK.is_symlink():
        raise Hold("HOST_MUTEX_SYMLINK")
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1 or
                stat.S_IMODE(st.st_mode) != 0o600):
            raise Hold("HOST_MUTEX_IDENTITY_UNPROVEN")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Hold("DEPLOY_MUTEX_BUSY") from exc
        plan = make_plan(candidate, failed, run_id, previous)
        targets = plan["targets"]
        disk_before = shutil.disk_usage("/").free
        inodes_before = _free_inodes()
        summary = {"schema": SCHEMA, "mode": mode, "candidate_sha": candidate,
                   "failed_candidate_sha": failed, "failed_run_id": run_id,
                   "plan_sha256": plan["plan_sha256"], "targets": len(targets),
                   "target_manifest": targets,
                   "discovered_candidate_shas": plan["discovered_candidate_shas"],
                   "apply_hold": plan["apply_hold"],
                   "size_upper_bytes": sum(t["size_upper"] for t in targets),
                   "paper_zero_receipts": plan["safety"] is not None,
                   "book_closed_flat": plan["safety"] is not None,
                   "book_state": ("VERIFIED_CLOSED_FLAT" if plan["safety"] is not None
                                  else "NO_VERIFICADO"),
                   "other_apps_touched": False, "ppi_watch_touched": False,
                   "mutations": 0, "removed": 0,
                   "disk_free_before_bytes": disk_before,
                   "disk_free_after_bytes": disk_before,
                   "disk_delta_observed_bytes": 0,
                   "inodes_free_before": inodes_before,
                   "inodes_free_after": inodes_before,
                   "mutation_count_exact": True}
        if mode == "plan":
            summary["state"] = "PLAN_READY" if targets else "NO_TARGETS"
            return summary
        if plan["apply_hold"] != "NONE" or plan["safety"] is None:
            raise Hold("APPLY_ADMISSION_NOT_PROVEN")
        if not targets or plan["plan_sha256"] != expected_digest:
            raise Hold("PLAN_DIGEST_MISMATCH_OR_EMPTY")
        removed = 0
        try:
            for item in targets:
                for alias in [*item["tags"], *item["digests"], item["id"]]:
                    # Recheck before each Docker mutation; no global prune.
                    now = _admission(candidate, failed, run_id, previous)
                    if now != plan["safety"]:
                        raise Hold("PREDELETE_SAFETY_CHANGED")
                    references, ids = _docker_inventory()
                    if item["id"] in references:
                        raise Hold("PREDELETE_CONTAINER_REFERENCE_CHANGED")
                    if item["id"] not in ids:
                        break  # Already fully collected by an earlier untag.
                    current = _image_details(item["id"])
                    if (not set(current["tags"]) <= set(item["tags"]) or
                            not set(current["digests"]) <= set(item["digests"]) or
                            current["size_upper"] != item["size_upper"] or
                            any(not TAG.fullmatch(t) or not t.endswith(candidate)
                                for t in current["tags"]) or
                            any(not DIGEST.fullmatch(d) for d in current["digests"])):
                        raise Hold("PREDELETE_IMAGE_PROVENANCE_CHANGED")
                    if alias == item["id"] or alias in current["tags"] + current["digests"]:
                        _docker("image", "rm", alias)  # Never force.
                        removed += 1
            current = _admission(candidate, failed, run_id, previous)
            if current != plan["safety"]:
                raise Hold("POSTDELETE_SAFETY_CHANGED")
            post_references, post_ids = _docker_inventory()
            if any(item["id"] in post_references or item["id"] in post_ids for item in targets):
                raise Hold("POSTDELETE_TARGET_STILL_PRESENT")
        except (OSError, Hold, ValueError, sqlite3.Error, subprocess.TimeoutExpired,
                subprocess.SubprocessError):
            # Fail closed with a *bounded lower bound* of successful rm calls.
            # A Docker timeout can happen after daemon-side mutation; never
            # claim exact counts or complete reclamation on partial failure.
            disk_after = shutil.disk_usage("/").free
            summary.update(state="NO_GO_PARTIAL", reason="APPLY_INCOMPLETE",
                           mutations=removed, removed=removed,
                           mutation_count_exact=False,
                           disk_free_after_bytes=disk_after,
                           disk_delta_observed_bytes=disk_after - disk_before,
                           inodes_free_after=_free_inodes())
            return summary
        disk_after = shutil.disk_usage("/").free
        summary.update(state="GREEN", mutations=removed, removed=removed,
                       disk_free_after_bytes=disk_after,
                       disk_delta_observed_bytes=disk_after - disk_before,
                       inodes_free_after=_free_inodes())
        return summary
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("plan", "apply"), required=True)
    ap.add_argument("--candidate-sha", required=True)
    ap.add_argument("--failed-candidate-sha", required=True)
    ap.add_argument("--failed-run-id", required=True)
    ap.add_argument("--previous-deploy-sha", required=True)
    ap.add_argument("--expected-plan-sha256", default="")
    args = ap.parse_args(argv)
    try:
        result = perform(mode=args.mode, candidate=args.candidate_sha,
                         failed=args.failed_candidate_sha, run_id=args.failed_run_id,
                         previous=args.previous_deploy_sha,
                         expected_digest=args.expected_plan_sha256)
    except (OSError, Hold, ValueError, sqlite3.Error, subprocess.TimeoutExpired,
            subprocess.SubprocessError) as exc:
        # Never leak Docker stderr, path contents or failed SQL text.
        result = {"schema": SCHEMA, "mode": args.mode, "state": "NO_GO",
                  "reason": str(exc) if isinstance(exc, Hold) else type(exc).__name__,
                  "mutations": 0 if args.mode == "plan" else "NO_VERIFICADO",
                  "other_apps_touched": "NO_VERIFICADO" if args.mode == "apply" else False,
                  "ppi_watch_touched": "NO_VERIFICADO" if args.mode == "apply" else False}
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["state"] in {"PLAN_READY", "NO_TARGETS", "GREEN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

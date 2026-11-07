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
# Bounded diagnostics only. These names are the existing A727 allowlist;
# neither this map nor its output grants deletion rights to any container.
OWNED_ROLE = dict(zip(OWNED, ("OBSERVER", "DASHBOARD", "APPROVAL")))
OWNED_FAILURES = ("TIMEOUT", "CLI", "COUNT", "JSON", "SEMANTICS", "OTHER")
OWNED_HOLDS = frozenset(f"OWNED_{role}_{kind}_UNVERIFIED"
                        for role in OWNED_ROLE.values() for kind in OWNED_FAILURES)
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
    # Inspect ONLY two known OCI provenance labels. Never read or publish
    # arbitrary labels from Porota/Boca/other co-resident Docker images.
    format_fields = ('{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|'
                     '{{json .Size}}|{{json (index .Config.Labels "porota.commit")}}|'
                     '{{json (index .Config.Labels "porota.predeploy")}}')
    fields = _docker("image", "inspect", "--format",
                     format_fields, identity).split("|")
    if len(fields) != 6:
        raise Hold("IMAGE_FIELDS_INVALID")
    iid, tags, digests, size, commit_label, predeploy_label = map(json.loads, fields)
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
    # Private fields are never copied into the sanitized target manifest.
    # Absent/malformed labels simply cannot establish candidate ownership.
    commit = (commit_label if type(commit_label) is str and
              HEX40.fullmatch(commit_label) else None)
    predeploy = "v2" if predeploy_label == "v2" else None
    return {"id": iid, "tags": sorted(tags), "digests": sorted(digests),
            "size_upper": size, "_oci_commit": commit,
            "_oci_predeploy": predeploy}


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


def _stage_read(code: str, reader, *args, **kwargs):
    """Preserve one bounded phase label, never raw host exception text.

    This does not loosen any admission predicate or introduce a new probe:
    each existing read is wrapped at its exact original callsite.
    """
    try:
        return reader(*args, **kwargs)
    except (OSError, Hold, ValueError, TypeError, sqlite3.Error,
            subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        raise Hold(code) from exc


def _owned_container(name: str) -> dict:
    """Identify only allowlisted role and bounded failure category, never host output.

    Retains the existing single Docker inspect per container and the original
    fail-closed admission predicates. Unknown failures cannot escape verbatim.
    """
    role = OWNED_ROLE[name]
    try:
        return _container(name)
    except subprocess.TimeoutExpired:
        kind = "TIMEOUT"
    except json.JSONDecodeError:
        kind = "JSON"
    except Hold as exc:
        kind = {"DOCKER_OPERATION_FAILED": "CLI",
                "CONTAINER_FIELDS_INVALID": "COUNT",
                "CONTAINER_FIELDS_UNPROVEN": "SEMANTICS"}.get(str(exc), "OTHER")
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        kind = "OTHER"
    raise Hold(f"OWNED_{role}_{kind}_UNVERIFIED") from None


def _admission(candidate: str, failed: str, run_id: str, previous: str) -> dict:
    # A reboot can remove the volatile attempt barrier. Record that specific
    # uncertainty; NEVER synthesize IDLE or weaken APPLY admission.
    try:
        barrier, barrier_hash = _file_json(BARRIER, owner_root=True)
    except FileNotFoundError:
        raise Hold("BARRIER_ABSENT_POSTBOOT_UNVERIFIED") from None
    except (OSError, Hold, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise Hold("BARRIER_FILE_UNVERIFIED") from exc
    if (barrier.get("candidate") != failed or str(barrier.get("run")) != run_id or
            barrier.get("status") != "IDLE" or
            set(barrier) != {"candidate", "run", "attempt", "phase", "status"}):
        raise Hold("FAILED_ATTEMPT_BARRIER_NOT_IDLE")
    marker, marker_hash = _stage_read("MARKER_FILE_UNVERIFIED", _file_json,
                                      DEPLOY / "CURRENT_STATE_V2.json")
    if (marker.get("deploy_sha") != previous or
            marker.get("validation_status") != "VALIDATED_RUNTIME" or
            marker.get("mode") != "PRODUCTION_PAPER" or
            type(marker.get("real_orders_sent")) is not int or
            marker.get("real_orders_sent") != 0 or
            marker.get("real_order_routes") != "NOT_CALLED" or
            not IMAGE_ID.fullmatch(str(marker.get("image_id", "")))):
        raise Hold("LAST_VALIDATED_RUNTIME_MARKER_INVALID")
    stable = _stage_read("STABLE_DOCKER_UNVERIFIED", _docker,
                         "image", "ls", "-q", "--no-trunc", STABLE).splitlines()
    if stable != [marker["image_id"]]:
        raise Hold("STABLE_IMAGE_MARKER_MISMATCH")
    containers = {name: _owned_container(name) for name in OWNED}
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
    quiescence, qhash = _stage_read("QUIESCENCE_FILE_UNVERIFIED", _file_json,
                                    DEPLOY / ("quiescence-" + failed + ".json"))
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
    watch = _stage_read("WATCH_READ_UNVERIFIED", _watch)
    return {"barrier_hash": barrier_hash, "marker_hash": marker_hash,
            "quiescence_hash": qhash, "containers": containers,
            "book": book, "watch": watch, "candidate": candidate}


def _tag_claim(image: dict) -> str | None:
    """Classify a candidate-looking alias WITHOUT treating it as ownership."""
    tags, digests = image["tags"], image["digests"]
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


def _candidate_owner(image: dict) -> str | None:
    """Require both alias isolation AND immutable OCI build provenance."""
    claimed = _tag_claim(image)
    if claimed is None:
        return None
    if (image.get("_oci_commit") != claimed or
            image.get("_oci_predeploy") != "v2"):
        return None
    return claimed


def _public_image(image: dict) -> dict:
    """Never emit labels, even the two inspected private provenance fields."""
    return {key: image[key] for key in ("id", "tags", "digests", "size_upper")}


def _target(image: dict, candidate: str) -> bool:
    return _candidate_owner(image) == candidate


APPLY_HOLDS = frozenset({"NONE", "BOOK_UNVERIFIED", "MARKER_UNVERIFIED",
                         "OCI_LABEL_UNVERIFIED", "BARRIER_ABSENT_POSTBOOT_UNVERIFIED",
                         "STOP_UNVERIFIED", "BARRIER_UNVERIFIED",
                         "RUNTIME_UNVERIFIED", "WATCH_UNVERIFIED",
                         "BARRIER_FILE_UNVERIFIED", "MARKER_FILE_UNVERIFIED",
                         "STABLE_DOCKER_UNVERIFIED", "OWNED_DOCKER_UNVERIFIED",
                         "QUIESCENCE_FILE_UNVERIFIED", "WATCH_READ_UNVERIFIED",
                         "FILE_UNVERIFIED", "DOCKER_UNVERIFIED",
                         "OTHER_UNVERIFIED"}) | OWNED_HOLDS


def _classify_apply_hold(exc: Exception) -> str:
    """Closed enum only: no raw path, SQLite, Docker or provider output."""
    code = str(exc) if isinstance(exc, Hold) else ""
    if code in APPLY_HOLDS:
        return code
    if code in {"FILE_IDENTITY_UNPROVEN", "FILE_CHANGED", "JSON_SHAPE"}:
        return "FILE_UNVERIFIED"
    if code in {"DOCKER_OPERATION_FAILED", "IMAGE_FIELDS_INVALID",
                "CONTAINER_REFERENCE_INVALID"} or isinstance(exc, subprocess.TimeoutExpired):
        return "DOCKER_UNVERIFIED"
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
    provenance_unverified = 0
    for identity in image_ids:
        if identity in references:
            continue
        details = _image_details(identity)
        # A candidate-looking tag is not deletion authority. Count only
        # *bounded* mismatches; never expose the label contents or foreign
        # object identities in this diagnostic.
        claimed = _tag_claim(details)
        owner = _candidate_owner(details)
        if claimed is not None:
            discovered.add(claimed)
        if claimed == candidate and owner is None:
            provenance_unverified += 1
        if owner == candidate:
            selected.append(_public_image(details))
    if (len(selected) > 8 or len(discovered) > 8 or
            provenance_unverified > 8):
        raise Hold("CANDIDATE_IMAGE_LIMIT")
    safety, apply_hold = _probe_apply(candidate, failed, run_id, previous)
    if safety is not None and safety["containers"][OWNED[2]]["image"] not in references:
        safety, apply_hold = None, "RUNTIME_UNVERIFIED"
    # Keep the host's real admission cause if already blocked (e.g. absent
    # postboot barrier), while counting independent OCI uncertainty.
    if provenance_unverified:
        if apply_hold == "NONE":
            apply_hold = "OCI_LABEL_UNVERIFIED"
        safety = None
    # No Docker objects or provenance labels of other applications enter plan.
    plan = {"schema": SCHEMA, "candidate_sha": candidate,
            "failed_candidate_sha": failed, "failed_run_id": run_id,
            "previous_deploy_sha": previous,
            "safety": safety,
            "apply_hold": apply_hold,
            "oci_provenance_unverified_count": provenance_unverified,
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
                   "oci_provenance_unverified_count":
                       plan.get("oci_provenance_unverified_count", 0),
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
                    if (current.get("_oci_commit") != candidate or
                            current.get("_oci_predeploy") != "v2" or
                            not set(current["tags"]) <= set(item["tags"]) or
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

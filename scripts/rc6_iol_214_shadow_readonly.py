"""Read a post-recovery SHADOW generation without accessing market data or writing host state."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {"source_sha": "eba76703be86e6a6b1406db36e5cb4cb87b21330",
            "source_tree": "5ac31c4b9cf2c16f477c08a6b5b3424f644a2667",
            "image_id": "sha256:274b143fd323ab0b79aff90890f54a7f5ce0ed84c3323fedd385ce85f28e83d2",
            "attempt": "45921311817-1"}
CIDS = {"observer": "f6440b9039b39f4d3a8b0047d1140b64d7eccadd68e00174fc2f6b2198662a09",
        "dashboard": "0d4e1b7b56bd3621e5448a652cc7e45a1b1652c92e612fdc9e3f9abcf298c774"}
INCIDENT = {"snapshot_id": "ff43ea0325554d1f85c95626c59d1621",
            "manifest_sha256": "9794ef3e462444b15a3ff863329ff9c15a9314d5c86e7265da65d261a3d08bd7",
            "pack_sha256": "dd0718808bd7ce0794d5e1201802f28eec801b391097e04bd2cc5e040910aea2",
            "pack_bytes": 211509986}
PROMOTED_AT = "2026-11-27T01:20:16.498838+00:00"
SCHEMA = "porota.iol.shadow-restored-214.v1"
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
SNAPSHOT = re.compile(r"[a-f0-9]{32}\Z")
PUBLIC_CODE = re.compile(r"(?:SHADOW|GENERATION|COLD2)_[A-Z0-9_]{1,120}\Z")
PHASE = frozenset({"NAMESPACE", "BACKLOG_RECONCILE", "MANIFEST", "PRIOR_RECEIPT", "NATIVE", "INPUT_CHECK",
                   "PACK_HASH", "REPORT_WRITE", "REPORT_VERIFY", "RECEIPT_COMMIT", "QUEUE_COMMIT", "PACK_CLEANUP"})
MODULES = frozenset({"runtime", "worker", "preopen", "promotion", "stages", "families", "lab", "entry_signals",
                     "funnel", "source_reads", "read_contract", "persistence", "cold_audit", "snapshot", "common", "live"})


def clock(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def epoch(value):
    return datetime.fromtimestamp(value, timezone.utc) if type(value) in (float, int) and 0 < value < 4102444800 else None


def iso(stamp):
    return stamp.isoformat() if stamp else None


def match(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def paper_zero(status):
    return (isinstance(status, dict) and type(status.get("real_orders_sent")) is int and
            status["real_orders_sent"] == 0 and status.get("real_routes") == "NOT_CALLED")


def archive_proof(root):
    from scripts.rc6_cold2_quarantine import directory, envelope, regular
    from scripts.porota_rootless_host import no_alias

    archive = no_alias(root / "data/deploy" / ("rc6-failed-cold-" + INCIDENT["snapshot_id"]))
    directory(archive)
    path = archive / "quarantine.json"
    before = regular(path, bound=256 * 1024)
    record = envelope(path)
    pack = regular(archive / (INCIDENT["snapshot_id"] + ".pack"), bound=INCIDENT["pack_bytes"])
    after = path.stat()
    require(before.st_ino == after.st_ino and before.st_mtime_ns == after.st_mtime_ns and
            pack.st_size == INCIDENT["pack_bytes"] and record.get("schema") == "rc6.issue624.cold2.successor.v1" and
            record.get("state") == "QUARANTINED_FAILED" and record.get("old_cut_revoked") is True and
            type(record.get("real_orders_sent")) is int and record["real_orders_sent"] == 0 and
            all(record.get(key) == value for key, value in INCIDENT.items()) and
            match(record.get("revoked_cut"), SHA256), "AUDIT_ARCHIVED_COLD_RECEIPT_UNVERIFIED")
    return {"snapshot_id": INCIDENT["snapshot_id"], "revoked_generation_id": record["revoked_cut"],
            "archive_receipt_at": iso(epoch(after.st_mtime)), "archive_receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def cold_metadata(files):
    from rc6_shadow_runtime.cold_audit import Directory, _manifest

    with Directory(files) as directory:
        queue = directory.read("queue.json")
        if not queue:
            return None, None, None
        ident = match(queue.get("snapshot_id"), SNAPSHOT)
        if not ident or queue.get("state") not in {"PENDING", "FAILED", "COMPLETE"}:
            raise ValueError("SHADOW_COLD_QUEUE_STATE")
        manifest = _manifest(directory, queue)
        receipt = directory.read("receipt-" + ident + ".json")
        if receipt is not None and (receipt.get("snapshot_id") != ident or
                                    receipt.get("manifest_sha256") != queue.get("manifest_sha256")):
            raise ValueError("SHADOW_COLD_MANIFEST_BINDING")
        return queue, manifest, receipt


def inspect_shadow(root, starts, *, now=None):
    from cg_paper_workspace import artifact_root
    from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
    from scripts.porota_rootless_host import DB_RELATIVE

    now = now or datetime.now(timezone.utc)
    files = EvidenceFiles(artifact_root(root / DB_RELATIVE) / "dynamic-shadow", payload_limit=4 * 1024**2)
    archived = archive_proof(root)
    baseline = max(clock(PROMOTED_AT), clock(archived["archive_receipt_at"]))
    reborn = all(baseline < stamp <= now for stamp in starts.values())
    floor = starts["observer"]
    status = files.read("status.json") or {}
    pointer = files.read("CURRENT.json") or {}
    result = {"state": "AWAITING_POST_RESTORE_CUT", "archive": archived, "restored_same_cids": reborn,
              "starts_at": {role: iso(stamp) for role, stamp in starts.items()},
              "observed_at": iso(now), "fresh_cut_not_before": iso(floor),
              "live_status": status.get("status") if status.get("status") in {"SHADOW_OBSERVING", "FAIL_CLOSED"} else None,
              "live_status_at": iso(clock(status.get("as_of"))),
              "current_generation_id": match(pointer.get("generation_id"), SHA256)}
    try:
        queue, manifest, receipt = cold_metadata(files)
    except ValueError as error:
        return {**result, "state": "COLD_METADATA_UNVERIFIED", "reason": match(str(error), PUBLIC_CODE)}
    if queue is not None:
        result["cold"] = {"snapshot_id": queue["snapshot_id"], "queue_state": queue["state"],
                          "receipt_state": receipt.get("state") if receipt and receipt.get("state") in {"COMPLETE", "FAILED"} else None,
                          "source_as_of": iso(clock(manifest.get("source_as_of")))}
    if not reborn:
        return {**result, "state": "POST_ARCHIVE_RESTART_UNVERIFIED"}
    status_at = clock(status.get("as_of"))
    source_at = clock(manifest.get("source_as_of")) if manifest else None
    if status_at is None or not floor <= status_at <= now or not paper_zero(status):
        return result
    if status.get("status") == "FAIL_CLOSED":
        result["state"] = "POST_RESTORE_FAIL_CLOSED"
        module, line = status.get("error_module"), status.get("error_line")
        result["failure"] = {"module": module if isinstance(module, str) and module in MODULES else None,
                             "line": line if type(line) is int and 0 < line <= 10000 else None}
        if (queue and queue["state"] == "FAILED" and receipt and receipt.get("state") == "FAILED" and
                queue["snapshot_id"] != INCIDENT["snapshot_id"] and source_at and floor <= source_at <= status_at):
            phase = receipt.get("error_phase")
            result["failure"].update({"cold_error": match(receipt.get("error_code"), PUBLIC_CODE),
                                      "phase": phase if isinstance(phase, str) and phase in PHASE else None,
                                      "failed_at": iso(epoch(receipt.get("failed_at")))})
        return result
    if not queue or not receipt or queue["state"] != "COMPLETE" or receipt.get("state") != "COMPLETE":
        return {**result, "state": "AWAITING_COMPLETE_COLD_RECEIPT"}
    if status.get("status") != "SHADOW_OBSERVING":
        return result
    try:
        cut = read_committed_generation(files.root, require_current_status=False)
        latest_status, latest_pointer = files.read("status.json"), files.read("CURRENT.json")
        latest_queue, _, _ = cold_metadata(files)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {**result, "state": "CURRENT_CUT_UNVERIFIED", "reason": match(str(error), PUBLIC_CODE)}
    if latest_status != status or latest_pointer != pointer or latest_queue != queue:
        return {**result, "state": "PUBLICATION_IN_PROGRESS"}
    if cut["computed_audit"] != receipt or pointer != cut["pointer"] or (
            pointer.get("generation_id") == archived["revoked_generation_id"] or queue["snapshot_id"] == INCIDENT["snapshot_id"]):
        return {**result, "state": "CURRENT_CUT_COLD_LINK_UNVERIFIED"}
    fields = ("as_of", "status", "report_digest")
    if not all(cut["status"].get(key) == status.get(key) for key in fields):
        return {**result, "state": "CURRENT_CUT_STATUS_UNVERIFIED"}
    published_at = clock(cut.get("publication", {}).get("publish_completed_at"))
    completed_at = epoch(receipt.get("completed_at"))
    if not (source_at and completed_at and published_at and floor <= source_at == status_at <= published_at <= completed_at <= now):
        return {**result, "state": "POST_RESTORE_PUBLICATION_CLOCK_UNVERIFIED"}
    return {**result, "state": "POST_RESTORE_SHADOW_VERIFIED", "cut": {
        "generation_id": pointer["generation_id"], "manifest_sha256": pointer["manifest_sha256"],
        "source_as_of": iso(source_at), "published_at": iso(published_at), "cold_completed_at": iso(completed_at),
        "computed_report_sha256": match(receipt["computed_report"].get("sha256"), SHA256),
        "cold_snapshot_id": queue["snapshot_id"], "cold_manifest_sha256": queue["manifest_sha256"]}}


def observe_installed():
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, require
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host

    root, identity = Path("/opt/porota-trading"), Identity.current()
    with scheduler_lock(identity):
        manager, current, _ = verified_host(root, identity)
        require(all(current.get(key) == value for key, value in EXPECTED.items()) and current.get("roles") == CIDS and
                current.get("market_provider") == "IOL_REST", "AUDIT_INSTALLED_IDENTITY_MISMATCH")
        before = database_snapshot(root / DB_RELATIVE)
        require(before == current.get("database"), "AUDIT_DATABASE_IDENTITY_UNVERIFIED")
        starts = {}
        for role in CIDS:
            observed = manager.check_role(current, role)["State"]
            starts[role] = clock(observed.get("StartedAt"))
            require(observed.get("Running") is True and starts[role], "AUDIT_RUNNING_PAIR_UNVERIFIED")
        observation = inspect_shadow(root, starts)
        require(database_snapshot(root / DB_RELATIVE) == before, "AUDIT_DATABASE_IDENTITY_CHANGED")
        for role, started in starts.items():
            observed = manager.check_role(current, role)["State"]
            require(observed.get("Running") is True and clock(observed.get("StartedAt")) == started,
                    "AUDIT_RUNTIME_CHANGED_DURING_READ")
        return {"schema": SCHEMA, **EXPECTED, "read_only": True,"market_requests_generated": 0,
                "oauth_grants_sent": 0, "real_orders_sent": 0, "ppi_watch_queried": False, **observation}


REMOTE = '''
import hashlib, json, os, re, sys
from pathlib import Path
try:
    assert 0 < os.getuid() == os.geteuid()
    source = Path.home() / '.local/state/porota-rc6/releases' / expected['source_sha'] / 'source'
    assert source.is_dir() and source.stat().st_uid == os.getuid() and all(not p.is_symlink() for p in (source,*source.parents))
    sys.path.insert(0,str(source))
    namespace = {'__name__':'rc6_iol_shadow_restored_readonly','__file__':str(source / 'scripts/rc6_iol_214_shadow_readonly.py')}
    exec(compile(probe_source,namespace['__file__'],'exec'),namespace)
    result = namespace['observe_installed']()
except Exception as exc:
    reason = str(exc)
    result = {'schema':schema, **expected, 'state':'NO_VERIFICADO', 'reason':reason
              if re.fullmatch(r'(?:AUDIT|SHADOW|COLD2|GENERATION)_[A-Z0-9_]{1,120}',reason) else type(exc).__name__}
result['probe_source_sha256'] = hashlib.sha256(probe_source.encode()).hexdigest()
print(json.dumps(result,sort_keys=True,allow_nan=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).read_text(encoding="utf-8")
    payload = (f"expected = {EXPECTED!r}\nprobe_source = {source!r}\nschema = {SCHEMA!r}\n" + REMOTE).encode()
    receipt = {"schema": SCHEMA, **EXPECTED, "state": "NO_VERIFICADO", "reason": "AUDIT_TRANSPORT_UNVERIFIED"}
    try:
        with connection(os.environ) as (ssh, _, _):
            result = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"], data=payload, timeout=40)
        observed = decode_receipt(result.stdout)
        require(result.returncode == 0 and observed.get("schema") == SCHEMA and
                all(observed.get(k) == v for k, v in EXPECTED.items()) and
                observed.get("probe_source_sha256") == hashlib.sha256(source.encode()).hexdigest(),
                "AUDIT_SHADOW_RECEIPT_UNVERIFIED")
        receipt = observed
        return 0 if observed.get("read_only") is True and type(observed.get("real_orders_sent")) is int and observed["real_orders_sent"] == 0 else 1
    except (ValueError, OSError):
        return 1
    finally:
        args.output.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

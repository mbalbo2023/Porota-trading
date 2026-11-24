"""Load one bounded batch of A3 CEM reference closes from an attested RC6 release."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile

from scripts.porota_actions_host import connection, decode_receipt, ssh_run, validate_runtime_receipt


REPOSITORY = "mbalbo2023/Porota-trading"
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
PROMOTE_WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"
SCHEMA = "rc6.history.a3-reference-once.v1"
MAX_RECEIPT = 1_048_576
CONTROL = "a3-reference-promotion.json"

HOST = r"""
import json
import os
from pathlib import Path
import re
import sys

candidate, tree, deployment, image, download_digest = sys.argv[1:]
assert all(re.fullmatch(r"[a-f0-9]{40}", value) for value in (candidate, tree, deployment))
assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
assert re.fullmatch(r"[a-f0-9]{64}", download_digest)
assert os.getuid() == os.geteuid() > 0
root = Path("/opt/porota-trading")
source = Path.home() / ".local/state/porota-rc6/releases" / candidate / "source"
assert source.resolve(strict=True) == source
assert source.stat().st_uid == os.getuid() and not source.stat().st_mode & 0o022
sys.path.insert(0, str(source))
from scripts.porota_rootless_host import Identity
from scripts.porota_rootless_jobs import scheduler_lock, verified_host

identity = Identity.current()
input_code = sys.stdin.buffer.read(16385)
assert 0 < len(input_code) <= 16384
with scheduler_lock(identity):
    marker = root / "data/deploy/CURRENT_STATE_V2.json"
    assert marker.resolve(strict=True) == marker and 0 < marker.stat().st_size < 1_048_576
    release = json.loads(marker.read_bytes())
    assert (release["candidate_sha"], release["candidate_tree_sha"]) == (candidate, tree)
    assert (release["deployment_sha"], release["image_id"]) == (deployment, image)
    assert release["download_receipt_sha256"] == download_digest
    assert release["validation_status"] == "VALIDATED_RUNTIME"
    assert release["mode"] == "PRODUCTION_PAPER" and release["real_order_capability"] == "BLOCKED"
    assert release["ppi_watch_untouched"] is True
    assert type(release["real_orders_sent"]) is int and release["real_orders_sent"] == 0
    manager, current, _ = verified_host(root, identity)
    assert current["source_sha"] == candidate and current["image_id"] == image
    role = "observer"
    command = manager.docker.command_result(
        "container", "exec", "-w", "/app", current["roles"][role],
        "nice", "-n", "10", "python", "-B", "-c", input_code.decode(), timeout=1200,
    )
    assert command.returncode == 0 and 0 < len(command.stdout) <= 1_048_576
    result = json.loads(command.stdout)
    assert result["schema"] == "rc6.history.a3-reference-once.v1"
    assert type(result["real_orders_sent"]) is int and result["real_orders_sent"] == 0
    for role in current["roles"]:
        assert manager.check_role(current, role)["State"]["Running"] is True
result.update(deployment_sha=deployment, candidate_sha=candidate, image_id=image)
print(json.dumps(result))
"""

CONTAINER = r'''
from contextlib import closing
import fcntl
import json
from pathlib import Path
import re
import sqlite3

from cu_history_store_v2_hf6 import A3_REPORTED, SOURCE_REPORTED, source_reported_adjustment
from cv_history_store_adapter_hf6 import default_history_store
from cz_a3_cem_public_history_hf6 import CEMReadOnlyError
from ew_a3_history_rc6 import load_targets, run
from ex_a3_history_job_rc6 import Store

SCHEMA = "rc6.history.a3-reference-once.v1"
BATCH_LIMIT = 40
DATA = Path("/app/data")
PAPER = DATA / "paper_v17/observer_v17.db"
HISTORY = DATA / "market_history.db"
KEY_PART = re.compile(r"[A-Z0-9][A-Z0-9 ./_+$()-]{0,79}")
SESSION_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SAFE_STATES = {
    "COMPLETE", "NOT_DUE", "ALIGNMENT_UNVERIFIED", "EMPTY_OBSERVED", "EMPTY_CONFIRMED", "FAILED",
}
SAFE_ALIGNMENTS = {"EXACT_SOURCE_SYMBOL", "EXACT_DETERMINISTIC"}


def readonly(path):
    assert path.resolve(strict=True) == path and path.is_file()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA query_only=ON")
    return db


def paper_state():
    with closing(readonly(PAPER)) as db:
        row = db.execute("SELECT mode,session_state,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        assert row and row[0] == "PRODUCTION_PAPER" and type(row[2]) is int and row[2] == 0
        return row[1]


def inspect_history(targets, *, quick_check=False):
    with closing(readonly(HISTORY)) as db, db:
        db.execute("BEGIN")
        counts = {}
        for name in ("history_canonical_v2", "history_versions_v2"):
            counts[name] = db.execute(
                f"SELECT COUNT(*) FROM {name} WHERE price_basis='RAW'"
            ).fetchone()[0]
        rows = {}
        for key in targets:
            count, first, last, latest_revision, bad = db.execute(
                """SELECT COUNT(*), MIN(date), MAX(date), COALESCE(MAX(version_id), 0),
                          COALESCE(SUM(CASE WHEN
                            json_valid(metadata_json)
                            AND json_extract(metadata_json, '$.requested_identity') = ?
                            AND json_extract(metadata_json, '$.readiness_implication') = 'NONE'
                            AND length(json_extract(metadata_json, '$.provider_row_sha256')) = 64
                            THEN 0 ELSE 1 END), 0)
                   FROM history_canonical_v2
                   WHERE symbol=? AND instrument_type=? AND market='A3'
                     AND currency='UNKNOWN' AND settlement='UNKNOWN'
                     AND price_basis=? AND adjustment_basis=? AND source=?""",
                (json.dumps(key, separators=(",", ":")), *key[:2], SOURCE_REPORTED,
                 source_reported_adjustment(A3_REPORTED), A3_REPORTED),
            ).fetchone()
            assert type(count) is int and type(latest_revision) is int and type(bad) is int
            assert first is None or SESSION_DATE.fullmatch(first)
            assert last is None or SESSION_DATE.fullmatch(last)
            rows[key] = (count, first, last, latest_revision, bad)
        integrity = None
        if quick_check:
            integrity = "ok" if db.execute("PRAGMA quick_check(1)").fetchall() == [("ok",)] else "failed"
        return counts, rows, integrity


def sanitize_target(event, before, after):
    target = tuple(event.get("target", ()))
    status = event.get("status")
    assert target in before and status in SAFE_STATES
    count, first, last, latest_revision, bad = after[target]
    outcome = {
        "requested_identity": target,
        "canonical_before": before[target][0],
        "canonical_after": count,
        "first_session": first,
        "last_session": last,
        "status": status,
        "revision_advanced": latest_revision > before[target][3],
        "provenance_mismatches": bad,
    }
    if "source_symbol" in event:
        assert KEY_PART.fullmatch(event["source_symbol"])
        assert event["alignment"] in SAFE_ALIGNMENTS
        outcome.update(source_symbol=event["source_symbol"], alignment=event["alignment"])
    if "range" in event:
        assert len(event["range"]) == 2 and all(SESSION_DATE.fullmatch(day) for day in event["range"])
        outcome["requested_range"] = event["range"]
    return outcome


def load_one_batch():
    base = {"schema": SCHEMA, "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
            "source": A3_REPORTED, "price_basis": SOURCE_REPORTED, "execution_allowed": False}
    if paper_state() != "MARKET_CLOSED":
        return {**base, "state": "BLOQUEADO", "reason": "RUNTIME_NOT_MARKET_CLOSED"}

    store = Store(str(PAPER))
    history = default_history_store().for_source_reported_history()
    assert Path(history.path) == HISTORY
    lock_path = DATA / ".rc6-history-cutoff-repair.lock"
    assert not lock_path.is_symlink()
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**base, "state": "BLOQUEADO", "reason": "HISTORY_LOAD_ALREADY_RUNNING"}
        targets = load_targets(store, reference_only=True)
        selected = targets[:BATCH_LIMIT]
        base.update(eligible=len(targets), selected=len(selected), batch_limit=BATCH_LIMIT)
        if not selected:
            return {**base, "state": "BLOQUEADO", "reason": "NO_EXACT_A3_TARGETS"}
        assert all(len(key) == 5 and all(KEY_PART.fullmatch(part) for part in key) for key in selected)
        raw_before, before, _ = inspect_history(selected)
        if any(row[4] for row in before.values()):
            return {**base, "state": "BLOQUEADO", "reason": "A3_REFERENCE_EXISTING_KEY_CONFLICT"}
        try:
            result = run(store, history_store=history, mode="BOOTSTRAP", reference_only=True,
                         batch_limit=BATCH_LIMIT, throttle_seconds=1.0)
        except CEMReadOnlyError:
            return {**base, "state": "BLOQUEADO", "reason": "A3_CEM_SOURCE_UNVERIFIED"}
        session_after = paper_state()
        raw_after, after, check = inspect_history(selected, quick_check=True)
    if result.get("ran"):
        events = result["targets"]
    elif result.get("reason") == "NO_NEW_A3_SESSION_GAP":
        events = [{"target": key, "status": "NOT_DUE"} for key in selected]
    else:
        reason = result.get("reason")
        assert reason in {"RUNTIME_NOT_CLOSED_OR_SAFE", "NO_EXACT_A3_TARGETS"}
        return {**base, "state": "BLOQUEADO", "reason": reason}
    evidence = [sanitize_target(event, before, after) for event in events]
    assert {tuple(row["requested_identity"]) for row in evidence} == set(selected)
    assert len(evidence) == len(selected)
    stats = {name: result.get(name, 0) for name in
             ("matched", "unmatched", "empty", "failed", "provider_requests",
              "versions_appended", "canonical_updates")}
    assert all(type(value) is int and value >= 0 for value in stats.values())
    new_keys = sum(row["canonical_after"] - row["canonical_before"] for row in evidence)
    stable = raw_before == raw_after
    valid = check == "ok" and stable and session_after == "MARKET_CLOSED"
    valid = valid and all(
        not row["provenance_mismatches"] and row["canonical_after"] >= row["canonical_before"]
        for row in evidence
    )
    valid = valid and 0 <= new_keys <= stats["canonical_updates"] and stats["failed"] == 0
    valid = valid and (not stats["canonical_updates"] or any(row["revision_advanced"] for row in evidence))
    idempotent = not new_keys and not stats["versions_appended"] and not stats["canonical_updates"] and all(
        row["status"] in {"COMPLETE", "NOT_DUE"} and row["canonical_after"] > 0 for row in evidence
    )
    if not valid:
        state, outcome = "NO_VERIFICADO", "POSTFLIGHT_OR_TARGET_FAILED"
    elif stats["canonical_updates"]:
        state, outcome = "VALIDADO_RUNTIME", "REFERENCE_WRITES_VERIFIED"
    elif idempotent:
        state, outcome = "VALIDADO_RUNTIME", "REFERENCE_ALREADY_PRESENT"
    else:
        state, outcome = "BLOQUEADO", "NO_A3_REFERENCE_WRITES_VERIFIED"
    return {
        **base, **stats, "state": state, "reason": outcome, "quick_check": check,
        "raw_before": raw_before, "raw_after": raw_after, "raw_counts_unchanged": stable,
        "new_canonical_keys": new_keys, "session_after": session_after, "targets": evidence,
    }


print(json.dumps(load_one_batch()))
'''


def require(condition, code):
    if not condition:
        raise ValueError(code)


def gh_api(path):
    return ["gh", "api", f"repos/{REPOSITORY}/{path}"]


def read_api(path):
    return json.loads(subprocess.check_output(gh_api(path), stderr=subprocess.DEVNULL, timeout=40))


def load_control():
    fields = {"candidate_sha", "deployment_sha", "predeploy_run_id", "promote_run_id", "runtime_artifact_id"}
    data = Path(__file__).with_name(CONTROL).read_bytes()
    require(0 < len(data) < 4096, "A3_CONTROL_INVALID")
    config = json.loads(data)
    require(isinstance(config, dict) and set(config) == fields, "A3_CONTROL_INVALID")
    for name in ("candidate_sha", "deployment_sha"):
        value = config[name]
        require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value), "A3_CONTROL_SHA_INVALID")
    for name in ("predeploy_run_id", "promote_run_id", "runtime_artifact_id"):
        require(type(config[name]) is int and config[name] > 0, "A3_CONTROL_ID_INVALID")
    return config


def verified_promotion(config):
    deploy_id = config["promote_run_id"]
    deployment = config["deployment_sha"]
    candidate = config["candidate_sha"]
    release_ref = read_api(f"git/ref/heads/{PRODUCT}")
    require(release_ref.get("object", {}).get("sha") == deployment, "A3_PRODUCT_REF_MOVED")
    promotion = read_api(f"actions/runs/{deploy_id}")
    require(promotion.get("id") == deploy_id and promotion.get("head_sha") == deployment
            and promotion.get("head_branch") == PRODUCT and promotion.get("path") == PROMOTE_WORKFLOW
            and promotion.get("event") == "workflow_dispatch" and promotion.get("status") == "completed"
            and promotion.get("conclusion") == "success"
            and promotion.get("actor", {}).get("login") == "mbalbo2023"
            and promotion.get("triggering_actor", {}).get("login") == "mbalbo2023",
            "A3_PROMOTION_NOT_VALIDATED")
    attempt = promotion.get("run_attempt")
    require(type(attempt) is int and attempt > 0, "A3_PROMOTION_ATTEMPT_INVALID")
    artifact_id = config["runtime_artifact_id"]
    artifacts = read_api(f"actions/runs/{deploy_id}/artifacts?per_page=100")["artifacts"]
    matches = [artifact for artifact in artifacts if artifact.get("id") == artifact_id]
    require(len(matches) == 1, "A3_PROMOTION_ARTIFACT_NOT_FOUND")
    artifact = matches[0]
    digest = artifact.get("digest", "")
    size = artifact.get("size_in_bytes")
    run_identity = artifact.get("workflow_run", {})
    require(artifact.get("expired") is False and artifact.get("name") == f"porota-runtime-{deploy_id}-{attempt}"
            and run_identity.get("id") == deploy_id and run_identity.get("head_sha") == deployment
            and isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            and type(size) is int and 0 < size <= MAX_RECEIPT, "A3_PROMOTION_ARTIFACT_INVALID")
    archive = Path(os.environ["RUNNER_TEMP"]) / "a3-promote-attested.zip"
    with archive.open("xb") as output:
        subprocess.run(gh_api(f"actions/artifacts/{artifact_id}/zip"), stdout=output,
                       stderr=subprocess.DEVNULL, timeout=45, check=True)
    require(archive.stat().st_size == size, "A3_PROMOTION_ARTIFACT_SIZE_INVALID")
    require("sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() == digest,
            "A3_PROMOTION_ARTIFACT_DIGEST_INVALID")
    with ZipFile(archive) as zipped:
        members = zipped.infolist()
        require(len(members) == 2 and {item.filename for item in members} == {"runtime.json", "download.json"}
                and all(0 < item.file_size <= MAX_RECEIPT for item in members),
                "A3_PROMOTION_ARTIFACT_CONTENTS_INVALID")
        download_raw = zipped.read("download.json")
        download = decode_receipt(download_raw)
        receipt = decode_receipt(zipped.read("runtime.json"))
    download_hash = hashlib.sha256(download_raw).hexdigest()
    tree = download.get("source_tree", "")
    image = download.get("image_id", "")
    require(isinstance(tree, str) and re.fullmatch(r"[a-f0-9]{40}", tree)
            and isinstance(image, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", image),
            "A3_PROMOTION_IMAGE_OR_TREE_INVALID")
    require(download.get("schema") == "porota.release.download.v1"
            and download.get("run_id") == config["predeploy_run_id"]
            and download.get("source_sha") == candidate and download.get("deployment_sha") == deployment,
            "A3_PREDEPLOY_BINDING_INVALID")
    require(receipt.get("attempt") == f"{deploy_id}-{attempt}", "A3_PROMOTION_ATTEMPT_MISMATCH")
    validate_runtime_receipt(
        receipt, {"source_sha": candidate, "source_tree": tree, "image_id": image}, 0,
        download={"deployment_sha": deployment, "download_receipt_sha256": download_hash},
    )
    return tree, image, download_hash


def main():
    assert len(CONTAINER.encode()) <= 16384
    operation = {"schema": SCHEMA, "state": "BLOQUEADO", "reason": "A3_RELEASE_NOT_VERIFIED"}
    stage = "RELEASE_VERIFICATION"
    try:
        config = load_control()
        operation.update(config)
        tree, image, digest = verified_promotion(config)
        stage = "HOST_EXECUTION"
        candidate, deployment = config["candidate_sha"], config["deployment_sha"]
        with connection(os.environ) as (ssh, _, _):
            reply = ssh_run(
                ssh, ["python3", "-B", "-c", HOST, candidate, tree, deployment, image, digest],
                data=CONTAINER.encode(), timeout=1260,
            )
        require(reply.returncode == 0 and 0 < len(reply.stdout) <= MAX_RECEIPT,
                "A3_HISTORY_HOST_UNVERIFIED")
        result = decode_receipt(reply.stdout)
        require(result.get("schema") == SCHEMA and result.get("deployment_sha") == deployment
                and result.get("candidate_sha") == candidate and result.get("image_id") == image
                and type(result.get("real_orders_sent")) is int and result["real_orders_sent"] == 0
                and result.get("state") in {"VALIDADO_RUNTIME", "BLOQUEADO", "NO_VERIFICADO"},
                "A3_HISTORY_PHYSICAL_RECEIPT_INVALID")
        operation.update(result)
    except Exception as error:
        operation["stage"] = stage
        code = str(error)
        if isinstance(error, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]{5,80}", code):
            operation["reason"] = code
        if stage == "HOST_EXECUTION":
            operation["state"] = "NO_VERIFICADO"
            if operation["reason"] == "A3_RELEASE_NOT_VERIFIED":
                operation["reason"] = "A3_HISTORY_HOST_UNVERIFIED"
    (Path(os.environ["RUNNER_TEMP"]) / "a3-history.json").write_text(json.dumps(operation) + "\n")
    print(json.dumps({key: operation.get(key) for key in (
        "state", "reason", "selected", "matched", "canonical_updates", "new_canonical_keys",
        "quick_check", "raw_counts_unchanged", "real_orders_sent",
    )}))
    return int(operation["state"] != "VALIDADO_RUNTIME")


if __name__ == "__main__":
    raise SystemExit(main())

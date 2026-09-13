#!/usr/bin/env python3
"""Durable RC6 PPI Web residual runner.

The runner is intentionally gated behind an ENABLED file and refuses to run
while the PPI API historical writer is active or API tasks are unfinished.
Browser work runs as the unprivileged trusted-profile owner; canonical history
writes stay in this process and use PPI_WEB_HISTORY provenance.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.getenv("POROTA_REPO", "/opt/porota-trading"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from ppi_web_residual_state_rc6 import ResidualState
from ppi_history_residual_manifest_rc6 import load_from_runtime, write_jsonl
from ppi_web_history_ingest_rc6 import ingest_web_rows
import bf_production_paper_observer as observer

RUN_ID = os.getenv("PPI_WEB_RESIDUAL_RUN_ID", "PPI-WEB-RESIDUAL-20260913-001")
ROOT = Path(os.getenv("PPI_WEB_RESIDUAL_ROOT", "/opt/porota-ingest/ppi-web-residual"))
ENABLED = ROOT / "ENABLED"
MANIFEST = ROOT / "residual.jsonl"
STATE_DB = ROOT / "state.sqlite3"
STATUS = ROOT / "status.json"
BATCH_ROOT = ROOT / "batches"
PROFILE = Path(os.getenv("PPI_WEB_PROFILE", "/home/porotaadmin/porota-browser-lab/chrome-profile"))
SECRET = Path(os.getenv("PPI_WEB_SECRET", "/etc/porota/contract-evidence-web.env"))
VENV_PY = Path(os.getenv("PPI_WEB_PYTHON", "/opt/porota-contract-evidence-venv/bin/python"))
CHROME = os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable")
BROWSER_USER = os.getenv("PPI_WEB_BROWSER_USER", "porotaadmin")
COLLECTOR = Path(os.getenv("PPI_WEB_COLLECTOR", str(REPO / "rc6_ppi_web_history_shadow.py")))
REAUTH = Path(os.getenv("PPI_WEB_REAUTH", str(REPO / "rc6_ppi_web_reauth.py")))
API_UNIT = os.getenv("PPI_HISTORY_API_UNIT", "porota-ppi-fullfamily-history-rc6.service")
BATCH_SIZE = max(1, int(os.getenv("PPI_WEB_BATCH_SIZE", "8")))
OBSERVER_DB = Path(os.getenv("POROTA_OBSERVER_DB", str(REPO / "data/paper_v17/observer_v17.db")))


def log(event: str, **fields) -> None:
    safe = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **fields}
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True), flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def api_writer_active() -> bool:
    p = subprocess.run(["systemctl", "is-active", "--quiet", API_UNIT], check=False)
    return p.returncode == 0


def safety_check() -> None:
    if not ENABLED.is_file():
        raise RuntimeError("PPI_WEB_RESIDUAL_NOT_ENABLED")
    if api_writer_active():
        raise RuntimeError("PPI_API_HISTORICAL_WRITER_ACTIVE")
    if not OBSERVER_DB.is_file():
        raise RuntimeError("OBSERVER_DB_MISSING")
    c = sqlite3.connect(f"file:{OBSERVER_DB}?mode=ro", uri=True, timeout=20)
    try:
        c.execute("PRAGMA query_only=ON")
        qc = c.execute("PRAGMA quick_check").fetchone()[0]
        row = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    finally:
        c.close()
    if qc != "ok" or not row or row[0] != "PRODUCTION_PAPER" or int(row[1] or 0) != 0:
        raise RuntimeError("SAFETY_STATE_NOT_PRODUCTION_PAPER_ZERO_ORDERS")
    for path in (PROFILE, SECRET, VENV_PY, COLLECTOR, REAUTH):
        if not path.exists():
            raise RuntimeError(f"REQUIRED_PATH_MISSING:{path}")


def ensure_manifest() -> list[dict]:
    rows = load_from_runtime()  # fails closed if API tasks remain active
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / ".residual.jsonl.tmp"
    write_jsonl(tmp, rows)
    os.replace(tmp, MANIFEST)
    return rows


def browser_command(script: Path, *args: str) -> list[str]:
    return [
        "runuser", "-u", BROWSER_USER, "--", "env",
        f"HOME={pwd.getpwnam(BROWSER_USER).pw_dir}", f"PYTHONPATH={REPO}:{HERE}",
        str(VENV_PY), str(script), *args,
    ]


def run_json_command(cmd: list[str], timeout: int) -> tuple[int, dict]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=timeout, check=False)
    payload = {}
    for line in reversed(p.stdout.splitlines()):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                payload = obj
                break
        except Exception:
            continue
    return p.returncode, payload


def reauthenticate() -> None:
    rc, out = run_json_command(browser_command(
        REAUTH, "--profile", str(PROFILE), "--secret", str(SECRET), "--chrome", CHROME,
    ), 120)
    status = str(out.get("status", "UNKNOWN"))
    log("reauth", rc=rc, status=status, credentials_exposed=False, real_orders_sent=0)
    if rc != 0 or status != "AUTHENTICATED_TRUSTED_DEVICE":
        raise RuntimeError(f"PPI_WEB_REAUTH_FAILED:{status}")


def make_batch_dir(index: int) -> Path:
    BATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=f"batch-{index:06d}-", dir=BATCH_ROOT))
    pw = pwd.getpwnam(BROWSER_USER)
    os.chown(path, pw.pw_uid, pw.pw_gid)
    os.chmod(path, 0o700)
    return path


def write_targets(path: Path, tasks: list[dict]) -> None:
    payload = "".join(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n" for t in tasks)
    path.write_text(payload, encoding="utf-8")
    pw = pwd.getpwnam(BROWSER_USER)
    os.chown(path, pw.pw_uid, pw.pw_gid)
    os.chmod(path, 0o600)


def collect(tasks: list[dict], batch_index: int) -> dict:
    directory = make_batch_dir(batch_index)
    targets = directory / "targets.jsonl"
    output = directory / "capture.json"
    write_targets(targets, tasks)
    cmd = browser_command(
        COLLECTOR, "--profile", str(PROFILE), "--output", str(output),
        "--targets-jsonl", str(targets), "--chrome", CHROME,
    )
    rc, summary = run_json_command(cmd, max(180, 50 * len(tasks)))
    if str(summary.get("auth_status")) == "BLOCKED_AUTH_SESSION_EXPIRED":
        reauthenticate()
        rc, summary = run_json_command(cmd, max(180, 50 * len(tasks)))
    if rc != 0 or str(summary.get("auth_status")) != "AUTHENTICATED_TRUSTED_DEVICE":
        raise RuntimeError(f"PPI_WEB_COLLECT_FAILED:{summary.get('auth_status','UNKNOWN')}")
    if not output.is_file():
        raise RuntimeError("PPI_WEB_CAPTURE_MISSING")
    result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("canonical_write") != "DENY" or int(result.get("real_orders_sent", -1)) != 0:
        raise RuntimeError("PPI_WEB_COLLECTOR_SAFETY_CONTRACT_BROKEN")
    return result


def identity(row: dict) -> tuple[str, str, str, str]:
    return tuple(str(row.get(k, "")).strip().upper() for k in
                 ("symbol", "instrument_type", "market", "settlement"))


def best_rows_by_identity(capture: dict) -> dict[tuple[str, str, str, str], list[dict]]:
    best: dict[tuple[str, str, str, str], list[dict]] = {}
    for item in capture.get("captures", []):
        if not isinstance(item, dict):
            continue
        key = identity(item)
        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        if len(rows) > len(best.get(key, [])):
            best[key] = rows
    return best


def classify(provider_rows: int, valid_rows: int, rejected_rows: int) -> str:
    if provider_rows <= 0:
        return "DONE_EMPTY"
    if valid_rows <= 0:
        return "DONE_EMPTY"
    if rejected_rows > 0 or valid_rows < provider_rows:
        return "DONE_PARTIAL"
    return "DONE_VALID"


def process_batch(state: ResidualState, tasks: list[dict], capture: dict) -> None:
    rows_by_key = best_rows_by_identity(capture)
    store = observer.runtime_store()
    today = datetime.now(timezone.utc).date()
    date_from = today - timedelta(days=365)
    for task in tasks:
        key = identity(task)
        rows = rows_by_key.get(key, [])
        if not rows:
            state.finish(RUN_ID, task, "DONE_EMPTY", provider_rows=0, valid_rows=0,
                         detail="PPI_WEB_NO_HISTORICAL_CAPTURE")
            continue
        try:
            result = ingest_web_rows(
                store, symbol=task["symbol"], instrument_type=task["instrument_type"],
                market=task["market"], settlement=task["settlement"], rows=rows,
                requested_from=date_from, requested_to=today,
            )
            terminal = classify(result["provider_rows"], result["valid_rows"], result["rejected_rows"])
            detail = (
                f"source=PPI_WEB_HISTORY; rejected={result['rejected_rows']}; "
                f"canonical_updates={result['canonical_updates']}; "
                f"protected_by_precedence={result['protected_by_precedence']}"
            )
            state.finish(RUN_ID, task, terminal, provider_rows=result["provider_rows"],
                         valid_rows=result["valid_rows"], detail=detail)
        except Exception as exc:
            state.finish(RUN_ID, task, "ERROR", detail=f"{type(exc).__name__}:{str(exc)[:900]}")


def main() -> int:
    safety_check()
    rows = ensure_manifest()
    manifest_hash = sha256_file(MANIFEST)
    state = ResidualState(STATE_DB, STATUS)
    state.seed(RUN_ID, manifest_hash, rows)
    recovered = state.recover_orphan_running(RUN_ID)
    log("runner_start", run_id=RUN_ID, residual=len(rows), recovered=recovered,
        api_writer_active=False, real_orders_sent=0)
    batch_index = 0
    while True:
        state.heartbeat(RUN_ID, status="RUNNING")
        tasks = state.claim_many(RUN_ID, BATCH_SIZE)
        if not tasks:
            break
        batch_index += 1
        try:
            capture = collect(tasks, batch_index)
            process_batch(state, tasks, capture)
        except Exception:
            # Leave RUNNING tasks recoverable on service restart; do not falsely terminalize them.
            state.heartbeat(RUN_ID, status="RUNNING")
            raise
        finally:
            # Keep captures as local evidence; they contain only sanitized market history.
            pass
    state.complete_if_terminal(RUN_ID)
    summary = state.publish(RUN_ID)
    log("runner_complete", **summary, real_orders_sent=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

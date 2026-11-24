"""Run the deployed archive importer once after a verified Deploy V2 promotion."""
import json
import os
from pathlib import Path
import re

from scripts.porota_actions_host import connection, ssh_run


HOST = r"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

deployment, candidate, image = sys.argv[1:]
release_marker = Path("/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json")
uid = os.getuid()
assert uid == os.geteuid() > 0
docker = ["/usr/bin/docker", "--host", f"unix:///run/user/{uid}/porota-rc6-docker.sock"]

def verify_deployment():
    assert release_marker.resolve(strict=True) == release_marker
    assert 0 < release_marker.stat().st_size < 1_048_576
    marker = json.loads(release_marker.read_bytes())
    assert (marker["deployment_sha"], marker["candidate_sha"], marker["image_id"]) == (deployment, candidate, image)
    assert marker["validation_status"] == "VALIDATED_RUNTIME"
    assert marker["mode"] == "PRODUCTION_PAPER" and marker["real_order_capability"] == "BLOCKED"
    assert type(marker["real_orders_sent"]) is int and marker["real_orders_sent"] == 0
    ids = subprocess.check_output([*docker, "ps", "--no-trunc", "--filter",
        "label=io.porota.project=rc6", "--filter", "label=io.porota.role=observer",
        "--format", "{{.ID}}"], text=True, timeout=8).splitlines()
    assert len(ids) == 1 and re.fullmatch(r"[0-9a-f]{64}", ids[0])
    seen_image = subprocess.check_output([*docker, "inspect", "--format", "{{.Image}}",
        ids[0]], text=True, timeout=8).strip()
    assert seen_image == image
    return ids[0]

observer = verify_deployment()
source = sys.stdin.buffer.read(16385)
assert 0 < len(source) <= 16384
run = subprocess.run([*docker, "container", "exec", "-i", "-w", "/app", observer,
    "nice", "-n", "10", "python", "-B", "-"], input=source, capture_output=True, timeout=2100)
if run.returncode != 0:
    status = run.stdout.decode(errors="replace").strip()
    if re.fullmatch("HISTORY_APPLY:[A-Za-z0-9_]+", status):
        print(status)
    raise SystemExit(2)
assert len(run.stdout) <= 4_194_304 and verify_deployment() == observer
payload = json.loads(run.stdout)
assert payload["schema"] == "rc6.history.ppi-reference-import.v1"
assert payload["quick_check"] == "ok" and payload["preserved_rows_reconciled"] is True
payload.update(deployment_sha=deployment, candidate_sha=candidate,
               image_id=image, same_runtime=True, real_orders_sent=0, order_guard="BLOCKED")
print(json.dumps(payload, separators=(",", ":")))
"""


CONTAINER = r"""
from contextlib import closing
import fcntl
import json
from pathlib import Path
import sqlite3

from cv_history_store_adapter_hf6 import HistoricalStore
from cu_history_store_v2_hf6 import PPI_REPORTED
from scripts.rc6_history_import_ppi_archive import import_ppi_archive

root = Path("/app/data")
history = root / "market_history.db"
observer = root / "paper_v17/observer_v17.db"


def read(path):
    assert path.resolve(strict=True) == path and path.is_file()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA query_only=ON")
    return db


def paper():
    with closing(read(observer)) as db:
        state = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    assert state == ("PRODUCTION_PAPER", 0)


def existing():
    with closing(read(history)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        db.execute("BEGIN")
        return {k: db.execute(f"SELECT count(*) FROM {k} WHERE source<>?", (PPI_REPORTED,)).fetchone()[0]
                for k in ("history_canonical_v2", "history_versions_v2")} | {
            k: db.execute(f"SELECT count(*) FROM {k}").fetchone()[0] for k in
            ("history_canonical_v2_legacy", "history_versions_v2_legacy")}


try:
    paper()
    with (root / ".rc6-history-cutoff-repair.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = existing()
        result = import_ppi_archive(HistoricalStore(str(history)).for_source_reported_history())
        after = existing()
    paper()
    assert before == after and result["stored_rows"] > 0
    result.update(preserved_rows_reconciled=True, preserved_rows=after, destination="LIVE_HISTORY_V2")
    print(json.dumps(result, separators=(",", ":")))
except (OSError, ValueError, sqlite3.Error, AssertionError) as error:
    code = str(error)
    print("HISTORY_APPLY:" + (code if code.isascii() and code.replace("_", "").isalnum()
          and code.startswith("HISTORY_") else type(error).__name__))
    raise SystemExit(2)
"""


def main():
    folder = Path(os.environ["RUNNER_TEMP"]) / "porota"
    deployed = json.loads((folder / "runtime.json").read_text())
    download = json.loads((folder / "download.json").read_text())
    assert deployed["state"] == "VALIDADO_RUNTIME" and deployed["phase"] == "COMPLETE"
    assert deployed["source_sha"] == download["source_sha"]
    assert deployed["deployment_sha"] == download["deployment_sha"]
    assert deployed["image_id"] == download["image_id"]
    assert re.fullmatch(r"[a-f0-9]{40}", deployed["deployment_sha"])
    assert re.fullmatch(r"[a-f0-9]{40}", deployed["source_sha"])
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", deployed["image_id"])
    assert deployed["operating_mode"] == "PRODUCTION_PAPER" and deployed["execution_mode"] == "SIMULATION"
    assert type(deployed["real_orders_sent"]) is int and deployed["real_orders_sent"] == 0
    assert deployed["ppi_watch_touched"] is False and deployed["health"]["orders_guard"] == "BLOCKED"
    with connection(os.environ) as (ssh, _, _):
        response = ssh_run(ssh, ["python3", "-B", "-c", HOST, deployed["deployment_sha"],
                           deployed["source_sha"], deployed["image_id"]],
                           data=CONTAINER.encode(), timeout=2200)
    if response.returncode != 0:
        reason = response.stdout.decode(errors="replace").strip()
        if re.fullmatch(r"HISTORY_APPLY:[A-Za-z0-9_]+", reason):
            raise SystemExit(reason)
        raise SystemExit("HISTORY_APPLY_HOST_UNVERIFIED")
    payload = json.loads(response.stdout)
    assert payload["deployment_sha"] == deployed["deployment_sha"]
    assert payload["real_orders_sent"] == 0 and payload["provider_requests"] == 0
    report = folder / "history-import.json"
    report.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(json.dumps({key: payload[key] for key in ("schema", "stored_rows", "quick_check",
                                                     "real_orders_sent", "preserved_rows")}))


if __name__ == "__main__":
    main()

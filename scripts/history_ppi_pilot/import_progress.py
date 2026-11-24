"""Observe committed historical rows without disturbing the one-time writer."""
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, ssh_run


PROBE = r"""
import json
import os
from pathlib import Path
import subprocess
import sys

deployment, candidate = sys.argv[1:]
state = Path("/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json")
assert state.resolve(strict=True) == state and 0 < state.stat().st_size <= 1_048_576
release = json.loads(state.read_bytes())
assert (release["deployment_sha"],release["candidate_sha"]) == (deployment,candidate)
assert release["validation_status"] == "VALIDATED_RUNTIME"
assert release["mode"] == "PRODUCTION_PAPER" and release["real_orders_sent"] == 0
assert release["real_order_capability"] == "BLOCKED"
uid = os.getuid()
assert uid == os.geteuid() > 0
docker = ["/usr/bin/docker","--host",f"unix:///run/user/{uid}/porota-rc6-docker.sock"]
ids = subprocess.check_output([*docker,"ps","--no-trunc","--filter","label=io.porota.project=rc6",
    "--filter","label=io.porota.role=observer","--format","{{.ID}}"],text=True,timeout=8).splitlines()
assert len(ids) == 1
assert subprocess.check_output([*docker,"inspect","--format","{{.Image}}",ids[0]],text=True,
                               timeout=8).strip() == release["image_id"]
snippet = sys.stdin.buffer.read(8193)
assert 0 < len(snippet) <= 8192
receipt = subprocess.run([*docker,"exec","-i",ids[0],"python","-B","-"],input=snippet,
                         capture_output=True,timeout=22)
assert receipt.returncode == 0 and 0 < len(receipt.stdout) <= 65536
output = json.loads(receipt.stdout)
output.update(deployment_sha=deployment,candidate_sha=candidate,image_id=release["image_id"])
print(json.dumps(output))
"""


SQL = r'''
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import sqlite3
import time

with closing(sqlite3.connect("file:/app/data/paper_v17/observer_v17.db?mode=ro",uri=True,timeout=3)) as db:
    db.execute("PRAGMA query_only=ON")
    paper = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
assert paper == ("PRODUCTION_PAPER",0)
with closing(sqlite3.connect("file:/app/data/market_history.db?mode=ro",uri=True,timeout=3)) as db:
    db.execute("PRAGMA query_only=ON")
    timeout = time.monotonic() + 10
    db.set_progress_handler(lambda: int(time.monotonic()>timeout),1000)
    journal = db.execute("PRAGMA journal_mode").fetchone()[0]
    db.execute("BEGIN")
    count = db.execute("SELECT count(*) FROM history_versions_v2 WHERE source='PPI_PRODUCTION_HISTORY_REPORTED'").fetchone()[0]
    groups = db.execute("""SELECT instrument_type,count(*),max(date)
        FROM history_canonical_v2 WHERE source='PPI_PRODUCTION_HISTORY_REPORTED'
        GROUP BY instrument_type ORDER BY instrument_type""").fetchall()
sources = {name: bool(os.getenv("POROTA_"+name+"_HISTORICAL_URL","").strip())
           for name in ("BYMA","CNV","MATBA_ROFEX")}
print(json.dumps({"schema":"rc6.history.live-progress.v1","committed_ppi_versions":count,
                  "committed_ppi_canonical":sum(group[1] for group in groups),
                  "committed_by_family":groups,"official_historical_endpoints_configured":sources,
                  "journal_mode":journal,"real_orders_sent":0,"production_sql_writes":0,
                  "observed_at":datetime.now(timezone.utc).isoformat()}))
'''


def main():
    control = json.loads(Path(__file__).with_name("promotion.json").read_text())
    with connection(os.environ) as (ssh,_,_):
        result = ssh_run(ssh,["python3","-B","-c",PROBE,control["deployment_sha"],control["candidate_sha"]],
                         data=SQL.encode(),timeout=45)
    if result.returncode != 0:
        raise SystemExit("HISTORY_LIVE_PROGRESS_UNVERIFIED")
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rc6.history.live-progress.v1"
    assert payload["production_sql_writes"] == payload["real_orders_sent"] == 0
    content = json.dumps(payload,sort_keys=True)+"\n"
    (Path(os.environ["RUNNER_TEMP"])/"history-live-progress.json").write_text(content)
    print(content, end="")


if __name__ == "__main__":
    main()

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
snippet = sys.stdin.buffer.read(65537)
assert 0 < len(snippet) <= 65536
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
import hashlib
import json
import sqlite3
from types import ModuleType

import fb_raw_evidence_exact_v1 as exact
from cu_history_store_v2_hf6 import instant, PPI_REPORTED_SELECTOR
from rc6_multisource_discovery import canonical_family, canonical_settlement

with closing(sqlite3.connect("file:/app/data/paper_v17/observer_v17.db?mode=ro",uri=True,timeout=3)) as db:
    db.execute("PRAGMA query_only=ON")
    paper = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
assert paper == ("PRODUCTION_PAPER",0)
operator = ModuleType("porota_verify_closeout_remainder")
exec(compile(CLOSEOUT_SOURCE, "deployed-closeout-operator.py", "exec"), operator.__dict__)
remaining = []
with closing(operator._manifest(exact.manifest_path())) as db:
    records = db.execute("""SELECT row_key,body_hash,recorded_at,quality,wrapper_json
        FROM exact_attempts_v1 WHERE origin='PPI_HISTORY' AND json_valid(row_key)
        AND json_array_length(row_key) IN (5,6)""")
    for row in records:
        wrapper = json.loads(row["wrapper_json"])
        try:
            operator._series(row,wrapper,legacy_closeout=True)
            continue
        except (TypeError, ValueError) as error:
            reject = str(error)
        symbol = wrapper["symbol"]
        family = wrapper.get("instrument_type") or wrapper["asset_class"]
        term = wrapper["settlement"]
        market = wrapper["market"]
        at = instant(row["recorded_at"])
        # Only inspect payloads with the previously verified identity prefix.
        prefix = [symbol, family, market, term, row["recorded_at"]]
        key_verified = json.loads(row["row_key"]) in (prefix,[*prefix,"API_CLOSEOUT"])
        item = {"symbol_sha256":hashlib.sha256(symbol.encode()).hexdigest(),
                "family":family,"reason":reject,"quality":row["quality"],
                "symbol_outside_current_grammar":sorted(set("U+%04X"%ord(c)
                    for c in symbol if c not in " ._/+()%-" and not ("A"<=c<="Z" or "0"<=c<="9")))}
        if key_verified and PPI_REPORTED_SELECTOR.fullmatch(family):
            try:
                bars, wrapper, digest = operator._body(db,(at,row["row_key"],row["body_hash"]),None)
            except (OSError,ValueError,EOFError):
                item["payload_status"] = "UNVERIFIED"
                remaining.append(item)
                continue
            from datetime import date
            bounds = (date.fromisoformat(wrapper["date_from"]),date.fromisoformat(wrapper["date_to"]))
            candidates, anomalies = operator._candles(bars,(symbol,canonical_family(family),
                canonical_settlement(term),family),bounds,(at,row["row_key"],row["body_hash"]),wrapper,digest)
            item.update(valid_ohlc_days=len(candidates),delivered_rows=len(bars),ohlc_rejections=dict(anomalies))
        remaining.append(item)
print(json.dumps({"schema":"rc6.history.live-progress.v1","rejected_closeout_entries":remaining,
                  "real_orders_sent":0,"production_sql_writes":0,
                  "observed_at":datetime.now(timezone.utc).isoformat()}))
'''


def main():
    control = json.loads(Path(__file__).with_name("promotion.json").read_text())
    file = Path(__file__).resolve().parents[1]/"rc6_history_import_ppi_archive.py"
    snippet = ("CLOSEOUT_SOURCE = "+repr(file.read_text())+"\n"+SQL).encode()
    with connection(os.environ) as (ssh,_,_):
        result = ssh_run(ssh,["python3","-B","-c",PROBE,control["deployment_sha"],control["candidate_sha"]],
                         data=snippet,timeout=45)
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

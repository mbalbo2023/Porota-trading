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
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import time

import fb_raw_evidence_exact_v1 as exact
from scripts.rc6_history_import_ppi_archive import _body, _manifest, _series
from cu_history_store_v2_hf6 import instant

with closing(sqlite3.connect("file:/app/data/paper_v17/observer_v17.db?mode=ro",uri=True,timeout=3)) as db:
    db.execute("PRAGMA query_only=ON")
    paper = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    db.execute("BEGIN")
    fci_count = db.execute("SELECT count(*) FROM financial_instrument_catalog WHERE instrument_type='FCI'").fetchone()[0]
    bindings = dict(db.execute("""SELECT lower(field.key),count(DISTINCT f.ticker)
        FROM financial_instrument_catalog f, json_tree(
          CASE WHEN json_valid(f.metadata_json) THEN f.metadata_json ELSE '{}' END) field
        WHERE f.instrument_type='FCI' AND lower(field.key) IN
          ('cafci_class_id','cafciclassid','idclasecafci','codigo_clase_cafci','cnv_fund_id','cnvfundid')
          AND field.type IN ('text','integer') AND length(field.atom)>0 GROUP BY 1"""))
assert paper == ("PRODUCTION_PAPER",0)
with closing(sqlite3.connect("file:/app/data/market_history.db?mode=ro",uri=True,timeout=3)) as db:
    db.execute("PRAGMA query_only=ON")
    timeout = time.monotonic() + 10
    db.set_progress_handler(lambda: int(time.monotonic()>timeout),1000)
    journal = db.execute("PRAGMA journal_mode").fetchone()[0]
    db.execute("BEGIN")
    count = db.execute("SELECT count(*) FROM history_versions_v2 WHERE source='PPI_PRODUCTION_HISTORY_REPORTED'").fetchone()[0]
    canonical = db.execute("SELECT count(*) FROM history_canonical_v2 WHERE source='PPI_PRODUCTION_HISTORY_REPORTED'").fetchone()[0]


def key_shape(value, wrapper, recorded):
    try:
        key = json.loads(value)
    except (TypeError, ValueError):
        return "INVALID_JSON"
    if not isinstance(key, list):
        return type(key).__name__
    lookup = {wrapper[name]: name for name in ("symbol","asset_class","instrument_type",
              "settlement","market","currency","date_from","date_to")
              if isinstance(wrapper.get(name), str)}
    lookup[recorded] = "recorded_at"

    def member(item):
        if isinstance(item, list):
            return [member(part) for part in item]
        if isinstance(item, str):
            return lookup.get(item, "text:" + str(len(item)))
        return type(item).__name__
    return json.dumps(member(key), separators=(",", ":"))


rejected = Counter()
examples = {}
suffixes = Counter()
with closing(_manifest(exact.manifest_path())) as db:
    rows = db.execute("""SELECT row_key,body_hash,inner_sha256,recorded_at,quality,
        CASE WHEN length(CAST(wrapper_json AS BLOB))<=65536 THEN wrapper_json END wrapper_json
        FROM exact_attempts_v1 WHERE origin='PPI_HISTORY' LIMIT 1000001""")
    for index, row in enumerate(rows):
        assert index < 1000000
        wrapper = {}
        try:
            wrapper = json.loads(row["wrapper_json"])
            _series(row, wrapper)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            family = str(wrapper.get("instrument_type") or wrapper.get("asset_class") or "UNKNOWN") if isinstance(wrapper, dict) else "UNKNOWN"
            reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("HISTORY_ARCHIVE_") else type(exc).__name__
            shape = key_shape(row["row_key"], wrapper, row["recorded_at"]) if isinstance(wrapper, dict) else "UNKNOWN"
            key = (family,reason,shape)
            rejected[key] += 1
            if reason == "HISTORY_ARCHIVE_KEY_CONFLICT" and shape.endswith('"text:12"]'):
                suffix = json.loads(row["row_key"])[-1]
                if suffix == row["inner_sha256"][:12]:
                    suffixes["INNER_SHA256_PREFIX"] += 1
                elif suffix == row["body_hash"][:12]:
                    suffixes["BODY_SHA256_PREFIX"] += 1
                elif suffix.isascii() and all(value.isupper() or value in "-_" for value in suffix):
                    suffixes[suffix] += 1
                elif all(value in "0123456789abcdefABCDEF" for value in suffix):
                    suffixes["UNMATCHED_HEX"] += 1
                else:
                    suffixes["UNRECOGNIZED_MARKER"] += 1
            if key not in examples and family == "FCI" and row["quality"] == "VALID_PAYLOAD":
                try:
                    bars, _, digest = _body(db, (instant(row["recorded_at"]),row["row_key"],row["body_hash"]),None)
                    examples[key] = {"symbol":wrapper.get("symbol"),"from":wrapper.get("date_from"),
                                     "to":wrapper.get("date_to"),"provider_bars":len(bars),
                                     "payload_sha256":digest}
                except (OSError, ValueError):
                    examples[key] = {"state":"BODY_UNVERIFIED"}
print(json.dumps({"schema":"rc6.history.live-progress.v1","committed_ppi_versions":count,
                  "committed_ppi_canonical":canonical,"fci_catalog_identities":fci_count,
                  "fcis_with_catalog_binding_markers":bindings,
                  "historical_key_markers":dict(suffixes),
                  "manifest_rejections":[{"family":k[0],"reason":k[1],"shape":k[2],"count":v,
                                          "example":examples.get(k)} for k,v in rejected.most_common()],
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

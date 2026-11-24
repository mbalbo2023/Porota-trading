"""Run the deployed archive importer once after a verified Deploy V2 promotion."""
from base64 import b64encode
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

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
source = sys.stdin.buffer.read(4_194_305)
assert 0 < len(source) <= 4_194_304
run = subprocess.run([*docker, "container", "exec", "-i", "-w", "/app", observer,
    "nice", "-n", "10", "python", "-B", "-"], input=source, capture_output=True, timeout=2100)
if run.returncode != 0:
    status = run.stdout.decode(errors="replace").strip()
    if re.fullmatch("HISTORY_APPLY:[A-Za-z0-9_]+", status):
        print(status)
    raise SystemExit(2)
assert len(run.stdout) <= 4_194_304 and verify_deployment() == observer
payload = json.loads(run.stdout)
assert payload["schema"] in {"rc6.history.ppi-reference-import.v1", "rc6.history.reference-gap-import.v1"}
assert payload["quick_check"] == "ok" and payload["preserved_rows_reconciled"] is True
payload.update(deployment_sha=deployment, candidate_sha=candidate,
               image_id=image, same_runtime=True, real_orders_sent=0, order_guard="BLOCKED")
print(json.dumps(payload, separators=(",", ":")))
"""


CONTAINER = r'''
from base64 import b64decode
from collections import defaultdict
from contextlib import closing
import fcntl
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import ModuleType
from hashlib import sha256

from cv_history_store_adapter_hf6 import HistoricalStore
from cu_history_store_v2_hf6 import PPI_REPORTED
from scripts.rc6_history_import_ppi_archive import import_ppi_archive
from scripts.rc6_history_import_source_reported import import_reference_export
from rc6_multisource_discovery import canonical_family

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


def append_references(store):
    outcomes = {}
    with TemporaryDirectory(prefix="porota-history-reference-") as temporary:
        for source, data, digest in REFERENCES:
            archive = Path(temporary) / (source + ".zip")
            archive.write_bytes(b64decode(data, validate=True))
            receipt = import_reference_export(archive, history, digest, source=source,
                                                existing_identity=store.existing_history_identity)
            outcomes[source] = receipt
    return outcomes


def inventory():
    reference = defaultdict(list)
    with closing(read(history)) as db:
        for symbol,family,term,basis,count,last in db.execute("""SELECT symbol,instrument_type,settlement,
            adjustment_basis,count(*),max(date) FROM history_canonical_v2
            WHERE price_basis='SOURCE_REPORTED' AND source=? GROUP BY 1,2,3,4""",(PPI_REPORTED,)):
            reference[symbol,family,term].append([basis.removeprefix("UNKNOWN:"+PPI_REPORTED+":"),count,last])
        alternate = {(symbol,family): [count,last] for symbol,family,count,last in db.execute("""
            SELECT symbol,instrument_type,count(DISTINCT date),max(date) FROM history_canonical_v2
            WHERE price_basis='SOURCE_REPORTED' AND source<>? GROUP BY 1,2""", (PPI_REPORTED,))}
        exact_rows = {tuple(item[:5]): list(item[5:]) for item in db.execute("""
            SELECT symbol,instrument_type,market,currency,settlement,count(DISTINCT date),max(date)
            FROM history_canonical_v2 WHERE price_basis='RAW' GROUP BY 1,2,3,4,5""")}
    with closing(read(observer)) as db:
        catalog = db.execute("""SELECT ticker,instrument_type,market,currency,settlement,status
            FROM financial_instrument_catalog ORDER BY 1,2,3,4,5 LIMIT 50001""").fetchall()
    assert len(catalog) <= 50000
    return [[*row, reference.get((row[0],canonical_family(row[1]),row[4]), []),
             alternate.get((row[0],canonical_family(row[1])), []) if row[2] == "BYMA" else [],
             exact_rows.get(tuple(row[:5]), [])] for row in catalog]


def recover(store):
    if ARCHIVE_COMPATIBILITY is None:
        return import_ppi_archive(store)
    # Run the pinned operator program in this temporary Python process. The
    # deployed modules, their source policy and the archive are left intact.
    operator = ModuleType("porota_history_legacy_closeout")
    exec(compile(ARCHIVE_COMPATIBILITY, "rc6_history_import_ppi_archive.py", "exec"), operator.__dict__)
    result = operator.import_ppi_archive(store, legacy_closeout=True)
    result["operator_script_sha256"] = sha256(ARCHIVE_COMPATIBILITY.encode()).hexdigest()
    return result


def recover_reference_gaps(store, before):
    references = append_references(store)
    assert set(references) == {"IOL_MCP"}
    changes = references["IOL_MCP"]
    after = existing()
    assert after["history_canonical_v2"] - before["history_canonical_v2"] == changes["canonical_updates"]
    assert after["history_versions_v2"] - before["history_versions_v2"] == changes["versions_appended"]
    assert all(before[name] == after[name] for name in
               ("history_canonical_v2_legacy", "history_versions_v2_legacy"))
    return {"schema": "rc6.history.reference-gap-import.v1", "source": "IOL_MCP",
            "stored_rows": changes["canonical_rows"], "new_rows": changes["canonical_updates"],
            "additional_references": references, "quick_check": changes["quick_check"],
            "preserved_rows": before, "provider_requests": 0}


try:
    paper()
    with (root / ".rc6-history-cutoff-repair.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = existing()
        store = HistoricalStore(str(history)).for_source_reported_history()
        if REFERENCE_GAPS:
            result = recover_reference_gaps(store, before)
        else:
            result = recover(store)
            after = existing()
            assert before == after and result["stored_rows"] > 0
            result.update(additional_references=append_references(store), preserved_rows=after)
    paper()
    result["catalog_coverage"] = inventory()
    result.update(preserved_rows_reconciled=True, destination="LIVE_HISTORY_V2")
    print(json.dumps(result, separators=(",", ":")))
except (OSError, ValueError, sqlite3.Error, AssertionError) as error:
    code = str(error)
    print("HISTORY_APPLY:" + (code if code.isascii() and code.replace("_", "").isalnum()
          and code.startswith("HISTORY_") else type(error).__name__))
    raise SystemExit(2)
'''


def supplemental_references(*, gaps=False):
    from scripts.rc6_history_import_source_reported import INTERVALORES, INTERVALORES_MEMBER, MEMBER, SOURCE
    from scripts.rc6_history_import_source_reported import _capture_sha

    evidence = Path(__file__).with_name("evidence")
    if gaps:
        evidence /= "fixed-income-gaps"
    output = []
    for source, member, files in ((SOURCE, MEMBER, sorted(evidence.glob("iol-*.jsonl"))),
                                  (INTERVALORES, INTERVALORES_MEMBER, sorted(evidence.glob("intervalores-*.jsonl")))):
        rows = []
        for path in files:
            for record in path.read_text().splitlines():
                capture = json.loads(record)
                if capture["payload"]["bars"]:
                    capture["payload_sha256"] = _capture_sha(capture["payload"])
                    rows.append(json.dumps(capture, allow_nan=False, ensure_ascii=False))
        if not rows:
            continue
        buffer = BytesIO()
        entry = ZipInfo(member)
        entry.compress_type = ZIP_DEFLATED
        with ZipFile(buffer, "w") as archive:
            archive.writestr(entry, "\n".join(rows) + "\n")
        raw = buffer.getvalue()
        output.append((source, b64encode(raw).decode("ascii"), sha256(raw).hexdigest()))
    return output


def main():
    legacy = sys.argv[1:] == ["--legacy-closeout"]
    gaps = sys.argv[1:] == ["--reference-gaps"]
    assert legacy or gaps or len(sys.argv) == 1
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
    override = None
    if legacy:
        override = (Path(__file__).resolve().parents[1] / "rc6_history_import_ppi_archive.py").read_text()
    references = [] if legacy else supplemental_references(gaps=gaps)
    source = ("REFERENCES = " + repr(references) + "\nARCHIVE_COMPATIBILITY = " + repr(override)
              + "\nREFERENCE_GAPS = " + repr(gaps)
              + "\n" + CONTAINER).encode()
    assert len(source) <= 4_194_304
    with connection(os.environ) as (ssh, _, _):
        response = ssh_run(ssh, ["python3", "-B", "-c", HOST, deployed["deployment_sha"],
                           deployed["source_sha"], deployed["image_id"]],
                           data=source, timeout=2200)
    if response.returncode != 0:
        reason = response.stdout.decode(errors="replace").strip()
        if re.fullmatch(r"HISTORY_APPLY:[A-Za-z0-9_]+", reason):
            raise SystemExit(reason)
        raise SystemExit("HISTORY_APPLY_HOST_UNVERIFIED")
    payload = json.loads(response.stdout)
    assert payload["deployment_sha"] == deployed["deployment_sha"]
    assert payload["real_orders_sent"] == 0 and payload["provider_requests"] == 0
    catalog = payload.pop("catalog_coverage")
    with (folder / "history-coverage.jsonl").open("w", encoding="utf-8") as rows:
        for row in catalog:
            names = ("symbol", "family", "market", "currency", "settlement", "catalog_status",
                     "ppi_reference_by_selector", "other_reference_days_and_latest",
                     "raw_days_and_latest")
            rows.write(json.dumps(dict(zip(names, row, strict=True)), separators=(",", ":")) + "\n")
    payload["catalog_identities"] = len(catalog)
    payload["catalog_identities_with_raw"] = sum(bool(row[8]) for row in catalog)
    payload["catalog_identities_with_ppi_reference"] = sum(bool(row[6]) for row in catalog)
    payload["catalog_identities_with_any_reference"] = sum(bool(row[6] or row[7]) for row in catalog)
    report = folder / "history-import.json"
    report.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(json.dumps({key: payload[key] for key in ("schema", "stored_rows", "quick_check",
                                                     "real_orders_sent", "preserved_rows")}))


if __name__ == "__main__":
    main()

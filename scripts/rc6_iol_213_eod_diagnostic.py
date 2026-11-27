"""Read the failing local archive timer and its admission conditions."""
from __future__ import annotations

import json
import re
import subprocess

from scripts.porota_rootless_host import decode


UNIT = "porota-local-eod-history-rc6.service"
JOURNAL_CODES = re.compile(r"\b(?:DOCKER_|LOCAL_EOD_|SCHEDULER_|RUNTIME_CLOSED_|OPEN_POSITIONS)[A-Z0-9_]{0,90}\b")
CHECK = '''
from contextlib import closing
from datetime import datetime, timezone
import json, os, shutil, sqlite3
from pathlib import Path
from bl_candle_engine import fingerprint
from cg_paper_workspace import database_path
from rc6_local_eod_history import HEADROOM, RESERVE, _committed_ready_cursor, _runtime_block

source = database_path()
target = Path(os.environ.get('HIST_DB_PATH','/app/data/market_history.db')).absolute()
assert source.is_file() and source.resolve() == source and target.resolve() == target and source != target
source_stat = source.stat()
source_id = fingerprint({'path': str(source), 'device': source_stat.st_dev, 'inode': source_stat.st_ino})
with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=.2)) as reader:
    reader.row_factory = sqlite3.Row
    reader.execute('PRAGMA query_only=ON')
    block = _runtime_block(reader, datetime.now(timezone.utc))
    hot = reader.execute('SELECT COALESCE(MAX(id),0) FROM candle_versions').fetchone()[0]
    opened = reader.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0]
ready = _committed_ready_cursor(target, source_id)
cursor = None
if target.is_file():
    with closing(sqlite3.connect(target.as_uri()+'?mode=ro', uri=True, timeout=.2)) as archive:
        archive.execute('PRAGMA query_only=ON')
        row = archive.execute('SELECT cursor, state FROM local_eod_cursor_v1 WHERE source_id=?', (source_id,)).fetchone()
        if row is not None and type(row[0]) is int and row[1] in {'LOCAL_EOD_READY','BACKLOG'}:
            last = archive.execute('SELECT 1 FROM local_eod_receipts_v1 WHERE source_id=? AND version_id=?',
                                   (source_id,row[0])).fetchone()
            cursor = {'version':row[0], 'state':row[1], 'has_last_receipt':last is not None}
size = sum(p.stat().st_size for suffix in ('','-wal','-shm') if (p:=Path(str(target)+suffix)).is_file())
print(json.dumps({'status':'OBSERVED','open_positions':opened,'paper_admission_block':block,
    'hot_version_high_water':hot,'cold_cursor':cursor,'cold_ready_cursor':ready,'cold_physical_bytes':size,
    'cold_physical_limit_bytes':2*1024**3-HEADROOM,'cold_physical_budget_exceeded':size>2*1024**3-HEADROOM,
    'cold_space_reserve_met':shutil.disk_usage(target.parent).free>=RESERVE,
    'cold_db_writable':target.is_file() and os.access(target,os.W_OK),
    'source_sha':os.environ.get('POROTA_BUILD_SHA'),'source_tree':os.environ.get('POROTA_CANDIDATE_TREE_SHA')}))
'''


def _journal_codes():
    command = ["/usr/bin/journalctl", "--unit", UNIT, "--since", "2026-11-27 00:00:00 UTC",
               "--lines", "36", "--no-pager", "--output=json", "--output-fields=MESSAGE,_SYSTEMD_UNIT"]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"state": "NO_VERIFICADO", "codes": []}
    if proc.returncode != 0 or len(proc.stdout) > 128 * 1024:
        return {"state": "NO_VERIFICADO", "codes": []}
    codes, entries = set(), 0
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("_SYSTEMD_UNIT") == UNIT and isinstance(row.get("MESSAGE"), str):
            entries += 1
            codes.update(JOURNAL_CODES.findall(row["MESSAGE"]))
    return {"state": "OBSERVED" if entries else "NO_JOURNAL_ENTRIES_OBSERVED", "entries": entries, "codes": sorted(codes)}


def observe_eod(manager, current):
    runtime = {"status": "NO_VERIFICADO"}
    try:
        proc = manager.docker.command_result("container", "exec", current["roles"]["observer"],
                                              "python", "-B", "-c", CHECK, timeout=10)
        if proc.returncode == 0 and len(proc.stdout) <= 8192:
            proof = decode(proc.stdout)
            if all(proof.get(key) == current[key] for key in ("source_sha", "source_tree")):
                runtime = proof
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        pass
    return {"unit": UNIT, "runtime_admission": runtime, "service_journal": _journal_codes()}

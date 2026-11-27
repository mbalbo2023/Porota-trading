"""Run one verified local archival batch and repeat the installed replay via Actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, ssh_run
from scripts.rc6_iol_213_readonly import EXPECTED, ROOT


ARCHIVE_SCHEMA = "porota.rc6.local-eod-applied.v1"
PATCH_SHA256 = "9a5bfa824de3e2e98c74473d3d729308f945337b3c149a55a1640f0f44507346"

CONTAINER = '''
import hashlib, json, os, sqlite3, sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from cg_paper_workspace import database_path

payload = sys.stdin.buffer.read(65537)
assert len(payload) <= 65536 and hashlib.sha256(payload).hexdigest() == sys.argv[1]
assert os.environ.get('POROTA_BUILD_SHA') == sys.argv[2]
assert os.environ.get('POROTA_CANDIDATE_TREE_SHA') == sys.argv[3]
assert os.environ.get('POROTA_RC6_MARKET_PROVIDER') == 'IOL_REST'
assert os.environ.get('REAL_ORDER_CAPABILITY') == 'BLOCKED'
module = {'__name__':'rc6_eod_verified_patch','__file__':'/app/rc6_local_eod_history.py'}
exec(compile(payload, 'rc6_local_eod_history.py', 'exec'), module)
hot = database_path()
cold = Path(os.environ.get('HIST_DB_PATH', '/app/data/market_history.db')).absolute()
assert str(hot) == '/app/data/paper_v17/observer_v17.db'
assert cold == Path('/app/data/market_history.db') and cold.resolve() == cold and cold.is_file()
assert cold.stat().st_nlink == 1

def archive_state():
    file_id = (cold.stat().st_dev, cold.stat().st_ino)
    hot_meta = hot.stat()
    source_id = module['fingerprint']({'path':str(hot),'device':hot_meta.st_dev,'inode':hot_meta.st_ino})
    with closing(sqlite3.connect(hot.as_uri()+'?mode=ro', uri=True, timeout=0.2)) as source:
        source.execute('PRAGMA query_only=ON')
        assert source.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone() == ('PRODUCTION_PAPER',0)
        watermark = source.execute('SELECT COALESCE(MAX(id),0) FROM candle_versions').fetchone()[0]
    with closing(sqlite3.connect(cold.as_uri()+'?mode=ro', uri=True, timeout=0.2)) as destination:
        destination.execute('PRAGMA query_only=ON')
        canonical = destination.execute('SELECT COUNT(*) FROM history_canonical_v2').fetchone()[0]
        revisions = destination.execute('SELECT COUNT(*),COALESCE(MAX(id),0) FROM history_versions_v2').fetchone()
        pointer = destination.execute('SELECT cursor,state FROM local_eod_cursor_v1 WHERE source_id=?',(source_id,)).fetchone()
        has_receipt = pointer is not None and destination.execute(
            'SELECT 1 FROM local_eod_receipts_v1 WHERE source_id=? AND version_id=?',(source_id,pointer[0])).fetchone() is not None
    size = sum(p.stat().st_size for suffix in ('','-wal','-shm') if (p:=Path(str(cold)+suffix)).is_file())
    return file_id, {'canonical_rows':canonical,'history_version_rows':revisions[0],'history_high_water':revisions[1],
                     'cold_bytes':size,'hot_high_water':watermark,'cold_cursor':pointer[0] if pointer else None,
                     'cold_state':pointer[1] if pointer and pointer[1] in {'BACKLOG','LOCAL_EOD_READY'} else None,
                     'latest_cursor_receipt':bool(has_receipt)}

before_file, before = archive_state()
result = module['consolidate'](hot,cold,as_of=datetime.now(timezone.utc))
after_file, after = archive_state()
assert before_file == after_file
assert all(before[key] == after[key] for key in ('canonical_rows','history_version_rows','history_high_water'))
assert result.get('provider_requests') == 0 and result.get('history_v2_writes') is False and result.get('real_routes') == 'NOT_CALLED'
print(json.dumps({'patch_sha256':sys.argv[1],'before':before,'after':after,
                  'free_bytes':module['shutil'].disk_usage(cold.parent).free,
                  'result':{key: result[key] for key in ('state','reason','imported','rejected','cursor','daily_groups','as_of','provider_requests') if key in result}}))
'''

ARCHIVE_REMOTE = '''
import hashlib, json, os, subprocess, sys
from pathlib import Path

try:
    assert 0 < os.getuid() == os.geteuid()
    source = Path.home()/'.local/state/porota-rc6/releases'/expected['source_sha']/'source'
    assert all(not path.is_symlink() for path in (source,*source.parents)) and source.stat().st_uid == os.getuid()
    assert hashlib.sha256(patch).hexdigest() == patch_hash
    sys.path.insert(0,str(source))
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, decode, require
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host
    root = Path('/opt/porota-trading')
    identity = Identity.current()
    with scheduler_lock(identity):
        manager,current,_ = verified_host(root,identity)
        require(all(current.get(key) == value for key,value in expected.items()) and
                current.get('market_provider') == 'IOL_REST','EOD_INSTALLED_RELEASE_MISMATCH')
        before = database_snapshot(root/DB_RELATIVE)
        require(before == current['database'],'EOD_PAPER_IDENTITY_MISMATCH')
        cid = current['roles']['observer']
        cmd = ['/usr/bin/docker','--host',f'unix://{identity.socket}','container','exec','-i','-w','/app',cid,
               'timeout','--signal=TERM','--kill-after=5s','45s','nice','-n','15','python','-B','-c',
               launcher,patch_hash,expected['source_sha'],expected['source_tree']]
        proc = subprocess.run(cmd,input=patch,capture_output=True,check=False,timeout=54,env=identity.environment(),cwd='/')
        require(database_snapshot(root/DB_RELATIVE) == before,'EOD_PAPER_IDENTITY_CHANGED')
        require(proc.returncode == 0 and 0 < len(proc.stdout) <= 8192,'EOD_ARCHIVE_COMMAND_FAILED')
        result = decode(proc.stdout)
        require(result.get('patch_sha256') == patch_hash,'EOD_PATCH_RECEIPT_MISMATCH')
    print(json.dumps({'schema':schema,**expected,'real_orders_sent':0,**result},sort_keys=True))
except Exception as error:
    reason = str(error) if type(error).__name__ == 'Hold' and str(error).startswith('EOD_') else type(error).__name__
    print(json.dumps({'schema':schema,**expected,'state':'BLOQUEADO','reason':reason},sort_keys=True))
    raise SystemExit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("archive", "replay"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {"state": "BLOQUEADO", "reason": "ACTION_CONNECTION_UNVERIFIED", **EXPECTED}
    try:
        with connection(os.environ) as (ssh, _, _):
            if args.mode == "archive":
                patch = (ROOT / "rc6_local_eod_history.py").read_bytes()
                if hashlib.sha256(patch).hexdigest() != PATCH_SHA256:
                    raise ValueError("ARCHIVE_SOURCE_MISMATCH")
                payload = (f"expected={EXPECTED!r}\npatch={patch!r}\npatch_hash={PATCH_SHA256!r}\n"
                           f"launcher={CONTAINER!r}\nschema={ARCHIVE_SCHEMA!r}\n" + ARCHIVE_REMOTE).encode()
                proc = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"], data=payload, timeout=95)
                schema = ARCHIVE_SCHEMA
            else:
                path = f".local/state/porota-rc6/releases/{EXPECTED['source_sha']}/source/scripts/porota_rootless_host.py"
                proc = ssh_run(ssh, ["/usr/bin/python3", "-B", path, "replay", "--source-sha", EXPECTED["source_sha"],
                                     "--source-tree", EXPECTED["source_tree"], "--attempt", EXPECTED["attempt"]], timeout=430)
                schema = "porota.iol.focus.v1"
        evidence = decode_receipt(proc.stdout)
        if evidence.get("schema") != schema or any(evidence.get(key) != value for key, value in EXPECTED.items()):
            raise ValueError("RECEIPT_IDENTITY_UNVERIFIED")
        result = evidence
        status = (result.get("result", {}).get("state") == "LOCAL_EOD_READY" if args.mode == "archive"
                  else result.get("status") == "PASS")
        return 0 if proc.returncode == 0 and result.get("real_orders_sent") == 0 and status else 1
    except (ValueError, OSError):
        return 1
    finally:
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

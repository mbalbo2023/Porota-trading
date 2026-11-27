"""Inventory installed release #214 and observe its existing IOL session."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {"source_sha": "eba76703be86e6a6b1406db36e5cb4cb87b21330",
            "source_tree": "5ac31c4b9cf2c16f477c08a6b5b3424f644a2667",
            "image_id": "sha256:274b143fd323ab0b79aff90890f54a7f5ce0ed84c3323fedd385ce85f28e83d2",
            "attempt": "45921311817-1"}
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "porota.iol.215-failure.readonly.v1"

REMOTE = "\nfrom collections import Counter\nfrom contextlib import closing\nfrom datetime import datetime, timezone\nimport hashlib, json, os, re, sqlite3, sys\nfrom pathlib import Path\n\nattempt='46009114277-1'\ncandidate_sha='9e199d27011dacfb3f5d156bbcd61ad156ba254e'\ncandidate_tree='6d3b10ac19988551e3a44ac1e088edebe1199f03'\nimage='sha256:f770a43285fc2b61b46633a647824720b99b24c2b782ea518cc59044f7e4ec81'\nsince='2026-11-27T13:47:35+00:00'\n\ndef collect():\n    assert 0 < os.getuid() == os.geteuid()\n    src=Path.home()/'.local/state/porota-rc6/releases'/candidate_sha/'source'\n    assert all(not p.is_symlink() for p in (src,*src.parents))\n    assert src.is_dir() and src.stat().st_uid==os.getuid()\n    sys.path.insert(0,str(src))\n    from scripts.porota_rootless_host import DB_RELATIVE, Host, Identity, database_snapshot, read_regular, require\n    from scripts.porota_rootless_jobs import scheduler_lock\n    from scripts.porota_operator_intent import private_read\n\n    identity=Identity.current()\n    root=Path('/opt/porota-trading')\n    with scheduler_lock(identity):\n        manager=Host(root,identity)\n        current=json.loads(read_regular(identity.state/'current.json'))\n        receipt=json.loads(read_regular(identity.state/'attempts'/attempt/'receipt.json'))\n        require(current['daemon_id']==manager.boundary()==receipt['daemon_id'],'AUDIT_DAEMON_MISMATCH')\n        require((receipt['attempt'],receipt['source_sha'],receipt['source_tree'],receipt['image_id'])==\n                (attempt,candidate_sha,candidate_tree,image),'AUDIT_ATTEMPT_MISMATCH')\n        require(receipt.get('failure_signature')=='IOL_RUNTIME_UNCLASSIFIED_DEGRADED',\n                'AUDIT_FAILURE_SIGNATURE_MISMATCH')\n        original=database_snapshot(root/DB_RELATIVE)\n        require(original==receipt.get('database') and original.get('real_orders_sent')==0,'AUDIT_PAPER_ZERO')\n        old_roles={role:manager.check_role(current,role)['State'].get('Running') is True\n                   for role in current.get('roles',{})}\n        attempt_roles=list(manager.discover_attempt(receipt).get('roles',{}))\n        with closing(sqlite3.connect((root/DB_RELATIVE).as_uri()+'?mode=ro',uri=True,timeout=1)) as db:\n            db.row_factory=sqlite3.Row\n            db.execute('PRAGMA query_only=ON')\n            state=dict(db.execute('SELECT mode,real_orders_sent,ppi_auth,process_state,session_state,heartbeat_at,last_market_data_at,detail FROM observer_state WHERE id=1').fetchone())\n            detail=state.pop('detail',None)\n            token=(detail or '').split(':',1)[0]\n            state['hold_code']=token if re.fullmatch(r'[A-Z][A-Z_0-9]{2,112}',token) else None\n            batch=re.search(r'(\\d+)/(\\d+) instrumentos actualizados de (\\d+) elegibles;',detail or '')\n            state['cycle_counts']=list(map(int,batch.groups())) if batch else None\n            state['gateway_reconnect']=str(detail or '').startswith('Login fallo: sesión del gateway pendiente o desconectada.')\n            cycles=[]\n            for row in db.execute('SELECT started_at,finished_at,eligible_total,selected_count,successful_count,failed_count,detail FROM universe_cycle_metrics ORDER BY id DESC LIMIT 8'):\n                if datetime.fromisoformat(row['started_at'])<datetime.fromisoformat(since):\n                    continue\n                summary={key:row[key] for key in row.keys() if key!='detail'}\n                try:\n                    plan=json.loads(row['detail'].split('factibilidad=',1)[1])\n                    summary['observation_counts']=plan.get('observation_counts')\n                    why=plan.get('opening_hold_reason')\n                    if isinstance(why,str) and re.fullmatch(r'[A-Z][A-Z_0-9]{2,112}',why):\n                        summary['hold']=why\n                    summary['catalog_status']=(plan.get('active_plan') or {}).get('catalog_status')\n                except (IndexError, TypeError, ValueError):\n                    pass\n                cycles.append(summary)\n            events=Counter()\n            for row in db.execute('SELECT event_type,detail,event_at FROM paper_events ORDER BY id DESC LIMIT 768'):\n                if datetime.fromisoformat(row['event_at'])<datetime.fromisoformat(since):\n                    continue\n                event=row['event_type']\n                if event in {'DATA_ERROR','DATA_REJECTED','DATA_TEMPORAL_HOLD','HTTP_BLOCKED','IOL_GATEWAY_ATTACHED','IOL_GATEWAY_REATTACH_REQUIRED','IOL_GATEWAY_METRICS_UNVERIFIED','CAUCION_LEDGER_INVALID'}:\n                    code=row['detail'].rsplit(': ',1)[-1]\n                    events[event+(':'+code if re.fullmatch(r'[A-Z][A-Z_0-9]{2,112}',code) else '')]+=1\n        try:\n            gate=json.loads(private_read(identity.operator/'paper-preopen-v2.json'))\n            gate={k:gate.get(k) for k in ('status','reason','candidate_sha','issued_at_ar','phase','market_provider')}\n        except (OSError, ValueError, TypeError):\n            gate={'status':'NO_VERIFICADO'}\n        require(database_snapshot(root/DB_RELATIVE)==original,'AUDIT_DATABASE_CHANGED')\n        return {'schema':schema,**expected,'observed_at':datetime.now(timezone.utc).isoformat(),\n                'read_only':True,'real_orders_sent':original['real_orders_sent'],'grants_generated':0,\n                'market_requests_generated':0,'auth_probe_sha256':hashlib.sha256(auth_source.encode()).hexdigest(),\n                'runtime_state':current.get('state'),'retired_roles_running':old_roles,'attempt_roles':attempt_roles,\n                'selected_current_sha':current.get('source_sha'),'fix_forward_ready':current.get('fix_forward_ready'),\n                'failure_signature':receipt.get('failure_signature'),'observer':state,'cycles':cycles,'events':dict(events),\n                'preopen':gate}\n\ntry:\n    print(json.dumps(collect(),sort_keys=True))\nexcept Exception as exc:\n    reason = str(exc) if type(exc).__name__=='Hold' and str(exc).startswith('AUDIT_') else type(exc).__name__\n    print(json.dumps({'schema':schema,**expected,'state':'NO_VERIFICADO','reason':reason}))\n    raise SystemExit(1)\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    auth = (ROOT / "scripts/rc6_iol_auth_diagnostic.py").read_text(encoding="utf-8")
    payload = (f"expected = {EXPECTED!r}\nauth_source = {auth!r}\nschema = {SCHEMA!r}\n" + REMOTE).encode()
    receipt = {"schema": SCHEMA, **EXPECTED, "state": "NO_VERIFICADO", "reason": "AUDIT_TRANSPORT_UNVERIFIED"}
    try:
        with connection(os.environ) as (ssh, _, _):
            proc = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"], data=payload, timeout=90)
        observed = decode_receipt(proc.stdout)
        require(observed.get("schema") == SCHEMA and all(observed.get(k) == v for k, v in EXPECTED.items()),
                "AUDIT_RELEASE_MISMATCH")
        if proc.returncode == 0:
            require(observed.get("read_only") is True and all(type(observed.get(k)) is int and observed[k] == 0
                    for k in ("real_orders_sent", "grants_generated")) and
                    type(observed.get("market_requests_generated")) is int and observed["market_requests_generated"] in (0,1,2) and
                    observed.get("auth_probe_sha256") == hashlib.sha256(auth.encode()).hexdigest(),
                    "AUDIT_PAPER_SAFETY_UNVERIFIED")
        receipt = observed
        return 0 if proc.returncode == 0 else 1
    except (ValueError, OSError):
        return 1
    finally:
        args.output.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

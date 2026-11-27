"""Collect evidence on the installed #213 release without promoting or restarting it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {"source_sha": "36617d9c1a1bd0f80a55bd519c3f052b33830944",
            "source_tree": "98433951559b1ab7b6a78e6cc7b178505f385d58",
            "image_id": "sha256:252c9705181005b03a4055bf61f5d01485832f46691a16c41ede440f4035bedf",
            "attempt": "45913851493-1"}
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "porota.iol.readonly-postdeploy.v1"


REMOTE = '''
import hashlib, json, os, sys
from pathlib import Path

def run_probe():
    assert 0 < os.getuid() == os.geteuid()
    source = Path.home() / '.local/state/porota-rc6/releases' / expected['source_sha'] / 'source'
    assert all(not path.is_symlink() for path in (source, *source.parents))
    assert source.is_dir() and source.stat().st_uid == os.getuid()
    sys.path.insert(0, str(source))
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, require
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host
    identity = Identity.current()
    root = Path('/opt/porota-trading')
    with scheduler_lock(identity):
        manager, current, _ = verified_host(root, identity)
        require(all(current.get(key) == value for key,value in expected.items()) and
                current.get('market_provider') == 'IOL_REST', 'AUDIT_INSTALLED_IDENTITY_MISMATCH')
        original = database_snapshot(root / DB_RELATIVE)
        require(current.get('database') == original, 'AUDIT_DATABASE_IDENTITY_UNVERIFIED')
        probes = {}
        for name, body in checked_sources.items():
            probes[name] = {'__name__': 'rc6_iol_readonly_'+name,
                            '__file__': str(source/'scripts'/('rc6_iol_'+name+'.py'))}
            exec(compile(body, name, 'exec'), probes[name])
        inventory = probes['installed_inventory']['run_host_inventory'](
            manager, current, runtime_probe_source=checked_sources['installed_inventory'])
        authentication = probes['auth_diagnostic']['observe_auth'](manager, current)
        require(database_snapshot(root / DB_RELATIVE) == original, 'AUDIT_DATABASE_IDENTITY_CHANGED')
        return {'schema': schema, **expected, 'read_only': True, 'real_orders_sent': 0,
                'inventory': inventory, 'authentication': authentication,
                'probe_sha256': {name: hashlib.sha256(body.encode()).hexdigest()
                                 for name,body in checked_sources.items()}}

try:
    print(json.dumps(run_probe(), sort_keys=True))
except Exception as exc:
    if type(exc).__name__ == 'Hold' and str(exc).startswith('AUDIT_'):
        print(json.dumps({'schema': schema, **expected, 'status': 'NO_VERIFICADO', 'reason': str(exc)}))
    else:
        print(json.dumps({'schema': schema, **expected, 'status': 'NO_VERIFICADO', 'reason': type(exc).__name__}))
    raise SystemExit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = {name: (ROOT / "scripts" / ("rc6_iol_" + name + ".py")).read_text(encoding="utf-8")
               for name in ("installed_inventory", "auth_diagnostic")}
    data = (f"expected = {EXPECTED!r}\nchecked_sources = {sources!r}\nschema = {SCHEMA!r}\n" + REMOTE).encode()
    receipt = {"schema": SCHEMA, **EXPECTED, "status": "NO_VERIFICADO", "reason": "AUDIT_TRANSPORT_UNVERIFIED"}
    try:
        with connection(os.environ) as (ssh, _, _):
            proc = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"], data=data, timeout=125)
        observed = decode_receipt(proc.stdout)
        require(observed.get("schema") == SCHEMA and all(observed.get(k) == v for k, v in EXPECTED.items()),
                "AUDIT_RELEASE_MISMATCH")
        if proc.returncode == 0:
            require(observed.get("read_only") is True and type(observed.get("real_orders_sent")) is int
                    and observed["real_orders_sent"] == 0, "AUDIT_PAPER_SAFETY_UNVERIFIED")
            require(observed.get("probe_sha256") == {name: hashlib.sha256(body.encode()).hexdigest()
                    for name, body in sources.items()}, "AUDIT_SOURCE_MISMATCH")
        receipt = observed
        return 0 if proc.returncode == 0 else 1
    except (ValueError, OSError):
        return 1
    finally:
        args.output.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

"""Run one read-only PAPER gateway and introspection probe through Actions."""
import json
import os
from pathlib import Path
import sys

from scripts.porota_actions_host import connection, ssh_run

CONTROL = Path(sys.argv[1])
EXPECTED_PRODUCT = "a7b1bb9f4c508b301fe44252972fdcac70904226"
EXPECTED_TREE = "b0d18a34750e94a973e5dd5a27916fa7ff881786"

def main():
    monitor = (CONTROL / "scripts/rc6_market_watch_readonly.py").read_text(encoding="utf-8")
    probe = Path(__file__).with_name("observer_probe.py").read_text(encoding="utf-8")
    remote = f"""
from datetime import datetime, timezone
import json
import sys
import types

receipt = {{"status": "NO_VERIFICADO", "read_only": True, "observed_at": datetime.now(timezone.utc).isoformat()}}
try:
    monitor = types.ModuleType("_rc6_market_watch")
    monitor.__file__ = "rc6_market_watch_readonly.py"
    sys.modules[monitor.__name__] = monitor
    exec(compile({monitor!r}, monitor.__file__, "exec"), monitor.__dict__)
    context = monitor._rootless_context()
    if (context.current.get("source_tree") != {EXPECTED_TREE!r}
            or context.current.get("deployment_sha") != {EXPECTED_PRODUCT!r}
            or context.items["observer"]["State"].get("Running") is not True):
        raise ValueError("PRODUCT_BINDING_UNVERIFIED")
    result = context.host.docker.command_result(
        "container", "exec", "-w", "/app", context.roles["observer"], "python", "-B", "-c",
        {probe!r}, timeout=12)
    if result.returncode or len(result.stdout) > 20000:
        raise ValueError("CONTAINER_RECEIPT_UNAVAILABLE")
    data = json.loads(result.stdout)
    identity = context.host.identity
    if (data.get("real_orders_sent") != 0 or
            monitor._read_private_current(identity.state / "current.json", identity.uid) != context.current):
        raise ValueError("PRODUCT_BINDING_UNVERIFIED")
    receipt.update(data=data, status="OBSERVED", source_sha=context.current["source_sha"],
                   source_tree=context.current["source_tree"], provider_requests_by_probe=0,
                   telegram_sends_by_probe=0, sqlite_writes_by_probe=0)
except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
    allowed = ("PRODUCT_BINDING_UNVERIFIED", "CONTAINER_RECEIPT_UNAVAILABLE")
    receipt["reason"] = str(error) if type(error) is ValueError and str(error) in allowed else "HOST_PROBE_UNAVAILABLE"
print(json.dumps(receipt, separators=(",", ":")))
"""
    try:
        with connection(os.environ) as (ssh, _, _):
            result = ssh_run(ssh, ["timeout", "25", "/usr/bin/python3", "-B", "-"],
                             data=remote.encode(), timeout=38)
        if result.returncode or len(result.stdout) > 23000:
            raise ValueError("SSH_READ_RECEIPT_UNAVAILABLE")
        receipt = json.loads(result.stdout)
        if not isinstance(receipt, dict) or receipt.get("read_only") is not True:
            raise ValueError("SSH_READ_RECEIPT_INVALID")
    except (OSError, RuntimeError, ValueError):
        receipt = {"status": "NO_VERIFICADO", "reason": "SSH_READ_PROBE_UNAVAILABLE"}
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt.get("status") == "OBSERVED" else 2

if __name__ == "__main__":
    raise SystemExit(main())

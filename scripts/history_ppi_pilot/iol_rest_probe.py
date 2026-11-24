"""Verify the documented IOL REST daily-bars route from the existing RC6 host."""
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, ssh_run


PROBE = r"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote

root = Path("/opt/porota-trading")
marker = root / "data/deploy/CURRENT_STATE_V2.json"
assert marker.resolve(strict=True) == marker and 0 < marker.stat().st_size <= 1_048_576
release = json.loads(marker.read_bytes())
candidate = release["candidate_sha"]
assert re.fullmatch(r"[a-f0-9]{40}", candidate)
assert release["validation_status"] == "VALIDATED_RUNTIME"
assert release["mode"] == "PRODUCTION_PAPER" and release["real_order_capability"] == "BLOCKED"
assert type(release["real_orders_sent"]) is int and release["real_orders_sent"] == 0
state = Path.home() / ".local/state/porota-rc6"
source = state / "releases" / candidate / "source"
assert os.getuid() == os.geteuid() > 0 and source.resolve(strict=True) == source
assert source.stat().st_uid == os.getuid() and not source.stat().st_mode & 0o022
sys.path.insert(0, str(source))
from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly
from iol_shadow_collector_rc6 import CollectionPolicy, RateGovernor

gov = RateGovernor(CollectionPolicy())
client = IOLRestReadOnly(state / "iol-rest", config=root / ".env", governor=gov)
symbol = "AL30"
market = "BCBA"
query = {"market": market, "from": "2026-11-19 00:00:00", "to": "2026-11-20 23:59:59"}
result = {"schema": "rc6.history.iol-rest-route.v1", "candidate_sha": candidate,
          "deployment_sha": release["deployment_sha"], "real_orders_sent": 0,
          "symbol": symbol, "market": market, "production_sql_writes": 0,
          "captured_at": datetime.now(timezone.utc).isoformat()}
try:
    raw = client._get(f"/broker/market-data/{quote(symbol, safe='')}/bars", query)
    if not isinstance(raw, dict) or (raw.get("symbol"), raw.get("market")) != (symbol, market):
        raise ValueError("IOL_REST_HISTORY_IDENTITY_UNVERIFIED")
    bars = raw.get("bars")
    if not isinstance(bars, list) or raw.get("total_bars") != len(bars) or len(bars) > 2:
        raise ValueError("IOL_REST_HISTORY_ENVELOPE_UNVERIFIED")
    result.update(status="CAPTURED" if bars else "EMPTY", rows=len(bars),
                  fields=sorted({key for bar in bars if isinstance(bar, dict) for key in bar}),
                  payload_sha256=sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                                                  allow_nan=False).encode()).hexdigest())
except IOLRestError as error:
    result.update(status="PROVIDER_DENIED", error=str(error), http_status=error.status_code,
                  retry_after=error.retry_after)
except ValueError as error:
    result.update(status="NO_VERIFICADO", error=type(error).__name__)
result["provider_calls"] = gov.calls_total
print(json.dumps(result))
"""


def main():
    with connection(os.environ) as (ssh, _, _):
        remote = ssh_run(ssh, ["python3", "-B", "-c", PROBE], timeout=55)
    if remote.returncode != 0:
        raise SystemExit("IOL_REST_HISTORY_HOST_PROBE_FAILED")
    result = json.loads(remote.stdout)
    assert result["schema"] == "rc6.history.iol-rest-route.v1" and result["real_orders_sent"] == 0
    target = Path(os.environ["RUNNER_TEMP"]) / "iol-rest-bars.json"
    target.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(result["status"])


if __name__ == "__main__":
    main()

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
    def field_names(keys):
        return sorted(k for k in keys if isinstance(k, str) and re.fullmatch(r"[A-Za-z0-9_]{1,40}", k))
    result.update(shape=type(raw).__name__,
                  payload_sha256=sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                                                   allow_nan=False).encode()).hexdigest())
    result["documented_fields"] = field_names(raw) if isinstance(raw, dict) else []
    bars = raw.get("bars") if isinstance(raw, dict) else raw
    result["bars_shape"] = type(bars).__name__
    if isinstance(raw, dict):
        result["identity_matches"] = (raw.get("symbol"), raw.get("market")) == (symbol, market)
        result["reported_total_type"] = type(raw.get("total_bars")).__name__
        result["reported_total"] = raw.get("total_bars") if type(raw.get("total_bars")) is int else None
    if isinstance(bars, list):
        result["rows"] = len(bars)
        result["fields"] = field_names({key for row in bars[:5] if isinstance(row, dict) for key in row})
        result["rows_in_requested_dates"] = sum(
            type(row) is dict and str(row.get("datetime", ""))[:10] in {"2026-11-19", "2026-11-20"}
            for row in bars)
    if result.get("identity_matches") and result.get("reported_total") == result.get("rows") and "rows" in result:
        result["status"] = "CAPTURED" if result["rows_in_requested_dates"] else "EMPTY_REQUESTED_WINDOW"
    else:
        result["status"] = "IOL_REST_HISTORY_ENVELOPE_UNVERIFIED"
except IOLRestError as error:
    result.update(status="PROVIDER_DENIED", error=str(error), http_status=error.status_code,
                  retry_after=error.retry_after)
except ValueError:
    result.update(status="IOL_REST_HISTORY_RESPONSE_INVALID")
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

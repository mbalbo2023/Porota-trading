"""Run the HISTORY pilot through the existing rootless Actions transport."""
import json
import os
from pathlib import Path
import hashlib
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.request import Request, urlopen

from scripts.porota_actions_host import connection, ssh_run


MISSING_DATES = {"2026-04-21", "2026-06-05"}


def data912_capture():
    """Request the documented public endpoint and retain only the two gaps."""
    request = Request("https://data912.com/historical/cedears/AAPL",
                      headers={"User-Agent": "Porota-Trading/17.0 historical-pilot"})
    try:
        with urlopen(request, timeout=9) as response:
            limit = 4 * 1024 * 1024
            raw = response.read(limit + 1)
            if len(raw) > limit:
                return {"status": "RESPONSE_TOO_LARGE"}
        series = json.loads(raw)
        if not isinstance(series, list):
            return {"status": "RESPONSE_SHAPE_UNVERIFIED"}
        fields = ("date", "o", "h", "l", "c", "v", "currency", "volume_kind")
        bars = [{key: row[key] for key in fields if key in row} for row in series
                if isinstance(row, dict) and str(row.get("date", ""))[:10] in MISSING_DATES]
        if len(bars) > 8:
            return {"status": "DUPLICATE_SCOPE_UNVERIFIED"}
        captured = {"status": "CAPTURED", "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "captured_at": datetime.now(timezone.utc).isoformat(), "bars": bars}
        json.dumps(captured, allow_nan=False)
        return captured
    except (OSError, HTTPException, ValueError):
        return {"status": "SOURCE_UNAVAILABLE"}


HOST = r"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

deployment = "28f8337e9026fcfea6dace798d5c9e7267f85d8a"
image = "sha256:8fa64b29c93d7e2cf39db8e3a505da4bbaf633494f8e73c038ce73df3f15df12"
candidate = "969980989a414791e78c7c36f98b59c4e1be161d"
marker = Path("/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json")
uid = os.getuid()
assert uid == os.geteuid() > 0
docker = ["/usr/bin/docker", "--host", f"unix:///run/user/{uid}/porota-rc6-docker.sock"]

def runtime():
    assert 0 < marker.stat().st_size < 1_048_576
    release = json.loads(marker.read_bytes())
    assert (release["deployment_sha"], release["candidate_sha"], release["image_id"]) == (deployment, candidate, image)
    assert release["validation_status"] == "VALIDATED_RUNTIME"
    assert release["mode"] == "PRODUCTION_PAPER" and release["real_order_capability"] == "BLOCKED"
    assert type(release["real_orders_sent"]) is int and release["real_orders_sent"] == 0
    ids = subprocess.check_output([*docker, "ps", "--no-trunc", "--filter",
        "label=io.porota.project=rc6", "--filter", "label=io.porota.role=observer",
        "--format", "{{.ID}}"], text=True, timeout=5).splitlines()
    assert len(ids) == 1 and re.fullmatch(r"[0-9a-f]{64}", ids[0])
    actual = subprocess.check_output([*docker, "container", "inspect", "--format", "{{.Image}}",
        ids[0]], text=True, timeout=5).strip()
    assert actual == image
    return ids[0]

observer = runtime()
source = sys.stdin.read(32_769)
assert 0 < len(source) <= 32_768
run = subprocess.run([*docker, "container", "exec", "-i", "-w", "/app", observer,
    "python", "-B", "-"], input=source, text=True, capture_output=True, timeout=35)
if run.returncode != 0:
    code = run.stdout.strip()
    if re.fullmatch(r"PILOT_ERROR:[A-Za-z_]+", code):
        print(code)
    raise SystemExit(2)
assert len(run.stdout) < 65_536 and runtime() == observer
result = json.loads(run.stdout)
assert result["schema"] == "rc6.history.ppi-cutoff-pilot.v1"
result.update(deployment_sha=deployment, candidate_sha=candidate, image_id=image,
              same_runtime=True, real_orders_sent=0, order_guard="BLOCKED")
print(json.dumps(result))
"""


def main():
    pilot = Path(__file__).with_name("pilot.py").read_text()
    external = json.dumps(data912_capture(), allow_nan=False)
    pilot = f"import json\nDATA912_CAPTURE = json.loads({external!r})\n" + pilot
    with connection(os.environ) as (ssh, _, _):
        result = ssh_run(ssh, ["python3", "-B", "-c", HOST], data=pilot.encode(), timeout=60)
    if result.returncode != 0:
        code = result.stdout.decode(errors="replace").strip() if isinstance(result.stdout, bytes) else result.stdout.strip()
        if code.startswith("PILOT_ERROR:") and code.removeprefix("PILOT_ERROR:").replace("_", "").isalpha():
            raise SystemExit(code)
        raise SystemExit("HISTORY_PPI_PILOT_HOST_UNVERIFIED")
    payload = json.loads(result.stdout)
    assert payload["same_runtime"] is True and payload["real_orders_sent"] == 0
    assert payload["provider_requests"] == payload["production_sql_writes"] == 0
    payload["data912_http_attempts_from_runner"] = 1
    target = Path(os.environ["RUNNER_TEMP"]) / "rc6-history-ppi-pilot.json"
    target.write_text(json.dumps(payload, indent=2) + "\n")
    print("HISTORY_PPI_PILOT=COMPLETE")


if __name__ == "__main__":
    main()

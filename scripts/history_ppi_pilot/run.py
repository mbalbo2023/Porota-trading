"""Run the HISTORY pilot through the existing rootless Actions transport."""
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, ssh_run


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
    target = Path(os.environ["RUNNER_TEMP"]) / "rc6-history-ppi-pilot.json"
    target.write_text(json.dumps(payload, indent=2) + "\n")
    print("HISTORY_PPI_PILOT=COMPLETE")


if __name__ == "__main__":
    main()

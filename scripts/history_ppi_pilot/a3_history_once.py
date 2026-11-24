"""Use the deployed A3 CEM historical importer once on the rootless observer."""
import json
import os
from pathlib import Path
import re

from scripts.porota_actions_host import connection, ssh_run


HOST = r"""
import json
import os
from pathlib import Path
import re
import sys

root = Path("/opt/porota-trading")
marker = root / "data/deploy/CURRENT_STATE_V2.json"
assert marker.resolve(strict=True) == marker and 0 < marker.stat().st_size < 1_048_576
release = json.loads(marker.read_bytes())
candidate, deployment = sys.argv[1:]
assert re.fullmatch(r"[a-f0-9]{40}", candidate) and re.fullmatch(r"[a-f0-9]{40}", deployment)
assert (release["candidate_sha"],release["deployment_sha"]) == (candidate,deployment)
assert release["validation_status"] == "VALIDATED_RUNTIME"
assert release["mode"] == "PRODUCTION_PAPER" and release["real_order_capability"] == "BLOCKED"
assert type(release["real_orders_sent"]) is int and release["real_orders_sent"] == 0
source = Path.home() / ".local/state/porota-rc6/releases" / candidate / "source"
assert os.getuid() == os.geteuid() > 0 and source.resolve(strict=True) == source
assert source.stat().st_uid == os.getuid() and not source.stat().st_mode & 0o022
sys.path.insert(0, str(source))
from scripts.porota_rootless_host import Identity
from scripts.porota_rootless_jobs import scheduler_lock, verified_host

identity = Identity.current()
input_code = sys.stdin.buffer.read(16385)
assert 0 < len(input_code) <= 16384
with scheduler_lock(identity):
    manager,current,_ = verified_host(root,identity)
    role = "observer"
    assert manager.check_role(current,role)["State"]["Running"] is True
    command = manager.docker.command_result("container","exec","-w","/app",current["roles"][role],
        "nice","-n","10","python","-B","-c",input_code.decode(),timeout=1200)
    assert command.returncode == 0 and len(command.stdout) <= 1_048_576
    result = json.loads(command.stdout)
    assert result["schema"] == "rc6.history.a3-cem-once.v1" and result["real_orders_sent"] == 0
    assert manager.check_role(current,role)["State"]["Running"] is True
result.update(deployment_sha=deployment,candidate_sha=candidate,image_id=release["image_id"])
print(json.dumps(result))
"""


CONTAINER = r"""
import fcntl
import json
from pathlib import Path

from ex_a3_history_job_rc6 import Store
from ew_a3_history_rc6 import run,load_targets
from cz_a3_cem_public_history_hf6 import CEMReadOnlyError

data = Path("/app/data")
store = Store(str(data/"paper_v17/observer_v17.db"))
with store.connect() as conn:
    state = tuple(conn.execute("SELECT mode,session_state,real_orders_sent FROM observer_state WHERE id=1").fetchone())
assert state == ("PRODUCTION_PAPER","MARKET_CLOSED",0)
targets = load_targets(store)
try:
    with (data/".rc6-history-cutoff-repair.lock").open("a+",encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        result = run(store,mode="BOOTSTRAP",batch_limit=len(targets),throttle_seconds=1.0)
except CEMReadOnlyError as error:
    result = {"ran":False,"reason":"A3_CEM_SOURCE_UNVERIFIED","error_class":type(error).__name__,
              "selected":len(targets),"execution_allowed":False}
with store.connect() as conn:
    mode,orders = tuple(conn.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone())
assert (mode,orders)==("PRODUCTION_PAPER",0)
result.update(schema="rc6.history.a3-cem-once.v1",real_orders_sent=orders)
print(json.dumps(result))
"""


def main():
    control = json.loads(Path(__file__).with_name("promotion.json").read_text())
    candidate, deployment = control["candidate_sha"], control["deployment_sha"]
    for sha in (candidate,deployment):
        assert re.fullmatch(r"[a-f0-9]{40}",sha)
    with connection(os.environ) as (ssh,_,_):
        reply = ssh_run(ssh,["python3","-B","-c",HOST,candidate,deployment],data=CONTAINER.encode(),timeout=1250)
    if reply.returncode != 0:
        raise SystemExit("A3_HISTORY_HOST_UNVERIFIED")
    result = json.loads(reply.stdout)
    assert result["deployment_sha"] == deployment and result["candidate_sha"] == candidate
    assert result["schema"] == "rc6.history.a3-cem-once.v1" and result["real_orders_sent"] == 0
    (Path(os.environ["RUNNER_TEMP"])/"a3-history.json").write_text(json.dumps(result)+"\n")
    print(json.dumps({key: result.get(key) for key in ("state","reason","selected","matched",
                                                      "canonical_updates","real_orders_sent")}))


if __name__ == "__main__":
    main()

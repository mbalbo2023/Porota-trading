"""Execute the current Predeploy cleanup against a journalled fake Docker.

The shell comes from the actual workflow. No Docker daemon, host service or
provider is contacted; fake mutations affect only disposable fixture files.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CID = "a" * 64
IMAGE = "sha256:" + "b" * 64
RUN = "123-2"
IDENTITY = [CID, IMAGE, RUN]


def cleanup_shell():
    workflow = yaml.safe_load((ROOT / ".github/workflows/porota-predeploy-v2.yml").read_text())
    scripts = [step["run"] for job in workflow["jobs"].values()
               for step in job.get("steps", []) if isinstance(step.get("run"), str)
               and "owned() {" in step["run"] and "cleanup() {" in step["run"]]
    assert len(scripts) == 1, "Expected the current Predeploy ownership/cleanup implementation"
    source = scripts[0]
    return source[source.index("owned() {"):source.index("trap cleanup EXIT")]


def exercise(tmp_path, *, identities=None, running=False, stop_exit=0, prior_exit=0):
    fixture = tmp_path / "docker-state.json"
    journal = tmp_path / "docker-journal.jsonl"
    resource = tmp_path / "container-alive"
    resource.write_text("retain unless exact owned cleanup succeeds")
    fixture.write_text(json.dumps({"identities": identities or [IDENTITY],
                                   "running": running, "stop_exit": stop_exit,
                                   "identity_reads": 0}))
    binary = tmp_path / "docker"
    binary.write_text("#!" + sys.executable + "\n" + textwrap.dedent('''
        import json, os, sys
        from pathlib import Path
        args = sys.argv[1:]
        state_file = Path(os.environ["CLEANUP_FIXTURE"])
        state = json.loads(state_file.read_text())
        with open(os.environ["CLEANUP_JOURNAL"], "a") as stream:
            stream.write(json.dumps(args) + "\\n")
        cid = os.environ["OWNED_CID"]
        if len(args) == 4 and args[:2] == ["inspect", "-f"] and args[3] == cid:
            if args[2] == "{{.State.Running}}":
                print(str(state["running"]).lower())
            elif "{{.Id}}|{{.Image}}|" in args[2]:
                index = min(state["identity_reads"], len(state["identities"]) - 1)
                print("|".join(state["identities"][index]))
                state["identity_reads"] += 1
                state_file.write_text(json.dumps(state))
            else:
                raise SystemExit(97)
        elif args == ["stop", "--time", "10", cid]:
            if state["stop_exit"]:
                raise SystemExit(state["stop_exit"])
            state["running"] = False
            state_file.write_text(json.dumps(state))
        elif args == ["rm", cid]:
            if state["running"]:
                raise SystemExit(98)
            Path(os.environ["CLEANUP_RESOURCE"]).unlink()
        else:
            raise SystemExit(99)
    '''))
    binary.chmod(0o700)
    environment = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "CLEANUP_FIXTURE": str(fixture), "CLEANUP_JOURNAL": str(journal),
        "CLEANUP_RESOURCE": str(resource), "OWNED_CID": CID, "IMAGE_ID": IMAGE,
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "PRIOR_EXIT": str(prior_exit),
    }
    program = ('set -Eeuo pipefail\nOWNED_CIDS=("$OWNED_CID")\n' + cleanup_shell() +
               '\ntrap cleanup EXIT\nexit "$PRIOR_EXIT"\n')
    result = subprocess.run(["bash", "-c", program], cwd=tmp_path, env=environment,
                            text=True, capture_output=True, timeout=10, check=False)
    calls = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    return result, calls, resource


def mutations(calls):
    return [call for call in calls if call[0] != "inspect"]


@pytest.mark.parametrize("field", ["cid", "image", "run"])
def test_foreign_identity_cannot_stop_or_remove_container(tmp_path, field):
    identity = list(IDENTITY)
    index = {"cid": 0, "image": 1, "run": 2}[field]
    identity[index] = {"cid": "c" * 64, "image": "sha256:" + "d" * 64, "run": "999-1"}[field]
    result, calls, resource = exercise(tmp_path, identities=[identity], running=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert mutations(calls) == []
    assert resource.exists()


def test_stop_failure_preserves_container_without_remove(tmp_path):
    result, calls, resource = exercise(tmp_path, running=True, stop_exit=43)
    assert result.returncode != 0, result.stdout + result.stderr
    assert mutations(calls) == [["stop", "--time", "10", CID]]
    assert resource.exists()


def test_identity_drift_before_remove_revokes_cleanup_authority(tmp_path):
    result, calls, resource = exercise(tmp_path, identities=[IDENTITY, [CID, IMAGE, "999-1"]])
    assert result.returncode != 0, result.stdout + result.stderr
    assert len([call for call in calls if call[0] == "inspect" and "{{.Id}}" in call[2]]) == 2
    assert mutations(calls) == []
    assert resource.exists()


def test_owned_stopped_container_is_removed_only_by_exact_cid(tmp_path):
    result, calls, resource = exercise(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mutations(calls) == [["rm", CID]]
    assert not resource.exists()


def test_successful_cleanup_preserves_original_test_failure(tmp_path):
    result, calls, resource = exercise(tmp_path, prior_exit=23)
    assert result.returncode == 23, result.stdout + result.stderr
    assert mutations(calls) == [["rm", CID]]
    assert not resource.exists()

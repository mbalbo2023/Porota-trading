"""OFFHOST unit contract for native session/cadence ordering, never native GO."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/porota-predeploy-v2.yml"


def _native_step():
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]
    return next(step["run"] for step in job["steps"]
                if step.get("name") == "Exact image native sizing and generated catalog proof")


def _run_orchestration(tmp_path, *, fail_label=""):
    """Use the real source-only orderer, replacing only external native work."""
    fake_native = tmp_path / "fake_native.py"
    fake_native.write_text('''import json, os, sys, time
from pathlib import Path
label = sys.argv[1]
events = Path(os.environ["RC6_EVENTS"])
def record(event):
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps([label, event, time.monotonic()]) + "\\n")
record("start")
time.sleep({"session": .32, "cadence-fresh": .22,
            "cadence-legacy": .22}.get(label, .01))
record("end")
if label == os.getenv("RC6_FAIL_LABEL"):
    sys.exit(7)
''', encoding="utf-8")
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path),
           "RC6_TEST_PYTHON": sys.executable,
           "RC6_TEST_RUNNER": str(fake_native),
           "RC6_EVENTS": str(tmp_path / "events.jsonl"),
           "RC6_FAIL_LABEL": fail_label}
    shell = '''set -Eeuo pipefail
run_native_proof() { "$RC6_TEST_PYTHON" "$RC6_TEST_RUNNER" "$@"; }
source ./scripts/rc6_predeploy_native_orchestration.sh
rc6_native_session_cadence
echo 'RC6_NATIVE_UNIT_ORCHESTRATION=GREEN'
'''
    result = subprocess.run(["bash", "-c", shell], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=10)
    event_path = tmp_path / "events.jsonl"
    events = ([json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
              if event_path.exists() else [])
    return result, events


def test_session_overlaps_ordered_cadence_and_all_proofs_finish(tmp_path):
    result, events = _run_orchestration(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    times = {(label, event): stamp for label, event, stamp in events}
    assert times["session", "start"] < times["cadence-fresh", "end"]
    assert times["cadence-fresh", "end"] <= times["cadence-legacy", "start"]
    assert times["session", "end"] < times["cadence-legacy", "end"]
    assert "RC6_NATIVE_UNIT_ORCHESTRATION=GREEN" in result.stdout
    assert "POROTA_EXACT_IMAGE_NATIVE_SIZING=GREEN" not in result.stdout


def test_session_failure_refuses_success_even_when_cadence_passes(tmp_path):
    result, events = _run_orchestration(tmp_path, fail_label="session")
    assert result.returncode != 0
    assert any(row[:2] == ["cadence-legacy", "end"] for row in events)
    assert "RC6_NATIVE_UNIT_ORCHESTRATION=GREEN" not in result.stdout
    assert "POROTA_EXACT_IMAGE_NATIVE_SIZING=GREEN" not in result.stdout


def test_fresh_failure_waits_for_session_without_starting_legacy(tmp_path):
    result, events = _run_orchestration(tmp_path, fail_label="cadence-fresh")
    assert result.returncode != 0
    assert any(row[:2] == ["session", "end"] for row in events)
    assert not any(row[0] == "cadence-legacy" for row in events)
    assert "RC6_NATIVE_UNIT_ORCHESTRATION=GREEN" not in result.stdout


def test_legacy_failure_waits_for_session_and_refuses_green(tmp_path):
    result, events = _run_orchestration(tmp_path, fail_label="cadence-legacy")
    assert result.returncode != 0
    assert any(row[:2] == ["session", "end"] for row in events)
    assert any(row[:2] == ["cadence-legacy", "end"] for row in events)
    assert "RC6_NATIVE_UNIT_ORCHESTRATION=GREEN" not in result.stdout


def test_real_native_step_binds_only_one_offhost_orderer():
    source = _native_step()
    assert source.count("source ./scripts/rc6_predeploy_native_orchestration.sh") == 1
    assert source.count("rc6_native_session_cadence") == 1
    assert "run_native_proof session python" not in source


@pytest.mark.parametrize("failed_stage, failure_kind", [
    ("cadence-fresh", "start_rc"), ("cadence-fresh", "inspect_bad"),
    ("cadence-legacy", "start_rc"), ("cadence-legacy", "inspect_bad"),
])
def test_multicommand_callee_failure_cannot_be_hidden_by_later_success(
    tmp_path, failed_stage, failure_kind
):
    """A failed native start/inspect cannot be overwritten by a later rm success."""
    trace = tmp_path / "multicommand-events"
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path),
           "RC6_FAIL_LABEL": failed_stage, "RC6_FAIL_KIND": failure_kind,
           "RC6_TRACE": str(trace)}
    shell = '''set -Eeuo pipefail
run_native_proof() {
  local label="$1"
  if [[ "$label" == "$RC6_FAIL_LABEL" ]]; then
    printf '%s\\n' "$label:before_failure" >> "$RC6_TRACE"
    if [[ "$RC6_FAIL_KIND" == start_rc ]]; then
      set +e
      local rc6_start_rc=7
      set -e
      test "$rc6_start_rc" -eq 0
    else
      test '7|true' = '0|false'
    fi
    # Stand-in for legacy docker rm by name returning 0 after a bad start.
    printf '%s\\n' "$label:unsafe_after_failure" >> "$RC6_TRACE"
  fi
  printf '%s\\n' "$label:completed" >> "$RC6_TRACE"
}
source ./scripts/rc6_predeploy_native_orchestration.sh
rc6_native_session_cadence
echo 'RC6_NATIVE_UNIT_ORCHESTRATION=GREEN'
'''
    result = subprocess.run(["bash", "-c", shell], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=5)
    events = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    assert result.returncode != 0
    assert f"{failed_stage}:before_failure" in events
    assert f"{failed_stage}:unsafe_after_failure" not in events
    if failed_stage == "cadence-fresh":
        assert not any(event.startswith("cadence-legacy:") for event in events)
    assert "RC6_NATIVE_UNIT_ORCHESTRATION=GREEN" not in result.stdout

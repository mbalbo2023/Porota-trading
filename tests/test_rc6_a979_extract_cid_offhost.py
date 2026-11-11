"""OFFHOST: extraction never removes a foreign or unverifiable CID."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-predeploy-v2.yml"

def _extract_app_shell(tmp_path: Path, case: str) -> tuple[int, str, list[str], bool]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Extract exact image application filesystem")
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    # Redirect the existing fixed staging path into the disposable fixture;
    # execute the actual Docker lifecycle without touching shared /tmp paths.
    script = script.replace("/tmp/porota-predeploy-app", str(stage / "porota-predeploy-app"))
    foreign = tmp_path / "foreign-container-running"
    foreign.write_text("FOREIGN", encoding="utf-8")
    binary = tmp_path / "docker"
    trace = tmp_path / "docker.calls"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'case "$1" in\n'
        ' create)\n'
        '   if [[ "$RC6_FAKE_CASE" == "create_failure" ]]; then exit 44; fi\n'
        '   if [[ "$RC6_FAKE_CASE" == "forged_id" ]]; then echo foreign-container; exit 0; fi\n'
        '   printf "c%.0s" {1..64}; echo; exit 0;;\n'
        ' inspect)\n'
        '   if [[ "$RC6_FAKE_CASE" == "inspect_failure" ]]; then exit 47; fi\n'
        '   if [[ "$RC6_FAKE_CASE" == "changed_owner" && -f "$RC6_TMPDIR/copied" ]]; then\n'
        '      printf "c%.0s" {1..64}; echo "|FOREIGN|v2|1999"; exit 0; fi\n'
        '   if [[ "$RC6_FAKE_CASE" == "wrong_owner" ]]; then\n'
        '      printf "c%.0s" {1..64}; echo "|FOREIGN|v2|1999"; exit 0; fi\n'
        '   printf "c%.0s" {1..64}; echo "|0123456789012345678901234567890123456789|v2|1999"; exit 0;;\n'
        ' cp)\n'
        '   if [[ "$RC6_FAKE_CASE" == "copy_failure" ]]; then exit 45; fi\n'
        '   touch "$RC6_TMPDIR/copied"\n'
        '   touch "$RC6_TMPDIR/porota-predeploy-app/Dockerfile"; exit 0;;\n'
        ' rm)\n'
        '   for arg in "$@"; do\n'
        '     if [[ "$arg" == "foreign-container" ]]; then rm -f "$FOREIGN_MARKER"; fi\n'
        '   done\n'
        '   if [[ "$RC6_FAKE_CASE" == "rm_failure" ]]; then exit 46; fi\n'
        '   exit 0;;\n'
        'esac\n'
        'exit 97\n', encoding="utf-8",
    )
    binary.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "RC6_TMPDIR": str(stage), "GITHUB_RUN_ID": "1999",
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           "IMAGE": "porota-predeploy-v2:0123456789012345678901234567890123456789",
           "RC6_FAKE_CASE": case, "FOREIGN_MARKER": str(foreign), "DOCKER_TRACE": str(trace)}
    result = subprocess.run(["bash", "-c", script], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result.returncode, result.stdout, calls, foreign.exists()


@pytest.mark.parametrize("case,expect_remove", (
    ("create_failure", False), ("forged_id", False),
    ("wrong_owner", False), ("inspect_failure", False),
    ("changed_owner", False), ("copy_failure", True),
    ("rm_failure", True), ("healthy", True),
))
def test_extract_container_attests_ownership_before_nonforce_cleanup(
    tmp_path: Path, case: str, expect_remove: bool
) -> None:
    code, stdout, calls, foreign_alive = _extract_app_shell(tmp_path, case)
    assert (code == 0) == (case == "healthy"), (case, code, stdout)
    assert foreign_alive
    assert any(s.startswith("rm ") and s.endswith("c" * 64) for s in calls) == expect_remove
    assert not any(s.startswith("rm -f ") for s in calls)
    assert ("POROTA_EXACT_ARTIFACT_EXTRACT=GREEN" in stdout) == (case == "healthy")


@pytest.mark.parametrize("step_name", ["Local cleanup", "Exact image native sizing and generated catalog proof"])
def test_unproven_cleanup_preserves_foreign_resources_and_fails_closed(tmp_path, step_name):
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == step_name)
    marker = tmp_path / "foreign"
    marker.write_text("retain", encoding="utf-8")
    trace = tmp_path / "trace"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'if [[ "$1" == ps ]]; then echo foreign; exit 0; fi\n'
        'if [[ "$1" == rm ]]; then rm -f "$FOREIGN_MARKER"; exit 0; fi\n'
        'exit 7\n', encoding="utf-8")
    docker.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "DOCKER_TRACE": str(trace), "FOREIGN_MARKER": str(marker),
           "RUNNER_TEMP": str(tmp_path), "GITHUB_RUN_ID": "123",
           "GITHUB_RUN_ATTEMPT": "1", "IMAGE": "candidate"}
    # Redirect legacy fixed cleanup paths into the disposable fixture.
    script = script.replace("/tmp/porota-", str(tmp_path / "porota-"))
    result = subprocess.run(["bash", "-c", script], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    assert marker.exists()
    assert result.returncode == 79
    assert "HOLD" in result.stdout
    assert not trace.exists()


def test_predeploy_green_certificate_disabled_during_cleanup_hold():
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    summary = next(step for step in steps if step.get("name") == "Predeploy summary")
    assert summary["if"] == "${{ false }}"

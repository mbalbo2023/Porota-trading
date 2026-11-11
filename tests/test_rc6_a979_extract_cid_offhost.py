"""OFFHOST: extraction never removes a foreign or unverifiable CID."""

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-predeploy-v2.yml"

def _extract_app_shell(
    tmp_path: Path, case: str, *, runner_temp: Path | None = None,
    attempt: str = "1",
) -> tuple[int, str, list[str], bool]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Extract exact image application filesystem")
    runner = runner_temp or tmp_path / "runner"
    runner.mkdir(mode=0o700, exist_ok=True)
    stage = runner / f"porota-predeploy-app-1999-{attempt}"
    github_env = tmp_path / "github_env"
    github_env.write_text("", encoding="utf-8")
    # Invoke unmodified workflow shell. Interpose filesystem commands so the
    # original global /tmp stage is rejected, never deleted during RED.
    for command in ("rm", "mkdir"):
        guard = tmp_path / command
        guard.write_text(
            "#!/usr/bin/env bash\n"
            'for path in "$@"; do\n'
            '  [[ "$path" == -* ]] && continue\n'
            '  if [[ "$path" == /* && "$path" != "$RC6_ALLOWED_ROOT/"* ]]; then\n'
            '    echo "FORBIDDEN_GLOBAL_STAGE_WRITE:$path" >&2; exit 97\n'
            '  fi\n'
            'done\n'
            f'exec /usr/bin/{command} "$@"\n', encoding="utf-8",
        )
        guard.chmod(0o700)
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
        '   [[ "$3" == "$RC6_EXPECTED_STAGE/" || "$3" == "$RC6_EXPECTED_STAGE/app/" ]] || exit 98\n'
        '   if [[ "$RC6_FAKE_CASE" == "cp_relaxes_mode" ]]; then chmod 755 "$3"; fi\n'
        '   touch "$RC6_TMPDIR/copied"\n'
        '   touch "$3/Dockerfile"; exit 0;;\n'
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
           "RC6_TMPDIR": str(tmp_path), "GITHUB_RUN_ID": "1999",
           "GITHUB_RUN_ATTEMPT": attempt, "GITHUB_ENV": str(github_env),
           "RUNNER_TEMP": str(runner), "RC6_EXPECTED_STAGE": str(stage),
           "RC6_ALLOWED_ROOT": str(tmp_path.parent),
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           "IMAGE": "porota-predeploy-v2:0123456789012345678901234567890123456789",
           "RC6_FAKE_CASE": case, "FOREIGN_MARKER": str(foreign), "DOCKER_TRACE": str(trace)}
    result = subprocess.run(["bash", "-c", script], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result.returncode, result.stdout + result.stderr, calls, foreign.exists()


def test_extract_private_stage_is_per_run_attempt_and_preserves_foreign_siblings(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "runner"
    runner.mkdir(mode=0o700)
    foreign = runner / "porota-predeploy-app-1999-3"
    foreign.mkdir(mode=0o700)
    sentinel = foreign / "not-ours"
    sentinel.write_text("FOREIGN", encoding="utf-8")

    for attempt in ("1", "2"):
        fixture = tmp_path / f"attempt-{attempt}"
        fixture.mkdir()
        code, stdout, calls, alive = _extract_app_shell(
            fixture, "healthy", runner_temp=runner, attempt=attempt,
        )
        assert code == 0, (attempt, stdout)
        assert alive
        assert any(call.startswith("cp ") for call in calls)
        stage = runner / f"porota-predeploy-app-1999-{attempt}"
        assert (stage / "app/Dockerfile").is_file()
        assert not stage.is_symlink()
        assert stat.S_IMODE(stage.stat().st_mode) == 0o700
        identity = f"{stage.stat().st_dev}:{stage.stat().st_ino}:{os.getuid()}:700"
        assert (fixture / "github_env").read_text(encoding="utf-8").splitlines() == [
            f"RC6_EXTRACT_STAGE={stage}", f"RC6_EXTRACT_STAGE_ID={identity}",
        ]
        assert sentinel.read_text(encoding="utf-8") == "FOREIGN"

    assert (runner / "porota-predeploy-app-1999-1/app/Dockerfile").exists()
    assert (runner / "porota-predeploy-app-1999-2/app/Dockerfile").exists()


def test_container_copy_cannot_relax_private_stage_root_mode(tmp_path: Path) -> None:
    code, stdout, calls, _ = _extract_app_shell(tmp_path, "cp_relaxes_mode")
    assert code == 0, stdout
    assert any(call.startswith("cp ") for call in calls)
    stage = tmp_path / "runner/porota-predeploy-app-1999-1"
    assert stat.S_IMODE(stage.stat().st_mode) == 0o700
    assert (stage / "app/Dockerfile").is_file()


@pytest.mark.parametrize("preexisting", ("directory", "symlink"))
def test_extract_refuses_preexisting_stage_without_overwrite_or_docker(
    tmp_path: Path, preexisting: str,
) -> None:
    runner = tmp_path / "runner"
    runner.mkdir()
    stage = runner / "porota-predeploy-app-1999-1"
    if preexisting == "symlink":
        foreign_target = runner / "unrelated"
        foreign_target.mkdir(mode=0o700)
        stage.symlink_to(foreign_target, target_is_directory=True)
    else:
        stage.mkdir(mode=0o700)
    sentinel = stage / "foreign-record"
    sentinel.write_text("FOREIGN", encoding="utf-8")
    code, stdout, calls, alive = _extract_app_shell(
        tmp_path, "healthy", runner_temp=runner,
    )
    assert code == 79, stdout
    assert not calls
    assert alive
    assert sentinel.read_text(encoding="utf-8") == "FOREIGN"
    assert not (stage / "Dockerfile").exists()


def _artifact_consumer_shell(tmp_path: Path, case: str) -> tuple[int, str, list[str], bool]:
    """Run the real consumer step until a fake validator, never shared /tmp."""
    extracted, output, _, _ = _extract_app_shell(tmp_path, "healthy")
    assert extracted == 0, output
    runner = tmp_path / "runner"
    stage = runner / "porota-predeploy-app-1999-1"
    receipt = dict(line.split("=", 1) for line in
                   (tmp_path / "github_env").read_text(encoding="utf-8").splitlines())
    foreign = tmp_path / "unrelated"
    foreign.mkdir()
    sentinel = foreign / "keep"
    sentinel.write_text("FOREIGN", encoding="utf-8")

    if case == "missing_receipt":
        receipt.pop("RC6_EXTRACT_STAGE_ID")
    elif case == "tampered_receipt":
        receipt["RC6_EXTRACT_STAGE_ID"] = "0:0:0:700"
    elif case == "foreign_path":
        receipt["RC6_EXTRACT_STAGE"] = str(foreign)
    elif case == "symlink_stage":
        stage.rename(tmp_path / "original-stage")
        stage.symlink_to(foreign, target_is_directory=True)
    elif case == "symlink_runner":
        runner.rename(tmp_path / "original-runner")
        runner.symlink_to(tmp_path / "original-runner", target_is_directory=True)
    elif case == "replaced_inode":
        stage.rename(tmp_path / "original-stage")
        stage.mkdir(mode=0o700)
        (stage / "app").mkdir(mode=0o700)
    elif case == "relaxed_mode":
        stage.chmod(0o755)
    elif case == "missing_app":
        (stage / "app").rename(stage / "saved-app")
    elif case == "symlink_app":
        (stage / "app").rename(stage / "saved-app")
        (stage / "app").symlink_to(foreign, target_is_directory=True)
    else:
        assert case == "valid", case

    validator_trace = tmp_path / "validator.calls"
    fake_validator = tmp_path / "python"
    fake_validator.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" > "$RC6_VALIDATOR_TRACE"\n'
        "exit 86\n", encoding="utf-8",
    )
    fake_validator.chmod(0o700)
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Artifact completeness and import closure")
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "RUNNER_TEMP": str(runner), "GITHUB_RUN_ID": "1999",
           "GITHUB_RUN_ATTEMPT": "1", "RC6_VALIDATOR_TRACE": str(validator_trace),
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           **receipt}
    result = subprocess.run(["bash", "-c", script], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    calls = validator_trace.read_text(encoding="utf-8").splitlines() if validator_trace.exists() else []
    return result.returncode, result.stdout + result.stderr, calls, sentinel.exists()


def test_artifact_gate_consumes_attested_private_stage_before_fake_validator(tmp_path: Path) -> None:
    code, stdout, args, foreign_alive = _artifact_consumer_shell(tmp_path, "valid")
    assert code == 86, stdout  # Fake validator reached; remaining artifact step intentionally not run.
    assert foreign_alive
    assert args[0] == "scripts/porota_validate_deploy_artifact.py"
    assert args[args.index("--artifact-root") + 1] == str(
        tmp_path / "runner/porota-predeploy-app-1999-1/app"
    )


@pytest.mark.parametrize("case,reason", (
    ("missing_receipt", "consumer_path_unverified"),
    ("tampered_receipt", "consumer_identity_changed"),
    ("foreign_path", "consumer_path_unverified"),
    ("symlink_stage", "consumer_path_unverified"),
    ("symlink_runner", "consumer_path_unverified"),
    ("replaced_inode", "consumer_identity_changed"),
    ("relaxed_mode", "consumer_identity_changed"),
    ("missing_app", "consumer_app_invalid"),
    ("symlink_app", "consumer_app_invalid"),
))
def test_artifact_consumer_fails_79_before_validator_for_untrusted_stage(
    tmp_path: Path, case: str, reason: str,
) -> None:
    code, stdout, args, foreign_alive = _artifact_consumer_shell(tmp_path, case)
    assert code == 79, (case, stdout)
    assert f"POROTA_PRIVATE_EXTRACT_STAGE=RED|{reason}" in stdout
    assert not args, (case, args)
    assert foreign_alive


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

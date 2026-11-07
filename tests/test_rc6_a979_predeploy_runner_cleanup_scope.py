"""A979: Predeploy V2 cleanup may only affect its own ephemeral resources."""

import os
import subprocess
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-predeploy-v2.yml"


def _predeploy_cleanup() -> str:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["artifact-gate"]["steps"]
    matches = [step for step in steps if step.get("name") == "Local cleanup"]
    assert len(matches) == 1
    assert matches[0]["if"] == "always()"
    assert matches[0]["shell"] == "bash"
    return matches[0]["run"]


def test_predeploy_cleanup_never_enumerates_containers_by_shared_image() -> None:
    """An image ancestry match is not proof of Porota container ownership."""
    cleanup = _predeploy_cleanup()
    assert "docker ps" not in cleanup
    assert "docker container rm" not in cleanup
    assert "docker rm" not in cleanup
    assert "xargs" not in cleanup


def test_predeploy_cleanup_retains_only_no_force_candidate_tag_cleanup() -> None:
    cleanup = _predeploy_cleanup()
    assert 'docker image rm "$IMAGE"' in cleanup
    assert "docker image rm -f" not in cleanup
    assert "docker system prune" not in cleanup
    assert "docker image prune" not in cleanup
    assert "docker volume prune" not in cleanup


def _exercise_runner_docker_cleanup(tmp_path: Path, case: str) -> tuple[int, str, list[str], bool]:
    """Run the isolated Docker branch only, never the broader /tmp cleanup."""
    cleanup = _predeploy_cleanup()
    assert "# RC6_A979_OWNED_IMAGE_BEGIN" in cleanup
    assert "# RC6_A979_OWNED_IMAGE_END" in cleanup
    commands = cleanup.split("# RC6_A979_OWNED_IMAGE_BEGIN", 1)[1].split(
        "# RC6_A979_OWNED_IMAGE_END", 1)[0]
    foreign = tmp_path / "foreign-container-alive"
    foreign.write_text("FOREIGN_RUNNING", encoding="utf-8")
    trace = tmp_path / "docker.calls"
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'if [[ "$1 $2" == "image inspect" ]]; then\n'
        '  if [[ "$RC6_FAKE_CASE" == "inspect_failure" ]]; then exit 7; fi\n'
        '  case "$RC6_FAKE_CASE" in\n'
        '    foreign_label) echo "sha256:$(printf x%.0s {1..64})|foreign|v2";;\n'
        '    *) echo "sha256:$(printf a%.0s {1..64})|0123456789012345678901234567890123456789|v2";;\n'
        '  esac\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1 $2" == "container ls" ]]; then\n'
        '  if [[ "$RC6_FAKE_CASE" == "shared_image" ]]; then echo foreign-id; fi\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1 $2" == "image rm" ]]; then\n'
        '  if [[ "$RC6_FAKE_CASE" == "rm_failure" ]]; then exit 8; fi\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1" == "rm" ]]; then rm -f "$FOREIGN_MARKER"; exit 0; fi\n'
        "exit 97\n", encoding="utf-8")
    binary.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "IMAGE": "porota-predeploy-v2:0123456789012345678901234567890123456789",
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           "RC6_FAKE_CASE": case, "FOREIGN_MARKER": str(foreign),
           "DOCKER_TRACE": str(trace)}
    result = subprocess.run(["bash", "-c", commands], env=env,
                            cwd=tmp_path, timeout=3, check=False,
                            capture_output=True, text=True)
    assert foreign.read_text(encoding="utf-8") == "FOREIGN_RUNNING"
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result.returncode, result.stdout, calls, foreign.exists()


def test_predeploy_cleanup_never_untags_foreign_or_shared_image(tmp_path: Path) -> None:
    for case in ("foreign_label", "shared_image", "inspect_failure"):
        case_dir = tmp_path / case
        case_dir.mkdir()
        code, stdout, calls, foreign_alive = _exercise_runner_docker_cleanup(case_dir, case)
        assert code != 0
        assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=WARN" in stdout
        assert not any("image rm" in x or x.startswith("rm ") for x in calls)
        assert foreign_alive


def test_predeploy_cleanup_reports_failed_image_removal_truthfully(tmp_path: Path) -> None:
    code, stdout, calls, _ = _exercise_runner_docker_cleanup(tmp_path, "rm_failure")
    assert code != 0
    assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=WARN" in stdout
    assert sum("image rm" in x for x in calls) == 1


def test_predeploy_cleanup_exact_private_image_only(tmp_path: Path) -> None:
    code, stdout, calls, foreign_alive = _exercise_runner_docker_cleanup(tmp_path, "healthy")
    assert code == 0
    assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=GREEN" in stdout
    assert sum("image rm" in x for x in calls) == 1
    assert foreign_alive


def test_predeploy_containers_have_explicit_creation_cleanup() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    source = "\n".join(step.get("run", "") for step in data["jobs"]["artifact-gate"]["steps"])
    assert 'cid="$(docker create "$IMAGE")"' in source
    assert 'trap \'docker rm -f "$cid" >/dev/null 2>&1 || true\' EXIT' in source
    assert 'docker run --rm --pull never' in source
    assert 'docker rm -f "porota-native-cases-$GITHUB_RUN_ID"' in source
    assert 'trap cleanup_native_proof EXIT' in source

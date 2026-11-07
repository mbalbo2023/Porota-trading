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
    assert "docker container ls" not in cleanup
    assert "xargs" not in cleanup


def test_predeploy_cleanup_retains_only_no_force_candidate_tag_cleanup() -> None:
    cleanup = _predeploy_cleanup()
    assert 'docker image rm "$IMAGE" >/dev/null 2>&1 || true' in cleanup
    assert "docker image rm -f" not in cleanup
    assert "docker system prune" not in cleanup
    assert "docker image prune" not in cleanup
    assert "docker volume prune" not in cleanup


def test_predeploy_cleanup_never_stops_foreign_container_sharing_image(tmp_path: Path) -> None:
    """A simulated image-reference failure must not trigger container deletion."""
    # Execute ONLY the Docker portion, never the workflow's broad /tmp deletion.
    cleanup = _predeploy_cleanup()
    commands = [line.strip() for line in cleanup.splitlines()
                if line.strip().startswith("docker ")]
    assert commands == ['docker image rm "$IMAGE" >/dev/null 2>&1 || true']
    foreign = tmp_path / "foreign-container-alive"
    foreign.write_text("FOREIGN_RUNNING", encoding="utf-8")
    trace = tmp_path / "docker.calls"
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'if [[ "$1 $2" == "image rm" ]]; then exit 1; fi\n'
        'if [[ "$1" == "rm" ]]; then rm -f "$FOREIGN_MARKER"; exit 0; fi\n'
        "exit 97\n", encoding="utf-8")
    binary.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "IMAGE": "porota-predeploy-v2:frozen-sha",
           "FOREIGN_MARKER": str(foreign), "DOCKER_TRACE": str(trace)}
    subprocess.run(["bash", "-c", "\n".join(commands)], env=env,
                   cwd=tmp_path, timeout=3, check=True)
    assert foreign.read_text(encoding="utf-8") == "FOREIGN_RUNNING"
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "image rm porota-predeploy-v2:frozen-sha"
    ]


def test_predeploy_containers_have_explicit_creation_cleanup() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    source = "\n".join(step.get("run", "") for step in data["jobs"]["artifact-gate"]["steps"])
    assert 'cid="$(docker create "$IMAGE")"' in source
    assert 'trap \'docker rm -f "$cid" >/dev/null 2>&1 || true\' EXIT' in source
    assert 'docker run --rm --pull never' in source
    assert 'docker rm -f "porota-native-cases-$GITHUB_RUN_ID"' in source
    assert 'trap cleanup_native_proof EXIT' in source

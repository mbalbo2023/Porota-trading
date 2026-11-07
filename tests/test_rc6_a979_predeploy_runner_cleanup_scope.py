"""A979: Predeploy V2 cleanup may only affect its own ephemeral resources."""

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


def test_predeploy_containers_have_explicit_creation_cleanup() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    source = "\n".join(step.get("run", "") for step in data["jobs"]["artifact-gate"]["steps"])
    assert 'cid="$(docker create "$IMAGE")"' in source
    assert 'trap \'docker rm -f "$cid" >/dev/null 2>&1 || true\' EXIT' in source
    assert 'docker run --rm --pull never' in source
    assert 'docker rm -f "porota-native-cases-$GITHUB_RUN_ID"' in source
    assert 'trap cleanup_native_proof EXIT' in source

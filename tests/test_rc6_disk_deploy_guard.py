from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
CANONICAL = WORKFLOWS / "porota-deploy-v2-promote.yml"


def test_canonical_deploy_checks_disk_before_first_remote_transfer() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    guard = text.index("- name: Pre-transfer Droplet disk guard")
    transfer = text.index("- name: Transfer frozen artifact")
    promote = text.index("- name: Promote exact frozen candidate and verify")
    assert guard < transfer < promote
    assert 'echo "RC6_DISK_PREFLIGHT_GREEN=1"' in text
    transfer_block = text[transfer:promote]
    assert 'test "${RC6_DISK_PREFLIGHT_GREEN:-0}" = "1"' in transfer_block
    assert "RC6_DISK_PRETRANSFER_GATE=GREEN" in text


def test_full_docker_load_deploy_route_is_unique_and_guarded() -> None:
    loaders = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "docker load" in text:
            loaders.append(path.name)
            assert "RC6_DISK_PRETRANSFER_GATE=GREEN" in text
            assert "RC6_DISK_PREFLIGHT_GREEN" in text
    assert loaders == ["porota-deploy-v2-promote.yml"]


def test_canonical_deploy_uses_policy_for_final_disk_floor() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "rc6-disk-housekeeping-v1.json" in text
    assert "post_cleanup_min_free_bytes" in text
    assert 'test "$DISK_AFTER" -ge 1073741824' not in text


def test_deploy_transfer_step_cannot_precede_guard_by_reordering() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert text.count("- name: Pre-transfer Droplet disk guard") == 1
    assert text.count("- name: Transfer frozen artifact") == 1
    assert text.index("Pre-transfer Droplet disk guard") < text.index(
        "Transfer frozen artifact"
    )


def test_disk_metric_probe_avoids_runner_shell_positional_expansion() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    start = text.index("- name: Pre-transfer Droplet disk guard")
    end = text.index("- name: Transfer frozen artifact")
    block = text[start:end]
    assert "python3 -c 'import os,shutil;" in block
    assert "shutil.disk_usage" in block
    assert "os.statvfs" in block
    assert "awk 'NR==2 {print $4}'" not in block
    assert "\\\\$4" not in block

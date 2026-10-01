from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
CANONICAL = WORKFLOWS / "porota-deploy-v2-promote.yml"


def test_canonical_deploy_checks_disk_before_frozen_artifact_transfer() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    guard = text.index("- name: Dynamic pre-transfer disk guard and conditional safe cleanup")
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
    assert text.count("- name: Dynamic pre-transfer disk guard and conditional safe cleanup") == 1
    assert text.count("- name: Transfer frozen artifact") == 1
    assert text.index("Dynamic pre-transfer disk guard and conditional safe cleanup") < text.index(
        "Transfer frozen artifact"
    )


def test_disk_metric_probe_avoids_runner_shell_positional_expansion() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    start = text.index("- name: Dynamic pre-transfer disk guard and conditional safe cleanup")
    end = text.index("- name: Transfer frozen artifact")
    block = text[start:end]
    assert "python3 -c 'import os,shutil;" in block
    assert "shutil.disk_usage" in block
    assert "os.statvfs" in block
    assert "awk 'NR==2 {print $4}'" not in block
    assert "\\\\$4" not in block


def test_dynamic_gate_uses_exact_unpacked_image_and_cleans_before_transfer_when_needed() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    guard = text.index("Dynamic pre-transfer disk guard and conditional safe cleanup")
    transfer = text.index("Transfer frozen artifact")
    block = text[guard:transfer]
    assert "--image-unpacked-bytes" in block
    assert "image_size_bytes" in block
    assert "RC6_DISK_PRECLEAN=START" in block
    assert "rc6_disk_housekeeping.py" in block
    assert "docker image prune -f" in block
    assert block.index("RC6_DISK_PRECLEAN=START") < block.index("RC6_DISK_PRETRANSFER_GATE=GREEN")


def test_final_cleanup_runs_after_runtime_validation_and_is_measured() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    validated = text.index('"validation_status":"VALIDATED_RUNTIME"')
    cleanup = text.index("RC6_FINAL_HOUSEKEEPING=GREEN")
    assert validated < cleanup
    tail = text[validated:cleanup + 200]
    assert "--mode cleanup" in tail
    assert "last-deploy-housekeeping.json" in tail
    assert "docker image prune -f" in tail
    assert "FINAL_INODE_FREE_PERCENT" in tail

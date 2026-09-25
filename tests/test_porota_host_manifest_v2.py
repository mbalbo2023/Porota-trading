from pathlib import Path

from scripts.porota_host_manifest_v2 import build_manifest


def test_host_manifest_records_hash_target_and_unmanaged_units(tmp_path):
    repo = tmp_path / "repo"
    (repo / "systemd").mkdir(parents=True)
    (repo / ".github/workflows").mkdir(parents=True)
    managed = repo / "systemd/porota-demo-rc6.timer"
    unmanaged = repo / "systemd/porota-old-rc6.service"
    managed.write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    unmanaged.write_text("[Service]\nType=oneshot\n", encoding="utf-8")
    workflow = repo / ".github/workflows/deploy.yml"
    workflow.write_text(
        "sudo install -m 644 $REPO/systemd/porota-demo-rc6.timer /etc/systemd/system/porota-demo-rc6.timer\n"
        "sudo systemctl enable --now porota-demo-rc6.timer\n",
        encoding="utf-8",
    )
    modes = {
        "systemd/porota-demo-rc6.timer": "100644",
        "systemd/porota-old-rc6.service": "100644",
        ".github/workflows/deploy.yml": "100644",
    }
    result = build_manifest(repo, workflow, "abc123", modes=modes)
    assert result["status"] == "GREEN"
    assert result["counts"] == {
        "tracked_rc6_units": 2,
        "canonical_deploy_units": 1,
        "tracked_unmanaged_review": 1,
        "referenced_untracked": 0,
    }
    by = {x["unit_name"]: x for x in result["units"]}
    row = by["porota-demo-rc6.timer"]
    assert row["install_target"] == "/etc/systemd/system/porota-demo-rc6.timer"
    assert row["expected_enabled"] is True
    assert row["expected_active"] is True
    assert len(row["source_sha256"]) == 64
    assert by["porota-old-rc6.service"]["classification"] == "TRACKED_UNMANAGED_REVIEW"


def test_host_manifest_fails_on_untracked_workflow_input(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".github/workflows").mkdir(parents=True)
    workflow = repo / ".github/workflows/deploy.yml"
    workflow.write_text(
        "systemd/porota-missing-rc6.service\n",
        encoding="utf-8",
    )
    modes = {".github/workflows/deploy.yml": "100644"}
    result = build_manifest(repo, workflow, "abc123", modes=modes)
    assert result["status"] == "FAILED_UNTRACKED_WORKFLOW_INPUT"
    assert result["referenced_untracked"] == ["porota-missing-rc6.service"]

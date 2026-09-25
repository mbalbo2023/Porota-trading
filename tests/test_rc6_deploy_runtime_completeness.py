from pathlib import Path

from scripts.porota_validate_deploy_artifact import is_runtime_relevant


PROMOTE = Path(".github/workflows/porota-deploy-v2-promote.yml").read_text(encoding="utf-8")
PREDEPLOY = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
DOCKERFILE = Path("Dockerfile").read_text(encoding="utf-8")


def test_runtime_dependency_is_rule_packaged_and_exact_image_verified():
    assert is_runtime_relevant("rc6_source_consolidation.py")
    assert "porota-deploy-bundle-v2-manifest.json" in PROMOTE
    assert 'assert src.is_file() and sha(src)==row["sha256"]' in PROMOTE
    assert 'test "$OBSERVER_IMAGE_ID" = "$EXPECTED_IMAGE_ID"' in PROMOTE
    assert 'test "$DASHBOARD_IMAGE_ID" = "$EXPECTED_IMAGE_ID"' in PROMOTE


def test_postclose_reconciliation_is_only_required_when_it_can_run():
    assert "STATUS=SKIPPED_MARKET_NOT_CLOSED" in PROMOTE
    assert "RC6_FULL_CONTRACT_RECONCILIATION=SKIPPED_MARKET_OPEN" in PROMOTE
    assert 'grep -Fq "STATUS=OK" <<< "$CONTRACT_OUTPUT"' in PROMOTE
    assert 'grep -Fq "QUICK_CHECK=ok" <<< "$CONTRACT_OUTPUT"' in PROMOTE


def test_dashboard_bootstrap_and_storage_cleanup_are_deploy_guards():
    assert "for job in validation_projection action4_audit; do" in PROMOTE
    assert 'timeout 60 python az_maintenance_job.py "$job"' in PROMOTE
    assert "DISK_BEFORE=" in PROMOTE
    assert "DISK_AFTER=" in PROMOTE
    assert "docker image prune -f" in PROMOTE
    assert "docker builder prune -af" in PROMOTE
    assert "CURRENT_STATE_V2=GREEN" in PROMOTE


def test_dashboard_and_observer_are_recreated_from_the_same_frozen_image():
    assert 'gunzip -c "$REMOTE_DIR/porota-predeploy-image.tar.gz" | sudo -n docker load' in PROMOTE
    assert 'LOADED_IMAGE_ID="$(sudo -n docker image inspect' in PROMOTE
    assert 'sudo -n docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"' in PROMOTE
    assert 'OBSERVER_IMAGE_ID="$(sudo -n docker inspect' in PROMOTE
    assert 'DASHBOARD_IMAGE_ID="$(sudo -n docker inspect' in PROMOTE
    assert 'test "$OBSERVER_IMAGE_ID" = "$EXPECTED_IMAGE_ID"' in PROMOTE
    assert 'test "$DASHBOARD_IMAGE_ID" = "$EXPECTED_IMAGE_ID"' in PROMOTE


def test_all_dashboard_modules_are_runtime_image_inputs():
    modules = (
        "bg_paper_dashboard.py", "zz_wave8_dashboard_live_rc6.py", "da_dashboard_ux_hf6.py",
        "rc6_annual_instrument_analysis.py", "rc6_on_validation.py", "bd_ppi_readonly_guard.py",
        "c_ppi_client.py", "cr_pending_settlement_diagnostics_hf6.py", "eq_dashboard_table_layout_rc6.py",
        "rc6_dashboard_responsive_ux.py", "rc6_family_readiness.py", "o_dashboard.py",
    )
    for module in modules:
        assert f"COPY {module} /app/{module}" in DOCKERFILE


def test_contract_reconciliation_reuses_the_frozen_promoted_image():
    assert 'sudo -n docker image inspect -f' in PROMOTE
    assert '--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py' in PROMOTE
    assert "porota-predeploy-image.tar.gz" in PROMOTE


def test_contract_runner_uses_host_timeout_and_python_entrypoint():
    assert 'sudo -n timeout 1200 sudo -n docker run' in PROMOTE
    assert '--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py' in PROMOTE
    assert '--entrypoint timeout' not in PROMOTE

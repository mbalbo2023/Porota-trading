import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREDEPLOY = ROOT / ".github/workflows/porota-predeploy-v2.yml"
WORKFLOW = ROOT / ".github/workflows/porota-deploy-v2-promote.yml"
POLICY = ROOT / "ops/policy/porota-policy.yaml"
DOCKERFILE = Path("Dockerfile")


def _predeploy() -> str:
    return PREDEPLOY.read_text(encoding="utf-8")


def _deploy() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_runtime_dependency_is_packaged_installed_rolled_back_and_verified():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    source = _deploy()
    module = "rc6_source_consolidation.py"
    assert select_bundle_paths([module]) == [module]
    assert 'assert data.get("status")=="GREEN" and data.get("files")' in source
    assert 'assert src.is_file() and sha(src)==row["sha256"]' in source
    assert 'shutil.copy2(src,dst)' in source
    assert 'assert sha(dst)==row["sha256"]' in source
    assert "rollback" not in source.lower()


def test_postclose_reconciliation_is_only_required_when_it_can_run():
    source = _deploy()
    assert 'STATUS=SKIPPED_MARKET_NOT_CLOSED' in source
    assert 'RC6_FULL_CONTRACT_RECONCILIATION=SKIPPED_MARKET_OPEN' in source
    assert 'grep -Fq "STATUS=OK" <<< "$CONTRACT_OUTPUT"' in source
    assert 'grep -Fq "QUICK_CHECK=ok" <<< "$CONTRACT_OUTPUT"' in source


def test_dashboard_bootstrap_dependencies_and_storage_cleanup_are_deploy_guards():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    source = _deploy()
    assert select_bundle_paths(["_version.py", "cg_paper_workspace.py"]) == [
        "_version.py", "cg_paper_workspace.py"
    ]
    assert "SAFE_PREFLIGHT_RECLAIM=" in source
    assert "CURRENT_STATE_V2=GREEN" in source
    assert "SPACE_RECOVERED=" in source
    assert "POROTA_DEPLOY_V2_RUNNER_CLEANUP=GREEN" in source


def test_dashboard_is_recreated_directly_from_verified_candidate():
    source = _deploy()
    loaded = source.index('gunzip -c "$REMOTE_DIR/porota-predeploy-image.tar.gz" | sudo -n docker load')
    tagged = source.index('sudo -n docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"')
    recreated = source.index('porota_mode_manager.py" simulation')
    assert loaded < tagged < recreated
    assert not re.search(r"(?m)^\s*(?:sudo -n )?docker build(?:\s|$)", source)
    assert 'DASHBOARD_IMAGE_ID="$(sudo -n docker inspect' in source
    assert 'test "$DASHBOARD_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in source


def test_all_verified_dashboard_modules_are_explicit_image_inputs():
    source = DOCKERFILE.read_text(encoding="utf-8")
    modules = (
        "bg_paper_dashboard.py", "zz_wave8_dashboard_live_rc6.py", "da_dashboard_ux_hf6.py",
        "rc6_annual_instrument_analysis.py", "rc6_on_validation.py", "bd_ppi_readonly_guard.py",
        "c_ppi_client.py", "cr_pending_settlement_diagnostics_hf6.py", "eq_dashboard_table_layout_rc6.py",
        "rc6_dashboard_responsive_ux.py", "rc6_family_readiness.py", "o_dashboard.py",
    )
    for module in modules:
        assert f"COPY {module} /app/{module}" in source


def test_contract_reconciliation_reuses_the_immutable_candidate_image():
    source = _deploy()
    promote = source[source.index("- name: Promote exact frozen candidate and verify"):]
    assert 'gunzip -c "$REMOTE_DIR/porota-predeploy-image.tar.gz" | sudo -n docker load' in promote
    assert '--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py' in promote
    assert not re.search(r"(?m)^\s*(?:sudo -n )?docker build(?:\s|$)", promote)


def test_late_verification_uses_retained_stable_image_tag():
    source = _deploy()
    assert 'STABLE_IMAGE="porota-trading-bot:17.0.0-rc6"' in source
    assert 'sudo -n docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"' in source
    assert '--pull never' in source
    assert 'docker image rm "$STABLE_IMAGE"' not in source


def test_contract_runner_uses_host_timeout_and_python_entrypoint():
    source = _deploy()
    assert 'sudo -n timeout --signal=TERM --kill-after=60 3300 sudo -n docker run' in source
    assert 'timeout-minutes: 90' in source
    assert '--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py' in source
    assert '--entrypoint timeout' not in source


def test_sector_map_and_multisource_helpers_are_immutable_deploy_inputs():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    artifacts = ["POROTA_SECTOR_MAP_V1.csv", "rc6_multisource_discovery.py", "rc6_iol_family_reference.py"]
    assert select_bundle_paths(artifacts) == sorted(artifacts)
    assert "POROTA_SECTOR_MAP_V1.csv" in _predeploy()
    source = _deploy()
    assert 'for row in data["files"]' in source
    assert 'assert src.is_file() and sha(src)==row["sha256"]' in source

def test_deploy_scope_rejects_watchlist_as_universe_authority():
    observer = (ROOT / "bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert "DISCOVERY_SEEDS = {}" in observer
    assert "WATCHLIST_PATH.read_text" not in observer
    assert "from rc6_multisource_discovery import" in observer

def test_live_safety_checks_do_not_use_heavy_quick_check():
    source = _deploy()
    fast = source[source.index('POST_STATE=""'):source.index('CONTRACT_OUTPUT=')]
    assert "PRAGMA quick_check" not in fast
    assert "PRAGMA query_only=ON" in fast
    assert "POST_STATE_HOST_READONLY_ATTEMPT" in fast
    assert 'test "$POST_STATE" = "STATE_FAST|PRODUCTION_PAPER|0"' in fast
    assert "POST_SQLITE_QUICK_CHECK=DEFERRED_TO_CONTRACT_RUNNER" in source


def test_sector_map_runtime_verifier_keeps_stdin_attached():
    source = _deploy()
    assert '--entrypoint python -i "$STABLE_IMAGE" - <<\'PY\'' in source
    assert "OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES" in source


def test_history_cutoff_repair_is_decoupled_from_code_deploy():
    source = _deploy()
    policy = POLICY.read_text(encoding="utf-8")
    assert "rc6_history_cutoff_repair_once.py" not in source
    assert "RC6_HISTORY_ALLOW_PPI_GAP_REPAIR" not in source
    assert "strategy: FIX_FORWARD_ONLY" in policy
    assert "rollback_allowed: false" in policy
    assert not (ROOT / ".github/workflows/deploy.yml").exists()
    assert not (ROOT / ".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml").exists()

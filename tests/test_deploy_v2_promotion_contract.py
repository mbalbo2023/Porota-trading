from pathlib import Path


PROMOTE=Path(".github/workflows/porota-deploy-v2-promote.yml").read_text(encoding="utf-8")
LEGACY_PATH=Path(".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml")


def test_deploy_v2_promotes_frozen_artifact_without_droplet_build():
    assert "porota-predeploy-image.tar.gz" in PROMOTE
    assert "docker load" in PROMOTE
    import re
    assert not re.search(r"\bdocker\s+build(?:\s|$)", PROMOTE)
    assert "POROTA_BUILD_ONCE_PROMOTION=GREEN" in PROMOTE


def test_deploy_v2_is_the_only_rc6_production_push_path():
    assert "push:" in PROMOTE
    assert "deploy/rc6-pr69-isolated-20260915" in PROMOTE
    assert not LEGACY_PATH.exists()


def test_deploy_v2_requires_merge_tree_identity_and_successful_predeploy():
    assert 'CANDIDATE_TREE="$(git rev-parse "$CANDIDATE_SHA^{tree}")"' in PROMOTE
    assert 'test "$CANDIDATE_TREE" = "$DEPLOY_TREE"' in PROMOTE
    assert 'run.get("name")!="Porota Predeploy V2"' in PROMOTE
    assert 'run.get("conclusion")!="success"' in PROMOTE


def test_deploy_v2_preserves_paper_and_ppi_watch_invariants():
    assert 'test "$POST_STATE" = "ok|PRODUCTION_PAPER|0"' in PROMOTE
    assert "PPI_WATCH_UNTOUCHED=GREEN" in PROMOTE
    assert "REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED" in PROMOTE
    assert "rollback" not in PROMOTE.lower()

from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths


def test_bundle_selection_is_derived_not_manual():
    tracked = [
        "Dockerfile",
        "requirements.txt",
        "requirements.lock.txt",
        "n_instrument_watchlist.json",
        "POROTA_SECTOR_MAP_V1.csv",
        "bf_production_paper_observer.py",
        "scripts/runtime.sh",
        "systemd/porota-preopen-rc6.timer",
        "ops/policy/porota-policy.yaml",
        "ops/state/CURRENT_STATE.schema.json",
        "tests/test_should_not_ship.py",
        "docs/README.md",
        ".github/workflows/deploy.yml",
    ]
    selected = select_bundle_paths(tracked)
    assert "Dockerfile" in selected
    assert "requirements.lock.txt" in selected
    assert "n_instrument_watchlist.json" in selected
    assert "POROTA_SECTOR_MAP_V1.csv" in selected
    assert "bf_production_paper_observer.py" in selected
    assert "scripts/runtime.sh" in selected
    assert "systemd/porota-preopen-rc6.timer" in selected
    assert "ops/policy/porota-policy.yaml" in selected
    assert "ops/state/CURRENT_STATE.schema.json" in selected
    assert "tests/test_should_not_ship.py" not in selected
    assert "docs/README.md" not in selected
    assert ".github/workflows/deploy.yml" not in selected

from pathlib import Path

import yaml

from ci_frozen_candidate_contract import (
    FrozenContractError,
    run_negative_fixtures,
    validate_frozen_candidate,
)


PREDEPLOY = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
POLICY = yaml.safe_load(Path("ops/policy/test-policy.yaml").read_text(encoding="utf-8"))


def test_productive_scope_is_repository_root_automatic_discovery():
    governance = POLICY["test_governance"]
    scope = POLICY["productive_scope"]
    assert governance["automatic_discovery_required"] is True
    assert governance["manual_test_file_allowlist_as_primary_ci"] is False
    assert scope["roots"] == ["."]
    assert scope["python_files"] == "test_*.py"
    assert "tests/test_porota_*.py" not in PREDEPLOY
    assert 'python -m pytest --collect-only -q "${TEST_ARGS[@]}"' in PREDEPLOY
    assert 'python -m pytest -q "${TEST_ARGS[@]}"' in PREDEPLOY


def test_every_exclusion_is_versioned_justified_and_has_a_successor():
    exclusions = POLICY["productive_scope"]["exclusions"]
    assert exclusions
    for item in exclusions:
        assert Path(item["path"]).is_file()
        assert Path(item["successor"]).is_file()
        assert item["classification"]
        assert item["reason"].strip()
        assert item["owner"]


def test_predeploy_reports_truthful_collection_and_execution_counts():
    for token in (
        "POROTA_TEST_DISCOVERED",
        "POROTA_TEST_EXECUTED",
        "POROTA_TEST_SKIPPED",
        "POROTA_TEST_XFAIL",
        "POROTA_TEST_EXCLUSIONS",
        "POROTA_GOVERNED_TEST_STATUS",
    ):
        assert token in PREDEPLOY
    assert "POROTA_FULL_SUITE=NO_VERIFICADO" not in PREDEPLOY
    assert "Full automatic test discovery: GREEN" not in PREDEPLOY


def test_all_six_negative_fixtures_are_executed_fail_closed():
    evidence = run_negative_fixtures(Path("/tmp/porota-negative-fixtures-unit.json"))
    for fixture in (
        "MISSING_FILE",
        "CORRUPT_MANIFEST",
        "WRONG_SHA",
        "WRONG_DIGEST",
        "NON_PAPER_MODE",
        "REAL_ROUTE_ENABLED",
    ):
        assert fixture in evidence
    assert "ci_frozen_candidate_contract.py negative" in PREDEPLOY
    assert "ci_frozen_candidate_contract.py validate" in PREDEPLOY


def test_candidate_tree_mismatch_fails_closed(tmp_path):
    image = tmp_path / "image.tar.gz"
    manifest = tmp_path / "manifest.json"
    frozen = tmp_path / "frozen.json"
    image.write_bytes(b"image")
    manifest.write_text('{"schema_version":1}', encoding="utf-8")
    from hashlib import sha256
    import json

    frozen.write_text(
        json.dumps(
            {
                "candidate_sha": "a" * 40,
                "candidate_tree_sha": "b" * 40,
                "image_tar_sha256": sha256(image.read_bytes()).hexdigest(),
                "paper_mode_required": "PRODUCTION_PAPER",
                "real_orders_sent_required": 0,
                "real_order_capability_required": "BLOCKED",
                "build_once": True,
            }
        ),
        encoding="utf-8",
    )
    import pytest

    with pytest.raises(FrozenContractError, match="CANDIDATE_TREE_MISMATCH"):
        validate_frozen_candidate(
            frozen_path=frozen,
            image_path=image,
            manifest_path=manifest,
            candidate_sha="a" * 40,
            tree_sha="c" * 40,
        )


def test_predeploy_stays_off_droplet_and_builds_once():
    assert PREDEPLOY.count("docker build") == 1
    assert "ssh " not in PREDEPLOY
    assert "scp " not in PREDEPLOY
    assert "DROPLET_TOUCHED=NO" in PREDEPLOY

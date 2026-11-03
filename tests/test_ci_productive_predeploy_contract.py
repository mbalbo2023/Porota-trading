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


def test_a210_early_host_incident_replay_covers_all_known_failure_families():
    """A CI host-transition RCA is incomplete if its regressions run only late."""
    early = PREDEPLOY.split('      - name: Fast host-transition incident replay', 1)[1].split(
        '      - name:', 1)[0]
    required = (
        'test_rc6_cold2_apply_guard.py',
        'test_rc6_degraded_closed_rescue.py',
        'test_rc6_a145_pending_rescue.py',
        'test_rc6_a151_operator_hold_deploy.py',
        'test_rc6_a166_ssh_argument_budget.py',
        'test_rc6_a172_running_continuation.py',
        'test_rc6_a175_running_continuation.py',
        'test_rc6_a178_a175_wiring.py',
        'test_rc6_deploy_failure_cleanup.py',
    )
    for name in required:
        assert (Path('tests') / name).is_file()
        assert early.count('tests/' + name) == 1, name
    assert 'POROTA_HOST_TRANSITION_EARLY=GREEN' in early


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
    assert PREDEPLOY.count("uses: docker/build-push-action@v7") == 1
    assert "load: true" in PREDEPLOY and "push: false" in PREDEPLOY
    assert "cache-from: type=gha,scope=porota-rc6-predeploy-v2" in PREDEPLOY
    assert "cache-to: type=gha,scope=porota-rc6-predeploy-v2" in PREDEPLOY
    assert "ssh " not in PREDEPLOY
    assert "scp " not in PREDEPLOY
    assert "DROPLET_TOUCHED=NO" in PREDEPLOY


def test_governed_gate_rejects_actual_pytest_skip_and_xfail_receipts(tmp_path):
    """Execute the workflow's real evaluator with rc=0 and one skipped testcase."""
    import json
    import os
    import subprocess
    import sys

    marker = '          TEST_RC="$TEST_RC" python - <<' + "'PY'\n"
    start = PREDEPLOY.index(marker) + len(marker)
    end = PREDEPLOY.index('\n          PY', start)
    evaluator = '\n'.join(line.removeprefix('          ')
                          for line in PREDEPLOY[start:end].splitlines())
    paths = {
        '/tmp/porota-test-collection.txt': tmp_path / 'collection.txt',
        '/tmp/porota-governed-tests.xml': tmp_path / 'junit.xml',
        '/tmp/porota-test-exclusions.txt': tmp_path / 'exclusions.txt',
        '/tmp/porota-governed-tests.json': tmp_path / 'result.json',
    }
    for old, new in paths.items():
        evaluator = evaluator.replace(old, str(new))
    paths['/tmp/porota-test-collection.txt'].write_text('tests/test_one.py: 1\n')
    paths['/tmp/porota-test-exclusions.txt'].write_text('')
    for xml, expected in (
        ('', 'GREEN'),
        ('<skipped message="deliberate" />', 'RED'),
        ('<skipped type="pytest.xfail" message="expected" />', 'RED'),
    ):
        paths['/tmp/porota-governed-tests.xml'].write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0">'
            '<testcase classname="test_one" name="test_one">' + xml +
            '</testcase></testsuite></testsuites>')
        result = subprocess.run([sys.executable, '-c', evaluator],
                                env=os.environ | {'TEST_RC': '0'},
                                capture_output=True, text=True, timeout=10)
        receipt = json.loads(paths['/tmp/porota-governed-tests.json'].read_text())
        assert receipt['status'] == expected
        assert (result.returncode == 0) == (expected == 'GREEN')


def test_predeploy_forces_unexpected_xpass_to_fail(tmp_path):
    """Pytest rc and native XML must turn XPASS into a failure."""
    import os
    import subprocess
    import sys
    from xml.etree import ElementTree as ET

    assert 'python -m pytest -q "${TEST_ARGS[@]}" -o xfail_strict=true --junitxml=' in PREDEPLOY
    file = tmp_path / 'test_unexpected_pass.py'
    file.write_text('import pytest\\n@pytest.mark.xfail(reason="expected failure")\\ndef test_unexpected_pass():\\n    assert True\\n'.replace('\\n', '\n'))
    junit = tmp_path / 'strict.xml'
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-o', 'xfail_strict=true',
         '--junitxml=' + str(junit), str(file)],
        env=os.environ | {'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=15,
    )
    root = ET.parse(junit).getroot()
    assert result.returncode != 0, result.stdout + result.stderr
    assert len(list(root.iter('testcase'))) == 1
    assert len(list(root.iter('failure'))) == 1

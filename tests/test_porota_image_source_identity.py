import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts.porota_image_source_identity import verify_source_identity

SHA = 'a' * 40
TREE = 'b' * 40


def test_observed_image_identity_is_required_and_matches_exactly():
    env = {'POROTA_BUILD_SHA': SHA, 'POROTA_CANDIDATE_TREE_SHA': TREE}
    result = verify_source_identity(SHA, TREE, environ=env)
    assert result['status'] == 'GREEN' and result['candidate_sha'] == SHA
    assert env == {'POROTA_BUILD_SHA': SHA, 'POROTA_CANDIDATE_TREE_SHA': TREE}


@pytest.mark.parametrize('env', [{}, {'POROTA_BUILD_SHA': SHA},
    {'POROTA_BUILD_SHA': 'c' * 40, 'POROTA_CANDIDATE_TREE_SHA': TREE},
    {'POROTA_BUILD_SHA': SHA, 'POROTA_CANDIDATE_TREE_SHA': 'c' * 40}])
def test_unknown_and_mismatched_identity_never_uses_expected_as_fallback(env):
    with pytest.raises(ValueError, match='MISSING_OR_MISMATCHED'):
        verify_source_identity(SHA, TREE, environ=env)


@pytest.mark.parametrize('value', [None, '', 'UNKNOWN', 'g' * 40])
def test_invalid_expected_identity_cannot_authorize_image(value):
    with pytest.raises(ValueError, match='EXPECTED_SOURCE_IDENTITY_INVALID'):
        verify_source_identity(value, TREE, environ={})


def test_child_process_inherits_identity_and_failure_is_red_without_private_output():
    env = dict(os.environ, POROTA_BUILD_SHA=SHA, POROTA_CANDIDATE_TREE_SHA=TREE)
    args = [sys.executable, 'scripts/porota_image_source_identity.py', '--expected-sha', SHA, '--expected-tree', TREE]
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)['candidate_sha'] == SHA
    env['POROTA_BUILD_SHA'] = 'PRIVATE_TEST_VALUE'
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and json.loads(result.stdout)['status'] == 'RED'
    assert 'PRIVATE_TEST_VALUE' not in result.stdout + result.stderr


def test_dashboard_env_cannot_override_the_frozen_image_identity(tmp_path, monkeypatch):
    import porota_mode_manager as manager
    monkeypatch.setattr(manager, 'DATA', tmp_path)
    folder = tmp_path / 'diagnosticos'; folder.mkdir()
    (folder / 'dashboard_preview_v1633.env').write_text(
        'POROTA_BUILD_SHA=old\nPOROTA_CANDIDATE_TREE_SHA=old-tree\nDASHBOARD_ACCESS_TOKEN=TEST_ONLY\n')
    monkeypatch.setattr(manager, 'env_file', lambda: {})
    output = manager.dashboard_env('PRODUCTION_PAPER').read_text()
    assert 'POROTA_BUILD_SHA=' not in output and 'POROTA_CANDIDATE_TREE_SHA=' not in output
    assert 'DASHBOARD_ACCESS_TOKEN=TEST_ONLY' in output


def test_build_and_promotion_verify_observed_identity_without_runtime_env_injection():
    root = Path(__file__).resolve().parents[1]
    predeploy = (root / '.github/workflows/porota-predeploy-v2.yml').read_text()
    assert '--build-arg "POROTA_BUILD_SHA=$CANDIDATE_SHA"' in predeploy
    assert '--build-arg "POROTA_CANDIDATE_TREE_SHA=$(git rev-parse HEAD^{tree})"' in predeploy
    assert 'verify_source_identity(sys.argv[1], sys.argv[2])' in predeploy
    deploy = (root / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    start = deploy.index('for IDENTITY_CONTAINER in porota_production_observer porota_production_dashboard;')
    end = deploy.index('POROTA_RUNNING_SOURCE_IDENTITY=GREEN', start)
    block = deploy[start:end]
    assert 'docker exec "$IDENTITY_CONTAINER" python /app/scripts/porota_image_source_identity.py' in block
    assert '--expected-sha "$CANDIDATE_SHA" --expected-tree "$CANDIDATE_TREE"' in block
    assert '--env' not in block and ' -e ' not in block

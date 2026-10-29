"""Replay the A175 candidate tag left on both running containers after retag."""
import hashlib
import json
from pathlib import Path

import pytest

import rc6_preopen as preopen


SHA = 'a' * 40
TREE = 'b' * 40
IMAGE_ID = 'sha256:' + 'c' * 64
FROZEN_ID = 'sha256:' + 'd' * 64  # docker load may normalize image configuration
STABLE = preopen.EXPECTED_IMAGE
CANDIDATE = STABLE + '-candidate-' + SHA


@pytest.fixture
def candidate_host(tmp_path, monkeypatch):
    root = tmp_path / 'porota'
    deploy = root / 'data/deploy'
    deploy.mkdir(parents=True)
    for rel in ('rc6_preopen.py', '_version.py'):
        (root / rel).write_bytes(Path(rel).read_bytes())
    frozen = {
        'candidate_sha': SHA, 'candidate_tree_sha': TREE,
        'image_id': FROZEN_ID, 'image_tar_sha256': 'e' * 64,
        'paper_mode_required': 'PRODUCTION_PAPER',
        'real_orders_sent_required': 0,
        'real_order_capability_required': 'BLOCKED', 'build_once': True,
    }
    manifest = {'schema_version': 1, 'status': 'GREEN',
                'bundle_sha256': 'f' * 64,
                'files': [{'path': rel, 'sha256': hashlib.sha256((root / rel).read_bytes()).hexdigest()}
                          for rel in ('rc6_preopen.py', '_version.py')],
                'file_count': 2}
    marker = {'candidate_sha': SHA, 'validation_status': 'VALIDATED_RUNTIME',
              'image_id': IMAGE_ID, 'mode': 'PRODUCTION_PAPER',
              'real_orders_sent': 0, 'real_order_routes': 'NOT_CALLED',
              'ppi_watch_untouched': True}
    files = {'porota-frozen-candidate.json': frozen,
             'porota-deploy-bundle-v2-manifest.json': manifest,
             'CURRENT_STATE_V2.json': marker}
    for filename, payload in files.items():
        (deploy / filename).write_text(json.dumps(payload))
    host = {'config_tag': CANDIDATE, 'image_id': IMAGE_ID,
            'candidate_image_id': IMAGE_ID, 'stable_image_id': IMAGE_ID,
            'running': 'true', 'restart': '0', 'oom': 'false',
            'readonly': 'true',
            'env': ['POROTA_BUILD_SHA=' + SHA, 'POROTA_CANDIDATE_TREE_SHA=' + TREE]}
    calls = []

    def cmd(args, timeout=20):
        calls.append(args)
        if args[:3] == ['docker', 'inspect', '-f']:
            return 0, '|'.join((host['running'], host['config_tag'], host['image_id'],
                                host['restart'], host['oom'], host['readonly'])), ''
        if args[:4] == ['docker', 'image', 'inspect', '-f']:
            if args[4] == '{{.Id}}':
                return 0, (host['candidate_image_id'] if args[5] == CANDIDATE
                           else host['stable_image_id']), ''
            if args[4] == '{{json .Config.Env}}':
                return 0, json.dumps(host['env']), ''
        raise AssertionError(args)

    monkeypatch.setattr(preopen, 'ROOT', root)
    monkeypatch.setattr(preopen, 'cmd', cmd)
    return deploy, files, host, calls


def state(host, *, deploy_sha=None, observer=False):
    return preopen.container('porota_production_observer' if observer else
                             'porota_production_dashboard', observer, deploy_sha)


def update(deploy, files, filename, key, value):
    files[filename][key] = value
    (deploy / filename).write_text(json.dumps(files[filename]))


def test_a175_deploy_candidate_tag_matches_loaded_id_stable_alias_and_source(candidate_host):
    deploy, files, host, calls = candidate_host
    update(deploy, files, 'CURRENT_STATE_V2.json', 'validation_status',
           'DEPLOYED_VALIDATION_PENDING')
    assert state(host, deploy_sha=SHA, observer=True)['state'] == 'GREEN'
    assert state(host, deploy_sha=SHA)['state'] == 'GREEN'
    assert ['docker', 'image', 'inspect', '-f', '{{.Id}}', STABLE] in calls
    # The predeploy tar ID is retained as evidence, while the exact loaded ID
    # drives both Docker references and the container .Image comparison.
    assert FROZEN_ID != host['image_id']


def test_daily_candidate_requires_validated_marker_for_exact_sha_and_image(candidate_host):
    deploy, files, host, _ = candidate_host
    assert state(host, observer=True)['state'] == 'GREEN'
    update(deploy, files, 'CURRENT_STATE_V2.json', 'validation_status',
           'DEPLOYED_VALIDATION_PENDING')
    assert state(host)['state'] == 'RED'
    update(deploy, files, 'CURRENT_STATE_V2.json', 'validation_status', 'VALIDATED_RUNTIME')
    update(deploy, files, 'CURRENT_STATE_V2.json', 'image_id', 'sha256:' + '1' * 64)
    assert state(host)['state'] == 'RED'
    update(deploy, files, 'CURRENT_STATE_V2.json', 'image_id', IMAGE_ID)
    update(deploy, files, 'CURRENT_STATE_V2.json', 'candidate_sha', '1' * 40)
    assert state(host)['state'] == 'RED'


@pytest.mark.parametrize('corruption', (
    'wrong_arg', 'candidate_tag', 'stable_alias', 'candidate_alias', 'immutable_id',
    'env_sha', 'env_tree', 'frozen_sha', 'frozen_tree', 'frozen_mode',
    'manifest_source', 'manifest_source_symlink', 'manifest_missing',
    'marker_paper', 'marker_route',
    'marker_symlink', 'frozen_symlink', 'oom', 'restart', 'observer_writable',
))
def test_candidate_provenance_or_runtime_corruption_is_red(candidate_host, corruption):
    deploy, files, host, _ = candidate_host
    deploy_sha = SHA
    if corruption == 'wrong_arg':
        deploy_sha = '2' * 40
    elif corruption == 'candidate_tag':
        host['config_tag'] = STABLE + '-candidate-' + '1' * 40
    elif corruption == 'stable_alias':
        host['stable_image_id'] = 'sha256:' + '1' * 64
    elif corruption == 'candidate_alias':
        host['candidate_image_id'] = 'sha256:' + '1' * 64
    elif corruption == 'immutable_id':
        host['image_id'] = 'sha256:' + '1' * 64
    elif corruption == 'env_sha':
        host['env'][0] = 'POROTA_BUILD_SHA=' + '1' * 40
    elif corruption == 'env_tree':
        host['env'][1] = 'POROTA_CANDIDATE_TREE_SHA=' + '1' * 40
    elif corruption == 'frozen_sha':
        update(deploy, files, 'porota-frozen-candidate.json', 'candidate_sha', '1' * 40)
    elif corruption == 'frozen_tree':
        update(deploy, files, 'porota-frozen-candidate.json', 'candidate_tree_sha', 'bad')
    elif corruption == 'frozen_mode':
        update(deploy, files, 'porota-frozen-candidate.json', 'paper_mode_required', 'REAL')
    elif corruption == 'manifest_source':
        (deploy.parent.parent / 'rc6_preopen.py').write_text('tampered')
    elif corruption == 'manifest_source_symlink':
        original = deploy.parent.parent / 'rc6_preopen.py'
        moved = deploy.parent.parent / 'original-rc6_preopen.py'
        original.rename(moved)
        original.symlink_to(moved)
    elif corruption == 'manifest_missing':
        (deploy / 'porota-deploy-bundle-v2-manifest.json').unlink()
    elif corruption == 'marker_paper':
        update(deploy, files, 'CURRENT_STATE_V2.json', 'real_orders_sent', 1)
    elif corruption == 'marker_route':
        update(deploy, files, 'CURRENT_STATE_V2.json', 'real_order_routes', 'CALLED')
    elif corruption in {'marker_symlink', 'frozen_symlink'}:
        filename = ('CURRENT_STATE_V2.json' if corruption == 'marker_symlink'
                    else 'porota-frozen-candidate.json')
        original = deploy / filename
        moved = deploy / ('original-' + filename)
        original.rename(moved)
        original.symlink_to(moved)
    elif corruption == 'oom':
        host['oom'] = 'true'
    elif corruption == 'restart':
        host['restart'] = '1'
    elif corruption == 'observer_writable':
        host['readonly'] = 'false'
    if corruption.startswith('marker_'):
        deploy_sha = None
    assert state(host, deploy_sha=deploy_sha, observer=True)['state'] == 'RED'


def test_stable_only_existing_runtime_accepts_only_matching_immutable_id(candidate_host):
    deploy, files, host, calls = candidate_host
    host['config_tag'] = STABLE
    (deploy / 'porota-frozen-candidate.json').unlink()
    assert state(host, observer=True)['state'] == 'GREEN'
    assert not any(call[-1] == CANDIDATE for call in calls)
    host['stable_image_id'] = 'sha256:' + '1' * 64
    assert state(host)['state'] == 'RED'
    host['stable_image_id'] = IMAGE_ID
    update(deploy, files, 'CURRENT_STATE_V2.json', 'validation_status',
           'DEPLOYED_VALIDATION_PENDING')
    assert state(host)['state'] == 'RED'
    update(deploy, files, 'CURRENT_STATE_V2.json', 'validation_status', 'VALIDATED_RUNTIME')
    update(deploy, files, 'CURRENT_STATE_V2.json', 'candidate_sha', 'bad')
    assert state(host)['state'] == 'RED'
    update(deploy, files, 'CURRENT_STATE_V2.json', 'candidate_sha', SHA)
    assert state(host, deploy_sha=SHA)['state'] == 'RED'

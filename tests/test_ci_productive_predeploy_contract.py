from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import time

import yaml

from ci_frozen_candidate_contract import (
    FrozenContractError,
    run_negative_fixtures,
    validate_frozen_candidate,
)


PREDEPLOY = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
POLICY = yaml.safe_load(Path("ops/policy/test-policy.yaml").read_text(encoding="utf-8"))
G6_RUNNER = Path('scripts/rc6_controlled_governed_runner.py').read_text(encoding='utf-8')


def governed_g7(steps):
    return next(step for step in steps if step['name'] ==
                'Governed automatic test discovery and execution - verify authenticated external G6')


def test_productive_scope_is_repository_root_automatic_discovery():
    governance = POLICY["test_governance"]
    scope = POLICY["productive_scope"]
    assert governance["automatic_discovery_required"] is True
    assert governance["manual_test_file_allowlist_as_primary_ci"] is False
    assert scope["roots"] == ["."]
    assert scope["python_files"] == "test_*.py"
    assert "tests/test_porota_*.py" not in PREDEPLOY
    assert "scope['roots'] == ['.']" in G6_RUNNER
    assert "governance['manual_test_file_allowlist_as_primary_ci'] is False" in G6_RUNNER
    assert "argv.append('--collect-only')" in G6_RUNNER
    assert "rc = int(pytest.main(argv, plugins=" in G6_RUNNER
    assert 'import_verified_governed(' in PREDEPLOY
    assert 'supervise-pytest' not in PREDEPLOY and 'python -m pytest' not in PREDEPLOY


def test_governed_predeploy_creates_real_private_venv_and_cleanup_survives_its_removal(tmp_path):
    from scripts import porota_predeploy_cleanup as private_cleanup

    workflow = yaml.safe_load(PREDEPLOY)
    steps = workflow['jobs']['artifact-gate']['steps']
    governed = governed_g7(steps)
    # Exercise the real workspace helper offline, independently of G7's
    # Actions/capacity admission. The workflow still gates this helper first.
    code = governed['run']
    create = next(line for line in code.splitlines() if 'porota_predeploy_test_workspace.py create --scope ' in line)
    assert code.index('g7_bootstrap_capacity environment-bootstrap') < code.index(create)
    setup = code.split('g7_bootstrap_capacity() {', 1)[0] + create + '\n' + '\n'.join(
        line for line in code.splitlines() if line.startswith('export PATH=') or '>> "$GITHUB_PATH"' in line)
    repo, runner = tmp_path / 'source', tmp_path / 'runner'
    repo.mkdir(mode=0o755)
    runner.mkdir(mode=0o755)
    (repo / 'scripts').mkdir(mode=0o755)
    for name in ('porota_predeploy_cleanup.py', 'porota_predeploy_test_workspace.py'):
        (repo / 'scripts' / name).write_bytes((Path('scripts') / name).read_bytes())
    subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Controlled RC6 test',
                    '-c', 'user.email=rc6-local@example.invalid', 'commit', '-qm', 'Native workspace fixture'],
                   check=True, capture_output=True)
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    context = {'repository': private_cleanup.REPOSITORY, 'workflow_path': private_cleanup.WORKFLOW,
               'event': 'pull_request', 'run_id': '471', 'run_attempt': '2',
               'candidate_sha': sha, 'candidate_tree': tree}

    def no_docker(*args, timeout):
        if args[:2] == ('image', 'ls'):
            return ''
        assert args == ('info', '--format', '{{.DockerRootDir}}')
        return str(runner)

    scope, control = private_cleanup.prepare(repo, runner, context, run=no_docker)
    private = Path(control['private_root'])
    env = dict(os.environ, POROTA_PREDEPLOY_TMP=str(private), RUNNER_TEMP=str(runner),
               POROTA_PREDEPLOY_OWNER=str(scope), POROTA_PREDEPLOY_OWNER_UUID=control['owner_uuid'],
               CANDIDATE_SHA=sha, CANDIDATE_TREE=tree, GITHUB_REPOSITORY=private_cleanup.REPOSITORY,
               GITHUB_WORKFLOW_REF=private_cleanup.REPOSITORY + '/' + private_cleanup.WORKFLOW + '@controlled-test',
               GITHUB_EVENT_NAME='pull_request', GITHUB_RUN_ID='471', GITHUB_RUN_ATTEMPT='2',
               GITHUB_ENV=str(tmp_path / 'github-env'), GITHUB_PATH=str(tmp_path / 'github-path'))
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
    subprocess.run(['bash', '-c', setup], cwd=repo, env=env, check=True, capture_output=True, timeout=30)
    observed = subprocess.check_output([str(private / 'venv/bin/python'), '-I', '-B', '-c',
        'import json,sys;print(json.dumps([sys.prefix,sys.base_prefix]))'], text=True)
    prefix, base = json.loads(observed)
    assert prefix == str(private / 'venv') and prefix != base
    emitted = dict(line.split('=', 1) for line in (tmp_path / 'github-env').read_text().splitlines())
    bootstrap = emitted['POROTA_PREDEPLOY_BOOTSTRAP_PYTHON']
    assert Path(bootstrap).samefile(sys.executable)
    assert (tmp_path / 'github-path').read_text().strip() == str(private / 'venv/bin')
    rows = private_cleanup.inventory(control, deadline=time.monotonic() + 30, clock=time.monotonic)
    assert rows and not os.path.lexists(private / 'venv/lib64')
    external = Path(emitted['POROTA_PREDEPLOY_PYTEST_BASETEMP']).parent
    assert external.parent == runner and not external.is_relative_to(private)
    assert external.is_dir() and external.stat().st_uid == os.geteuid()
    claim = json.loads((private / 'porota-test-workspace.json').read_text())
    assert claim['owner_uuid'] == control['owner_uuid'] and claim['context'] == context
    cleanup = next(step for step in steps if step['name'] == 'Local cleanup')
    summary = next(step for step in steps if step['name'] == 'Predeploy summary')
    assert cleanup['if'] == "always() && steps.pytest_postread.outputs.safe_postread == 'true'"
    assert cleanup['run'].splitlines()[1].startswith('"${POROTA_PREDEPLOY_BOOTSTRAP_PYTHON:-python}"')
    assert summary['run'].splitlines()[1].startswith('"${POROTA_PREDEPLOY_BOOTSTRAP_PYTHON:-python}"')
    shutil.rmtree(private)  # Only this test's owned new venv, as the real cleanup does.
    env['POROTA_PREDEPLOY_BOOTSTRAP_PYTHON'] = bootstrap
    env['PATH'] = str(private / 'venv/bin') + os.pathsep + env['PATH']
    subprocess.run(['bash', '-c', '"${POROTA_PREDEPLOY_BOOTSTRAP_PYTHON:-python}" -I -B -c "import sys;assert sys.version_info[:2] in ((3,11),(3,12))"'],
                   env=env, check=True, capture_output=True, timeout=5)
    assert external.exists()  # Explicitly retained; no claim of external cleanup.


def test_predeploy_uses_bound_external_pytest_basetemp_and_reports_retention():
    steps = yaml.safe_load(PREDEPLOY)['jobs']['artifact-gate']['steps']
    governed = governed_g7(steps)
    assert 'porota_predeploy_test_workspace.py create --scope "$POROTA_PREDEPLOY_OWNER"' in governed['run']
    assert 'import_verified_governed(' in governed['run']
    assert 'supervise-pytest' not in governed['run']
    assert "'--basetemp='+str(output/(args.phase+'-private')/'pytest')" in G6_RUNNER
    assert "namespace = phase_namespace(root, output, args.phase," in G6_RUNNER
    assert 'source_suite_reexecuted":False' in Path('scripts/rc6_architectural_gates.py').read_text()
    uploaded = next(step for step in steps if step['name'] == 'Upload predeploy evidence')
    assert uploaded['if'] == "always() && steps.pytest_postread.outputs.safe_postread == 'true'"
    for name in ('porota-test-workspace.json', 'porota-pytest-owned-fin.json', 'porota-pytest-retained.json',
                 'porota-pytest-launch-intent.json', 'porota-pytest-postread.json', 'porota-pytest-producers-postread.json',
                 'porota-pytest-collection-launch-intent.json', 'porota-pytest-collection-owned-fin.json'):
        assert '${{ env.POROTA_PREDEPLOY_TMP }}/' + name in uploaded['with']['path']
    assert steps.index(uploaded) < next(index for index, step in enumerate(steps) if step['name'] == 'Local cleanup')


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
    assert 'POROTA_TEST_COUNT_ORIGIN=AUTHENTICATED_G6_NATIVE_PAYLOAD' in PREDEPLOY


def test_all_six_negative_fixtures_are_executed_fail_closed(tmp_path):
    evidence = run_negative_fixtures(tmp_path / "porota-negative-fixtures-unit.json")
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

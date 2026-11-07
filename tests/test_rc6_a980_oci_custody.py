"""OCI contract tests use a fake daemon, NOT proof of physical isolation."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml


MODULE = Path(__file__).resolve().parents[1] / 'scripts/rc6_ci_oci_custody.py'
IMAGE = 'example.invalid/qa@sha256:' + 'a' * 64
CID = 'b' * 64


def module():
    assert MODULE.is_file(), 'independent OCI custody launcher is not implemented'
    spec = importlib.util.spec_from_file_location('a980_custody', MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'candidate.py').write_text('pass\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'candidate.py'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=QA',
                    '-c', 'user.email=qa@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    def rev(ref):
        return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', ref], text=True).strip()
    return repo, rev('HEAD'), rev('HEAD^{tree}')


class FakeDocker:
    """Deliberate test double for Docker only; never treated as OS evidence."""
    def __init__(self, mutate=None, timeout=False):
        self.calls = []
        self.mutate = mutate
        self.timeout = timeout
        self.config = None

    def __call__(self, argv, *, timeout):
        self.calls.append(argv)
        if 'image' in argv:
            return json.dumps([{'RepoDigests': [IMAGE], 'Config': {'Volumes': None}}])
        if 'create' in argv:
            def option(name):
                return argv[argv.index(name) + 1]
            self.config = {
                'Id': CID, 'Config': {'User': '65532:65532',
                'Labels': {'rc6.qa.custody': option('--label').split('=', 1)[1]}},
                'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True,
                'NetworkMode': 'none', 'PidMode': '', 'IpcMode': 'none', 'CgroupnsMode': 'private',
                'CapAdd': None, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                'PidsLimit': 256, 'Memory': 1073741824, 'MemorySwap': 1073741824,
                'NanoCpus': 2000000000, 'LogConfig': {'Type': 'none'},
                'Devices': [], 'DeviceRequests': None, 'Binds': None,
                'VolumesFrom': None, 'GroupAdd': None,
                'Tmpfs': {'/work': 'rw,nosuid,nodev,noexec,size=512m,mode=1777',
                          '/tmp': 'rw,nosuid,nodev,noexec,size=64m,mode=1777'}},
                'Mounts': [{'Type': 'bind', 'Source': option('--mount').split(',')[1][4:],
                            'Destination': '/src', 'RW': False}],
            }
            return CID + '\n'
        if 'inspect' in argv:
            value = json.loads(json.dumps(self.config))
            if self.mutate:
                self.mutate(value)
            return json.dumps([value])
        if 'wait' in argv:
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, 300)
            return '2\n'
        return CID + '\n'


def invoke(mod, source, out, fake, image=IMAGE):
    repo, sha, tree = source
    return mod.run(repo, out, image=image, source_sha=sha,
                   source_tree_sha=tree, command=fake)


@pytest.mark.parametrize('image', ['', 'python:latest', IMAGE + '\n--privileged'])
def test_missing_or_mutable_image_never_starts_docker(source, tmp_path, image):
    fake = FakeDocker()
    assert invoke(module(), source, tmp_path / 'public', fake, image) == 79
    assert fake.calls == []


def test_reuses_bounded_runner_with_no_host_writable_mount_or_secret(source, tmp_path):
    fake = FakeDocker()
    out = tmp_path / 'public'
    assert invoke(module(), source, out, fake) == 79
    create = next(call for call in fake.calls if 'create' in call)
    assert '--privileged' not in create and '--env-file' not in create
    assert create.count('--mount') == 1
    assert create[create.index('--mount') + 1].endswith(',dst=/src,readonly,bind-recursive=disabled')
    assert 'scripts.rc6_ci_parallel_runner' in create
    assert '--bounded-shard-index' in create and '--batch-limit' in create
    assert '--entrypoint' in create and '/usr/bin/env' in create and '-i' in create
    assert sorted(p.name for p in out.iterdir()) == ['attestation.json']
    receipt = json.loads((out / 'attestation.json').read_text())
    assert receipt == {
        'schema_version': 1, 'status': 'OFFHOST_DIAGNOSTIC_ONLY',
        'source_sha': source[1], 'source_tree_sha': source[2],
        'image_digest': 'sha256:' + 'a' * 64, 'container_exit_code': 2,
        'automatic_go': False, 'runtime_authority': False,
        'full_suite_certified': False, 'physical_custody_certified': False,
    }
    assert fake.calls[-1][-2:] == ['rm', CID]
    assert not any('--force' in call for call in fake.calls)


@pytest.mark.parametrize('mutation', [
    lambda c: c['HostConfig'].update(Privileged=True),
    lambda c: c['HostConfig'].update(NetworkMode='host'),
    lambda c: c['HostConfig'].update(CapAdd=['SYS_ADMIN']),
    lambda c: c['Config'].update(User='0'),
    lambda c: c['Mounts'].append({'Type': 'bind', 'Source': '/var/run/docker.sock',
                                 'Destination': '/var/run/docker.sock', 'RW': True}),
])
def test_daemon_policy_drift_never_starts_candidate(source, tmp_path, mutation):
    fake = FakeDocker(mutate=mutation)
    out = tmp_path / 'public'
    assert invoke(module(), source, out, fake) == 79
    assert not any('start' in call for call in fake.calls)
    assert not (out / 'attestation.json').exists()


def test_foreign_container_identity_forbids_even_cleanup(source, tmp_path):
    fake = FakeDocker(mutate=lambda c: c['Config']['Labels'].update({'rc6.qa.custody': 'foreign'}))
    assert invoke(module(), source, tmp_path / 'public', fake) == 79
    assert not any('start' in call or 'rm' in call for call in fake.calls)


def test_timeout_kills_only_owned_container_and_publishes_nothing(source, tmp_path):
    fake = FakeDocker(timeout=True)
    out = tmp_path / 'public'
    assert invoke(module(), source, out, fake) == 79
    assert any(call[-4:] == ['stop', '--timeout', '5', CID] for call in fake.calls)
    assert any(call[-2:] == ['rm', CID] for call in fake.calls)
    assert not any('--force' in call for call in fake.calls)
    assert not (out / 'attestation.json').exists()


def test_symlink_output_is_rejected_before_daemon(source, tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    out = tmp_path / 'public'
    out.symlink_to(target, target_is_directory=True)
    fake = FakeDocker()
    assert invoke(module(), source, out, fake) == 79
    assert fake.calls == [] and list(target.iterdir()) == []


def test_dirty_source_is_rejected_before_daemon(source, tmp_path):
    (source[0] / 'candidate.py').write_text('changed\n')
    fake = FakeDocker()
    assert invoke(module(), source, tmp_path / 'public', fake) == 79
    assert fake.calls == []


@pytest.mark.parametrize('value', [None, [], 'SYNTHETIC_SECRET'])
def test_malformed_daemon_config_is_hold_not_raw_traceback(source, tmp_path, value, capsys):
    fake = FakeDocker(mutate=lambda c: c.update(Config=value))
    assert invoke(module(), source, tmp_path / 'public', fake) == 79
    assert capsys.readouterr() == ('', '')
    assert not any('start' in c or 'rm' in c for c in fake.calls)


@pytest.mark.parametrize('value', [None, [], 'SYNTHETIC_SECRET'])
def test_malformed_log_config_is_hold_not_raw_traceback(source, tmp_path, value, capsys):
    fake = FakeDocker(mutate=lambda c: c['HostConfig'].update(LogConfig=value))
    assert invoke(module(), source, tmp_path / 'public', fake) == 79
    assert capsys.readouterr() == ('', '')
    assert not any('start' in c for c in fake.calls)


@pytest.mark.parametrize('value', [None, '', 'host'])
def test_cgroup_namespace_must_be_verified_private(source, tmp_path, value):
    fake = FakeDocker(mutate=lambda c: c['HostConfig'].update(CgroupnsMode=value))
    assert invoke(module(), source, tmp_path / 'public', fake) == 79
    assert not any('start' in c for c in fake.calls)
    assert not (tmp_path / 'public' / 'attestation.json').exists()


@pytest.mark.parametrize('digests', [IMAGE, {IMAGE: True}, [IMAGE, 42]])
def test_malformed_repo_digests_never_creates_candidate(source, tmp_path, digests):
    fake = FakeDocker()
    def transport(argv, *, timeout):
        if 'image' in argv:
            return json.dumps([{'RepoDigests': digests, 'Config': {'Volumes': None}}])
        return fake(argv, timeout=timeout)
    assert invoke(module(), source, tmp_path / 'public', transport) == 79
    assert not any('create' in c for c in fake.calls)
    assert not (tmp_path / 'public' / 'attestation.json').exists()


def test_failed_cleanup_reports_fixed_unverified_status_without_raw_error(source, tmp_path):
    fake = FakeDocker()
    reports = []
    def transport(argv, *, timeout):
        if 'rm' in argv:
            raise OSError('SYNTHETIC_SECRET_FROM_DAEMON')
        return fake(argv, timeout=timeout)
    repo, sha, tree = source
    assert module().run(repo, tmp_path / 'public', image=IMAGE,
                        source_sha=sha, source_tree_sha=tree,
                        command=transport, report=reports.append) == 79
    assert reports == ['TEARDOWN_UNVERIFIED']
    assert not (tmp_path / 'public' / 'attestation.json').exists()


def test_output_parent_swap_cannot_redirect_publication(source, tmp_path):
    parent = tmp_path / 'original'
    parent.mkdir()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    out = parent / 'public'
    fake = FakeDocker()
    def transport(argv, *, timeout):
        result = fake(argv, timeout=timeout)
        if 'wait' in argv:
            parent.rename(tmp_path / 'moved')
            parent.symlink_to(foreign, target_is_directory=True)
        return result
    assert invoke(module(), source, out, transport) == 79
    assert list(foreign.iterdir()) == []


def test_real_subprocess_scrubs_tokens_and_prints_no_raw_output(monkeypatch, capsys):
    monkeypatch.setenv('GITHUB_TOKEN', 'SYNTHETIC_GITHUB_SECRET')
    monkeypatch.setenv('ACTIONS_RUNTIME_TOKEN', 'SYNTHETIC_ACTIONS_SECRET')
    result = module()._command(['/usr/bin/env'], timeout=5)
    assert 'SYNTHETIC_' not in result
    assert capsys.readouterr() == ('', '')


def test_ws12_never_executes_candidate_supervisor_as_docker_authority(tmp_path):
    workflow = MODULE.parents[1] / '.github/workflows/rc6-ws-tests-12-current-contract.yml'
    steps = yaml.safe_load(workflow.read_text())['jobs']['full-suite']['steps']
    assert steps[0]['id'] == 'execution_custody'
    selected = [step for step in steps if step.get('id') == 'oci_diagnostic']
    assert len(selected) == 1, 'existing WS12 must wire the supervisor behind HOLD'
    step = selected[0]
    assert step.get('if') in (None, 'success()')
    assert 'env' not in step
    assert 'rc6_ci_oci_custody.py' not in step['run']
    assert 'secrets.' not in step['run'] and 'pip install' not in step['run']
    assert not any(str(s.get('uses', '')).startswith('actions/upload-artifact@') for s in steps)
    result = subprocess.run(['bash', '-c', step['run']], cwd=tmp_path,
                            env={'PATH': '/usr/bin:/bin', 'RC6_QA_CUSTODIAN_SHA': 'f' * 40,
                                 'RC6_QA_IMAGE_DIGEST': IMAGE},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 79 and result.stderr == ''
    assert result.stdout == 'RC6_OCI_QA=BLOQUEADO|reason=TRUSTED_BOOTSTRAP_UNVERIFIED|automatic_go=false\n'


def test_legacy_sanitizer_cannot_bypass_independent_bootstrap_hold():
    workflow = MODULE.parents[1] / '.github/workflows/rc6-ws-tests-12-current-contract.yml'
    steps = yaml.safe_load(workflow.read_text())['jobs']['full-suite']['steps']
    sanitizer = next(s for s in steps if s.get('name') == 'Sanitize FULL evidence')
    assert sanitizer['if'] == (
        "${{ always() && steps.execution_custody.outcome == 'success' "
        "&& steps.oci_diagnostic.outcome == 'success' }}"
    )

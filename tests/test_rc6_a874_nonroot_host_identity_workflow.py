"""A874: execute isolated metadata code OFFHOST; never connect to a host."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/rc6-a205-paper-book-readonly.yml'
SHA = 'a' * 40
SOCKET = '/run/user/1001/porota-rc6/docker.sock'
VETTED_BASE = '151f5b1e9b757897ed38a5c878212a9405289d47'
GUARD_PATH = 'scripts/rc6_droplet_identity_guard.py'
GUARD_BLOB = 'af5997f95496d7001525db1954385531050cbab7'
GUARD_SHA256 = 'e7a2bfc510ef20ad330ba1b81cd37120136bf1837ec1d1336aab6b661ca38ffc'


def job(source, name):
    found = re.search(r'(?ms)^  ' + re.escape(name) + r':\n(.*?)(?=^  [a-zA-Z][\w-]*:|\Z)', source)
    if not found:
        raise AssertionError('A874_JOB_MISSING:' + name)
    return found.group(1)


def job_selected(block, actor, ref):
    match = re.search(r'^    if: (.+)$', block, re.M)
    if match is None:
        raise AssertionError('A874_EXPLICIT_JOB_IF_REQUIRED')
    expression = match.group(1).replace('github.actor', repr(actor))
    expression = expression.replace('github.ref_name', repr(ref)).replace('&&', ' and ').replace('||', ' or ')
    tree = ast.parse(expression, mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq,
               ast.Constant, ast.Call, ast.Name, ast.Load)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError('A874_UNSUPPORTED_JOB_IF')
    return eval(compile(tree, '<job-if>', 'eval'), {'__builtins__': {}},
                {'startsWith': lambda value, prefix: value.casefold().startswith(prefix.casefold())})


def assert_routes(source):
    legacy = job(source, 'paper-preflight')
    metadata = job(source, 'nonroot-metadata')
    for actor, ref, legacy_want, metadata_want in (
        ('mbalbo2023', 'rc6-a874-nonroot-host-ro-v1-' + SHA, False, True),
        ('mbalbo2023', 'rc6-a205-paper-book-ro-v1-' + SHA, True, False),
        ('mbalbo2023', 'work/rc6-a874-nonroot-host-metadata-20261107', False, False),
        ('someone-else', 'rc6-a874-nonroot-host-ro-v1-' + SHA, False, False),
        ('mbalbo2023', 'deploy/rc6-pr69-isolated-20260915', False, False),
    ):
        if job_selected(legacy, actor, ref) is not legacy_want:
            raise AssertionError('A874_LEGACY_ROUTE_REACHABLE')
        if job_selected(metadata, actor, ref) is not metadata_want:
            raise AssertionError('A874_METADATA_ROUTE_INVALID')


def remote_source(source):
    block = job(source, 'nonroot-metadata')
    match = re.search(r'(?ms)^          # RC6_A874_REMOTE_BEGIN\n(.*?)^          # RC6_A874_REMOTE_END$', block)
    if not match:
        raise AssertionError('A874_REMOTE_SOURCE_MISSING')
    return textwrap.dedent(match.group(1))


def preconnection_script(source, name='nonroot-metadata'):
    block = job(source, name)
    marker = ('Verify immutable diagnostic ref and source' if name == 'paper-preflight'
              else 'Verify exact metadata diagnostic ref before any connection')
    part = block.split('- name: ' + marker + '\n', 1)[1]
    return textwrap.dedent(part.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])


def validate_remote(source):
    tree = ast.parse(source)
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    if imports != {'json', 'os', 'stat', 'sys'} or any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)):
        raise AssertionError('A874_REMOTE_IMPORT_NOT_ALLOWED')
    calls = {'emit', 'main', 'inspect_metadata', 'print', 'str', 'len', 'range', 'any',
             'enumerate', 'RuntimeError', 'SystemExit'}
    attrs = {'os.getuid', 'os.geteuid', 'os.getgid', 'os.getegid', 'os.getenv',
             'os.lstat', 'stat.S_ISDIR', 'stat.S_ISSOCK', 'json.dumps', 'sys.exit'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in calls:
                continue
            if isinstance(node.func, ast.Attribute) and ast.unparse(node.func) in attrs:
                continue
            raise AssertionError('A874_REMOTE_CALL_NOT_ALLOWED')
    return tree


class A874WorkflowContract(unittest.TestCase):
    def source(self):
        return WORKFLOW.read_text(encoding='utf-8')

    def test_metadata_route_cannot_enter_legacy_sudo_book_job(self):
        assert_routes(self.source())

    def test_missing_legacy_prefix_condition_is_detected(self):
        source = self.source()
        assert_routes(source)
        changed = source.replace("github.actor == 'mbalbo2023' && startsWith(github.ref_name, 'rc6-a205-paper-book-ro-v1-')", "github.actor == 'mbalbo2023'", 1)
        self.assertNotEqual(source, changed)
        with self.assertRaisesRegex(AssertionError, 'LEGACY_ROUTE_REACHABLE'):
            assert_routes(changed)

    def test_metadata_source_has_no_mutation_privileged_or_external_calls(self):
        source = remote_source(self.source())
        validate_remote(source)
        for injected in ('import subprocess\nsubprocess.run(["sudo", "id"])',
                         'os.remove("/tmp/x")', 'os.system("docker ps")',
                         'open("/opt/porota-trading/data/paper_v17/observer_v17.db")',
                         '__import__("sqlite3")', 'os.system("systemctl restart x")'):
            with self.subTest(injected=injected), self.assertRaises(AssertionError):
                validate_remote(source + '\n' + injected)

    def test_runner_route_is_exact_and_never_dispatches_or_mutates_host(self):
        block = job(self.source(), 'nonroot-metadata')
        self.assertIn('rc6-a874-nonroot-host-ro-v1-$GITHUB_SHA', block)
        self.assertIn('persist-credentials: false', block)
        self.assertIn('StrictHostKeyChecking=yes', block)
        self.assertIn('IdentitiesOnly=yes', block)
        self.assertIn("'python3 -I -B -'", block)
        self.assertNotRegex(block, r'\b(?:sudo|systemctl|sqlite3|scp|sftp)\b|\bdocker\s+')
        self.assertNotIn('secrets: inherit', block)
        self.assertNotIn('workflow_dispatch:', self.source())
        promote = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        self.assertLess(promote.index('exit 79'), promote.index('name: Prepare strict SSH'))

    def test_only_two_fixed_python_programs_can_be_sent_to_host(self):
        block = job(self.source(), 'nonroot-metadata')
        self.assertEqual(len(re.findall(r'\bssh -i ', block)), 2)
        commands = re.findall(r'"\$DO_USER@\$DO_HOST"\s+(?:\\\n\s*)?\x27([^\x27]+)\x27', block)
        self.assertEqual(commands, ['python3 -I -B - --expected-id 594077619 --expected-region nyc1',
                                    'python3 -I -B -'])

    def test_receipt_filter_rejects_extra_private_fields_and_false_go(self):
        block = job(self.source(), 'nonroot-metadata')
        script = textwrap.dedent(block.split('RESULT="$RESULT" python3 -I -B - <<\x27PY\x27\n', 1)[1].split('\n          PY\n', 1)[0])
        base = {'schema': 'RC6_A874_NONROOT_METADATA_V1', 'status': 'UNCONFIGURED',
                'reason': 'DEDICATED_ENDPOINT_NOT_CONFIGURED', 'uid': 1001, 'gid': 1001,
                'diagnostic_only': True, 'deploy_approved': False, 'host_mutations': 0,
                'exclusive_namespace_attested': False, 'daemon_rootless': 'NO_VERIFICADO'}
        for value, allowed in ((base, True), (dict(base, private_path='/other-app/secret'), False),
                               (dict(base, deploy_approved=True), False),
                               (dict(base, reason='PRIVATE_TOKEN'), False), ('not json', False)):
            with self.subTest(value=value):
                result = subprocess.run([sys.executable, '-I', '-B', '-'], input=script,
                                        text=True, capture_output=True, timeout=10,
                                        env=dict(os.environ, RESULT=json.dumps(value)))
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                if allowed:
                    self.assertEqual(json.loads(result.stdout), base)
                else:
                    self.assertEqual(result.stdout, '')
                    self.assertNotIn('/other-app', result.stderr)
                    self.assertNotIn('PRIVATE_TOKEN', result.stderr)

    def test_remote_cli_without_configuration_is_red_even_without_adapters(self):
        source = remote_source(self.source())
        result = subprocess.run([sys.executable, '-I', '-B', '-'], input=source,
                                text=True, capture_output=True, timeout=10, env={})
        self.assertEqual(result.returncode, 2, result.stderr)
        value = json.loads(result.stdout)
        self.assertIn(value['status'], {'BLOCKED', 'UNCONFIGURED'})
        self.assertFalse(value['deploy_approved'])
        self.assertEqual(value['host_mutations'], 0)

    def test_actual_preconnection_shell_guards_reject_cross_route_and_wrong_sha(self):
        source = self.source()
        actual_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        for name, prefix in (('nonroot-metadata', 'rc6-a874-nonroot-host-ro-v1-'),
                             ('paper-preflight', 'rc6-a205-paper-book-ro-v1-')):
            script = preconnection_script(source, name)
            for ref, expected in ((prefix + actual_sha, 0),
                                  ('work/rc6-a874-nonroot-host-metadata-20261107', 1),
                                  (prefix + 'b' * 40, 1),
                                  (('rc6-a205-paper-book-ro-v1-' if name == 'nonroot-metadata'
                                    else 'rc6-a874-nonroot-host-ro-v1-') + actual_sha, 1)):
                with self.subTest(name=name, ref=ref):
                    env = dict(os.environ, GITHUB_SHA=actual_sha, GITHUB_REF_NAME=ref)
                    result = subprocess.run(['bash', '-c', script], cwd=ROOT, env=env,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode == 0, expected == 0, result.stderr)


class A874VettedPinGuard(unittest.TestCase):
    def vetted_bytes(self):
        return subprocess.check_output(['git', 'show', VETTED_BASE + ':' + GUARD_PATH], cwd=ROOT)

    def run_fixture(self, mode):
        """Real disposable git/worktree fixtures; marker substitutes for SSH."""
        script = preconnection_script(WORKFLOW.read_text(encoding='utf-8'))
        vetted = self.vetted_bytes()
        with tempfile.TemporaryDirectory(prefix='a874-pin-offhost-') as folder:
            root = Path(folder)
            target = root / GUARD_PATH
            target.parent.mkdir()
            target.write_bytes(vetted)

            def git(*args):
                return subprocess.check_output(
                    ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=A874 OFFHOST',
                     '-c', 'user.email=a874-offhost@example.invalid', *args],
                    cwd=root, stderr=subprocess.PIPE, text=True).strip()

            git('-c', 'init.templateDir=', 'init', '-q')
            git('add', GUARD_PATH)
            git('commit', '-qm', 'synthetic reviewed source fixture')
            if mode == 'worktree_tampered':
                target.write_bytes(vetted + b'\n# UNREVIEWED_WORKTREE\n')
            elif mode == 'head_tampered_worktree_restored':
                target.write_bytes(vetted + b'\n# UNREVIEWED_COMMIT\n')
                git('add', GUARD_PATH)
                git('commit', '-qm', 'synthetic unreviewed source fixture')
                target.write_bytes(vetted)
            elif mode == 'missing':
                target.unlink()
            elif mode == 'symlink':
                other = root / 'same-bytes.py'
                other.write_bytes(vetted)
                target.unlink()
                target.symlink_to(other)
            sha = git('rev-parse', 'HEAD')
            env = dict(os.environ, GITHUB_SHA=sha,
                       GITHUB_REF_NAME='rc6-a874-nonroot-host-ro-v1-' + sha)
            return subprocess.run(['bash', '-c', script + '\nprintf "REACHED_SSH_BOUNDARY\\n"\n'],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=10)

    def test_vetted_base_contains_exact_independently_pinned_blob_and_bytes(self):
        blob = subprocess.check_output(['git', 'rev-parse', VETTED_BASE + ':' + GUARD_PATH],
                                       cwd=ROOT, text=True).strip()
        self.assertEqual(blob, GUARD_BLOB)
        self.assertEqual(hashlib.sha256(self.vetted_bytes()).hexdigest(), GUARD_SHA256)

    def test_vetted_source_can_pass_runner_preconnection_check_without_ssh(self):
        result = self.run_fixture('vetted')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'REACHED_SSH_BOUNDARY\n')

    def test_tampered_worktree_stops_before_ssh_boundary(self):
        result = self.run_fixture('worktree_tampered')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('REACHED_SSH_BOUNDARY', result.stdout)

    def test_unreviewed_head_stops_even_when_worktree_bytes_are_restored(self):
        result = self.run_fixture('head_tampered_worktree_restored')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('REACHED_SSH_BOUNDARY', result.stdout)

    def test_missing_or_symlink_pin_source_stops_before_ssh_boundary(self):
        for mode in ('missing', 'symlink'):
            with self.subTest(mode=mode):
                result = self.run_fixture(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('REACHED_SSH_BOUNDARY', result.stdout)

    def test_source_pin_checks_precede_connection_material_and_ssh(self):
        block = job(WORKFLOW.read_text(encoding='utf-8'), 'nonroot-metadata')
        self.assertIn('PIN_GUARD_BLOB=', block)
        self.assertIn('PIN_GUARD_SHA256=', block)
        self.assertLess(block.index('PIN_GUARD_BLOB='), block.index('name: Prepare metadata-only connection'))
        self.assertLess(block.index('PIN_GUARD_SHA256='), block.index('timeout 20 ssh'))


class A874MetadataBehavior(unittest.TestCase):
    def run_metadata(self, *, endpoint=SOCKET, uid=1001, gid=1001, denied=False,
                     mode=stat.S_IFSOCK | 0o600, owner=1001, parent_mode=0o700):
        source = remote_source(WORKFLOW.read_text(encoding='utf-8'))
        validate_remote(source)
        namespace = {'__name__': 'a874_test'}
        exec(compile(source, '<a874-remote>', 'exec'), namespace)
        calls = []

        def metadata(path):
            calls.append(path)
            if denied:
                raise PermissionError('PRIVATE_PATH_MUST_NOT_LEAK')
            return SimpleNamespace(st_mode=mode if path == SOCKET else stat.S_IFDIR | parent_mode,
                                   st_uid=owner, st_gid=1001, st_dev=7, st_ino=42, st_nlink=1)

        env = {} if endpoint is None else {'POROTA_RC6_ROOTLESS_SOCKET': endpoint}
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(os, 'getuid', return_value=uid), \
             mock.patch.object(os, 'geteuid', return_value=uid), \
             mock.patch.object(os, 'getgid', return_value=gid), \
             mock.patch.object(os, 'getegid', return_value=gid), \
             mock.patch.object(os, 'lstat', side_effect=metadata), contextlib.redirect_stdout(output):
            rc = namespace['main']()
        raw = output.getvalue()
        value = json.loads(raw)
        self.assertEqual(rc, 2)
        self.assertFalse(value['deploy_approved'])
        self.assertFalse(value['exclusive_namespace_attested'])
        self.assertEqual(value['host_mutations'], 0)
        self.assertTrue(value['diagnostic_only'])
        self.assertNotIn(SOCKET, raw)
        self.assertNotIn('PRIVATE_PATH', raw)
        return value, calls

    def test_missing_endpoint_stops_without_guessing_or_stat(self):
        value, calls = self.run_metadata(endpoint=None)
        self.assertEqual(value['status'], 'UNCONFIGURED')
        self.assertEqual(calls, [])

    def test_root_or_root_group_stops_before_paths(self):
        for uid, gid in ((0, 1001), (1001, 0)):
            with self.subTest(uid=uid, gid=gid):
                value, calls = self.run_metadata(uid=uid, gid=gid)
                self.assertEqual(value['reason'], 'NONROOT_IDENTITY_REQUIRED')
                self.assertEqual(calls, [])

    def test_other_identity_or_unscoped_path_stops_without_stat(self):
        for endpoint in ('/run/user/1002/porota-rc6/docker.sock', '/var/run/docker.sock',
                         '/run/user/1001/other-app/docker.sock', '/run/user/1001/porota-rc6/../docker.sock',
                         'unix:///run/user/1001/porota-rc6/docker.sock'):
            with self.subTest(endpoint=endpoint):
                value, calls = self.run_metadata(endpoint=endpoint)
                self.assertEqual(value['reason'], 'SCOPED_ENDPOINT_INVALID')
                self.assertEqual(calls, [])

    def test_permission_denied_stops_without_retry_or_error_disclosure(self):
        value, calls = self.run_metadata(denied=True)
        self.assertEqual(value['reason'], 'METADATA_PERMISSION_DENIED')
        self.assertEqual(len(calls), 1)

    def test_symlink_non_socket_foreign_owner_and_shared_parent_are_rejected(self):
        for kwargs in ({'mode': stat.S_IFLNK | 0o777}, {'mode': stat.S_IFREG | 0o600},
                       {'owner': 1002}, {'parent_mode': 0o770}, {'mode': stat.S_IFSOCK | 0o666}):
            with self.subTest(kwargs=kwargs):
                value, _ = self.run_metadata(**kwargs)
                self.assertEqual(value['status'], 'BLOCKED')

    def test_even_private_socket_metadata_never_certifies_rootless_or_go(self):
        value, calls = self.run_metadata()
        self.assertEqual(value['status'], 'METADATA_ONLY')
        self.assertEqual(value['daemon_rootless'], 'NO_VERIFICADO')
        self.assertEqual(value['socket_owner_uid'], 1001)
        self.assertEqual(calls, ['/run/user/1001', '/run/user/1001/porota-rc6', SOCKET])


if __name__ == '__main__':
    unittest.main()

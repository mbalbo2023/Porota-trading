"""Exact Git diff classification and the executed required-evidence branch."""
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


class PredeployImpactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'CI')
        self.git('config', 'user.email', 'ci@example.invalid')
        (self.root / 'docs').mkdir()
        (self.root / 'app.py').write_text('answer = 42\n')
        (self.root / 'README.md').write_text('# Porota\n')
        (self.root / 'docs/old.md').write_text('# Old documentation\n')
        self.base = self.commit()

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.root), *arguments],
                                       text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def classify(self, head=None, base=None):
        self.assertIsNotNone(importlib.util.find_spec('scripts.porota_predeploy_impact'),
                             'Exact diff classifier is missing')
        module = importlib.import_module('scripts.porota_predeploy_impact')
        return module.classify(self.root, base or self.base, head or self.git('rev-parse', 'HEAD'))

    def test_markdown_only_checks_changed_links_and_exact_source_identity(self):
        (self.root / 'docs/guide.md').write_text(
            'Guide\n=======\n[Code](../app.py) [Root](../README.md#porota) '
            '[Remote](https://example.invalid/source) [Repository](../) [Reference][source]\n'
            '[source]: ../app.py\n'
            '```markdown\n[Example only](not-a-file.md)\n```\n')
        head = self.commit()
        report = self.classify()
        self.assertEqual(report['classification'], 'DOCUMENTATION_ONLY')
        self.assertTrue(report['documentation_validated'])
        self.assertEqual((report['base_sha'], report['head_sha']), (self.base, head))
        self.assertEqual(report['head_tree'], self.git('rev-parse', 'HEAD^{tree}'))
        self.assertEqual(report['changed_paths'], ['docs/guide.md'])
        self.assertEqual(report['local_links_checked'], 4)

    def test_any_nonmarkdown_change_keeps_the_full_release_path(self):
        (self.root / 'README.md').write_text('# Updated\n')
        (self.root / 'app.py').write_text('answer = 43\n')
        self.commit()
        report = self.classify()
        self.assertEqual(report['classification'], 'APPLICATION_RELEASE')
        self.assertFalse(report['documentation_validated'])

    def test_renaming_code_to_markdown_cannot_hide_code_deletion(self):
        (self.root / 'app.py').rename(self.root / 'app.md')
        self.commit()
        report = self.classify()
        self.assertEqual(report['classification'], 'APPLICATION_RELEASE')
        self.assertIn('app.py', report['changed_paths'])

    def test_removed_markdown_is_classified_without_reading_absent_head_blob(self):
        (self.root / 'docs/old.md').unlink()
        self.commit()
        self.assertEqual(self.classify()['classification'], 'DOCUMENTATION_ONLY')

    def test_broken_changed_document_link_fails(self):
        (self.root / 'README.md').write_text('# Guide\n[Missing](docs/missing.md)\n')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'DOCUMENT_LINK_MISSING'):
            self.classify()

    def test_conflict_marker_or_non_utf8_markdown_fails(self):
        for value in (b'# Guide\n<<<<<<< HEAD\n', b'# Guide\n\xff'):
            with self.subTest(value=value):
                (self.root / 'README.md').write_bytes(value)
                self.commit()
                with self.assertRaises(ValueError):
                    self.classify()

    def test_symlink_named_markdown_cannot_take_documentation_path(self):
        (self.root / 'README.md').unlink()
        (self.root / 'README.md').symlink_to('app.py')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'DOCUMENT_NOT_REGULAR'):
            self.classify()

    def test_missing_base_wrong_head_and_dirty_checkout_fail_closed(self):
        (self.root / 'README.md').write_text('# Updated\n')
        self.commit()
        for base, head in (('0' * 40, None), (None, self.base)):
            with self.subTest(base=base, head=head), self.assertRaises(ValueError):
                self.classify(base=base, head=head)
        (self.root / 'app.py').write_text('uncommitted = True\n')
        with self.assertRaisesRegex(ValueError, 'CHECKOUT_DIRTY'):
            self.classify()

    def test_no_changes_cannot_claim_documentation_validation(self):
        with self.assertRaisesRegex(ValueError, 'IMPACT_EMPTY_DIFF'):
            self.classify()


class RequiredEvidenceBranchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / '.github/workflows/porota-predeploy-v2.yml'
        cls.workflow = yaml.safe_load(path.read_text())
        cls.command = cls.workflow['jobs']['release-evidence-required']['steps'][0]['run']

    def execute(self, **changes):
        environment = dict(os.environ, RESULT='success', SOURCE_SHA='a' * 40,
                           EXPECTED_SHA='a' * 40, BASE_SHA='b' * 40, EXPECTED_BASE_SHA='b' * 40,
                           CLASSIFICATION='DOCUMENTATION_ONLY', DOCUMENTATION_VALIDATED='true',
                           ARTIFACT_ID='', ARTIFACT_DIGEST='')
        environment.update(changes)
        return subprocess.run(['bash', '-c', self.command], env=environment,
                              capture_output=True, text=True, timeout=5)

    def test_documentation_success_does_not_claim_artifact_validation(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DOCUMENTATION_ONLY', result.stdout)
        self.assertNotIn('RELEASE_VALIDATED=', result.stdout)

    def test_unproven_documentation_or_unknown_classification_cannot_pass(self):
        for change in ({'DOCUMENTATION_VALIDATED': 'false'}, {'ARTIFACT_ID': '123'},
                       {'BASE_SHA': 'c' * 40}, {'CLASSIFICATION': ''}, {'RESULT': 'failure'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.execute(**change).returncode, 0)

    def test_application_requires_immutable_artifact_identity(self):
        release = {'CLASSIFICATION': 'APPLICATION_RELEASE', 'DOCUMENTATION_VALIDATED': 'false'}
        self.assertNotEqual(self.execute(**release).returncode, 0)
        result = self.execute(**release, ARTIFACT_ID='123', ARTIFACT_DIGEST='d' * 64)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RELEASE_VALIDATED=', result.stdout)

    def test_all_shell_blocks_parse(self):
        for job in self.workflow['jobs'].values():
            for step in job['steps']:
                if 'run' in step:
                    with self.subTest(step=step.get('name')):
                        result = subprocess.run(['bash', '-n'], input=step['run'], text=True,
                                                capture_output=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)

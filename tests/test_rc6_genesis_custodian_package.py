"""Exercise the exact executable bytes installed by the custodian bootstrap."""
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts import rc6_genesis_custodian_package as package

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    'rc6_genesis_custodian', 'rc6_genesis_host_identity', 'rc6_genesis_host_install',
    'rc6_genesis_host_transaction',
    'rc6_host_rootowned_installer_contract', 'rc6_oci_host_helper',
    'rc6_r304_root_install_manifest_freeze', 'rc6_r308_root_launch_profile_freeze',
    'rc6_r326_root_bundle_provenance', 'rc6_r334_root_env_genesis', 'rc6_r337_root_env_seal',
)


def checkout(root):
    scripts = root / 'scripts'
    scripts.mkdir()
    for name in SOURCES:
        shutil.copyfile(ROOT / 'scripts' / (name + '.py'), scripts / (name + '.py'))
    return scripts


class CustodianPackageTest(unittest.TestCase):
    def test_reproducible_exact_members_and_metadata(self):
        expected = package.build_custodian()
        with TemporaryDirectory() as directory:
            scripts = checkout(Path(directory))
            for source in scripts.iterdir():
                source.chmod(0o751)
                os.utime(source, (1, 1))
            (scripts / 'sitecustomize.py').write_text('raise RuntimeError("foreign")')
            (scripts / '__init__.py').write_text('raise RuntimeError("foreign")')
            (scripts.parent / '__main__.py').write_text('raise RuntimeError("foreign")')
            self.assertEqual(package.build_custodian(scripts.parent), expected)
        self.assertLessEqual(len(expected), 1024 * 1024)
        self.assertTrue(expected.startswith(b'#!/usr/bin/python3 -I\nPK'))
        with zipfile.ZipFile(io.BytesIO(expected)) as archive:
            names = ['scripts/' + name + '.py' for name in SOURCES]
            self.assertEqual(archive.namelist(), sorted(['__main__.py', 'scripts/__init__.py', *names]))
            self.assertEqual(archive.read('scripts/__init__.py'), b'')
            for path in names:
                self.assertEqual(archive.read(path), (ROOT / path).read_bytes())
            for entry in archive.infolist():
                self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(entry.create_system, 3)
                self.assertEqual(entry.external_attr >> 16, stat.S_IFREG | 0o644)
                self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                self.assertEqual((entry.extra, entry.comment, entry.flag_bits), (b'', b'', 0))

    def test_tampered_missing_or_redirected_source_is_rejected(self):
        with TemporaryDirectory() as directory:
            scripts = checkout(Path(directory))
            for name in SOURCES:
                source = scripts / (name + '.py')
                original = source.read_bytes()
                with self.subTest(module=name), self.assertRaises(package.PackageHold):
                    source.write_bytes(original + b'\n')
                    package.build_custodian(scripts.parent)
                source.write_bytes(original)
            source.unlink()
            with self.assertRaises(package.PackageHold):
                package.build_custodian(scripts.parent)
            outside = scripts.parent / 'outside.py'
            outside.write_bytes(original)
            source.symlink_to(outside)
            with self.assertRaises(package.PackageHold):
                package.build_custodian(scripts.parent)
            source.unlink()
            os.mkfifo(source)
            with self.assertRaises(package.PackageHold):
                package.build_custodian(scripts.parent)
            source.unlink()
            with source.open('wb') as handle:
                handle.truncate(1024 * 1024 + 1)
            with self.assertRaisesRegex(package.PackageHold, 'SOURCE_INVALID'):
                package.build_custodian(scripts.parent)
            source.write_bytes(original)
            scripts.rename(scripts.parent / 'foreign')
            scripts.symlink_to(scripts.parent / 'foreign', target_is_directory=True)
            with self.assertRaises(package.PackageHold):
                package.build_custodian(scripts.parent)

    def test_executable_uses_the_bundled_cli_under_isolation(self):
        raw = package.build_custodian()
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            app = folder / 'rc6-genesis-custodian'
            app.write_bytes(raw)
            app.chmod(0o555)
            marker = folder / 'injected'
            (folder / 'sitecustomize.py').write_text(
                'from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
            env = dict(os.environ, PYTHONPATH=str(folder))
            for command, verb, data in (
                    ([str(app)], '--unreviewed-verb', b''),
                    ([sys.executable, '-I', str(app)], 'apply', b'{"invalid":true}\n')):
                with self.subTest(executable=command[0], verb=verb):
                    run = subprocess.run([*command, verb], input=data, cwd=folder, env=env,
                                         capture_output=True, timeout=10)
                    self.assertEqual(run.returncode, 79, run.stderr)
                    self.assertNotIn(b'Traceback', run.stderr)
                    self.assertFalse(marker.exists())

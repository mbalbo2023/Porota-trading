import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import rc6_a159_stage_reclaim as cleanup


class StageReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.stage=root/'old-a138-stage'
        self.stage.mkdir()
        (self.stage/'frozen-file').write_bytes(b'old-candidate')
        self.raw=Path(__file__).parents[1].joinpath(
            'a138-host-control-plane-applied-from-action-log.json').read_bytes() if Path(__file__).parents[1].joinpath(
            'a138-host-control-plane-applied-from-action-log.json').exists() else b'X'*11851
        self.base=root/'private-forensics'
        self.archive=self.base/'receipt.json'
        self.quarantine=self.base/'quarantine-a138'
        self.patches=[patch.object(cleanup,'STAGE',self.stage),
                      patch.object(cleanup,'ARCHIVE_BASE',self.base),
                      patch.object(cleanup,'ARCHIVE',self.archive),
                      patch.object(cleanup,'QUARANTINE',self.quarantine),
                      patch.object(cleanup,'HOST_DEV',os.lstat(root).st_dev),
                      patch.object(cleanup,'STAGE_UID',os.getuid()),
                      patch.object(cleanup,'FORENSIC_UID',os.getuid()),
                      patch.object(cleanup,'STAGE_INODE',os.lstat(self.stage).st_ino),
                      patch.object(cleanup,'RECEIPT_HASH',hashlib.sha256(self.raw).hexdigest()),
                      patch.object(cleanup,'metadata',side_effect=['594077619','nyc1']),
                      patch.object(cleanup,'acquire_lock',side_effect=lambda:os.open('/dev/null',os.O_RDONLY)),
                      patch.object(cleanup,'barrier_safe',return_value='ABSENT'),
                      patch.object(cleanup,'container_snapshot',return_value={'observer':('id','image','running','0')}),
                      patch.object(cleanup,'open_refs',return_value={'pids':3,'fds':5,'stage_refs':0}),
                      patch.object(cleanup,'artifact_hashes',return_value={'frozen-file':'abc'}),
                      patch.object(cleanup,'stage_snapshot',return_value=({'stage_inode':os.lstat(self.stage).st_ino},self.raw))]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def run_cleanup(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            code=cleanup.execute()
        return code,out.getvalue()

    def test_archived_first_then_only_old_stage_reclaimed(self):
        untouched=self.stage.parent/'independent-data'
        untouched.write_text('preserve')
        code,out=self.run_cleanup()
        self.assertEqual(code,0,out)
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.quarantine.exists())
        self.assertEqual(self.archive.read_bytes(),self.raw)
        self.assertEqual(untouched.read_text(),'preserve')
        self.assertIn('"status": "GREEN"',out)

    def test_new_open_fd_fails_closed_without_archive_or_delete(self):
        with patch.object(cleanup,'open_refs',side_effect=RuntimeError('STAGE_OPEN_FD_RED')):
            code,out=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertTrue(self.stage.exists())
        self.assertFalse(self.base.exists())
        self.assertIn('STAGE_OPEN_FD_RED',out)

    def test_inflight_barrier_fails_before_archive(self):
        with patch.object(cleanup,'barrier_safe',side_effect=RuntimeError('BARRIER_ACTIVE')):
            code,_=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertTrue(self.stage.exists())
        self.assertFalse(self.base.exists())

    def test_after_archive_stage_drift_keeps_stage_and_receipt(self):
        initial=({'stage_inode':os.lstat(self.stage).st_ino},self.raw)
        with patch.object(cleanup,'stage_snapshot',side_effect=[initial,RuntimeError('UNKNOWN_MEMBER')]):
            code,out=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertTrue(self.stage.exists())
        self.assertEqual(self.archive.read_bytes(),self.raw)
        self.assertIn('UNKNOWN_MEMBER',out)

    def test_new_container_before_rename_keeps_stage(self):
        stable={'observer':('id','image','running','0')}
        changed={'observer':('different','image','running','0')}
        with patch.object(cleanup,'container_snapshot',side_effect=[stable,changed]):
            code,_=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertTrue(self.stage.exists())
        self.assertEqual(self.archive.read_bytes(),self.raw)

    def test_preexisting_quarantine_does_not_overwrite_or_delete(self):
        self.base.mkdir(mode=0o700)
        self.quarantine.mkdir()
        marker=self.quarantine/'foreign'
        marker.write_text('preserve')
        code,out=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertTrue(self.stage.exists())
        self.assertEqual(marker.read_text(),'preserve')
        self.assertIn('EXACT_QUARANTINE_PATH_EXISTS',out)

    def test_symlink_archive_does_not_overwrite_target_or_delete_stage(self):
        self.base.mkdir(mode=0o700)
        foreign=self.stage.parent/'foreign-receipt'
        foreign.write_text('preserve')
        self.archive.symlink_to(foreign)
        code,out=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertTrue(self.stage.exists())
        self.assertEqual(foreign.read_text(),'preserve')
        self.assertIn('ARCHIVED_RECEIPT_HASH_RED',out)

    def test_drift_after_private_move_keeps_quarantine_for_investigation(self):
        initial=({'stage_inode':os.lstat(self.stage).st_ino},self.raw)
        with patch.object(cleanup,'stage_snapshot',side_effect=[initial,initial,
                                                                  RuntimeError('STAGE_TREE_DRIFT')]):
            code,out=self.run_cleanup()
        self.assertEqual(code,2)
        self.assertFalse(self.stage.exists())
        self.assertTrue(self.quarantine.exists())
        self.assertEqual(self.archive.read_bytes(),self.raw)
        self.assertIn('STAGE_TREE_DRIFT',out)

    def test_staged_tree_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            external=base.parent/'outside'
            (base/'bad-link').symlink_to(external)
            with self.assertRaisesRegex(RuntimeError,'STAGE_TREE_ALIAS_RED'):
                cleanup.stage_tree(base,os.lstat(base).st_dev)
            (base/'bad-link').unlink()
            (base/'first').write_text('x')
            os.link(base/'first',base/'second')
            with self.assertRaisesRegex(RuntimeError,'STAGE_TREE_ALIAS_RED'):
                cleanup.stage_tree(base,os.lstat(base).st_dev)


if __name__=='__main__':unittest.main()

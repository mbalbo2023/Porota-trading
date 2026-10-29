"""A176 read-only A175 internals probe rejects wrong identity and tampering."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'scripts/rc6_a176_a175_internals_ro.py'
spec=importlib.util.spec_from_file_location('probe',PATH)
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class ProbeTests(unittest.TestCase):
    def test_wrong_droplet_before_lock(self):
        with patch.object(probe,'metadata',side_effect=['594077618','nyc1']),\
             patch.object(probe,'lock_existing',side_effect=AssertionError('LOCK_TOUCHED')):
            with self.assertRaisesRegex(ValueError,'DROPLET_IDENTITY_RED'):
                probe.run()

    def test_lock_existing_no_create_and_barrier_rejected(self):
        import fcntl
        import stat
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'lock';barrier=Path(d)/'barrier'
            lock.write_bytes(b'keep');lock.chmod(0o600)
            original_lstat=Path.lstat;original_fstat=os.fstat
            def owned(info):
                return types.SimpleNamespace(st_mode=info.st_mode,st_uid=0,
                    st_nlink=info.st_nlink,st_dev=info.st_dev,st_ino=info.st_ino)
            def lstat(path,*args,**kwargs):
                info=original_lstat(path,*args,**kwargs)
                return owned(info) if path in {lock,lock.parent} else info
            with patch.object(probe,'LOCK',lock),patch.object(probe,'BARRIER',barrier),\
                 patch.object(probe.os,'geteuid',return_value=0),\
                 patch.object(Path,'lstat',lstat),\
                 patch.object(probe.os,'fstat',side_effect=lambda fd:owned(original_fstat(fd))):
                fd=probe.lock_existing()
                rival=os.open(lock,os.O_RDONLY)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(rival,fcntl.LOCK_EX|fcntl.LOCK_NB)
                os.close(rival);os.close(fd)
                self.assertEqual(lock.read_bytes(),b'keep')
                barrier.write_text('{}')
                with self.assertRaisesRegex(ValueError,'BARRIER_PRESENT'):
                    probe.lock_existing()
                barrier.unlink();lock.unlink()
                with self.assertRaises(FileNotFoundError):probe.lock_existing()

    def test_sealed_rejects_duplicate_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'receipt';path.write_text('{"x":1,"x":2}')
            with self.assertRaisesRegex(ValueError,'DUPLICATE_KEY_RED'):
                probe.sealed(path,1024)
            path.write_text('{"x":1}');alias=path.with_name('alias');alias.symlink_to(path)
            with self.assertRaisesRegex(ValueError,'RECEIPT_PATH_RED'):
                probe.sealed(alias,1024)

    def test_no_host_mutator_in_source(self):
        text=PATH.read_text()
        self.assertNotIn("['docker','stop'",text)
        self.assertNotIn("['docker','start'",text)
        self.assertNotIn("['systemctl','start'",text)
        self.assertNotIn('os.replace(',text)

if __name__=='__main__':unittest.main()

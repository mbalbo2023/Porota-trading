import tempfile
import unittest
from pathlib import Path
import re
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rc6_679_immediate_backup_retire as m


class RetirementInventoryTest(unittest.TestCase):
    def test_exact_inventory_and_unknown_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backups = root / 'backups'
            backups.mkdir()
            for number in range(31):
                path = backups / f'backup-{number:02d}.gz'
                with path.open('wb') as out:
                    out.truncate(145_799_033)
            with patch.object(m, 'ROOT', root), patch.object(
                    m, 'ROOTS', ((backups, re.compile(r'backup-\d{2}\.gz')),)):
                rows = m.inventory()
                self.assertEqual(len(rows), 31)
                self.assertEqual(sum(row[3] for row in rows), 4_519_770_023)
                (backups / 'unexpected.db').touch()
                with self.assertRaisesRegex(RuntimeError, 'UNEXPECTED_BACKUP_ENTRY'):
                    m.inventory()

    def test_open_file_refuses_retirement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'backup'
            path.touch()
            proc = root / 'proc'
            fds = proc / '123' / 'fd'
            fds.mkdir(parents=True)
            (fds / '3').symlink_to(path)
            with patch.object(m, 'Path', lambda x: proc if x == '/proc' else Path(x)):
                with self.assertRaisesRegex(RuntimeError, 'BACKUP_FILE_OPEN'):
                    m.no_open_files({str(path)})


if __name__ == '__main__':
    unittest.main()

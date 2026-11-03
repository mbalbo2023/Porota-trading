"""Exercise the actual status writer with SQLite transactions and live FDs."""
import ast
from contextlib import closing
import gc
import logging
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "bv_paper_runtime.py"


def writer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name in {"_reader_status_write", "_sqlite_contention"}]
    assert len(functions) == 2
    namespace = {"sqlite3": sqlite3, "closing": closing,
                 "now_iso": lambda: "2026-10-12T21:00:00Z",
                 "LOG": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"),
         namespace)
    return namespace["_reader_status_write"]


def open_database_fds(path):
    target = str(path)
    count = 0
    for descriptor in Path("/proc/self/fd").iterdir():
        try:
            if Path("/proc/self/fd", descriptor.name).readlink().as_posix() == target:
                count += 1
        except FileNotFoundError:
            pass
    return count


class ReaderStatusConnectionLifecycle(unittest.TestCase):
    @unittest.skipUnless(Path("/proc/self/fd").exists(), "requires Linux procfs")
    def test_waiting_market_repeats_commit_and_close(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.db"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE paper_exit_reader_state (id INTEGER PRIMARY KEY, "
                    "stamp TEXT, state TEXT, detail TEXT)")
                connection.commit()
            baseline = open_database_fds(path)
            write = writer()
            for index in range(25):
                self.assertTrue(write(SimpleNamespace(path=path), "WAITING_MARKET", str(index)))
                self.assertEqual(open_database_fds(path), baseline)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT state,detail FROM paper_exit_reader_state").fetchone(),
                    ("WAITING_MARKET", "24"))

    @unittest.skipUnless(Path("/proc/self/fd").exists(), "requires Linux procfs")
    def test_failed_write_rolls_back_and_closes(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.db"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    "CREATE TABLE paper_exit_reader_state (id INTEGER PRIMARY KEY, "
                    "stamp TEXT, state TEXT, detail TEXT);"
                    "INSERT INTO paper_exit_reader_state VALUES (1,'old','OLD','old');"
                    "CREATE TRIGGER reject_status BEFORE INSERT ON paper_exit_reader_state "
                    "BEGIN SELECT RAISE(ABORT, 'forced'); END;")
                connection.commit()
            baseline = open_database_fds(path)
            with self.assertRaises(sqlite3.IntegrityError):
                writer()(SimpleNamespace(path=path), "WAITING_MARKET")
            self.assertEqual(open_database_fds(path), baseline)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT state FROM paper_exit_reader_state").fetchone(),
                    ("OLD",))


if __name__ == "__main__":
    unittest.main()

"""Exercise the Action's capture and fail-closed contract without a host."""

import ast
import datetime
import hashlib
import io
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import textwrap
from types import SimpleNamespace
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/rc6-retention-offhost-census.yml'
PRODUCT_SHA = '7e1e744d6ca9ab30c51b5747dd4467af5ece0145'
PRODUCT_TREE = '743f066f1e1cae196d0518f9f6633b18e33544bf'


def embedded(name):
    content = WORKFLOW.read_text(encoding='utf-8')
    start = "<<'" + name + "'\n"
    return textwrap.dedent(content.split(start, 1)[1].split('\n          ' + name, 1)[0])


def capture_functions(**overrides):
    tree = ast.parse(embedded('PY_CAPTURE'))
    funcs = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in {'check', 'inventory', 'sha_pass', 'preflight'}]
    namespace = {'datetime': datetime, 'hashlib': hashlib, 'os': os,
                 'Path': Path, 'shutil': shutil, 'sqlite3': sqlite3, 'stat': stat,
                 'MAX_BYTES': 1024**3, 'START': 0, 'LIMIT': 60}
    namespace.update(overrides)
    exec(compile(ast.Module(body=funcs, type_ignores=[]), '<workflow-capture>', 'exec'),
         namespace)
    namespace['check'] = lambda: None  # Synthetic fixture has no host resource probe.
    return namespace


class RetentionOffhostActionTest(unittest.TestCase):
    def test_only_manual_main_and_exact_product_code_before_host_credentials(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        trigger = content.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(trigger.strip(), 'workflow_dispatch:')
        self.assertIn("github.ref == 'refs/heads/main'", content)
        self.assertIn('contents: read', content)
        self.assertIn('cancel-in-progress: false', content)
        self.assertIn('timeout-minutes: 10', content)
        self.assertIn('ref: ' + PRODUCT_SHA, content)
        self.assertIn('RC6_SOURCE_TREE: ' + PRODUCT_TREE, content)
        self.assertLess(content.index('Verify exact source SHA, tree'),
                        content.index('Bounded quiescent read'))
        self.assertIn('test "$(git rev-parse HEAD)" = "$RC6_SOURCE_SHA"', content)
        self.assertIn('test "$(git rev-parse HEAD^{tree})" = "$RC6_SOURCE_TREE"', content)
        self.assertIn('nice -n 19 ionice -c 3 python3 -', content)
        self.assertNotIn('docker ', content)
        self.assertNotIn('systemctl start', content)
        self.assertNotIn('systemctl stop', content)
        self.assertNotIn('VACUUM', content)
        self.assertNotRegex(content, r'\bDELETE\s+FROM\b')
        self.assertIn('host_purge_allowed\': False', content)
        self.assertIn('source_mutated\': False', content)
        self.assertIn('real_orders_sent_by_action\': 0', content)

    def test_only_sanitized_receipt_is_uploaded_and_private_image_is_removed(self):
        content = WORKFLOW.read_text(encoding='utf-8')
        publish = content.split('name: Publish only the sanitized HOLD receipt', 1)[1]
        publish = publish.split('name: Destroy private runner copy', 1)[0]
        self.assertIn('path: ${{ runner.temp }}/rc6-retention-sanitized.json', publish)
        self.assertNotIn('.private.sqlite', publish)
        self.assertNotIn('.private.log', publish)
        self.assertIn('shred -u "$path"', content)
        self.assertIn('rc6-retention-source.private.sqlite', content)
        ast.parse(embedded('PY_CAPTURE'))
        ast.parse(embedded('PY_CAPTURE_STATUS'))
        ast.parse(embedded('PY_CENSUS'))

    def test_capture_rejects_wal_alias_and_changed_source_and_hashes_exact_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'history.sqlite'
            source.write_bytes(b'R' * 4096)
            ns = capture_functions(SOURCE=source)
            before = ns['inventory']()
            out = io.BytesIO()
            digest = ns['sha_pass'](before, out)
            self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(out.getvalue(), source.read_bytes())
            self.assertEqual(ns['sha_pass'](before), digest)
            source.write_bytes(b'changed' * 1024)
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'):
                ns['sha_pass'](before)
            Path(str(source) + '-wal').write_bytes(b'active')
            with self.assertRaisesRegex(ValueError, 'SOURCE_NOT_QUIESCENT'):
                ns['inventory']()
            Path(str(source) + '-wal').unlink()
            alias = Path(root) / 'alias.sqlite'
            alias.symlink_to(source)
            ns['SOURCE'] = alias
            with self.assertRaisesRegex(ValueError, 'SOURCE_ALIAS'):
                ns['inventory']()

    def test_runtime_preflight_fails_closed_on_orders_timer_writer_or_stale_heartbeat(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        state = {'row': ('PRODUCTION_PAPER', 0, 'MARKET_CLOSED', now.isoformat()),
                 'timer': 'ActiveState=inactive\nUnitFileState=masked\n',
                 'writer': 'inactive\n'}

        class DB:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, *_):
                return self

            def fetchone(self):
                return state['row']

        def run(args, **_):
            return SimpleNamespace(returncode=0, stdout=(state['writer'] if
                args[1] == 'is-active' else state['timer']))

        ns = capture_functions(
            subprocess=SimpleNamespace(run=run),
            sqlite3=SimpleNamespace(connect=lambda *a, **k: DB()),
            shutil=SimpleNamespace(disk_usage=lambda _: SimpleNamespace(free=5 * 1024**3)))
        self.assertIsInstance(ns['preflight'](), datetime.datetime)
        state['row'] = ('PRODUCTION_PAPER', 1, 'MARKET_CLOSED', now.isoformat())
        with self.assertRaisesRegex(ValueError, 'CLOSED_PAPER_REQUIRED'):
            ns['preflight']()
        state['row'] = ('PRODUCTION_PAPER', 0, 'MARKET_CLOSED', now.isoformat())
        state['timer'] = 'ActiveState=active\nUnitFileState=enabled\n'
        with self.assertRaisesRegex(ValueError, 'A3_QUARANTINE_REQUIRED'):
            ns['preflight']()
        state['timer'] = 'ActiveState=inactive\nUnitFileState=masked\n'
        state['writer'] = 'active\n'
        with self.assertRaisesRegex(ValueError, 'HISTORY_WRITER_ACTIVE'):
            ns['preflight']()
        state['writer'] = 'inactive\n'
        state['row'] = ('PRODUCTION_PAPER', 0, 'MARKET_CLOSED',
                        (now - datetime.timedelta(minutes=5)).isoformat())
        with self.assertRaisesRegex(ValueError, 'HEARTBEAT_REQUIRED'):
            ns['preflight']()

    def test_offhost_summary_never_promotes_age_to_purge(self):
        script = embedded('PY_CENSUS')
        self.assertIn('from rc6_data_retention import ART, daily_start', script)
        self.assertIn('report = census(source, capture, runner_root=root, seconds=240)', script)
        self.assertIn("'?mode=ro&immutable=1'", script)
        self.assertIn('date.fromisoformat(raw)', script)
        self.assertIn("'older_review_only'", script)
        self.assertIn("'version_ids_safe_to_purge': 0", script)
        self.assertIn("'identity_pit_per_row_verified': False", script)
        self.assertIn("'cross_store_and_artifact_pins_verified': False", script)
        self.assertIn("'host_purge_allowed': False", script)

    def test_365_date_boundary_future_and_invalid_are_review_only(self):
        tree = ast.parse(embedded('PY_CENSUS'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'classify_day')
        namespace = {'datetime': datetime}
        exec(compile(ast.Module(body=[function], type_ignores=[]),
                     '<workflow-date-classifier>', 'exec'), namespace)
        classify = namespace['classify_day']
        cutoff = datetime.date(2025, 11, 2)  # 365 ART dates through 01-Nov-2026.
        today = datetime.date(2026, 11, 1)
        self.assertEqual(classify('2025-11-01', cutoff, today), 'older_review_only')
        self.assertEqual(classify('2025-11-02', cutoff, today), 'at_cutoff_held')
        self.assertEqual(classify('2026-10-30', cutoff, today), 'newer_held')
        self.assertEqual(classify('2026-11-02', cutoff, today), 'future_held')
        self.assertEqual(classify('2025-1-2', cutoff, today), 'unclassifiable_held')
        self.assertEqual(classify(None, cutoff, today), 'unclassifiable_held')


if __name__ == '__main__':
    unittest.main()

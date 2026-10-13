"""The governed scope must use the reviewed product interpreter."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import rc6_material_carrier as carrier


def _source(tmp_path):
    source=tmp_path/'source';scripts=source/'scripts';scripts.mkdir(parents=True)
    shutil.copyfile(Path(carrier.__file__).with_name('rc6_material_static_scope.py'),
                    scripts/'rc6_material_static_scope.py')
    (scripts/'rc6_controlled_governed_runner.py').write_text(
        'def approved_scope(root, sha):\n'
        '    import yaml\n'
        '    assert yaml.safe_load("ready: true")["ready"] is True\n'
        '    return ["."], ["original-exclusion"], '
        '{"candidate_sha": sha, "candidate_tree": "b" * 40, '
        '"software_status": "INVENTORY_NOT_EXECUTED"}\n')
    return source


class ReviewedScopeInterpreterTests(unittest.TestCase):
    def test_scope_imports_product_dependency_only_in_reviewed_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path=Path(temporary)
            source=_source(tmp_path);output=tmp_path/'output';output.mkdir()
            args=SimpleNamespace(repo_root=source,source_sha='a'*40,source_tree='b'*40)
            observed=[]
            def managed(command,*,cwd,label,limit):
                observed.append((command,cwd,label,limit))
                process=subprocess.run(command,cwd=cwd,capture_output=True,text=True,timeout=30)
                self.assertEqual(process.returncode,0,process.stderr)
                return {'returncode':process.returncode}
            # The carrier interpreter never imports the governed module or PyYAML.
            with patch.object(carrier.runpy,'run_path',side_effect=AssertionError('HOST_IMPORT_FORBIDDEN')):
                arguments,exclusions,inventory=carrier.reviewed_approved_scope(
                    args,managed,output,{'311':sys.executable})
            self.assertEqual((arguments,exclusions),(['.'],['original-exclusion']))
            self.assertEqual(inventory['candidate_sha'],args.source_sha)
            command,cwd,label,limit=observed[0]
            self.assertEqual(command[:3],[sys.executable,'-I','-B'])
            self.assertEqual((cwd,label,limit),(output,'G0-reviewed-scope',1800))
            self.assertEqual(json.loads((output/'G0.reviewed-scope.json').read_bytes())['source_sha'],
                             args.source_sha)

    def test_scope_rejects_child_source_rebinding(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path=Path(temporary)
            source=_source(tmp_path);output=tmp_path/'output';output.mkdir()
            args=SimpleNamespace(repo_root=source,source_sha='a'*40,source_tree='b'*40)
            def tampered(command,*,cwd,label,limit):
                Path(command[-1]).write_text(json.dumps({'source_sha':'c'*40,
                    'arguments':['.'],'exclusions':[],'inventory':{}}))
                return {'returncode':0}
            with self.assertRaisesRegex(ValueError,'G0_REVIEWED_SCOPE_RESULT_INVALID'):
                carrier.reviewed_approved_scope(args,tampered,output,{'311':sys.executable})

    def test_scope_rejects_inventory_tree_rebinding(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path=Path(temporary)
            source=_source(tmp_path);output=tmp_path/'output';output.mkdir()
            args=SimpleNamespace(repo_root=source,source_sha='a'*40,source_tree='b'*40)
            def tampered(command,*,cwd,label,limit):
                Path(command[-1]).write_text(json.dumps({'source_sha':'a'*40,
                    'arguments':['.'],'exclusions':[],
                    'inventory':{'candidate_sha':'a'*40,'candidate_tree':'c'*40}}))
                return {'returncode':0}
            with self.assertRaisesRegex(ValueError,'G0_REVIEWED_SCOPE_SOURCE_REBOUND'):
                carrier.reviewed_approved_scope(args,tampered,output,{'311':sys.executable})

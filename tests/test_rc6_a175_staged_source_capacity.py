"""The grown A175 helper must actually pass both preinstall source gates."""

import base64
import ast
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as scheduler


def test_frozen_a175_quiescence_fits_scheduler_and_inline_classifier(monkeypatch):
    source = Path('scripts/rc6_deploy_quiescence.py').read_bytes()
    files = ('cb_caucion_audit.py', 'scripts/rc6_deploy_scheduler_suspend.py',
             'scripts/rc6_deploy_quiescence.py')
    encoded = {path: base64.b64encode(gzip.compress(Path(path).read_bytes(), mtime=0)).decode()
               for path in files}
    digests = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in files}

    monkeypatch.setenv('RC6_A145_QUIESCENCE_GZIP_B64', encoded[files[2]])
    staged = scheduler.a143_staged_module()
    assert staged.A174_FAILED_CANDIDATE == q.A174_FAILED_CANDIDATE

    workflow = yaml.safe_load(Path('.github/workflows/porota-deploy-v2-promote.yml').read_text())
    step = next(row for row in workflow['jobs']['promote']['steps']
                if row['name'] == 'Classify current flat PAPER session before host writes')
    body = step['run']
    start = body.index('printf "CANDIDATE_AUDIT_GZIP_B64=')
    end = body.index('\n} | timeout 45 ssh', start)
    actual_stdin = body[start:end]
    process = subprocess.run(['bash', '-euo', 'pipefail', '-c', actual_stdin],
                             env=dict(os.environ,
                                      CANDIDATE_AUDIT_GZIP_B64=encoded[files[0]],
                                      CANDIDATE_A125_B64=encoded[files[1]],
                                      CANDIDATE_A143_B64=encoded[files[2]]),
                             text=True, capture_output=True, check=True)
    payload = process.stdout
    assert sum(map(len, encoded.values())) < 160000
    tree = ast.parse(payload)
    # Execute exactly the source prelude and three real bounded verifiers,
    # excluding the host inventory and admission code after them.
    subset = tree.body[:next(i for i, node in enumerate(tree.body)
                             if isinstance(node, ast.Expr) and
                             isinstance(node.value, ast.Call) and
                             getattr(node.value.func, 'attr', None) == 'insert')]
    argv = ['', 'a' * 40, digests[files[1]], digests[files[2]], digests[files[0]]]
    namespace = {'sys': SimpleNamespace(argv=argv),
                 'A143_PENDING_IMAGE_ID': q.A143_PENDING_IMAGE_ID}
    exec(compile(ast.Module(body=subset, type_ignores=[]),
                 'candidate/inline-classifier.py', 'exec'), namespace)
    namespace['sys'] = SimpleNamespace(argv=argv)
    assert namespace['staged_audit_source']() == Path(files[0]).read_bytes()
    assert namespace['staged_operator_source']() == Path(files[1]).read_bytes()
    candidate = namespace['staged_a143_candidate']()
    assert candidate.A174_FAILED_CANDIDATE == q.A174_FAILED_CANDIDATE
    assert len(source) < 260000

    for name, index in (('staged_audit_source', 4),
                        ('staged_operator_source', 2),
                        ('staged_a143_candidate', 3)):
        tampered = list(argv)
        tampered[index] = '0' * 64
        namespace['sys'].argv = tampered
        with pytest.raises(SystemExit, match='SOURCE_HASH_RED'):
            namespace[name]()

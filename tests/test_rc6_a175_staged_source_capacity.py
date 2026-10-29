"""The grown A175 helper must actually pass both preinstall source gates."""

import base64
import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace
import types
import zlib

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_deploy_scheduler_suspend as scheduler


def test_frozen_a175_quiescence_fits_scheduler_and_inline_classifier(monkeypatch):
    source = Path('scripts/rc6_deploy_quiescence.py').read_bytes()
    compressed = gzip.compress(source, mtime=0)
    encoded = base64.b64encode(compressed).decode()
    digest = hashlib.sha256(source).hexdigest()

    monkeypatch.setenv('RC6_A145_QUIESCENCE_GZIP_B64', encoded)
    staged = scheduler.a143_staged_module()
    assert staged.A174_FAILED_CANDIDATE == q.A174_FAILED_CANDIDATE

    workflow = Path('.github/workflows/porota-deploy-v2-promote.yml').read_text()
    body = workflow.split('          def staged_a143_candidate():\n', 1)[1]
    body = body.split("          sys.path.insert(0,'/opt/porota-trading')", 1)[0]
    program = 'def staged_a143_candidate():\n' + body
    argv = ['', '', '', '', encoded, '', digest]
    namespace = {'base64': base64, 'zlib': zlib, 'hashlib': hashlib,
                 'types': types, 'sys': SimpleNamespace(argv=argv),
                 'A143_PENDING_IMAGE_ID': q.A143_PENDING_IMAGE_ID}
    exec(compile(program, 'candidate/inline-classifier.py', 'exec'), namespace)
    candidate = namespace['staged_a143_candidate']()
    assert candidate.A174_FAILED_CANDIDATE == q.A174_FAILED_CANDIDATE
    assert len(source) < 210000

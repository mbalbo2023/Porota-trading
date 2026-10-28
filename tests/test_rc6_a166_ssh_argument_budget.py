"""Regression for Deploy #40737073432: E2BIG before host classification."""
import base64
import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


budget = load('rc6_deploy_ssh_arg_budget')
quiescence = load('rc6_deploy_quiescence')


def test_exact_sources_fit_with_headroom_and_old_raw_wire_fails():
    sizes = budget.source_sizes()
    observed = budget.require_budget(sizes)
    assert observed['classify'] <= budget.MAX_REMOTE_ARG_BYTES
    assert max(observed.values()) == observed['classify']
    raw = budget.encoded_size('scripts/rc6_deploy_scheduler_suspend.py')
    assert raw > 90000 and sizes['a125_gzip'] < 20000
    with pytest.raises(ValueError, match='SSH_REMOTE_SINGLE_ARGUMENT_TOO_LARGE'):
        budget.require_budget({**sizes, 'a125_gzip': raw})


def test_future_growth_fails_before_host_contact():
    sizes = budget.source_sizes()
    room = budget.MAX_REMOTE_ARG_BYTES - budget.budgets(sizes)['classify']
    with pytest.raises(ValueError, match='SSH_REMOTE_SINGLE_ARGUMENT_TOO_LARGE'):
        budget.require_budget({**sizes, 'a125_gzip': sizes['a125_gzip'] + room + 1})
    with pytest.raises(ValueError, match='SSH_ARG_SOURCE_INVENTORY_RED'):
        budget.require_budget({**sizes, 'mutex': 0})


def test_staged_operator_decompresses_bounded_exact_source():
    source = b"MARKER = 'EXACT_CANDIDATE_SOURCE'\n"
    encoded = base64.b64encode(gzip.compress(source, mtime=0)).decode('ascii')
    digest = hashlib.sha256(source).hexdigest()
    variables = {'RC6_A125_CANDIDATE_SOURCE_GZIP_B64': encoded,
                 'RC6_A125_CANDIDATE_SOURCE_SHA256': digest}
    with patch.dict(os.environ, variables, clear=True):
        assert quiescence.candidate_operator_module(require_staged=True).MARKER == \
            'EXACT_CANDIDATE_SOURCE'
    with patch.dict(os.environ, {**variables,
                                  'RC6_A125_CANDIDATE_SOURCE_SHA256': '0'*64}, clear=True):
        with pytest.raises(ValueError, match='QUIESCENCE_OPERATOR_SOURCE_INVALID'):
            quiescence.candidate_operator_module(require_staged=True)
    with patch.dict(os.environ, {**variables,
                                  'RC6_A125_CANDIDATE_SOURCE_B64': encoded}, clear=True):
        with pytest.raises(ValueError, match='QUIESCENCE_OPERATOR_SOURCE_AMBIGUOUS'):
            quiescence.candidate_operator_module(require_staged=True)


def test_bomb_and_corrupt_gzip_cannot_execute():
    source = b'Z = 1\n' * 20000
    encoded = base64.b64encode(gzip.compress(source, mtime=0)).decode('ascii')
    variables = {'RC6_A125_CANDIDATE_SOURCE_GZIP_B64': encoded,
                 'RC6_A125_CANDIDATE_SOURCE_SHA256': hashlib.sha256(source).hexdigest()}
    with patch.dict(os.environ, variables, clear=True):
        with pytest.raises(ValueError, match='QUIESCENCE_OPERATOR_SOURCE_INVALID'):
            quiescence.candidate_operator_module(require_staged=True)
    with patch.dict(os.environ, {**variables,
                                  'RC6_A125_CANDIDATE_SOURCE_GZIP_B64':
                                  base64.b64encode(b'not a gzip stream').decode('ascii')},
                    clear=True):
        with pytest.raises(ValueError, match='QUIESCENCE_OPERATOR_SOURCE_INVALID'):
            quiescence.candidate_operator_module(require_staged=True)


def test_workflow_bounds_both_preinstall_and_drain_routes():
    workflow = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert 'Bound source-bearing SSH arguments before host contact' in workflow
    assert 'RC6_CLASSIFY_REMOTE_ARGUMENT=RED' in workflow
    assert 'RC6_DRAIN_REMOTE_ARGUMENT=RED' in workflow
    assert workflow.count('gzip -9 -n -c < scripts/rc6_deploy_scheduler_suspend.py') >= 2
    assert "RC6_A125_CANDIDATE_SOURCE_GZIP_B64=\"$A125_CANDIDATE_SOURCE_GZIP_B64\"" in workflow
    assert "RC6_A125_CANDIDATE_SOURCE_SHA256=\"$A125_CANDIDATE_SOURCE_SHA256\"" in workflow
    assert "sys.argv[5]" in workflow and 'staged_operator_source()' in workflow

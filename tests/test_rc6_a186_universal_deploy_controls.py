"""A186 regression: provider outages alarm; failed deployments reclaim only safe orphans."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import textwrap

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml'


def steps():
    return yaml.safe_load(WORKFLOW.read_text())['jobs']['promote']['steps']


def promotion_run():
    return next(step['run'] for step in steps() if step.get('id') == 'promotion')


def candidate_program():
    body = promotion_run().split('CANDIDATE_READY="$(sudo -n timeout 10 python3', 1)[1]
    return compile(textwrap.dedent(body.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]),
                   'a186-candidate-admission.py', 'exec')


def candidate_result(tmp_path, monkeypatch, *, auth='OK', mode='PRODUCTION_PAPER',
                     real_orders=0, ready=True, missing_table=False):
    db = tmp_path / 'observer.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,ppi_auth TEXT)')
        conn.execute('INSERT INTO observer_state VALUES (1,?,?,?)', (mode, real_orders, auth))
        if not missing_table:
            conn.execute('CREATE TABLE candidate_identity_v2(status TEXT,can_simulate INTEGER,instrument_type TEXT)')
            if ready:
                conn.execute("INSERT INTO candidate_identity_v2 VALUES('AVAILABLE',1,'ACCIONES')")
    monkeypatch.setattr(sys, 'argv', ['a186-candidate-admission.py', str(db)])
    out = StringIO()
    with redirect_stdout(out):
        try:
            exec(candidate_program(), {})
        except SystemExit as error:
            return error.code, out.getvalue().strip()
    return 0, out.getvalue().strip()


@pytest.mark.parametrize('auth,ready,expected', [
    ('OK', True, 'GREEN'),
    ('ERROR', True, 'AMBER|PPI_AUTH_UNAVAILABLE'),
    ('COOLDOWN', True, 'AMBER|PPI_AUTH_UNAVAILABLE'),
    ('NOT_ATTEMPTED', True, 'AMBER|PPI_AUTH_UNAVAILABLE'),
    ('OK', False, 'AMBER|PAPER_CANDIDATES_UNAVAILABLE'),
])
def test_external_unavailable_never_vetoes_technical_candidate_gate(
        tmp_path, monkeypatch, auth, ready, expected):
    assert candidate_result(tmp_path, monkeypatch, auth=auth, ready=ready) == (0, expected)


@pytest.mark.parametrize('mutation', [
    {'mode': 'LIVE'}, {'real_orders': 1}, {'real_orders': None},
    {'auth': 'UNCLASSIFIED'}, {'missing_table': True},
])
def test_canonical_paper_and_schema_safety_remains_blocking(tmp_path, monkeypatch, mutation):
    code, value = candidate_result(tmp_path, monkeypatch, **mutation)
    assert code == 2 and value.startswith('RED|')


def test_zero_ready_is_technical_alarm_not_a_20_minute_wait():
    block = promotion_run()
    assert 'RC6_PREOPEN_CANDIDATE_ALERT=$CANDIDATE_READY' in block
    assert 'RC6_PREOPEN_READY=2' in block
    assert 'RC6_PREOPEN_CANDIDATE_WAIT=AMBER' in block
    assert 'PAPER_INSTRUMENTS_FAIL_CLOSED' in block


@pytest.mark.parametrize('state,returncode,expected', [
    (1, 0, 'RC6_PREOPEN_CANDIDATE_WAIT=GREEN'),
    (2, 0, 'RC6_PREOPEN_CANDIDATE_WAIT=AMBER|SOURCE_UNAVAILABLE|PAPER_READINESS_FAIL_CLOSED'),
    (0, 42, 'RC6_PREOPEN_CANDIDATE_WAIT=RED|TIMEOUT_20M'),
    (3, 43, 'RC6_PREOPEN_CANDIDATE_WAIT=RED|UNCLASSIFIED_STATE'),
])
def test_actual_deploy_shell_finalizer_keeps_amber_nonblocking(state, returncode, expected):
    """Execute the real workflow branch: merely printing AMBER is not a guard.

    A186's first regression checked only the probe and the marker text. The
    later `-ne 1` finalizer still converted AMBER(2) to exit 42. This test
    executes the exact shell fragment to guard that self-induced incident.
    """
    match = re.search(r'case "\$RC6_PREOPEN_READY" in\n.*?\n\s*esac',
                      promotion_run(), flags=re.DOTALL)
    assert match, 'Preopen classification finalizer not found in real workflow'
    command = f'set -Eeuo pipefail\nRC6_PREOPEN_READY={state}\n{match.group(0)}\n'
    result = subprocess.run(['bash', '-c', command], capture_output=True, text=True,
                            check=False)
    assert result.returncode == returncode, result.stderr + result.stdout
    assert expected in result.stdout
    assert ('RC6_PREOPEN_CANDIDATE_WAIT=GREEN' in result.stdout) == (state == 1)


def test_byma_refresh_is_owned_by_external_producer_not_deploy():
    block = promotion_run()
    assert 'RC6_BYMA_MORNING_REFRESH=AMBER' in block
    assert 'PROVIDER_REFRESH_DEFERRED_TO_INDEPENDENT_TIMER' in block
    assert 'python3 "$REPO/scripts/rc6_byma_morning_pipeline.py"' not in block


def test_failed_deploy_always_attempts_exact_orphan_gc_after_safe_recovery():
    rows = steps()
    recovery = next(row for row in rows if row.get('id') == 'failure_cleanup')
    garbage = next(row for row in rows if row.get('id') == 'failure_orphan_cleanup')
    assert rows.index(garbage) > rows.index(recovery)
    assert "steps.failure_cleanup.outcome == 'success'" in str(garbage.get('if'))
    block = garbage['run']
    assert 'rc6-unified-paper-deploy' not in block  # workflow's own mutex already owns the group
    assert '/run/lock/porota-rc6-deploy-v2.lock' in block
    assert 'test ! -e /run/lock/porota-rc6-deploy-v2-attempt.json' in block
    assert '< scripts/rc6_pretransfer_image_cleanup.py' in block
    assert "proof.get('runtime_unchanged') is True" in block
    assert 'docker image prune' not in block and 'docker system prune' not in block
    assert subprocess.run(['bash', '-n'], input=block, capture_output=True, text=True).returncode == 0
    assert subprocess.run(['bash', '-n'], input=recovery['run'], capture_output=True, text=True).returncode == 0


def test_deploy_mutex_group_remains_global_and_never_cancels_in_progress():
    raw = yaml.safe_load(WORKFLOW.read_text())
    assert raw['concurrency']['group'] == 'rc6-unified-paper-deploy'
    assert raw['concurrency']['cancel-in-progress'] is False


def test_external_source_and_failed_attempt_cleanup_are_durable_repo_policies():
    root = WORKFLOW.parents[2]
    policy = yaml.safe_load((root / 'ops/policy/porota-policy.yaml').read_text())
    deploy = policy['deployment']
    assert deploy['external_market_source_connectivity_veto_allowed'] is False
    assert deploy['external_market_source_freshness_veto_allowed'] is False
    assert deploy['external_market_source_unavailability_effect'] == 'AMBER_ALERT_ONLY'
    assert set(('PPI', 'IOL', 'BYMA', 'A3', 'ROFEX')).issubset(deploy['external_market_sources'])
    assert deploy['external_source_contract_corruption_remains_red'] is True
    assert deploy['paper_instrument_readiness_on_source_failure'] == 'FAIL_CLOSED'
    cleanup = policy['cleanup']
    assert cleanup['failed_deploy_reference_safe_orphan_gc_required_when_runtime_safe'] is True
    assert cleanup['residual_orphans_must_be_audited_and_cleaned_before_next_deploy'] is True
    assert cleanup['docker_prune_or_force_delete_allowed'] is False
    agents = (root / 'AGENTS.md').read_text()
    assert 'External **market-data/provider** outages' in agents
    assert 'Before the *next* Deploy V2' in agents


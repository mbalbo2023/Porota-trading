"""Cheap physical counterexamples for premature Horizon admission."""
import sys

import pytest

from scripts import rc6_architectural_gates as gates
from scripts import rc6_material_carrier as carrier
from scripts import rc6_material_pr_admission as admission


REASON = 'HORIZON_ALL1202_PHYSICAL_MODEL_NO_VERIFICADO_BEFORE_MATERIAL'


@pytest.mark.parametrize('gate', ['Horizon', 'G5'])
def test_partial_closed_samples_cannot_admit_the_original_horizon(gate):
    with pytest.raises(ValueError, match=REASON):
        gates.require_horizon_model_before_material(gate)


def test_a_caller_cannot_rewrite_the_model_hold_in_place():
    with pytest.raises(TypeError):
        gates.HORIZON_MODEL_CLOSURE['global_retained_footprint_proved'] = True


@pytest.mark.parametrize('name,value', [
    ('HORIZON_MODEL_CLOSED', 'true'),
    ('HORIZON_CAPACITY_PROOF_SHA256', 'a' * 64),
    ('HORIZON_CAPACITY_FORECAST_BYTES', '499376128'),
    ('HORIZON_ALL1202_PROVED', 'true'),
    ('RC6_MATERIAL_AUTOMATIC_PR_AUTHORIZATION', 'APPROVED'),
    ('GATES_AUTHORIZED', 'Horizon'),
])
def test_environment_or_a_partial_forecast_does_not_authorize_horizon(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=REASON):
        gates.require_horizon_model_before_material('Horizon')


def test_admission_stops_before_event_api_and_source_reads(monkeypatch, tmp_path):
    marker = tmp_path / 'expensive-admission-started'

    def forbidden(*args, **kwargs):
        marker.write_text('called')
        raise AssertionError('event/API/Source admission must not begin')

    for key, value in {
        'GITHUB_REPOSITORY': admission.REPO,
        'GITHUB_REPOSITORY_ID': str(admission.REPO_ID),
        'GITHUB_RUN_ATTEMPT': '1',
    }.items():
        monkeypatch.setenv(key, value)
    for name in ('actual_event', 'fresh_source', 'api'):
        monkeypatch.setattr(admission, name, forbidden)
    with pytest.raises(ValueError, match=REASON):
        admission.admit(source_sha='a' * 40, source_tree='b' * 40, gate='Horizon',
                        launch_receipt_url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
                        owner_session=admission.SUCCESSOR_OWNER)
    assert not marker.exists()


def test_direct_horizon_entry_cannot_export_or_launch_a_producer(monkeypatch, tmp_path):
    marker = tmp_path / 'producer-started'

    def forbidden(*args, **kwargs):
        marker.write_text('called')
        raise AssertionError('export/producer must not begin')

    monkeypatch.setattr(carrier, 'exported', forbidden)
    with pytest.raises(ValueError, match=REASON):
        carrier.horizon(None, forbidden, None, None, None)
    assert not marker.exists()


def test_cli_stops_before_bootstrap_fetch_namespace_or_tooling(monkeypatch, tmp_path):
    marker = tmp_path / 'bootstrap-started'

    def forbidden(*args, **kwargs):
        marker.write_text('called')
        raise AssertionError('bootstrap must not begin')

    monkeypatch.setattr(carrier, 'bootstrap', forbidden)
    monkeypatch.setattr(sys, 'argv', ['rc6_material_carrier.py', '--repo-root', str(tmp_path),
        '--source-sha', 'a' * 40, '--source-tree', 'b' * 40, '--launch-receipt-url',
        'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
        '--owner-session', admission.SUCCESSOR_OWNER, '--python311', sys.executable,
        '--python312', sys.executable, '--gate', 'Horizon', '--require-pr-admission'])
    with pytest.raises(ValueError, match=REASON):
        carrier.main()
    assert not marker.exists()

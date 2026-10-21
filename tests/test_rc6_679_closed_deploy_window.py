"""Intraday operator suspension preserves the exact one PAPER caucion."""

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml'


def test_deploy_requires_current_intraday_window_and_exact_caucion_admission():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert "local.date().isoformat() != '2026-10-21'" in source
    assert '13 <= local.hour < 17' in source
    assert 'RC6_INTRADAY_CAUCION_WINDOW=RED' in source
    assert '--action drain --market-open-suspend --intraday-caucion' in source
    assert '--action install --market-open-suspend --intraday-caucion' in source
    assert '--carry-open-paper-spots' not in source
    assert '--paused-postclose' not in source
    assert 'RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION' not in source


def test_deploy_checks_exact_intraday_caucion_after_restart():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'from scripts.rc6_deploy_quiescence import admit' in source
    assert 'assert admit(allow_market_open_suspend=True, allow_intraday_caucion=True) == \\"RUNNING\\"' in source
    assert 'RC6_INTRADAY_CAUCION_POSTSTART=GREEN' in source
    assert '--action verify-carryover' in source
    cleanup = source.split('      - name: Failure-safe host cleanup', 1)[1].split('      - name: Postdeploy full-session stress', 1)[0]
    assert "steps.flat_book.outcome == 'failure'" in cleanup
    assert "steps.backup_retire.outcome == 'failure'" in cleanup
    assert 'steps.carryover.' not in cleanup

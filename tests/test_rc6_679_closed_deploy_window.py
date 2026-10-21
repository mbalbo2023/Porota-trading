"""The one Deploy V2 route must match the current closed and flat PAPER book."""

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml'


def test_deploy_requires_current_closed_window_and_exact_flat_admission():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert "local.date().isoformat() != '2026-10-21'" in source
    assert '17 <= local.hour < 23' in source
    assert 'RC6_POSTCLOSE_WINDOW=RED' in source
    assert '--action drain"' in source
    assert '--action install\n' in source
    assert '--market-open-suspend' not in source
    assert '--carry-open-paper-spots' not in source
    assert '--paused-postclose' not in source
    assert 'RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION' not in source


def test_deploy_checks_exact_closed_flat_admission_after_restart():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'from scripts.rc6_deploy_quiescence import admit' in source
    assert 'assert admit() == \\"WAITING_MARKET\\"' in source
    assert 'RC6_CLOSED_FLAT_PAPER_POSTSTART=GREEN' in source
    assert '--action verify-carryover' not in source
    cleanup = source.split('      - name: Failure-safe host cleanup', 1)[1].split('      - name: Postdeploy full-session stress', 1)[0]
    assert "steps.flat_book.outcome == 'failure'" in cleanup
    assert "steps.backup_retire.outcome == 'failure'" in cleanup
    assert 'steps.carryover.' not in cleanup

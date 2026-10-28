"""Deploy selects live PAPER quiescence and one exact overnight receipt."""

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/porota-deploy-v2-promote.yml'


def test_deploy_classifies_live_session_and_rechecks_flat_paper_at_drain():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert "local.date().isoformat()" in source  # only the exact incident identity
    assert "('RUNNING','MARKET_OPEN')" in source
    assert "state[0]=='WAITING_MARKET'" in source
    assert "state[1] in ('CLOSED','MARKET_CLOSED')" in source
    assert "assert admit(allow_market_open_suspend=True)=='RUNNING'" in source
    assert "assert admit()=='WAITING_MARKET'" in source
    assert "case \"$RC6_QUIESCENCE_MODE\" in OPEN) FLAG='--market-open-suspend';; CLOSED) FLAG='';;" in source
    assert '--action drain $FLAG' in source
    assert '--action install $QUIESCENCE_FLAG' in source
    assert '--intraday-caucion' not in source
    assert '--carry-open-paper-spots' not in source
    assert '--paused-postclose' not in source
    assert "CLOSED_CAUCION) FLAG='--pending-paper-caucion'" in source
    assert "CLOSED_CAUCION) QUIESCENCE_FLAG='--pending-paper-caucion'" in source
    assert 'if [ "$RC6_QUIESCENCE_MODE" = CLOSED_CAUCION ]; then' in source
    assert 'AUDIT_ENV="RC6_CANDIDATE_CAUCION_AUDIT_B64=$(base64 -w0 cb_caucion_audit.py)"' in source
    assert 'RC6_CANDIDATE_CAUCION_AUDIT_B64=$(base64 -w0 cb_caucion_audit.py)' in source
    assert 'RC6_CANDIDATE_CAUCION_AUDIT_B64=$(base64 -w0 "$STAGE/cb_caucion_audit.py")' in source
    assert "allow_pending_caucion=bool(pending)" in source
    assert "print('CLOSED_CAUCION' if pending else 'CLOSED')" in source
    assert 'RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION' not in source


def test_deploy_checks_flat_paper_after_restart():
    source = WORKFLOW.read_text(encoding='utf-8')
    assert 'from scripts.rc6_deploy_quiescence import admit_poststart_flat' in source
    assert 'after=admit_poststart_flat(before,candidate)' in source
    assert 'RC6_PAPER_POSTSTART=GREEN|before=' in source
    assert "|after='+after+'|PRODUCTION_PAPER|0" in source
    assert '--action verify-carryover' in source
    cleanup = source.split('      - name: Failure-safe host cleanup', 1)[1].split('      - name: Postdeploy full-session stress', 1)[0]
    assert "steps.flat_book.outcome == 'failure'" in cleanup
    assert "steps.backup_retire.outcome == 'failure'" in cleanup
    assert 'steps.carryover.' not in cleanup

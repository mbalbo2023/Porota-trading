from pathlib import Path

from be_paper_engine import PaperBroker, PaperStore
from cf_sale_settlement import validated_sale_settlement


def test_isolated_broker_does_not_invent_daily_risk_policy(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / 'isolated.db')))
    assert broker.daily_risk is None
    assert broker.economics_mode == 'SHADOW'


def test_runtime_explicitly_builds_daily_risk_and_defaults_economics_to_shadow(tmp_path, monkeypatch):
    from bv_paper_runtime import broker_from_environment
    monkeypatch.delenv('PAPER_ECONOMIC_GATE_MODE', raising=False)
    monkeypatch.setenv('MAX_DAILY_LOSS_PCT', '2.5')
    monkeypatch.setenv('PAPER_DAILY_SOFT_STOP_PCT', '1.5')
    broker = broker_from_environment(PaperStore(str(tmp_path / 'runtime.db')),
                                     clock_fn=lambda: '2026-09-07T11:00:00-03:00',
                                     session_policy=None, require_supervisor=False)
    assert broker.economics_mode == 'SHADOW'
    assert broker.daily_risk is not None
    assert str(broker.daily_risk.limit_pct) == '2.5'
    assert str(broker.daily_risk.soft_limit_pct) == '1.5'


def test_t1_pending_confirmation_never_releases_cash_by_inferred_clock():
    traded = '2026-09-04T16:00:00-03:00'
    assert validated_sale_settlement('A-24HS', traded, None, 'PENDING_CONFIRMATION') is None
    # This is deliberately independent of how many days have elapsed: this
    # function validates evidence, not the current clock.
    assert validated_sale_settlement('T+1', traded, None, 'PENDING_CONFIRMATION') is None


def test_source_keeps_real_money_blocked():
    import _version
    assert _version.MODE == 'PRODUCTION_PAPER'
    assert _version.EXECUTION == 'SIMULATED'
    assert _version.REAL_ORDER_CAPABILITY == 'BLOCKED'

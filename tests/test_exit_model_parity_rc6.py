from decimal import Decimal

from be_paper_engine import PaperBroker, PaperStore
from bk_exit_model import PAPER_EXIT_MODEL, fixed_percent_barriers


def test_fixed_percent_barriers_match_rc6_paper_parameters():
    barriers = fixed_percent_barriers(
        Decimal("100.0000"), stop_loss_pct=Decimal("0.02"),
        target_gain_pct=Decimal("0.05"))
    assert barriers.model == PAPER_EXIT_MODEL
    assert barriers.stop_price == Decimal("98.000000")
    assert barriers.target_price == Decimal("105.000000")


def test_paper_broker_uses_canonical_model_and_rc6_defaults(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    assert broker.exit_model == PAPER_EXIT_MODEL
    barriers = fixed_percent_barriers(
        Decimal("100"), stop_loss_pct=broker.stop_loss_pct,
        target_gain_pct=broker.target_gain_pct)
    assert barriers.stop_price == Decimal("98.00")
    assert barriers.target_price == Decimal("105.00")


def test_legacy_backtest_parameters_are_paper_percentages(monkeypatch):
    monkeypatch.setenv("PAPER_STOP_LOSS_PCT", "0.02")
    monkeypatch.setenv("PAPER_TARGET_GAIN_PCT", "0.05")
    import importlib
    import q_backtest
    module = importlib.reload(q_backtest)
    barriers = fixed_percent_barriers(
        Decimal("100"), stop_loss_pct=module.PAPER_STOP_LOSS_PCT,
        target_gain_pct=module.PAPER_TARGET_GAIN_PCT)
    assert module.PAPER_STOP_LOSS_PCT == 0.02
    assert module.PAPER_TARGET_GAIN_PCT == 0.05
    assert barriers.model == PAPER_EXIT_MODEL


def test_candidate_atr_replay_is_explicitly_not_comparable_to_paper():
    from by_strategy_backtest import CANDIDATE_ATR_EXIT_MODEL
    assert CANDIDATE_ATR_EXIT_MODEL == (
        "CANDIDATE_ATR_EXIT_MODEL_DIFFERS_FROM_PAPER_FIXED_PERCENT_V1")

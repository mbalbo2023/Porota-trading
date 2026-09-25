from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import historical_candle_shadow_rc6 as shadow
import rc6_cost_settlement_takeprofit_shadow as costs


def test_valid_ohlc_rejects_inconsistent_bar():
    assert shadow._valid_ohlc({
        "openingPrice": "10", "max": "9", "min": "8", "price": "8.5"
    }) is None


def test_trend_requires_enough_observations():
    assert shadow._trend([Decimal("10")] * 19, 20) is None
    assert shadow._trend([Decimal("10")] * 20, 20) == Decimal("0")


def test_collect_is_shadow_only_when_store_is_unavailable():
    q = SimpleNamespace(symbol="GGAL", asset_class="ACCIONES",
                        market="BYMA", currency="ARS", settlement="A-24HS")
    result = shadow.collect(object(), q, datetime.now(timezone.utc))
    assert result["mode"] == "SHADOW"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["shadow_score_delta"] == "0"


def test_personal_tax_is_out_of_scope_without_explicit_rate(monkeypatch):
    monkeypatch.delenv("PAPER_GAIN_TAX_RATE", raising=False)
    result = costs.published_cost_diagnostic(Decimal("100"))
    assert result["state"] == "NOT_APPLICABLE"
    assert result["effect"] == "PERSONAL_TAXES_OUT_OF_SCOPE"


def test_personal_tax_environment_cannot_become_binding(monkeypatch):
    monkeypatch.setenv("PAPER_GAIN_TAX_RATE", "0.35")
    result = costs.published_cost_diagnostic(Decimal("100"))
    assert result["state"] == "NOT_APPLICABLE"
    assert result["amount"] == "0"
    assert result["effect"] == "PERSONAL_TAXES_OUT_OF_SCOPE"


def test_take_profit_diagnostic_does_not_change_execution():
    result = costs.net_take_profit("100", "105", "10", "1", "1", "1")
    assert result["effect"] == "SHADOW_ONLY"
    assert Decimal(result["net_pnl_after_published_costs"]) == Decimal("48")

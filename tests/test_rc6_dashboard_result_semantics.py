"""Signed PAPER results retain meaning across the canonical and classic dashboards."""
from rc6_trader_dashboard.components import Field, metric, value_html
from bg_paper_dashboard import _result_amount, _result_card_state


def test_signed_results_are_distinguishable_without_coloring_prices_or_missing_data():
    pnl = Field("net_pnl", "PnL neto", "money")
    assert "result-positive" in value_html({"net_pnl": "12.5", "currency": "ARS"}, pnl)
    assert "Resultado positivo" in value_html({"net_pnl": "12.5", "currency": "ARS"}, pnl)
    assert "result-negative" in value_html({"net_pnl": "-12.5", "currency": "ARS"}, pnl)
    assert "-12,50" in value_html({"net_pnl": "-12.5", "currency": "ARS"}, pnl)
    assert "result-neutral" in value_html({"net_pnl": "0", "currency": "ARS"}, pnl)
    assert "result-" not in value_html({"net_pnl": "bad", "currency": "ARS"}, pnl)
    assert "result-" not in value_html({"entry_price": "12.5", "currency": "ARS"},
                                        Field("entry_price", "Entrada", "money"))
    assert "result-negative" in metric("PnL neto hoy", "-5", kind="money", currency="ARS", result=True)
    assert "result-" not in metric("Caja", "-5", kind="money", currency="ARS")

    assert "class='positive'" in _result_amount("12.5", "ARS")
    assert "class='negative'" in _result_amount("-12.5", "ARS")
    assert "class='negative'" in _result_amount("12.5", "ARS", loss_magnitude=True)
    assert "class='neutral'" in _result_amount("0", "ARS")
    assert _result_amount(None, "ARS") == "—"
    assert _result_amount("bad", "ARS") == "—"
    assert [_result_card_state(v) for v in ("12.5", "-12.5", "0", "bad")] == [
        "green", "red", "gray", "gray"]

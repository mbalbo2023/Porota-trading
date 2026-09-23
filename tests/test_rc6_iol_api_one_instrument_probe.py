import json
from pathlib import Path

import scripts.rc6_iol_api_one_instrument_probe as probe


def test_comparison_is_ppi_primary_and_iol_complementary():
    result = probe._comparison(100.0, 101.0)
    assert result["status"] == "MATCH"
    assert result["ppi_last"] == 100.0
    assert result["iol_last"] == 101.0
    assert result["difference_pct"] == 1.0


def test_probe_never_infers_missing_comparison():
    assert probe._comparison(None, 101.0)["status"] == "COMPARISON_INSUFFICIENT_EVIDENCE"


def test_safe_quote_does_not_retain_unbounded_payload():
    payload = {"unit_price": 100, "secret": "must-not-persist", "trade": {"lot_price": 101}}
    safe = probe._safe_quote(payload)
    assert safe == {"unit_price": 100, "trade_lot_price": 101}

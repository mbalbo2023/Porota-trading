"""A1115-BM: SHADOW benchmark must not measure a different native PPI identity.

Provider payloads are synthetic. No credentials, wire calls, or runtime writes.
This branch depends on A1115-PI's shared native identity guard in normalize_quote.
"""

from datetime import datetime, timezone

import pytest

from rc6_dynamic_universe.benchmark import _observation


AT = datetime(2026, 11, 10, 14, tzinfo=timezone.utc)
ROW = {
    "ticker": "GGAL",
    "instrument_type": "ACCIONES",
    "market": "BYMA",
    "currency": "ARS",
    "settlement": "A-24HS",
}


def _payload(endpoint, **native_identity):
    shape = (
        {"date": AT.isoformat(), "price": 100}
        if endpoint == "current"
        else {
            "date": AT.isoformat(),
            "bids": [{"price": 99, "quantity": 10}],
            "offers": [{"price": 101, "quantity": 10}],
        }
    )
    return shape | native_identity


@pytest.mark.parametrize("endpoint", ("current", "book"))
@pytest.mark.parametrize("identity_field,contradiction", (
    ("ticker", "YPFD"),
    ("type", "CEDEARS"),
    ("market", "A3"),
    ("currency", "USD"),
    ("settlement", "INMEDIATA"),
    ("settlement_used", "INMEDIATA"),
))
def test_benchmark_rejects_explicit_native_identity_mismatch(
        endpoint, identity_field, contradiction):
    # Without the exact 5-key guard, a fresh wrong Book counts as useful
    # evidence for GGAL/BYMA/ARS/A-24HS and distorts capacity measurement.
    with pytest.raises(ValueError, match="PPI_RESPONSE_IDENTITY_MISMATCH"):
        _observation(endpoint, _payload(endpoint, **{identity_field: contradiction}),
                     ROW, AT, 120, None)


@pytest.mark.parametrize("endpoint", ("current", "book"))
def test_benchmark_accepts_native_aliases_and_absent_optional_identity(endpoint):
    result = _observation(endpoint, _payload(
        endpoint, ticker="ggal", instrumentType="ACCIONES", market="BCBA",
        currency="Pesos", settlementUsed="T1"), ROW, AT, 120, None)
    assert result["useful"] is True
    assert result["useful_distinct"] is True
    assert result["error_code"] is None

    omitted = _observation(endpoint, _payload(endpoint), ROW, AT, 120, None)
    assert omitted["useful"] is True
    assert omitted["error_code"] is None

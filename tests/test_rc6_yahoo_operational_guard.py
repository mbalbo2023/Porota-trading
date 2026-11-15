"""PAPER release identity must override legacy Yahoo research preferences.

Only the provider boundary is replaced: no network or broker is accessed.
Removing a canonical PAPER guard must expose the recorded Yahoo access.
"""
import time

import pytest

import _version
import e_technical_engine as technical


@pytest.fixture
def yahoo_requested_in_paper(monkeypatch):
    assert _version.MODE == "PRODUCTION_PAPER"
    # Environment MODE is deliberately untrusted; the release stays PAPER.
    monkeypatch.setenv("MODE", "RESEARCH")
    monkeypatch.setenv("TECHNICAL_DATA_SOURCE_CEDEARS", "yahoo")
    monkeypatch.setenv("YFINANCE_SHADOW_ONLY", "false")
    monkeypatch.setattr(technical, "TECHNICAL_DATA_SOURCE_CEDEARS", "yahoo")
    monkeypatch.setattr(technical, "YFINANCE_SHADOW_ONLY", False)
    attempts = []

    def offline_fetch(*args):
        attempts.append(args)
        return None

    monkeypatch.setattr(technical, "_fetch_bars", offline_fetch)
    return attempts


@pytest.mark.parametrize("has_ppi", [False, True])
def test_paper_technical_ignores_yahoo_request_and_preserves_ppi_fail_closed(
        yahoo_requested_in_paper, has_ppi):
    class OfflinePPI:
        def __init__(self):
            self.requests = []

        def get_historical_series(self, *args):
            self.requests.append(args)
            return []  # Missing PPI evidence must not fall back to Yahoo.

    ppi = OfflinePPI() if has_ppi else None
    result = technical.evaluate_technical("AAPL", ppi_client=ppi)
    assert yahoo_requested_in_paper == []
    assert result.data_ok is False
    assert result.score_tech == 0.0
    assert result.atr_1h is None
    assert "PPI" in result.reason
    if ppi is not None:
        assert ppi.requests == [("AAPL", "CEDEARS", "A-24HS", 180)]


def test_paper_scalping_ignores_yahoo_request(yahoo_requested_in_paper):
    result = technical.evaluate_technical_scalping("AAPL")
    assert yahoo_requested_in_paper == []
    assert result.data_ok is False
    assert result.score_tech == 0.0
    assert result.atr_1h is None


@pytest.mark.parametrize("scalping", [False, True])
def test_paper_prefetch_never_starts_yahoo_work(yahoo_requested_in_paper, scalping):
    assert technical.prefetch_bars(["AAPL"], scalping=scalping) == 0
    # Zero completed downloads is insufficient: even an attempted read is wrong.
    assert yahoo_requested_in_paper == []


@pytest.mark.parametrize("cached", [False, True])
def test_paper_fetch_denies_yahoo_before_cache_or_transport(monkeypatch, cached):
    assert _version.MODE == "PRODUCTION_PAPER"
    monkeypatch.setenv("MODE", "RESEARCH")
    monkeypatch.setattr(technical, "TECHNICAL_DATA_SOURCE_CEDEARS", "yahoo")
    monkeypatch.setattr(technical, "YFINANCE_SHADOW_ONLY", False)
    attempts = []

    def offline_ticker(symbol):
        attempts.append(symbol)
        raise AssertionError("YAHOO_TRANSPORT_MUST_NOT_BE_REACHED")

    monkeypatch.setattr(technical.yf, "Ticker", offline_ticker)
    key = ("AAPL", "1h", "3mo")
    cached_frame = object()
    monkeypatch.setattr(technical, "_bars_cache", {
        key: (time.time(), cached_frame)
    } if cached else {})
    assert technical._fetch_bars(*key) is None
    assert attempts == []

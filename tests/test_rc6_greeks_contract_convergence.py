"""AUD-468-14: contract style, discounted bounds and European BS↔IV."""
from dataclasses import replace
from datetime import date
import math

import pytest

import ai_derivatives_engine as caller
import as_greeks_engine as model


def test_european_put_below_immediate_exercise_intrinsic_is_valid():
    premium = model.precio_teorico(80, 100, 180 / 365, .30, .10, False)
    assert premium == pytest.approx(6.671575834616235)
    result = model.calcular(premium, 80, 100, 180, False, .30,
                            exercise_style="EUROPEAN", dividend_yield=0.)
    assert result.convergio and result.volatilidad_implicita == pytest.approx(.10)
    assert result.valor_temporal < 0 and result.delta < 0
    assert result.entry_authority is False


@pytest.mark.parametrize("style", ["AMERICAN", "UNKNOWN", "", None])
def test_non_european_or_unknown_contract_never_uses_european_model(style):
    result = model.calcular(10, 100, 100, 90, True, .30, exercise_style=style)
    assert not result.convergio and result.delta is None
    assert result.entry_authority is False


@pytest.mark.parametrize("is_call", [True, False])
@pytest.mark.parametrize("rate,yield_rate", [(.30, 0.), (.10, .08), (-.02, .01)])
def test_dividend_and_rate_consistency_and_upper_lower_bounds(is_call, rate, yield_rate):
    premium = model.precio_teorico(95, 100, .7, rate, .35, is_call, yield_rate)
    implied = model.volatilidad_implicita(premium, 95, 100, .7, rate, is_call, yield_rate)
    assert implied == pytest.approx(.35, abs=1e-6)
    lo, hi = model.european_bounds(95, 100, .7, rate, is_call, yield_rate)
    if lo > .1:
        assert model.volatilidad_implicita(lo - .1, 95, 100, .7, rate, is_call, yield_rate) is None
    assert model.volatilidad_implicita(hi + .1, 95, 100, .7, rate, is_call, yield_rate) is None
    result = model.calcular(premium, 95, 100, .7 * 365, is_call, rate,
                            exercise_style="EUROPEAN", dividend_yield=yield_rate)
    assert result.convergio and result.volatilidad_implicita == pytest.approx(.35)
    assert math.isfinite(result.theta_diario)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_invalid_parameters_abstain(value):
    assert model.volatilidad_implicita(value, 100, 100, .5, .3) is None
    assert not model.calcular(value, 100, 100, 180, True, .3).convergio


@pytest.mark.parametrize("rate,yield_rate", [(-1e300, 0.), (.3, -1e300)])
def test_finite_unrepresentable_discount_bounds_abstain_without_crashing(rate, yield_rate):
    assert model.precio_teorico(100, 100, .5, rate, .3, True, yield_rate) is None
    assert model.volatilidad_implicita(10, 100, 100, .5, rate, True, yield_rate) is None
    assert not model.calcular(10, 100, 100, 180, True, rate, dividend_yield=yield_rate).convergio


def test_real_option_caller_requires_explicit_contract_terms(monkeypatch):
    monkeypatch.setattr(caller, "_volatilidad_historica", lambda _: .20)
    spec = caller.parse_option_ticker("GFGC7500O", today=date(2026, 8, 20), tipo_subyacente="ACCIONES")
    spec.strike_source = "api"
    missing = caller.assess_option(replace(spec), 700., 7100., spread_pct=1.)
    assert missing.greeks["exercise_style"] == "UNKNOWN"
    assert missing.greeks["convergio"] is False
    assert missing.greeks["entry_authority"] is False
    declared = caller.assess_option(replace(spec, exercise_style="EUROPEAN", dividend_yield=0., risk_free_rate=.29),
                                    700., 7100., spread_pct=1.)
    assert declared.blocking_code == "PRIMA_MUY_CARA_VS_VOLATILIDAD"
    assert declared.greeks["entry_authority"] is False

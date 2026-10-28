"""PAPER cost scenario is source-versioned and never becomes broker evidence."""

from decimal import Decimal

import pytest

import rc6_caucion_fee_model as model


def test_public_byma_dual_rights_prorate_over_90_not_365():
    principal = Decimal("100000")
    gross = Decimal("1000")
    days = 3
    old = model.old_public_modeled_fee(principal, days)
    revised = model.conservative_public_modeled_fee(principal, gross, days)
    assert old == Decimal("20.34")
    # PPI annualized /365 plus each BYMA right /90 on greater value.
    independent = (
        principal * Decimal(".02") * Decimal("1.21") * days / 365
        + (principal + gross) * Decimal(".00045") * 2 * Decimal("1.21") * days / 90
    ).quantize(Decimal(".01"), rounding=model.ROUND_HALF_UP)
    assert revised == independent and revised > old
    assert model.ACCOUNT_FEES_VERIFIED is False
    assert len(model.SOURCE_SHA256) == 64


def test_restatement_can_only_lower_the_paper_net_without_touching_original():
    assert model.restated_public_modeled_fee("1000", "3.00", "0.20", 3) > Decimal("0.20")
    assert model.restated_public_modeled_fee("1000", "3.00", "1.00", 3) == Decimal("1.00")
    assert model.restated_public_modeled_fee("1000", "0", "0.20", 3) > 0


@pytest.mark.parametrize("principal,gross,days", [
    ("NaN", "10", 3), ("100", "Infinity", 3), ("-100", "5", 3),
    ("100", "-5", 3), ("100", "10", 0), ("100", "10", 91),
    ("100", "10", 3.0),
])
def test_unsupported_economics_fail_closed(principal, gross, days):
    with pytest.raises(ValueError, match="CAUCION_PUBLIC_FEE_"):
        model.conservative_public_modeled_fee(principal, gross, days)

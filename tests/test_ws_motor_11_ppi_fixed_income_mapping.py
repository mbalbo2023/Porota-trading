from decimal import Decimal

import rc6_ppi_contract_normalizer as normalizer
import cq_family_contract_rules_hf6 as rules


def test_ppi_datos_tecnicos_maps_explicit_minimum_multiple_and_quote_basis():
    payload = {"payload": {"ticker": "GD30", "laminaMinima": 1,
                           "multiploMinimo": 1, "nominalesEnPrecio": 100,
                           "fechaVencimiento": "2030-07-09",
                           "intereses": "EVENT_PAYLOAD", "amortizacion": "EVENT_PAYLOAD",
                           "cantidadDecimales": 0, "cantidadDecimalesPrecio": 3}}
    row = normalizer.bond_technical(payload)
    assert row["quantity_min"] == "1"
    assert row["quantity_step"] == "1"
    assert row["price_quote_unit"] == "100"
    assert Decimal(row["cash_multiplier"]) == Decimal("0.01")
    assert row["order_price_tick"] is None

    open_fields = rules.FAMILY_CONTRACT_FIELDS["BONOS"] - rules.EVENT_CONDITIONAL_FIELDS["BONOS"]
    identity = {"market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}
    assert not open_fields - set(row) - set(identity)


def test_display_decimals_still_do_not_invent_quantity_or_price_steps():
    payload = {"payload": {"ticker": "X", "cantidadDecimales": 2,
                           "cantidadDecimalesPrecio": 4}}
    row = normalizer.bond_technical(payload)
    assert row["quantity_min"] is None
    assert row["quantity_step"] is None
    assert row["cash_multiplier"] is None
    assert row["order_price_tick"] is None

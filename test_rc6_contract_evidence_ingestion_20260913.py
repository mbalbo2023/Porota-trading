from rc6_ppi_contract_normalizer import instrumentos_operables, bond_technical
from rc6_contract_capture_importer import stage_evidence


def test_instrumentos_operables_keeps_explicit_fee_schedule_without_inventing_steps():
    payload = {"payload": [{
        "itemId": 123,
        "ticker": "GGAL",
        "nombre": "Grupo Financiero Galicia",
        "moneda": {"descripcion": "Pesos", "simbolo": "$", "id": 1},
        "comisionMontoMinimo": 10,
        "porcentajeComisionEstimado": 0.6,
        "porcentajeIVAComisionEstimado": 0.126,
        "porcentajeDerechoMercadoYBolsa": 0.08,
        "cantidadDecimales": 0,
        "cantidadDecimalesPrecio": 2,
    }]}
    row = instrumentos_operables(payload)[0]
    assert row["fee_schedule"] == {
        "commission_minimum": 10,
        "commission_rate_estimated": 0.6,
        "commission_vat_rate_estimated": 0.126,
        "market_fee_rate": 0.08,
    }
    assert row["quantity_step"] is None
    assert row["price_tick"] is None
    assert "conversion_ratio" not in row


def test_bond_technical_maps_only_direct_canonical_semantics():
    payload = {"payload": {
        "ticker": "AL30",
        "isin": "ARARGE3209S6",
        "fechaVencimiento": "2030-07-09",
        "intereses": [{"rate": 1.0}],
        "amortizacion": [{"percent": 10.0}],
        "laminaMinima": 1,
        "multiploMinimo": 1,
        "nominalesEnPrecio": 100,
        "cantidadDecimales": 0,
        "cantidadDecimalesPrecio": 3,
    }}
    row = bond_technical(payload)
    assert row["maturity_date"] == "2030-07-09"
    assert row["coupon_terms"] == [{"rate": 1.0}]
    assert row["amortization_terms"] == [{"percent": 10.0}]
    assert row["order_quantity_step"] is None
    assert row["order_price_tick"] is None
    assert row["price_unit_nominals"] is None
    assert "payment_currency" not in row


def test_stage_evidence_merges_same_source_endpoints_and_fails_closed_on_disagreement():
    pending = {}
    conflicts = {}
    common = dict(
        family="BONOS", ticker="AL30", market="BYMA", settlement="CI",
        source_class="PPI_AUTHENTICATED_XHR", source_ref="https://ppi/one",
    )
    stage_evidence(pending, conflicts, **common,
                   evidence={"fee_schedule": {"market_fee_rate": 0.08},
                             "source_job": "STATIC"})
    stage_evidence(pending, conflicts, **{**common, "source_ref": "https://ppi/two"},
                   evidence={"maturity_date": "2030-07-09", "isin": "ARARGE3209S6",
                             "source_job": "TECHNICAL"})
    assert conflicts == {}
    staged = next(iter(pending.values()))["evidence"]
    assert staged["fee_schedule"] == {"market_fee_rate": 0.08}
    assert staged["maturity_date"] == "2030-07-09"
    assert staged["isin"] == "ARARGE3209S6"
    assert staged["source_job"] == "TECHNICAL"

    stage_evidence(pending, conflicts, **common,
                   evidence={"maturity_date": "2031-01-01"})
    key = next(iter(pending))
    assert "maturity_date" in conflicts[key]
    assert pending[key]["evidence"]["maturity_date"] == "2030-07-09"

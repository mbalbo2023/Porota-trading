import rc6_family_readiness as readiness


def test_ready_instrument_promotes_only_paper():
    report = readiness.evaluate(
        [{"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}],
        [{"symbol": "YPFD", "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "A-24HS",
          "primary_comparison": {"contract_state": "READY_PAPER_SHADOW"}}],
    )
    item = report["instruments"][0]
    assert item["paper_contract_ready"] is True
    assert item["paper_auto_enabled"] is False
    assert item["entry_authority"] is False
    assert "ENTRY_SIGNAL_ECONOMICS_AND_ADMISSION_NOT_EVALUATED" in item["reasons"]
    assert item["real_money_authorized"] is False
    assert next(row for row in report["families"] if row["family"] == "ACCIONES")["state"] == "READY_CONTRACT_ONLY"
    assert report["paper_auto_promotion"] is False


def test_missing_iol_is_explicit_pending_gap():
    report = readiness.evaluate(
        [{"family": "CEDEARS", "symbol": "AAPL", "market": "BYMA", "settlement": "A-24HS"}],
        [],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is False
    assert "PPI_IOL_COMPARISON_NOT_PUBLISHED" in item["reasons"]
    assert report["families"][0]["state"] == "PENDING"


def test_one_family_does_not_block_another():
    report = readiness.evaluate(
        [{"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"},
         {"family": "BONOS", "symbol": "AL30", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}],
        [{"symbol": "YPFD", "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "A-24HS",
          "primary_comparison": {"contract_state": "READY_PAPER_SHADOW"}}],
    )
    states = {item["family"]: item["state"] for item in report["families"]}
    assert states["ACCIONES"] == "READY_CONTRACT_ONLY"
    assert states["BONOS"] == "PENDING"


def test_only_explicit_paper_states_are_simulatable():
    assert "READY_PAPER" in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_PAPER_SHADOW" in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_SHADOW" not in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_SHADOW_PARTIAL" not in readiness.PAPER_SIMULATABLE_STATES


def test_complemented_source_state_does_not_authorize_real_money():
    report = readiness.evaluate(
        [{"family": "BONOS", "symbol": "GD30", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}],
        [{"symbol": "GD30", "asset_type": "BONOS", "market": "BYMA", "currency": "ARS", "term": "A-24HS",
          "primary_comparison": {
              "contract_state": "READY_PAPER_SHADOW",
              "complemented": ["tir", "duration"],
              "comparison_complete": True,
          }}],
    )
    item = report["instruments"][0]
    assert item["paper_contract_ready"] is True
    assert item["paper_auto_enabled"] is False
    assert item["real_money_authorized"] is False


def test_partial_shadow_is_observable_but_not_paper_simulatable():
    report = readiness.evaluate(
        [{"family": "CAUCIONES", "symbol": "CAUCION-1", "market": "BYMA"}],
        [{"symbol": "CAUCION-1", "asset_type": "CAUCIONES",
          "primary_comparison": {
              "contract_state": "READY_SHADOW_PARTIAL",
              "comparison_complete": True,
          }}],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is False
    assert item["real_money_authorized"] is False


def test_audit_keeps_all_families_visible_when_capture_is_empty():
    report = readiness.evaluate([], [])
    families = {item["family"] for item in report["families"]}
    assert {"BONOS", "OBLIGACIONES", "CAUCIONES", "LETRAS", "FCI", "FUTUROS", "OPCIONES"} <= families
    assert all(item["state"] == "PENDING" for item in report["families"])


def test_ticker_only_or_cross_family_observation_never_promotes_paper_contract():
    catalog = [{"family": "BONOS", "symbol": "COLLIDE", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}]
    ready = {"primary_comparison": {"contract_state": "READY_PAPER_SHADOW"}}
    for observation in (
        {"symbol": "COLLIDE", "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "A-24HS"},
        {"symbol": "COLLIDE", "asset_type": "BONOS"},
        {"symbol": "COLLIDE", "asset_type": "BONOS", "market": "BYMA", "currency": "ARS", "term": "CI"},
        {"symbol": "COLLIDE", "asset_type": "BONOS", "market": "BYMA", "currency": "USD", "term": "A-24HS"},
    ):
        result = readiness.evaluate(catalog, [observation | ready])
        bond = next(item for item in result["instruments"] if item["family"] == "BONOS"
                    and item["market"] == "BYMA" and item["settlement"] == "A-24HS")
        assert not bond["paper_contract_ready"] and not bond["paper_auto_enabled"]


def test_duplicate_exact_identity_requires_review():
    catalog = [{"family": "ON", "symbol": "SAME", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}]
    row = {"symbol": "SAME", "asset_type": "OBLIGACIONES", "market": "BYMA", "currency": "ARS",
           "term": "A-24HS", "primary_comparison": {"contract_state": "READY_PAPER_SHADOW"}}
    result = readiness.evaluate(catalog, [row, row])
    instrument = result["instruments"][0]
    assert instrument["family"] == "OBLIGACIONES"
    assert instrument["contract_state"] == "BLOCKED_CONFLICT"
    assert "DUPLICATE_EXACT_IDENTITY_REVIEW_REQUIRED" in instrument["reasons"]
    assert not instrument["paper_auto_enabled"]

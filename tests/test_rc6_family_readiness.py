import rc6_family_readiness as readiness


def test_ready_instrument_promotes_only_paper():
    report = readiness.evaluate(
        [{"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "settlement": "A-24HS"}],
        [{"symbol": "YPFD", "asset_type": "ACCIONES",
          "primary_comparison": {"contract_state": "READY_SHADOW"}}],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is True
    assert item["real_money_authorized"] is False
    assert report["families"][0]["state"] == "READY"


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
        [{"family": "ACCIONES", "symbol": "YPFD"},
         {"family": "BONOS", "symbol": "AL30"}],
        [{"symbol": "YPFD", "asset_type": "ACCIONES",
          "primary_comparison": {"contract_state": "READY_SHADOW"}}],
    )
    states = {item["family"]: item["state"] for item in report["families"]}
    assert states["ACCIONES"] == "READY"
    assert states["BONOS"] == "PENDING"


def test_ready_paper_and_ready_shadow_are_both_simulatable():
    assert "READY_PAPER" in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_PAPER_SHADOW" in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_SHADOW" in readiness.PAPER_SIMULATABLE_STATES


def test_complemented_source_state_does_not_authorize_real_money():
    report = readiness.evaluate(
        [{"family": "BONOS", "symbol": "GD30", "market": "BYMA"}],
        [{"symbol": "GD30", "asset_type": "BONOS",
          "primary_comparison": {
              "contract_state": "READY_PAPER_SHADOW",
              "complemented": ["tir", "duration"],
              "comparison_complete": True,
          }}],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is True
    assert item["real_money_authorized"] is False


def test_partial_shadow_is_simulatable_without_real_authority():
    report = readiness.evaluate(
        [{"family": "CAUCIONES", "symbol": "CAUCION-1", "market": "BYMA"}],
        [{"symbol": "CAUCION-1", "asset_type": "CAUCIONES",
          "primary_comparison": {
              "contract_state": "READY_SHADOW_PARTIAL",
              "comparison_complete": True,
          }}],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is True
    assert item["real_money_authorized"] is False


def test_audit_keeps_all_families_visible_when_capture_is_empty():
    report = readiness.evaluate([], [])
    families = {item["family"] for item in report["families"]}
    assert {"BONOS", "ON", "CAUCIONES", "LETRAS", "FCI", "FUTUROS", "OPCIONES"} <= families
    assert all(item["state"] == "PENDING" for item in report["families"])

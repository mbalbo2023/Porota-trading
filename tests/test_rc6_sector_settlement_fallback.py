import es_policy_context_rc6 as policy


def row(sector):
    return {"sector": sector, "source": "TEST_REVIEWED", "author": "TEST", "effective_at": "2026-09-10"}


def test_exact_reviewed_mapping_wins():
    exact = ("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA")
    assert policy._reviewed_sector_mapping({exact: row("FINANCIALS")}, exact)["sector"] == "FINANCIALS"


def test_reviewed_sector_is_settlement_invariant_when_unambiguous():
    reviewed = ("AAPLD", "CEDEARS", "BYMA", "USD_MEP", "A-24HS")
    immediate = ("AAPLD", "CEDEARS", "BYMA", "USD_MEP", "INMEDIATA")
    resolved = policy._reviewed_sector_mapping({reviewed: row("TECHNOLOGY")}, immediate)
    assert resolved["sector"] == "TECHNOLOGY"
    assert resolved["resolution"] == "REVIEWED_UNAMBIGUOUS_SETTLEMENT_FALLBACK"


def test_sector_fallback_never_crosses_currency_family_or_market():
    base = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    mapping = {base: row("FINANCIALS")}
    assert policy._reviewed_sector_mapping(mapping, ("GGAL", "ACCIONES", "BYMA", "USD_MEP", "INMEDIATA")) is None
    assert policy._reviewed_sector_mapping(mapping, ("GGAL", "CEDEARS", "BYMA", "ARS", "INMEDIATA")) is None
    assert policy._reviewed_sector_mapping(mapping, ("GGAL", "ACCIONES", "NYSE", "ARS", "INMEDIATA")) is None


def test_ambiguous_reviewed_sectors_remain_fail_closed():
    mapping = {
        ("TEST", "ACCIONES", "BYMA", "ARS", "A-24HS"): row("ENERGY"),
        ("TEST", "ACCIONES", "BYMA", "ARS", "CI"): row("FINANCIALS"),
    }
    target = ("TEST", "ACCIONES", "BYMA", "ARS", "INMEDIATA")
    assert policy._reviewed_sector_mapping(mapping, target) is None


def test_option_inherits_sector_only_from_unambiguous_reviewed_underlying():
    mapping = {
        ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"): row("FINANCIERO"),
        ("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA"): row("FINANCIERO"),
    }
    resolved = policy._reviewed_underlying_sector_mapping(mapping, "GGAL", "BYMA")
    assert resolved["sector"] == "FINANCIERO"
    assert resolved["resolution"] == "REVIEWED_UNDERLYING_SECTOR"


def test_option_underlying_sector_ambiguity_stays_fail_closed():
    mapping = {
        ("TEST", "ACCIONES", "BYMA", "ARS", "A-24HS"): row("ENERGIA"),
        ("TEST", "CEDEARS", "BYMA", "ARS", "A-24HS"): row("TECNOLOGIA"),
    }
    assert policy._reviewed_underlying_sector_mapping(mapping, "TEST", "BYMA") is None

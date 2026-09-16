import ba_data912_history as data912


def test_non_operational_history_symbol_is_rejected_before_network():
    assert data912.ensure_symbol("AL30", "BONOS") == 0


def test_operational_scope_is_explicit():
    assert data912.OPERATIONAL_FAMILIES == frozenset(("ACCIONES", "CEDEARS"))

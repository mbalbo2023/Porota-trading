from cz_a3_cem_public_history_hf6 import (
    A3CEMPublicReadOnlyClient,
    CEM_LIVE_TRADING_GATE_ALLOWED,
    CEM_ORDER_ROUTING_ALLOWED,
    assert_cem_invariants,
)


def test_cem_is_not_order_source_or_live_gate():
    assert CEM_ORDER_ROUTING_ALLOWED is False
    assert CEM_LIVE_TRADING_GATE_ALLOWED is False
    assert_cem_invariants()


def test_cem_client_exposes_no_order_methods():
    names=set(dir(A3CEMPublicReadOnlyClient))
    for name in ("send_order","new_order","replace_order","cancel_order"):
        assert name not in names


def test_cem_expected_read_methods_exist():
    names=set(dir(A3CEMPublicReadOnlyClient))
    for name in ("products","symbols","closing_prices","tick_prices","totals","option_underlying_prices"):
        assert name in names

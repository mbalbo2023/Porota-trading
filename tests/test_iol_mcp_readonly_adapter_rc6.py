import json
import pytest

import iol_mcp_readonly_adapter_rc6 as m


def envelope(payload):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload})


def test_structured_content_is_unwrapped_for_read_only_family_tools():
    body = envelope({
        "content": [{"type": "text", "text": "typed response"}],
        "structuredContent": {
            "result": [
                {"asset": "FUND1", "currency": "ARS", "operable": True}
            ]
        },
        "isError": False,
    })
    parsed = m._json_from_mcp_body(body)
    assert parsed == {
        "result": [{"asset": "FUND1", "currency": "ARS", "operable": True}]
    }


def test_structured_caucion_payload_keeps_rates():
    body = envelope({
        "structuredContent": {
            "result": [{"days": 3, "rate": 18.0, "min_amount": 100000}]
        },
        "isError": False,
    })
    parsed = m._json_from_mcp_body(body)
    assert parsed["result"][0]["days"] == 3
    assert parsed["result"][0]["min_amount"] == 100000


def test_explicit_tool_error_never_becomes_empty_success():
    with pytest.raises(m.IOLMCPError, match="IOL_MCP_TOOL_ERROR"):
        m._json_from_mcp_body(envelope({
            "structuredContent": {},
            "isError": True,
        }))


def test_execution_and_account_tools_are_not_in_read_only_allowlist():
    assert not (m.ALLOWED_TOOLS & m.FORBIDDEN_ACCOUNT_OR_EXECUTION_TOOLS)
    for name in (
        "validate_order", "place_order", "cancel_order",
        "validate_caucion", "place_caucion",
        "validate_fci_subscription", "subscribe_fci",
        "validate_fci_redemption", "redeem_fci",
    ):
        assert name not in m.ALLOWED_TOOLS


def test_family_reference_reads_remain_allowed():
    for name in (
        "get_fixed_income_analytics", "simulate_fixed_income_by_nominals",
        "get_options_chain", "get_caucion_rates", "get_fci_funds",
    ):
        assert name in m.ALLOWED_TOOLS

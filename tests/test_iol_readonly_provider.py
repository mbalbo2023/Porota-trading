import unittest
from datetime import datetime, timezone

from iol_readonly_provider import (
    IOLReadOnlyProvider,
    IOLReadOnlyProviderError,
    SOURCE_CLASS,
)


class FakeIOLClient:
    def __init__(self):
        self.calls = []
        self.quote_response = {"ultimoPrecio": 1234.5}

    def get_panel(self, instrument, panel, country):
        self.calls.append(("get_panel", instrument, panel, country))
        return ["GGAL", "AL30"]

    def get_cotizacion(self, symbol, market):
        self.calls.append(("get_cotizacion", symbol, market))
        return self.quote_response

    def get_serie_historica(self, symbol, market, *, dias, ajustada):
        self.calls.append(("get_serie_historica", symbol, market, dias, ajustada))
        return [{"fecha": "2026-09-14", "cierre": 1234.5}]

    def get_estado_cuenta(self):
        raise AssertionError("account endpoint must never be invoked")

    def get_portafolio(self):
        raise AssertionError("portfolio endpoint must never be invoked")

    def get_estado_operacion(self, _number):
        raise AssertionError("operation endpoint must never be invoked")

    def estimar_operacion(self, *_args, **_kwargs):
        raise AssertionError("estimate endpoint is outside this read-only adapter")


class IOLReadOnlyProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeIOLClient()
        self.clock = lambda: datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
        self.provider = IOLReadOnlyProvider(self.client, clock=self.clock)

    def test_quote_is_one_read_only_instrument_call_with_provenance(self):
        result = self.provider.get_quote("AAPL", "nasdaq")
        self.assertEqual(result.source_class, SOURCE_CLASS)
        self.assertEqual(result.operation, "quote")
        self.assertEqual(result.status, "NONEMPTY_UNVALIDATED")
        self.assertEqual(result.observed_at, "2026-09-14T12:30:00.000000+00:00")
        self.assertEqual(result.request, {"symbol": "AAPL", "market": "nasdaq"})
        self.assertEqual(self.client.calls, [("get_cotizacion", "AAPL", "nasdaq")])

    def test_history_forwards_explicit_bounded_query(self):
        result = self.provider.get_history("AL30", days=120, adjusted=False)
        self.assertEqual(result.operation, "history")
        self.assertEqual(result.request["days"], 120)
        self.assertEqual(self.client.calls, [
            ("get_serie_historica", "AL30", "bcba", 120, False),
        ])

    def test_panel_uses_only_allowlisted_panel_method(self):
        result = self.provider.get_panel()
        self.assertEqual(result.payload, ["GGAL", "AL30"])
        self.assertEqual(self.client.calls, [("get_panel", "acciones", "lideres", "argentina")])

    def test_empty_source_response_is_not_readiness(self):
        self.client.quote_response = None
        result = self.provider.get_quote("GGAL")
        self.assertEqual(result.status, "EMPTY_OR_UNAVAILABLE")
        self.assertFalse(hasattr(result, "ready"))

    def test_malformed_nonempty_payload_is_explicitly_unvalidated(self):
        self.client.quote_response = {"unexpected": object()}
        result = self.provider.get_quote("GGAL")
        self.assertEqual(result.status, "NONEMPTY_UNVALIDATED")
        self.assertFalse(hasattr(result, "ready"))

    def test_account_portfolio_operation_and_estimate_are_not_exposed(self):
        for name in ("get_estado_cuenta", "get_portafolio", "get_estado_operacion", "estimar_operacion"):
            self.assertFalse(hasattr(self.provider, name))
        # This wrapper narrows its public API; it is not a Python security boundary.
        self.assertFalse(hasattr(self.provider, "_client"))
        self.provider.get_quote("GGAL")
        self.assertEqual(len(self.client.calls), 1)

    def test_rejects_path_injection_before_calling_client(self):
        for symbol in ("GGAL/../../account", "GGAL?x=1", "", ".."):
            with self.subTest(symbol=symbol):
                with self.assertRaises(IOLReadOnlyProviderError):
                    self.provider.get_quote(symbol)
        self.assertEqual(self.client.calls, [])

    def test_history_limits_and_types_fail_before_calling_client(self):
        for days in (0, -1, 3661, True, 1.5):
            with self.subTest(days=days):
                with self.assertRaises(IOLReadOnlyProviderError):
                    self.provider.get_history("GGAL", days=days)
        with self.assertRaises(IOLReadOnlyProviderError):
            self.provider.get_history("GGAL", adjusted=1)
        self.assertEqual(self.client.calls, [])

    def test_invalid_clock_is_rejected_before_any_allowed_call(self):
        provider = IOLReadOnlyProvider(self.client, clock=lambda: datetime(2026, 9, 14))
        operations = (
            lambda: provider.get_panel(),
            lambda: provider.get_quote("GGAL"),
            lambda: provider.get_history("GGAL"),
        )
        for operation in operations:
            with self.assertRaises(IOLReadOnlyProviderError):
                operation()
        self.assertEqual(self.client.calls, [])

    def test_missing_allowlisted_method_is_explicit_error(self):
        provider = IOLReadOnlyProvider(object(), clock=self.clock)
        with self.assertRaises(IOLReadOnlyProviderError):
            provider.get_quote("GGAL")


if __name__ == "__main__":
    unittest.main()

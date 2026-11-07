"""A695: a stale persisted zero cannot certify current real-order safety.

OFFHOST deterministic renderer tests; no SQLite writes, provider or host needed.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from rc6_trader_dashboard.navigation import DESTINATIONS
from rc6_trader_dashboard.shell import render


class RealOrdersReadoutTests(unittest.TestCase):
    def _html(self, real_orders_sent=0, **overrides):
        runtime = {
            "mode": "PRODUCTION_PAPER",
            "session_state": "OPEN",
            "ppi_auth": "NO_VERIFICADO",
            "real_orders_sent": real_orders_sent,
            "heartbeat_at": "2026-11-05T13:29:30Z",
            "heartbeat_age": 30.0,
            "heartbeat_budget_seconds": 75,
            "heartbeat_freshness": "FRESH",
            "last_market_data_at": "2026-11-05T13:29:30Z",
            "market_age": 30.0,
            "market_freshness": "FRESH",
        }
        runtime.update(overrides)
        projection = SimpleNamespace(
            runtime=runtime,
            shadow={"report": {"capacity_policy": {"status": "OFF"}, "as_of": None}},
            shadow_fresh=False,
            filters={},
            now=datetime(2026, 11, 5, 13, 30, tzinfo=timezone.utc),
        )
        destination = DESTINATIONS[0]
        return render(destination, destination.tabs[0][0], projection, "<p>Prueba</p>")

    def test_fresh_verified_zero_can_show_no_real_orders_observed(self):
        html = self._html()
        self.assertIn("real_orders_sent=0 · órdenes reales: NINGUNA observada", html)
        self.assertIn("role='status' aria-live='polite'", html)
        self.assertNotIn("Alerta de seguridad: contador", html)

    def test_stale_zero_is_historical_not_current_safety_assertion(self):
        html = self._html(heartbeat_age=180.0, heartbeat_freshness="STALE")
        self.assertIn("real_orders_sent=0 (último valor almacenado)", html)
        self.assertIn("observación histórica", html)
        self.assertIn("estado actual NO_VERIFICADO", html)
        self.assertNotIn("órdenes reales: NINGUNA", html)

    def test_fresh_word_without_bound_clock_is_not_certified(self):
        html = self._html(heartbeat_age=180.0, heartbeat_freshness="FRESH")
        self.assertIn("estado actual NO_VERIFICADO", html)
        self.assertNotIn("órdenes reales: NINGUNA", html)
        html = self._html(heartbeat_at=None, heartbeat_age=None, heartbeat_freshness="FRESH")
        self.assertIn("estado actual NO_VERIFICADO", html)
        self.assertNotIn("órdenes reales: NINGUNA", html)

    def test_positive_counter_is_critical_even_if_observer_stale(self):
        html = self._html(real_orders_sent=2, heartbeat_freshness="STALE", heartbeat_age=180)
        self.assertIn("real_orders_sent=2 · ALERTA", html)
        self.assertIn("role='alert'", html)
        self.assertIn("contador de órdenes reales distinto de cero", html)
        self.assertNotIn("órdenes reales: NINGUNA", html)

    def test_unknown_and_malformed_values_do_not_invent_zero(self):
        for value in (None, False, "0", -1):
            with self.subTest(value=value):
                html = self._html(real_orders_sent=value)
                self.assertIn("real_orders_sent=NO_VERIFICADO", html)
                self.assertIn("estado actual NO_VERIFICADO", html)
                self.assertNotIn("órdenes reales: NINGUNA", html)


if __name__ == "__main__":
    unittest.main()

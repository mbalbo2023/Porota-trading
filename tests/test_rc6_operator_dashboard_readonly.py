"""The internal probe exports status evidence, never HTML or credentials."""
import json
from types import SimpleNamespace
from unittest import TestCase

from scripts.rc6_operator_dashboard_readonly import CHILD, POSTCLOSE_ROUTES, ROUTES, SCHEMA, audit_host


class OperatorAuditTests(TestCase):
 def test_embedded_probe_compiles(self):
    compile(CHILD, "<dashboard-probe>", "exec")
    self.assertEqual(len(POSTCLOSE_ROUTES), 49)
    self.assertEqual(len(set(POSTCLOSE_ROUTES)), 49)

 def test_sanitized_audit_receipt_has_exact_routes_and_no_page_content(self):
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL", "source_status": "BOUNDED",
               "routes": [{"path": path, "http": 200, "nav": True, "content": True,
                           "secret": "must never leave host"} for path in ROUTES],
               "sources": [{"component": "PPI_MARKETDATA", "state": "VERDE",
                            "checked_age_seconds": 2, "last_success_age_seconds": 2,
                            "detail": "must never leave host"}]}
    def runner(argv, **kwargs):
        assert argv[:4] == ["docker", "exec", "porota_production_dashboard", "python"]
        assert kwargs["timeout"] == 150
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    result = audit_host(runner=runner)
    self.assertEqual(result["status"], "ROUTES_VERIFIED")
    self.assertEqual(len(result["routes"]), len(ROUTES))
    self.assertNotIn("must never leave host", json.dumps(result))

    payload["profile_status"] = "BOUNDED"
    payload["profile"] = [{"stage": "balances", "elapsed_ms": 2832,
                           "read_queries": 4, "state": "READ_OK", "sql": "must never leave host"}]
    payload["balance_queries"] = [{"stage": "LATEST_EQUITY", "elapsed_ms": 2800,
                                    "state": "READ_OK", "sql": "must never leave host"}]
    self.assertEqual(audit_host(runner=runner)["profile"][0]["elapsed_ms"], 2832)
    self.assertEqual(audit_host(runner=runner)["balance_queries"][0]["stage"], "LATEST_EQUITY")
    self.assertNotIn("must never leave host", json.dumps(audit_host(runner=runner)))

    payload["routes"][0]["elapsed_ms"] = 3100
    payload["routes"][0]["slow_2s"] = True
    payload["routes"][0]["unverified_mentions"] = 3
    self.assertEqual(audit_host(runner=runner)["status"], "ROUTES_DEGRADED")
    self.assertEqual(audit_host(runner=runner)["routes"][0]["unverified_mentions"], 3)
    payload["audit_scope"] = "POSTCLOSE_ALL"
    payload["routes"] = [{"path": path, "http": 200, "nav": True, "content": True}
                         for path in POSTCLOSE_ROUTES]
    self.assertEqual(audit_host(runner=runner)["audit_scope"], "POSTCLOSE_ALL")
    self.assertEqual(len(audit_host(runner=runner)["routes"]), 49)


 def test_partial_or_untrusted_receipt_fails_closed(self):
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL", "source_status": "BOUNDED",
               "routes": [{"path": "/", "http": 200, "nav": True, "content": True}],
               "sources": []}
    result = audit_host(runner=lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    self.assertEqual(result["status"], "NO_VERIFICADO")
    self.assertEqual(result["routes"], [])

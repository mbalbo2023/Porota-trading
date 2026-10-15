"""The internal probe exports status evidence, never HTML or credentials."""
import json
from types import SimpleNamespace
from unittest import TestCase

from scripts.rc6_operator_dashboard_readonly import CHILD, ROUTES, SCHEMA, audit_host


class OperatorAuditTests(TestCase):
 def test_embedded_probe_compiles(self):
    compile(CHILD, "<dashboard-probe>", "exec")

 def test_sanitized_audit_receipt_has_exact_routes_and_no_page_content(self):
    payload = {"schema": SCHEMA, "read_only": True, "source_status": "BOUNDED",
               "routes": [{"path": path, "http": 200, "nav": True, "content": True,
                           "secret": "must never leave host"} for path in ROUTES],
               "sources": [{"component": "PPI_MARKETDATA", "state": "VERDE",
                            "checked_age_seconds": 2, "last_success_age_seconds": 2,
                            "detail": "must never leave host"}]}
    def runner(argv, **kwargs):
        assert argv[:4] == ["docker", "exec", "porota_production_dashboard", "python"]
        assert kwargs["timeout"] == 85
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    result = audit_host(runner=runner)
    self.assertEqual(result["status"], "ROUTES_VERIFIED")
    self.assertEqual(len(result["routes"]), len(ROUTES))
    self.assertNotIn("must never leave host", json.dumps(result))

    payload["routes"][0]["elapsed_ms"] = 3100
    payload["routes"][0]["slow_2s"] = True
    self.assertEqual(audit_host(runner=runner)["status"], "ROUTES_DEGRADED")


 def test_partial_or_untrusted_receipt_fails_closed(self):
    payload = {"schema": SCHEMA, "read_only": True, "source_status": "BOUNDED",
               "routes": [{"path": "/", "http": 200, "nav": True, "content": True}],
               "sources": []}
    result = audit_host(runner=lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    self.assertEqual(result["status"], "NO_VERIFICADO")
    self.assertEqual(result["routes"], [])

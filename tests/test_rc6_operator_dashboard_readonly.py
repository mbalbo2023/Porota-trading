"""The internal probe exports status evidence, never HTML or credentials."""
import json
from types import SimpleNamespace
from unittest import TestCase

from scripts.rc6_operator_dashboard_readonly import CHILD, POSTCLOSE_ROUTES, ROUTES, SCHEMA, audit_host


class OperatorAuditTests(TestCase):
 def test_embedded_probe_compiles(self):
    compile(CHILD, "<dashboard-probe>", "exec")
    self.assertEqual(len(POSTCLOSE_ROUTES), 50)
    self.assertEqual(len(set(POSTCLOSE_ROUTES)), 50)
    self.assertIn("/trading/cauciones", POSTCLOSE_ROUTES)

 def test_sanitized_audit_receipt_has_exact_routes_and_no_page_content(self):
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL", "source_status": "BOUNDED",
               "routes": [{"path": path, "http": 200, "nav": True, "content": True,
                           "secret": "must never leave host"} for path in ROUTES],
               "sources": [{"component": "PPI_MARKETDATA", "state": "VERDE",
                            "checked_age_seconds": 2, "last_success_age_seconds": 2,
                            "detail": "must never leave host"}],
               "source_sync_status": "BOUNDED", "history_cutoff_repair": "INCOMPLETE",
               "source_sync": [{"source": "PPI_PRODUCTION_HISTORY", "state": "AMARILLO",
                                "last_attempt_age_seconds": 100, "last_success_age_seconds": 200,
                                "detail": "must never leave host"}],
               "equity_exits": {"status": "BOUNDED", "sampled": 9, "truncated": False,
                                "reasons": {"EOD_PAPER": 8, "STOP_PAPER": 1,
                                            "private-reason": 99}, "secret": "must never leave host"},
               "paper_today": {"status": "BOUNDED", "art_date": "2026-10-15", "sampled": 12,
                               "opened_today": 5, "closed_today": 4, "wins_today": 2,
                               "invalid_net_pnl": 0, "families_closed": {"ACCIONES": 4, "private": 1},
                               "equity_close_reasons_today": {"EOD_PAPER": 3, "STOP_PAPER": 1},
                               "secret": "must never leave host"},
               "caucion_attempt": {"status": "BOUNDED", "sampled": 1, "latest_age_seconds": 42,
                                   "latest_today": True, "latest_result": "PLACED_SIMULATED",
                                   "has_paper_id": True, "today_attempts": 1,
                                   "worker_status": "BOUNDED", "worker_attempts_today": 1,
                                   "worker_latest_today": True, "worker_latest_result": "HOLD",
                                   "secret": "must never leave host"}}
    def runner(argv, **kwargs):
        assert argv[:4] == ["docker", "exec", "porota_production_dashboard", "python"]
        assert kwargs["timeout"] == 150
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    result = audit_host(runner=runner)
    self.assertEqual(result["status"], "ROUTES_VERIFIED")
    self.assertEqual(len(result["routes"]), len(ROUTES))
    self.assertEqual(result["source_sync"][0]["last_attempt_age_seconds"], 100)
    self.assertEqual(result["history_cutoff_repair"], "INCOMPLETE")
    self.assertEqual(result["equity_exits"], {"status": "BOUNDED", "sampled": 9,
                      "truncated": False, "reasons": {"EOD_PAPER": 8, "STOP_PAPER": 1}})
    self.assertEqual(result["paper_today"]["families_closed"], {"ACCIONES": 4})
    self.assertEqual(result["paper_today"]["wins_today"], 2)
    self.assertEqual(result["caucion_attempt"]["today_attempts"], 1)
    self.assertEqual(result["caucion_attempt"]["worker_latest_result"], "HOLD")
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
    self.assertEqual(len(audit_host(runner=runner)["routes"]), 50)
    payload["history_cutoff_repair"] = "COMPLETE; secret"
    payload["source_sync"][0]["last_success_age_seconds"] = -1
    redacted = audit_host(runner=runner)
    self.assertEqual(redacted["history_cutoff_repair"], "NO_VERIFICADO")
    self.assertIsNone(redacted["source_sync"][0]["last_success_age_seconds"])


 def test_partial_or_untrusted_receipt_fails_closed(self):
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL", "source_status": "BOUNDED",
               "routes": [{"path": "/", "http": 200, "nav": True, "content": True}],
               "sources": []}
    result = audit_host(runner=lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    self.assertEqual(result["status"], "NO_VERIFICADO")
    self.assertEqual(result["routes"], [])

"""A331 Action and streamed host PPI History evidence: no live IO in tests."""

import base64
import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a331_host_ppi_history_readonly as a331


def request(identity=None, day="2026-10-30"):
    target = identity or ["GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA"]
    encoded = base64.b64encode(json.dumps([target], separators=(",", ":")).encode()).decode()
    return encoded, day, day


class FakeReader:
    def __init__(self, key, secret, *, budget, consumer, priority, rows=None,
                 login_error=None, history_error=None, http_allowed=2, blocked=0,
                 audit=None, unexpected_route=False):
        self.key = key
        self.secret = secret
        self.budget = budget
        self.consumer = consumer
        self.priority = priority
        self.rows = rows if rows is not None else [
            {"date": "2026-10-30T16:00:00Z", "price": 100, "volume": 23}]
        self.login_error = login_error
        self.history_error = history_error
        self.metrics = {"http_allowed": http_allowed, "http_blocked": blocked,
                        "login_calls": 1}
        self.calls = []
        self.closed = False
        self.audit = audit
        self.unexpected_route = unexpected_route

    def login_once(self):
        self.calls.append("login")
        if self.audit:
            self.audit("POST", "/api/1.0/account/loginapi", "ALLOWED")
        if self.login_error:
            raise self.login_error

    def history(self, ticker, instrument_type, settlement, date_from, date_to):
        self.calls.append(("history", ticker, instrument_type, settlement,
                           date_from, date_to))
        if self.audit:
            self.audit("GET", "/api/1.0/marketdata/search", "ALLOWED")
            if self.unexpected_route:
                self.audit("GET", "/api/1.0/account/accounts", "ALLOWED")
        if self.history_error:
            raise self.history_error
        return self.rows

    def close(self):
        self.closed = True


class TestA331HostHistoryReadonly(unittest.TestCase):
    def setUp(self):
        self.target, self.day = a331.preflight(*request())

    def factory(self, **overrides):
        made = []
        def create(*args, **kwargs):
            reader = FakeReader(*args, **kwargs, **overrides)
            made.append(reader)
            return reader
        return create, made

    def test_one_art_day_positive_retrospective_never_pit_or_paper(self):
        factory, readers = self.factory()
        result = a331.capture_one(self.target, self.day, ("secret-key", "secret-pass"),
                                  factory, clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual("EVIDENCE_ONLY_NO_GO", result["state"])
        self.assertEqual(1, result["rows_in_day"])
        self.assertEqual(1, result["total_provider_rows"])
        self.assertEqual(2, result["provider_requests"])
        self.assertEqual(1, result["history_invocations"])
        self.assertEqual(0, result["causal_pit_rows_verified"])
        self.assertEqual(0, result["paper_sim_rows_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["raw_provider_payload_persisted"])
        self.assertEqual("RETROSPECTIVE_COUNTERFACTUAL_ONLY", result["source_knowledge"])
        self.assertEqual("NOT_IN_PPI_HISTORY_RESPONSE", result["row_currency"])
        self.assertEqual("NOT_IN_PPI_HISTORY_RESPONSE", result["row_price_basis"])
        self.assertEqual("REQUEST_ONLY_NOT_IN_HISTORY_ROWS", result["row_identity"])
        self.assertEqual("REQUEST_ONLY_NOT_IN_HISTORY_ROWS", result["row_settlement"])
        self.assertEqual("NOT_IN_PPI_HISTORY_RESPONSE", result["row_volume_kind"])
        self.assertTrue(readers[0].closed)
        self.assertIs(readers[0].budget, False)
        self.assertEqual("RC6_A331_HOST_HISTORY_ONESHOT", readers[0].consumer)
        self.assertEqual(["login", readers[0].calls[1]], readers[0].calls)
        call = readers[0].calls[1]
        self.assertEqual(("history", "GGAL", "ACCIONES", "INMEDIATA"), call[:4])
        self.assertEqual("2026-10-30T00:00:00-03:00", call[4].isoformat())
        self.assertEqual("2026-10-30T23:59:59.999999-03:00", call[5].isoformat())
        self.assertEqual(0, result["real_orders_sent"])
        self.assertEqual("THIS_A331_ACTION_ONLY", result["real_orders_scope"])
        self.assertEqual("PINNED_CODE_ONLY", result["paper_mode_evidence"])
        self.assertEqual(0, result["host_db_writes"])
        self.assertEqual(0, result["history_v2_writes"])
        self.assertTrue(a331.verify_sanitized_receipt(result, self.target, self.day))

    def test_public_artifact_allowlist_rejects_sdk_secret_injection_or_extra_fields(self):
        factory, _ = self.factory()
        good = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertTrue(a331.verify_sanitized_receipt(good, self.target, self.day))
        for name, value in (("reason", "ApiSecret=PRIVATE_TOKEN"),
                            ("requested_identity", ["SECRET_INJECTED"]),
                            ("real_orders_sent", 1), ("paper_sim_rows_verified", 1),
                            ("source_knowledge", "CAUSAL_PIT_VERIFIED"),
                            ("real_orders_scope", "ALL_HOST_LEDGER_VERIFIED"),
                            ("paper_mode_evidence", "RUNTIME_PROCESS_MEASURED"),
                            ("canonical_payload_sha256", "not-a-sha"),
                            ("rows_in_day", 1000000), ("raw_provider_payload_persisted", True)):
            forged = copy.deepcopy(good)
            forged[name] = value
            self.assertFalse(a331.verify_sanitized_receipt(forged, self.target, self.day), name)
        forged = dict(good, raw_provider_secret="EXFILTRATE")
        self.assertFalse(a331.verify_sanitized_receipt(forged, self.target, self.day))

    def test_ppi_family_adapter_maps_on_and_etf_not_internal_group_name(self):
        for family, expected in (("OBLIGACIONES", "ON"), ("ETFS", "ETF"),
                                 ("FCI", "FCI"), ("CAUCIONES", "CAUCIONES")):
            identity, day = a331.preflight(*request(
                ["TEST", family, "BYMA", "ARS", "INMEDIATA"]))
            factory, readers = self.factory()
            result = a331.capture_one(identity, day, ("key", "secret"), factory)
            self.assertEqual(expected, result["ppi_instrument_type"])
            self.assertEqual(expected, readers[0].calls[1][2])

    def test_absent_existing_container_credentials_calls_no_provider(self):
        factory = mock.Mock(side_effect=AssertionError("NO_PROVIDER_ON_ABSENT_CREDS"))
        result = a331.capture_one(self.target, self.day, None, factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("EXISTING_CONTAINER_CREDENTIALS_ABSENT", result["reason"])
        self.assertEqual(0, result["provider_requests"])
        self.assertEqual(0, result["history_invocations"])
        factory.assert_not_called()

    def test_auth_exception_does_not_leak_secret_or_raw_payload(self):
        factory, readers = self.factory(login_error=RuntimeError(
            "TOKEN_PRIVATE_API_SECRET_IN_EXCEPTION"), http_allowed=1)
        output = io.StringIO()
        with redirect_stdout(output):
            result = a331.capture_one(self.target, self.day, ("top-secret", "password"),
                                      factory)
        exported = json.dumps(result) + output.getvalue()
        self.assertEqual("NO_VERIFICADO", result["state"])
        for secret in ("TOKEN_PRIVATE", "top-secret", "password", "SECRET_IN_EXCEPTION"):
            self.assertNotIn(secret, exported)
        self.assertTrue(readers[0].closed)

    def test_sdk_print_is_suppressed(self):
        class Chatty(FakeReader):
            def login_once(self):
                print("THIS_SHOULD_NEVER_LEAK")
                super().login_once()
            def history(self, *args):
                print("RAW_SECRET_QUOTE_SHOULD_NEVER_LEAK")
                return super().history(*args)
        output = io.StringIO()
        with redirect_stdout(output):
            result = a331.capture_one(self.target, self.day, ("key", "secret"),
                                      lambda *a, **kw: Chatty(*a, **kw))
        self.assertEqual("EVIDENCE_ONLY_NO_GO", result["state"])
        self.assertEqual("", output.getvalue())

    def test_explicit_sdk_guard_blocked_is_negative_even_when_rows_exist(self):
        factory, _ = self.factory(blocked=1)
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("PPI_HTTP_ROUTE_BUDGET_OR_GUARD_UNVERIFIED", result["reason"])

    def test_malicious_sdk_performs_extra_http_is_negative(self):
        factory, _ = self.factory(http_allowed=7)
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])

    def test_unverified_http_request_count_is_negative(self):
        factory, _ = self.factory(http_allowed=0)
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])

    def test_unexpected_account_read_route_is_negative_even_if_sdk_returns_data(self):
        factory, _ = self.factory(unexpected_route=True, http_allowed=3)
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("PPI_EXACT_HISTORY_GET_ROUTE_NOT_ATTESTED", result["reason"])

    def test_empty_exact_day_is_negative(self):
        factory, _ = self.factory(rows=[])
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("EMPTY_EXACT_ART_DAY", result["reason"])

    def test_rows_other_day_cannot_verify_requested_day(self):
        factory, _ = self.factory(rows=[{"date": "2026-10-31T16:00:00Z", "price": 500}])
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual(0, result["rows_in_day"])
        self.assertEqual("A331_PROVIDER_ROWS_OUTSIDE_REQUESTED_DAY", result["reason"])

    def test_mixed_target_day_and_outside_day_is_all_negative(self):
        factory, _ = self.factory(rows=[{"date": "2026-10-30T16:00:00Z", "price": 10},
                                       {"date": "2026-10-31T16:00:00Z", "price": 20}])
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("A331_PROVIDER_ROWS_OUTSIDE_REQUESTED_DAY", result["reason"])
        self.assertEqual(0, result["rows_in_day"])

    def test_provider_naive_timestamp_requires_timezone(self):
        factory, _ = self.factory(rows=[{"date": "2026-10-30T16:00:00", "price": 100}])
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("A331_PPI_ROW_CLOCK_UNVERIFIED", result["reason"])

    def test_provider_over_500_rows_is_negative(self):
        factory, _ = self.factory(rows=[{"date": "2026-10-30T16:00:00Z"}] * 501)
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("A331_PROVIDER_ROW_BUDGET_OR_SHAPE_INVALID", result["reason"])

    def test_provider_over_256kb_is_negative(self):
        factory, _ = self.factory(rows=[{"date": "2026-10-30T16:00:00Z",
                                         "unexpected": "x" * (a331.MAX_PAYLOAD_BYTES + 10)}])
        result = a331.capture_one(self.target, self.day, ("key", "secret"), factory)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("A331_PROVIDER_PAYLOAD_BUDGET_EXCEEDED", result["reason"])

    def test_single_target_and_exact_day_only(self):
        valid, first, last = request()
        self.assertEqual(self.target, a331.preflight(valid, first, last)[0])
        with self.assertRaisesRegex(ValueError, "ONE_HISTORICAL_ART_DAY_REQUIRED"):
            a331.preflight(valid, first, "2026-10-29")
        with self.assertRaisesRegex(ValueError, "ONE_HISTORICAL_ART_DAY_REQUIRED"):
            a331.preflight(valid, "2026-11-02", "2026-11-02")

    def test_oversized_or_ambiguous_targets_rejected_before_auth(self):
        target = ["GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA"]
        for data in ([], [target, target], [target[:4]], ["GGAL"]):
            encoded = base64.b64encode(json.dumps(data).encode()).decode()
            with self.assertRaises(ValueError):
                a331.preflight(encoded, "2026-10-30", "2026-10-30")
        with self.assertRaises(ValueError):
            a331.preflight(base64.b64encode(b"z" * 513).decode(),
                           "2026-10-30", "2026-10-30")

    def test_injection_shape_and_wrong_currency_rejected_before_auth(self):
        for target in (["GGAL;curl", "ACCIONES", "BYMA", "ARS", "INMEDIATA"],
                       ["GGAL", "ACCIONES", "BYMA", "EUR", "INMEDIATA"],
                       ["GGAL", "INTERNAL_UNKNOWN", "BYMA", "ARS", "INMEDIATA"],
                       ["GGAL", "ACCIONES", "B@DMA", "ARS", "INMEDIATA"]):
            with self.assertRaisesRegex(ValueError, "EXACT_IDENTITY_INVALID"):
                a331.preflight(*request(target))

    def test_relaxed_os_file_limit_is_forbidden(self):
        with mock.patch.object(a331.resource, "setrlimit") as limit:
            a331._read_only_process_limits()
        limit.assert_any_call(a331.resource.RLIMIT_FSIZE, (0, 0))
        limit.assert_any_call(a331.resource.RLIMIT_CORE, (0, 0))

    def test_container_main_without_existing_ppi_credentials_emits_only_negative_receipt(self):
        module = types.SimpleNamespace(MODE="PRODUCTION_PAPER", REAL_ORDER_CAPABILITY="BLOCKED")
        target, first, last = request()
        env = {"RC6_A331_EXPECTED_PRODUCT_SHA": a331.SHA_PRODUCT,
               "RC6_A331_TARGET_B64": target, "RC6_A331_FROM_DAY": first,
               "RC6_A331_TO_DAY": last}
        stdout = io.StringIO()
        with mock.patch.object(a331, "_read_only_process_limits"), \
                mock.patch.dict(sys.modules, {"_version": module}), \
                mock.patch.dict("os.environ", env, clear=True), redirect_stdout(stdout):
            code = a331.main()
        receipt = json.loads(stdout.getvalue().strip())
        self.assertEqual(2, code)
        self.assertEqual("NO_VERIFICADO", receipt["state"])
        self.assertEqual("EXISTING_CONTAINER_CREDENTIALS_ABSENT", receipt["reason"])
        self.assertEqual(0, receipt["provider_requests"])
        self.assertEqual(0, receipt["history_v2_writes"])
        self.assertNotIn("api_key", stdout.getvalue().lower())

    def test_product_mode_guard_prevents_live_reader_even_with_credentials(self):
        module = types.SimpleNamespace(MODE="LIVE", REAL_ORDER_CAPABILITY="ENABLED")
        target, first, last = request()
        env = {"RC6_A331_EXPECTED_PRODUCT_SHA": a331.SHA_PRODUCT,
               "RC6_A331_TARGET_B64": target, "RC6_A331_FROM_DAY": first,
               "RC6_A331_TO_DAY": last, "PPI_API_KEY": "untrusted",
               "PPI_API_SECRET": "untrusted"}
        stdout = io.StringIO()
        with mock.patch.object(a331, "_read_only_process_limits"), \
                mock.patch.dict(sys.modules, {"_version": module}), \
                mock.patch.dict("os.environ", env, clear=True), redirect_stdout(stdout):
            code = a331.main()
        receipt = json.loads(stdout.getvalue().strip())
        self.assertEqual(2, code)
        self.assertEqual("NO_VERIFICADO", receipt["state"])
        self.assertEqual(0, receipt["provider_requests"])

    def test_action_is_manual_main_only_and_legacy_runner_path_remains(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" /
                    "rc6-history-fresh-offhost-evidence.yml").read_text()
        must = ("github.event_name == 'workflow_dispatch'",
                "github.ref == 'refs/heads/main'",
                "github.repository == 'mbalbo2023/Porota-trading'",
                "inputs.execution_mode == 'DROPLET_EXISTING_CREDENTIALS'",
                "inputs.execution_mode == 'RUNNER_EXISTING'",
                "RC6_EXPECTED_PRODUCT_SHA: 7e1e744d6ca9ab30c51b5747dd4467af5ece0145",
                "RC6_EXPECTED_IMAGE_ID: sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b",
                "-o StrictHostKeyChecking=yes", "-o IdentitiesOnly=yes",
                "sudo -n timeout 45 docker exec -i", "python -B -",
                "< scripts/rc6_a331_host_ppi_history_readonly.py",
                "persist-credentials: false", "retention-days: 7",
                "A331_PREFLIGHT_OR_SSH_FAILED_BEFORE_SANITIZATION",
                "verify_sanitized_receipt(result, identity, day)")
        self.assertTrue(all(item in workflow for item in must))
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("/Order/", workflow)
        self.assertNotIn("docker restart", workflow)
        self.assertNotIn("docker build", workflow)
        self.assertNotIn("docker run", workflow)


if __name__ == "__main__":
    unittest.main()

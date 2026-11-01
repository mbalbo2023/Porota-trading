"""Adversarial A338 Book tests: all offline, no PPI/SSH/host access."""

import base64
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a338_host_ppi_book_atomic as a338


class TestA343MountedBookKeypair(unittest.TestCase):
    def test_book_uses_pinned_observer_secret_not_untrusted_env_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "ppi_production.json"
            file.write_text('{"api_key":"KEY_ONLY_TEST","api_secret":"SECRET_ONLY_TEST"}')
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {
                    "PPI_PRODUCTION_SECRET_FILE": str(file),
                    "PPI_API_KEY": "BAD_ENV", "PPI_API_SECRET": "BAD_ENV"}), \
                    redirect_stdout(stdout):
                self.assertEqual(("KEY_ONLY_TEST", "SECRET_ONLY_TEST"),
                                 a338.existing_container_keypair())
            self.assertEqual("", stdout.getvalue())

    def test_book_mount_missing_bad_json_duplicate_symlink_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "ppi_production.json"
            for bad in ("", "[]", '{"api_key":"KEY"}',
                        '{"api_key":"X","api_key":"Y","api_secret":"SECRET"}',
                        "x" * 4097):
                with self.subTest(length=len(bad)):
                    file.write_text(bad)
                    with mock.patch.dict(os.environ, {"PPI_PRODUCTION_SECRET_FILE": str(file)}):
                        self.assertIsNone(a338.existing_container_keypair())
            file.write_text('{"api_key":"KEY","api_secret":"SECRET"}')
            alias = Path(folder) / "alias.json"
            alias.symlink_to(file)
            with mock.patch.dict(os.environ, {"PPI_PRODUCTION_SECRET_FILE": str(alias)}):
                self.assertIsNone(a338.existing_container_keypair())
            with mock.patch.dict(os.environ, {
                    "PPI_PRODUCTION_SECRET_FILE": str(Path(folder) / "missing.json"),
                    "PPI_API_KEY": "BAD_ENV", "PPI_API_SECRET": "BAD_ENV"}):
                self.assertIsNone(a338.existing_container_keypair())

    def test_book_main_only_passes_mounted_keypair_to_budgeted_reader(self):
        captured = []
        def read(target, credentials, budget, factory):
            captured.append((credentials, budget, factory))
            return a338._receipt(target)
        output = io.StringIO()
        reader_module = types.SimpleNamespace(ProductionMarketReader=object())
        with mock.patch.dict(os.environ, {
                    "RC6_A338_EXPECTED_PRODUCT_SHA": a338.PRODUCT_SHA,
                    "RC6_A338_TARGET_B64": encoded()}, clear=True), \
                mock.patch.dict(sys.modules, {
                    "_version": types.SimpleNamespace(MODE="PRODUCTION_PAPER",
                                                      REAL_ORDER_CAPABILITY="BLOCKED"),
                    "bd_ppi_readonly_guard": reader_module}), \
                mock.patch.object(a338.resource, "setrlimit"), \
                mock.patch.object(a338, "existing_container_keypair",
                                  return_value=("MOUNTED_KEY", "MOUNTED_SECRET")), \
                mock.patch.object(a338, "_existing_shared_budget", return_value="BUDGET"), \
                mock.patch.object(a338, "capture_one", side_effect=read), \
                redirect_stdout(output):
            self.assertEqual(2, a338.main())
        self.assertEqual([(("MOUNTED_KEY", "MOUNTED_SECRET"), "BUDGET",
                           reader_module.ProductionMarketReader)], captured)
        self.assertNotIn("MOUNTED_SECRET", output.getvalue())


TARGET = ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"]
WHEN = datetime.now(timezone.utc).replace(microsecond=0)


def encoded(identity=None):
    raw = json.dumps([identity or TARGET], separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


class SharedBudget:
    def __init__(self, *, admitted=True, lease="lease-opaque-123", fail_at=None):
        self.admitted = admitted
        self.lease = lease
        self.fail_at = fail_at
        self.calls = []

    def acquire(self, endpoint, **kwargs):
        self.calls.append(("acquire", endpoint, kwargs))
        if self.fail_at == "acquire":
            raise RuntimeError("TOKEN_NOT_FOR_PUBLIC_OUTPUT")
        return {"allowed": self.admitted, "lease": self.lease,
                "reason": "PPI_BUDGET_CAPACITY_DENIED"}

    def start(self, lease):
        self.calls.append(("start", lease))
        if self.fail_at == "start":
            raise RuntimeError("PRIVATE_DATA_IN_START_ERROR")

    def finish(self, lease, **outcome):
        self.calls.append(("finish", lease))
        if self.fail_at == "finish":
            raise RuntimeError("PRIVATE_DATA_IN_FINISH_ERROR")

    def wire_scope(self, lease):
        self.calls.append(("scope", lease))
        return nullcontext()

    def report_error(self, endpoint, code):
        self.calls.append(("report_error", endpoint, code))


class FakeReader:
    def __init__(self, key, secret, *, budget, consumer, priority, audit,
                 response=None, fail_at=None, extra_route=False):
        self.key = key
        self.secret = secret
        self.budget = budget
        self.consumer = consumer
        self.priority = priority
        self.audit = audit
        self.response = response if response is not None else {
            "date": (WHEN - timedelta(seconds=12)).isoformat(),
            "bids": [{"price": 100, "quantity": 1}],
            "offers": [{"price": 101, "quantity": 2}],
        }
        self.fail_at = fail_at
        self.extra_route = extra_route
        self.closed = False
        self.calls = []

    def login_once(self):
        self.calls.append("login")
        self.audit("POST", "/api/1.0/account/loginapi", "ALLOWED")
        if self.fail_at == "login":
            raise RuntimeError("ApiSecret=PRIVATE_VALUE")

    def book(self, symbol, kind, settlement):
        self.calls.append(("book", symbol, kind, settlement))
        self.audit("GET", "/api/1.0/marketdata/book", "ALLOWED")
        if self.extra_route:
            self.audit("GET", "/api/1.0/account/accounts", "ALLOWED")
        row = self.budget.acquire("book", consumer=self.consumer, priority=self.priority)
        if not row["allowed"]:
            raise RuntimeError("PPI_BACKPRESSURE_SAFE_DENY")
        lease = row["lease"]
        try:
            self.budget.start(lease)
            with self.budget.wire_scope(lease):
                if self.fail_at == "book":
                    raise RuntimeError("raw_token=SECRET_PROVIDER_ERROR")
                return self.response
        finally:
            self.budget.finish(lease)

    def close(self):
        self.closed = True
        if self.fail_at == "close":
            raise RuntimeError("ACCOUNT_SECRET_LEAK_IN_CLOSE")


class TestA338BookAtomic(unittest.TestCase):
    def setUp(self):
        self.identity = a338.preflight(encoded())

    def capture(self, *, shared=None, credentials=("KEY_PRIVATE", "SECRET_PRIVATE"),
                response=None, fail_at=None, extra_route=False, clock=None):
        shared = SharedBudget() if shared is None else shared
        readers = []

        def factory(*args, **kwargs):
            item = FakeReader(*args, **kwargs, response=response,
                fail_at=fail_at, extra_route=extra_route)
            readers.append(item)
            return item

        receipt = a338.capture_one(self.identity, credentials, shared, factory,
            clock=clock or (lambda: WHEN), limiter=nullcontext)
        return receipt, shared, readers

    def test_one_atomic_leased_book_no_pit_or_orders(self):
        result, budget, readers = self.capture()
        self.assertEqual("EVIDENCE_ONLY_NO_GO", result["state"])
        self.assertEqual("PPI_BOOK_SDK_PARSED_DIGEST_ONLY", result["reason"])
        self.assertEqual(1, result["book_invocations"])
        self.assertEqual(1, result["book_gets"])
        self.assertEqual(1, result["lease_acquires"])
        self.assertTrue(result["lease_started"] and result["lease_finished"])
        self.assertEqual("SDK_PARSED_BOOK_UNVERIFIED_NOT_WIRE_RAW", result["provenance"])
        self.assertEqual((1, 1), (result["bid_levels"], result["offer_levels"]))
        self.assertEqual(12000, result["provider_age_ms"])
        self.assertFalse(result["source_pit_verified"])
        self.assertFalse(result["independent_ed25519_verified"])
        self.assertFalse(result["paper_sim_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["raw_provider_payload_persisted"])
        self.assertEqual(0, result["real_orders_sent_by_action"])
        self.assertEqual(0, result["trading_db_writes_by_action"])
        self.assertEqual(0, result["ppi_watch_mutations_by_action"])
        self.assertTrue(a338.verify_sanitized_receipt(result, self.identity))
        self.assertEqual(["acquire", "start", "scope", "finish"], [x[0] for x in budget.calls])
        self.assertEqual("DISCOVERY", budget.calls[0][2]["priority"])
        self.assertEqual("RC6_A338_BOOK_DIAGNOSTIC", budget.calls[0][2]["consumer"])
        self.assertEqual(["login", ("book", "GGAL", "ACCIONES", "A-24HS")], readers[0].calls)
        self.assertTrue(readers[0].closed)

    def test_missing_credential_and_missing_budget_do_not_authenticate(self):
        for creds, shared, reason in [
            (None, SharedBudget(), "EXISTING_CONTAINER_CREDENTIALS_ABSENT"),
            (("key", "secret"), None, "PPI_BUDGET_EXISTING_SIDECAR_NOT_CERTIFIED"),
        ]:
            def forbidden(*args, **kwargs):
                raise AssertionError("NEVER_CREATE_READER")
            result = a338.capture_one(self.identity, creds, shared, forbidden)
            self.assertEqual("NO_VERIFICADO", result["state"])
            self.assertEqual(reason, result["reason"])
            self.assertEqual(0, result["book_gets"])

    def test_denial_and_null_lease_never_authorize_wire(self):
        for shared in [SharedBudget(admitted=False), SharedBudget(lease=None),
                       SharedBudget(lease=""), SharedBudget(fail_at="acquire")]:
            result, budget, _ = self.capture(shared=shared)
            self.assertEqual("NO_VERIFICADO", result["state"])
            self.assertFalse(result["lease_started"])
            self.assertFalse(result["lease_finished"])
            self.assertEqual(1, len([x for x in budget.calls if x[0] == "acquire"]))
            self.assertTrue(a338.verify_sanitized_receipt(result, self.identity))

    def test_second_book_or_other_endpoint_fails_before_shared_budget(self):
        shared = SharedBudget()
        one = a338.OneGetAtomicBudget(shared)
        admitted = one.acquire("book", consumer="RC6_A338_BOOK_DIAGNOSTIC", priority="DISCOVERY")
        self.assertTrue(admitted["allowed"])
        for endpoint, consumer, priority in [
            ("book", "RC6_A338_BOOK_DIAGNOSTIC", "DISCOVERY"),
            ("current", "RC6_A338_BOOK_DIAGNOSTIC", "DISCOVERY"),
            ("book", "EXIT_READER", "EXIT_CRITICAL"),
        ]:
            result = one.acquire(endpoint, consumer=consumer, priority=priority)
            self.assertFalse(result["allowed"])
        self.assertEqual(1, len(shared.calls))

    def test_missing_provider_offset_stale_or_future_fail_closed(self):
        cases = [
            "2026-10-30T13:00:00", "not-a-clock",
            (WHEN - timedelta(seconds=121)).isoformat(),
            (WHEN + timedelta(milliseconds=1)).isoformat(),
        ]
        for value in cases:
            receipt, _, _ = self.capture(response={"date": value, "bids": [], "offers": []})
            self.assertEqual("NO_VERIFICADO", receipt["state"], value)
            self.assertIsNone(receipt["first_known_at_utc"])
            self.assertIsNone(receipt["sdk_parsed_sha256"])
            self.assertTrue(a338.verify_sanitized_receipt(receipt, self.identity))

    def test_clock_boundary_120_seconds_is_accepted(self):
        payload = {"date": (WHEN - timedelta(seconds=120)).isoformat(), "bids": [], "offers": []}
        receipt, _, _ = self.capture(response=payload)
        self.assertEqual("EVIDENCE_ONLY_NO_GO", receipt["state"])
        self.assertEqual(120000, receipt["provider_age_ms"])

    def test_reject_oversize_payload_and_bounded_levels(self):
        cases = [
            {"date": WHEN.isoformat(), "bids": [], "offers": [], "junk": "x" * (128 * 1024)},
            {"date": WHEN.isoformat(), "bids": [{}] * 201, "offers": []},
            {"date": WHEN.isoformat(), "bids": {}, "offers": []},
        ]
        for payload in cases:
            result, _, _ = self.capture(response=payload)
            self.assertEqual("NO_VERIFICADO", result["state"])
            self.assertIsNone(result["sdk_parsed_sha256"])

    def test_secret_bearing_exceptions_never_escape_into_stdout_or_receipt(self):
        for reason in ("login", "book", "start", "finish", "close"):
            shared = SharedBudget(fail_at=reason if reason in {"start", "finish"} else None)
            out = io.StringIO()
            with redirect_stdout(out):
                result, _, _ = self.capture(shared=shared,
                    fail_at=reason if reason in {"login", "book", "close"} else None)
            exported = json.dumps(result) + out.getvalue()
            self.assertEqual("NO_VERIFICADO", result["state"], reason)
            for secret in ("PRIVATE_VALUE", "KEY_PRIVATE", "SECRET_PRIVATE",
                           "raw_token", "ACCOUNT_SECRET_LEAK", "PRIVATE_DATA_IN_START_ERROR"):
                self.assertNotIn(secret, exported)

    def test_unexpected_http_route_makes_sanitized_failure(self):
        result, _, _ = self.capture(extra_route=True)
        self.assertEqual("NO_VERIFICADO", result["state"])
        self.assertEqual("PPI_BOOK_GET_SCOPE_OR_COUNT_INVALID", result["reason"])
        self.assertTrue(a338.verify_sanitized_receipt(result, self.identity))

    def test_allowlist_rejects_forged_success_independent_signer_or_trading(self):
        result, _, _ = self.capture()
        for key, bad in [
            ("independent_ed25519_verified", True), ("source_pit_verified", True),
            ("real_orders_sent_by_action", 1), ("execution_allowed", True),
            ("budget_snapshot", "UNBOUNDED"), ("sdk_parsed_bytes", 200000),
            ("state", "VALIDADO_RUNTIME"), ("reason", "SECRET=123"),
            ("provider_age_ms", -1), ("book_gets", 2),
            ("requested_identity", ["EVIL", *TARGET[1:]]),
        ]:
            forged = dict(result, **{key: bad})
            self.assertFalse(a338.verify_sanitized_receipt(forged, self.identity), key)
        self.assertFalse(a338.verify_sanitized_receipt(dict(result, raw_price=100), self.identity))
        self.assertFalse(a338.verify_sanitized_receipt(dict(result,
            state="NO_VERIFICADO", reason="PPI_BOOK_AUTH_OR_PROVIDER_FAILURE"), self.identity))

    def test_wire_body_is_bounded_before_sdk_parses_it(self):
        class Response:
            def __init__(self, url, chunks):
                self.url = url
                self.chunks = chunks

            def iter_content(self, chunk_size=1, decode_unicode=False):
                yield from self.chunks

        original = Response.iter_content
        parent = types.ModuleType("requests")
        models = types.ModuleType("requests.models")
        models.Response = Response
        parent.models = models
        with mock.patch.dict(sys.modules, {"requests": parent, "requests.models": models}):
            with a338._cap_book_body():
                with self.assertRaisesRegex(ValueError, "PPI_BOOK_PAYLOAD_BUDGET_EXCEEDED"):
                    list(Response("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book",
                        [b"x" * (64 * 1024), b"x" * (64 * 1024 + 1)]).iter_content())
                self.assertEqual(1, len(list(Response(
                    "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Search",
                    [b"x" * (128 * 1024 + 1)]).iter_content())))
                self.assertEqual(2, len(list(Response(
                    "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book",
                    [b"x" * 100, b"x" * 100]).iter_content())))
        self.assertIs(Response.iter_content, original)

    def test_action_keeps_history_and_book_separate_manual_only(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/rc6-history-fresh-offhost-evidence.yml").read_text()
        for literal in (
            "- DROPLET_EXISTING_CREDENTIALS", "- DROPLET_ATOMIC_BOOK",
            "inputs.execution_mode == 'DROPLET_ATOMIC_BOOK'",
            "github.event_name == 'workflow_dispatch'",
            "github.ref == 'refs/heads/main'",
            "group: rc6-unified-paper-deploy", "cancel-in-progress: false",
            "RC6_EXPECTED_IMAGE_ID: sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b",
            "sudo -n timeout 45 docker exec -i", "python -B -",
            "Strict SSH pin and one Book GET through existing global PPI budget",
            "A338_DROPLET_BOOK=", "no_raw_provider_data_uploaded",
            "tests/test_rc6_a331_host_ppi_history_readonly.py",
            "tests/test_rc6_a338_host_ppi_book_atomic.py",
            "Destroy runner private SSH and stdio files",
        ):
            self.assertIn(literal, workflow)
        self.assertNotIn("on:\n  push:", workflow)

    def test_shared_sidecar_requires_existing_observed_inode_not_snapshot_authorization(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            sidecar = folder / "ppi-budget/global.sqlite"
            sidecar.parent.mkdir()
            created = []

            class RuntimeBudget:
                def __init__(self, path):
                    self.path = path

            workspace = types.ModuleType("cg_paper_workspace")
            workspace.database_path = lambda: folder / "trading.sqlite"
            workspace.artifact_root = lambda database: folder
            ppi = types.ModuleType("rc6_ppi_global_budget")
            ppi.RuntimePPIBudget = RuntimeBudget
            ppi.runtime_budget_snapshot = lambda database: {
                "status": "OBSERVED", "exit_service": {"lower_suspended": False}}
            def construct(database):
                created.append(str(database))
                return RuntimeBudget(sidecar)
            ppi.budget_from_environment = construct
            with mock.patch.dict(sys.modules, {
                "cg_paper_workspace": workspace, "rc6_ppi_global_budget": ppi}):
                self.assertIsNone(a338._existing_shared_budget())
                self.assertFalse(created, "missing sidecar must not bootstrap a new file")
                sidecar.write_bytes(b"SQLite test fixture only")
                self.assertIsInstance(a338._existing_shared_budget(), RuntimeBudget)
                ppi.runtime_budget_snapshot = lambda database: {
                    "status": "DEGRADED", "exit_service": {"lower_suspended": True}}
                self.assertIsNone(a338._existing_shared_budget())
                ppi.runtime_budget_snapshot = lambda database: {
                    "status": "OBSERVED", "exit_service": {"lower_suspended": False}}
                sidecar.unlink()
                sidecar.symlink_to(folder / "trading.sqlite")
                self.assertIsNone(a338._existing_shared_budget())

    def test_exact_identity_contract_and_ppi_aliases(self):
        for family, adapter in (("ETFS", "ETF"), ("OBLIGACIONES", "ON"),
                                ("BONOS", "BONOS"), ("CAUCIONES", "CAUCIONES")):
            identity = a338.preflight(encoded(["TEST", family, "BYMA", "ARS", "INMEDIATA"]))
            self.assertEqual(adapter, a338._receipt(identity)["ppi_type"])
        for malformed in [[], [TARGET, TARGET], [["X", "ACCIONES"]],
                          [["GGAL", "acc", "BYMA", "ARS", "INMEDIATA"]],
                          [["GGAL;rm", "ACCIONES", "BYMA", "ARS", "INMEDIATA"]]]:
            raw = base64.b64encode(json.dumps(malformed).encode()).decode()
            with self.assertRaises(ValueError):
                a338.preflight(raw)


if __name__ == "__main__":
    unittest.main()

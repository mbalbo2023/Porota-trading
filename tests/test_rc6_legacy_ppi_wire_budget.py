"""A651: legacy PPI SDK physical sends obey the same global budget.

Only fake sessions and fake budget receipts are used; no HTTP, credentials,
broker, host-sidecar, or PPI Watch mutation is possible in this test module.
"""

import ast
from contextlib import contextmanager
import importlib
from importlib.metadata import version
import importlib.util
import os
import socket
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Backpressure(RuntimeError):
    pass


def legacy_module(provider):
    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    ppi_parent = ModuleType("ppi_client")
    ppi_module = ModuleType("ppi_client.ppi")
    ppi_module.PPI = object
    db = ModuleType("ac_db")
    status = ModuleType("bb_runtime_status")
    status.record_event = lambda *_args: None
    status.epoch_iso = str
    budget_module = ModuleType("rc6_ppi_global_budget")
    budget_module.BudgetBackpressure = Backpressure
    budget_module.budget_from_environment = provider
    imports = {
        "dotenv": dotenv, "ppi_client": ppi_parent,
        "ppi_client.ppi": ppi_module, "ac_db": db,
        "bb_runtime_status": status, "rc6_ppi_global_budget": budget_module,
    }
    with patch.dict(sys.modules, imports):
        spec = importlib.util.spec_from_file_location("_test_rc6_legacy_ppi", ROOT / "c_ppi_client.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, imports


class FakeResponse:
    def __init__(self, status=200, scope=None):
        self.status_code = status
        self.scope = scope
        self.body_seen_inside_lease = False

    @property
    def content(self):
        self.body_seen_inside_lease = bool(self.scope and self.scope.active)
        return b"{}"


class FakeAdapter:
    def __init__(self, scope=None, *, status=200, retry_total=0):
        self.max_retries = SimpleNamespace(total=retry_total)
        self.calls = []
        self.scope = scope
        self.status = status

    def send(self, request, **kwargs):
        self.calls.append((request.method, request.url, kwargs))
        return FakeResponse(self.status, self.scope)

    def close(self):
        return None


class FakeSession:
    def __init__(self, adapter):
        self.adapters = {"https://": adapter, "http://": adapter}

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter


class FakeRest:
    def __init__(self, session):
        self.session = session

    def get_session(self):
        return self.session


class FakeSDK:
    def __init__(self, rest):
        self._PPI__apiClient = SimpleNamespace(get_rest_client=lambda: rest)


class FakeBudget:
    def __init__(self, admitted=True):
        self.admitted = admitted
        self.active = False
        self.calls = []

    def acquire(self, endpoint, *, consumer, priority):
        self.calls.append(("acquire", endpoint, consumer, priority))
        if not self.admitted:
            return {"allowed": False, "reason": "PPI_BUDGET_EXHAUSTED", "lease": None}
        return {"allowed": True, "lease": f"lease-{len(self.calls)}"}

    @contextmanager
    def wire_scope(self, lease):
        self.active = True
        try:
            yield
        finally:
            self.active = False

    def start(self, lease):
        self.calls.append(("start", lease))

    def finish(self, lease, **kwargs):
        self.calls.append(("finish", lease, kwargs))


CURRENT = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current"
BOOK = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book"


def req(path, *, method="GET"):
    return SimpleNamespace(url=path, method=method)


class LegacyPPIBudgetTest(TestCase):
    def test_pinned_real_sdk_marketdata_current_book_reach_denied_adapter_no_network(self):
        """Assert the actual 1.3.0 Current/Book methods traverse RestClient.

        CI has the pinned SDK; local pure-source runs do not. A fake requests
        adapter plus a socket veto ensure this test can NEVER reach PPI.
        """
        try:
            sdk_module = importlib.import_module("ppi_client.ppi")
        except ModuleNotFoundError:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                self.fail("CI_DEPENDENCY_CLOSURE_PPI_SDK_NOT_INSTALLED")
            return
        self.assertEqual(version("ppi-client"), "1.3.0")
        ppi = sdk_module.PPI(sandbox=False)
        rest = ppi._PPI__apiClient.get_rest_client()
        session = rest.get_session()
        original_adapter = FakeAdapter()
        session.mount("https://", original_adapter)
        rest.get_session = lambda: session
        module, imports = legacy_module(lambda: None)
        module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
            module.ResilientPPIClient), ppi)
        guarded = session.get_adapter(CURRENT)
        self.assertIsInstance(guarded, module._BudgetedPPIAdapter)
        observed = []
        original_guarded_send = guarded.send

        def observe_send(request, **kwargs):
            try:
                return original_guarded_send(request, **kwargs)
            except Backpressure as exc:
                observed.append((request.method, request.url, str(exc)))
                raise

        guarded.send = observe_send
        attempted_network = []

        def block_network(*_args, **_kwargs):
            attempted_network.append(1)
            raise AssertionError("PPI_REAL_NETWORK_FORBIDDEN_IN_SDK_CONTRACT_TEST")

        with patch.dict(sys.modules, {"rc6_ppi_global_budget": imports["rc6_ppi_global_budget"]}), \
             patch.object(socket.socket, "connect", block_network), \
             patch.object(socket.socket, "connect_ex", block_network):
            for method in (ppi.marketdata.current, ppi.marketdata.book):
                try:
                    method("AL30", "BONOS", "INMEDIATA")
                except Backpressure as exc:
                    self.assertEqual(str(exc), "PPI_SHARED_BUDGET_UNAVAILABLE")
                except Exception as exc:
                    self.fail("PPI_SDK_MARKETDATA_PATH_NOT_ATTESTED:" + type(exc).__name__)

        self.assertEqual(len(observed), 2, "PPI_SDK_CURRENT_BOOK_MUST_REACH_GUARDED_SEND")
        self.assertTrue(all(reason == "PPI_SHARED_BUDGET_UNAVAILABLE"
                            for _method, _url, reason in observed))
        self.assertTrue(observed[0][1].lower().startswith(CURRENT.lower()))
        self.assertTrue(observed[1][1].lower().startswith(BOOK.lower()))
        self.assertEqual(original_adapter.calls, [])
        self.assertEqual(attempted_network, [])

    def test_pinned_real_sdk_session_contract_offhost_without_network(self):
        """Actions installs the pinned SDK; local source-only runs use fakes.

        Merely instantiating the SDK requires no credentials or HTTP. The
        injected fake adapter rejects the first wire send before networking.
        """
        try:
            sdk_module = importlib.import_module("ppi_client.ppi")
            import requests
        except ModuleNotFoundError:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                self.fail("CI_DEPENDENCY_CLOSURE_PPI_SDK_NOT_INSTALLED")
            # The separate pure-fake tests still assert fail-closed behavior.
            return
        self.assertEqual(version("ppi-client"), "1.3.0")
        ppi = sdk_module.PPI(sandbox=False)
        rest = ppi._PPI__apiClient.get_rest_client()
        session = rest.get_session()
        self.assertIsInstance(session, requests.Session)

        original_adapter = FakeAdapter()
        session.mount("https://", original_adapter)
        rest.get_session = lambda: session
        module, imports = legacy_module(lambda: None)
        with patch.dict(sys.modules, imports):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), ppi)
            guarded = rest.get_session().get_adapter(CURRENT)
            self.assertIsInstance(guarded, module._BudgetedPPIAdapter)
            with self.assertRaisesRegex(Backpressure, "PPI_SHARED_BUDGET_UNAVAILABLE"):
                guarded.send(req(CURRENT))
        self.assertEqual(original_adapter.calls, [])
        unrelated = requests.Session()
        try:
            self.assertNotIsInstance(unrelated.get_adapter(CURRENT), module._BudgetedPPIAdapter)
        finally:
            unrelated.close()

    def test_no_authority_denies_current_and_book_before_physical_send(self):
        module, imports = legacy_module(lambda: None)
        original = FakeAdapter()
        rest = FakeRest(FakeSession(original))
        with patch.dict(sys.modules, imports):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), FakeSDK(rest))
            guarded = rest.get_session().adapters["https://"]
            for url in (CURRENT, BOOK):
                with self.assertRaisesRegex(Backpressure, "PPI_SHARED_BUDGET_UNAVAILABLE"):
                    guarded.send(req(url))
        self.assertEqual(original.calls, [])

    def test_approved_current_and_book_each_get_one_distinct_lease(self):
        budget = FakeBudget()
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter(scope=budget)
        rest = FakeRest(FakeSession(original))
        with patch.dict(sys.modules, imports):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), FakeSDK(rest))
            guarded = rest.get_session().adapters["https://"]
            responses = [guarded.send(req(url)) for url in (CURRENT, BOOK)]
        self.assertEqual(len(original.calls), 2)
        self.assertTrue(all(r.body_seen_inside_lease for r in responses))
        self.assertEqual([c[1] for c in budget.calls if c[0] == "acquire"],
                         ["current", "book"])
        self.assertEqual(sum(c[0] == "start" for c in budget.calls), 2)
        self.assertEqual(sum(c[0] == "finish" for c in budget.calls), 2)

    def test_budget_denial_is_not_retried_or_sent(self):
        budget = FakeBudget(admitted=False)
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter(scope=budget)
        rest = FakeRest(FakeSession(original))
        with patch.dict(sys.modules, imports):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), FakeSDK(rest))
            with self.assertRaisesRegex(Backpressure, "PPI_BUDGET_EXHAUSTED"):
                rest.get_session().adapters["https://"].send(req(CURRENT))
        self.assertEqual(original.calls, [])
        self.assertEqual(len(budget.calls), 1)

    def test_legacy_baseline_allowed_without_actual_lease_is_off_wire(self):
        class Unleased(FakeBudget):
            def acquire(self, *_args, **_kwargs):
                self.calls.append(("baseline",))
                return {"allowed": True, "lease": None}

        budget = Unleased()
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter(scope=budget)
        with patch.dict(sys.modules, imports):
            with self.assertRaisesRegex(Backpressure, "PPI_BUDGET_LEASE_UNVERIFIED"):
                module._BudgetedPPIAdapter(original).send(req(BOOK))
        self.assertEqual(original.calls, [])

    def test_catalog_history_bond_estimate_and_write_are_off_wire(self):
        budget = FakeBudget()
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter()
        rest = FakeRest(FakeSession(original))
        with patch.dict(sys.modules, imports):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), FakeSDK(rest))
            guarded = rest.get_session().adapters["https://"]
            for path in ("SearchInstrument", "Search", "Bonds/Estimate"):
                with self.assertRaisesRegex(Backpressure, "PPI_MARKETDATA_ENDPOINT_BUDGET_UNVERIFIED"):
                    guarded.send(req("https://clientapi.portfoliopersonal.com/api/1.0/MarketData/" + path))
            with self.assertRaisesRegex(Backpressure, "PPI_CONFIGURATION_ENDPOINT_BUDGET_UNVERIFIED"):
                guarded.send(req("https://clientapi.portfoliopersonal.com/api/1.0/Configuration/Settlements"))
            with self.assertRaisesRegex(Backpressure, "PPI_SDK_ENDPOINT_NOT_GOVERNED"):
                guarded.send(req("https://clientapi.portfoliopersonal.com/api/1.0/Orders/Confirm", method="POST"))
        self.assertEqual(original.calls, [])
        self.assertEqual(budget.calls, [])

    def test_login_only_passes_without_market_budget_lease(self):
        budget = FakeBudget()
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter()
        rest = FakeRest(FakeSession(original))
        with patch.dict(sys.modules, imports):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), FakeSDK(rest))
            response = rest.get_session().adapters["https://"].send(req(
                "https://clientapi.portfoliopersonal.com/api/1.0/Account/LoginApi", method="POST"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(original.calls), 1)
        self.assertEqual(budget.calls, [])

    def test_http_429_consumes_one_lease_and_reports_circuit_metadata(self):
        budget = FakeBudget()
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter(scope=budget, status=429)
        with patch.dict(sys.modules, imports):
            response = module._BudgetedPPIAdapter(original).send(req(BOOK))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(original.calls), 1)
        receipts = [e for e in budget.calls if e[0] == "finish"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0][2], {"status_code": 429})

    def test_physical_send_failure_finishes_reserved_lease_without_second_send(self):
        budget = FakeBudget()
        module, imports = legacy_module(lambda: budget)

        class BrokenAdapter(FakeAdapter):
            def send(self, request, **kwargs):
                self.calls.append((request.method, request.url, kwargs))
                raise ConnectionError("fake-only")

        original = BrokenAdapter()
        with patch.dict(sys.modules, imports):
            with self.assertRaisesRegex(ConnectionError, "fake-only"):
                module._BudgetedPPIAdapter(original).send(req(CURRENT))
        self.assertEqual(len(original.calls), 1)
        receipts = [e for e in budget.calls if e[0] == "finish"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0][2], {
            "status_code": None, "error_code": "PPI_SDK_WIRE_ERROR"})

    def test_backpressure_is_not_retried_by_legacy_call_wrapper(self):
        module, imports = legacy_module(lambda: None)
        client = object.__new__(module.ResilientPPIClient)
        client.is_authenticated = lambda: True
        client._circuit_allow_call = lambda: True
        client._refrescar_token_si_hace_falta = lambda: None
        module._throttle = lambda: None
        attempts = []

        def rejected():
            attempts.append(1)
            raise Backpressure("PPI_SHARED_BUDGET_UNAVAILABLE")

        with patch.dict(sys.modules, imports):
            result = client._call_with_retry(rejected, "get_book(AL30)")
        self.assertIsNone(result)
        self.assertEqual(attempts, [1])

    def test_untrusted_host_http_retrying_adapter_and_stream_cannot_send(self):
        budget = FakeBudget()
        module, imports = legacy_module(lambda: budget)
        original = FakeAdapter(retry_total=2)
        with patch.dict(sys.modules, imports):
            guard = module._BudgetedPPIAdapter(original)
            for url in ("http://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current",
                        "https://other.example.com/api/1.0/MarketData/Current"):
                with self.assertRaises(Backpressure):
                    guard.send(req(url))
            with self.assertRaisesRegex(Backpressure, "PPI_SDK_TRANSPORT_RETRIES_UNVERIFIED"):
                guard.send(req(CURRENT))
            original.max_retries.total = 0
            with self.assertRaisesRegex(Backpressure, "PPI_SDK_STREAMING_READ_UNGOVERNED"):
                guard.send(req(CURRENT), stream=True)
        self.assertEqual(original.calls, [])

    def test_unknown_sdk_session_shape_blocks_login_before_network(self):
        module, _ = legacy_module(lambda: FakeBudget())
        invalid_sdk = SimpleNamespace(_PPI__apiClient=SimpleNamespace(get_rest_client=lambda: object()))
        with self.assertRaisesRegex(RuntimeError, "PPI_SDK_SESSION_TRANSPORT_UNVERIFIED"):
            module.ResilientPPIClient._install_sdk_wire_budget(object.__new__(
                module.ResilientPPIClient), invalid_sdk)

    def test_authentication_installs_wire_guard_before_sdk_login(self):
        tree = ast.parse((ROOT / "c_ppi_client.py").read_text(encoding="utf-8"))
        clazz = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ResilientPPIClient")
        method = next(n for n in clazz.body if isinstance(n, ast.FunctionDef) and n.name == "_login")
        code = ast.unparse(method)
        self.assertLess(code.index("self._install_sdk_wire_budget(candidate)"),
                        code.index("candidate.account.login_api(self.api_key, self.api_secret)"))


if __name__ == "__main__":
    import unittest
    unittest.main()

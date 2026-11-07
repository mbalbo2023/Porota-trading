"""A696 OFFHOST singleton registry regression, zero SDK/network/host calls.

The active slot must be bound to the base class, not the dynamic subclass.
Different owners in one Python process must not construct competing readers.
"""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "rc6_ppi_shared_session_owner.py"


class _FakeFeed:
    def __init__(self, **kwargs):
        self.consumers = {}

    def register_consumer(self, name, *, watched_five_keys):
        self.consumers[name] = list(watched_five_keys)


class _FakeReader:
    budget_enabled = True

    def __init__(self, observed):
        self._observed = observed
        observed["clients"] += 1

    def login_once(self):
        self._observed["logins"] += 1

    def market_only_sdk_transport(self, *, instrument_factory):
        return object()

    def capture_guarded_native(self, *args, **kwargs):
        raise AssertionError("NO_PROVIDER_GET")

    def close(self):
        self._observed["closes"] += 1


class _FakeAuthority:
    def permits(self, action, *, five_key=None):
        return True  # synthetic permission only, never an external witness


def _import_owner_with_offhost_stubs():
    feed = types.ModuleType("rc6_ppi_shared_feed")
    feed.PPISharedMarketFeed = _FakeFeed
    feed.PPIReadOnlyNativeReceiptStore = lambda instance: object()
    feed.ReadOnlyUnixFeedServer = lambda *args, **kwargs: object()
    sdk = types.ModuleType("rc6_ppi_shared_producer")
    sdk.PPISharedSDKProducer = lambda **kwargs: object()
    rest = types.ModuleType("rc6_ppi_shared_rest_producer")
    rest.MarketOnlyRESTProducer = lambda **kwargs: object()
    dependencies = {feed.__name__: feed, sdk.__name__: sdk, rest.__name__: rest}
    with mock.patch.dict(sys.modules, dependencies):
        spec = importlib.util.spec_from_file_location("rc6_a696_owner_offhost", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.PPISharedSessionOwner, module.SessionOwnerBlocked


class TestSingleProcessRegistry(unittest.TestCase):
    def setUp(self):
        self.Base, self.Blocked = _import_owner_with_offhost_stubs()
        self.observed = {"clients": 0, "logins": 0, "closes": 0}

    def _owner(self, cls):
        auth = _FakeAuthority()
        return cls(
            reader_factory=lambda: _FakeReader(self.observed),
            instrument_factory=lambda *args: args,
            stream_authority=auth,
            external_capacity_authority=auth,
            catalog_authority=auth,
            ppi_catalog_rows=[], catalog_five_keys=[],
            catalog_source_digest="f" * 64,
            consumer_five_keys={"test": []},
            socket_path=Path("/nonexistent/a696-no-socket"),
        )

    @staticmethod
    def _close_if_open(owner):
        if owner._state == "OPEN":
            owner.close()

    def test_subclass_open_first_blocks_base_class_reader(self):
        class ChildOwner(self.Base):
            pass

        child = self._owner(ChildOwner)
        base = self._owner(self.Base)
        try:
            child.open_once()
            with self.assertRaisesRegex(self.Blocked, "PPI_PROCESS_ALREADY_HAS_MARKET_OWNER"):
                base.open_once()
            self.assertEqual(self.observed["clients"], 1)
            self.assertEqual(self.observed["logins"], 1)
        finally:
            self._close_if_open(base)
            self._close_if_open(child)

        # Legitimate succession may open only after the previous guard closes.
        base.open_once()
        try:
            self.assertEqual(self.observed["clients"], 2)
            self.assertEqual(self.observed["logins"], 2)
        finally:
            base.close()
        self.assertEqual(self.observed["closes"], 2)

    def test_base_and_subclass_thread_race_has_one_winner(self):
        class ChildOwner(self.Base):
            pass

        base, child = self._owner(self.Base), self._owner(ChildOwner)
        barrier = threading.Barrier(3)

        def attempt(owner):
            barrier.wait(timeout=5)
            try:
                owner.open_once()
                return "OPEN"
            except self.Blocked as exc:
                return str(exc)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(attempt, base)
                second = executor.submit(attempt, child)
                barrier.wait(timeout=5)
                outcomes = [first.result(timeout=5), second.result(timeout=5)]
            self.assertEqual(outcomes.count("OPEN"), 1)
            self.assertIn("PPI_PROCESS_ALREADY_HAS_MARKET_OWNER", outcomes)
            self.assertEqual(self.observed["clients"], 1)
            self.assertEqual(self.observed["logins"], 1)
        finally:
            self._close_if_open(base)
            self._close_if_open(child)

    def test_subclass_factory_failure_releases_common_registry_slot(self):
        class ChildOwner(self.Base):
            pass

        child = self._owner(ChildOwner)
        child._reader_factory = lambda: (_ for _ in ()).throw(
            RuntimeError("FAKE_READER_INIT_FAILED"))
        base = self._owner(self.Base)
        with self.assertRaisesRegex(RuntimeError, "FAKE_READER_INIT_FAILED"):
            child.open_once()
        self.assertEqual(self.observed["clients"], 0)
        self.assertIsNone(self.Base._active_owner)
        base.open_once()
        try:
            self.assertEqual(self.observed["clients"], 1)
            self.assertEqual(self.observed["logins"], 1)
        finally:
            base.close()


if __name__ == "__main__":
    unittest.main()

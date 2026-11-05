"""Additional A708 OFFHOST coverage on exact #997 source, no SDK/network."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import threading
import unittest

# Import the exact #997 test's offhost-only helpers from a file path, avoiding
# tests-package/module-name ambiguity on hosted runners. This never imports SDK.
_original = Path(__file__).with_name("test_rc6_a696_ppi_registry_subclass.py")
_spec = importlib.util.spec_from_file_location("rc6_a696_stubs_a708", _original)
_stubs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stubs)
_FakeAuthority = _stubs._FakeAuthority
_FakeReader = _stubs._FakeReader
_import_owner_with_offhost_stubs = _stubs._import_owner_with_offhost_stubs


class TestA708RegistryAdversarial(unittest.TestCase):
    def setUp(self):
        self.Base, self.Blocked = _import_owner_with_offhost_stubs()
        self.counts = {"clients": 0, "logins": 0, "closes": 0}
        self.auth = _FakeAuthority()

    def owner(self, cls, factory=None):
        return cls(reader_factory=factory or (lambda: _FakeReader(self.counts)),
                   instrument_factory=lambda *args: args,
                   stream_authority=self.auth,
                   external_capacity_authority=self.auth,
                   catalog_authority=self.auth,
                   ppi_catalog_rows=[], catalog_five_keys=[],
                   catalog_source_digest="f" * 64,
                   consumer_five_keys={"a708": []},
                   socket_path=Path("/nonexistent/a708-no-socket"))

    def test_grandchild_first_blocks_every_ancestor(self):
        class Child(self.Base):
            pass
        class GrandChild(Child):
            pass
        first, middle, base = self.owner(GrandChild), self.owner(Child), self.owner(self.Base)
        first.open_once()
        try:
            for candidate in (middle, base):
                with self.assertRaisesRegex(self.Blocked, "PPI_PROCESS_ALREADY_HAS_MARKET_OWNER"):
                    candidate.open_once()
            self.assertEqual((self.counts["clients"], self.counts["logins"]), (1, 1))
        finally:
            first.close()

    def test_base_first_blocks_subclasses_and_shadow_attribute(self):
        class Child(self.Base):
            pass
        base, child = self.owner(self.Base), self.owner(Child)
        base.open_once()
        try:
            Child._active_owner = None  # adversarial shadow cannot release base slot
            with self.assertRaisesRegex(self.Blocked, "PPI_PROCESS_ALREADY_HAS_MARKET_OWNER"):
                child.open_once()
            self.assertIs(self.Base._active_owner, base)
        finally:
            base.close()

    def test_eight_contenders_one_reader_and_login(self):
        class Child(self.Base):
            pass
        class GrandChild(Child):
            pass
        owners = [self.owner(c) for c in (self.Base, Child, GrandChild, Child,
                                          self.Base, GrandChild, Child, self.Base)]
        barrier = threading.Barrier(len(owners) + 1)
        def attempt(owner):
            barrier.wait(timeout=4)
            try:
                owner.open_once()
                return "OPEN"
            except self.Blocked as exc:
                return str(exc)
        try:
            with ThreadPoolExecutor(max_workers=len(owners)) as pool:
                futures = [pool.submit(attempt, owner) for owner in owners]
                barrier.wait(timeout=4)
                results = [f.result(timeout=4) for f in futures]
            self.assertEqual(results.count("OPEN"), 1)
            self.assertEqual(self.counts["clients"], 1)
            self.assertEqual(self.counts["logins"], 1)
        finally:
            for o in owners:
                if o._state == "OPEN":
                    o.close()

    def test_idempotent_open_same_owner_without_second_login(self):
        owner = self.owner(self.Base)
        try:
            self.assertIs(owner.open_once(), owner)
            self.assertIs(owner.open_once(), owner)
            self.assertEqual((self.counts["clients"], self.counts["logins"]), (1, 1))
        finally:
            owner.close()

    def test_login_failure_closed_reader_releases_registry(self):
        class FailingLoginReader(_FakeReader):
            def login_once(self):
                raise RuntimeError("FAKE_AUTH_FAILURE")
        failing = self.owner(self.Base, factory=lambda: FailingLoginReader(self.counts))
        with self.assertRaisesRegex(RuntimeError, "FAKE_AUTH_FAILURE"):
            failing.open_once()
        self.assertIsNone(self.Base._active_owner)
        self.assertEqual(self.counts["closes"], 1)
        successor = self.owner(self.Base)
        successor.open_once()
        successor.close()
        self.assertEqual(self.counts["logins"], 1)

    def test_close_failure_keeps_global_guard_fail_closed(self):
        class UnrestorableReader(_FakeReader):
            def close(self):
                raise RuntimeError("FAKE_HTTP_GUARD_CANNOT_RESTORE")
        first = self.owner(self.Base, factory=lambda: UnrestorableReader(self.counts))
        first.open_once()
        with self.assertRaisesRegex(RuntimeError, "FAKE_HTTP_GUARD_CANNOT_RESTORE"):
            first.close()
        second = self.owner(self.Base)
        with self.assertRaisesRegex(self.Blocked, "PPI_PROCESS_ALREADY_HAS_MARKET_OWNER"):
            second.open_once()
        self.assertEqual(self.counts["clients"], 1)


if __name__ == "__main__":
    unittest.main()

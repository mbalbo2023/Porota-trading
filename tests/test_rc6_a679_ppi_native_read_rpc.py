"""A679: native PPI REST receipts can be shared without another provider GET.

OFFHOST synthetic fixtures only. No SDK import, account login, PPI network,
production database or PPI Watch integration is permitted in this suite.
"""

from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from rc6_ppi_shared_feed import (
    EventRejected, PPISharedMarketFeed, PPIReadOnlyNativeReceiptStore,
    ReadOnlyUnixFeedServer, ReadOnlyUnixFeedClient, SharedFeedUnavailable,
)


T0 = datetime(2026, 11, 4, 16, 30, tzinfo=timezone.utc)
FIVE = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-48HS")
OTHER = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")


class FakeClock:
    def __init__(self):
        self.now = T0
        self.mono = 1_000_000_000_000

    def utcnow(self):
        return self.now

    def mono_ns(self):
        return self.mono

    def advance(self, ms):
        self.now += timedelta(milliseconds=ms)
        self.mono += ms * 1_000_000


def catalog():
    return [
        {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "ARS"},
        {"ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "ARS"},
    ]


class NativeRPCTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.bus = PPISharedMarketFeed(
            ppi_catalog_rows=catalog(), catalog_source_digest="c" * 64,
            max_events=8, max_age_seconds=5,
        )
        self.bus.register_consumer("scanner")
        self.bus.register_consumer("exit_reader", watched_five_keys=[FIVE])
        self.bus.register_consumer("scalping")
        self.bus.register_consumer("watch")
        self.store = PPIReadOnlyNativeReceiptStore(
            self.bus, max_receipts=4, max_age_seconds=5,
            clock=self.clock.utcnow, monotonic_ns=self.clock.mono_ns,
        )

    def publish(self, *, method="book", identity=FIVE, price=100,
                session="a" * 64, guard_receipt="b" * 64):
        at = self.clock.utcnow()
        return self.store.publish_native_rest_receipt(
            method=method, five_key=identity,
            raw_payload={"Date": at.isoformat(), "Ticker": identity[0],
                         "Type": identity[1], "Settlement": identity[4],
                         "Bids": [{"Position": 0, "Price": price, "Quantity": 4}],
                         "Offers": [{"Position": 0, "Price": price + 1, "Quantity": 5}]},
            native_at=at, received_at=at,
            received_monotonic_ns=self.clock.mono_ns(),
            session_fingerprint=session,
            guard_budget_receipt_sha256=guard_receipt,
            capture_type="REST_" + method.upper(),
        )

    def test_no_native_snapshot_is_not_inferred_from_normalized_stream(self):
        res = self.store.read_native(consumer="scanner", method="book", five_key=FIVE)
        self.assertEqual(res["state"], "NO_AUTHENTIC_NATIVE_RECEIPT")
        self.assertEqual(res["provider_calls_by_rpc"], 0)

    def test_original_raw_book_preserved_and_receipts_are_not_paper_authority(self):
        recorded = self.publish()
        res = self.store.read_native(consumer="exit_reader", method="book", five_key=FIVE)
        self.assertEqual(res["state"], "OK")
        self.assertEqual(res["receipt"], recorded)
        self.assertEqual(res["receipt"]["raw_payload"]["Bids"][0]["Price"], 100)
        self.assertEqual(res["receipt"]["ppi_catalog_authority"], "NO_VERIFICADO")
        self.assertIs(res["receipt"]["paper_ready"], False)
        self.assertEqual(res["receipt"]["order_route"], "NOT_PRESENT")
        self.assertEqual(res["provider_calls_by_rpc"], 0)

    def test_five_key_scope_and_consumer_methods_do_not_escalate(self):
        self.publish()
        self.assertEqual(self.store.read_native(
            consumer="watch", method="book", five_key=FIVE)["state"], "DENIED_READ_ONLY")
        self.assertEqual(self.store.read_native(
            consumer="exit_reader", method="current", five_key=FIVE)["state"], "DENIED_READ_ONLY")
        self.assertEqual(self.store.read_native(
            consumer="exit_reader", method="book", five_key=OTHER)["state"], "DENIED_READ_ONLY")
        self.assertEqual(self.store.read_native(
            consumer="scanner", method="place_order", five_key=FIVE)["state"], "DENIED_READ_ONLY")

    def test_search_history_configuration_require_new_budget_evidence(self):
        for method in ("history", "search_instruments", "market_configuration"):
            res = self.store.read_native(consumer="scanner", method=method, five_key=FIVE)
            self.assertEqual(res["state"], "NO_BUDGET_AUTHORITY")
            self.assertEqual(res["provider_calls_by_rpc"], 0)

    def test_stale_exit_native_book_denies_without_last_price_fallback(self):
        self.publish()
        self.clock.advance(5010)
        res = self.store.read_native(consumer="exit_reader", method="book", five_key=FIVE)
        self.assertEqual(res["state"], "PPI_NATIVE_RECEIPT_STALE")

    def test_causal_first_book2_not_later_cherry_picked(self):
        book1 = self.publish(price=100)
        self.clock.advance(200)
        committed = self.clock.utcnow()
        commit_ns = self.clock.mono_ns()
        self.clock.advance(150)
        first = self.publish(price=105)
        self.clock.advance(150)
        self.publish(price=109)
        res = self.store.read_native(
            consumer="exit_reader", method="book", five_key=FIVE,
            book1_receipt_sha256=book1["receipt_sha256"],
            commit_at=committed, commit_monotonic_ns=commit_ns,
        )
        self.assertEqual(res["state"], "OK")
        self.assertEqual(res["receipt"]["receipt_sha256"], first["receipt_sha256"])

    def test_causal_book2_rejects_changed_sdk_session(self):
        book1 = self.publish()
        self.clock.advance(100)
        committed = self.clock.utcnow()
        mono = self.clock.mono_ns()
        self.clock.advance(100)
        self.publish(price=101, session="e" * 64)
        self.clock.advance(100)
        self.publish(price=102, session="a" * 64)
        res = self.store.read_native(
            consumer="exit_reader", method="book", five_key=FIVE,
            book1_receipt_sha256=book1["receipt_sha256"],
            commit_at=committed, commit_monotonic_ns=mono,
        )
        self.assertEqual(res["state"], "PPI_PIT_BOOK2_SESSION_CHANGED")

    def test_unapproved_or_forged_rest_capture_fails_before_cache(self):
        with self.assertRaisesRegex(EventRejected, "PPI_NATIVE_CAPTURE_TYPE_INVALID"):
            self.store.publish_native_rest_receipt(
                method="book", five_key=FIVE, raw_payload={"Date": T0.isoformat()},
                native_at=T0, received_at=T0, received_monotonic_ns=self.clock.mono_ns(),
                session_fingerprint="a" * 64, guard_budget_receipt_sha256="b" * 64,
                capture_type="SDK_STREAM_BOOK",
            )
        with self.assertRaisesRegex(EventRejected, "PPI_NATIVE_BUDGET_RECEIPT_INVALID"):
            self.publish(guard_receipt="INVALID")
        with self.assertRaisesRegex(EventRejected, "PPI_NATIVE_PAYLOAD_UNSAFE"):
            self.store.publish_native_rest_receipt(
                method="book", five_key=FIVE, raw_payload={"account_number": "A"},
                native_at=T0, received_at=T0, received_monotonic_ns=self.clock.mono_ns(),
                session_fingerprint="a" * 64, guard_budget_receipt_sha256="b" * 64,
                capture_type="REST_BOOK",
            )
        with self.assertRaisesRegex(EventRejected, "PPI_NATIVE_CLOCK_NOT_IN_RAW_RECEIPT"):
            self.store.publish_native_rest_receipt(
                method="book", five_key=FIVE,
                raw_payload={"Date": (T0 - timedelta(seconds=1)).isoformat(),
                             "Bids": [], "Offers": []},
                native_at=T0, received_at=T0,
                received_monotonic_ns=self.clock.mono_ns(),
                session_fingerprint="a" * 64, guard_budget_receipt_sha256="b" * 64,
                capture_type="REST_BOOK",
            )

    def test_cache_and_dedup_metadata_are_bounded(self):
        for i in range(6):
            self.publish(price=100 + i)
            self.clock.advance(50)
        stats = self.store.stats()
        self.assertEqual(stats["retained_raw_receipts"], 4)
        self.assertEqual(stats["dropped_raw_receipts"], 2)
        self.assertLessEqual(stats["retained_latest_native_sources"], 4)

    def test_malicious_unhashable_protocol_inputs_are_denied_not_executed(self):
        server = ReadOnlyUnixFeedServer(
            self.bus, socket_path="/tmp/nonrunning-a679.sock",
            native_receipts=self.store,
        )
        for req in (
            {"op": "native_read", "consumer": [], "method": "book",
             "ppi_five_key": list(FIVE)},
            {"op": "native_read", "consumer": "scanner", "method": [],
             "ppi_five_key": list(FIVE)},
            {"op": "native_read", "consumer": "scanner", "method": "book",
             "ppi_five_key": {"bad": "shape"}},
        ):
            self.assertEqual(server.dispatch(req)["state"], "DENIED_READ_ONLY")
        self.assertEqual(self.bus.stats()["provider_calls_by_bus"], 0)

    def test_native_unix_client_validates_raw_digest_and_preserves_denial(self):
        self.publish()
        client = ReadOnlyUnixFeedClient(socket_path="/tmp/nonrunning-a679.sock")
        packet = self.store.read_native(
            consumer="exit_reader", method="book", five_key=FIVE)
        with patch("rc6_ppi_shared_feed.socket.socket") as mocked:
            mocked.return_value.__enter__.return_value.recv.side_effect = [
                (json.dumps(packet) + "\n").encode()]
            self.assertEqual(client.read_native(
                "exit_reader", method="book", five_key=FIVE)["state"], "OK")
        packet["receipt"]["raw_payload"]["Bids"][0]["Price"] = 1
        with patch("rc6_ppi_shared_feed.socket.socket") as mocked:
            mocked.return_value.__enter__.return_value.recv.side_effect = [
                (json.dumps(packet) + "\n").encode()]
            with self.assertRaisesRegex(
                    SharedFeedUnavailable, "PPI_NATIVE_RPC_RECEIPT_INVALID"):
                client.read_native("exit_reader", method="book", five_key=FIVE)
        denial = self.store._state("NO_AUTHENTIC_NATIVE_RECEIPT")
        with patch("rc6_ppi_shared_feed.socket.socket") as mocked:
            mocked.return_value.__enter__.return_value.recv.side_effect = [
                (json.dumps(denial) + "\n").encode()]
            self.assertEqual(client.read_native(
                "exit_reader", method="book", five_key=FIVE)["state"],
                "NO_AUTHENTIC_NATIVE_RECEIPT")

    def test_socket_dispatch_is_inert_without_native_store_and_denies_orders(self):
        server = ReadOnlyUnixFeedServer(self.bus, socket_path="/tmp/nonrunning-a679.sock")
        req = {"op": "native_read", "consumer": "exit_reader", "method": "book",
               "ppi_five_key": list(FIVE)}
        self.assertEqual(server.dispatch(req)["state"], "NO_AUTHENTIC_NATIVE_RECEIPT")
        server2 = ReadOnlyUnixFeedServer(self.bus, socket_path="/tmp/nonrunning-a679.sock",
                                        native_receipts=self.store)
        self.publish()
        self.assertEqual(server2.dispatch(req)["state"], "OK")
        self.assertEqual(server2.dispatch({"op": "place_order"})["state"], "DENIED_READ_ONLY")
        self.assertEqual(self.bus.stats()["provider_calls_by_bus"], 0)


if __name__ == "__main__":
    unittest.main()

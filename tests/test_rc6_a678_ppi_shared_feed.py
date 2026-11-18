"""A678 OFFHOST: shared PPI market-data bus; no provider, orders or Watch IO."""

from __future__ import annotations

import json
import errno
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from rc6_ppi_shared_feed import (
    CatalogIdentityError,
    EventRejected,
    PPISharedMarketFeed,
    ReadOnlyUnixFeedClient,
    ReadOnlyUnixFeedServer,
    SharedFeedUnavailable,
)


UTC = timezone.utc
T0 = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
FIVE = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-48HS")
SESSION = "7" * 64  # synthetic fixture only; NOT provider attestation


def catalog(market="BYMA", currency="ARS"):
    return [{"ticker": "GGAL", "type": "ACCIONES", "market": market,
             "currency": currency}]


def ppi_book(at=T0, *, bid=100.0, ask=101.0, term="A-48HS"):
    return {"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": term,
            "Date": at.isoformat(), "Trade": False,
            "Bids": [{"Position": 1, "Price": bid, "Quantity": 20}],
            "Offers": [{"Position": 1, "Price": ask, "Quantity": 11}],
            # These fields must never leak through the read-only data bus.
            "AuthorizedClient": "UNTRUSTED_EXAMPLE", "AccountNumber": "UNTRUSTED"}


def ppi_trade(at=T0, *, price=100.5):
    return {"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS",
            "Date": at.isoformat(), "Trade": True, "Price": price,
            "VolumeAmount": 10}


def feed(*, capacity=8, slots=3, rows=None):
    source = PPISharedMarketFeed(
        ppi_catalog_rows=catalog() if rows is None else rows,
        catalog_source_digest="f" * 64,
        max_events=capacity,
        max_subscriptions=slots,
        max_age_seconds=10,
    )
    source.register_consumer("watch", watched_five_keys=[FIVE])
    source.register_consumer("scanner", watched_five_keys=[FIVE])
    source.plan_subscription(FIVE, priority="HOT")
    return source


class PPISharedFeedTests(unittest.TestCase):
    def test_one_event_fans_out_to_two_independent_consumers_without_network(self):
        bus = feed()
        event = bus.publish_native_market_event(
            ppi_book(), received_at=T0 + timedelta(milliseconds=100),
            received_monotonic_ns=1000,
        )
        watch = bus.pull("watch", after_sequence=0)
        scanner = bus.pull("scanner", after_sequence=0)
        self.assertEqual(watch["state"], "OK")
        self.assertEqual(scanner["state"], "OK")
        self.assertEqual(watch["events"], scanner["events"])
        self.assertEqual(event["ppi_five_key"], list(FIVE))
        self.assertEqual(event["ppi_catalog_authority"], "NO_VERIFICADO")
        self.assertFalse(event["paper_ready"])
        self.assertEqual(event["kind"], "BOOK")
        self.assertNotIn("AccountNumber", json.dumps(event))
        self.assertNotIn("AuthorizedClient", json.dumps(event))
        self.assertEqual(bus.stats()["provider_calls_by_bus"], 0)

    def test_catalog_ambiguous_four_key_does_not_select_one_arbitrarily(self):
        bus = feed(rows=catalog() + catalog(market="A3", currency="USD"))
        with self.assertRaisesRegex(CatalogIdentityError, "AMBIGUOUS"):
            bus.publish_native_market_event(ppi_book(), received_at=T0,
                                            received_monotonic_ns=1)
        self.assertEqual(bus.stats()["published"], 0)

    def test_missing_five_key_or_unsubscribed_identity_fails_closed(self):
        bus = feed()
        with self.assertRaisesRegex(CatalogIdentityError, "IDENTITY"):
            bus.publish_native_market_event(ppi_book(term="INMEDIATA"),
                                            received_at=T0, received_monotonic_ns=1)
        incomplete = ppi_book()
        incomplete.pop("Type")
        resolved = bus.publish_native_market_event(incomplete, received_at=T0,
                                                   received_monotonic_ns=2)
        self.assertEqual(resolved["ppi_five_key"], list(FIVE))
        self.assertEqual(resolved["type_provenance"],
                         "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE")
        self.assertEqual(resolved["ppi_catalog_authority"], "NO_VERIFICADO")
        self.assertFalse(resolved["paper_ready"])

    def test_missing_native_type_requires_unique_active_subscription_context(self):
        bon = {"ticker": "GGAL", "type": "BONOS", "market": "BYMA",
               "currency": "ARS"}
        bus = feed(rows=catalog() + [bon])
        bus.plan_subscription(("GGAL", "BONOS", "BYMA", "ARS", "A-48HS"),
                              priority="WARM")
        incomplete = ppi_book()
        incomplete.pop("Type")
        with self.assertRaisesRegex(CatalogIdentityError, "CONTEXT_NOT_UNIQUE"):
            bus.publish_native_market_event(incomplete, received_at=T0,
                                            received_monotonic_ns=100)
        self.assertEqual(bus.stats()["published"], 0)
        with self.assertRaisesRegex(CatalogIdentityError, "MARKET_CONFLICT"):
            bus.publish_native_market_event({**ppi_book(), "Market": "A3"},
                                            received_at=T0,
                                            received_monotonic_ns=101)

    def test_rejects_future_stale_nonfinite_or_unbounded_book(self):
        for event, arrival in (
            (ppi_book(), T0 - timedelta(seconds=1)),
            (ppi_book(), T0 + timedelta(seconds=11)),
            (ppi_trade(price=float("nan")), T0),
            ({**ppi_book(), "Bids": [{"Price": 1, "Quantity": 1}] * 41}, T0),
        ):
            with self.subTest(event=str(event)[:40]):
                bus = feed()
                with self.assertRaises(EventRejected):
                    bus.publish_native_market_event(event, received_at=arrival,
                                                    received_monotonic_ns=42)

    def test_duplicate_event_is_idempotent_and_conflicting_same_time_holds(self):
        bus = feed()
        first = bus.publish_native_market_event(ppi_book(), received_at=T0,
                                                received_monotonic_ns=100)
        again = bus.publish_native_market_event(ppi_book(), received_at=T0,
                                                received_monotonic_ns=101)
        self.assertEqual(first["sequence"], again["sequence"])
        self.assertEqual(bus.stats()["published"], 1)
        with self.assertRaisesRegex(EventRejected, "CONFLICT"):
            bus.publish_native_market_event(ppi_book(bid=99), received_at=T0,
                                            received_monotonic_ns=102)
        with self.assertRaisesRegex(EventRejected, "QUARANTINED"):
            bus.publish_native_market_event(ppi_book(T0 + timedelta(seconds=1)),
                                            received_at=T0 + timedelta(seconds=1),
                                            received_monotonic_ns=103)

    def test_book2_requires_strictly_post_commit_native_event_and_new_sha(self):
        bus = feed()
        first = bus.publish_native_market_event(ppi_book(), received_at=T0,
                                                received_monotonic_ns=1_000,
                                                session_fingerprint=SESSION)
        with self.assertRaisesRegex(EventRejected, "BOOK1_NOT_PRE_COMMIT"):
            bus.get_post_commit_book(FIVE, book1_receipt_sha256=first["event_sha256"],
                                     commit_at=T0, commit_monotonic_ns=1_000)
        later = bus.publish_native_market_event(
            ppi_book(T0 + timedelta(milliseconds=300), bid=100.1),
            received_at=T0 + timedelta(milliseconds=400),
            received_monotonic_ns=1_500,
            session_fingerprint=SESSION,
        )
        self.assertEqual(later["event_sha256"], bus.get_post_commit_book(
            FIVE, book1_receipt_sha256=first["event_sha256"],
            commit_at=T0 + timedelta(milliseconds=100),
            commit_monotonic_ns=1_250)["event_sha256"])
        even_later = bus.publish_native_market_event(
            ppi_book(T0 + timedelta(milliseconds=600), bid=100.2),
            received_at=T0 + timedelta(milliseconds=650),
            received_monotonic_ns=1_800,
            session_fingerprint=SESSION,
        )
        self.assertNotEqual(even_later["event_sha256"], later["event_sha256"])
        # Causal PIT cannot choose the most advantageous *later* market Book.
        self.assertEqual(bus.get_post_commit_book(
            FIVE, book1_receipt_sha256=first["event_sha256"],
            commit_at=T0 + timedelta(milliseconds=100),
            commit_monotonic_ns=1_250)["event_sha256"], later["event_sha256"])
        with self.assertRaisesRegex(EventRejected, "POST_COMMIT"):
            bus.get_post_commit_book(FIVE, book1_receipt_sha256=first["event_sha256"],
                                     commit_at=T0 + timedelta(seconds=1),
                                     commit_monotonic_ns=2_000)
        with self.assertRaisesRegex(EventRejected, "BOOK1_NOT_PRE_COMMIT"):
            bus.get_post_commit_book(FIVE, book1_receipt_sha256=first["event_sha256"],
                                     commit_at=T0 - timedelta(seconds=1),
                                     commit_monotonic_ns=500)

    def test_book2_rejects_unattested_or_changed_acquisition_session(self):
        unknown_bus = feed()
        first_unknown = unknown_bus.publish_native_market_event(
            ppi_book(), received_at=T0, received_monotonic_ns=1000)
        unknown_bus.publish_native_market_event(
            ppi_book(T0 + timedelta(milliseconds=400), bid=100.2),
            received_at=T0 + timedelta(milliseconds=450),
            received_monotonic_ns=1400)
        with self.assertRaisesRegex(EventRejected, "SESSION_NOT_PROVEN"):
            unknown_bus.get_post_commit_book(
                FIVE, book1_receipt_sha256=first_unknown["event_sha256"],
                commit_at=T0 + timedelta(milliseconds=100),
                commit_monotonic_ns=1200)
        bus = feed()
        first = bus.publish_native_market_event(
            ppi_book(), received_at=T0, received_monotonic_ns=1000,
            session_fingerprint=SESSION)
        bus.publish_native_market_event(
            ppi_book(T0 + timedelta(milliseconds=400), bid=100.2),
            received_at=T0 + timedelta(milliseconds=450),
            received_monotonic_ns=1400, session_fingerprint="8" * 64)
        with self.assertRaisesRegex(EventRejected, "BOOK2_SESSION_CHANGED"):
            bus.get_post_commit_book(
                FIVE, book1_receipt_sha256=first["event_sha256"],
                commit_at=T0 + timedelta(milliseconds=100),
                commit_monotonic_ns=1200)

    def test_memory_bound_last_event_idempotence_and_receive_clock(self):
        bus = feed(capacity=3)
        for index in range(200):
            at = T0 + timedelta(milliseconds=10 * index)
            bus.publish_native_market_event(ppi_trade(at, price=100 + index),
                                            received_at=at, received_monotonic_ns=index + 1)
        self.assertEqual(bus.stats()["published"], 200)
        self.assertEqual(bus.stats()["retained_source_events"], 3)
        self.assertEqual(bus.stats()["dropped_from_ring"], 197)
        with self.assertRaisesRegex(EventRejected, "OUT_OF_ORDER"):
            bus.publish_native_market_event(ppi_trade(T0), received_at=T0,
                                            received_monotonic_ns=201)
        with self.assertRaisesRegex(EventRejected, "MONOTONIC"):
            at = T0 + timedelta(seconds=3)
            bus.publish_native_market_event(ppi_trade(at, price=301),
                                            received_at=at, received_monotonic_ns=199)

    def test_socket_protocol_dispatch_readonly_without_socket_or_provider(self):
        bus = feed()
        bus.publish_native_market_event(ppi_book(), received_at=T0,
                                        received_monotonic_ns=10)
        server = ReadOnlyUnixFeedServer(bus, socket_path=Path("/not-started.sock"))
        self.assertEqual(server.dispatch({"op": "pull", "consumer": "watch",
                                          "after_sequence": 0})["state"], "OK")
        for request in (
            {"op": "place_order", "consumer": "watch"},
            {"op": "pull", "consumer": "watch", "after_sequence": 0,
             "token": "unowned"},
            {"op": "pull", "consumer": "missing", "after_sequence": 0},
            {"op": "pull", "consumer": "watch", "after_sequence": -1},
        ):
            self.assertEqual(server.dispatch(request)["state"], "DENIED_READ_ONLY")
        self.assertEqual(bus.stats()["provider_calls_by_bus"], 0)

    def test_readonly_client_absent_worker_and_tampered_event_fail_closed(self):
        bus = feed()
        bus.publish_native_market_event(ppi_book(), received_at=T0,
                                        received_monotonic_ns=10)
        client = ReadOnlyUnixFeedClient(socket_path=Path("/not-running/feed.sock"))
        with patch("rc6_ppi_shared_feed.socket.socket", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(SharedFeedUnavailable, "GATEWAY_UNAVAILABLE"):
                client.pull("watch", after_sequence=0)
        packet = bus.pull("watch", after_sequence=0)
        with patch("rc6_ppi_shared_feed.socket.socket") as mocked:
            mocked.return_value.__enter__.return_value.recv.side_effect = [
                (json.dumps(packet) + "\n").encode()]
            self.assertEqual(client.pull("watch", after_sequence=0)["state"], "OK")
        packet["events"][0]["market_payload"]["bids"][0]["price"] = "0.01"
        with patch("rc6_ppi_shared_feed.socket.socket") as mocked:
            mocked.return_value.__enter__.return_value.recv.side_effect = [
                (json.dumps(packet) + "\n").encode()]
            with self.assertRaisesRegex(SharedFeedUnavailable, "EVENT_INVALID"):
                client.pull("watch", after_sequence=0)
        self.assertEqual(bus.stats()["provider_calls_by_bus"], 0)

    def test_queue_overflow_detectable_without_silently_reusing_stale_event(self):
        bus = feed(capacity=2)
        for i in range(3):
            at = T0 + timedelta(milliseconds=100 * i)
            bus.publish_native_market_event(ppi_trade(at, price=100 + i),
                                            received_at=at, received_monotonic_ns=i + 1)
        self.assertEqual(bus.pull("watch", after_sequence=0)["state"], "GAP")
        self.assertEqual(bus.pull("scanner", after_sequence=1)["state"], "OK")
        self.assertEqual(bus.stats()["dropped_from_ring"], 1)

    def test_subscription_capacity_priority_does_not_evict_exit(self):
        bus = PPISharedMarketFeed(ppi_catalog_rows=catalog(),
                                  catalog_source_digest="f" * 64,
                                  max_events=8, max_subscriptions=1)
        self.assertEqual(bus.plan_subscription(FIVE, priority="EXIT_CRITICAL"), "ADMITTED")
        other = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
        self.assertEqual(bus.plan_subscription(other, priority="DISCOVERY"), "CAPACITY_HOLD")
        self.assertEqual(bus.active_subscriptions(), [list(FIVE)])

    def test_unix_socket_exposes_data_only_pull_not_provider_or_order_command(self):
        bus = feed()
        bus.publish_native_market_event(ppi_book(), received_at=T0,
                                        received_monotonic_ns=10)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ppi-market.sock"
            server = ReadOnlyUnixFeedServer(bus, socket_path=path)
            self.assertFalse(path.exists())  # no auto-start
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            try:
                server.start()  # explicitly localhost UNIX socket only
            except OSError as exc:
                if exc.errno in {errno.EPERM, errno.EAFNOSUPPORT, errno.ENOSYS}:
                    self.skipTest("SANDBOX_AF_UNIX_UNAVAILABLE: protocol covered separately")
                raise
            thread.start()
            try:
                def request(payload):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(1)
                        client.connect(str(path))
                        client.sendall((json.dumps(payload) + "\n").encode())
                        return json.loads(client.makefile("rb").readline())
                result = request({"op": "pull", "consumer": "watch", "after_sequence": 0})
                self.assertEqual(result["state"], "OK")
                self.assertEqual(len(result["events"]), 1)
                denied = request({"op": "place_order", "consumer": "watch"})
                self.assertEqual(denied["state"], "DENIED_READ_ONLY")
                self.assertEqual(bus.stats()["provider_calls_by_bus"], 0)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.close()


if __name__ == "__main__":
    unittest.main()

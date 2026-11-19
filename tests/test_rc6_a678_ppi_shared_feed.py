"""A678 OFFHOST: shared PPI market-data bus; no provider, orders or Watch IO."""

from __future__ import annotations

import json
import errno
import os
import socket
import tempfile
import threading
import time
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
    def test_exit_round_observe_and_discard_share_native_wire_thread(self):
        touched = []
        status = {"owner_pid": os.getpid(), "session_generation": "a" * 64}

        def native(**kwargs):
            touched.append(("GET", threading.get_ident()))
            return {"schema": "rc6.ppi.native-acquire-owner.v1", "state": "OK"}

        def observe(**kwargs):
            touched.append(("OBSERVE", threading.get_ident()))
            return {"status": "DEGRADED", "lower_suspended": True}

        def discard(**kwargs):
            touched.append(("DISCARD", threading.get_ident()))

        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"),
            native_acquirer=native, exit_round_observer=observe,
            exit_round_discarder=discard, owner_status=lambda: status,
            supervisor_pid=os.getpid())
        server._server = object()  # live ACL/worker, without sandbox socket
        server._registered_clients["exit_reader"] = (
            os.getpid(), server.process_starttime(os.getpid()))
        server.start_native_priority_router()
        try:
            native_reply = server.dispatch({"op": "native_acquire",
                "consumer": "exit_reader", "method": "book",
                "ppi_five_key": list(FIVE), "priority": "EXIT_CRITICAL"},
                peer_pid=os.getpid())
            round_reply = server.dispatch({"op": "exit_round_observe",
                "consumer": "exit_reader", "elapsed_seconds": 3.25,
                "deadline_seconds": 5.0, "failures": 1}, peer_pid=os.getpid())
            reset_reply = server.dispatch({"op": "exit_round_discard",
                "consumer": "exit_reader"}, peer_pid=os.getpid())
        finally:
            server.stop_native_priority_router()
        assert native_reply["state"] == "OK"
        assert round_reply["state"] == "OK"
        assert round_reply["observation"] == {
            "status": "DEGRADED", "lower_suspended": True}
        assert round_reply["provider_calls_by_rpc"] == 0
        assert reset_reply["state"] == "RESET"
        assert reset_reply["provider_calls_by_rpc"] == 0
        assert [name for name, _ in touched] == ["GET", "OBSERVE", "DISCARD"]
        assert len({tid for _, tid in touched}) == 1

    def test_exit_round_control_rejects_role_spoof_and_unregistered_pid(self):
        invoked = []
        status = {"owner_pid": os.getpid(), "session_generation": "a" * 64}
        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"),
            native_acquirer=lambda **kwargs: None,
            exit_round_observer=lambda **kwargs: invoked.append(kwargs),
            owner_status=lambda: status, supervisor_pid=os.getpid())
        server._server = object()
        server._registered_clients["scanner"] = (
            os.getpid(), server.process_starttime(os.getpid()))
        request = {"op": "exit_round_observe", "consumer": "exit_reader",
                   "elapsed_seconds": 2, "deadline_seconds": 5, "failures": 0}
        denied = server.dispatch(request, peer_pid=os.getpid())
        assert denied["state"] == "PPI_GATEWAY_UNREGISTERED_CLIENT"
        bad_role = server.dispatch({**request, "consumer": "scanner"},
                                   peer_pid=os.getpid())
        assert bad_role["state"] == "PPI_EXIT_ROUND_ROLE_DENIED"
        assert denied["provider_calls_by_rpc"] == bad_role["provider_calls_by_rpc"] == 0
        assert invoked == []

    def test_exit_round_client_requires_pinned_generation_and_exact_role(self):
        client = ReadOnlyUnixFeedClient(socket_path=Path("/nonrunning.sock"))
        client._pinned_owner = (os.getpid(), "a" * 64)
        packets = []

        def recv(packet, **kwargs):
            packets.append(packet)
            return {"schema": "rc6.ppi.native-acquire-owner.v1",
                    "state": "OK" if packet["op"] == "exit_round_observe" else "RESET",
                    "owner_pid": os.getpid(), "session_generation": "a" * 64,
                    "provider_calls_by_rpc": 0,
                    "observation": {"status": "DEGRADED", "lower_suspended": True}
                    if packet["op"] == "exit_round_observe" else None}

        client._request = recv
        observation = client.observe_exit_round("exit_reader",
            elapsed_seconds=3, deadline_seconds=5, failures=1)
        assert observation == {"status": "DEGRADED", "lower_suspended": True}
        client.discard_exit_round_scope("exit_reader")
        assert [packet["op"] for packet in packets] == [
            "exit_round_observe", "exit_round_discard"]
        with self.assertRaises(ValueError):
            client.observe_exit_round("scanner", elapsed_seconds=1,
                                      deadline_seconds=5, failures=0)
        client._request = lambda *a, **kw: {**recv({"op": "exit_round_observe"}),
            "session_generation": "b" * 64}
        with self.assertRaisesRegex(SharedFeedUnavailable,
                                    "PPI_EXIT_ROUND_OWNER_MISMATCH"):
            client.observe_exit_round("exit_reader", elapsed_seconds=1,
                                      deadline_seconds=5, failures=0)

    def test_orphan_gateway_rejects_read_after_supervisor_birth_changes(self):
        observed = []
        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"),
            native_acquirer=lambda **kw: observed.append(kw),
            owner_status=lambda: {"owner_pid": os.getpid(),
                "session_generation": "a" * 64},
            supervisor_pid=os.getpid())
        fake_child = 12345
        server._registered_clients["scanner"] = (fake_child, "child-birth")
        original = server._supervisor_starttime
        with patch.object(ReadOnlyUnixFeedServer, "process_starttime",
                          side_effect=lambda pid: (
                              "new-parent-birth" if pid == os.getpid()
                              else "child-birth")):
            result = server.dispatch({"op": "native_acquire", "consumer": "scanner",
                "method": "book", "ppi_five_key": list(FIVE),
                "priority": "WARM"}, peer_pid=fake_child)
        assert original != "new-parent-birth"
        assert result["state"] == "PPI_GATEWAY_UNREGISTERED_CLIENT"
        assert result["provider_calls_by_rpc"] == 0
        assert not observed

    def test_native_postwire_receipt_failure_never_reports_zero_provider_calls(self):
        invoked = []

        def fail_after_possible_wire(**kwargs):
            invoked.append(kwargs)
            raise RuntimeError("PPI_REST_RECEIPT_UNVERIFIED")

        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"),
            native_acquirer=fail_after_possible_wire,
            owner_status=lambda: {"owner_pid": os.getpid(),
                "session_generation": "a" * 64})
        result = server.dispatch({"op": "native_acquire", "consumer": "scanner",
            "method": "book", "ppi_five_key": list(FIVE),
            "priority": "WARM"})
        assert len(invoked) == 1
        assert result["state"] == "PPI_REST_RECEIPT_UNVERIFIED"
        assert result["provider_calls_by_rpc"] == "NO_VERIFICADO"
        assert "receipt" not in result and "guard_wire_receipt" not in result

        # A verified peer may carry a sanitized denial-with-unknown-wire
        # sentinel; it never becomes a successful quote or fabricated zero.
        client = ReadOnlyUnixFeedClient(socket_path=Path("/nonrunning.sock"))
        client._pinned_owner = (os.getpid(), "a" * 64)
        client._request = lambda *a, **kw: result
        denied = client.acquire_native("scanner", method="book", five_key=FIVE,
                                      priority="WARM")
        assert denied["state"] == "PPI_REST_RECEIPT_UNVERIFIED"
        assert denied["provider_calls_by_rpc"] == "NO_VERIFICADO"

    def test_live_gateway_denies_unpreemptible_seven_get_configuration(self):
        called = []
        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"),
            native_acquirer=lambda **kw: None,
            auxiliary_acquirer=lambda **kw: called.append(kw),
            owner_status=lambda: {"owner_pid": os.getpid(),
                "session_generation": "a" * 64},
            supervisor_pid=os.getpid())
        # Simulate a live listener without creating an AF_UNIX socket in the
        # local sandbox. ProductionMarketReader.market_configuration() does
        # seven sequential SDK GETs and cannot yield to queued EXIT requests.
        server._server = object()
        server._registered_clients["scanner"] = (
            os.getpid(), server.process_starttime(os.getpid()))
        result = server.dispatch({"op": "aux_acquire", "consumer": "scanner",
            "method": "market_configuration", "args": {}}, peer_pid=os.getpid())
        assert result["state"] == "PPI_AUX_CONFIGURATION_MULTI_GET_UNPREEMPTIBLE"
        assert result["provider_calls_by_rpc"] == 0
        assert not called

    def test_supervisor_admin_registration_rejects_parent_pid_reuse(self):
        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"),
            owner_status=lambda: {"owner_pid": os.getpid(),
                "session_generation": "a" * 64},
            supervisor_pid=os.getpid())
        assert server.process_starttime(os.getpid()) is not None
        # Simulate an old parent PID being reused while the gateway process
        # survived. The claimed child birth tick is valid for a new process,
        # but supervisor authority must be tied to its ORIGINAL birth tick.
        with patch.object(ReadOnlyUnixFeedServer,
                          "process_starttime", return_value="999999999"):
            result = server.dispatch({"op": "register_client",
                "consumer": "exit_reader", "client_pid": os.getpid(),
                "client_starttime": "999999999"}, peer_pid=os.getpid())
        assert result["state"] == "PPI_GATEWAY_SUPERVISOR_PEER_DENIED"

    def test_native_exit_role_requires_kernel_peer_pid_registration(self):
        observed = []

        def acquire(**kwargs):
            observed.append(kwargs)
            return {"schema": "rc6.ppi.native-acquire-owner.v1", "state": "OK"}

        server = ReadOnlyUnixFeedServer(feed(),
            socket_path=Path("/nonrunning.sock"), native_acquirer=acquire,
            supervisor_pid=os.getpid())
        exiting = {"op": "native_acquire", "consumer": "exit_reader",
                   "method": "book", "ppi_five_key": list(FIVE),
                   "priority": "EXIT_CRITICAL"}
        assert server.dispatch(exiting, peer_pid=123456789)["state"] == (
            "PPI_GATEWAY_UNREGISTERED_CLIENT")
        start = server.process_starttime(os.getpid())
        assert server.dispatch({"op": "register_client", "consumer": "exit_reader",
            "client_pid": os.getpid(), "client_starttime": start},
            peer_pid=123456789)["state"] == "PPI_GATEWAY_SUPERVISOR_PEER_DENIED"
        registered = server.dispatch({"op": "register_client",
            "consumer": "exit_reader", "client_pid": os.getpid(),
            "client_starttime": start}, peer_pid=os.getpid())
        assert registered["state"] == "REGISTERED"
        assert server.dispatch(exiting, peer_pid=os.getpid())["state"] == "OK"
        assert server.dispatch(exiting, peer_pid=123456789)["state"] == (
            "PPI_GATEWAY_UNREGISTERED_CLIENT")
        assert len(observed) == 1

    def test_native_owner_router_prioritizes_queued_exit_before_warm(self):
        """While one guarded GET is running, queued EXIT must overtake WARM."""
        first_started = threading.Event()
        release_first = threading.Event()
        completion = []
        work_order = []

        def owner_acquire(*, consumer, method, five_key, priority):
            work_order.append(priority)
            if len(work_order) == 1:
                first_started.set()
                self.assertTrue(release_first.wait(2))
            return {"schema": "rc6.ppi.native-acquire-owner.v1", "state": "DONE",
                    "provider_calls_by_rpc": 0}

        server = ReadOnlyUnixFeedServer(
            feed(), socket_path=Path("/not-started.sock"),
            native_acquirer=owner_acquire)
        server.start_native_priority_router()

        def ask(role, priority):
            result = server.dispatch({
                "op": "native_acquire", "consumer": role, "method": "book",
                "ppi_five_key": list(FIVE), "priority": priority})
            completion.append((priority, result["state"]))

        def queued(n):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if server.native_router_status()["queued"] == n:
                    return True
                time.sleep(.005)
            return False

        threads = [threading.Thread(target=ask, args=("scanner", "WARM")),
                   threading.Thread(target=ask, args=("scanner", "DISCOVERY")),
                   threading.Thread(target=ask, args=("exit_reader", "EXIT_CRITICAL"))]
        try:
            threads[0].start()
            self.assertTrue(first_started.wait(2))
            threads[1].start()
            self.assertTrue(queued(1))
            threads[2].start()
            self.assertTrue(queued(2))
            release_first.set()
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(work_order, ["WARM", "EXIT_CRITICAL", "DISCOVERY"])
            self.assertEqual(len(completion), 3)
            self.assertEqual(server.native_router_status()["completed"], 3)
        finally:
            release_first.set()
            server.stop_native_priority_router()

    def test_inflight_warm_get_cannot_prove_exit_five_second_service(self):
        """No false EXIT success while a lower-priority SDK GET is in flight.

        The real guarded transport may wait connect10s/read30s. Priority
        overtakes *queued* work only; a blocked GET is not preemptible. The
        five-second EXIT SLA therefore remains NO_VERIFICADO pending physical
        wire measurements or a separately proven bounded transport contract.
        """
        warm_entered = threading.Event()
        release_warm = threading.Event()
        exit_observed = threading.Event()
        completion = {}

        def guarded_get(**request):
            if request["priority"] == "WARM":
                warm_entered.set()
                if not release_warm.wait(2):
                    raise AssertionError("test did not release blocked GET")
            return {"state": "OK", "provider_calls_by_rpc": 1}

        def budget_verdict(**request):
            exit_observed.set()
            return {"status": "BASELINE_NOT_MEASURED", "lower_suspended": None}

        server = ReadOnlyUnixFeedServer(
            feed(), socket_path=Path("/not-started.sock"),
            native_acquirer=guarded_get, exit_round_observer=budget_verdict,
            owner_status=lambda: {"owner_pid": os.getpid(),
                                  "session_generation": "a" * 64})
        server.start_native_priority_router()

        def ask_warm():
            completion["warm"] = server.dispatch({
                "op": "native_acquire", "consumer": "scanner",
                "method": "book", "ppi_five_key": list(FIVE),
                "priority": "WARM"})

        def ask_exit():
            completion["exit"] = server.dispatch({
                "op": "exit_round_observe", "consumer": "exit_reader",
                "elapsed_seconds": 5, "deadline_seconds": 5,
                "failures": 1})

        warm = threading.Thread(target=ask_warm)
        exit_control = threading.Thread(target=ask_exit)
        try:
            warm.start()
            self.assertTrue(warm_entered.wait(1))
            exit_control.start()
            deadline = time.monotonic() + 1
            while (server.native_router_status()["queued"] != 1
                    and time.monotonic() < deadline):
                time.sleep(.005)
            self.assertEqual(server.native_router_status()["queued"], 1)
            self.assertFalse(exit_observed.is_set())
            self.assertNotIn("exit", completion)
            self.assertTrue(server.native_router_status()["inflight"])
            release_warm.set()
            warm.join(timeout=2)
            exit_control.join(timeout=2)
            self.assertFalse(warm.is_alive())
            self.assertFalse(exit_control.is_alive())
            self.assertEqual(completion["exit"]["state"], "OK")
            self.assertEqual(completion["exit"]["observation"]["status"],
                             "BASELINE_NOT_MEASURED")
            self.assertTrue(exit_observed.is_set())
        finally:
            release_warm.set()
            server.stop_native_priority_router()

    def test_exit_rpc_deadline_fails_closed_while_sdk_wire_is_still_inflight(self):
        """The 4s caller timeout is NOT a 5s backend Book SLA.

        A guarded PPI GET may remain in flight for 10s connect + 30s read.
        The client must receive an UNKNOWN wire-count denial rather than a
        fake zero, COMPLETE or fresh Book; only the existing owner may finish
        the original GET after this caller's request has expired.
        """
        active_wire = threading.Event()
        release_wire = threading.Event()
        wire_finished = threading.Event()
        invocations = []
        responses = {}

        def guarded_get(**request):
            invocations.append(request["priority"])
            active_wire.set()
            try:
                self.assertTrue(release_wire.wait(6))
                return {"schema": "rc6.ppi.native-acquire-owner.v1",
                        "state": "OK", "provider_calls_by_rpc": 1}
            finally:
                wire_finished.set()

        server = ReadOnlyUnixFeedServer(
            feed(), socket_path=Path("/not-started.sock"),
            native_acquirer=guarded_get,
            owner_status=lambda: {"owner_pid": os.getpid(),
                                  "session_generation": "a" * 64})
        server.start_native_priority_router()

        def request(role, priority):
            responses[role] = server.dispatch({
                "op": "native_acquire", "consumer": role, "method": "book",
                "ppi_five_key": list(FIVE), "priority": priority})

        warm = threading.Thread(target=request, args=("scanner", "WARM"))
        try:
            warm.start()
            self.assertTrue(active_wire.wait(1))
            started = time.monotonic()
            denied = server.dispatch({
                "op": "native_acquire", "consumer": "exit_reader",
                "method": "book", "ppi_five_key": list(FIVE),
                "priority": "EXIT_CRITICAL"})
            exit_rpc_seconds = time.monotonic() - started

            self.assertEqual(denied["state"], "PPI_NATIVE_GATEWAY_DEADLINE_UNVERIFIED")
            self.assertEqual(denied["provider_calls_by_rpc"], "NO_VERIFICADO")
            self.assertNotIn("receipt", denied)
            self.assertNotIn("guard_wire_receipt", denied)
            self.assertGreaterEqual(exit_rpc_seconds, 3.5)
            self.assertLess(exit_rpc_seconds, 5.0)
            self.assertTrue(server.native_router_status()["inflight"])
            self.assertFalse(wire_finished.is_set())
            self.assertEqual(invocations, ["WARM"])
            release_wire.set()
            warm.join(timeout=2)
            self.assertFalse(warm.is_alive())
            self.assertTrue(wire_finished.wait(1))
            self.assertEqual(invocations, ["WARM"])
        finally:
            release_wire.set()
            server.stop_native_priority_router()

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

    def test_client_ipc_timeout_is_end_to_end_not_renewed_per_socket_phase(self):
        """Connect+send+recv cannot each spend a fresh five-second budget.

        The caller has no broker fallback. A response arriving after the
        absolute IPC deadline is not evidence, even if every individual
        socket operation completed inside its own timeout.
        """
        clock = [100.0]

        class PhasedSocket:
            def __init__(self):
                self.timeout = None
                self.timeouts = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def settimeout(self, seconds):
                self.timeout = seconds
                self.timeouts.append(seconds)

            def connect(self, _path):
                clock[0] += 2.7

            def sendall(self, _packet):
                clock[0] += 1.5

            def recv(self, _bytes):
                # Only 0.8s of the whole five-second budget remains.
                if self.timeout < 1.2:
                    clock[0] += self.timeout
                    raise socket.timeout("aggregate deadline reached")
                clock[0] += 1.2
                return b'{"state":"OK"}\n'

        fake = PhasedSocket()
        client = ReadOnlyUnixFeedClient(
            socket_path=Path("/nonrunning.sock"), timeout_seconds=5)
        with patch("rc6_ppi_shared_feed.socket.socket", return_value=fake), \
             patch("rc6_ppi_shared_feed.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(SharedFeedUnavailable,
                                        "PPI_GATEWAY_UNAVAILABLE"):
                client._request({"op": "owner_status"})
        self.assertLessEqual(clock[0] - 100, 5)
        self.assertGreaterEqual(len(fake.timeouts), 3)

    def test_client_rejects_response_if_validation_finishes_after_deadline(self):
        """No accepted owner evidence after bounded JSON decoding stalls."""
        clock = [100.0]

        class ImmediateSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def settimeout(self, _seconds):
                pass

            def connect(self, _path):
                pass

            def sendall(self, _packet):
                pass

            def recv(self, _size):
                return b'{"state":"OK"}\n'

        real_loads = json.loads

        def delayed_decode(payload):
            clock[0] += 5.1
            return real_loads(payload)

        client = ReadOnlyUnixFeedClient(
            socket_path=Path("/nonrunning.sock"), timeout_seconds=5)
        with patch("rc6_ppi_shared_feed.socket.socket", return_value=ImmediateSocket()), \
             patch("rc6_ppi_shared_feed.time.monotonic", side_effect=lambda: clock[0]), \
             patch("rc6_ppi_shared_feed.json.loads", side_effect=delayed_decode):
            with self.assertRaisesRegex(SharedFeedUnavailable,
                                        "PPI_GATEWAY_UNAVAILABLE"):
                client._request({"op": "owner_status"})

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

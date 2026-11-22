"""A678 OFFHOST contract: native PPI SDK reads are never fabricated from G1 fan-out.

The gateway bus transmits normalized STREAM observations, not authenticated
Current/Book/Intraday/Search/History RPC responses.  No provider is contacted.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from rc6_ppi_shared_feed import PPISharedMarketFeed, SharedFeedUnavailable
from rc6_ppi_gateway_reader_adapter import (
    GatewayEvidenceRejected,
    SharedPPIReaderAdapter,
    gateway_reader_needs_reattach,
)


UTC = timezone.utc
T0 = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
KEY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-48HS")
OTHER = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
DIGEST = "a" * 64


def bus():
    feed = PPISharedMarketFeed(
        ppi_catalog_rows=[
            {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "ARS"},
            {"ticker": "AL30", "type": "BONOS", "market": "BYMA", "currency": "ARS"},
        ],
        catalog_source_digest=DIGEST,
        max_events=3,
        max_subscriptions=2,
        max_age_seconds=5,
    )
    feed.register_consumer("scanner", watched_five_keys=[KEY])
    feed.register_consumer("exit_reader", watched_five_keys=[OTHER])
    feed.plan_subscription(KEY, priority="HOT")
    feed.plan_subscription(OTHER, priority="EXIT_CRITICAL")
    return feed


def stream_book(at=T0, *, ticker="GGAL", kind="ACCIONES", settlement="A-48HS"):
    return {"Ticker": ticker, "Type": kind, "Settlement": settlement,
            "Date": at.isoformat(), "Trade": False,
            "Bids": [{"Position": 1, "Price": 101, "Quantity": 5}],
            "Offers": [{"Position": 1, "Price": 102, "Quantity": 3}]}


def stream_trade(at=T0):
    return {"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS",
            "Date": at.isoformat(), "Trade": True,
            "Price": 101.5, "VolumeAmount": 42}


class FakeClient:
    def __init__(self, source):
        self.source = source
        self.calls = []

    def pull(self, consumer, *, after_sequence, limit):
        self.calls.append((consumer, after_sequence, limit))
        return self.source.pull(consumer, after_sequence=after_sequence, limit=limit)


class SharedPPIReaderAdapterTests(unittest.TestCase):
    def test_exit_round_adaptor_forwards_real_owner_verdict_and_scope_reset(self):
        class ExitRoundOwner(FakeClient):
            def owner_status(self):
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 1}

            def observe_exit_round(self, consumer, *, elapsed_seconds,
                                   deadline_seconds, failures):
                self.calls.append((consumer, elapsed_seconds,
                                   deadline_seconds, failures))
                return {"status": "DEGRADED", "lower_suspended": True}

            def discard_exit_round_scope(self, consumer):
                self.calls.append((consumer, "DISCARD"))

        client = ExitRoundOwner(bus())
        exit_reader = SharedPPIReaderAdapter(
            client=client, consumer="exit_reader", catalog_source_digest=DIGEST)
        exit_reader.login_once()
        reported = exit_reader.observe_exit_round(
            elapsed_seconds=3.25, deadline_seconds=5.0, failures=1)
        assert reported == {"status": "DEGRADED", "lower_suspended": True}
        exit_reader.discard_exit_round_scope()
        assert client.calls == [("exit_reader", 3.25, 5.0, 1),
                                ("exit_reader", "DISCARD")]

    def test_gateway_rotation_and_auth_need_reattach_but_429_never_does(self):
        for code in ("PPI_GATEWAY_OWNER_CHANGED", "PPI_GATEWAY_UNAVAILABLE",
                     "PPI_GATEWAY_PEER_IDENTITY_MISMATCH",
                     "PPI_NATIVE_ACQUIRE_ENVELOPE_INVALID",
                     "PPI_AUX_RPC_ENVELOPE_INVALID", "PPI_NATIVE_HTTP_401",
                     "PPI_NATIVE_HTTP_403", "PPI_AUX_HTTP_401"):
            with self.subTest(code=code):
                assert gateway_reader_needs_reattach(
                    SharedFeedUnavailable(code))
        for code in ("PPI_NATIVE_HTTP_429", "PPI_AUX_HTTP_429",
                     "PPI_GATEWAY_EXACT_IDENTITY_REQUIRED",
                     "PPI_NATIVE_GATEWAY_DEADLINE_UNVERIFIED"):
            with self.subTest(code=code):
                assert not gateway_reader_needs_reattach(
                    GatewayEvidenceRejected(code))
        assert not gateway_reader_needs_reattach(ValueError("PPI_GATEWAY_OWNER_CHANGED"))

    def test_direct_adapter_pin_requires_real_guard_login_send_witness(self):
        class MethodOnlyClient(FakeClient):
            def owner_status(self):
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 0}

        reader = SharedPPIReaderAdapter(
            client=MethodOnlyClient(bus()), consumer="scanner",
            catalog_source_digest=DIGEST)
        with self.assertRaisesRegex(GatewayEvidenceRejected,
                                    "AUTH_ATTESTATION_MISSING"):
            reader.login_once()
        self.assertFalse(reader.budget_enabled)

    def test_metrics_report_live_guard_send_counts_not_false_consumer_zero(self):
        class ObservedCountsClient(FakeClient):
            broken = False

            def owner_status(self):
                if self.broken:
                    raise SharedFeedUnavailable("OWNER_DISCONNECTED")
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 1,
                        "guard_http_allowed": 7,
                        "guard_http_blocked": 2}

        client = ObservedCountsClient(bus())
        reader = SharedPPIReaderAdapter(client=client, consumer="scanner",
                                        catalog_source_digest=DIGEST)
        reader.login_once()
        metrics = reader.metrics
        assert metrics["http_allowed"] == 7
        assert metrics["http_blocked"] == 2
        assert metrics["provider_requests"] == "UNKNOWN"
        assert metrics["consumer_direct_http"] == 0
        client.broken = True
        with self.assertRaisesRegex(SharedFeedUnavailable, "OWNER_DISCONNECTED"):
            _ = reader.metrics

    def test_pinned_owner_native_methods_delegate_without_reader_login_or_get(self):
        class AuthenticatedOwnerClient(FakeClient):
            def owner_status(self):
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 1,
                        "guard_http_allowed": 1, "guard_http_blocked": 0}

            def acquire_native(self, consumer, *, method, five_key, priority):
                self.calls.append((consumer, method, tuple(five_key), priority))
                return {"state": "OK", "receipt": {
                    "raw_payload": {"date": "2026-11-04T15:00:00Z", "price": 100}},
                    "provider_calls_by_rpc": 1}

        client = AuthenticatedOwnerClient(bus())
        reader = SharedPPIReaderAdapter(
            client=client, consumer="scanner", catalog_source_digest=DIGEST)
        reader.login_once()
        self.assertTrue(reader.budget_enabled)
        with reader.read_scope(priority="OPENED_CRITICAL", identity=KEY):
            value = reader.current("GGAL", "ACCIONES", "A-48HS")
        self.assertEqual(value["price"], 100)
        self.assertEqual(client.calls, [
            ("scanner", "current", KEY, "OPENED_CRITICAL")])
        self.assertEqual(reader.metrics["provider_requests"], "UNKNOWN")

    def test_catalog_history_configuration_use_same_pinned_owner(self):
        class AuxiliaryOwnerClient(FakeClient):
            def owner_status(self):
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 1}

            def acquire_auxiliary(self, consumer, *, method, args):
                self.calls.append((consumer, method, args))
                return {"state": "OK", "result":
                        [{"date": "2026-11-04", "close": 100}] if method == "history"
                        else [{"ticker": "GGAL"}] if method == "search_instruments"
                        else {"instrument_types": ["ACCIONES"]}}

        client = AuxiliaryOwnerClient(bus())
        reader = SharedPPIReaderAdapter(
            client=client, consumer="scanner", catalog_source_digest=DIGEST)
        reader.login_once()
        assert reader.search_instruments("GGAL", "ACCIONES", market="BYMA") == [
            {"ticker": "GGAL"}]
        assert reader.history("GGAL", "ACCIONES", "A-48HS",
                              "2026-11-03", "2026-11-04")[0]["close"] == 100
        assert reader.market_configuration()["instrument_types"] == ["ACCIONES"]
        self.assertEqual([item[1] for item in client.calls], [
            "search_instruments", "history", "market_configuration"])

    def test_caucion_discovery_splits_sdk_multi_get_into_exit_interruptible_rpc(self):
        class OneSearchPerPacketClient(FakeClient):
            def owner_status(self):
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 1}

            def acquire_auxiliary(self, consumer, *, method, args):
                self.calls.append((consumer, method, args))
                assert not (args["ticker"] == "CAUCION"
                            and args["instrument_type"] == "CAUCIONES")
                row = {"ticker": args["ticker"],
                    "type": "CAUCIONES", "market": "BYMA",
                    "currency": "ARS" if args["ticker"].startswith("PESOS") else "USD"}
                rows = [row, dict(row, settlement="INMEDIATA"), row] if args["ticker"] == "PESOS1" else [row]
                return {"state": "OK", "result": rows}

        client = OneSearchPerPacketClient(bus())
        reader = SharedPPIReaderAdapter(
            client=client, consumer="scanner", catalog_source_digest=DIGEST)
        reader.login_once()
        with patch("bd_ppi_readonly_guard.caucion_discovery_days", return_value=(1, 2)):
            result = reader.search_instruments("CAUCION", "CAUCIONES",
                                               name="CAUCION", market="BYMA")
        assert {row["ticker"] for row in result} == {
            "PESOS1", "DOLAR1", "PESOS2", "DOLAR2"}
        assert [row.get("settlement") for row in result if row["ticker"] == "PESOS1"] == [None, "INMEDIATA"]
        assert [(call[2]["ticker"], call[2]["name"]) for call in client.calls] == [
            ("PESOS1", "1"), ("DOLAR1", "1"),
            ("PESOS2", "2"), ("DOLAR2", "2")]

    def test_native_p0_wire_witness_is_relayed_not_fabricated_from_stream(self):
        raw = {"date": "2026-11-04T15:00:00Z", "price": 100}
        native_wire = {"schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                       "adapter_send_invoked": True,
                       "adapter_response_completed": True,
                       "wire_attempt_id": "original-not-a-fabricated-receipt"}

        class OriginalWireClient(FakeClient):
            def owner_status(self):
                return {"state": "OPEN", "owner_pid": 123,
                        "session_generation": "c" * 64,
                        "catalog_source_digest": DIGEST,
                        "login_invocations_by_owner": 1,
                        "guard_login_wire_attempts": 1}

            def acquire_native(self, consumer, *, method, five_key, priority):
                return {"state": "OK", "receipt": {"raw_payload": raw},
                        "guard_wire_receipt": native_wire}

        reader = SharedPPIReaderAdapter(
            client=OriginalWireClient(bus()), consumer="scanner",
            catalog_source_digest=DIGEST)
        reader.login_once()
        result = reader.capture_guarded_native("current", KEY, priority="DISCOVERY")
        assert result == {"native_payload": raw, "guard_wire_receipt": native_wire}
        result["guard_wire_receipt"]["wire_attempt_id"] = "tampered"
        assert native_wire["wire_attempt_id"] == "original-not-a-fabricated-receipt"

    def reader(self, source=None, *, clock=T0, digest=DIGEST, consumer="scanner", batch=2):
        source = source or bus()
        client = FakeClient(source)
        return SharedPPIReaderAdapter(
            client=client, consumer=consumer, catalog_source_digest=digest,
            now_utc=lambda: clock, max_age_seconds=5, batch_size=batch,
        ), client

    def test_observe_only_book_trade_are_unverified_not_native_paper_receipts(self):
        source = bus()
        source.publish_native_market_event(stream_book(), received_at=T0,
                                           received_monotonic_ns=1000)
        source.publish_native_market_event(stream_trade(T0 + timedelta(milliseconds=200)),
                                           received_at=T0 + timedelta(milliseconds=200),
                                           received_monotonic_ns=1200)
        reader, client = self.reader(source, clock=T0 + timedelta(seconds=1))
        observed = reader.poll_observations()
        self.assertEqual([event["kind"] for event in observed], ["BOOK", "TRADE"])
        self.assertTrue(all(event["ppi_five_key"] == list(KEY) for event in observed))
        self.assertTrue(all(event["ppi_catalog_authority"] == "NO_VERIFICADO" for event in observed))
        self.assertTrue(all(event["paper_ready"] is False for event in observed))
        self.assertEqual(reader.metrics["paper_authority"], "NO_VERIFICADO")
        self.assertEqual(reader.metrics["cursor"], 2)
        self.assertEqual(client.calls, [("scanner", 0, 2)])
        observed[0]["market_payload"]["bids"][0]["price"] = "9999"
        self.assertNotEqual(reader.observations()[0]["market_payload"]["bids"][0]["price"], "9999")

    def test_method_contract_never_fabricates_sdk_current_native_book_or_history(self):
        source = bus()
        source.publish_native_market_event(stream_book(), received_at=T0,
                                           received_monotonic_ns=1000)
        reader, client = self.reader(source)
        reader.poll_observations()
        with reader.read_scope(priority="HOT", identity=KEY):
            actions = [
                lambda: reader.current("GGAL", "ACCIONES", "A-48HS"),
                lambda: reader.book("GGAL", "ACCIONES", "A-48HS"),
                lambda: reader.intraday("GGAL", "ACCIONES", "A-48HS"),
                lambda: reader.history("GGAL", "ACCIONES", "A-48HS", "2026-11-01", "2026-11-04"),
                lambda: reader.search_instruments("GGAL", "ACCIONES", "GGAL", "BYMA"),
                lambda: reader.market_configuration(),
                lambda: reader.estimate_bonds(object()),
            ]
            for action in actions:
                with self.subTest(action=repr(action)):
                    with self.assertRaisesRegex(GatewayEvidenceRejected, "METHOD_NOT_SUPPORTED"):
                        action()
        self.assertEqual(reader.last_read_error_code, "PPI_GATEWAY_METHOD_NOT_SUPPORTED")
        self.assertEqual(len(client.calls), 1)  # one readonly pull, zero upstream SDK calls

    def test_socket_health_is_not_login_or_real_paper_authority(self):
        reader, client = self.reader(bus())
        with self.assertRaisesRegex(GatewayEvidenceRejected, "AUTH_ATTESTATION_MISSING"):
            reader.login_once()
        self.assertFalse(reader.metrics["authenticated"])
        self.assertFalse(reader.metrics["budget_enabled"])
        self.assertEqual(client.calls, [])
        self.assertFalse(any(hasattr(reader, method) for method in
                             ("order", "send_order", "place_order", "cancel_order")))

    def test_exact_five_key_scope_requires_own_symbol_and_native_settlement(self):
        reader, _ = self.reader(bus())
        with self.assertRaisesRegex(ValueError, "EXACT_IDENTITY_REQUIRED"):
            with reader.read_scope(priority="EXIT_CRITICAL"):
                pass
        with self.assertRaisesRegex(ValueError, "FIVE_KEY_INVALID"):
            with reader.read_scope(identity=("GGAL", "ACCIONES", "A-48HS")):
                pass
        with reader.read_scope(priority="EXIT_CRITICAL", identity=KEY):
            with self.assertRaisesRegex(GatewayEvidenceRejected, "IDENTITY_MISMATCH"):
                reader.current("AL30", "BONOS", "INMEDIATA")
        with self.assertRaisesRegex(GatewayEvidenceRejected, "EXACT_IDENTITY_REQUIRED"):
            reader.book("GGAL", "ACCIONES", "A-48HS")

    def test_gap_fails_closed_without_advancing_cursor_or_emitting_paper(self):
        source = bus()
        for offset in range(4):
            at = T0 + timedelta(milliseconds=offset * 50)
            source.publish_native_market_event(stream_trade(at), received_at=at,
                                               received_monotonic_ns=1000 + offset)
        reader, _ = self.reader(source)
        with self.assertRaisesRegex(SharedFeedUnavailable, "GAP_HOLD"):
            reader.poll_observations()
        self.assertEqual(reader.metrics["cursor"], 0)
        self.assertEqual(reader.metrics["state"], "GAP_HOLD")

    def test_catalog_source_digest_is_pinned_and_not_provider_attestation(self):
        source = bus()
        source.publish_native_market_event(stream_book(), received_at=T0,
                                           received_monotonic_ns=1000)
        reader, _ = self.reader(source, digest="b" * 64)
        with self.assertRaisesRegex(GatewayEvidenceRejected, "CATALOG_DIGEST_MISMATCH"):
            reader.poll_observations()
        self.assertEqual(reader.metrics["cursor"], 0)

    def test_age_and_future_clock_checked_at_consumer_not_only_at_producer(self):
        source = bus()
        source.publish_native_market_event(stream_book(), received_at=T0,
                                           received_monotonic_ns=1000)
        for observed_at, reason in (
            (T0 + timedelta(seconds=7), "STALE"),
            (T0 - timedelta(seconds=1), "FUTURE"),
        ):
            reader, _ = self.reader(source, clock=observed_at)
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(GatewayEvidenceRejected, reason):
                    reader.poll_observations()
                self.assertEqual(reader.metrics["cursor"], 0)

    def test_filtered_consumer_cursor_catches_up_without_unrelated_gap(self):
        source = bus()
        for offset in range(2):
            at = T0 + timedelta(milliseconds=offset * 100)
            source.publish_native_market_event(stream_book(
                at, ticker="AL30", kind="BONOS", settlement="INMEDIATA"),
                received_at=at, received_monotonic_ns=1000 + offset)
        reader, _ = self.reader(source)
        self.assertEqual(reader.poll_observations(), ())
        self.assertEqual(reader.metrics["cursor"], 2)

    def test_tampered_payload_rejected_even_for_injected_client(self):
        source = bus()
        source.publish_native_market_event(stream_book(), received_at=T0,
                                           received_monotonic_ns=1000)
        reader, client = self.reader(source)
        native_pull = client.pull

        def corrupted(consumer, *, after_sequence, limit):
            packet = native_pull(consumer, after_sequence=after_sequence, limit=limit)
            packet["events"][0]["market_payload"]["bids"][0]["price"] = "1.00"
            return packet

        with patch.object(client, "pull", side_effect=corrupted):
            with self.assertRaisesRegex(GatewayEvidenceRejected, "EVENT_HASH_MISMATCH"):
                reader.poll_observations()

    def test_subscription_inferred_type_never_becomes_native_catalog_proof(self):
        source = bus()
        native = stream_book()
        native.pop("Type")
        event = source.publish_native_market_event(native, received_at=T0,
                                                   received_monotonic_ns=1000)
        reader, _ = self.reader(source)
        self.assertEqual(event["type_provenance"],
                         "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE")
        result = reader.poll_observations()
        self.assertEqual(result[0]["type_provenance"],
                         "UNIQUE_ACTIVE_SUBSCRIPTION_NOT_NATIVE")
        self.assertEqual(result[0]["ppi_catalog_authority"], "NO_VERIFICADO")
        self.assertFalse(result[0]["paper_ready"])
        self.assertFalse(reader.metrics["authenticated"])

    def test_cached_observation_expires_before_consumption(self):
        source = bus()
        source.publish_native_market_event(stream_book(), received_at=T0,
                                           received_monotonic_ns=1000)
        current = [T0 + timedelta(seconds=1)]
        reader = SharedPPIReaderAdapter(
            client=FakeClient(source), consumer="scanner",
            catalog_source_digest=DIGEST, now_utc=lambda: current[0],
            max_age_seconds=5,
        )
        self.assertEqual(len(reader.poll_observations()), 1)
        current[0] = T0 + timedelta(seconds=10)
        with self.assertRaisesRegex(GatewayEvidenceRejected, "STALE"):
            reader.observations()
        self.assertEqual(reader.metrics["state"], "HOLD")

    def test_closed_reader_cannot_resume_silently_or_reopen_sdk(self):
        reader, client = self.reader(bus())
        reader.close()
        with self.assertRaisesRegex(GatewayEvidenceRejected, "CLOSED"):
            reader.poll_observations()
        with self.assertRaisesRegex(GatewayEvidenceRejected, "CLOSED"):
            reader.observations()
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()

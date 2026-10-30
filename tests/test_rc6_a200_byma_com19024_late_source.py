"""COM19024 02-Nov-2026 late SOURCE-only contract, with no real provider.

Official source (BYMA, 22-Sep-2026):
https://cdn.prod.website-files.com/6697a441a50c6b926e1972e0/6ab2dda27ea9e2c80e659a15_BYMA-COM19024-Horarios_de_Negociacion_Liquidacion_Recepcion-v2.pdf
PDF SHA256: 243aa692439f225d02ed1aac9648a6b224df8cab5a1bc18f3f05f20bcfe4ab98

RV contado 24h and regular options/plazos close at 18:00; contado inmediato,
cauciones and fixed income close at 17:00. Futures have subtype-specific
hours, so the generic quote collector must never extend them by analogy.
"""

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import iol_shadow_collector_rc6 as windows
from iol_mcp_readonly_adapter_rc6 import IOLMCPError
from scripts import rc6_iol_shadow_collect as collector
from scripts import rc6_source_cycle_dispatch as dispatch


ART = ZoneInfo("America/Argentina/Buenos_Aires")


def local(day, hour, minute=30, month=11):
    return datetime(2026, month, day, hour, minute, tzinfo=ART)


class BymaLateSourceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rc6-com19024-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.market = self.root / "market"
        self.market.mkdir()
        self.db = self.root / "paper.db"
        with sqlite3.connect(self.db) as connection:
            connection.executescript("""
                CREATE TABLE observer_state (id INTEGER, mode TEXT, real_orders_sent INTEGER);
                INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', 0);
                CREATE TABLE financial_instrument_catalog (
                    ticker TEXT, instrument_type TEXT, market TEXT,
                    currency TEXT, settlement TEXT, settlement_source TEXT,
                    status TEXT);
            """)

    def insert(self, ticker, family, settlement="A-24HS", *, market="BYMA",
               currency="ARS", source="PPI_FIELD", status="AVAILABLE"):
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                               (ticker, family, market, currency, settlement, source, status))

    def eligible(self, *, scope="OPERATIONAL", env=None):
        with patch.object(collector, "DEFAULT_DB", str(self.db)), patch.dict(
                os.environ, {"POROTA_IOL_SHADOW_UNIVERSE": "",
                             "POROTA_IOL_EVIDENCE_UNIVERSE": "", **(env or {})}, clear=False):
            return collector._late_verified_universe_with_source(local(2, 17), scope)

    def test_effective_date_and_segment_specific_close(self):
        self.assertFalse(windows.is_late_observation_window(local(30, 17, month=10)))
        self.assertFalse(windows.is_late_observation_window(local(1, 17)))
        self.assertFalse(windows.is_late_observation_window(local(2, 16, 59)))
        self.assertTrue(windows.is_late_observation_window(local(2, 17)))
        self.assertFalse(windows.is_late_observation_window(local(2, 18, 0)))
        self.assertFalse(windows.is_late_observation_window(
            datetime(2027, 1, 4, 17, 30, tzinfo=ART)))
        for family in ("ACCIONES", "CEDEARS", "OPCIONES"):
            self.assertTrue(windows.is_verified_late_contract_window(
                local(2, 17), family=family, market="BYMA",
                settlement="A-24HS", settlement_source="PPI_FIELD"))
        for family, settlement, market, source in (
            ("ACCIONES", "INMEDIATA", "BYMA", "PPI_FIELD"),
            ("ACCIONES", "A-24HS", "BYMA", "REQUEST_CANDIDATE"),
            ("ACCIONES", "A-24HS", "NASDAQ", "PPI_FIELD"),
            ("BONOS", "A-24HS", "BYMA", "PPI_FIELD"),
            ("CAUCIONES", "A-24HS", "BYMA", "PPI_FIELD"),
            ("FUTUROS", "A-24HS", "BYMA", "PPI_FIELD"),
            ("FUTUROS_DOLAR", "A-24HS", "BYMA", "PPI_FIELD"),
        ):
            self.assertFalse(windows.is_verified_late_contract_window(
                local(2, 17), family=family, market=market,
                settlement=settlement, settlement_source=source))

    def test_late_catalog_requires_exact_identity_and_filters_collisions(self):
        self.insert("AAA", "ACCIONES")
        self.insert("AAA", "ACCIONES", "INMEDIATA")  # allowed T+1 remains exact
        self.insert("AAPL", "CEDEARS")
        self.insert("CALL1", "OPCIONES")
        self.insert("CI", "ACCIONES", "INMEDIATA")
        self.insert("BOND", "BONOS")
        self.insert("PESO1", "CAUCIONES")
        self.insert("DLR", "FUTUROS")
        self.insert("UNVERIFIED", "ACCIONES", source="REQUEST_CANDIDATE")
        self.insert("MISSING_CURRENCY", "ACCIONES", currency="UNKNOWN")
        self.insert("FOREIGN", "ACCIONES", market="NASDAQ")
        self.insert("AMBIG", "ACCIONES", currency="ARS")
        self.insert("AMBIG", "ACCIONES", currency="USD")
        self.assertEqual(self.eligible(),
                         (["AAA", "AAPL", "CALL1"], "PPI_EXACT_BYMA_T1_LATE_SESSION"))
        self.assertEqual(self.eligible(env={"POROTA_IOL_SHADOW_UNIVERSE": "AAA,CI,DLR"}),
                         (["AAA"], "PPI_EXACT_BYMA_T1_LATE_SESSION"))
        self.assertEqual(self.eligible(scope="AUDIT_ALL", env={
            "POROTA_IOL_EVIDENCE_UNIVERSE": "CALL1,BOND,DLR"}),
            (["CALL1"], "PPI_EXACT_BYMA_T1_LATE_SESSION"))

    def test_no_catalog_or_verified_identity_means_no_oauth_construction(self):
        with patch.object(collector, "DEFAULT_DB", str(self.db)), patch.object(
                collector, "OAuthStoreReadOnlyMCP", side_effect=AssertionError("provider used")), patch.object(
                collector, "LateNoOAuthRefreshMCP", side_effect=AssertionError("oauth used")):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(collector.main(local(2, 17)), 0)
            self.assertIn("IOL_SHADOW_COLLECTION=NOT_DUE_LATE_NO_VERIFIED_T1", output.getvalue())
            self.assertEqual(list(self.market.iterdir()), [])

    def test_verified_late_main_uses_only_t1_and_no_refresh_client(self):
        self.insert("AAA", "ACCIONES")
        self.insert("CI", "ACCIONES", "INMEDIATA")
        observed = []

        def no_network_batch(symbols, client, **kwargs):
            observed.append((list(symbols), type(client).__name__, kwargs.get("root")))
            self.assertIsInstance(client, collector.LateNoOAuthRefreshMCP)
            return {"symbols": [], "progress": {"ready": 1, "completed": 1}}

        with patch.object(collector, "DEFAULT_DB", str(self.db)), patch.object(
                collector, "DEFAULT_ROOT", self.market), patch.dict(os.environ, {
                    "POROTA_IOL_SHADOW_UNIVERSE": "", "POROTA_IOL_SHADOW_SCOPE": "OPERATIONAL"}), patch.object(
                collector, "_rotation", return_value=(["AAA"], 0, {})), patch.object(
                collector, "_commit_rotation", return_value={"seen": ["AAA"]}), patch.object(
                collector, "_primary_snapshot", return_value=({}, {})), patch.object(
                collector, "run_batch", side_effect=no_network_batch), patch.object(
                collector, "_publish_progress", return_value={
                    "symbols": [], "progress": {"ready": 1, "completed": 1}}), patch.object(
                collector, "consolidate", return_value={"decision_effect": "OBSERVE_ONLY"}), patch.object(
                collector.family_reference, "collect", side_effect=AssertionError("family calls not allowed")):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(collector.main(local(2, 17)), 0)
        self.assertEqual(observed, [(["AAA"], "LateNoOAuthRefreshMCP", self.market)])
        self.assertIn("LATE_T1_ONLY=true", output.getvalue())

    def test_late_oauth_client_denies_refresh_write_and_401_rotation(self):
        token = self.root / "oauth.json"
        token.write_text(json.dumps({"issuer": "https://example.invalid",
            "tokens": {"access_token": "masked", "expires_in": 1},
            "token_obtained_at_epoch": 1}), encoding="utf-8")
        before = token.read_bytes()
        client = collector.LateNoOAuthRefreshMCP(token)
        with self.assertRaisesRegex(IOLMCPError, "IOL_LATE_REFRESH_REQUIRED_NO_MUTATION"):
            client._refresh_if_due()
        with self.assertRaisesRegex(IOLMCPError, "IOL_LATE_REFRESH_REQUIRED_NO_MUTATION"):
            client._refresh_after_401({})
        with self.assertRaisesRegex(IOLMCPError, "IOL_LATE_OAUTH_WRITE_FORBIDDEN"):
            client._write_store({})
        self.assertEqual(token.read_bytes(), before)

    def test_late_403_circuit_allows_at_most_one_transport_attempt_per_process(self):
        client = collector.LateNoOAuthRefreshMCP(self.root / "no-real-token.json")
        with patch.object(client, "_refresh_if_due", return_value=None), patch.object(
                client, "_rpc", side_effect=IOLMCPError("IOL_MCP_HTTP_403", status_code=403)) as rpc:
            with self.assertRaisesRegex(IOLMCPError, "IOL_MCP_HTTP_403"):
                client.call("get_asset_quote", {"symbol": "AAA", "term": "t1"})
            with self.assertRaisesRegex(IOLMCPError, "IOL_LATE_AUTH_TERMINAL_NO_RETRY"):
                client.call("get_asset_quote", {"symbol": "BBB", "term": "t1"})
            with self.assertRaisesRegex(PermissionError, "IOL_SHADOW_TOOL_DENIED"):
                client.call("place_order", {})
            self.assertEqual(rpc.call_count, 1)

    def test_dispatcher_only_runs_optional_iol_stage_after_seventeen(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(Path(command[1]).name)
            target = self.market / "iol_shadow_latest.json"
            temp = target.with_suffix(".new")
            temp.write_text("{}", encoding="utf-8")
            os.replace(temp, target)
            return SimpleNamespace(returncode=0, stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1\n")

        result = dispatch.run_cycle(now=local(2, 17), db=self.db,
                                    market_root=self.market, lock=self.root / "lock", runner=runner)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["stages"], {"iol": "OK"})
        self.assertEqual(calls, ["rc6_iol_shadow_collect.py"])
        self.assertEqual(result["reason"], "COM19024_EXACT_LATE_T1_OBSERVE_ONLY")
        self.assertFalse((self.market / "rc6_public_sources_latest.json").exists())

    def test_no_late_contract_and_pre_effective_date_never_call_provider(self):
        calls = []

        def not_due(command, **kwargs):
            calls.append(Path(command[1]).name)
            return SimpleNamespace(returncode=0,
                stdout="IOL_SHADOW_COLLECTION=NOT_DUE_LATE_NO_VERIFIED_T1 SOURCE=EMPTY\n")

        before = dispatch.run_cycle(now=local(30, 17, month=10), db=self.db,
                                    market_root=self.market, lock=self.root / "lock", runner=not_due)
        self.assertEqual(before["status"], "NOT_DUE")
        self.assertEqual(calls, [])
        after = dispatch.run_cycle(now=local(2, 17), db=self.db,
                                   market_root=self.market, lock=self.root / "lock", runner=not_due)
        self.assertEqual(after["status"], "NOT_DUE")
        self.assertEqual(after["stages"], {"iol": "NOT_DUE_LATE_EXACT_CONTRACT"})
        self.assertEqual(calls, ["rc6_iol_shadow_collect.py"])

    def test_timer_extended_only_as_checked_source_window(self):
        timer = (Path(__file__).resolve().parents[1] /
                 "systemd/porota-iol-shadow-collector-rc6.timer").read_text(encoding="utf-8")
        self.assertIn("17:*:00 America/Argentina/Buenos_Aires", timer)
        self.assertTrue(dispatch.phase_status(local(2, 17), "late_source")[0])
        self.assertFalse(dispatch.phase_status(local(30, 17, month=10), "late_source")[0])
        self.assertFalse(dispatch.phase_status(local(2, 18, 0), "late_source")[0])
        self.assertFalse(dispatch.phase_status(
            datetime(2027, 1, 4, 17, 30, tzinfo=ART), "late_source")[0])


if __name__ == "__main__":
    unittest.main()

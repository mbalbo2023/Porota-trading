from datetime import datetime, timezone
from hashlib import file_digest
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zipfile import ZipFile

from cu_history_store_v2_hf6 import (
    SOURCE_REPORTED, Candle, append_many, read_as_of, source_reported_adjustment,
)
from cv_history_store_adapter_hf6 import HistoricalStore
from scripts.rc6_history_import_source_reported import MEMBER, _capture_sha, import_iol_export


class SourceReportedImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "iol.zip"
        self.destination = self.root / "reference.sqlite"
        self.observed = "2025-10-02T21:00:00Z"
        bar_time = int(datetime(2025, 9, 22, 19, tzinfo=timezone.utc).timestamp())
        first = {"date": "2025-09-22", "time": bar_time, "open": 10,
                 "high": 15, "low": 8, "close": 12, "volume": 0}
        bad = dict(first, date="2025-09-23", time=bar_time+86400, close=7)
        payload = {"bars": [first, bad]}
        capture = {"market": "BCBA", "family": "ACCIONES", "symbol": "GGAL",
                   "from_date": "2025-09-21", "to_date": "2025-09-23",
                   "payload": payload, "payload_sha256": _capture_sha(payload),
                   "received_at": self.observed}
        with ZipFile(self.archive, "w") as archive:
            archive.writestr(MEMBER, json.dumps(capture) + "\n")
        with self.archive.open("rb") as archive:
            self.digest = file_digest(archive, "sha256").hexdigest()

    def test_valid_bars_are_canonical_for_their_source_and_retry_is_idempotent(self):
        first = import_iol_export(self.archive, self.destination, self.digest)
        self.assertEqual(first["canonical_updates"], 1)
        self.assertEqual(first["rejected"], {"HISTORY_OHLC_INCONSISTENT": 1})
        second = import_iol_export(self.archive, self.destination, self.digest)
        self.assertEqual((second["versions_appended"], second["canonical_updates"]), (0, 0))
        self.assertEqual((second["canonical_rows"], second["version_rows"]), (1, 1))
        store = HistoricalStore(str(self.destination))
        with store.connect() as connection:
            keys = dict(symbol="GGAL", instrument_type="ACCIONES", market="BCBA",
                        currency="UNKNOWN", settlement="UNKNOWN", price_basis=SOURCE_REPORTED,
                        adjustment_basis=source_reported_adjustment("IOL_MCP"))
            self.assertEqual(read_as_of(connection, as_of="2025-09-25T21:00:00Z", **keys), [])
            saved = read_as_of(connection, as_of=self.observed, **keys)
            self.assertEqual([(r["currency"], r["settlement"], r["close"], r["volume"])
                              for r in saved], [("UNKNOWN", "UNKNOWN", 12, 0)])
            self.assertEqual(read_as_of(connection, symbol="GGAL", instrument_type="ACCIONES",
                                        market="BYMA", currency="ARS", settlement="A-24HS",
                                        as_of=self.observed), [])

    def test_wrong_digest_and_existing_unrelated_database_are_refused(self):
        with self.assertRaisesRegex(ValueError, "HISTORY_CAPTURE_FILE_DIGEST_MISMATCH"):
            import_iol_export(self.archive, self.destination, "f"*64)
        self.assertFalse(self.destination.exists())
        with sqlite3.connect(self.destination) as connection:
            connection.execute("CREATE TABLE unrelated (id)")
        self.destination.chmod(0o600)
        original = self.destination.read_bytes()
        with self.assertRaisesRegex(ValueError, "HISTORY_REFERENCE_DESTINATION_REQUIRED"):
            import_iol_export(self.archive, self.destination, self.digest)
        mistyped_store = HistoricalStore(str(self.destination), source_reported_only=True)
        with self.assertRaisesRegex(ValueError, "HISTORY_REFERENCE_DESTINATION_REQUIRED"):
            mistyped_store.connect()
        self.assertEqual(self.destination.read_bytes(), original)

    def test_regular_store_rejects_source_reported_candles(self):
        candle = Candle(symbol="GGAL", instrument_type="ACCIONES", market="BCBA",
                        settlement="UNKNOWN", currency="UNKNOWN", date="2025-09-22",
                        open=10, high=15, low=8, close=12, volume=0,
                        source="IOL_MCP", observed_at=self.observed,
                        price_basis=SOURCE_REPORTED, adjustment_basis="UNKNOWN:IOL_MCP")
        ordinary = HistoricalStore(str(self.destination))
        with self.assertRaisesRegex(ValueError, "HISTORY_STORE_SERIES_SCOPE_MISMATCH"):
            append_many(ordinary, [candle])
        self.assertFalse(self.destination.exists())

    def test_existing_store_import_preserves_exact_history_and_schema_on_retry(self):
        destination = self.root / "market_history.db"
        ordinary = HistoricalStore(str(destination))
        append_many(ordinary, [Candle(symbol="GGAL", instrument_type="ACCIONES", market="BYMA",
            settlement="T+1", date="2025-09-22", open=100, high=110, low=90, close=105,
            volume=100, source="PPI", currency="ARS", observed_at=self.observed)])
        identity = (destination.stat().st_dev, destination.stat().st_ino)
        sql = "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        with ordinary.connect() as before:
            schema = [tuple(row) for row in before.execute(sql)]
        first = import_iol_export(self.archive, destination, self.digest, existing_identity=identity)
        retry = import_iol_export(self.archive, destination, self.digest, existing_identity=identity)
        self.assertEqual(first["destination"], "EXISTING_HISTORY")
        self.assertEqual(first["versions_appended"], 1)
        self.assertEqual(retry["versions_appended"], 0)
        self.assertEqual(retry["canonical_updates"], 0)
        with ordinary.connect() as after:
            self.assertEqual([tuple(row) for row in after.execute(sql)], schema)
            self.assertEqual([tuple(row) for row in after.execute(
                "SELECT price_basis,close FROM history_canonical_v2 ORDER BY price_basis")],
                [("RAW", 105.0), (SOURCE_REPORTED, 12.0)])

    def test_existing_store_rejects_legacy_schema_and_wrong_file_identity_without_writes(self):
        destination = self.root / "market_history.db"
        with sqlite3.connect(destination) as connection:
            connection.execute("CREATE TABLE history_canonical_v2(symbol TEXT PRIMARY KEY, close REAL)")
            connection.execute("INSERT INTO history_canonical_v2 VALUES('GGAL', 100)")
        before = destination.read_bytes()
        stat = destination.stat()
        with self.assertRaisesRegex(ValueError, "HISTORY_EXISTING_DESTINATION_CHANGED"):
            import_iol_export(self.archive, destination, self.digest,
                              existing_identity=(stat.st_dev, stat.st_ino + 1))
        with self.assertRaisesRegex(ValueError, "HISTORY_EXISTING_SCHEMA_REQUIRED"):
            import_iol_export(self.archive, destination, self.digest,
                              existing_identity=(stat.st_dev, stat.st_ino))
        self.assertEqual(destination.read_bytes(), before)

    def test_an_exact_history_row_keeps_its_original_route(self):
        exact = Candle(symbol="GGAL", instrument_type="ACCIONES", market="BYMA",
                       currency="ARS", settlement="A-24HS", date="2025-09-22",
                       open=10, high=15, low=8, close=12, volume=11,
                       source="PPI_PRODUCTION_HISTORY", observed_at=self.observed)
        store = HistoricalStore(str(self.destination))
        self.assertEqual(append_many(store, [exact])["canonical_updates"], 1)
        with store.connect() as connection:
            rows = read_as_of(connection, symbol="GGAL", instrument_type="ACCIONES",
                              market="BYMA", currency="ARS", settlement="A-24HS",
                              as_of=self.observed)
        self.assertEqual([(row["price_basis"], row["source"]) for row in rows],
                         [("RAW", "PPI_PRODUCTION_HISTORY")])

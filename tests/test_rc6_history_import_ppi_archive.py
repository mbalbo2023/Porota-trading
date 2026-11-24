from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import cu_history_store_v2_hf6 as history
import fb_raw_evidence_exact_v1 as exact
from scripts.rc6_history_import_ppi_archive import import_ppi_archive
from scripts.rc6_history_import_source_reported import _source_store


class ArchiveImportTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = {exact.MODE_ENV: exact.MODE, exact.ROOT_ENV: str(self.root / "evidence")}
        self.store = _source_store(self.root / "reference.db")

    def archive(self, ticker, family, term, observed_day, rows, *, new=False, key=None, legacy_marker=None):
        observed = f"{observed_day}T23:00:00+00:00"
        wrapper = {"symbol": ticker, "asset_class": family, "settlement": term,
                   "date_from": "2025-09-22", "date_to": observed_day, "payload_json": json.dumps(rows)}
        if legacy_marker is not None:
            wrapper.update(market="BYMA")
            default_key = [ticker, family, "BYMA", term, observed]
            if legacy_marker:
                default_key.append(legacy_marker)
        elif new:
            wrapper.update(market="BYMA", currency="ARS")
            default_key = [[ticker, family, "BYMA", "ARS", term], wrapper["date_from"], observed_day, observed]
        else:
            default_key = [ticker, family, term, observed]
        return exact.archive_wrapper(row_key=exact.canonical(default_key if key is None else key),
            wrapper=wrapper, recorded_at=observed, quality="VALID_PAYLOAD", environ=self.env)

    @staticmethod
    def bar(day, **fields):
        return {"date": f"{day}T15:00:00-03:00", "openingPrice": 10, "min": 9,
                "max": 16, "price": 12, "volume": 0, **fields}

    def read(self, ticker, family, provider_family, term, observed_day):
        with self.store.connect() as connection:
            return history.read_as_of(connection, symbol=ticker, instrument_type=family,
                market="PPI", currency="UNKNOWN", settlement=term,
                price_basis=history.SOURCE_REPORTED, as_of=f"{observed_day}T23:01:00+00:00",
                adjustment_basis=history.source_reported_adjustment(history.PPI_REPORTED, provider_family))

    def test_legacy_recent_revision_and_distinct_selectors_are_idempotent(self):
        old = self.bar("2025-09-22")
        self.archive("TEST", "ON", "A-24HS", "2025-09-23", [old])
        self.archive("TEST", "ON", "A-24HS", "2025-09-25", [old, self.bar("2025-09-24")])
        self.archive("TEST", "ON", "A-24HS", "2025-09-26", [self.bar("2025-09-22", price=13)])
        self.archive("TEST", "OBLIGACIONES", "A-24HS", "2025-09-26", [self.bar("2025-09-22", price=15)], new=True)
        self.archive("TEST", "ON", "INMEDIATA", "2025-09-24", [self.bar("2025-09-22", price=11)])
        first = import_ppi_archive(self.store, environ=self.env)
        retry = import_ppi_archive(self.store, environ=self.env)
        self.assertEqual((first["versions_appended"], first["stored_rows"]), (5, 4))
        self.assertEqual((retry["versions_appended"], retry["canonical_updates"]), (0, 0))
        self.assertEqual([row["close"] for row in self.read(
            "TEST", "OBLIGACIONES", "ON", "A-24HS", "2025-09-23")], [12])
        self.assertEqual([row["close"] for row in self.read(
            "TEST", "OBLIGACIONES", "ON", "A-24HS", "2025-09-26")], [13, 12])
        self.assertEqual([row["close"] for row in self.read(
            "TEST", "OBLIGACIONES", "OBLIGACIONES", "A-24HS", "2025-09-26")], [15])
        self.assertEqual([row["close"] for row in self.read(
            "TEST", "OBLIGACIONES", "ON", "INMEDIATA", "2025-09-26")], [11])
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("""SELECT COUNT(*) FROM history_canonical_v2
                WHERE price_basis='RAW' OR currency<>'UNKNOWN'""").fetchone()[0], 0)

    def test_conflicting_values_legacy_and_corrupt_objects_do_not_enter_store(self):
        old = self.bar("2025-09-22")
        bad = self.bar("2025-09-24", price=1)
        duplicate = self.bar("2025-09-22", price=13)
        good = self.bar("2025-09-25")
        self.archive("GGAL", "ACCIONES", "A-24HS", "2025-09-26", [old, duplicate, bad, good])
        self.archive("GD30", "BONOS", "A-24HS", "2025-09-26", [old], key=["unrelated"])
        corrupt = self.archive("FCI", "FCI", "INMEDIATA", "2025-09-26", [old])
        exact.object_path(corrupt["inner_sha256"], self.env).write_bytes(b"corrupt")
        receipt = import_ppi_archive(self.store, environ=self.env)
        self.assertEqual(receipt["stored_rows"], 1)
        self.assertEqual(receipt["manifests_rejected"]["HISTORY_ARCHIVE_MANIFEST_ENTRY_INVALID"], 1)
        by_symbol = {row["series"][0]: row for row in receipt["series"]}
        self.assertEqual(by_symbol["GGAL"]["first_date"], date(2025, 9, 25).isoformat())
        self.assertEqual(by_symbol["FCI"]["rejected"]["HISTORY_ARCHIVE_OBJECT_UNVERIFIED"], 1)

    def test_closeout_recovers_new_dates_and_cafci_like_symbols_without_replacing_prior_days(self):
        self.archive("GGAL", "ACCIONES", "A-24HS", "2025-09-26", [self.bar("2025-09-24")])
        self.archive("GGAL", "ACCIONES", "A-24HS", "2025-09-25",
                     [self.bar("2025-09-24", price=16), self.bar("2025-09-23")],
                     legacy_marker="API_CLOSEOUT")
        self.archive("GCAP 100% A", "FCI", "INMEDIATA", "2025-09-25",
                     [self.bar("2025-09-23")], legacy_marker="")
        self.archive("AL30", "BONOS", "A-24HS", "2025-09-25",
                     [self.bar("2025-09-23")], legacy_marker="UNVERIFIED")
        standard = import_ppi_archive(self.store, environ=self.env)
        first = import_ppi_archive(self.store, environ=self.env, legacy_closeout=True)
        repeat = import_ppi_archive(self.store, environ=self.env, legacy_closeout=True)
        self.assertEqual((standard["stored_rows"], first["stored_rows"], repeat["stored_rows"]), (1, 3, 3))
        self.assertEqual(first["manifests_rejected"]["HISTORY_ARCHIVE_MANIFEST_ENTRY_INVALID"], 1)
        self.assertEqual(repeat.get("versions_appended", 0), 0)
        self.assertEqual([row["close"] for row in self.read(
            "GGAL", "ACCIONES", "ACCIONES", "A-24HS", "2025-09-26")], [12, 12])
        self.assertEqual(len(self.read("GCAP 100% A", "FCI", "FCI", "INMEDIATA", "2025-09-25")), 1)

    def test_closeout_recovers_dollar_symbols_with_a_pinned_legacy_scope(self):
        self.archive("FONDO $ A", "FCI", "INMEDIATA", "2025-09-25",
                     [self.bar("2025-09-23")], legacy_marker="")
        self.archive("FONDO $ B", "FCI", "INMEDIATA", "2025-09-25",
                     [self.bar("2025-09-23")], legacy_marker="")
        selection = [sha256(b"FONDO $ A").hexdigest()]
        receipt = import_ppi_archive(self.store, environ=self.env, legacy_closeout=True,
                                     symbol_digests=selection)
        retry = import_ppi_archive(self.store, environ=self.env, legacy_closeout=True,
                                   symbol_digests=selection)
        self.assertEqual((receipt["stored_rows"], retry["stored_rows"]), (1, 1))
        self.assertEqual(retry.get("versions_appended",0), 0)
        self.assertEqual([row["series"][0] for row in receipt["series"]], ["FONDO $ A"])


if __name__ == "__main__":
    unittest.main()

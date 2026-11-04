"""A600: provenance guards for the offline PPI mass contract producer.

The old candidate-universe fallback tolerates legacy sources, but contract
evidence must not claim that non-PPI records were observed via PPI REST.
No provider connection, runtime DB, watch, or order route is used here.
"""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass


NOW = datetime.now(timezone.utc).isoformat()


def row(source, *, ticker="TEST", settlement_source="PPI_FIELD", status="AVAILABLE"):
    return {
        "ticker": ticker,
        "instrument_type": "ACCIONES",
        "market": "BYMA",
        "currency": "ARS",
        "settlement": "A-24HS",
        "settlement_source": settlement_source,
        "status": status,
        "capability": "READY_PAPER_SPOT",
        "last_seen_at": NOW,
        "description": "",
        "metadata_json": json.dumps({
            "_discovery_source": source,
            "_provider_instrument_type": "ACCIONES",
            "ticker": ticker, "type": "ACCIONES", "market": "BYMA",
            "currency": "ARS",
            **({"settlement": "A-24HS"} if settlement_source == "PPI_FIELD" else {}),
        }),
    }


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


class MassContractProvenanceA600Tests(unittest.TestCase):
    def test_ppi_source_uses_only_native_settlement_for_full_key_evidence(self):
        for settlement_source in ("PPI_FIELD", "REQUEST_CANDIDATE"):
            with self.subTest(settlement_source=settlement_source):
                records, skipped, identities = mass.planned_records([
                    row("PPI_PRIMARY", settlement_source=settlement_source)
                ])
                if settlement_source == "REQUEST_CANDIDATE":
                    self.assertEqual(records, [])
                    self.assertEqual(identities, {})
                    self.assertEqual(skipped, {"PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED": 1})
                else:
                    self.assertEqual(skipped, {})
                    self.assertEqual(identities, {"ACCIONES": 1})
                    self.assertEqual([item["source_class"] for item in records], [
                        "PPI_STRUCTURED_API", "DERIVED_OFFICIAL_RULE"
                    ])
                    self.assertEqual(records[0]["evidence"]["provider_catalog_status"],
                                     "AVAILABLE")

    def test_iol_and_legacy_with_ppi_looking_settlement_never_become_ppi(self):
        for source in ("IOL_COMPLEMENTARY", "LEGACY_CATALOG"):
            with self.subTest(source=source):
                records, skipped, identities = mass.planned_records([
                    row(source, settlement_source="PPI_FIELD")
                ])
                self.assertEqual(records, [])
                self.assertEqual(identities, {})
                self.assertEqual(skipped, {"NOT_PPI_PRIMARY": 1})

    def test_availability_marker_cannot_replace_discovery_proof(self):
        suspect = row("IOL_COMPLEMENTARY", settlement_source="REQUEST_CANDIDATE")
        metadata = json.loads(suspect["metadata_json"])
        metadata["_availability_source"] = "PPI_PRIMARY+IOL_COMPLEMENTARY"
        suspect["metadata_json"] = json.dumps(metadata)
        records, skipped, identities = mass.planned_records([suspect])
        self.assertEqual((records, skipped, identities),
                         ([], {"NOT_PPI_PRIMARY": 1}, {}))

    def test_missing_or_inconsistent_primary_evidence_fails_closed(self):
        candidates = [
            row("PPI_PRIMARY", settlement_source="IOL_COMPLEMENTARY"),
            row("", settlement_source="PPI_FIELD"),
            row("PPI_PRIMARY", status="STALE"),
        ]
        records, skipped, identities = mass.planned_records(candidates)
        self.assertEqual(records, [])
        self.assertEqual(identities, {})
        self.assertEqual(skipped, {"NOT_PPI_PRIMARY": 2, "NOT_AVAILABLE": 1})

    def test_sqlite_mass_collection_does_not_publish_non_ppi_snapshots(self):
        with tempfile.TemporaryDirectory(prefix="rc6-a600-mass-") as temp:
            store = Store(Path(temp) / "catalog.db")
            catalog.init_schema(store)
            with store.connect() as connection:
                for symbol, source in (
                    ("PPI", "PPI_PRIMARY"),
                    ("IOL", "IOL_COMPLEMENTARY"),
                    ("OLD", "LEGACY_CATALOG"),
                ):
                    record = catalog.normalize_record({
                        "ticker": symbol,
                        "type": "ACCIONES",
                        "market": "BYMA",
                        "currency": "Pesos",
                        "settlement": "A-24HS",
                        "_discovery_source": source,
                    }, "A-24HS", NOW, "A600-SYNTHETIC")
                    catalog.persist(connection, record)

            result = mass.collect(store, run_id="A600-SYNTHETIC-READONLY-TEST")
            self.assertEqual(result["catalog_available"], 3)
            self.assertEqual(result["identities_written"], {"ACCIONES": 1})
            self.assertEqual(result["skipped"], {"NOT_PPI_PRIMARY": 2})
            self.assertEqual(result["real_routes_used"], [])
            current = evidence.current_records(store)
            self.assertEqual(len(current), 2)
            self.assertEqual({record["ticker"] for record in current}, {"PPI"})


if __name__ == "__main__":
    unittest.main()

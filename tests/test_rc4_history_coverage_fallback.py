from __future__ import annotations

from pathlib import Path
import sqlite3

import dd_history_metrics_hf6 as hm


def _observer_db():
    c=sqlite3.connect(":memory:")
    c.executescript("""
    CREATE TABLE candidate_universe(
      ticker TEXT,instrument_type TEXT,settlement TEXT,market TEXT,
      can_simulate INTEGER,status TEXT,detail TEXT,last_checked_at TEXT);
    CREATE TABLE production_history(
      symbol TEXT,instrument_type TEXT,settlement TEXT,date_from TEXT,date_to TEXT,
      downloaded_at TEXT,row_count INTEGER,payload_json TEXT);
    CREATE TABLE production_history_attempts(
      symbol TEXT,instrument_type TEXT,settlement TEXT,attempted_at TEXT,
      state TEXT,valid_rows INTEGER,detail TEXT);
    """)
    c.executemany(
        "INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
        [
            ("GGAL","ACCIONES","A-24HS","BYMA",1,"AVAILABLE","","2026-09-03"),
            ("YPFD","ACCIONES","A-24HS","BYMA",1,"AVAILABLE","","2026-09-03"),
            ("GD30","BONOS","A-24HS","BYMA",0,"AVAILABLE","","2026-09-03"),
        ],
    )
    c.executemany(
        "INSERT INTO production_history VALUES(?,?,?,?,?,?,?,?)",
        [
            ("GGAL","ACCIONES","A-24HS","2026-01-01","2026-09-03","2026-09-03T10:00:00Z",100,"[]"),
            ("YPFD","ACCIONES","A-24HS","2026-01-01","2026-09-03","2026-09-03T10:01:00Z",90,"[]"),
            ("GD30","BONOS","A-24HS","2026-02-01","2026-09-03","2026-09-03T10:02:00Z",80,"[]"),
        ],
    )
    return c


def test_missing_v2_db_is_not_available(tmp_path: Path):
    result=hm.v2_store_metrics(tmp_path/"missing.db")
    assert result["available"] is False
    assert result["reason"] == "V2_DB_NOT_PRESENT"


def test_existing_legacy_db_without_v2_schema_is_not_available(tmp_path: Path):
    path=tmp_path/"market_history.db"
    c=sqlite3.connect(path)
    c.execute("CREATE TABLE market_historical_ohlcv(symbol TEXT,date TEXT)")
    c.close()
    result=hm.v2_store_metrics(path)
    assert result["available"] is False
    assert result["reason"] == "V2_SCHEMA_NOT_PRESENT"
    assert set(result["missing_tables"]) == {"history_canonical_v2","history_versions_v2"}


def test_legacy_family_coverage_is_truthful_and_labelled():
    c=_observer_db()
    metrics=hm.observer_history_metrics(c)
    assert metrics["target_by_family"] == {"ACCIONES":2,"BONOS":1}
    assert metrics["legacy_identities_total"] == 3
    assert metrics["legacy_rows_total"] == 270
    assert metrics["legacy_history_by_family"]["ACCIONES"]["identities"] == 2
    assert metrics["legacy_history_by_family"]["ACCIONES"]["rows"] == 190
    assert metrics["legacy_history_by_family"]["BONOS"]["identities"] == 1
    assert metrics["legacy_identity_semantics"] == "LEGACY_SYMBOL_TYPE_SETTLEMENT"
    effective=hm.effective_family_coverage(
        metrics,{"available":False,"reason":"V2_SCHEMA_NOT_PRESENT"})
    assert effective["source"] == "PPI_LEGACY_FALLBACK"
    assert effective["identities_total"] == 3
    assert effective["by_family"]["ACCIONES"]["identities"] == 2
    c.close()


def test_v2_schema_takes_priority_and_counts_full_identity(tmp_path: Path):
    path=tmp_path/"market_history.db"
    c=sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE history_versions_v2(id INTEGER PRIMARY KEY);
    CREATE TABLE history_canonical_v2(
      symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,date TEXT);
    """)
    c.executemany(
        "INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?)",
        [
            ("GGAL","ACCIONES","BYMA","A-24HS","2026-09-01"),
            ("GGAL","ACCIONES","BYMA","A-24HS","2026-09-02"),
            ("GGAL","ACCIONES","OTRO","A-24HS","2026-09-02"),
        ],
    )
    c.close()
    v2=hm.v2_store_metrics(path)
    assert v2["available"] is True
    assert v2["identities"] == 2
    assert v2["canonical_rows"] == 3
    assert v2["by_family"]["ACCIONES"]["identities"] == 2
    effective=hm.effective_family_coverage(
        {"legacy_identities_total":99,"legacy_history_by_family":{}},v2)
    assert effective["source"] == "HISTORY_STORE_V2"
    assert effective["identities_total"] == 2

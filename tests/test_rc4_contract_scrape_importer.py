from __future__ import annotations

import sqlite3

import cn_contract_scrape_importer_rc4 as importer
import cp_contract_evidence_v2_hf6 as evidence
from cq_contract_readiness_hf6 import canonical_family


class Store:
    def __init__(self,path): self.path=str(path)
    def connect(self):
        c=sqlite3.connect(self.path)
        c.row_factory=sqlite3.Row
        return c


def payload(*,auth="AUTHENTICATED"):
    return {
        "schema":importer.SCRAPER_SCHEMA,
        "observed_at":"2026-09-03T17:00:00+00:00",
        "safety":{"order_posts":0,"mutation_requests":0,"tokens_persisted":False,
                  "cookies_persisted":False,"raw_html_persisted":False,"password_persisted":False},
        "auth":{"status":auth},
        "families":{
            "FCI-EXTERIOR":{"sources":[
                {"route":"/Cotizaciones/FCIExterior","http":200,"authenticated_target_reached":True,
                 "title":"FCI","table_count":1,"detected_fields":["moneda"],
                 "tables":[{"headers":["Moneda"],"rows":[["USD"]],"row_count_sampled":1}]},
                {"route":"/Operar/FCIExterior","http":200,"authenticated_target_reached":True,
                 "title":"Operar FCI","table_count":0,"detected_fields":["cantidad"],"tables":[]},
            ]}
        },
    }


def test_family_aliases_are_canonical():
    assert canonical_family("FCI-EXTERIOR") == "FCI_EXTERIOR"
    assert canonical_family("ACCIONES-USA") == "ACCIONES_USA"
    assert canonical_family("OBLIGACIONES NEGOCIABLES") == "ON"


def test_importer_versions_distinct_routes_without_false_change(tmp_path):
    store=Store(tmp_path/"db.sqlite")
    result=importer.import_payload(store,payload(),run_id="run-1")
    assert result["state"] == "OK"
    assert result["recorded"] == 2
    assert result.get("changed",0) == 0
    with store.connect() as c:
        current=c.execute("""SELECT cur.family,cur.ticker,snap.source_ref
          FROM contract_evidence_v2_current cur
          JOIN contract_evidence_v2_snapshots snap ON snap.snapshot_id=cur.snapshot_id
          ORDER BY cur.ticker""").fetchall()
        assert len(current) == 2
        assert {row["family"] for row in current} == {"FCI_EXTERIOR"}
        assert len({row["ticker"] for row in current}) == 2
        run=c.execute("SELECT * FROM contract_evidence_v2_runs WHERE run_id='run-1'").fetchone()
        assert run["state"] == "OK" and run["recorded"] == 2


def test_two_factor_is_logged_fail_closed_without_snapshots(tmp_path):
    store=Store(tmp_path/"db.sqlite")
    result=importer.import_payload(store,payload(auth="TWO_FACTOR_REQUIRED_FAIL_CLOSED"),run_id="run-2")
    assert result["state"] == "BLOCKED_AUTH"
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM contract_evidence_v2_snapshots").fetchone()[0] == 0
        row=c.execute("SELECT state,blocked FROM contract_evidence_v2_runs WHERE run_id='run-2'").fetchone()
        assert tuple(row) == ("BLOCKED_AUTH",1)


def test_safety_violation_is_rejected_before_persistence(tmp_path):
    store=Store(tmp_path/"db.sqlite")
    bad=payload(); bad["safety"]["order_posts"]=1
    try:
        importer.import_payload(store,bad,run_id="run-bad")
    except ValueError as exc:
        assert str(exc) == "PPI_WEB_SCRAPE_SAFETY_VIOLATION"
    else:
        raise AssertionError("safety violation must fail")
    evidence.init_schema(store)
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM contract_evidence_v2_runs").fetchone()[0] == 0

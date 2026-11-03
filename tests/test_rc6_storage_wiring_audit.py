import hashlib
import sqlite3

from scripts.rc6_storage_wiring_audit import inspect_database, inspect_wiring


def test_storage_audit_is_bounded_and_does_not_mutate_revisions(tmp_path):
    path=tmp_path/"history.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE history_versions_v2(
            id INTEGER PRIMARY KEY,symbol TEXT,instrument_type TEXT,market TEXT,
            currency TEXT,settlement TEXT,date TEXT,price_basis TEXT,
            adjustment_basis TEXT,source TEXT,payload_hash TEXT,
            previous_version_id INTEGER,version_known_at TEXT)""")
        for i in range(3):
            c.execute("INSERT INTO history_versions_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i+1,"GGAL","ACCIONES","BYMA","ARS","CI","2026-10-20",
                 "RAW","RAW_NO_ADJUSTMENT","PPI_API",str(i),i,"2026-10-21"))
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=inspect_database(path,sample=2)
    assert result["history"]=={
        "state":"SAMPLED_ONLY","sampled_rows":2,"same_exact_revision_key_in_sample":0,
        "whole_database_duplicates":"NO_VERIFICADO","preserve_aba_revisions":True}
    assert result["deletion_allowed"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_wiring_absence_is_only_a_review_candidate(tmp_path):
    (tmp_path/"app.py").write_text("import used\n",encoding="utf-8")
    (tmp_path/"used.py").write_text("x=1\n",encoding="utf-8")
    (tmp_path/"dynamic.py").write_text("x=2\n",encoding="utf-8")
    result=inspect_wiring(tmp_path)
    assert "dynamic" in result["unreferenced_review_candidates"]
    assert "used" not in result["unreferenced_review_candidates"]
    assert result["deletion_allowed"] is False

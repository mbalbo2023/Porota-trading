"""PPI same-session EOD receipts certify reports, never PAPER execution."""
import json
from hashlib import sha256
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cg_paper_workspace import artifact_root, mark_new_database
import rc6_decision_evidence_report as decisions
from rc6_postclose_review import build_review
from rc6_snapshot import build_snapshot


ART = ZoneInfo("America/Argentina/Buenos_Aires")
PENDING = "PROVISIONAL_POSTCLOSE_SOURCE_PENDING"
COHORT = sha256(b"synthetic-six-column-full-key-history-cohort").hexdigest()
RAW_PROVENANCE = "source_raw_provenance=OBSERVER_PPI_HISTORY_CANONICAL_V2_RAW_FULL_KEY"


def _clock(day="2026-11-02", hour="18:45"):
    return datetime.fromisoformat(f"{day}T{hour}:00-03:00")


def _provision_scratch(path):
    root = artifact_root(path) / "sqlite-read-scratch"
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    return root


def _db(path, monkeypatch):
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    _provision_scratch(path)
    with sqlite3.connect(path) as conn:
        mark_new_database(conn)
        conn.execute("""CREATE TABLE source_sync(
            source TEXT PRIMARY KEY,status TEXT NOT NULL,last_attempt_at TEXT NOT NULL,
            last_success_at TEXT,items INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL)""")


def _row(path, *, status="VERDE", received="2026-11-02T18:20:00-03:00",
         detail=None):
    if detail is None:
        detail = ("session=2026-11-02; source_session_verified=2026-11-02:1/1; "
                  f"{RAW_PROVENANCE}; "
                  "Lote aceptado=1/1; cobertura exacta 1/1; "
                  f"cohort={COHORT}; consumer=PPI_HISTORY_INGEST_MONITOR; "
                  "session_requested=2026-11-02; readiness=NONE")
    with sqlite3.connect(path) as conn:
        conn.execute("""INSERT OR REPLACE INTO source_sync VALUES(?,?,?,?,?,?)""",
                     ("PPI_PRODUCTION_HISTORY", status, received, received, 1, detail))


def _paper_payload():
    return {"state": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0},
            "closed": [], "decisions": []}


def test_requested_session_is_not_a_verified_ppi_eod_receipt(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database, detail="session=2026-11-02; readiness=NONE")
    assert postclose_eod_receipt(_clock(), db_path=database)["state"] == PENDING
    _row(database, detail="session=2026-11-02; source_session_verified=2026-11-02:0/1")
    assert postclose_eod_receipt(_clock(), db_path=database)["state"] == PENDING
    _row(database, detail="session=2026-11-02; source_session_verified=2026-11-02:1/2")
    assert postclose_eod_receipt(_clock(), db_path=database)["state"] == PENDING
    _row(database)
    evidence = postclose_eod_receipt(_clock(), db_path=database)
    assert (evidence["state"], evidence["verified_exact_keys"],
            evidence["required_exact_keys"]) == ("VERIFIED_SOURCE_SESSION", 1, 1)


def test_source_receipt_rejects_early_future_stale_and_weekend(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    assert (postclose_eod_receipt(_clock(hour="18:08"), db_path=database)["reason"]
            == "BYMA_EOD_PUBLICATION_FLOOR_NOT_REACHED")
    assert postclose_eod_receipt(_clock(hour="18:19"), db_path=database)["state"] == PENDING
    assert postclose_eod_receipt(_clock(day="2026-11-03"), db_path=database)["state"] == PENDING
    assert postclose_eod_receipt(_clock(day="2026-11-07"), db_path=database)["state"] == PENDING
    _row(database, received="2026-11-02T18:14:59-03:00")
    assert postclose_eod_receipt(_clock(), db_path=database)["state"] == PENDING


def test_source_receipt_rejects_alias_view_and_symlinked_database(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    view_db = tmp_path / "view.db"
    with sqlite3.connect(view_db) as conn:
        conn.execute("""CREATE VIEW source_sync AS SELECT
            'PPI_PRODUCTION_HISTORY' AS source, 'VERDE' AS status,
            '2026-11-02T18:20:00-03:00' AS last_success_at,
            'source_session_verified=2026-11-02:1/1' AS detail""")
    assert postclose_eod_receipt(_clock(), db_path=view_db)["state"] == PENDING
    real = tmp_path / "real.db"
    _db(real, monkeypatch)
    _row(real)
    alias = tmp_path / "alias.db"
    alias.symlink_to(real)
    assert (postclose_eod_receipt(_clock(), db_path=alias)["reason"]
            == "PAPER_SOURCE_DATABASE_UNVERIFIED")


def test_eod_source_wal_reader_does_not_change_writer_data(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "wal-paper.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
    _provision_scratch(database)
    with sqlite3.connect(database) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        mark_new_database(writer)
        writer.execute("""CREATE TABLE source_sync(
            source TEXT PRIMARY KEY,status TEXT NOT NULL,last_attempt_at TEXT NOT NULL,
            last_success_at TEXT,items INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL)""")
        writer.execute("INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
                       ("PPI_PRODUCTION_HISTORY", "VERDE", "2026-11-02T18:20:00-03:00",
                        "2026-11-02T18:20:00-03:00", 1,
                        "session=2026-11-02; source_session_verified=2026-11-02:1/1; "
                        f"{RAW_PROVENANCE}; "
                        "Lote aceptado=1/1; cobertura exacta 1/1; "
                        f"cohort={COHORT}; consumer=PPI_HISTORY_INGEST_MONITOR; "
                        "session_requested=2026-11-02; readiness=NONE"))
        writer.commit()
        wal = tmp_path / "wal-paper.db-wal"
        shm = tmp_path / "wal-paper.db-shm"
        assert wal.exists() and shm.exists()
        original_files = {path.name: (path.stat().st_size, path.stat().st_mtime_ns)
                          for path in (database, wal, shm)}
        receipt = postclose_eod_receipt(_clock(), db_path=database)
        assert receipt["state"] == "VERIFIED_SOURCE_SESSION"
        assert writer.execute("SELECT COUNT(*) FROM source_sync").fetchone()[0] == 1
        assert {path.name: (path.stat().st_size, path.stat().st_mtime_ns)
                for path in (database, wal, shm)} == original_files
        assert shm.exists()


def test_only_verified_current_receipt_certifies_snapshot_and_review(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt, verified_current_source

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    at = _clock()
    receipt = postclose_eod_receipt(at, db_path=database)
    assert verified_current_source(receipt, at) is True
    forged = {**receipt, "required_exact_keys": 2}
    assert verified_current_source(forged, at) is False
    verified = build_snapshot("postclose", at, _paper_payload(), eod_source=receipt)
    assert verified["status"] == "VERIFIED"
    assert verified["paper_safety_status"] == "VERIFIED"
    assert build_review(verified, at)["status"] == "VERIFIED"
    pending = build_snapshot("postclose", at, _paper_payload(), eod_source=forged)
    assert pending["status"] == PENDING
    assert pending["paper_safety_status"] == "VERIFIED"
    review = build_review(pending, at)
    assert review["status"] == PENDING
    assert "PPI_EOD_CURRENT_SESSION_SOURCE_PENDING" in review["findings"]


def test_decision_report_cannot_override_prior_provisional_snapshot(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE decision_evidence_snapshots(
            decision_key TEXT,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT)""")
        conn.execute("""CREATE TABLE paper_positions(
            paper_id TEXT,status TEXT,net_pnl TEXT,closed_at TEXT)""")
    snapshot_path = tmp_path / "snapshot.json"
    now = _clock()
    provisional = build_snapshot("postclose", now, _paper_payload())
    snapshot_path.write_text(json.dumps(provisional), encoding="utf-8")
    _row(database)
    first = decisions.publish(db_path=database, root=tmp_path,
                              postclose=True, source_at=now,
                              snapshot_path=snapshot_path)
    assert first["postclose_report_status"] == PENDING
    assert first["postclose_eod_source"]["state"] == "VERIFIED_SOURCE_SESSION"
    certified = build_snapshot("postclose", now, _paper_payload(),
                               eod_source=postclose_eod_receipt(now, db_path=database))
    snapshot_path.write_text(json.dumps(certified), encoding="utf-8")
    second = decisions.publish(db_path=database, root=tmp_path,
                               postclose=True, source_at=now,
                               snapshot_path=snapshot_path)
    assert second["postclose_report_status"] == "VERIFIED"
    compact = json.loads((tmp_path / decisions.COUNTERFACTUAL_NAME).read_text())
    assert compact["postclose_report_status"] == "VERIFIED"
    assert compact["real_orders_authorized"] is False


def test_fake_four_column_source_sync_cannot_certify_eod_even_with_real_identity(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "fake-paper.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
    _provision_scratch(database)
    with sqlite3.connect(database) as connection:
        mark_new_database(connection)
        connection.execute("""CREATE TABLE source_sync(
            source TEXT PRIMARY KEY,status TEXT,last_success_at TEXT,detail TEXT)""")
        connection.execute("INSERT INTO source_sync VALUES(?,?,?,?)",
            ("PPI_PRODUCTION_HISTORY", "VERDE", "2026-11-02T18:20:00-03:00",
             "source_session_verified=2026-11-02:1/1"))
    receipt = postclose_eod_receipt(_clock(), db_path=database)
    assert receipt["state"] == PENDING
    assert receipt["reason"] == "PPI_EOD_SOURCE_SCHEMA_UNVERIFIED"


def test_fake_six_column_source_sync_without_authoritative_pk_cannot_certify(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "fake-six-column.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
    _provision_scratch(database)
    with sqlite3.connect(database) as connection:
        mark_new_database(connection)
        connection.execute("""CREATE TABLE source_sync(
            source TEXT,status TEXT,last_attempt_at TEXT,last_success_at TEXT,
            items INTEGER,detail TEXT)""")
        connection.execute("INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
            ("PPI_PRODUCTION_HISTORY", "VERDE", "2026-11-02T18:20:00-03:00",
             "2026-11-02T18:20:00-03:00", 1,
             "session=2026-11-02; source_session_verified=2026-11-02:1/1; "
             f"{RAW_PROVENANCE}; "
             "Lote aceptado=1/1; cobertura exacta 1/1; "
             f"cohort={COHORT}; consumer=PPI_HISTORY_INGEST_MONITOR; "
             "session_requested=2026-11-02; readiness=NONE"))
    receipt = postclose_eod_receipt(_clock(), db_path=database)
    assert receipt["state"] == PENDING
    assert receipt["reason"] == "PPI_EOD_SOURCE_SCHEMA_UNVERIFIED"


def test_current_receipt_requires_source_details_raw_provenance(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database, detail="session=2026-11-02; source_session_verified=2026-11-02:1/1")
    evidence = postclose_eod_receipt(_clock(), db_path=database)
    assert evidence["state"] == PENDING
    assert evidence["reason"] == "PPI_EOD_SOURCE_PROVENANCE_UNVERIFIED"


@pytest.mark.parametrize("token", (
    "", "source_raw_provenance=IOL_PRODUCTION_HISTORY",
    f"{RAW_PROVENANCE}; {RAW_PROVENANCE}",
))
def test_attested_eod_requires_exactly_one_genuine_raw_provenance_token(
        tmp_path, monkeypatch, token):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    detail = ("session=2026-11-02; source_session_verified=2026-11-02:1/1; "
              f"{token}; Lote aceptado=1/1; cobertura exacta 1/1; "
              f"cohort={COHORT}; consumer=PPI_HISTORY_INGEST_MONITOR; "
              "session_requested=2026-11-02; readiness=NONE")
    _row(database, detail=detail)
    evidence = postclose_eod_receipt(_clock(), db_path=database)
    assert evidence["state"] == PENDING
    assert evidence["reason"] == "PPI_EOD_SOURCE_PROVENANCE_UNVERIFIED"


def test_shared_pure_eod_detail_validator_exposes_strict_producer_contract(monkeypatch):
    import rc6_postclose_source_contract as contract

    detail = ("session=2026-11-02; source_session_verified=2026-11-02:2/2; "
              f"{RAW_PROVENANCE}; Lote aceptado=2/2; cobertura exacta 2/2; "
              f"cohort={COHORT}; consumer=PPI_HISTORY_INGEST_MONITOR; "
              "session_requested=2026-11-02; readiness=NONE")

    def unexpected_sqlite(*_args, **_kwargs):
        raise AssertionError("Shared EOD detail validator must be pure")

    monkeypatch.setattr(sqlite3, "connect", unexpected_sqlite)
    result = contract.verified_ppi_eod_detail(detail, "2026-11-02")
    assert result == {"verified_exact_keys": 2, "required_exact_keys": 2,
                      "source_cohort_sha256": COHORT}
    assert contract.verified_ppi_eod_detail(detail, "2026-11-03") is None
    assert contract.verified_ppi_eod_detail(detail.replace(RAW_PROVENANCE, ""),
                                          "2026-11-02") is None
    assert contract.verified_ppi_eod_detail(detail + "; " + RAW_PROVENANCE,
                                          "2026-11-02") is None


def test_foreign_database_path_and_legacy_namespace_cannot_certify(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    canonical = tmp_path / "canonical.db"
    _db(canonical, monkeypatch)
    _row(canonical)
    foreign = tmp_path / "foreign.db"
    _db(foreign, monkeypatch)
    _row(foreign)
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(canonical))
    evidence = postclose_eod_receipt(_clock(), db_path=foreign)
    assert evidence["state"] == PENDING
    assert evidence["reason"] == "PAPER_SOURCE_DATABASE_UNVERIFIED"

    with sqlite3.connect(canonical) as connection:
        connection.execute("UPDATE paper_workspace SET namespace='BOGUS'")
    assert postclose_eod_receipt(_clock(), db_path=canonical)["state"] == PENDING


def test_wal_without_existing_shm_cannot_create_shm_in_source(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "wal-no-shm.db"
    _db(database, monkeypatch)
    _row(database)
    with sqlite3.connect(database) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("UPDATE source_sync SET items=2")
        writer.commit()
        shm = tmp_path / "wal-no-shm.db-shm"
        wal = tmp_path / "wal-no-shm.db-wal"
        assert shm.is_file() and wal.is_file()
        shm.unlink()  # Synthetic adversarial condition: source -wal without source -shm.
        assert not shm.exists()
        source_before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
                         for p in (database, wal)}
        evidence = postclose_eod_receipt(_clock(), db_path=database)
        assert evidence["state"] == "VERIFIED_SOURCE_SESSION"
        assert not shm.exists()
        assert source_before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
                                 for p in (database, wal)}


def test_verified_receipt_rejects_forged_identity_fields(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt, verified_current_source

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    valid = postclose_eod_receipt(_clock(), db_path=database)
    assert verified_current_source(valid, _clock())
    for field in ("paper_dataset_id", "source_detail_sha256", "source_cohort_sha256"):
        forged = {**valid, field: "0"}
        assert not verified_current_source(forged, _clock()), field


def test_source_sqlite_is_never_opened_by_receipt_reader(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    original = sqlite3.connect
    observed = []

    def checked_connect(path, *args, **kwargs):
        observed.append(str(path))
        assert str(database) not in str(path), "SQLite tried to open the source file"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", checked_connect)
    receipt = postclose_eod_receipt(_clock(), db_path=database)
    assert receipt["state"] == "VERIFIED_SOURCE_SESSION"
    assert observed, "No verified private SQLite image was opened"


def test_reader_fail_during_private_copy_verification_cannot_report_verified(tmp_path, monkeypatch):
    import contextlib
    import rc6_postclose_source_contract as contract
    from rc6_audit_evidence.sqlite_scratch import SnapshotError

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)

    @contextlib.contextmanager
    def changed_source(*args, **kwargs):
        with sqlite3.connect(database) as connection:
            yield connection
        raise SnapshotError("SOURCE_SNAPSHOT_BUSY")

    monkeypatch.setattr(contract, "readonly_copy", changed_source, raising=False)
    result = contract.postclose_eod_receipt(_clock(), db_path=database)
    assert result["state"] == PENDING
    assert result["reason"] == "PPI_EOD_RECEIPT_READ_UNVERIFIED"


def test_decision_projection_sqlite_also_uses_bounded_private_copy(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE decision_evidence_snapshots(
            decision_key TEXT,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT)""")
        connection.execute("""CREATE TABLE paper_positions(
            paper_id TEXT,status TEXT,net_pnl TEXT,closed_at TEXT)""")
    original = sqlite3.connect
    opened = []

    def checked_connect(path, *args, **kwargs):
        opened.append(str(path))
        assert str(database) not in str(path), "Decision report opened SOURCE SQLite"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", checked_connect)
    result = decisions.build(db_path=database)
    assert result["read_only"] is True
    assert result["decisions"] == []
    assert opened


def test_decision_report_cannot_combine_receipts_from_different_paper_datasets(
        tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    old_database = tmp_path / "old-paper.db"
    _db(old_database, monkeypatch)
    _row(old_database)
    snapshot_path = tmp_path / "snapshot.json"
    now = _clock()
    first_receipt = postclose_eod_receipt(now, db_path=old_database)
    snapshot_path.write_text(json.dumps(build_snapshot(
        "postclose", now, _paper_payload(), eod_source=first_receipt)), encoding="utf-8")

    new_database = tmp_path / "new-paper.db"
    _db(new_database, monkeypatch)
    _row(new_database)
    with sqlite3.connect(new_database) as connection:
        connection.execute("""CREATE TABLE decision_evidence_snapshots(
            decision_key TEXT,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT)""")
        connection.execute("""CREATE TABLE paper_positions(
            paper_id TEXT,status TEXT,net_pnl TEXT,closed_at TEXT)""")
    current = postclose_eod_receipt(now, db_path=new_database)
    assert current["state"] == first_receipt["state"] == "VERIFIED_SOURCE_SESSION"
    assert current["paper_dataset_id"] != first_receipt["paper_dataset_id"]
    report = decisions.publish(db_path=new_database, root=tmp_path,
                               postclose=True, source_at=now, snapshot_path=snapshot_path)
    assert report["postclose_report_status"] == PENDING


def test_decision_report_requires_same_source_detail_as_earlier_snapshot(
        tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE decision_evidence_snapshots(
            decision_key TEXT,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT)""")
        connection.execute("""CREATE TABLE paper_positions(
            paper_id TEXT,status TEXT,net_pnl TEXT,closed_at TEXT)""")
    now = _clock()
    snapshot_path = tmp_path / "snapshot.json"
    verified_first = postclose_eod_receipt(now, db_path=database)
    snapshot_path.write_text(json.dumps(build_snapshot(
        "postclose", now, _paper_payload(), eod_source=verified_first)), encoding="utf-8")
    _row(database, detail=("session=2026-11-02; source_session_verified=2026-11-02:1/1; "
                           f"{RAW_PROVENANCE}; "
                           "Lote aceptado=2/2; cobertura exacta 1/1; "
                           f"cohort={COHORT}; consumer=PPI_HISTORY_INGEST_MONITOR; "
                           "session_requested=2026-11-02; readiness=NONE"))
    verified_second = postclose_eod_receipt(now, db_path=database)
    assert verified_second["state"] == "VERIFIED_SOURCE_SESSION"
    assert verified_second["source_detail_sha256"] != verified_first["source_detail_sha256"]
    report = decisions.publish(db_path=database, root=tmp_path,
                               postclose=True, source_at=now, snapshot_path=snapshot_path)
    assert report["postclose_report_status"] == PENDING


@pytest.mark.parametrize("mutation", (
    {"mode": "LIVE"}, {"mode": "SIMULATION"},
    {"real_orders_sent": 7}, {"real_orders_sent": False},
    {"real_orders_sent": "0"}, {"real_orders_sent": 0.0},
    {"urgent_alerts": None}, {"urgent_alerts": ["UNSAFE_RUNTIME"]},
    {"paper_safety_status": "NO_VERIFICADO"},
))
def test_decision_report_rejects_forged_paper_safety_in_snapshot_json(
        tmp_path, monkeypatch, mutation):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE decision_evidence_snapshots(
            decision_key TEXT,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT)""")
        connection.execute("""CREATE TABLE paper_positions(
            paper_id TEXT,status TEXT,net_pnl TEXT,closed_at TEXT)""")
    now = _clock()
    verified = build_snapshot("postclose", now, _paper_payload(),
                              eod_source=postclose_eod_receipt(now, db_path=database))
    assert verified["status"] == "VERIFIED"
    verified.update(mutation)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(verified), encoding="utf-8")
    report = decisions.publish(db_path=database, root=tmp_path, postclose=True,
                               source_at=now, snapshot_path=snapshot_path)
    assert report["postclose_report_status"] == "INSUFFICIENT_EVIDENCE"
    assert report["postclose_report_reason"] == "PAPER_SAFETY_CONTRADICTION"
    assert report["real_orders_authorized"] is False


@pytest.mark.parametrize("status,expected_exit", (
    ("VERIFIED", 0), (PENDING, 0), ("INSUFFICIENT_EVIDENCE", 2),
))
def test_decision_report_cli_exit_code_preserves_source_amber_but_blocks_paper_red(
        monkeypatch, capsys, status, expected_exit):
    monkeypatch.setattr(decisions, "publish", lambda **_kwargs: {
        "postclose_report_status": status, "decisions": [], "read_only": True,
    })
    assert decisions.main() == expected_exit
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status


def test_missing_owned_scratch_yields_provisional_without_temp_fallback(tmp_path, monkeypatch):
    from rc6_postclose_source_contract import postclose_eod_receipt

    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    root = artifact_root(database) / "sqlite-read-scratch"
    root.rmdir()  # Only fixture-owned empty scratch directory is removed.
    assert not root.exists()
    receipt = postclose_eod_receipt(_clock(), db_path=database)
    assert receipt["state"] == PENDING
    assert receipt["reason"] == "PPI_EOD_RECEIPT_READ_UNVERIFIED"
    assert not root.exists()


def test_postclose_report_copy_unavailable_publishes_provisional_not_verified(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    _db(database, monkeypatch)
    _row(database)
    root = artifact_root(database) / "sqlite-read-scratch"
    root.rmdir()
    result = decisions.publish(db_path=database, root=tmp_path,
                               postclose=True, source_at=_clock(),
                               snapshot_path=tmp_path / "missing-snapshot.json")
    assert result["postclose_report_status"] == PENDING
    assert result["real_orders_authorized"] is False
    assert result["decisions"] == []

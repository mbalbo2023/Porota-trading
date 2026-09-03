from rc4_backup_coverage import CANONICAL_TARGETS, coverage, release_blockers


def test_missing_history_store_is_not_fake_backup():
    rows=coverage(existence={"observer_db":True,"history_db":False,"sre_vector_db":True},
                  backup_by_key={
                      "observer_db":{"state":"OK","finished_at":"2026-09-03T10:00:00Z","quick_check":"ok","source":"DAILY_BACKUP"},
                      "sre_vector_db":{"state":"OK","finished_at":"2026-09-03T10:00:00Z","source":"HOST_GENERAL_BACKUP"},
                  }, now="2026-09-03T16:00:00Z")
    by={r["key"]:r for r in rows}
    assert by["history_db"]["state"] == "NOT_CREATED"
    assert by["observer_db"]["state"] == "PROTECTED"


def test_existing_sqlite_without_restore_check_is_error():
    rows=coverage(existence={"observer_db":True},
                  backup_by_key={"observer_db":{"state":"OK","finished_at":"2026-09-03T15:00:00Z"}},
                  now="2026-09-03T16:00:00Z")
    row={r["key"]:r for r in rows}["observer_db"]
    assert row["state"] == "ERROR"


def test_existing_storage_without_backup_blocks_release():
    rows=coverage(existence={"observer_db":True,"history_db":True,"sre_vector_db":True},
                  backup_by_key={}, now="2026-09-03T16:00:00Z")
    blockers=release_blockers(rows)
    assert "observer_db:NO_BACKUP" in blockers
    assert "history_db:NO_BACKUP" in blockers
    assert "sre_vector_db:NO_BACKUP" in blockers


def test_directory_backup_does_not_require_sqlite_quick_check():
    rows=coverage(existence={"sre_vector_db":True},
                  backup_by_key={"sre_vector_db":{"state":"OK","finished_at":"2026-09-03T15:30:00Z","source":"HOST_GENERAL_BACKUP"}},
                  now="2026-09-03T16:00:00Z")
    assert {r["key"]:r for r in rows}["sre_vector_db"]["state"] == "PROTECTED"

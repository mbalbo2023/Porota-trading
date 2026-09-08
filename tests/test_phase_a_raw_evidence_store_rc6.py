import gzip
import json
from pathlib import Path
import sqlite3

import pytest

import fa_raw_evidence_store_rc6 as evidence


def env(tmp_path):
    return {
        "DATA_DIR": str(tmp_path / "data"),
        evidence.RAW_STORAGE_ENV: evidence.EXTERNAL_V1,
        evidence.COORDINATOR_ENV: evidence.EXTERNAL_V1,
    }


def wrapper(payload, *, symbol="GGAL", attempted="2026-09-08T10:00:00-03:00"):
    return {
        "symbol": symbol,
        "asset_class": "ACCIONES",
        "settlement": "A-24HS",
        "date_from": "2025-09-08",
        "date_to": "2026-09-08",
        "metadata": {"market": "BYMA"},
        "valid_rows": len(payload),
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "attempted": attempted,
    }


def sample_payload(price=102):
    return [{
        "date": "2026-09-05T17:00:00-03:00",
        "openingPrice": 100,
        "max": 105,
        "min": 99,
        "price": price,
        "volume": 123,
    }]


def manifest_rows(e):
    path = evidence.manifest_path(e)
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM ingest_manifests_v1 ORDER BY attempted_at,attempt_id"
        )]


def test_external_store_deduplicates_physical_raw_but_preserves_attempts(tmp_path):
    e = env(tmp_path)
    payload = sample_payload()
    first = evidence.archive_ppi_history_wrapper(
        row_key='["GGAL","ACCIONES","A-24HS","a1"]',
        wrapper=wrapper(payload), recorded_at="2026-09-08T10:00:00-03:00",
        quality="VALID_PAYLOAD", environ=e,
    )
    second = evidence.archive_ppi_history_wrapper(
        row_key='["GGAL","ACCIONES","A-24HS","a2"]',
        wrapper=wrapper(payload), recorded_at="2026-09-08T12:00:00-03:00",
        quality="VALID_PAYLOAD", environ=e,
    )
    assert first.raw_sha256 == second.raw_sha256
    assert first.object_created is True
    assert second.object_created is False
    rows = manifest_rows(e)
    assert len(rows) == 2
    assert len({r["attempt_id"] for r in rows}) == 2
    assert len({r["raw_sha256"] for r in rows}) == 1
    objects = list((evidence.evidence_root(e) / "objects").rglob("*.json.gz"))
    assert len(objects) == 1
    with gzip.open(objects[0], "rt", encoding="utf-8") as handle:
        assert json.load(handle) == payload
    metrics = evidence.manifest_metrics(environ=e)
    assert metrics["attempts"] == 2
    assert metrics["unique_raw_objects"] == 1
    assert metrics["dedupe_ratio"] == 2.0


def test_attempt_metadata_does_not_change_raw_content_hash(tmp_path):
    e = env(tmp_path)
    payload = sample_payload()
    a = evidence.archive_ppi_history_wrapper(
        row_key="attempt-a", wrapper=wrapper(payload, symbol="GGAL"),
        recorded_at="2026-09-08T10:00:00-03:00", quality="PARTIAL", environ=e,
    )
    other = wrapper(payload, symbol="YPFD")
    other["metadata"] = {"market": "BYMA", "extra": "different-attempt-metadata"}
    b = evidence.archive_ppi_history_wrapper(
        row_key="attempt-b", wrapper=other,
        recorded_at="2026-09-08T11:00:00-03:00", quality="VALID_PAYLOAD", environ=e,
    )
    assert a.raw_sha256 == b.raw_sha256
    assert a.object_relpath == b.object_relpath
    assert len(manifest_rows(e)) == 2


def test_existing_corrupt_object_fails_closed(tmp_path):
    e = env(tmp_path)
    receipt = evidence.archive_ppi_history_wrapper(
        row_key="attempt-a", wrapper=wrapper(sample_payload()),
        recorded_at="2026-09-08T10:00:00-03:00", quality="VALID_PAYLOAD", environ=e,
    )
    object_path = evidence.evidence_root(e) / receipt.object_relpath
    object_path.write_bytes(b"not-a-gzip-object")
    with pytest.raises(ValueError, match="RAW_OBJECT_CORRUPT"):
        evidence.archive_ppi_history_wrapper(
            row_key="attempt-b", wrapper=wrapper(sample_payload()),
            recorded_at="2026-09-08T11:00:00-03:00", quality="VALID_PAYLOAD", environ=e,
        )
    assert len(manifest_rows(e)) == 1


def test_coordinator_skips_competing_cross_process_style_trigger(tmp_path):
    e = env(tmp_path)
    with evidence.ppi_history_ingest_lease(environ=e) as first:
        assert first.acquired is True
        with evidence.ppi_history_ingest_lease(environ=e) as second:
            assert second.acquired is False
            assert second.reason == "ANOTHER_PPI_HISTORY_INGEST_ACTIVE"
    with evidence.ppi_history_ingest_lease(environ=e) as third:
        assert third.acquired is True


def test_legacy_mode_is_default_and_creates_no_external_paths(tmp_path):
    e = {"DATA_DIR": str(tmp_path / "data")}
    assert evidence.raw_storage_mode(e) == evidence.LEGACY
    assert evidence.coordinator_mode(e) == evidence.LEGACY
    with evidence.ppi_history_ingest_lease(environ=e) as lease:
        assert lease.acquired is True
        assert lease.reason == "LEGACY_NO_COORDINATOR"
    assert not evidence.evidence_root(e).exists()
    with pytest.raises(RuntimeError, match="EXTERNAL_EVIDENCE_NOT_ENABLED"):
        evidence.archive_ppi_history_wrapper(
            row_key="x", wrapper=wrapper(sample_payload()),
            recorded_at="2026-09-08T10:00:00-03:00", quality="VALID_PAYLOAD", environ=e,
        )

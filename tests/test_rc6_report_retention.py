from pathlib import Path

import os
import pytest

import rc6_report_retention as retention


def test_plan_is_dry_run_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("REPORT_RETENTION_APPLY", raising=False)
    (tmp_path / "daily.html").write_text("report", encoding="utf-8")
    plan = retention.plan(tmp_path)
    assert plan["apply"] is False
    assert plan["deletion"] == "DISABLED_UNTIL_CONSOLIDATED_ARTIFACT_VERIFIED"


def test_compression_round_trip(tmp_path):
    source = tmp_path / "daily.html"
    destination = tmp_path / "archive" / "daily.html.gz"
    source.write_bytes(b"paper report")
    result = retention.compress_verified(source, destination)
    assert result["source_sha256"]
    assert result["restored_sha256"] == result["source_sha256"]
    assert result["verified"] is True
    assert destination.exists()


def test_compression_rejects_source_symlink_without_creating_archive(tmp_path):
    original = tmp_path / "original.html"
    original.write_bytes(b"a real report")
    alias = tmp_path / "alias.html"
    alias.symlink_to(original)
    destination = tmp_path / "archive.gz"
    with pytest.raises(ValueError, match="REPORT_SOURCE_ALIAS"):
        retention.compress_verified(alias, destination)
    assert not destination.exists()
    assert original.read_bytes() == b"a real report"


def test_compression_rejects_existing_destination_without_overwrite(tmp_path):
    original = tmp_path / "daily.html"
    original.write_bytes(b"source")
    destination = tmp_path / "archive.gz"
    destination.write_bytes(b"keep existing archive")
    with pytest.raises(ValueError, match="REPORT_DESTINATION_EXISTS"):
        retention.compress_verified(original, destination)
    assert destination.read_bytes() == b"keep existing archive"


def test_compression_rejects_destination_symlink_without_overwrite(tmp_path):
    original = tmp_path / "daily.html"
    original.write_bytes(b"source")
    protected = tmp_path / "protected.html"
    protected.write_bytes(b"preserve this unrelated file")
    destination = tmp_path / "archive.gz"
    destination.symlink_to(protected)
    with pytest.raises(ValueError, match="REPORT_DESTINATION_ALIAS"):
        retention.compress_verified(original, destination)
    assert protected.read_bytes() == b"preserve this unrelated file"


def test_compression_rejects_same_inode_and_hardlink_source(tmp_path):
    original = tmp_path / "daily.html"
    original.write_bytes(b"source report must survive")
    with pytest.raises(ValueError, match="REPORT_DESTINATION_SOURCE_ALIAS"):
        retention.compress_verified(original, original)
    hardlink = tmp_path / "other-name.html"
    os.link(original, hardlink)
    destination = tmp_path / "archive.gz"
    with pytest.raises(ValueError, match="REPORT_SOURCE_ALIAS"):
        retention.compress_verified(hardlink, destination)
    assert original.read_bytes() == b"source report must survive"
    assert not destination.exists()

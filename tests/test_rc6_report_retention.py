from pathlib import Path

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
    assert destination.exists()

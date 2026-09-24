from scripts.porota_secret_scan import is_placeholder, scan_repository, scan_text


def test_known_placeholders_are_allowed():
    for value in ("ci-not-real", "CHANGE_ME", "${PPI_API_SECRET}", "<secret>", "..."):
        assert is_placeholder(value)


def test_sensitive_assignment_is_reported_without_secret_value():
    variable = "PPI_API_" + "SECRET"
    secret = "s3cr3t-" + "value-that-must-not-leak"
    findings = scan_text("fixture.env", f"{variable}={secret}\n")
    assert findings == [{
        "path": "fixture.env",
        "line": 1,
        "kind": "SENSITIVE_ASSIGNMENT",
        "variable": variable,
    }]
    assert secret not in repr(findings)


def test_token_pattern_is_detected_without_echoing_token():
    token = "AK" + "IA" + ("A" * 16)
    findings = scan_text("fixture.txt", token + "\n")
    assert findings[0]["kind"] == "AWS_ACCESS_KEY"
    assert token not in repr(findings)


def test_repository_scan_checks_requested_text_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    safe = root / "safe.env"
    bad = root / "bad.env"
    safe.write_text("TELEGRAM_BOT_TOKEN=ci-not-real\n", encoding="utf-8")
    variable = "GEMINI_API_" + "KEY"
    bad.write_text(variable + "=real-looking-secret-value\n", encoding="utf-8")

    result = scan_repository(root, ["safe.env", "bad.env"])

    assert result["status"] == "FAILED"
    assert result["scanned_text_files"] == 2
    assert result["findings"] == [{
        "path": "bad.env",
        "line": 1,
        "kind": "SENSITIVE_ASSIGNMENT",
        "variable": variable,
    }]

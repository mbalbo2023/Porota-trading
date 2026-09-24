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



def test_git_ref_path_is_not_telegram_token():
    line = (
        "git show origin/feature/history-20260907:"
        "fj_history_backfill_planner_rc6.py > out.py\n"
    )
    assert scan_text("workflow.yml", line) == []


def test_python_prose_with_sensitive_name_is_not_assignment():
    line = 'logger.info("Sin GEMINI_API_KEY: no se puede descubrir el catálogo.")\n'
    assert scan_text("module.py", line) == []


def test_python_sensitive_variable_from_function_is_not_literal_secret():
    line = "github_token = _token_from_file(GITHUB_TOKEN_FILE)\n"
    assert scan_text("gateway.py", line) == []


def test_spanish_documentation_placeholder_is_allowed():
    line = 'export GEMINI_API_KEY="la-clave-que-generaste"\n'
    assert scan_text("guide.md", line) == []


def test_real_telegram_token_shape_is_detected():
    token = "123456789:" + ("A" * 35)
    findings = scan_text("fixture.txt", token + "\n")
    assert findings == [{
        "path": "fixture.txt",
        "line": 1,
        "kind": "TELEGRAM_BOT_TOKEN",
    }]


def test_literal_sensitive_assignment_is_still_rejected():
    findings = scan_text("settings.py", 'PPI_API_SECRET="actual-secret-value"\n')
    assert findings == [{
        "path": "settings.py",
        "line": 1,
        "kind": "SENSITIVE_ASSIGNMENT",
        "variable": "PPI_API_SECRET",
    }]

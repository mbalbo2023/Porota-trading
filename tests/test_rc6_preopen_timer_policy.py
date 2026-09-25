from pathlib import Path
import ast


def _tuple_strings(source: str, name: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            return tuple(value)
    raise AssertionError(f"{name} not found")


def test_preopen_timer_policy_is_noncontradictory():
    source = Path("rc6_preopen.py").read_text(encoding="utf-8")
    required = set(_tuple_strings(source, "REQUIRED_TIMERS"))
    blocked = set(_tuple_strings(source, "BLOCKED_HISTORY_TIMERS"))

    assert required == {
        "porota-fast-functional-health-rc6.timer",
        "porota-full-db-integrity-rc6.timer",
        "porota-host-general-backup-rc6.timer",
        "porota-preopen-rc6.timer",
        "porota-candle-integrity-rc6.timer",
    }
    assert blocked == {"porota-history-postclose-rc6.timer"}
    assert required.isdisjoint(blocked)
    assert "porota-functional-health-rc6.timer" not in required


def test_host_wrapper_does_not_override_timer_policy():
    source = Path("rc6_preopen_host_hardened.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "base"
                    and target.attr in {"REQUIRED_TIMERS", "BLOCKED_HISTORY_TIMERS"}
                ):
                    raise AssertionError(f"wrapper overrides base.{target.attr}")


def test_restart_count_is_informational(monkeypatch):
    import rc6_preopen as preopen

    monkeypatch.setattr(
        preopen,
        "cmd",
        lambda args, timeout=20: (
            0,
            "true|porota-trading-bot:17.0.0-rc6|3|true",
            "",
        ),
    )
    result = preopen.container("porota_production_observer", require_readonly=True)
    assert result["state"] == "GREEN"
    assert result["restart_count"] == 3
    assert result["restart_count_policy"] == "INFORMATIONAL"


def test_preopen_has_no_inline_full_db_scan():
    source = Path("rc6_preopen.py").read_text(encoding="utf-8")
    assert "PRAGMA quick_check" not in source
    assert "FULL_SCAN_POSTCLOSE_ONLY" in source


def test_full_db_integrity_evidence_green_after_success(monkeypatch):
    import rc6_preopen as preopen

    answers = iter([
        (0, "success", ""),
        (0, "0", ""),
        (0, "Fri 2026-09-25 12:30:00 UTC", ""),
    ])
    monkeypatch.setattr(preopen, "cmd", lambda args, timeout=20: next(answers))
    result = preopen.full_db_integrity_evidence()
    assert result["state"] == "GREEN"
    assert result["policy"] == "FULL_SCAN_POSTCLOSE_ONLY"


def test_full_db_integrity_evidence_red_without_completed_run(monkeypatch):
    import rc6_preopen as preopen

    answers = iter([
        (0, "success", ""),
        (0, "0", ""),
        (0, "", ""),
    ])
    monkeypatch.setattr(preopen, "cmd", lambda args, timeout=20: next(answers))
    result = preopen.full_db_integrity_evidence()
    assert result["state"] == "RED"

from pathlib import Path


def test_paper_state_inventory_is_read_only_and_fail_closed():
    text = Path("scripts/porota_rc5_paper_state_inventory.py").read_text(encoding="utf-8")
    assert "mode=ro" in text
    assert "PRAGMA quick_check" in text
    assert "REAL_ORDERS_SENT_NONZERO" in text
    forbidden = ("DELETE FROM", "DROP TABLE", "UPDATE ", "INSERT INTO", "VACUUM", "ALTER TABLE")
    assert not any(token in text for token in forbidden)


def test_sunday_readiness_never_mutates_runtime():
    text = Path("scripts/porota_rc5_sunday_readiness.sh").read_text(encoding="utf-8")
    for forbidden in (
        "docker restart", "docker rm", "docker system prune", "systemctl restart",
        "systemctl stop", "systemctl start", "rm -rf", "chmod ", "chown ",
    ):
        assert forbidden not in text
    assert "SUNDAY_READINESS=GREEN" in text
    assert "real_orders_sent" in text
    assert "rc5_release_preflight.py" in text


def test_a3_alignment_is_diagnostic_only():
    text = Path("scripts/porota_a3_catalog_alignment_rc5.py").read_text(encoding="utf-8")
    assert "A3CEMPublicReadOnlyClient" in text
    assert "mode=ro" in text
    assert "NO_AUTOMAP" in text
    assert "a3_order_routing" in text
    forbidden = ("send_order", "new_order", "replace_order", "cancel_order", "authenticate()")
    assert not any(token in text for token in forbidden)


def test_auditor_patch_contains_fail_closed_and_timer_fix():
    patch = Path("RC5_WEEKEND_AUDIT_FIXES.patch").read_text(encoding="utf-8")
    assert "ORDER_GATE_MISSING" in patch
    assert "and _is_business_day(now_local.date())" in patch
    assert patch.count("deploy/systemd/porota-scheduler-export-hf6.timer") == 4  # diff/---/+++ plus path occurrence
    assert "-Persistent=true" in patch

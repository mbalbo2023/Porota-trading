from pathlib import Path

ACTIVE_AUDIT_WORKFLOWS = (
    ".github/workflows/rc6-host-reconcile-fix-forward-20260925.yml",
    ".github/workflows/rc6-next-control-plane-readonly.yml",
    ".github/workflows/rc6-observer-restart-forensic-readonly.yml",
    ".github/workflows/rc6-sre-final-classification-readonly.yml",
    ".github/workflows/rc6-sre-control-plane-candidate-readonly.yml",
)


def test_active_audits_never_exec_python_inside_observer():
    for raw in ACTIVE_AUDIT_WORKFLOWS:
        text = Path(raw).read_text(encoding="utf-8")
        assert "docker exec -i porota_production_observer python" not in text, raw
        assert "docker exec porota_production_observer python" not in text, raw


def test_active_audits_never_run_inline_full_db_integrity():
    for raw in ACTIVE_AUDIT_WORKFLOWS:
        text = Path(raw).read_text(encoding="utf-8")
        assert "PRAGMA quick_check" not in text, raw

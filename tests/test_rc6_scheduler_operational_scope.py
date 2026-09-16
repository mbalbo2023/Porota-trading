from pathlib import Path


def test_scheduler_catalog_excludes_disabled_contract_and_caucion_jobs_from_active_rows():
    source = Path("de_scheduler_catalog_hf6.py").read_text(encoding="utf-8")

    assert "DISABLED_SCOPE_JOB_PREFIXES" in source
    assert '"CAUCION_",
    "CONTRACT_EVIDENCE_",
    "SCALP",' in source
    assert "for job in active_internal_jobs():" in source
    assert "if key in known or not _is_active_scope_job(key):" in source

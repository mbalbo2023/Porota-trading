"""Contrato fail-closed para impedir escrituras históricas desde el scheduler interno."""

from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "az_maintenance_scheduler.py"


def test_internal_scheduler_blocks_historical_writes_before_subprocess_launch():
    source = SOURCE.read_text(encoding="utf-8")

    guard = "if job_name in INTERNAL_HISTORICAL_WRITE_JOBS:"
    launch = "result = subprocess.run("
    assert guard in source
    assert source.index(guard) < source.index(launch)
    assert '"historical_refresh"' in source
    assert '"historical_refresh_if_needed"' in source


def test_internal_scheduler_does_not_register_historical_write_jobs():
    source = SOURCE.read_text(encoding="utf-8")

    forbidden_ids = {
        "maintenance_historical_catchup",
        "maintenance_historical_refresh",
        "maintenance_historical_startup_catchup",
    }
    for job_id in forbidden_ids:
        assert f'id="{job_id}"' not in source

    for required_id in (
        "maintenance_news_scan",
        "maintenance_gdelt_shadow",
        "maintenance_action4_audit",
        "maintenance_validation_projection",
    ):
        assert f'id="{required_id}"' in source

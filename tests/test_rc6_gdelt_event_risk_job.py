import inspect
from pathlib import Path
import rc6_gdelt_event_risk_job as m


def test_gdelt_job_is_deprecated_and_inert(tmp_path):
    target = tmp_path / "gdelt.db"
    out = m.run_once(db_path=str(target))
    assert out["state"] == "DEPRECATED_EXCLUDED"
    assert out["authority"] == "NONE"
    assert out["decision_effect"] == "EXCLUDED"
    assert out["real_order_routes"] == "NOT_PRESENT"
    assert out["fetched_events"] == 0
    assert not target.exists()


def test_latest_compatibility_readers_return_only_deprecation_state():
    status = m.latest_status()
    assert status["state"] == "DEPRECATED_EXCLUDED"
    assert status["freshness"] == "NOT_APPLICABLE"
    assert m.latest_events() == []


def test_tombstone_has_no_network_or_sqlite_runtime():
    source = inspect.getsource(m).lower()
    assert "requests" not in source
    assert "sqlite3" not in source
    assert "http://" not in source
    assert "https://" not in source

from pathlib import Path
import json
import sqlite3

import rc6_gdelt_event_risk_job as gdelt

ROOT=Path(__file__).resolve().parents[1]


def _legacy_store(path):
    c=sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE gdelt_event_risk_runs(
      run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
      state TEXT NOT NULL, requested_event_types INTEGER NOT NULL,
      successful_event_types INTEGER NOT NULL DEFAULT 0,
      fetched_events INTEGER NOT NULL DEFAULT 0, stored_events INTEGER NOT NULL DEFAULT 0,
      error_count INTEGER NOT NULL DEFAULT 0, errors_json TEXT NOT NULL DEFAULT '{}',
      authority TEXT NOT NULL DEFAULT 'SHADOW_ONLY');
    CREATE TABLE gdelt_event_risk_events(
      event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, first_seen_at TEXT NOT NULL,
      published_at TEXT NOT NULL, available_to_engine_at TEXT NOT NULL, source TEXT NOT NULL,
      source_tier TEXT NOT NULL, provenance_url TEXT NOT NULL, payload_hash TEXT NOT NULL,
      region TEXT NOT NULL, confirmed_at TEXT, retracted_at TEXT,
      entities_json TEXT NOT NULL, exposures_json TEXT NOT NULL,
      first_recorded_at TEXT NOT NULL, last_recorded_at TEXT NOT NULL,
      authority TEXT NOT NULL DEFAULT 'SHADOW_ONLY');
    """)
    c.commit(); c.close()


def test_legacy_gdelt_store_is_migrated_before_collection(tmp_path):
    db=tmp_path/"legacy.db"
    _legacy_store(db)
    c=gdelt._connect_store(str(db))
    cols={row[1] for row in c.execute("PRAGMA table_info(gdelt_event_risk_events)")}
    c.close()
    assert {"title","source_domain"} <= cols


def test_gdelt_scheduler_is_bounded_shadow_only_and_non_persistent():
    service=(ROOT/"systemd/porota-gdelt-event-risk-rc6.service").read_text(encoding="utf-8")
    timer=(ROOT/"systemd/porota-gdelt-event-risk-rc6.timer").read_text(encoding="utf-8")
    assert "rc6_gdelt_event_risk_job.py" in service
    assert "POROTA_GDELT_INTER_QUERY_SECONDS=2" in service
    assert "TimeoutStartSec=4min" in service
    assert "Nice=10" in service
    assert "IOSchedulingClass=idle" in service
    assert "OnCalendar=*-*-* *:35:00" in timer
    assert "RandomizedDelaySec=300" in timer
    assert "Persistent=false" in timer
    assert "OnUnitActiveSec=" not in timer


def test_gdelt_control_plane_installs_only_structured_scheduler():
    policy=json.loads((ROOT/"ops/policy/host-control-plane-reconciliation-v2.json").read_text(encoding="utf-8"))
    units=policy["units"]
    for unit in (
        "systemd/porota-gdelt-event-risk-rc6.service",
        "systemd/porota-gdelt-event-risk-rc6.timer",
    ):
        assert units[unit]["install"] is True
        assert units[unit]["remove_on_deploy"] is False
    assert units["systemd/porota-gdelt-event-risk-rc6.timer"]["enabled"] is True


def test_network_pacing_is_configurable_but_zero_by_default_for_tests():
    source=(ROOT/"rc6_gdelt_event_risk_job.py").read_text(encoding="utf-8")
    assert 'POROTA_GDELT_INTER_QUERY_SECONDS' in source
    assert "time.sleep(INTER_QUERY_SECONDS)" in source
    assert gdelt.INTER_QUERY_SECONDS == 0.0

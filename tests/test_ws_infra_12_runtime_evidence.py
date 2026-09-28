from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

import ops_runtime_evidence_rc6 as evidence
from scripts.porota_consume_runtime_evidence_rc6 import ConsumerError, consume


NOW = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)


class SystemctlResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def ppi_watch_contract(*expected):
    contract = copy.deepcopy(evidence._ppi_watch_contract())
    contract["expected_unit_names"] = list(expected)
    return contract


def install_systemctl_fixture(monkeypatch, tmp_path, listing, *, enabled="enabled",
                              active="active", enumeration_returncode=0,
                              fragment_exists=True):
    unit = tmp_path / "porota-ppi-watch.service"
    if fragment_exists:
        unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(tuple(command))
        verb = command[1]
        if verb == "list-unit-files":
            return SystemctlResult(listing, enumeration_returncode)
        if verb == "is-enabled":
            return SystemctlResult(enabled, 0 if enabled == "enabled" else 1)
        if verb == "is-active":
            return SystemctlResult(active, 0 if active == "active" else 3)
        if verb == "show":
            return SystemctlResult(str(unit))
        raise AssertionError(command)

    monkeypatch.setattr(evidence.subprocess, "run", fake_run)
    return unit, calls


def make_db(path: Path, *, last_seen="2026-09-27T16:59:00+00:00",
            mode="PRODUCTION_PAPER", real_orders_sent=0) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE observer_state(
          id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT, session_state TEXT,
          ppi_auth TEXT, heartbeat_at TEXT, last_market_data_at TEXT,
          real_orders_sent INTEGER, http_allowed INTEGER, http_blocked INTEGER);
        CREATE TABLE financial_instrument_catalog(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, settlement_source TEXT, last_seen_at TEXT, run_id TEXT,
          status TEXT, capability TEXT, metadata_json TEXT);
        CREATE TABLE complementary_contract_retry(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, source TEXT, observed_at TEXT, state TEXT, reason TEXT,
          last_attempt_at TEXT, attempts INTEGER);
        CREATE TABLE candidate_universe(
          ticker TEXT, instrument_type TEXT, market TEXT, settlement TEXT,
          can_simulate INTEGER, status TEXT, detail TEXT, last_checked_at TEXT);
        CREATE TABLE source_sync(
          source TEXT, status TEXT, last_attempt_at TEXT, last_success_at TEXT,
          items INTEGER, detail TEXT);
        """
    )
    conn.execute(
        "INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?,?,?)",
        (mode, "WAITING_MARKET", "MARKET_CLOSED", "OK",
         "2026-09-27T16:59:30+00:00", "2026-09-25T19:59:00+00:00",
         real_orders_sent, 10, 0),
    )
    metadata = {
        "_discovery_source": "PPI_PRIMARY",
        "_freshness_by_source": {
            "IOL_COMPLEMENTARY": "2026-09-27T16:58:00+00:00",
            "BYMA_PUBLIC_COMPLEMENTARY": "2026-09-27T16:57:00+00:00",
        },
    }
    conn.execute(
        "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS", "PPI_FIELD",
         last_seen, "run-1", "AVAILABLE", "READY_PAPER_SPOT", json.dumps(metadata)),
    )
    conn.execute(
        "INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
        ("GGAL", "ACCIONES", "BYMA", "A-24HS", 1, "AVAILABLE",
         "READY_PAPER_SPOT", "2026-09-27T16:59:00+00:00"),
    )
    conn.execute(
        "INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
        ("PPI_PRIMARY", "OK", "2026-09-27T16:59:00+00:00",
         "2026-09-27T16:59:00+00:00", 1, "OK"),
    )
    conn.commit()
    conn.close()


def json_file(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def inputs(tmp_path: Path, **db_options):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "observer.db"
    make_db(db, **db_options)
    mode = json_file(tmp_path / "operation_mode.json", {
        "mode": "PRODUCTION_PAPER", "execution": "SIMULATED",
        "apis": {"PPI_ORDERS": "BLOCKED"},
    })
    deploy = json_file(tmp_path / "CURRENT_STATE_V2.json", {
        "candidate_sha": "a" * 40, "deploy_sha": "b" * 40,
        "image_id": "sha256:" + "c" * 64,
        "recorded_at": "2026-09-26T20:00:00+00:00",
        "real_order_routes": "NOT_CALLED",
    })
    frozen = json_file(tmp_path / "frozen.json", {
        "candidate_sha": "a" * 40, "candidate_tree_sha": "d" * 40,
        "image_id": "sha256:" + "c" * 64, "image_tar_sha256": "e" * 64,
    })
    manifest = json_file(tmp_path / "manifest.json", {
        "schema_version": 2, "bundle_sha256": "f" * 64,
    })
    return db, mode, deploy, frozen, manifest


def build(tmp_path: Path, **db_options):
    db, mode, deploy, frozen, manifest = inputs(tmp_path, **db_options)
    return evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )


def test_complete_bundle_has_readonly_proof_and_instrument_contract(tmp_path):
    payload = build(tmp_path)
    evidence.validate_bundle(payload)
    assert payload["status"] == "COMPLETE"
    assert payload["database"]["open_mode"] == "mode=ro"
    assert payload["database"]["query_only"] is True
    assert payload["safety"] == {
        "mode": "PRODUCTION_PAPER",
        "operation_mode": "PRODUCTION_PAPER",
        "execution": "SIMULATED",
        "real_orders_sent": 0,
        "real_order_route_capability": "BLOCKED",
        "real_order_routes": "NOT_CALLED",
        "paper_shadow_only": True,
    }
    row = payload["instruments"][0]
    assert row["identity"]["primary_source"] == "PPI"
    assert row["readiness"]["status"] == "READY_PAPER"
    assert set(row["sources"]) == set(evidence.SOURCES)


def test_unknown_or_stale_freshness_never_produces_ready(tmp_path):
    payload = build(tmp_path, last_seen=None)
    row = payload["instruments"][0]
    assert row["readiness"]["status"] == "NO_READY"
    assert "PPI_FRESHNESS_UNKNOWN" in row["readiness"]["reasons"]


@pytest.mark.parametrize(
    "mode,real_orders,reason",
    [("REAL", 0, "NON_PAPER_MODE"), ("PRODUCTION_PAPER", 1, "REAL_ORDERS_SENT_NONZERO")],
)
def test_non_paper_or_real_orders_blocks_bundle(tmp_path, mode, real_orders, reason):
    db, operation, deploy, frozen, manifest = inputs(
        tmp_path, mode=mode, real_orders_sent=real_orders
    )
    if mode != "PRODUCTION_PAPER":
        json_file(operation, {"mode": mode, "execution": "REAL", "apis": {"PPI_ORDERS": "ENABLED"}})
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=operation, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["status"] == "BLOCKED"
    assert reason in payload["blockers"]


def test_missing_required_table_is_incomplete(tmp_path):
    db, operation, deploy, frozen, manifest = inputs(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE complementary_contract_retry")
    conn.commit()
    conn.close()
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=operation, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["status"] == "INCOMPLETE"
    assert "MISSING_TABLE:complementary_contract_retry" in payload["gaps"]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO observer_state(id,mode) VALUES(2,'X')",
        "UPDATE observer_state SET mode='X' WHERE id=1",
        "DELETE FROM observer_state",
        "CREATE TABLE forbidden(x INTEGER)",
        "VACUUM",
        "ATTACH DATABASE ':memory:' AS other",
    ],
)
def test_query_only_connection_rejects_every_write_class(tmp_path, statement):
    db, *_ = inputs(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    conn = evidence.connect_readonly(db)
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.Error):
        conn.execute(statement)
    conn.close()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_atomic_output_is_confined_to_staging(tmp_path):
    payload = build(tmp_path)
    stage = tmp_path / "stage"
    evidence.atomic_json(stage / "latest.json", stage, payload)
    assert json.loads((stage / "latest.json").read_text())["schema"] == evidence.SCHEMA
    with pytest.raises(evidence.EvidenceError, match="OUTPUT_OUTSIDE_STAGING_ROOT"):
        evidence.atomic_json(tmp_path / "outside.json", stage, payload)


def test_sanitizer_rejects_secret_keys_paths_and_payloads():
    with pytest.raises(evidence.EvidenceError, match="SANITIZER_FORBIDDEN_KEY"):
        evidence.assert_sanitized({"access_token": "redacted"})
    with pytest.raises(evidence.EvidenceError, match="SANITIZER_FORBIDDEN_VALUE"):
        evidence.assert_sanitized({"detail": "/opt/private/secret.json"})


def test_ppi_watch_probe_uses_read_only_systemd_verbs(monkeypatch, tmp_path):
    unit, calls = install_systemctl_fixture(
        monkeypatch, tmp_path, "porota-ppi-watch.service enabled\n"
    )
    contract = ppi_watch_contract("porota-ppi-watch.service")
    result = evidence.ppi_watch_status(NOW, contract=contract)
    assert result["state"] == "VERIFIED_READ_ONLY"
    assert result["reason"] == "EXACT_EXPECTED_UNIT_VERIFIED"
    assert result["enumeration_succeeded"] is True
    assert result["mutation_attempted"] is False
    assert result["units"][0]["unit_sha256"] == hashlib.sha256(unit.read_bytes()).hexdigest()
    assert {call[1] for call in calls} == {"list-unit-files", "is-enabled", "is-active", "show"}


def test_ppi_watch_zero_units_is_not_present_and_never_green(monkeypatch, tmp_path):
    _unit, calls = install_systemctl_fixture(monkeypatch, tmp_path, "")
    result = evidence.ppi_watch_status(NOW, contract=ppi_watch_contract())
    assert result["state"] == "NOT_PRESENT"
    assert result["reason"] == "NO_MATCHING_UNIT"
    assert result["units"] == []
    assert result["mutation_attempted"] is False
    assert {call[1] for call in calls} == {"list-unit-files"}


def test_ppi_watch_absence_is_green_only_when_versioned_contract_requires_it(monkeypatch, tmp_path):
    install_systemctl_fixture(monkeypatch, tmp_path, "")
    contract = ppi_watch_contract()
    contract["systemd_presence_expectation"] = "ABSENT"
    result = evidence.ppi_watch_status(NOW, contract=contract)
    assert result["state"] == "VERIFIED_ABSENT"
    assert result["reason"] == "ABSENCE_REQUIRED_BY_CONTRACT"


def test_ppi_watch_multiple_candidates_are_ambiguous_without_metadata_queries(monkeypatch, tmp_path):
    _unit, calls = install_systemctl_fixture(
        monkeypatch, tmp_path,
        "alpha-ppi-watch.service enabled\nbeta-ppi-watch.timer enabled\n",
    )
    result = evidence.ppi_watch_status(
        NOW, contract=ppi_watch_contract("alpha-ppi-watch.service")
    )
    assert result["state"] == "AMBIGUOUS"
    assert result["reason"] == "MULTIPLE_MATCHING_UNITS"
    assert len(result["units"]) == 2
    assert {call[1] for call in calls} == {"list-unit-files"}


def test_ppi_watch_enumeration_failure_is_no_verificado(monkeypatch, tmp_path):
    install_systemctl_fixture(
        monkeypatch, tmp_path, "permission denied", enumeration_returncode=1
    )
    result = evidence.ppi_watch_status(NOW, contract=ppi_watch_contract())
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "ENUMERATION_FAILED"
    assert result["enumeration_succeeded"] is False
    assert result["mutation_attempted"] is False


def test_ppi_watch_unregistered_single_candidate_is_no_verificado(monkeypatch, tmp_path):
    _unit, calls = install_systemctl_fixture(
        monkeypatch, tmp_path, "porota-ppi-watch.service enabled\n"
    )
    result = evidence.ppi_watch_status(NOW, contract=ppi_watch_contract())
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "UNIT_NOT_AUTHORIZED_BY_CONTRACT"
    assert result["units"] == [{"unit": "porota-ppi-watch.service"}]
    assert {call[1] for call in calls} == {"list-unit-files"}


@pytest.mark.parametrize(
    "enabled,active,fragment_exists",
    [("disabled", "active", True), ("enabled", "inactive", True),
     ("enabled", "active", False)],
)
def test_ppi_watch_expected_unit_requires_enabled_active_and_hash(
        monkeypatch, tmp_path, enabled, active, fragment_exists):
    install_systemctl_fixture(
        monkeypatch, tmp_path, "porota-ppi-watch.service enabled\n",
        enabled=enabled, active=active, fragment_exists=fragment_exists,
    )
    result = evidence.ppi_watch_status(
        NOW, contract=ppi_watch_contract("porota-ppi-watch.service")
    )
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "EXPECTED_UNIT_NOT_VERIFIED"
    assert result["mutation_attempted"] is False


def test_ppi_watch_bundle_validator_rejects_false_green(tmp_path):
    payload = build(tmp_path)
    payload["ppi_watch"] = {
        "state": "VERIFIED_READ_ONLY", "reason": "forged",
        "checked_at": NOW.isoformat(), "contract_schema_version": 1,
        "owner": "EXTERNAL_SEPARATE_OWNER",
        "systemd_presence_expectation": "UNKNOWN",
        "enumeration_succeeded": True, "units": [], "mutation_attempted": False,
    }
    with pytest.raises(evidence.EvidenceError, match="PPI_WATCH_FALSE_VERIFIED_UNIT"):
        evidence.validate_bundle(payload)


def test_ppi_watch_bundle_validator_accepts_all_fail_closed_states(tmp_path):
    for state in ("NOT_PRESENT", "AMBIGUOUS", "NO_VERIFICADO"):
        payload = build(tmp_path / state)
        payload["ppi_watch"]["state"] = state
        evidence.validate_bundle(payload)


def test_delta_reports_promotion_from_fixed_previous_bundle(tmp_path):
    payload = build(tmp_path)
    old = json.loads(json.dumps(payload["instruments"][0]))
    old["readiness"]["status"] = "NO_READY"
    previous = {"schema": evidence.SCHEMA, "instruments": [old]}
    delta = evidence._delta(previous, payload["instruments"])
    assert delta["state"] == "VERIFIED"
    assert delta["promotions"][0]["before"] == "NO_READY"


def test_collector_and_publisher_have_no_forbidden_dependencies_or_mutations():
    root = Path(__file__).parents[1]
    collector = (root / "ops_runtime_evidence_rc6.py").read_text(encoding="utf-8")
    publisher = (root / "scripts/porota_publish_runtime_evidence_github_rc6.sh").read_text(encoding="utf-8")
    assert collector.count("sqlite3.connect(") == 1
    assert "?mode=ro" in collector
    assert "PRAGMA query_only=ON" in collector
    for forbidden in ("order_router", "broker_client", "enqueue_critical", "paper_notification_outbox"):
        assert forbidden not in collector.lower()
    for forbidden in ("docker ", "systemctl start", "systemctl stop", "systemctl restart",
                      "systemctl enable", "systemctl disable", "git checkout"):
        assert forbidden not in publisher.lower()
    assert "runtime/evidence/latest.json" in publisher
    assert "runtime-observability" in publisher
    legacy_publisher = (root / "scripts/porota_publish_introspection_github_rc6.sh").read_text(encoding="utf-8")
    assert "/run/porota-observability-publish/lock" in publisher
    assert "/run/porota-observability-publish/lock" in legacy_publisher


def test_consumer_requires_exact_commit_and_blob(tmp_path):
    payload = build(tmp_path / "data")
    repo = tmp_path / "sink"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "runtime-observability"], cwd=repo, check=True)
    target = repo / "runtime/evidence/latest.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-qm", "fixture"], cwd=repo, check=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    blob = subprocess.check_output(
        ["git", "rev-parse", f"{commit}:runtime/evidence/latest.json"], cwd=repo, text=True
    ).strip()
    result = consume(repo, commit, blob)
    assert result["commit_sha"] == commit
    assert result["blob_sha"] == blob
    with pytest.raises(ConsumerError, match="BLOB_MISMATCH"):
        consume(repo, commit, "0" * 40)


def test_systemd_unit_is_hardened_and_has_no_docker_group():
    root = Path(__file__).parents[1]
    unit = (root / "systemd/porota-runtime-evidence-rc6.service").read_text(encoding="utf-8")
    for required in (
        "User=porotaadmin", "NoNewPrivileges=true", "ProtectSystem=strict",
        "ProtectHome=read-only", "ReadWritePaths=/var/lib/porota-runtime-evidence",
        "StateDirectory=porota-runtime-evidence", "RuntimeDirectoryPreserve=yes",
    ):
        assert required in unit
    assert "ProtectHome=false" not in unit
    assert "SupplementaryGroups=docker" not in unit


def test_systemd_home_is_read_only_and_git_credential_fixture_remains_readable(tmp_path):
    root = Path(__file__).parents[1]
    unit = (root / "systemd/porota-runtime-evidence-rc6.service").read_text(encoding="utf-8")
    fixture_home = tmp_path / "home" / "porotaadmin"
    fixture_home.mkdir(parents=True)
    credential_config = fixture_home / ".gitconfig"
    credential_config.write_text(
        "[credential]\n\thelper = fixture-readonly-helper\n", encoding="utf-8"
    )
    assert "ProtectHome=read-only" in unit
    assert credential_config.read_text(encoding="utf-8").endswith(
        "helper = fixture-readonly-helper\n"
    )
    write_paths = {
        line.split("=", 1)[1] for line in unit.splitlines()
        if line.startswith("ReadWritePaths=")
    }
    assert write_paths == {
        "/var/lib/porota-runtime-evidence",
        "/var/lib/porota-observability/repo",
        "/run/porota-observability-publish",
    }
    assert not any(path.startswith("/home") for path in write_paths)


def test_host_policy_and_manifest_remain_green_with_ppi_watch_external_contract():
    from scripts.porota_host_manifest_v2 import build_manifest, tracked_modes
    from scripts.porota_host_policy_v2 import validate_policy

    root = Path(__file__).parents[1]
    policy = json.loads(
        (root / "ops/policy/host-control-plane-reconciliation-v2.json").read_text(
            encoding="utf-8"
        )
    )
    contract = policy["ppi_watch_evidence_contract"]
    assert contract["managed_by_porota_host_control_plane"] is False
    assert contract["mutation_allowed"] is False
    assert contract["expected_unit_names"] == []
    assert contract["systemd_presence_expectation"] == "UNKNOWN"
    manifest = build_manifest(root, policy, "a" * 40, modes=tracked_modes(root))
    assert manifest["status"] == "GREEN"
    assert validate_policy(manifest, policy)["status"] == "GREEN"

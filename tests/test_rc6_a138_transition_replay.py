"""A138 #40592426296/attempt 2: bounded, OFFHOST failed-transition replay.

The historical Git objects, rather than a newly copied 111-kB implementation,
are the source of the original systemd and COLD2 logic.  The fake Docker,
systemctl, private receipts and SQLite database below are **synthetic** and
cannot attest the physical host's three CID births, DB/WAL or PAPER ledger.
The distinct historic stdin-import regression lives in R223's dedicated test.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from scripts import porota_apply_host_control_plane_v2 as systemd
from scripts import rc6_cold2_apply_guard as current_cold2
from scripts import rc6_deploy_quiescence as quiescence
from scripts import rc6_runtime_oci_custody as custody


REPO = Path(__file__).resolve().parents[1]
A138 = "dd22a1b0761cc4f231813edb6b502d1dd21bb8de"
HISTORICAL_POLICY_SHA256 = "dbfbe7d7036075bef0cc39eb546093ce1a879b454994c87543726835cf80066e"
HISTORICAL_COLD2_SHA256 = "6939ae0413f6de7c91c9a4470e14cd1de8d584714f2cd73c5e18ca9c410eee39"
OWNED = quiescence.OWNED


def _historic(path: str, digest: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{A138}:{path}"], cwd=REPO, capture_output=True,
        check=True, timeout=20,
    )
    assert hashlib.sha256(result.stdout).hexdigest() == digest
    return result.stdout


def _historic_cold2_apply():
    """Load the exact old COLD2 gate without executing its CLI/host actions."""
    code = _historic("scripts/rc6_cold2_apply_guard.py", HISTORICAL_COLD2_SHA256)
    scope = {
        "__name__": "rc6_a138_historic_cold2_readonly",
        "__file__": str(REPO / "scripts/rc6_cold2_apply_guard.py"),
    }
    exec(compile(code, f"git:{A138}:rc6_cold2_apply_guard.py", "exec"), scope)
    return scope["apply"]


def test_a138_exact_44_policy_units_two_retire_are_not_44_installs(tmp_path, monkeypatch):
    """Authentic Actions log: 44 managed units, two additional legacy retirees."""
    old = json.loads(_historic(
        "ops/policy/host-control-plane-reconciliation-v2.json",
        HISTORICAL_POLICY_SHA256,
    ))
    current = json.loads((REPO / "ops/policy/host-control-plane-reconciliation-v2.json")
                         .read_text(encoding="utf-8"))
    old_plan, now_plan = systemd.build_plan(old), systemd.build_plan(current)
    old_retire, now_retire = (systemd.build_legacy_retire_plan(p)
                              for p in (old, current))
    assert len(old_plan) == len(now_plan) == 44
    assert len(old_retire) == len(now_retire) == 2
    assert {row["unit_name"] for row in old_plan} == {
        row["unit_name"] for row in now_plan}
    assert old_retire == now_retire
    assert sum(row["install"] for row in old_plan) == 35
    assert all("ppi-watch" not in row["unit_name"].lower() for row in old_plan)

    by_name = {row["unit_name"]: row for row in now_plan}
    commands = []

    def fake_systemctl(*args, **_kwargs):
        assert args[0] == "systemctl"  # no system command is ever executed
        commands.append(args[1:])
        mode, name = args[1], args[-1]
        row = by_name.get(name, {})
        if mode == "is-enabled":
            value = "masked" if row.get("role") == "QUARANTINED_TIMER" else "enabled"
        elif mode == "is-active":
            value = "active" if row.get("preserve_during_deploy") else "inactive"
        else:
            value = ""
        return SimpleNamespace(returncode=0, stdout=value + "\n")

    monkeypatch.setattr(systemd, "_run", fake_systemctl)
    result = systemd.apply(REPO, current, tmp_path / "fake_systemd",
                           keep_active_timers_paused=True)
    assert result["count"] == 44 and result["legacy_retired_count"] == 2
    assert {name: sum(item["action"] == name for item in result["changed"])
            for name in ("INSTALL", "RETIRE", "QUARANTINE", "RETIRE_EXACT_LEGACY")} == {
                "INSTALL": 35, "RETIRE": 4, "QUARANTINE": 5,
                "RETIRE_EXACT_LEGACY": 2,
            }
    assert not any("ppi" in " ".join(cmd).lower() and
                   "watch" in " ".join(cmd).lower() for cmd in commands)
    assert not any(cmd[:2] == ("enable", "--now") for cmd in commands)


@contextmanager
def _synthetic_wal_and_receipt(root: Path):
    """Only a local SQLite/WAL fixture and fabricated old two-CID stop receipt."""
    deployment = root / "data/deploy"
    deployment.mkdir(parents=True)
    (deployment / ".deploy-quiescence.lock").touch()
    db_dir = root / "data/paper_v17"
    db_dir.mkdir(parents=True)
    db = db_dir / "observer_v17.db"
    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE observer_state(id, mode, real_orders_sent, "
                       "process_state, session_state, heartbeat_at)")
        writer.execute("INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', "
                       "0, 'STOPPED', 'BOOT_CALENDAR', '2026-10-27T22:14:00+00:00')")
        for table in ("paper_positions", "paper_cauciones", "paper_future_positions"):
            writer.execute(f"CREATE TABLE {table}(status)")
        writer.execute("CREATE TABLE paper_family_lifecycle(family, state)")
        writer.commit()
        receipt = {
            "candidate_sha": A138, "status": "HOST_INSTALLATION_STARTED",
            "installation_started": True,
            "inflight": "ABSENT_OWNED_RUNTIME_STOPPED",
            "real_orders_sent": 0, "ppi_watch_accessed": False,
            "degraded_closed_rescue": True,
            "market_open_suspended": False, "open_paper_carryover": False,
            "paused_postclose": False, "intraday_caucion": False,
            "pending_caucion": False, "spot_carryover": None,
            "caucion_carryover": None, "rescue_pre_state": ["DEGRADED", "BOOT_CALENDAR"],
            "containers": {name: {"id": str(i + 1) * 64,
                                  "image": "sha256:" + "a" * 64}
                           for i, name in enumerate(OWNED)},
            "stop_evidence": {name: {"classification": "CLEAN_EXIT",
                                      "after": {"Status": "exited", "ExitCode": 0}}
                              for name in OWNED},
        }
        (deployment / f"quiescence-{A138}.json").write_text(json.dumps(receipt))
        assert (db_dir / "observer_v17.db-wal").is_file()
        yield writer, receipt
    finally:
        writer.close()


def _offline_cold2(monkeypatch, receipt, writer, *, mutate=None):
    """Reuses real COLD2/admit/receipt_for; never invokes Docker or archive I/O."""
    root = Path(writer.execute("PRAGMA database_list").fetchone()[2]).parents[2]
    monkeypatch.setattr(quiescence, "ROOT", root)
    # These four physical proofs cannot be reconstructed from historic Actions
    # logs; injecting them is an explicit simulation, never runtime GREEN.
    for name in ("degraded_closed_rescue_proof", "exact_a92_marker",
                 "exact_rescue_receipt", "exact_clean_stopped"):
        monkeypatch.setattr(quiescence, name, lambda *_args, **_kwargs: None)

    def fake_inspect(name):
        if name == "porota_critical_approval_rc6":
            return {"State": {"Status": "running", "OOMKilled": False,
                              "Health": {"Status": "healthy"}}}
        row = receipt["containers"][name]
        return {"Id": row["id"], "Image": row["image"],
                "State": {"Status": "exited", "ExitCode": 0}}

    monkeypatch.setattr(quiescence, "inspect", fake_inspect)
    monkeypatch.setattr(current_cold2, "ppi_units", lambda: {})
    monkeypatch.setattr(current_cold2.cold, "envelope",
                        lambda *_args, **_kwargs: {
                            "status": "FAIL_CLOSED", "real_routes": "NOT_CALLED"})
    witnessed = []

    def fake_archive(_root, *, admission, expected_owned_ids):
        assert set(expected_owned_ids) == set(OWNED)
        witnessed.append(admission())
        if mutate:
            if mutate == "real":
                writer.execute("UPDATE observer_state SET real_orders_sent=1")
            elif mutate == "caucion":
                writer.execute("INSERT INTO paper_cauciones VALUES('OPEN')")
            elif mutate == "fci":
                writer.execute("INSERT INTO paper_family_lifecycle "
                               "VALUES('FCI', 'NAV_APPLIED')")
            writer.commit()  # remains visible only through live WAL
        return {"state": "QUARANTINED_FAILED", "old_cut_revoked": True}

    monkeypatch.setattr(current_cold2.cold, "quarantine", fake_archive)
    return root, witnessed


def test_a138_historic_cold2_red_then_current_a139_rescue_green_offhost(
        tmp_path, monkeypatch):
    with _synthetic_wal_and_receipt(tmp_path) as (writer, receipt):
        root, witnessed = _offline_cold2(monkeypatch, receipt, writer)
        # Historic Git source lacks the A139 degraded rescue branch and
        # wrongly imposes generic CLOSED on STOPPED/BOOT_CALENDAR.
        with pytest.raises(ValueError, match="QUIESCENCE_CLOSED_REQUIRED"):
            _historic_cold2_apply()(root, A138)
        assert not witnessed  # old guard never reached archive
        if os.environ.get("RC6_A138_MUTATE_CURRENT_TO_HISTORIC") == "1":
            apply = _historic_cold2_apply()
        else:
            apply = current_cold2.apply
        outcome = apply(root, A138)
        assert outcome["old_cut_revoked"] is True
        assert len(witnessed) == 1 and witnessed[0]["owned_stopped"] is True
        assert witnessed[0]["mode"] == "PRODUCTION_PAPER"
        assert witnessed[0]["real_orders_sent"] == 0


@pytest.mark.parametrize("change,signature", [
    ("real", "QUIESCENCE_PAPER_REQUIRED"),
    ("caucion", "QUIESCENCE_OPEN_POSITION"),
    ("fci", "QUIESCENCE_FUND_NAV_LIABILITY_UNRECONCILED"),
])
def test_a138_cold2_rechecks_uncheckpointed_wal_and_ledgers_after_archive(
        tmp_path, monkeypatch, change, signature):
    with _synthetic_wal_and_receipt(tmp_path) as (writer, receipt):
        root, witnessed = _offline_cold2(
            monkeypatch, receipt, writer,
            mutate=None if os.environ.get("RC6_A138_SKIP_WAL_MUTATION") else change)
        with pytest.raises(ValueError, match=signature):
            current_cold2.apply(root, A138)
        assert len(witnessed) == 1  # initial archive admitted before WAL race


def test_a138_two_old_cids_cannot_masquerade_as_three_sealed_births():
    """An A138 two-CID stop receipt is NOT new three-role OCI custody."""
    # A138 named old observer/dashboard. Its critical approval role existed,
    # but the job log did NOT prove a trusted root-owned critical birth CID.
    legacy = {"schema_version": 1, "candidate_sha": A138,
              "candidate_tree": "b" * 40, "image_id": "sha256:" + "a" * 64,
              "artifact_digest": "sha256:" + "c" * 64,
              "generation": "d" * 32, "run_id": "40592426296",
              "run_attempt": "2", "daemon_id": "synthetic-daemon",
              "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
              "sealed": True, "birth_intent_sha256": "e" * 64,
              "roles": {role: {
                  "cid": str(i + 1) * 64, "image_id": "sha256:" + "a" * 64,
                  "created": "2026-10-27T22:14:00+00:00",
                  "name": custody._birth_name(role, "d" * 32)}
                  for i, role in enumerate(("observer", "dashboard"))}}
    with pytest.raises(custody.CustodyHold, match="THREE_ROLE_SEAL"):
        custody._validate_current(legacy)
    legacy["roles"]["critical"] = dict(legacy["roles"]["observer"])
    legacy["roles"]["critical"]["name"] = custody._birth_name("critical", "d" * 32)
    with pytest.raises(custody.CustodyHold, match="BIRTH_CID"):
        custody._validate_current(legacy)

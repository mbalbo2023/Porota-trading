"""A302 manual Action remains observational and cannot cross an exact gate."""
from __future__ import annotations

import sqlite3
import hashlib
import stat
import sys
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from scripts import rc6_monday_open_readonly as probe


ART = ZoneInfo("America/Argentina/Buenos_Aires")
SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64


def request(phase="SPOT", family="ACCIONES", ticker="GGAL", **changes):
    value = {
        "phase": phase, "trade_day_ar": "2026-11-02",
        "candidate_sha": SHA, "candidate_tree_sha": TREE,
        "image_id": IMAGE, "family": family, "ticker": ticker,
        "currency": "ARS", "settlement": "A-24HS",
        "source_commit": "d" * 40, "source_sha256": "e" * 64,
    }
    if phase in {"T45", "T10", "POSTCLOSE"}:
        value.update(family="NONE", ticker="NONE", currency="NONE", settlement="NONE")
    if phase == "CAUCION":
        value.update(family="CAUCIONES", ticker="PESOS3", settlement="INMEDIATA")
    value.update(changes)
    return value


def at(hour, minute=0):
    return datetime(2026, 11, 2, hour, minute, tzinfo=ART)


@pytest.mark.parametrize("phase,clock", [
    ("T45", (9, 48)), ("T10", (10, 24)), ("SPOT", (10, 40)),
    ("CEDEAR", (11, 40)), ("SCALPING", (12, 15)),
    ("CAUCION", (16, 32)), ("POSTCLOSE", (18, 12)),
])
def test_exact_host_phase_boundaries(phase, clock):
    value = request(phase)
    if phase == "CEDEAR":
        value.update(family="CEDEARS", ticker="AAPL")
    assert probe.validate_request(value, at(*clock)) == at(*clock)
    with pytest.raises(probe.ProbeBlocked, match="PHASE_WINDOW_CLOSED"):
        probe.validate_request(value, at(8))
    with pytest.raises(probe.ProbeBlocked, match="TRADE_DAY_MISMATCH"):
        probe.validate_request(value, datetime(2026, 11, 3, *clock, tzinfo=ART))


@pytest.mark.parametrize("changes,reason", [
    ({"candidate_sha": "f" * 39}, "SHA_INVALID"),
    ({"candidate_tree_sha": "f" * 40 + ";touch /tmp/unsafe"}, "SHA_INVALID"),
    ({"image_id": "sha256:" + "x" * 64}, "IMAGE_ID_INVALID"),
    ({"family": "CEDEARS"}, "EXACT_IDENTITY_INVALID"),
    ({"ticker": "GGAL;echo"}, "EXACT_IDENTITY_INVALID"),
    ({"settlement": "INMEDIATA"}, "SPOT_SETTLEMENT_INVALID"),
    ({"trade_day_ar": "2026-11-03"}, "TRADE_DAY_MISMATCH"),
])
def test_request_identity_and_day_fail_closed(changes, reason):
    with pytest.raises(probe.ProbeBlocked, match=reason):
        probe.validate_request(request(**changes), at(10, 45))


def test_caucion_never_borrows_a_spot_settlement_or_usd():
    with pytest.raises(probe.ProbeBlocked, match="CAUCION_IDENTITY_INVALID"):
        probe.validate_request(request("CAUCION", currency="USD"), at(16, 40))
    with pytest.raises(probe.ProbeBlocked, match="CAUCION_IDENTITY_INVALID"):
        probe.validate_request(request("CAUCION", settlement="A-24HS"), at(16, 40))


def release():
    marker = {
        "candidate_sha": SHA, "image_id": IMAGE,
        "validation_status": "VALIDATED_RUNTIME", "mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0, "real_order_routes": "NOT_CALLED",
        "ppi_watch_untouched": True,
    }
    frozen = {
        "candidate_sha": SHA, "candidate_tree_sha": TREE,
        "real_order_capability_required": "BLOCKED",
        "paper_mode_required": "PRODUCTION_PAPER", "build_once": True,
    }
    container = {
        "running": True, "image": IMAGE, "build_sha": SHA, "tree_sha": TREE,
        "readonly_rootfs": True, "scalping_mode": "ACTIVE_PAPER",
        "caucion_mode": "ACTIVE_PAPER",
    }
    return marker, frozen, container


def test_three_release_proofs_and_negative_drift():
    marker, frozen, observer = release()
    dashboard = dict(observer)
    probe.validate_release(request(), marker, frozen, observer, dashboard)
    for index, key, bad in (
        (0, "candidate_sha", "f" * 40),
        (1, "candidate_tree_sha", "f" * 40),
        (2, "image", "sha256:" + "f" * 64),
        (3, "image", "sha256:" + "f" * 64),
        (0, "real_orders_sent", 1),
        (0, "real_order_routes", "CALLED"),
        (2, "build_sha", "f" * 40),
    ):
        objects = [marker, frozen, observer, dashboard]
        altered = dict(objects[index])
        altered[key] = bad
        objects[index] = altered
        with pytest.raises(probe.ProbeBlocked, match="INSTALLED_RELEASE"):
            probe.validate_release(request(), *objects)


def test_t10_receipt_cannot_be_old_foreign_or_late():
    receipt = {
        "schema": "RC6_PAPER_PREOPEN_GATE_V2", "candidate_sha": SHA,
        "trade_day_ar": "2026-11-02", "status": "GO_PAPER_SCALPING",
        "phase": "T_MINUS_10", "issued_at_ar": at(10, 23).isoformat(),
    }
    assert probe.validate_t10(request("T10"), receipt, at(10, 25)) == "VERIFIED_T10_GO"
    for altered in (
        dict(receipt, candidate_sha="f" * 40),
        dict(receipt, issued_at_ar=at(10, 30).isoformat()),
        dict(receipt, status="NO_GO_PAPER_SCALPING"),
    ):
        assert probe.validate_t10(request("T10"), altered, at(10, 31)) == "HOLD_T10_RECEIPT"
    assert probe.validate_t10(request("T10"), receipt, at(18, 12)) == "HOLD_T10_RECEIPT"


def _db(path):
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER,
        process_state TEXT,session_state TEXT,ppi_auth TEXT,heartbeat_at TEXT);
      CREATE TABLE intraday_scalping_worker_state(id INTEGER PRIMARY KEY,state TEXT,
        heartbeat_at TEXT);
      CREATE TABLE paper_caucion_cash_sweep_state(id INTEGER PRIMARY KEY,state TEXT,
        heartbeat_at TEXT,code TEXT,request_id TEXT,real_orders_sent INTEGER,routes_json TEXT);
      CREATE TABLE paper_supervisor_state(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT);
      CREATE TABLE paper_exit_reader_state(id INTEGER PRIMARY KEY,state TEXT,heartbeat_at TEXT);
      CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,
        currency TEXT,settlement TEXT,status TEXT,capability TEXT,settlement_source TEXT,last_seen_at TEXT,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
      CREATE TABLE candidate_identity_v2(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
        settlement TEXT,status TEXT,can_simulate INTEGER,checked_at TEXT,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
      CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,
        symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,book_at TEXT,
        trade_at TEXT,last_kind TEXT,metadata_source TEXT,opening_block_reason TEXT,
        bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT);
      CREATE INDEX idx_snapshot_identity ON market_snapshots(symbol,asset_class,settlement,currency,market,id);
    """)
    c.execute("INSERT INTO observer_state VALUES(1,?,?,?,?,?,?)",
              ("PRODUCTION_PAPER", 0, "RUNNING", "MARKET_OPEN", "OK", at(10, 44).isoformat()))
    for table in ("intraday_scalping_worker_state", "paper_supervisor_state",
                  "paper_exit_reader_state"):
        c.execute("INSERT INTO " + table + " VALUES(1,?,?)",
                  ("RUNNING", at(10, 44).isoformat()))
    c.execute("INSERT INTO paper_caucion_cash_sweep_state VALUES(1,?,?,?,?,?,?)",
              ("WAITING_WINDOW", at(10, 44).isoformat(), "SWEEP_WINDOW_NOT_OPEN",
               None, 0, "[]"))
    c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?)",
              ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS", "AVAILABLE",
               "READY_PAPER_SPOT", "PPI_FIELD", at(10, 44).isoformat()))
    c.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?)",
              ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS", "AVAILABLE",
               1, at(10, 44).isoformat()))
    c.execute("INSERT INTO market_snapshots VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("PRODUCTION_PAPER", at(10, 44).isoformat(), "GGAL", "ACCIONES",
               "BYMA", "USD", "A-24HS", at(10, 44).isoformat(),
               at(10, 44).isoformat(), "TRADE", "PPI_CATALOG:WRONG_CURRENCY", "",
               "1", "2", "10", "10"))
    c.commit()
    return c


def test_db_exact_identity_does_not_borrow_sibling_book(tmp_path, monkeypatch):
    path = tmp_path / "observer.db"
    with _db(path) as c:
        pass
    monkeypatch.setattr(probe, "DB", path)
    observed = probe._db_snapshot(request(), at(10, 45))
    assert observed["catalog"]["capability"] == "READY_PAPER_SPOT"
    assert observed["pit"]["can_simulate"] is True
    assert observed["quote"] == {"state": "NO_EXACT_ROW"}
    assert observed["observer"]["real_orders_sent"] == 0


def test_exact_snapshot_query_plan_is_indexed_or_blocks(tmp_path, monkeypatch):
    path = tmp_path / "observer.db"
    c = _db(path)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    probe._require_snapshot_index(c, identity)
    plan = " ".join(row[3] for row in c.execute(
        "EXPLAIN QUERY PLAN " + probe.SNAPSHOT_SQL, identity))
    assert "idx_snapshot_identity" in plan
    c.execute("DROP INDEX idx_snapshot_identity")
    c.commit()
    with pytest.raises(probe.ProbeBlocked, match="SNAPSHOT_EXACT_INDEX_MISSING"):
        probe._require_snapshot_index(c, identity)
    c.close()


def test_db_nonzero_route_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "observer.db"
    with _db(path) as c:
        c.execute("UPDATE paper_caucion_cash_sweep_state SET routes_json='[1]'")
    monkeypatch.setattr(probe, "DB", path)
    with pytest.raises(probe.ProbeBlocked, match="CAUCION_REAL_ORDER_ROUTE_RED"):
        probe._db_snapshot(request(), at(10, 45))


def test_integrated_receipt_uses_marker_frozen_container_t10_and_exact_db(
        tmp_path, monkeypatch):
    path = tmp_path / "observer.db"
    _db(path).close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    marker, frozen, container = release()
    operator = tmp_path / "operator"
    operator.mkdir()
    monkeypatch.setattr(probe, "DB", path)
    monkeypatch.setattr(probe, "OPERATOR", operator)
    expected_paths = {
        "CURRENT_STATE_V2.json": marker, "porota-frozen-candidate.json": frozen,
        "paper-preopen-v2.json": {
            "schema": "RC6_PAPER_PREOPEN_GATE_V2", "candidate_sha": SHA,
            "trade_day_ar": "2026-11-02", "status": "GO_PAPER_SCALPING",
            "phase": "T_MINUS_10", "issued_at_ar": at(10, 23).isoformat()}}
    monkeypatch.setattr(probe, "_read_json", lambda path, **_kw: expected_paths[path.name])
    monkeypatch.setattr(probe, "_container", lambda _name: dict(container))
    monkeypatch.setattr(probe, "_installed_source", lambda: "FROZEN_BUNDLE_HELPERS_VERIFIED")
    monkeypatch.setattr(probe, "_timer", lambda _name: {
        "enabled": True, "active": True, "last_trigger": at(10, 20).isoformat(),
        "service_result": "success"})
    monkeypatch.setattr(probe, "_source_status", lambda: {"session_date": "2026-11-02"})
    real_lstat = probe.os.lstat
    monkeypatch.setattr(probe.os, "lstat", lambda p: (
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        if p == operator else real_lstat(p)))
    monkeypatch.setitem(sys.modules, "scripts.rc6_operator_paper_rearm",
                        SimpleNamespace(evaluate_rearm=lambda **_kw: (
                            True, "OPERATOR_REARM_AUTHORIZED", {"candidate_sha": SHA})))
    result = probe.probe(request(), at(10, 45))
    assert result["status"] == "OBSERVED_HOLD"
    assert result["entry_authority"] is False and result["real_orders_sent"] == 0
    assert result["evidence"]["t10_receipt"] == "VERIFIED_T10_GO"
    assert result["evidence"]["pit"]["can_simulate"] is True
    assert result["evidence"]["quote"] == {"state": "NO_EXACT_ROW"}
    assert "EXACT_PPI_TRADE_BOOK_UNVERIFIED" in result["missing_observations"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_observational_checklist_has_no_trade_authority():
    evidence = {
        "operator_intent": "ACTIVE_VERIFIED", "t10_receipt": "VERIFIED_T10_GO",
        "timers": {"porota-iol-shadow-collector-rc6": {
            "enabled": True, "active": True, "last_trigger": at(10, 44).isoformat()}},
        "observer": {"heartbeat_age_seconds": 1, "process": "RUNNING",
                     "session": "MARKET_OPEN", "ppi_auth": "OK"},
        "workers": {"supervisor": {"state": "RUNNING", "heartbeat_age_seconds": 1},
                    "exit_reader": {"state": "READY", "heartbeat_age_seconds": 1}},
        "source_cycle": {"session_date": "2026-11-02"},
        "catalog": {"status": "AVAILABLE", "capability": "READY_PAPER_SPOT",
                    "settlement_source": "PPI_FIELD"},
        "pit": {"status": "AVAILABLE", "can_simulate": True},
        "quote": {"source": "PRODUCTION_PAPER", "ppi_catalog_provenance": True,
                  "last_kind": "TRADE", "book_shape_valid": True,
                  "opening_blocked": False, "observed_age_seconds": 1,
                  "book_age_seconds": 1, "trade_age_seconds": 1},
    }
    assert probe.review_evidence(request(), evidence) == []
    evidence["quote"]["book_age_seconds"] = 121
    assert probe.review_evidence(request(), evidence) == [
        "EXACT_PPI_TRADE_BOOK_UNVERIFIED"]
    evidence["quote"]["book_age_seconds"] = 1
    evidence["t10_receipt"] = "HOLD_T10_RECEIPT"
    assert probe.review_evidence(request(), evidence) == [
        "T10_EXACT_RECEIPT_MISSING"]


def test_droplet_pin_uses_two_independent_metadata_keys():
    class Response:
        status = 200
        def __init__(self, value):
            self.value = value
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, _size):
            return self.value.encode()
    class Opener:
        def __init__(self, droplet, region):
            self.values = [droplet, region]
            self.urls = []
        def open(self, request, timeout):
            self.urls.append(request.full_url)
            assert timeout == 3 and request.get_method() == "GET"
            return Response(self.values[len(self.urls)-1])
    ok = Opener(probe.DROPLET_ID, probe.DROPLET_REGION)
    assert probe.verify_droplet_identity(ok)["region"] == "nyc1"
    assert ok.urls == [probe.METADATA + "id", probe.METADATA + "region"]
    with pytest.raises(probe.ProbeBlocked, match="DROPLET_IDENTITY_MISMATCH"):
        probe.verify_droplet_identity(Opener("111111111", probe.DROPLET_REGION))


def test_host_helpers_must_match_installed_frozen_bundle_before_import(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    sources = {
        "scripts/rc6_operator_paper_rearm.py": b"read only\n",
        "am_us_equity_calendar_rc6.py": b"calendar\n",
    }
    for relative, source in sources.items():
        (tmp_path / relative).write_bytes(source)
    monkeypatch.setattr(probe, "PRODUCT", tmp_path)
    manifest = {
        "schema_version": 1, "status": "GREEN", "file_count": 2,
        "bundle_sha256": "f" * 64,
        "files": [{"path": relative, "sha256": hashlib.sha256(source).hexdigest(),
                   "bytes": len(source)} for relative, source in sources.items()],
    }
    monkeypatch.setattr(probe, "_read_json", lambda _path, **_kw: manifest)
    monkeypatch.setattr(probe, "_command", lambda *_args, **_kw: pytest.fail(
        "host git checkout is not Deploy V2 installed source authority"))
    assert probe._installed_source() == "FROZEN_BUNDLE_HELPERS_VERIFIED"
    (tmp_path / "am_us_equity_calendar_rc6.py").write_bytes(b"altered!\n")
    with pytest.raises(probe.ProbeBlocked, match="HOST_HELPER_DRIFT"):
        probe._installed_source()
    (tmp_path / "am_us_equity_calendar_rc6.py").write_bytes(sources[
        "am_us_equity_calendar_rc6.py"])
    manifest["files"] = manifest["files"][:1]
    manifest["file_count"] = 1
    with pytest.raises(probe.ProbeBlocked, match="FROZEN_HELPER_MANIFEST_ENTRY_INVALID"):
        probe._installed_source()
    manifest["file_count"] = 2
    with pytest.raises(probe.ProbeBlocked, match="FROZEN_BUNDLE_MANIFEST_INVALID"):
        probe._installed_source()


def test_frozen_bundle_manifest_requires_root_owned_regular_bounded_file(tmp_path, monkeypatch):
    import json
    manifest = tmp_path / "porota-deploy-bundle-v2-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}))
    original_fstat = probe.os.fstat
    def pretend_root(fd):
        value = original_fstat(fd)
        return SimpleNamespace(st_uid=0, st_mode=value.st_mode,
                               st_nlink=value.st_nlink, st_size=value.st_size)
    monkeypatch.setattr(probe.os, "fstat", pretend_root)
    assert probe._read_json(manifest, root_owned=True)["schema_version"] == 1
    manifest.chmod(0o666)
    with pytest.raises(probe.ProbeBlocked, match="RECEIPT_FILE_UNTRUSTED"):
        probe._read_json(manifest, root_owned=True)


def test_workflow_manual_only_fixed_host_and_no_market_or_write_route():
    root = Path(__file__).resolve().parents[1]
    source = (root / ".github/workflows/rc6-monday-open-readonly.yml").read_text()
    parsed = yaml.load(source, Loader=yaml.BaseLoader)
    assert set(parsed["on"]) == {"workflow_dispatch", "pull_request"}
    assert parsed["on"]["pull_request"]["paths"] == [
        ".github/workflows/rc6-monday-open-readonly.yml",
        "scripts/rc6_monday_open_readonly.py",
        "tests/test_rc6_a302_monday_open_readonly.py"]
    assert parsed["jobs"]["offline-contract"]["if"] == "github.event_name == 'pull_request'"
    assert "DO_HOST" not in str(parsed["jobs"]["offline-contract"])
    assert parsed["permissions"] == {"contents": "read"}
    assert "github.ref == 'refs/heads/main'" in source
    assert "python3 -B - --verify-droplet" in source
    assert probe.DROPLET_ID == "594077619" and probe.DROPLET_REGION == "nyc1"
    assert "sudo -n env PYTHONDONTWRITEBYTECODE=1" in source
    assert "rc6-a205-paper-book-ro-v1-" not in source
    assert "docker exec" not in source
    assert "systemctl start" not in source
    assert "PPI_PRODUCTION_SECRET" not in source
    script = (root / "scripts/rc6_monday_open_readonly.py").read_text()
    assert "PRAGMA query_only=ON" in script
    assert "?mode=ro" in script
    assert "entry_authority\": False" in script
    assert '["docker", "inspect"' in script
    assert '["docker", "exec"' not in script

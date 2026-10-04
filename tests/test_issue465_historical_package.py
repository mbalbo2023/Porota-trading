"""Synthetic-only adversarial tests; never the inaccessible 68/151 master."""
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_audit_evidence import EvidenceError, Limits, export_package, verify_package
from rc6_audit_evidence.package import Budget, canonical, readonly_snapshot

ROOT = Path(__file__).resolve().parents[1]
SEED = b"synthetic-test-seed-for-nonproduction-only-465"
SESSIONS = []
_date = date(2026, 9, 7)
while len(SESSIONS) < 20:
    if _date.weekday() < 5:
        SESSIONS.append(_date.isoformat())
    _date += timedelta(days=1)


def source(tmp_path, *, include_second_currency=True, duplicate_ids=False):
    path = tmp_path / "synthetic-source.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
            INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
            CREATE TABLE paper_positions(
              paper_id TEXT PRIMARY KEY,strategy_version TEXT,symbol TEXT,asset_class TEXT,currency TEXT,
              market TEXT,settlement TEXT,status TEXT,quantity TEXT,entry_cost TEXT,exit_cost TEXT,
              gross_pnl TEXT,net_pnl TEXT,opened_at TEXT,closed_at TEXT,close_reason TEXT,features_json TEXT,
              account_number TEXT,api_token TEXT,chat_id TEXT);
            CREATE TABLE paper_fills(id INTEGER,paper_id TEXT,side TEXT,filled_at TEXT,
              quantity TEXT,price TEXT,costs TEXT,slippage TEXT);
        """)
        features = {"account_number": "PRIVATE-ACCOUNT-NEVER-EXPORT", "api_token": "PRIVATE-TOKEN-NEVER-EXPORT",
                    "chat_id": "PRIVATE-CHAT-NEVER-EXPORT", "performance_lineage": {
                        "strategy_id": "SYNTHETIC_ONLY",
                        "signal_started_at": "2026-09-09T13:59:58Z", "signal_at": "2026-09-09T13:59:59Z",
                        "decision_at": "2026-09-09T14:00:00Z", "intent_at": "2026-09-09T14:00:00Z",
                        "git_sha": "a" * 40, "configuration_fingerprint": "b" * 64}}
        connection.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("RAW-PAPER-POSITION-SECRET", "synthetic-v1", "GGAL", "ACCIONES", "ARS", "BYMA", "A-24HS",
                            "CLOSED", "3", "2", "1", "6", "3", "2026-09-09T14:00:00Z", "2026-09-09T15:00:00Z",
                            "EOD_PAPER", json.dumps(features), "PRIVATE-ACCOUNT-NEVER-EXPORT", "PRIVATE-TOKEN-NEVER-EXPORT", "PRIVATE-CHAT-NEVER-EXPORT"))
        rows = [(1, "RAW-PAPER-POSITION-SECRET", "BUY_SIMULATED", "2026-09-09T14:00:00Z", "3", "100", "2", "0.3"),
                (2, "RAW-PAPER-POSITION-SECRET", "SELL_SIMULATED", "2026-09-09T14:30:00Z", "1", "102", "0.33", "0.2"),
                (3, "RAW-PAPER-POSITION-SECRET", "SELL_SIMULATED", "2026-09-09T15:00:00Z", "2", "102", "0.67", "0.2")]
        if include_second_currency:
            connection.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               ("RAW-USD-SECRET", "synthetic-v1", "GGAL", "ACCIONES", "USD_MEP", "BYMA", "A-24HS", "CLOSED",
                                "1", "0.01", "0.02", "-0.1", "-0.13", "2026-09-09T14:00:00Z", "2026-09-09T15:00:00Z", "STOP_PAPER", "{}", None, None, None))
            rows += [(4, "RAW-USD-SECRET", "BUY_SIMULATED", "2026-09-09T14:00:00Z", "1", "1.1", "0.01", "0"),
                     (5, "RAW-USD-SECRET", "SELL_SIMULATED", "2026-09-09T15:00:00Z", "1", "1", "0.02", "0")]
        if duplicate_ids:
            rows.append(rows[0])
        connection.executemany("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?)", rows)
    return path


def export(path, destination, **options):
    return export_package(path, destination, sessions=options.pop("sessions", SESSIONS), pseudonym_key=SEED, **options)


def records(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def rewrite(package, filename, rows, *, rows_count=None, row_hash=True):
    if row_hash:
        for row in rows:
            row["row_sha256"] = hashlib.sha256(canonical({k: v for k, v in row.items() if k != "row_sha256"}).encode()).hexdigest()
    data = b"".join((canonical(row) + "\n").encode() for row in rows)
    (package / filename).write_bytes(data)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    detail = manifest["files"][filename]
    detail.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    if rows_count is not None:
        detail["rows"] = rows_count
    manifest_path.write_text(canonical(manifest) + "\n")


def test_exact_hand_calculation_partials_currencies_and_no_slippage_double_count(tmp_path):
    path = source(tmp_path)
    result = export(path, tmp_path / "private-package")
    checked = verify_package(tmp_path / "private-package", expected_manifest_sha256=result["manifest_sha256"])
    ars, usd = checked["currencies"]["ARS"], checked["currencies"]["USD_MEP"]
    assert (ars["gross_pnl"], ars["costs"], ars["net_pnl"]) == ("6", "3", "3")
    assert (usd["gross_pnl"], usd["costs"], usd["net_pnl"]) == ("-0.1", "0.03", "-0.13")
    assert checked["counts"] == {"positions": 2, "fills": 5, "closed_positions": 2, "open_survivors": 0}
    assert checked["currency_totals_combined"] is False and checked["gross_costs_equals_net"] is True
    assert next(p for p in checked["positions"] if p["currency"] == "ARS")["partial_exit"] is True
    assert ars["exit_reasons"] == {"EOD_PAPER": 1}
    assert checked["source_authentication"].startswith("EXTERNAL_EVIDENCE_PENDING")


def test_whitelist_does_not_export_ids_secrets_accounts_key_or_source_path(tmp_path):
    path = source(tmp_path)
    export(path, tmp_path / "private-package")
    data = b"".join(f.read_bytes() for f in (tmp_path / "private-package").iterdir())
    for value in (b"RAW-PAPER-POSITION-SECRET", b"RAW-USD-SECRET", b"PRIVATE-ACCOUNT-NEVER-EXPORT", b"PRIVATE-TOKEN-NEVER-EXPORT", b"PRIVATE-CHAT-NEVER-EXPORT", SEED, str(path).encode(), b"features_json", b"account_number", b"api_token", b"chat_id"):
        assert value not in data
    assert stat.S_IMODE((tmp_path / "private-package").stat().st_mode) == 0o700
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in (tmp_path / "private-package").iterdir())


def test_byte_determinism_same_snapshot_key_session_order_and_path_independence(tmp_path):
    path = source(tmp_path)
    export(path, tmp_path / "one")
    export(path, tmp_path / "two", sessions=list(reversed(SESSIONS)))
    clone = tmp_path / "renamed-offline-snapshot.sqlite"
    clone.write_bytes(path.read_bytes())
    export(clone, tmp_path / "three")
    for name in ("positions.jsonl", "fills.jsonl", "manifest.json", "methodology.json"):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes() == (tmp_path / "three" / name).read_bytes()


def test_other_seed_changes_pseudonyms_but_preserves_cashflows(tmp_path):
    path = source(tmp_path)
    export(path, tmp_path / "one")
    export_package(path, tmp_path / "two", sessions=SESSIONS, pseudonym_key=b"different-synthetic-external-seed-00000000000")
    a, b = verify_package(tmp_path / "one"), verify_package(tmp_path / "two")
    assert a["currencies"] == b["currencies"]
    assert a["positions"][0]["position_id"] != b["positions"][0]["position_id"]


def test_actual_sqlite_readonly_query_only_authorizer_and_no_source_change(tmp_path):
    path = source(tmp_path)
    before = path.read_bytes()
    with readonly_snapshot(path, Budget(Limits())) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE paper_positions SET net_pnl='0'")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("ATTACH DATABASE ':memory:' AS scratch")
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 5
    export(path, tmp_path / "package")
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode,orders", [("REAL", 0), ("SIMULATION", 0), ("PRODUCTION_PAPER", 1), ("PRODUCTION_PAPER", "0")])
def test_safety_state_must_be_exact_paper_zero_real_orders(tmp_path, mode, orders):
    path = source(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE observer_state SET mode=?,real_orders_sent=?", (mode, orders))
        if isinstance(orders, str):
            connection.execute("ALTER TABLE observer_state RENAME TO original_state")
            connection.execute("CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent TEXT)")
            connection.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER','0')")
    with pytest.raises(EvidenceError, match="PAPER_SOURCE_SAFETY_REQUIRED"):
        export(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()


@pytest.mark.parametrize("mutation,reason", [
    ("missing_buy", "OPENING_FILL_MISSING"), ("missing_sell", "CLOSING_QUANTITY_MISMATCH"),
    ("over_sell", "OVER_SALE"), ("quantity", "OPENING_QUANTITY_MISMATCH"),
    ("net", "LEDGER_MONEY_RECONCILIATION"), ("cent", "LEDGER_MONEY_RECONCILIATION"),
    ("fee", "LEDGER_COST_RECONCILIATION"), ("negative_cost", "INVALID_DECIMAL"),
    ("clock", "SOURCE_CLOCK_REQUIRED"), ("future_entry", "FILL_CLOCK_ORDER"),
    ("wrong_side", "PAPER_FILL_REQUIRED"), ("missing_currency", "SOURCE_ROW_INCOMPLETE"),
    ("features_secret_blob", "SOURCE_ROW_INCOMPLETE"), ("amount_nan", "INVALID_DECIMAL"),
    ("amount_huge", "INVALID_DECIMAL"), ("unsupported_family", "SPECIALIZED_LEDGER_REQUIRED"),
    ("no_multiplier", "CONTRACT_MULTIPLIER_REQUIRED"), ("closing_time", "POSITION_CLOCK_ORDER"),
])
def test_source_counterexamples_cannot_become_partial_package(tmp_path, mutation, reason):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        sql = {
            "missing_buy": "DELETE FROM paper_fills WHERE id=1",
            "missing_sell": "DELETE FROM paper_fills WHERE id=3",
            "over_sell": "UPDATE paper_fills SET quantity='5' WHERE id=2",
            "quantity": "UPDATE paper_positions SET quantity='4'",
            "net": "UPDATE paper_positions SET net_pnl='4'",
            "cent": "UPDATE paper_positions SET net_pnl='3.01'",
            "fee": "UPDATE paper_positions SET entry_cost='3'",
            "negative_cost": "UPDATE paper_fills SET costs='-1' WHERE id=2",
            "clock": "UPDATE paper_positions SET opened_at='2026-09-09T14:00:00'",
            "future_entry": "UPDATE paper_fills SET filled_at='2026-09-09T13:59:00Z' WHERE id=1",
            "wrong_side": "UPDATE paper_fills SET side='BUY_REAL' WHERE id=1",
            "missing_currency": "UPDATE paper_positions SET currency=NULL",
            "features_secret_blob": "UPDATE paper_positions SET features_json=zeroblob(70000)",
            "amount_nan": "UPDATE paper_fills SET price='NaN' WHERE id=1",
            "amount_huge": "UPDATE paper_fills SET price='1e100000000' WHERE id=1",
            "unsupported_family": "UPDATE paper_positions SET asset_class='FUTUROS'",
            "no_multiplier": "UPDATE paper_positions SET asset_class='BONOS',features_json='{}'",
            "closing_time": "UPDATE paper_positions SET closed_at='2026-09-09T13:00:00Z'",
        }[mutation]
        connection.execute(sql)
    with pytest.raises(EvidenceError, match=reason):
        export(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".package.sanitized-*"))


def test_duplicate_raw_fill_id_rejected_before_export(tmp_path):
    path = source(tmp_path, duplicate_ids=True)
    with pytest.raises(EvidenceError, match="DUPLICATE_FILL"):
        export(path, tmp_path / "package")


@pytest.mark.parametrize("budget,reason", [(Limits(positions=1), "POSITION_ROW_BUDGET"),
                                         (Limits(fills=2), "FILL_ROW_BUDGET"),
                                         (Limits(bytes=1024), "BYTE_BUDGET"),
                                         (Limits(row_bytes=512), "ROW_BYTE_BUDGET"),
                                         (Limits(seconds=1e-9), "TIME_BUDGET")])
def test_limits_fail_closed_without_silent_truncation(tmp_path, budget, reason):
    path = source(tmp_path)
    with pytest.raises(EvidenceError, match=reason):
        export(path, tmp_path / "package", limits=budget)
    assert not (tmp_path / "package").exists()


@pytest.mark.parametrize("kwargs", [{"positions": 0}, {"fills": 100001}, {"bytes": 0}, {"row_bytes": 65537}, {"seconds": 61}, {"seconds": float("nan")}, {"positions": True}])
def test_unbounded_budget_rejected(kwargs):
    with pytest.raises(EvidenceError, match="BOUNDED"):
        Limits(**kwargs)


def test_partial_open_survivor_never_gets_invented_closed_pnl(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET status='OPEN',closed_at=NULL,exit_cost=NULL,gross_pnl=NULL,net_pnl=NULL,close_reason=NULL")
        connection.execute("DELETE FROM paper_fills WHERE id=3")
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    position = checked["positions"][0]
    assert position["remaining_quantity"] == "2" and position["gross_pnl"] is None and position["net_pnl"] is None
    assert checked["currencies"]["ARS"]["closed_positions"] == 0
    assert checked["counts"]["open_survivors"] == 1


def test_future_close_is_open_as_of_cutoff_no_lookahead(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET closed_at='2026-10-05T15:00:00Z'")
        connection.execute("UPDATE paper_fills SET filled_at='2026-10-05T15:00:00Z' WHERE id=3")
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["counts"]["fills"] == 2 and checked["positions"][0]["remaining_quantity"] == "2"
    assert checked["positions"][0]["lifecycle"] == "OPEN_AT_CUTOFF"


def test_opened_before_cohort_includes_original_fill_no_backfill_invention(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET opened_at='2026-09-04T14:00:00Z',features_json='{}'")
        connection.execute("UPDATE paper_fills SET filled_at='2026-09-04T14:00:00Z' WHERE id=1")
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["currencies"]["ARS"]["net_pnl"] == "3"
    assert records(tmp_path / "package" / "positions.jsonl")[0]["entry_session"] == "2026-09-04"


def test_same_timestamp_fills_preserve_source_order_without_raw_ids(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET closed_at=opened_at")
        connection.execute("UPDATE paper_fills SET filled_at='2026-09-09T14:00:00Z'")
    export(path, tmp_path / "package")
    rows = records(tmp_path / "package" / "fills.jsonl")
    assert [r["position_fill_index"] for r in rows] == [1, 2, 3]
    assert verify_package(tmp_path / "package")["currencies"]["ARS"]["net_pnl"] == "3"


def test_full_native_source_missing_fields_stay_unverified(tmp_path):
    path = source(tmp_path)
    export(path, tmp_path / "package")
    p = records(tmp_path / "package" / "positions.jsonl")[0]
    f = records(tmp_path / "package" / "fills.jsonl")[0]
    assert p["lineage"]["status"] == "NO_VERIFICADO" and p["units_per_lot"] is None
    assert f["provider_at"] is None and f["provider_clock_status"] == "NO_VERIFICADO"
    assert f["cost_components"]["commission"] is None and f["cost_components_status"] == "NO_VERIFICADO"


def test_explicit_bond_multiplier_and_currency_stay_exact(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET asset_class='BONOS',features_json=?,gross_pnl='0.06',net_pnl='-2.94'", (json.dumps({"contract_cash_multiplier": "0.01"}),))
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["currencies"]["ARS"]["gross_pnl"] == "0.06"
    assert checked["currencies"]["ARS"]["net_pnl"] == "-2.94"


@pytest.mark.parametrize("sessions", [SESSIONS[:19], SESSIONS + ["2026-10-05"], SESSIONS[:-1] + [SESSIONS[0]], SESSIONS[:-1] + ["2026-13-04"]])
def test_no_inferred_or_missing_twenty_sessions(tmp_path, sessions):
    path = source(tmp_path)
    with pytest.raises(EvidenceError, match="20_SESSIONS"):
        export(path, tmp_path / "package", sessions=sessions)


def test_empty_cohort_pending_not_fabricated_actual_68_151(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM paper_positions")
        connection.execute("DELETE FROM paper_fills")
    with pytest.raises(EvidenceError, match="EXTERNAL_EVIDENCE_PENDING"):
        export(path, tmp_path / "package")


def test_database_lock_short_timeout_does_not_publish(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(EvidenceError, match="SOURCE_READ_FAILED"):
            export(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_source_aliases_cannot_bypass_snapshot_guard(tmp_path, alias):
    path = source(tmp_path)
    target = tmp_path / "aliased.sqlite"
    if alias == "symlink":
        target.symlink_to(path)
    else:
        os.link(path, target)
    with pytest.raises(EvidenceError, match="UNALIASED"):
        export(target, tmp_path / "package")


def test_destination_inside_git_rejected_and_existing_never_overwritten(tmp_path):
    path = source(tmp_path)
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with pytest.raises(EvidenceError, match="OUTSIDE_REPOSITORY"):
        export(path, repo / "package")
    export(path, tmp_path / "package")
    before = (tmp_path / "package" / "manifest.json").read_bytes()
    with pytest.raises(EvidenceError, match="NEW_DESTINATION"):
        export(path, tmp_path / "package")
    assert (tmp_path / "package" / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["disk_full", "permission_denied", "fsync_failure"])
def test_private_publication_faults_leave_no_misleading_final_package(tmp_path, monkeypatch, fault):
    path = source(tmp_path)
    import rc6_audit_evidence.package as package
    if fault == "fsync_failure":
        monkeypatch.setattr(package, "_fsync_directory", lambda _: (_ for _ in ()).throw(OSError("synthetic fsync failure")))
    else:
        def failing_write(target, data):
            if target.name == "positions.jsonl":
                raise OSError(28 if fault == "disk_full" else 13, "synthetic fault")
            original(target, data)
        original = package._write_private
        monkeypatch.setattr(package, "_write_private", failing_write)
    with pytest.raises(OSError):
        export(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".package.sanitized-*"))


@pytest.mark.parametrize("mutation,reason", [("file_byte", "FILE_DIGEST"), ("row_byte_rehashed", "ROW_HASH"),
                                            ("money_rehashed", "LEDGER_MONEY"), ("drop_fill_rehashed", "ROW_COUNT"),
                                            ("duplicate_fill_rehashed", "DUPLICATE_OR_INVALID_FILL"),
                                            ("extra_sensitive_key", "SANITIZED_SCHEMA"), ("wrong_currency", "CURRENCY_IDENTITY"),
                                            ("orphan", "ORPHAN_FILL"), ("row_order", "DETERMINISTIC_ORDER")])
def test_independent_recomputation_attacks_even_with_rehashed_manifest(tmp_path, mutation, reason):
    path = source(tmp_path)
    package = tmp_path / "package"
    export(path, package)
    if mutation == "file_byte":
        (package / "positions.jsonl").write_bytes((package / "positions.jsonl").read_bytes().replace(b"GGAL", b"GGBL"))
    elif mutation in {"row_byte_rehashed", "money_rehashed", "extra_sensitive_key", "wrong_currency"}:
        rows = records(package / "positions.jsonl")
        if mutation == "row_byte_rehashed": rows[0]["symbol"] = "GGBL"
        if mutation == "money_rehashed": rows[0]["ledger"]["net_pnl"] = "999"
        if mutation == "extra_sensitive_key": rows[0]["account_number"] = "forbidden"
        if mutation == "wrong_currency": rows[0]["currency"] = "UNKNOWN"
        rewrite(package, "positions.jsonl", rows, row_hash=mutation != "row_byte_rehashed")
    else:
        rows = records(package / "fills.jsonl")
        if mutation == "drop_fill_rehashed": rows.pop()
        if mutation == "duplicate_fill_rehashed": rows[-1]["fill_id"] = rows[0]["fill_id"]
        if mutation == "orphan": rows[0]["position_id"] = "pos_" + "c" * 64
        if mutation == "row_order": rows.reverse()
        rewrite(package, "fills.jsonl", rows)
    with pytest.raises(EvidenceError, match=reason):
        verify_package(package)


def test_manifest_digest_independently_pinned_and_mixed_generations_rejected(tmp_path):
    path = source(tmp_path)
    result = export(path, tmp_path / "one")
    export_package(path, tmp_path / "two", sessions=SESSIONS, pseudonym_key=b"different-synthetic-external-seed-00000000000")
    with pytest.raises(EvidenceError, match="MANIFEST_DIGEST"):
        verify_package(tmp_path / "two", expected_manifest_sha256=result["manifest_sha256"])
    (tmp_path / "one" / "fills.jsonl").write_bytes((tmp_path / "two" / "fills.jsonl").read_bytes())
    with pytest.raises(EvidenceError, match="FILE_DIGEST"):
        verify_package(tmp_path / "one")


def test_extra_file_or_alias_in_package_fails_closed(tmp_path):
    path = source(tmp_path)
    export(path, tmp_path / "package")
    extra = tmp_path / "package" / "raw.sqlite"
    extra.write_text("must never be a member")
    with pytest.raises(EvidenceError, match="FILE_SET"):
        verify_package(tmp_path / "package")
    extra.unlink()
    one = tmp_path / "package" / "fills.jsonl"
    target = tmp_path / "raw-fills"
    target.write_bytes(one.read_bytes())
    one.unlink()
    one.symlink_to(target)
    with pytest.raises(EvidenceError, match="UNALIASED"):
        verify_package(tmp_path / "package")


def test_cli_roundtrip_deterministic_private_output_and_missing_source_safe_error(tmp_path):
    path = source(tmp_path)
    seed = tmp_path / "external.seed"
    seed.write_bytes(SEED)
    seed.chmod(0o600)
    command = [sys.executable, str(ROOT / "scripts/rc6_20session_evidence.py"), "export", "--source", str(path),
               "--output", str(tmp_path / "package"), "--pseudonym-seed-file", str(seed)]
    for session in SESSIONS:
        command.extend(["--session", session])
    produced = subprocess.run(command, check=True, capture_output=True, text=True)
    report = json.loads(produced.stdout)
    checked = subprocess.run([sys.executable, str(ROOT / "scripts/rc6_20session_evidence.py"), "recompute", "--package", str(tmp_path / "package"), "--manifest-sha256", report["manifest_sha256"]], check=True, capture_output=True, text=True)
    assert json.loads(checked.stdout)["currencies"]["ARS"]["net_pnl"] == "3"
    command[command.index("--source") + 1] = str(tmp_path / "missing-sensitive-source.sqlite")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 2 and not failed.stderr and "missing-sensitive-source" not in failed.stdout
    assert json.loads(failed.stdout)["status"] == "EXTERNAL_EVIDENCE_PENDING"


def test_canonical_paper_store_schema_export_not_only_custom_fixture(tmp_path, monkeypatch):
    from be_paper_engine import PaperStore, PaperBroker, D
    from test_rc6_performance_capture import quote
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    store = PaperStore(str(tmp_path / "actual-schema.sqlite"))
    broker = PaperBroker(store)
    q = quote(at="2026-09-09T14:00:00+00:00")
    assert broker._open(q, D(".8"), {})[0]
    position = store.open_positions()[0]
    assert broker._close(position, quote(price="110", at="2026-09-09T14:01:00+00:00"), "EOD_PAPER")
    before = Path(store.path).read_bytes()
    export(store.path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["counts"]["closed_positions"] == 1 and checked["counts"]["fills"] == 2
    assert checked["real_orders_sent"] == 0 and checked["real_routes"] == "NOT_CALLED"
    assert Path(store.path).read_bytes() == before


@pytest.mark.parametrize("mutation,reason", [
    ("extra_manifest_secret", "SANITIZED_MANIFEST"), ("hidden_gap", "MISSING_EVIDENCE"),
    ("edge_claim", "SAFETY_CONTRACT"), ("real_bool", "SAFETY_CONTRACT"),
    ("source_safety", "SAFETY_CONTRACT"), ("session_count", "SESSION_ROW_COUNT"),
    ("fake_lineage", "LINEAGE_PROVENANCE"), ("fake_units", "UNITS_PROVENANCE"),
    ("unknown_exit", "EXIT_REASON_PROVENANCE"), ("method_secret", "CALCULATION_VERSION"),
])
def test_misleading_provenance_or_secret_schema_injection_never_recomputes_green(tmp_path, mutation, reason):
    path = source(tmp_path)
    package = tmp_path / "package"
    export(path, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "extra_manifest_secret": manifest["account_number"] = "forbidden"
    if mutation == "hidden_gap": manifest["missing_evidence"] = []
    if mutation == "edge_claim": manifest["calibration_or_edge_claim"] = True
    if mutation == "real_bool": manifest["real_orders_sent"] = False
    if mutation == "source_safety": manifest["source_safety"] = "REAL_SOURCE"
    if mutation == "session_count": manifest["session_rows"][SESSIONS[0]]["fills"] = 1
    if mutation in {"fake_lineage", "fake_units", "unknown_exit"}:
        rows = records(package / "positions.jsonl")
        if mutation == "fake_lineage": rows[0]["lineage"]["status"] = "SOURCE_FIELDS_PRESENT"
        if mutation == "fake_units": rows[0]["units_per_lot_status"] = "VERIFIED"
        if mutation == "unknown_exit": rows[0]["exit_reason"] = "UNKNOWN_PRIVATE_REASON"
        rewrite(package, "positions.jsonl", rows)
    elif mutation == "method_secret":
        data = (canonical({"calculation_version": "rc6-independent-fill-cashflow.v1", "token": "forbidden"}) + "\n").encode()
        (package / "methodology.json").write_bytes(data)
        manifest["files"]["methodology.json"].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        manifest_path.write_text(canonical(manifest) + "\n")
    else:
        manifest_path.write_text(canonical(manifest) + "\n")
    with pytest.raises(EvidenceError, match=reason):
        verify_package(package)


def test_unmapped_free_text_exit_reason_is_not_exposed_or_claimed_native(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET close_reason='PRIVATE account 123 token never export'")
    export(path, tmp_path / "package")
    row = records(tmp_path / "package" / "positions.jsonl")[0]
    assert row["exit_reason"] == "LEGACY_REASON_UNMAPPED" and row["exit_reason_status"] == "NO_VERIFICADO"
    assert b"PRIVATE account" not in (tmp_path / "package" / "positions.jsonl").read_bytes()
    manifest = json.loads((tmp_path / "package" / "manifest.json").read_text())
    assert "LEGACY_EXIT_REASON_MAPPING" in manifest["missing_evidence"]


def test_raced_destination_is_preserved_by_atomic_no_replace(tmp_path, monkeypatch):
    path = source(tmp_path)
    destination = tmp_path / "package"
    import rc6_audit_evidence.package as package
    original = package._publish

    def race(staging, target):
        target.mkdir()
        original(staging, target)

    monkeypatch.setattr(package, "_publish", race)
    with pytest.raises(EvidenceError, match="NEW_DESTINATION"):
        export(path, destination)
    assert destination.is_dir() and not list(destination.iterdir())
    assert not list(tmp_path.glob(".package.sanitized-*"))


def test_after_publication_fsync_error_leaves_complete_recomputable_package(tmp_path, monkeypatch):
    path = source(tmp_path)
    import rc6_audit_evidence.package as package
    original = package._fsync_directory

    def fail_parent(target):
        if target == tmp_path:
            raise OSError("synthetic parent fsync error after directory rename")
        original(target)

    monkeypatch.setattr(package, "_fsync_directory", fail_parent)
    with pytest.raises(OSError):
        export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["currencies"]["ARS"]["net_pnl"] == "3"
    assert not list(tmp_path.glob(".package.sanitized-*"))


def test_source_views_do_not_replace_trading_tables(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE paper_fills RENAME TO unrelated_fills")
        connection.execute("CREATE VIEW paper_fills AS SELECT * FROM unrelated_fills")
    with pytest.raises(EvidenceError, match="SOURCE_SCHEMA_UNAVAILABLE"):
        export(path, tmp_path / "package")


def test_sql_query_deadline_is_programmatic_not_only_python_preflight(tmp_path, monkeypatch):
    path = source(tmp_path)
    import rc6_audit_evidence.package as package
    budget = Budget(Limits())
    with pytest.raises(EvidenceError, match="TIME_BUDGET_EXHAUSTED"):
        with readonly_snapshot(path, budget) as connection:
            monkeypatch.setattr(package.time, "monotonic", lambda: budget.deadline + 1)
            # The SQLite VM itself interrupts an expensive SELECT. No Python
            # preflight of _read(), sleep or assumed host timing proves this.
            with pytest.raises(sqlite3.OperationalError, match="interrupted"):
                connection.execute("SELECT COUNT(*) FROM paper_fills a CROSS JOIN paper_fills b CROSS JOIN paper_fills c CROSS JOIN paper_fills d CROSS JOIN paper_fills e CROSS JOIN paper_fills f CROSS JOIN paper_fills g")
    assert not (tmp_path / "package").exists()


def test_many_synthetic_rows_are_complete_and_under_conservative_evidence_budget(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        base = connection.execute("SELECT * FROM paper_positions").fetchone()
        legs = connection.execute("SELECT * FROM paper_fills").fetchall()
        for index in range(1, 300):
            position = list(base)
            position[0] = "SYNTHETIC-STRESS-POSITION-" + str(index)
            connection.execute("INSERT INTO paper_positions VALUES(" + ",".join("?" for _ in position) + ")", position)
            for leg in legs:
                fill = list(leg)
                fill[0] += 3 * index
                fill[1] = position[0]
                connection.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?)", fill)
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["counts"] == {"positions": 300, "fills": 900, "closed_positions": 300, "open_survivors": 0}
    assert checked["currencies"]["ARS"]["gross_pnl"] == "1800"
    assert checked["currencies"]["ARS"]["costs"] == "900"
    assert checked["currencies"]["ARS"]["net_pnl"] == "900"
    assert sum(f.stat().st_size for f in (tmp_path / "package").iterdir()) < 4 * 1024 * 1024


def test_sql_boundary_candidate_counts_cannot_hide_later_real_position(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        row = list(connection.execute("SELECT * FROM paper_positions").fetchone())
        row[0] = "A-OUTSIDE-COHORT-BOUNDARY-SENTINEL"
        row[7] = "OPEN"
        row[13] = "2026-10-03T03:00:00Z"
        row[14] = None
        row[16] = "{}"
        connection.execute("INSERT INTO paper_positions VALUES(" + ",".join("?" for _ in row) + ")", row)
        later = list(connection.execute("SELECT * FROM paper_positions WHERE paper_id='RAW-PAPER-POSITION-SECRET'").fetchone())
        later[0] = "Z-SECOND-REAL-POSITION-HIDDEN-BY-BAD-CAP"
        connection.execute("INSERT INTO paper_positions VALUES(" + ",".join("?" for _ in later) + ")", later)
        for leg in connection.execute("SELECT * FROM paper_fills").fetchall():
            clone = list(leg)
            clone[0] += 10
            clone[1] = later[0]
            connection.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?)", clone)
    with pytest.raises(EvidenceError, match="POSITION_ROW_BUDGET"):
        export(path, tmp_path / "package", limits=Limits(positions=1))
    assert not (tmp_path / "package").exists()


def test_entry_one_microsecond_before_cutoff_cannot_disappear_in_sqlite_rounding(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        at = "2026-10-03T02:59:59.999999Z"
        connection.execute("UPDATE paper_positions SET status='OPEN',opened_at=?,closed_at=NULL,features_json='{}',exit_cost=NULL,gross_pnl=NULL,net_pnl=NULL,close_reason=NULL", (at,))
        connection.execute("DELETE FROM paper_fills WHERE id>1")
        connection.execute("UPDATE paper_fills SET filled_at=?", (at,))
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["counts"]["open_survivors"] == 1


def test_decimal_reconciliation_keeps_exact_partial_cash_even_at_allowed_numeric_bounds(tmp_path):
    from fractions import Fraction
    path = source(tmp_path, include_second_currency=False)
    quantity = price = "123456789012345678901234.123456789012345678"
    multiplier = "999999999999999999999999.999999999999999999"
    remaining = "123456789012345678901233.123456789012345678"
    # Independently computed rational arithmetic: equal prices with a full
    # partial liquidation have zero gross, regardless of numeric magnitude.
    assert (Fraction(1) + Fraction(remaining) - Fraction(quantity)) * Fraction(price) * Fraction(multiplier) == 0
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET quantity=?,entry_cost='0',exit_cost='0',gross_pnl='0',net_pnl='0',features_json=?", (quantity, json.dumps({"contract_cash_multiplier": multiplier})))
        connection.execute("UPDATE paper_fills SET quantity=?,price=?,costs='0',slippage='0' WHERE id=1", (quantity, price))
        connection.execute("UPDATE paper_fills SET quantity='1',price=?,costs='0',slippage='0' WHERE id=2", (price,))
        connection.execute("UPDATE paper_fills SET quantity=?,price=?,costs='0',slippage='0' WHERE id=3", (remaining, price))
    export(path, tmp_path / "package")
    checked = verify_package(tmp_path / "package")
    assert checked["currencies"]["ARS"]["gross_pnl"] == "0"
    assert checked["currencies"]["ARS"]["net_pnl"] == "0"


def test_future_nonpaper_fill_is_not_a_safety_bypass_for_inspected_source_rows(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO paper_fills VALUES(6,'RAW-PAPER-POSITION-SECRET','BUY_REAL','2026-10-05T15:00:00Z','1','100','0','0')")
    with pytest.raises(EvidenceError, match="PAPER_FILL_REQUIRED"):
        export(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()


def test_peer_source_decision_and_commit_clocks_cannot_follow_position_exit(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        features = json.loads(connection.execute("SELECT features_json FROM paper_positions").fetchone()[0])
        lineage = features["performance_lineage"]
        for name in ("signal_started_at", "signal_at", "decision_at", "intent_at", "entry_fill_committed_at"):
            lineage[name] = "2026-09-10T14:00:00Z"
        lineage["candidate_tree_sha"] = "c" * 40
        lineage["manifest_sha256"] = "d" * 64
        connection.execute("UPDATE paper_positions SET features_json=?", (json.dumps(features),))
    with pytest.raises(EvidenceError, match="LINEAGE"):
        export(path, tmp_path / "package")


def test_peer_open_survivor_rejects_rehashed_invented_closed_ledger_pnl(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET status='OPEN',closed_at=NULL,exit_cost=NULL,gross_pnl=NULL,net_pnl=NULL,close_reason=NULL")
        connection.execute("DELETE FROM paper_fills WHERE id=3")
    export(path, tmp_path / "package")
    rows = records(tmp_path / "package" / "positions.jsonl")
    rows[0]["ledger"].update(exit_cost="777", gross_pnl="999", net_pnl="888")
    rewrite(tmp_path / "package", "positions.jsonl", rows)
    with pytest.raises(EvidenceError, match="OPEN_LEDGER"):
        verify_package(tmp_path / "package", expected_manifest_sha256=hashlib.sha256((tmp_path / "package" / "manifest.json").read_bytes()).hexdigest())


@pytest.mark.parametrize("forged_limits", [{"positions": 1}, {"fills": 1}, {"row_bytes": 512}])
def test_peer_declared_manifest_caps_cannot_be_smaller_than_delivered_evidence(tmp_path, forged_limits):
    path = source(tmp_path)
    export(path, tmp_path / "package")
    manifest_path = tmp_path / "package" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["limits"].update(forged_limits)
    manifest_path.write_text(canonical(manifest) + "\n")
    with pytest.raises(EvidenceError, match="MANIFEST_BUDGET"):
        verify_package(tmp_path / "package", expected_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())


def test_peer_one_lifecycle_cannot_reopen_after_full_liquidation_and_hide_extra_trade(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM paper_fills")
        connection.executemany("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?)", [
            (1, "RAW-PAPER-POSITION-SECRET", "BUY_SIMULATED", "2026-09-09T14:00:00Z", "1", "100", "0.67", "0"),
            (2, "RAW-PAPER-POSITION-SECRET", "SELL_SIMULATED", "2026-09-09T14:15:00Z", "1", "102", "0.33", "0"),
            (3, "RAW-PAPER-POSITION-SECRET", "BUY_SIMULATED", "2026-09-09T14:30:00Z", "2", "100", "1.33", "0"),
            (4, "RAW-PAPER-POSITION-SECRET", "SELL_SIMULATED", "2026-09-09T15:00:00Z", "2", "102", "0.67", "0"),
        ])
    with pytest.raises(EvidenceError, match="LIFECYCLE_REOPEN"):
        export(path, tmp_path / "package")


def test_extreme_numeric_bound_cannot_round_nonzero_gross_into_false_ledger_zero(tmp_path):
    from fractions import Fraction
    path = source(tmp_path, include_second_currency=False)
    quantity = multiplier = price = "1000000000000000000000000.000000000000000001"
    final_price = "1000000000000000000000000.000000000000000002"
    first_sale_quantity = "1000000000000000000000000"
    final_sale_quantity = "0.000000000000000001"
    actual = (Fraction(first_sale_quantity) * Fraction(price) + Fraction(final_sale_quantity) * Fraction(final_price) - Fraction(quantity) * Fraction(price)) * Fraction(multiplier)
    assert actual > 0
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET quantity=?,entry_cost='0',exit_cost='0',gross_pnl='0',net_pnl='0',features_json=?", (quantity, json.dumps({"contract_cash_multiplier": multiplier})))
        connection.execute("UPDATE paper_fills SET quantity=?,price=?,costs='0',slippage='0' WHERE id=1", (quantity, price))
        connection.execute("UPDATE paper_fills SET quantity=?,price=?,costs='0',slippage='0' WHERE id=2", (first_sale_quantity, price))
        connection.execute("UPDATE paper_fills SET quantity=?,price=?,costs='0',slippage='0' WHERE id=3", (final_sale_quantity, final_price))
    with pytest.raises(EvidenceError, match="LEDGER_MONEY_RECONCILIATION"):
        export(path, tmp_path / "package")


def test_native_intent_and_commit_may_follow_stored_fill_clock_before_exit(tmp_path):
    path = source(tmp_path, include_second_currency=False)
    with sqlite3.connect(path) as connection:
        features = json.loads(connection.execute("SELECT features_json FROM paper_positions").fetchone()[0])
        lineage = features["performance_lineage"]
        lineage["intent_at"] = "2026-09-09T14:00:00.001Z"
        lineage["entry_fill_committed_at"] = "2026-09-09T14:00:00.002Z"
        lineage["candidate_tree_sha"] = "c" * 40
        lineage["manifest_sha256"] = "d" * 64
        connection.execute("UPDATE paper_positions SET features_json=?", (json.dumps(features),))
    export(path, tmp_path / "package")
    assert verify_package(tmp_path / "package")["counts"]["closed_positions"] == 1


def test_duplicate_control_state_cannot_hide_real_mode_or_real_orders(tmp_path):
    path = source(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE observer_state RENAME TO old_observer_state")
        connection.execute("CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER)")
        connection.executemany("INSERT INTO observer_state VALUES(1,?,?)", [("PRODUCTION_PAPER", 0), ("REAL", 1)])
    with pytest.raises(EvidenceError, match="PAPER_SOURCE_SAFETY_REQUIRED"):
        export(path, tmp_path / "package")
    assert not (tmp_path / "package").exists()

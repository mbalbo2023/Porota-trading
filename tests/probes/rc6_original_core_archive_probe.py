"""Replay original #468/#469 core audit inputs on one complete Git archive.

Original driver bytes are preserved in fixtures/original_core_audits. Only
selected AST definitions execute from the multi-front original scripts; their
function bodies and inputs remain unchanged. U08 has a separate native-caller
reconstruction because its handoff includes a receipt, not an executable driver.
No production checkout, database, service, account, or network is used.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import runpy
import socket
import sqlite3
import sys
import tempfile
import traceback


DRIVER_HASHES = {
    "issue468_adversarial_harness.py":
        "a9d2ea84ae48bbff0ccf07bcee85a346e5fe874446edfdb4cf3ad95225845595",
    "issue469_f01_f02_probes.py":
        "dd3c1efeb06133cea6aad69b70de6120597bf063d67fe1cbecc1d2b6500f4bf5",
    "issue469_scalping_economics_probe.py":
        "553128c430c5b9f71efcf34882df96bebb26f01264f2bd6c990fc1c91a51d1fe",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_definitions(path, names, namespace):
    """Select original definitions; never execute source-routing top-level code."""
    text = path.read_text()
    tree = ast.parse(text, filename=str(path))
    chosen, hashes = [], {}
    for node in tree.body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
        if name in names:
            chosen.append(node)
            fragment = ast.get_source_segment(text, node)
            hashes[name] = hashlib.sha256(fragment.encode()).hexdigest()
    assert set(hashes) == set(names), "ORIGINAL_DEFINITION_UNAVAILABLE"
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(path), "exec"), namespace)
    return hashes


def u08_native_reconstruction(directory):
    """Retain original five-key collision and call the old native entry API."""
    from be_paper_engine import PaperBroker, PaperStore, Quote
    from bm_exit_supervisor import PositionExitSupervisor, init_schema
    from bs_instrument_contracts import InstrumentContract
    from rc6_ppi_future_contract_policy import standard_dlr_terms

    opened_at = "2026-10-05T12:00:00-03:00"
    exit_at = "2026-10-05T12:01:00-03:00"
    clock = [opened_at]

    def broker(name, *, slippage_bps="2", **kwargs):
        return PaperBroker(PaperStore(str(directory / name)), initial_cash="100000000",
            risk_pct="0.005", max_positions=10, participation="1",
            max_position_pct="1", max_total_exposure_pct="1", slippage_bps=slippage_bps,
            clock_fn=lambda: clock[0], session_policy=None, require_supervisor=False,
            ai_mode="OFF", economics_mode="SHADOW", daily_loss_pct="5",
            daily_soft_stop_pct="4", **kwargs)

    def quote(symbol, family, bid, ask, *, currency="ARS", market="BYMA", contract=None):
        return Quote(symbol, family, "INMEDIATA", D(bid), D(bid), D(ask),
            D("1000"), D("1000"), clock[0], currency=currency, market=market,
            contract=contract, metadata_source="PPI_CATALOG:SYNTHETIC_469_RECONSTRUCTION",
            book_at=clock[0], trade_at=clock[0], last_kind="TRADE")

    spot = broker("spot_identity.sqlite")
    init_schema(spot.store)  # The supervisor's own native schema initializer.
    first = quote("DUPL", "ACCIONES", "99", "100")
    spot.store.add_quote(first)
    opened, reason, _ = spot._open(first, D(".8"), {})
    assert opened, "U08_SPOT_POSITIVE_ENTRY_FAILED:" + reason
    position = spot.store.open_positions()[0]
    spot_stop = position["stop_price"]
    clock[0] = exit_at
    exact = quote("DUPL", "ACCIONES", "90", "90.05")
    wrong = quote("DUPL", "ACCIONES", "200", "200.05", currency="USD", market="NASDAQ")
    spot.store.add_quote(exact)
    spot.store.add_quote(wrong)
    selected = spot.store.latest_quote(position)
    supervisor = PositionExitSupervisor(spot, clock_fn=lambda: clock[0], max_hold_minutes=360)
    collision_verdicts = supervisor.tick()  # Same native clock path, no injected map.
    collision_status = spot.store.open_positions()[0]["status"] if spot.store.open_positions() else "CLOSED"
    control_verdicts = supervisor.tick({("DUPL", "ACCIONES", "INMEDIATA", "ARS", "BYMA"): exact})
    with spot.store.connect() as connection:
        control_status = connection.execute("SELECT status FROM paper_positions").fetchone()[0]

    clock[0] = opened_at
    terms = standard_dlr_terms("DLR/OCT26")
    contract = InstrumentContract("DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        D("1000"), D("1"), "PPI_PRIMARY+A3_OFFICIAL:SYNTHETIC_469_RECONSTRUCTION",
        expires_at=terms["expires_at"], underlying=terms["underlying"],
        minimum_quantity=D("1"), paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"))
    future = broker("future_identity.sqlite", slippage_bps="0",
        stop_loss_pct=str(D("10") / D("1500")), target_gain_pct=".10")
    opening_quote = quote("DLR/OCT26", "FUTUROS", "1499.5", "1500", market="A3", contract=contract)
    # Bound native size to the original representative one-contract position.
    opening_quote = __import__("dataclasses").replace(opening_quote, ask_size=D("1"))
    future.store.add_quote(opening_quote)
    opened, reason, _ = future._open_future(opening_quote, D(".8"), {})
    assert opened, "U08_FUTURE_POSITIVE_ENTRY_FAILED:" + reason
    position = future.store.active_future_positions()[0]
    future_stop = json.loads(position["metadata_json"])["stop_loss_price"]
    clock[0] = exit_at
    exact = quote("DLR/OCT26", "FUTUROS", "1400", "1400.5", market="A3", contract=contract)
    wrong = quote("DLR/OCT26", "FUTUROS", "2000", "2000.5", currency="USD", market="NASDAQ")
    future.store.add_quote(exact)
    future.store.add_quote(wrong)
    selected_future = future.store.latest_quote({**position, "asset_class": "FUTUROS"})
    future.supervise_futures(clock[0])  # bv_paper_runtime.run_clock's actual delegate.
    collision_future_status = "ACTIVE" if future.store.active_future_positions() else "CLOSED"
    future._on_future_quote(exact, allow_new_openings=False)
    control_future_status = "ACTIVE" if future.store.active_future_positions() else "CLOSED"
    return {
        "scope": "INDEPENDENT_NATIVE_CALLER_RECONSTRUCTION_OF_ORIGINAL_RECEIPT_NOT_ORIGINAL_DRIVER",
        "spot": {"stop_price": spot_stop,
            "selected_currency_market": [selected.currency, selected.market],
            "collision_verdicts": [v.__dict__ for v in collision_verdicts],
            "collision_status": collision_status,
            "exact_quote_control_verdicts": [v.__dict__ for v in control_verdicts],
            "exact_quote_control_status": control_status},
        "future": {"selected_currency_market": [selected_future.currency, selected_future.market],
            "configured_stop": future_stop, "exact_bid": "1400", "collision_status": collision_future_status,
            "exact_quote_control_status": control_future_status},
        "product_red": (collision_status == "OPEN" and control_status == "CLOSED"
            and any(v.state == "WATCH_IDENTITY_MISMATCH" for v in collision_verdicts)
            and collision_future_status == "ACTIVE" and control_future_status == "CLOSED"),
        "original_ambiguous_catalog_reader_replayed": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    index = json.loads(args.source_index.read_bytes())
    root = Path(index["extracted_root"]).resolve(strict=True)
    output = args.output.resolve()
    assert not output.is_relative_to(root)
    expected = index["source_file_hashes"]
    before = {name: sha(root / name) for name in expected}
    assert before == expected, "ARCHIVE_SOURCE_MISMATCH"
    drivers = Path(__file__).resolve().parent / "fixtures" / "original_core_audits"
    assert {name: sha(drivers / name) for name in DRIVER_HASHES} == DRIVER_HASHES
    sys.path[:] = [str(root)] + [p for p in sys.path if not p.startswith("/workspace/porota_rc6_")]
    os.chdir(root)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.pop("POROTA_RUNTIME_SCHEMA_READY", None)
    sys.dont_write_bytecode = True
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append("NETWORK_ATTEMPT_BLOCKED")
        raise RuntimeError("OFFLINE_ORIGINAL_CORE_PROBE_NETWORK_FORBIDDEN")

    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = deny
    socket.getaddrinfo = deny
    started = datetime.now(timezone.utc).isoformat()
    import cf_intraday_scalping as sc
    from be_paper_engine import PaperBroker, PaperStore, Quote

    results, fragments = {}, {}

    def record(name, call):
        try:
            results[name] = {"execution": "COMPLETED", "result": call()}
        except Exception as error:
            results[name] = {"execution": "HARNESS_ERROR_NOT_PRODUCT_RED",
                "error": type(error).__name__ + ":" + str(error), "traceback": traceback.format_exc()}

    with tempfile.TemporaryDirectory(prefix="rc6-original-core-archive-") as scratch:
        directory = Path(scratch)
        original_root = type("Root", (), {"name": scratch})()
        scope = dict(Path=Path, sqlite3=sqlite3, contextmanager=contextmanager,
            datetime=datetime, timedelta=timedelta, timezone=timezone, D=D,
            sc=sc, PaperStore=PaperStore, PaperBroker=PaperBroker, Quote=Quote, ROOT=original_root)
        names = {"iso", "Store", "rows", "REC", "NOW", "scalp_store", "fill_scalp",
                 "scalp_case", "events", "scenarios", "vol_contract", "dense_main_signal"}
        fragments["issue468_adversarial_harness.py"] = selected_definitions(
            drivers / "issue468_adversarial_harness.py", names, scope)
        for name in ("regular", "same_minute_15_points", "irregular_43_minutes", "stale_tail_20_minutes"):
            events, kwargs = scope["scenarios"][name]
            record("468:" + name, lambda name=name, events=events, kwargs=kwargs:
                   scope["scalp_case"](name, events, **kwargs))
        for kind in ("monotone_interval", "cumulative_with_reset"):
            record("468:" + kind, lambda kind=kind: scope["vol_contract"](kind))
        record("468:main_sample_cadence", scope["dense_main_signal"])
        # Report the native decision contract separately without changing the
        # original function or substituting any mathematical/gate implementation.
        def main_contracts():
            records = []
            for minutes in (90, 1):
                store = PaperStore(str(directory / f"main_dense_{minutes}.db"))
                quote = store.latest_quote({"symbol": "AUDIT", "asset_class": "ACCIONES",
                    "settlement": "A-24HS", "currency": "ARS", "market": "BYMA"})
                decision = PaperBroker(store, daily_loss_pct=None).decide(quote)
                records.append({"time_span_minutes": minutes, "action": decision[0],
                    "features": decision[3]})
            return records
        record("468:main_native_contract_diagnostics", main_contracts)

        rollback = {"datetime": datetime, "timedelta": timedelta, "json": json}
        fragments["issue469_f01_f02_probes.py"] = selected_definitions(
            drivers / "issue469_f01_f02_probes.py", {"intraread_clock_rollback_probe"}, rollback)
        record("469:U03", lambda: rollback["intraread_clock_rollback_probe"](directory))

        def economics():
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                runpy.run_path(str(drivers / "issue469_scalping_economics_probe.py"), run_name="__main__")
            return json.loads(stdout.getvalue())
        record("469:U06", economics)
        old_environment = dict(os.environ)
        try:
            os.environ["PAPER_SECTOR_CONCENTRATION_POLICY"] = "OBSERVATION_ONLY"
            record("469:U08", lambda: u08_native_reconstruction(directory))
        finally:
            os.environ.clear()
            os.environ.update(old_environment)

    imported, alien, origins = {}, {}, {}
    repo_modules = {name[:-3].replace("/", ".").removesuffix(".__init__")
                    for name in expected if name.endswith(".py")}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if not path.is_file():
            continue
        origins[name] = str(path)
        if path.is_relative_to(root):
            relative = str(path.relative_to(root))
            imported[name] = {"path": relative, "sha256": sha(path)}
            if relative not in expected or expected[relative] != sha(path):
                alien[name] = str(path)
        elif name in repo_modules or str(path).startswith("/workspace/porota_rc6_"):
            # multiprocessing aliases the exact entry-point module. It is the
            # pinned external driver, never a production/helper source overlay.
            if not (name in {"__main__", "__mp_main__"} and path == Path(__file__).resolve()):
                alien[name] = str(path)
    after = {name: sha(root / name) for name in expected}
    valid = before == after and not alien and not attempts
    receipt = {
        "schema": "rc6.original-core-audit-archive-replay.v1",
        "status": "VALID_SOURCE_REPLAY" if valid else "INVALID_SOURCE_OR_NETWORK_REPLAY",
        "source_sha": index["source_sha"], "source_tree": index["source_tree"],
        "archive_sha256": index["archive_sha256"], "source_index_sha256": sha(args.source_index),
        "source_files_before": before, "source_files_after": after, "source_unchanged": before == after,
        "imported_repo_modules": imported, "alien_repo_modules": alien,
        "all_imported_file_origins": origins, "network_attempts": len(attempts),
        "runner_sha256": sha(Path(__file__)), "original_driver_sha256": DRIVER_HASHES,
        "unchanged_original_definition_sha256": fragments,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "results": results,
        "scope": "OFFLINE_SYNTHETIC_CORE_CALLERS_ONLY; SELECTED_ORIGINAL_DEFINITIONS_AND_SEPARATE_U08_RECONSTRUCTION",
        "real_orders_sent": 0, "release_artifact_verified": False,
        "external_20_sessions_master": "EXTERNAL_NO_VERIFICADO", "economic_edge": "EDGE_NO_DEMOSTRADO",
        "provider_volume_semantics": "NO_VERIFICADO; RESET_AND_INTERVAL_LABELS_ARE_SYNTHETIC_GROUND_TRUTH",
        "AUD01": "CONTROL_PRESERVED_FROM_466_NO_NEW_FIX_RED_CLAIM",
        "AUD04": "BASELINE_CAPACITY_LIMITATION_NOT_REPLAYED_AS_A_SOFTWARE_ERROR_OR_APPROVED_CAPACITY",
        "AUD05": "EVENT_COUNT_EQUIVALENCE_AND_MISSING_DECLARED_TEMPORAL_CONTRACT_NOT_AUTOMATIC_MATH_ERROR",
    }
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({"status": receipt["status"], "source_sha": index["source_sha"],
        "source_unchanged": before == after, "repo_imports": len(imported), "alien_imports": len(alien),
        "network_attempts": len(attempts), "cases": {key: value["execution"] for key, value in results.items()}}))
    return 0 if valid and all(value["execution"] == "COMPLETED" for value in results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

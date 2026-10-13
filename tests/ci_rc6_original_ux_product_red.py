"""Reproduce original UX omissions through native writers in an exact archive.

This external probe uses the unmodified initial c27+#470 merge. It creates only
offline synthetic ledgers, then reads materialized copies. API/setup failures
are PROBE_INVALID, never evidence of a product defect. No browser is launched.
"""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import gc
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from urllib.parse import unquote, urlsplit

SEED = "0f810c168b0bfb5362148b804f3e15ffd1855610"
TREE = "764229fb8807d15022834fc422f9d6baf56437ad"
PARENTS = ["c27dfd963c4fe83465c0f2105347e974fbbe6356",
           "4a5fc6384260b31f9a07e791093add6ee83102a1"]


class ProbeInvalid(RuntimeError):
    pass


def prerequisite(value, reason):
    if not value:
        raise ProbeInvalid(reason)


def protected_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def inventory(database):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        if path.exists():
            info = path.stat()
            result[str(path)] = {"sha256": hashlib.sha256(protected_bytes(path)).hexdigest(),
                "bytes": info.st_size, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns,
                "atime_ns": info.st_atime_ns, "ctime_ns": info.st_ctime_ns}
    return result


def materialize(database, target):
    """Copy the finished native main/WAL without opening the producer source."""
    before = inventory(database)
    prerequisite(bool(before), "NATIVE_DATABASE_NOT_WRITTEN")
    for suffix in ("", "-wal"):
        source = Path(str(database) + suffix)
        if source.exists():
            with Path(str(target) + suffix).open("xb") as stream:
                stream.write(protected_bytes(source))
    prerequisite(inventory(database) == before, "NATIVE_SOURCE_CHANGED_DURING_COPY")
    return before


def run(index, output):
    prerequisite(index.get("source_sha") == SEED and index.get("candidate_tree_sha") == TREE
                 and index.get("parents") == PARENTS and index.get("overlays") == [],
                 "EXACT_ORIGINAL_COMBINED_ARCHIVE_REQUIRED")
    product = Path(index["extracted_root"]).resolve()
    prerequisite(not output.exists() and not output.resolve().is_relative_to(product),
                 "NEW_OUTPUT_OUTSIDE_PRODUCT_REQUIRED")
    expected = index["source_file_hashes"]
    def source_hashes():
        return {str(path.relative_to(product)): hashlib.sha256(protected_bytes(path)).hexdigest()
                for path in sorted(product.rglob("*")) if path.is_file()}
    before = source_hashes()
    prerequisite(before == expected, "ARCHIVE_FILES_DO_NOT_MATCH_SOURCE_INDEX")
    output.mkdir(parents=True)
    native = output / "native"
    private = output / "private"
    native.mkdir()
    private.mkdir()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(product))
    network, blocked_sources, frozen = [], [], {}
    original_connect = sqlite3.connect

    def no_network(*args, **kwargs):
        network.append("BLOCKED")
        raise ProbeInvalid("NETWORK_ATTEMPTED")

    def private_or_writer_connect(value, *args, **kwargs):
        raw = os.fsdecode(value)
        raw = unquote(urlsplit(raw).path) if raw.startswith("file:") else raw
        path = Path(raw).resolve() if raw != ":memory:" else None
        members = [Path(member) for data in frozen.values() for member in data]
        if path in members or path is not None and path.exists() and any(
                member.exists() and path.samefile(member) for member in members):
            blocked_sources.append("BLOCKED")
            raise ProbeInvalid("SQLITE_OPENED_FROZEN_NATIVE_SOURCE")
        return original_connect(value, *args, **kwargs)

    socket.socket.connect = no_network
    socket.create_connection = no_network
    sqlite3.connect = private_or_writer_connect
    os.environ["POROTA_RUNTIME_SCHEMA_READY"] = "0"
    os.environ["POROTA_SHADOW_RUNTIME_ROOT"] = str(private / "unpublished-shadow")
    try:
        from be_paper_engine import PaperStore
        from bf_production_paper_observer import _support_schema
        from bs_instrument_contracts import InstrumentContract
        from rc6_paper_family_lifecycle import FamilyPaperExecutor, future_positions
        from rc6_ppi_future_contract_policy import standard_dlr_terms
        from rc6_trader_dashboard.projection import Projection, Store
        from rc6_trader_dashboard.routes import build_page
        import cu_history_store_v2_hf6 as history
        import rc6_annual_instrument_analysis as annual

        database = native / "paper.db"
        store = PaperStore(str(database))
        _support_schema(store)
        terms = standard_dlr_terms("DLR/OCT26")
        contract = InstrumentContract("DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
            Decimal("1000"), Decimal("1"), "PPI_PRIMARY+A3_OFFICIAL:OFFLINE_SYNTHETIC",
            expires_at=terms["expires_at"], minimum_quantity=Decimal("1"),
            paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=Decimal("1"),
            underlying=terms["underlying"])
        executor = FamilyPaperExecutor(store)
        opened = "2026-10-02T15:59:00+00:00"
        marked = "2026-10-02T16:00:00+00:00"
        executor.open_future(contract, lifecycle_id="ORIGINAL-FUT", event_id="ORIGINAL-FUT:OPEN",
            entry_price="1500", quantity="1", entry_cost="100", occurred_at=opened,
            book_at=opened)
        executor.mark_future(contract, lifecycle_id="ORIGINAL-FUT", event_id="ORIGINAL-FUT:MARK",
            mark_price="1510", book_at=marked, occurred_at=marked)
        with closing(store.connect()) as connection:
            futures = future_positions(None, connection=connection, active_only=True)
            spots = connection.execute("SELECT count(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0]
        prerequisite(len(futures) == 1 and futures[0]["status"] == "ACTIVE" and spots == 0,
                     "NATIVE_ACTIVE_FUTURE_ZERO_SPOT_NOT_PRODUCED")
        gc.collect()
        copied = private / "paper.db"
        frozen[str(database)] = materialize(database, copied)
        with Store(copied, now=datetime.fromisoformat(marked)) as ui_store:
            projection = Projection(ui_store)
            positions = projection.positions()
            counts = projection.counts()
            prerequisite(positions.state == "AVAILABLE" and not ui_store.errors,
                         "ORIGINAL_POSITIONS_READER_API_OR_SCHEMA_INVALID")
        surfaces = {}
        for path in ("/", "/en-vivo", "/en-vivo/posiciones", "/riesgo/posiciones", "/riesgo/exposicion", "/riesgo/liquidez"):
            document, headers = build_page(path, {}, copied)
            surfaces[path] = {"future_visible": "DLR/OCT26" in document,
                "html_sha256": hashlib.sha256(document.encode()).hexdigest(), "headers": headers}
            filename = "home.html" if path == "/" else path.strip("/").replace("/", "-") + ".html"
            (output / filename).write_text(document)
        omitted = positions.total == 0 and not positions.rows and counts.get("open_positions") == 0
        future_case = {"id": "UX470-I04", "classification": "PRODUCT_RED" if omitted else "PRODUCT_POSITIVE",
            "native_writer": "FamilyPaperExecutor.open_future + mark_future", "active_future_rows": len(futures),
            "open_spot_rows": spots, "native_future": futures[0], "projection_total": positions.total,
            "projection_rows": len(positions.rows), "projection_state": positions.state,
            "ui_open_positions": counts.get("open_positions"), "surfaces": surfaces,
            "attribution": "Original combined seed c27+#470, not isolated #470 or a convergence head"}

        history_db = native / "history.db"
        history_store = PaperStore(str(history_db))
        known = ("2026-10-01T15:00:00+00:00", "2026-10-02T15:00:00+00:00")
        results = []
        for close, observed in zip((100.0, 80.0), known):
            candle = history.Candle("GGAL", "ACCIONES", "BYMA", "A-24HS", "2026-09-30",
                close, close + 1, close - 1, close, 1000, "PPI", observed_at=observed,
                metadata={"currency": "ARS", "offline_synthetic": True})
            results.append(history.append_candle(history_store, candle))
        with closing(history_store.connect()) as connection:
            versions = [dict(row) for row in connection.execute(
                "SELECT id,date,close,source,observed_at,metadata_json FROM history_versions_v2 ORDER BY id")]
            canonical = dict(connection.execute(
                "SELECT date,close,source,observed_at,version_id FROM history_canonical_v2").fetchone())
        prerequisite(len(versions) == 2 and versions[0]["close"] == 100 and canonical["close"] == 80,
                     "NATIVE_DISTINCT_HISTORY_REVISIONS_NOT_PRODUCED")
        gc.collect()
        history_copy = private / "history.db"
        frozen[str(history_db)] = materialize(history_db, history_copy)
        os.environ["HIST_DB_PATH"] = str(history_copy)
        identity = ("ACCIONES", "GGAL", "BYMA", "A-24HS")
        bars = annual._bars(identity, date(2026, 10, 1))
        prerequisite(len(bars) == 1 and bars[0]["close"] == 80, "ORIGINAL_ANNUAL_NATIVE_READER_INVALID")
        document = annual._render_report(identity, bars, 2026)
        (output / "annual-history-concept.html").write_text(document)
        display = {"provider_source_visible": "PPI" in document,
            "bar_date_visible": "2026-09-30" in document,
            "version_observation_visible": known[1] in document,
            "currency_visible": "ARS" in document,
            "version_observation_projected": "observed_at" in bars[0],
            "decision_input_contract_visible": "input" in document.lower() or "decisión" in document.lower()}
        missing = display["provider_source_visible"] and display["bar_date_visible"] and not display["version_observation_visible"] and not display["version_observation_projected"]
        history_case = {"id": "AUD-468-16", "classification": "PRODUCT_RED" if missing else "PRODUCT_POSITIVE",
            "scope": "HISTORY_PRESENTATION_SOURCE_CUT_IDENTITY_SUBCLAUSE",
            "native_writer": "cu_history_store_v2_hf6.append_candle", "native_append_results": results,
            "native_versions": versions, "canonical": canonical, "annual_identity": identity,
            "annual_calendar_date_cutoff": "2026-10-01", "annual_rows": bars,
            "display_contract": display,
            "observation": "The calendar date filter returns current canonical history but drops its version observation clock from projection and presentation.",
            "limits": ["Calendar date is not an observation/decision cut; no binding lookahead is claimed.",
                       "This reproduces the history presentation subclause, not all five concept surfaces or broker impact.",
                       "No identical reingest is used, so this is not AUD08 renamed as AUD16."]}
        return {"cases": [future_case, history_case]}
    finally:
        gc.collect()
        imports, unexpected = [], []
        for name, module in sorted(sys.modules.items()):
            file = getattr(module, "__file__", None)
            if not file:
                continue
            path = Path(file).resolve()
            if path.is_relative_to(product):
                relative = str(path.relative_to(product))
                imports.append({"module": name, "path": relative,
                    "sha256": hashlib.sha256(protected_bytes(path)).hexdigest(),
                    "matches_archived_blob": hashlib.sha256(protected_bytes(path)).hexdigest() == expected.get(relative)})
            elif str(path).startswith(("/workspace/porota_", "/tmp/rc6_finance_core_original_")) and path != Path(__file__).resolve():
                unexpected.append({"module": name, "path": str(path)})
        evidence = {"schema": "rc6.original-ux-native-product-red-source-proof.v1", "source_sha": SEED,
            "candidate_tree_sha": TREE, "parents": PARENTS, "archive_sha256": index["archive_sha256"],
            "overlays": [], "tracked_source_files": len(before), "source_hashes_unchanged": source_hashes() == before,
            "imported_product_modules": imports, "unexpected_product_imports": unexpected,
            "network_attempts": len(network), "frozen_source_sqlite_attempts": len(blocked_sources),
            "native_sources_unchanged_after_projection": all(inventory(Path(path)) == data for path, data in frozen.items()),
            "native_source_inventory": frozen, "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runtime_touched": False, "ppi_watch_touched": False, "synthetic": True}
        (output / "source-proof.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        prerequisite(evidence["source_hashes_unchanged"] and not unexpected and not network and not blocked_sources
                     and evidence["native_sources_unchanged_after_projection"]
                     and all(row["matches_archived_blob"] for row in imports), "PROBE_SOURCE_OR_CUSTODY_INVALID")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output must be a new directory")
    try:
        receipt = run(json.loads(args.source_index.read_text()), args.output)
        receipt.update(schema="rc6.original-ux-native-product-red.v1", recorded_at=datetime.now(timezone.utc).isoformat(),
            command_argv=sys.argv, source_sha=SEED, candidate_tree_sha=TREE, parents=PARENTS, runtime_touched=False)
        receipt["status"] = "PRODUCT_RED_REPRODUCED" if all(case["classification"] == "PRODUCT_RED" for case in receipt["cases"]) else "PRODUCT_BEHAVIOR_OBSERVED"
    except Exception as error:
        receipt = {"schema": "rc6.original-ux-native-product-red.v1", "status": "PROBE_INVALID",
            "error_class": type(error).__name__, "reason": str(error) if isinstance(error, ProbeInvalid) else "API_SETUP_OR_RUNTIME_ERROR",
            "recorded_at": datetime.now(timezone.utc).isoformat()}
    if args.output.exists() and not (args.output / "product-observations.json").exists():
        with (args.output / "product-observations.json").open("x") as stream:
            stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: receipt[key] for key in ("status", "error_class", "reason") if key in receipt}))
    raise SystemExit(0 if receipt["status"] == "PRODUCT_RED_REPRODUCED" else 1)

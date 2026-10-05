"""Read-only acceptance of a completed native 12000/60000 SHADOW fixture.

Run only after the synthetic producer exits. Every measured query verifies the
same four sealed members; no original report/checkpoint is logically decoded.
The receipt distinguishes complete index population from sampled UI requests.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from time import monotonic, perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rc6_audit_evidence.sqlite_snapshot import readonly_copy
from rc6_shadow_runtime import persistence
from rc6_shadow_runtime.projection import open_projection
from rc6_trader_dashboard.datasets import shadow_rows
from rc6_trader_dashboard.projected_generation import VERIFICATION_LEVEL
from rc6_trader_dashboard.projection import Projection, Store, funnel_cohort_id
from rc6_trader_dashboard.routes import build_page


class GateFailure(AssertionError):
    def __init__(self, gate, details=None):
        super().__init__(gate)
        self.details = details or {}


def require(value, gate, details=None):
    if not value:
        raise GateFailure(gate, details)


def protected_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def custody_inventory(database, root):
    paths = [Path(str(database)+suffix) for suffix in ("", "-wal", "-shm", "-journal")]
    for folder in (root, Path(str(root)+".authority")):
        if folder.exists():
            paths.extend(path for path in folder.rglob("*") if path.is_file())
    result = {}
    for path in paths:
        if path.exists():
            info = path.stat()
            result[str(path)] = {"sha256": hashlib.sha256(protected_bytes(path)).hexdigest(),
                "bytes": info.st_size, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns,
                "atime_ns": info.st_atime_ns, "ctime_ns": info.st_ctime_ns}
    return result


def run(database, root, *, catalog_count, observation_count, receipt):
    require(not any(member.is_symlink() for path in (database, root) for member in (path, *path.parents)),
            "SOURCE_ALIAS_FORBIDDEN")
    database, root = database.resolve(), root.resolve()
    require(database.is_file() and root.is_dir(), "COMPLETED_NATIVE_FIXTURE_REQUIRED")
    require(not receipt.resolve().is_relative_to(root)
            and not receipt.resolve().is_relative_to(Path(str(root)+".authority"))
            and receipt.resolve() not in {Path(str(database)+suffix) for suffix in ("", "-wal", "-shm", "-journal")},
            "RECEIPT_MUST_BE_OUTSIDE_SOURCES")
    before = custody_inventory(database, root)
    pointer = json.loads(protected_bytes(root/"CURRENT.json"))
    manifest = json.loads(protected_bytes(root/("gen-"+pointer["generation_id"])/"manifest.json"))
    require(set(manifest["files"]) == {"report", "checkpoint", "status", "projection"}, "FOUR_ROLES_REQUIRED")
    cut_at = datetime.fromisoformat(manifest["as_of"].replace("Z", "+00:00"))
    forbidden = {manifest["files"][role]["payload_digest"] for role in ("report", "checkpoint")}
    measures, network, source_opens, decode_calls = [], [], [], []
    original_connect, original_decode = sqlite3.connect, persistence.decode_storage

    def no_network(*_args, **_kwargs):
        network.append("BLOCKED")
        raise GateFailure("PROVIDER_OR_NETWORK_CALLED")

    def no_source(database_arg, *args, **kwargs):
        require(str(database_arg).split("?", 1)[0] not in {str(database), database.as_uri()}, "SQLITE_OPENED_SOURCE")
        source_opens.append("PRIVATE_COPY_OR_MEMORY")
        return original_connect(database_arg, *args, **kwargs)

    def bounded_decode(value, *args, **kwargs):
        if isinstance(value, dict):
            require(value.get("logical_sha256") not in forbidden, "ORIGINAL_REPORT_OR_CHECKPOINT_DECODED")
        decode_calls.append("BOUNDED_STATUS_OR_SMALL_PAYLOAD")
        return original_decode(value, *args, **kwargs)

    def query(filters=None):
        begin = perf_counter()
        with Store(database, now=cut_at) as store:
            projection = Projection(store, filters)
            cut = projection.shadow
            page = shadow_rows(projection, "opportunities")
            scope = projection.funnel_scope
            require(cut["state"] == "COMMITTED_COHERENT_SHADOW", "PROJECTED_CUT_UNAVAILABLE",
                    {"state": cut["state"], "reason": cut["reason"], "error_class": cut.get("error_class"),
                     "elapsed_seconds": perf_counter()-begin, "filters": filters or {}})
            require(cut["pointer"] == pointer, "QUERY_CHANGED_COMMITTED_CUT")
            require(cut["verification_level"] == VERIFICATION_LEVEL, "FALSE_VERIFICATION_SCOPE")
            require(page.state == "AVAILABLE", "PLANNER_PAGE_UNAVAILABLE")
            require(cut["query_bytes"] < 4*1024**2, "QUERY_EXCEEDS_FOUR_MIB")
        elapsed = perf_counter()-begin
        measures.append({"kind": "projected_query", "filters": filters or {}, "elapsed_seconds": elapsed,
                         "query_bytes": cut["query_bytes"], "total": page.total, "rows": len(page.rows),
                         "funnel_groups_total": scope["total_groups"]})
        require(not store.errors and elapsed <= 1, "QUERY_EXCEEDS_ONE_SECOND_OR_SOURCE_REJECTED", measures[-1])
        return cut, page, scope

    with ExitStack() as guards:
        guards.enter_context(patch.dict(os.environ, {"POROTA_DYNAMIC_SHADOW_ROOT": str(root),
                                                    "POROTA_SHADOW_RUNTIME_ROOT": str(root)}))
        guards.enter_context(patch.object(socket.socket, "connect", no_network))
        guards.enter_context(patch.object(socket, "create_connection", no_network))
        guards.enter_context(patch.object(sqlite3, "connect", no_source))
        guards.enter_context(patch.object(persistence, "decode_storage", bounded_decode))
        with readonly_copy(database, validate=False, deadline=monotonic()+2) as copied:
            catalog = [tuple(row) for row in copied.execute("SELECT ticker,instrument_type,market,currency,settlement "
                                                          "FROM financial_instrument_catalog ORDER BY ticker")]
            observations = copied.execute("SELECT count(*) FROM ppi_intraday_points").fetchone()[0]
        require(len(catalog) == catalog_count and observations == observation_count, "NATIVE_POPULATION_INCOMPLETE")
        cut, first, _ = query()
        require(first.total == 2*catalog_count and len(first.rows) == 10, "PLANNER_DENOMINATOR_INCOMPLETE")
        require(cut["export_contract"]["verified_payloads"]["report"]["logical_bytes"] > 4*1024**2,
                "NATIVE_LARGE_REPORT_NOT_EXERCISED")
        _, second, _ = query({"offset": "10"})
        key = lambda row: (row["engine"], row["identity"])
        require(not {key(row) for row in first.rows} & {key(row) for row in second.rows}, "PAGES_OVERLAP")
        _, last, _ = query({"offset": str(first.total-10)})
        require(last.total == first.total and len(last.rows) == 10, "LAST_PAGE_UNREACHABLE")
        last_identity = catalog[-1]
        _, matched, _ = query({"q": last_identity[0], "currency": last_identity[3]})
        require(matched.total == 2 and all(row["symbol"] == last_identity[0] for row in matched.rows), "LAST_IDENTITY_SEARCH_UNREACHABLE")
        _, exact, _ = query({"identity": json.dumps(last_identity)})
        require(exact.total == 2 and all(tuple(json.loads(row["identity"])) == last_identity for row in exact.rows),
                "FULL_IDENTITY_QUERY_MISMATCH")
        _, injected, _ = query({"q": "' OR 1=1 --"})
        require(injected.total == 0 and not injected.rows, "QUERY_NOT_PARAMETERIZED")
        _, _, scope = query({"family": last_identity[1]})
        _, _, end_scope = query({"family": last_identity[1], "funnel_offset": str(scope["total_groups"]-10)})
        require(scope["total_groups"] >= 2*catalog_count and len(end_scope["groups"]) == 10,
                "COMPLETE_COHORT_POPULATION_UNREACHABLE")
        require(scope["selected"] == end_scope["selected"] and scope["counts"] == end_scope["counts"],
                "PAGING_CHANGED_SELECTED_AGGREGATES")
        chosen = end_scope["groups"][-1]
        _, _, exact_scope = query({"cohort": funnel_cohort_id(chosen)})
        require(exact_scope["total_groups"] == 1 and exact_scope["selected"] == chosen, "LAST_COHORT_UNREACHABLE")

        # Complete population proof is separate from the measured ten-row UI
        # queries. The index bytes already passed the canonical reader's wire
        # and custody checks; this audit opens those same bytes only in memory.
        record = manifest["files"]["projection"]
        raw = protected_bytes(root/("gen-"+pointer["generation_id"])/record["name"])
        require(hashlib.sha256(raw).hexdigest() == record["sha256"], "INDEX_CHANGED_AFTER_VERIFIED_QUERY")
        connection, _header = open_projection(raw)
        try:
            indexed = {tuple(row) for row in connection.execute("SELECT DISTINCT ticker,family,market,currency,settlement "
                                                              "FROM projection_rows WHERE dataset='planner'")}
            require(indexed == set(catalog), "INDEX_OMITTED_OR_CHANGED_FULL_IDENTITIES")
        finally:
            connection.close()
        for path, filters in (("/universo/discovery", {}),
                              ("/en-vivo/oportunidades", {"q": last_identity[0], "currency": last_identity[3]}),
                              ("/en-vivo", {"family": last_identity[1], "funnel_offset": str(scope["total_groups"]-10)})):
            begin = perf_counter()
            html, headers = build_page(path, filters, database, now=cut_at)
            elapsed = perf_counter()-begin
            measures.append({"kind": "complete_render", "path": path, "filters": filters,
                             "elapsed_seconds": elapsed, "html_bytes": len(html.encode()), "server_timing": headers["Server-Timing"]})
            require(elapsed <= 1 and len(html.encode()) < 4*1024**2, "RENDER_EXCEEDS_REQUEST_BUDGET", measures[-1])
            visible_scope = (f"de {scope['total_groups']}" in html if path == "/en-vivo" else VERIFICATION_LEVEL in html)
            require(visible_scope and "SOURCE_SNAPSHOT_REJECTED" not in html
                    and "Corte de lectura no disponible dentro del presupuesto" not in html,
                    "RENDER_DID_NOT_PRESENT_VERIFIED_CUT")
    after = custody_inventory(database, root)
    require(before == after, "SOURCE_OR_CUSTODY_MUTATED")
    require(not network, "NETWORK_ATTEMPTED")
    return {"schema": "rc6.dashboard-native-large-projection-proof.v1", "status": "GREEN",
        "as_of": datetime.now(timezone.utc).isoformat(), "source_cut": cut_at.isoformat(),
        "pointer": pointer, "generation_verification": VERIFICATION_LEVEL,
        "custody": cut["export_contract"]["custody"], "derivation": cut["export_contract"]["derivation"],
        "catalog_full_identities": len(catalog), "observations": observations,
        "planner_rows": first.total, "funnel_groups": scope["total_groups"],
        "complete_index_population_checked": True, "sampled_ui_queries": measures,
        "full_page_traversal_claimed": False, "native_report_logical_bytes": cut["export_contract"]["verified_payloads"]["report"]["logical_bytes"],
        "source_custody_inventory_unchanged": True, "source_inventory": before,
        "network_attempts": len(network), "provider_requests": 0,
        "source_sqlite_opens": 0, "private_or_memory_sqlite_opens": len(source_opens),
        "bounded_decode_calls": len(decode_calls), "original_report_checkpoint_decode_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog-count", type=int, default=12000)
    parser.add_argument("--observation-count", type=int, default=60000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output, source_root, source_database = args.output.resolve(), args.root.resolve(), args.database.resolve()
    if (output.is_relative_to(source_root) or output.is_relative_to(Path(str(source_root)+".authority"))
            or output in {Path(str(source_database)+suffix) for suffix in ("", "-wal", "-shm", "-journal")}
            or args.output.is_symlink() or output.exists() and (not output.is_file() or output.stat().st_nlink != 1)):
        parser.error("Receipt must be outside the database, sidecars and SHADOW custody")
    try:
        result = run(args.database, args.root, catalog_count=args.catalog_count,
                     observation_count=args.observation_count, receipt=args.output)
    except (GateFailure, ValueError, OSError, sqlite3.Error) as error:
        result = {"schema": "rc6.dashboard-native-large-projection-proof.v1", "status": "RED",
                  "error_class": type(error).__name__, "gate": str(error) if isinstance(error, GateFailure) else "NATIVE_READER_REJECTED",
                  "details": error.details if isinstance(error, GateFailure) else {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({key: result[key] for key in ("status", "schema", "gate") if key in result}))
    raise SystemExit(0 if result["status"] == "GREEN" else 1)

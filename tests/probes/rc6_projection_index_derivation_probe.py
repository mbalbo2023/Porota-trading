"""Repeat the independent native-producer index mutation on one exact archive.

The SQLite mutation is in memory only. It does not bypass sealed wire hashes,
custody, or external authentication and makes no financial-authority claim.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import socket
import sys
import tempfile
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect", choices=("RED", "GREEN"), required=True)
    args = parser.parse_args()
    args.source_index = args.source_index.resolve(strict=True)
    args.output = args.output.resolve()
    index = json.loads(args.source_index.read_bytes())
    root = Path(index["extracted_root"]).resolve(strict=True)
    expected = index["source_file_hashes"]
    assert not args.output.is_relative_to(root)
    before = {name: sha(root / name) for name in expected}
    assert before == expected, "ARCHIVE_SOURCE_MISMATCH"
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["PYTHONPATH"] = str(root)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    attempts = []

    def denied(*arguments, **keywords):
        attempts.append("NETWORK_FORBIDDEN")
        raise AssertionError("NETWORK_FORBIDDEN")

    socket.socket.connect = denied
    socket.create_connection = denied
    started = datetime.now(timezone.utc).isoformat()
    from tests.rc6_dashboard_native_fixture import native_fixture
    from rc6_shadow_runtime import persistence
    from rc6_shadow_runtime.projection import open_projection, logical_digest, query_projection
    with tempfile.TemporaryDirectory(prefix="rc6-projection-index-exact-") as directory:
        native = native_fixture(Path(directory), count=25)
        generation = native.root / ("gen-" + native.cut["pointer"]["generation_id"])
        member = generation / persistence.GENERATION_ROLES["projection"]
        original = member.read_bytes()
        connection, header = open_projection(original)
        try:
            prior_digest = logical_digest(connection, header)
            assert prior_digest[0] == native.cut["manifest"]["files"]["projection"]["payload_digest"]
            pages, _ = query_projection(connection, header, filters={"currency": "USD"},
                                        offset=0, limit=10, deadline=time.monotonic()+5)
            prior_total = pages["opportunities"]["total"]
            assert prior_total == 0, "POSITIVE_CONTROL_CURRENCY_SELECTION_CHANGED"
            connection.execute("PRAGMA query_only=OFF")
            changed = connection.execute("UPDATE projection_rows SET currency='USD' "
                                         "WHERE dataset='planner' AND ticker='T000'").rowcount
            assert changed == 2, "ORIGINAL_MUTATION_CARDINALITY_CHANGED"
            error = None
            try:
                after_digest = logical_digest(connection, header)
            except ValueError as exception:
                error = str(exception)
                after_digest = None
            changed_pages, _ = query_projection(connection, header, filters={"currency": "USD"},
                                                offset=0, limit=10, deadline=time.monotonic()+5)
            changed_page = changed_pages["opportunities"]
            native_labels = [row["identity"][3] for row in changed_page["rows"]]
            assert changed_page["total"] == 2 and native_labels == ["ARS", "ARS"]
            unchanged_member = member.read_bytes() == original
            guard_green = error is not None and "DERIVATION_MISMATCH" in error
            original_red = error is None and after_digest == prior_digest
            observed = "GREEN" if guard_green else "RED" if original_red else "OTHER"
            probe = {"original_currency_USD_total": prior_total,
                     "changed_currency_USD_total": changed_page["total"],
                     "returned_native_currency_labels": native_labels,
                     "logical_digest_before": prior_digest, "logical_digest_after": after_digest,
                     "derivation_guard_error": error, "original_member_unchanged": unchanged_member,
                     "original_member_sha256": hashlib.sha256(original).hexdigest(),
                     "logical_encoding": header["logical_encoding"], "as_of": header["as_of"],
                     "dataset_counts": header["dataset_counts"]}
        finally:
            connection.close()
    repository_module_names = {name[:-3].replace("/", ".").removesuffix(".__init__")
                               for name in expected if name.endswith(".py")}
    imported, alien, all_origins = {}, {}, {}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if not path.is_file():
            continue
        all_origins[name] = str(path)
        if path.is_relative_to(root):
            relative = str(path.relative_to(root))
            imported[name] = {"path": relative, "sha256": sha(path)}
            assert relative in expected and imported[name]["sha256"] == expected[relative], name
        elif name in repository_module_names or str(path).startswith("/workspace/porota_rc6_"):
            alien[name] = str(path)
    after = {name: sha(root / name) for name in expected}
    completed = (observed == args.expect and unchanged_member and before == after
                 and not alien and not attempts)
    receipt = {"schema": "rc6.independent-projection-index-exact-archive-receipt.v1",
               "observed_guard_status": observed, "expected_guard_status": args.expect,
               "probe_completed_as_expected": completed,
               "source_sha": index["source_sha"], "source_tree": index["source_tree"],
               "archive_sha256": index["archive_sha256"], "extracted_source": str(root),
               "source_index_sha256": sha(args.source_index), "probe_sha256": sha(Path(__file__)),
               "source_files_before": before, "source_files_after": after,
               "source_unchanged": before == after,
               "imported_repo_modules": imported, "alien_repo_modules": alien,
               "all_imported_file_origins": all_origins,
               "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
               "network_attempts": attempts, "real_orders_sent": 0, "economic_edge": "NO_DEMOSTRADO",
               "scope": "NATIVE_PRODUCER_REPORT_THEN_TEMPORARY_IN_MEMORY_INDEX_MUTATION; NOT_A_SEALED_CUSTODY_BYPASS",
               "note": "Published-member wire hashes and custody reject rewriting SQLite bytes. This probe tests the standalone logical derivation digest only.",
               **probe}
    args.output.write_text(json.dumps(receipt, sort_keys=True, indent=2)+"\n")
    print(json.dumps({"observed_guard_status": observed, "probe_completed_as_expected": completed,
                      "source_sha": index["source_sha"], "derivation_guard_error": error}))
    return 0 if completed else 1


if __name__ == "__main__":
    raise SystemExit(main())

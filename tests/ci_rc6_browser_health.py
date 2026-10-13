"""Separate offline live SHADOW health gate through the locked stdio product.

Never uses or changes the historical BIG database. The actual supervisor
spawns only dynamic_shadow; the canonical health publisher owns its metadata.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.rc6_browser_ipc import GateFailure, ProductClient, imported_source, output_guard, require
from tests.rc6_browser_coverage import observe_health


def run(output, *, product_python, index, python_version):
    output_guard(output)
    client = ProductClient(product_python, index=index, python_version=python_version, require_complete_index=True)
    with client:
        source = client.request("initialize", mode="HEALTH_LIVE")
        observed = observe_health(client, require_live=True)
        native = observed["native_result"]
        require(native["generation_id"] == source["pointer"]["generation_id"]
                and datetime.fromisoformat(native["generation_as_of"]) == datetime.fromisoformat(source["source_cut"]),
                "HEALTH_CONSUMED_A_DIFFERENT_NATIVE_CUT")
        require(native["provider_capacity_open"] == "NO_VERIFICADO" and native["real_orders_sent"] == 0
                and native["real_routes"] == "NOT_CALLED", "HEALTH_GRANTED_UNPUBLISHED_TRADE_AUTHORITY")
    fixture = client.finish_receipt["offline_live_health_fixture"]
    require(fixture["producer_reaped"] and fixture["producer_exitcode"] == 0
            and not fixture["producer_cleanup_forced"],
            "NATIVE_HEALTH_PRODUCER_CLEANUP_FAILED")
    require(fixture["health_observation"]["before"] == fixture["health_observation"]["after"],
            "HEALTH_CONSUMER_MUTATED_NATIVE_SOURCE")
    closure, unexpected = imported_source(client.root, client.source_before)
    require(not unexpected and all(row["matches_archived_blob"] for row in closure), "DRIVER_SOURCE_IMPORT_PROOF_FAILED")
    require(not any(name.startswith(("rc6_trader_dashboard", "rc6_shadow_runtime", "be_paper_engine",
        "rc6_paper_family_lifecycle")) for name in sys.modules), "DRIVER_EXECUTED_PRODUCT_MODULE")
    return {"schema": "rc6.browser-offline-live-health-proof.v1", "status": "GREEN",
        "scope": "SEPARATE_SMALL_LIVE_NATIVE_HEALTH_NOT_BIG_RENDER",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "health_observation": observed,
        "product_environment": client.product_environment, "driver_environment": client.driver_environment,
        "driver_imported_source": closure,
        "tracked_source_hashes_and_modes_unchanged": client.source_before == client.source_after,
        "product_proof": client.finish_receipt, "source_cut": source["source_cut"], "pointer": source["pointer"],
        "provider_capacity_open": "NO_VERIFICADO", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "runtime_live_after_fixture_cleanup": False, "big_render_acceptance_claim": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-python", type=Path, required=True)
    parser.add_argument("--product-python-version", choices=("3.11", "3.12"), required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output_guard(args.output)
    except GateFailure as error:
        parser.error(str(error))
    try:
        receipt = run(args.output, product_python=args.product_python, index=args.index,
                      python_version=args.product_python_version)
    except Exception as error:
        receipt = {"schema": "rc6.browser-offline-live-health-proof.v1", "status": "RED",
            "scope": "SEPARATE_SMALL_LIVE_NATIVE_HEALTH_NOT_BIG_RENDER",
            "gate": str(error) if isinstance(error, GateFailure) else "NATIVE_HEALTH_REJECTED",
            "error_class": type(error).__name__, "details": error.details if isinstance(error, GateFailure) else {},
            "big_render_acceptance_claim": False}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "health-gate.json").write_text(json.dumps(receipt, sort_keys=True, indent=2)+"\n")
    print(json.dumps({"status": receipt["status"], "scope": receipt["scope"]}))
    raise SystemExit(0 if receipt["status"] == "GREEN" else 1)

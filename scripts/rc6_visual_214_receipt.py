#!/usr/bin/env python3
"""Verify the exact Promote #214 artifact and keep the visual audit fields."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path

from scripts.porota_actions_host import decode_receipt, require, validate_runtime_receipt


PROMOTE_RUN_ID = 45921311817
PROMOTE_ATTEMPT = 1
RUNTIME_SHA256 = "127019529410a79a8325645f7921bed69c156f93a1521dd7633c3322673cd19f"
DEPLOYMENT_SHA = "3236a969faf286032dc56502423471198e6d8dd1"
EXACT_RELEASE = {
    "source_sha": "eba76703be86e6a6b1406db36e5cb4cb87b21330",
    "source_tree": "5ac31c4b9cf2c16f477c08a6b5b3424f644a2667",
    "image_id": "sha256:274b143fd323ab0b79aff90890f54a7f5ce0ed84c3323fedd385ce85f28e83d2",
}
EXACT_ROLES = {
    "observer": "f6440b9039b39f4d3a8b0047d1140b64d7eccadd68e00174fc2f6b2198662a09",
    "dashboard": "0d4e1b7b56bd3621e5448a652cc7e45a1b1652c92e612fdc9e3f9abcf298c774",
}


def sanitize(raw):
    require(len(raw) <= 131072 and hashlib.sha256(raw).hexdigest() == RUNTIME_SHA256,
            "PROMOTE_214_ARTIFACT_RECEIPT_HASH_MISMATCH")
    document = decode_receipt(raw)
    validate_runtime_receipt(document, EXACT_RELEASE, 0)
    require(document.get("deployment_sha") == DEPLOYMENT_SHA and
            document.get("attempt") == f"{PROMOTE_RUN_ID}-{PROMOTE_ATTEMPT}" and
            document.get("market_provider") == "IOL_REST" and document.get("roles") == EXACT_ROLES and
            document["health"].get("ppi_auth") == "NOT_ATTEMPTED", "PROMOTE_214_RUNTIME_IDENTITY_MISMATCH")
    fields = ("schema", "state", "phase", "source_sha", "source_tree", "image_id", "attempt", "deployment_sha",
              "market_provider", "operating_mode", "execution_mode", "real_orders_sent", "ppi_watch_touched",
              "boca_touched", "roles")
    return {**{key: document[key] for key in fields},
            "health": {key: document["health"][key] for key in ("dashboard", "observer", "orders_guard", "ppi_auth")},
            "cleanup": {"state": document["cleanup"]["state"]},
            "source_receipt_sha256": RUNTIME_SHA256, "source_promote_run_id": PROMOTE_RUN_ID,
            "source_promote_attempt": PROMOTE_ATTEMPT}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--downloaded-receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.downloaded_receipt.open("rb") as source:
        document = sanitize(source.read(131073))
    args.output.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

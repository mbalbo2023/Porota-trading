#!/usr/bin/env python3
"""Verify the exact Promote #216 artifact and keep the visual audit fields."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path

from scripts.porota_actions_host import decode_receipt, require, validate_runtime_receipt


PROMOTE_RUN_ID = 46014207358
PROMOTE_ATTEMPT = 1
RUNTIME_SHA256 = "440312343c3c1a1a10dc2d4e12c2837195e375acf5959971e31172576fce9344"
DEPLOYMENT_SHA = "d35a39b45170a5af85e07443711eac69dde62ace"
EXACT_RELEASE = {
    "source_sha": "6da112dcc58268ac5ea3f93e5dddf4f87dabd141",
    "source_tree": "8ea511a9437d0a950df10838d7cc7a7118adcf5a",
    "image_id": "sha256:04e3dc288079358b93b2d0c3f77f6da1de63f53db304613695cb2a5c5173f20e",
}
EXACT_ROLES = {
    "observer": "ab4bb10ba9f9223c5c75d5c7aaeab671f51804ea2e02b19165d8f0da04575b04",
    "dashboard": "7aa7462e94263e2609f36245dd2f2dfe1a0a73884a9512a00a79203ded650316",
}


def sanitize(raw):
    require(len(raw) <= 131072 and hashlib.sha256(raw).hexdigest() == RUNTIME_SHA256,
            "PROMOTE_216_ARTIFACT_RECEIPT_HASH_MISMATCH")
    document = decode_receipt(raw)
    validate_runtime_receipt(document, EXACT_RELEASE, 0)
    require(document.get("deployment_sha") == DEPLOYMENT_SHA and
            document.get("attempt") == f"{PROMOTE_RUN_ID}-{PROMOTE_ATTEMPT}" and
            document.get("market_provider") == "IOL_REST" and document.get("roles") == EXACT_ROLES and
            document["health"].get("ppi_auth") == "NOT_ATTEMPTED", "PROMOTE_216_RUNTIME_IDENTITY_MISMATCH")
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

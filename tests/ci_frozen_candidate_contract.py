"""Fail-closed validator shared by Predeploy V2 and its contract tests."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile


class FrozenContractError(RuntimeError):
    pass


def validate_frozen_candidate(
    *,
    frozen_path: Path,
    image_path: Path,
    manifest_path: Path,
    candidate_sha: str,
    tree_sha: str,
) -> dict:
    if not frozen_path.is_file():
        raise FrozenContractError("FROZEN_FILE_MISSING")
    if not manifest_path.is_file():
        raise FrozenContractError("MANIFEST_FILE_MISSING")
    try:
        payload = json.loads(frozen_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenContractError("MANIFEST_OR_METADATA_CORRUPT") from exc
    if not isinstance(payload, dict) or not isinstance(manifest, dict):
        raise FrozenContractError("MANIFEST_OR_METADATA_NOT_OBJECT")
    if payload.get("candidate_sha") != candidate_sha:
        raise FrozenContractError("CANDIDATE_SHA_MISMATCH")
    if payload.get("candidate_tree_sha") != tree_sha:
        raise FrozenContractError("CANDIDATE_TREE_MISMATCH")
    if not image_path.is_file():
        raise FrozenContractError("IMAGE_TAR_MISSING")
    if payload.get("image_tar_sha256") != sha256(image_path.read_bytes()).hexdigest():
        raise FrozenContractError("IMAGE_DIGEST_MISMATCH")
    try:
        image_size_bytes = int(payload.get("image_size_bytes", 0))
    except (TypeError, ValueError) as exc:
        raise FrozenContractError("IMAGE_SIZE_INVALID") from exc
    if image_size_bytes <= 0:
        raise FrozenContractError("IMAGE_SIZE_INVALID")
    if payload.get("paper_mode_required") != "PRODUCTION_PAPER":
        raise FrozenContractError("NON_PAPER_MODE")
    if payload.get("real_orders_sent_required") != 0:
        raise FrozenContractError("REAL_ORDERS_NONZERO")
    if payload.get("real_order_capability_required") != "BLOCKED":
        raise FrozenContractError("REAL_ROUTE_ENABLED")
    if payload.get("build_once") is not True:
        raise FrozenContractError("SECOND_BUILD_ALLOWED")
    return payload


def run_negative_fixtures(evidence_path: Path) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="porota-frozen-negative-") as temp:
        root = Path(temp)
        image = root / "image.tar.gz"
        manifest = root / "manifest.json"
        frozen = root / "frozen.json"
        image.write_bytes(b"synthetic immutable image fixture")
        manifest.write_text('{"schema_version":1}\n', encoding="utf-8")
        baseline = {
            "candidate_sha": "a" * 40,
            "candidate_tree_sha": "b" * 40,
            "image_tar_sha256": sha256(image.read_bytes()).hexdigest(),
            "image_size_bytes": 123456789,
            "paper_mode_required": "PRODUCTION_PAPER",
            "real_orders_sent_required": 0,
            "real_order_capability_required": "BLOCKED",
            "build_once": True,
        }

        def write(payload: dict) -> None:
            frozen.write_text(json.dumps(payload), encoding="utf-8")

        fixtures = {}
        write(baseline)
        frozen.unlink()
        fixtures["MISSING_FILE"] = lambda: None

        def corrupt_manifest() -> None:
            write(baseline)
            manifest.write_text("{not-json", encoding="utf-8")

        fixtures["CORRUPT_MANIFEST"] = corrupt_manifest
        fixtures["WRONG_SHA"] = lambda: write({**baseline, "candidate_sha": "0" * 40})
        fixtures["WRONG_DIGEST"] = lambda: write({**baseline, "image_tar_sha256": "0" * 64})
        fixtures["INVALID_IMAGE_SIZE"] = lambda: write({**baseline, "image_size_bytes": 0})
        fixtures["NON_PAPER_MODE"] = lambda: write({**baseline, "paper_mode_required": "REAL"})
        fixtures["REAL_ROUTE_ENABLED"] = lambda: write(
            {**baseline, "real_order_capability_required": "ENABLED"}
        )

        evidence = {}
        for name, prepare in fixtures.items():
            manifest.write_text('{"schema_version":1}\n', encoding="utf-8")
            if name != "MISSING_FILE":
                prepare()
            try:
                validate_frozen_candidate(
                    frozen_path=frozen,
                    image_path=image,
                    manifest_path=manifest,
                    candidate_sha=baseline["candidate_sha"],
                    tree_sha=baseline["candidate_tree_sha"],
                )
            except FrozenContractError as exc:
                evidence[name] = f"FAIL_CLOSED:{exc}"
            else:
                raise AssertionError(f"{name}_DID_NOT_FAIL_CLOSED")
            finally:
                write(baseline)
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    negative = sub.add_parser("negative")
    negative.add_argument("--evidence", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--frozen", type=Path, required=True)
    validate.add_argument("--image", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--candidate-sha", required=True)
    validate.add_argument("--tree-sha", required=True)
    args = parser.parse_args()
    if args.command == "negative":
        evidence = run_negative_fixtures(args.evidence)
        for name in sorted(evidence):
            print(f"POROTA_NEGATIVE_FIXTURE={name}|FAIL_CLOSED")
    else:
        validate_frozen_candidate(
            frozen_path=args.frozen,
            image_path=args.image,
            manifest_path=args.manifest,
            candidate_sha=args.candidate_sha,
            tree_sha=args.tree_sha,
        )
        print("POROTA_FROZEN_CONTRACT=GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

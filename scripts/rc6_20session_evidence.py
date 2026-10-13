#!/usr/bin/env python3
"""Offline private cohort export/recompute; no default DB or network access."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_audit_evidence import EvidenceError, Limits, export_package, verify_package
from rc6_audit_evidence.package import _safe_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="READ_ONLY export of an already authorized offline PAPER snapshot")
    export.add_argument("--source", required=True)
    export.add_argument("--output", required=True, help="New private directory outside a Git repository")
    export.add_argument("--session", action="append", required=True, help="Explicit ART date; exactly 20 unique sessions")
    export.add_argument("--pseudonym-seed-file", required=True, help="External high-entropy seed; never uploaded or committed")
    recompute = commands.add_parser("recompute", help="Validate hashes/counts and recompute every closed position from fills")
    recompute.add_argument("--package", required=True)
    recompute.add_argument("--manifest-sha256", required=True, help="Independently retained digest from the source owner")
    for command in (export, recompute):
        command.add_argument("--max-positions", type=int, default=2000)
        command.add_argument("--max-fills", type=int, default=10000)
        command.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
        command.add_argument("--max-seconds", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        limits = Limits(positions=args.max_positions, fills=args.max_fills, bytes=args.max_bytes, seconds=args.max_seconds)
        if args.command == "export":
            key_path = _safe_file(args.pseudonym_seed_file)
            if key_path.stat().st_size > 4096:
                raise EvidenceError("EXTERNAL_PSEUDONYM_SEED_REQUIRED")
            if key_path == Path(args.source).absolute() or key_path.is_relative_to(Path(args.output).absolute()):
                raise EvidenceError("EXTERNAL_PSEUDONYM_SEED_REQUIRED")
            if any((p / ".git").exists() for p in key_path.parents):
                raise EvidenceError("EXTERNAL_PSEUDONYM_SEED_REQUIRED")
            if key_path.stat().st_mode & 0o077:
                raise EvidenceError("PRIVATE_SEED_PERMISSIONS_REQUIRED")
            key = key_path.read_bytes()
            report = export_package(args.source, args.output, sessions=args.session, pseudonym_key=key, limits=limits)
        else:
            report = verify_package(args.package, expected_manifest_sha256=args.manifest_sha256, limits=limits)
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (EvidenceError, OSError, KeyError, TypeError, ValueError):
        # Error values, filesystem paths and source SQL are deliberately absent.
        error = sys.exc_info()[1]
        reason = str(error) if isinstance(error, EvidenceError) else "EVIDENCE_FAILED_CLOSED"
        print(json.dumps({"status": "EXTERNAL_EVIDENCE_PENDING", "reason": reason,
                          "real_orders_sent": 0, "real_routes": "NOT_CALLED"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

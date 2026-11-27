"""Run the pinned COLD successor checks through the existing Actions transport."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = {
    "source_sha": "eba76703be86e6a6b1406db36e5cb4cb87b21330",
    "source_tree": "5ac31c4b9cf2c16f477c08a6b5b3424f644a2667",
    "image_id": "sha256:274b143fd323ab0b79aff90890f54a7f5ce0ed84c3323fedd385ce85f28e83d2",
    "attempt": "45921311817-1",
    "market_provider": "IOL_REST",
}
SCHEMA = "rc6.rootless.cold-recovery.v1"
MODULES = (
    "scripts/porota_rootless_host.py",
    "scripts/rc6_cold2_quarantine.py",
    "scripts/rc6_cold2_successor.py",
    "ak_byma_calendar.py",
    "ak_byma_t1_source_clock.py",
    "co_market_sessions_hf6.py",
)
REMOTE = '''
import hashlib
import json
import os
from pathlib import Path
import sys

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def run():
    uid = os.getuid()
    require(uid > 0 and uid == os.geteuid(), "COLD_ROOTLESS_IDENTITY_REQUIRED")
    source = Path.home() / ".local/state/porota-rc6/releases" / product["source_sha"] / "source"
    require(source.is_dir() and source.stat().st_uid == uid and
            all(not path.is_symlink() for path in (source, *source.parents)),
            "COLD_RELEASE_SOURCE_DIRECTORY_UNVERIFIED")
    for relative, expected in checksums.items():
        path = source / relative
        require(path.is_file() and path.stat().st_uid == uid and path.stat().st_nlink == 1 and
                all(not part.is_symlink() for part in (path, *path.parents)) and
                hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                "COLD_INSTALLED_DEPENDENCY_HASH_MISMATCH")
    sys.path.insert(0, str(source))
    scope = {"__file__": str(source / "scripts/rc6_rootless_cold_recovery.py"),
             "__name__": "rc6_installed_cold_recovery"}
    exec(compile(body, scope["__file__"], "exec"), scope)
    require(scope["frozen_coordinates"]() == product, "COLD_PROMOTION_COORDINATES_MISMATCH")
    return scope["operate"](operation, Path("/opt/porota-trading"))

try:
    report = run()
except (OSError, ValueError, RuntimeError, KeyError) as exc:
    reason = str(exc)
    report = {"schema": schema, **product, "operation": operation, "state": "BLOQUEADO",
              "failure_signature": reason if reason.isascii() and reason.replace("_", "").isalnum()
                 and reason.isupper() and len(reason) <= 96 else type(exc).__name__}
report["recovery_source_sha256"] = hashlib.sha256(body.encode()).hexdigest()
print(json.dumps(report, sort_keys=True, allow_nan=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("cold_inspect", "cold_recover", "cold_resume"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    body = (ROOT / "scripts/rc6_rootless_cold_recovery.py").read_text(encoding="utf-8")
    checksums = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in MODULES}
    run_source = f"product = {PRODUCT!r}\nchecksums = {checksums!r}\nbody = {body!r}\n"
    run_source += f"operation = {args.operation!r}\nschema = {SCHEMA!r}\n" + REMOTE
    report = {"schema": SCHEMA, **PRODUCT, "operation": args.operation, "state": "BLOQUEADO",
              "failure_signature": "COLD_ACTIONS_TRANSPORT_UNAVAILABLE"}
    expected_state = {"cold_inspect": "READ_ONLY_OBSERVED",
                      "cold_recover": "COLD_ARCHIVE_COMPLETE",
                      "cold_resume": "RUNTIME_RESTORED"}[args.operation]
    try:
        with connection(os.environ) as (ssh, _, _):
            response = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"],
                               data=run_source.encode(), timeout=760)
        observed = decode_receipt(response.stdout)
        expected = {**PRODUCT, "schema": SCHEMA, "operation": args.operation,
                    "recovery_source_sha256": hashlib.sha256(body.encode()).hexdigest()}
        mismatches = [key for key, value in expected.items() if observed.get(key) != value]
        if response.returncode != 0 or mismatches:
            code = observed.get("failure_signature", "")
            report.update({"failure_signature": "COLD_ACTIONS_RECEIPT_UNVERIFIED",
                           "transport_exit_code": response.returncode, "receipt_mismatches": mismatches,
                           "remote_failure_signature": code if isinstance(code, str)
                           and re.fullmatch(r"[A-Za-z0-9_]{1,96}", code) else None})
            return 1
        report = observed
        if report.get("state") == expected_state:
            require(type(report.get("real_orders_sent")) is int and report["real_orders_sent"] == 0
                    and report.get("ppi_watch_touched") is False and report.get("boca_touched") is False,
                    "COLD_ACTIONS_PAPER_GUARD_UNVERIFIED")
            if args.operation == "cold_recover":
                require(report.get("archive_complete") is True and report.get("cold_queue_failed") is False
                        and report.get("restoration") == "RESTORED_ORIGINAL_RUNTIME",
                        "COLD_ACTIONS_ARCHIVE_RESTORE_UNVERIFIED")
            return 0
        return 1
    except (OSError, ValueError) as exc:
        code = str(exc)
        if code.startswith("COLD_ACTIONS_") and code.replace("_", "").isalnum():
            report.update({"state": "BLOQUEADO", "failure_signature": code})
        return 1
    finally:
        args.output.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

"""Run scoped native guards against an entire exact Git archive, without overlays."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("nodes", nargs="+")
    args = parser.parse_args()
    source, fixture = args.source_root.absolute(), args.fixture_root.absolute()
    if (source.resolve() != source or fixture.exists() or fixture.is_relative_to(source)
            or args.junit.absolute().is_relative_to(source) or args.receipt.absolute().is_relative_to(source)):
        raise ValueError("EXACT_SOURCE_AND_NEW_PRIVATE_FIXTURE_REQUIRED")
    if os.getenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1" or os.getenv("PYTHONDONTWRITEBYTECODE") != "1":
        raise ValueError("LITERAL_NATIVE_PYTEST_ENV_REQUIRED")
    helpers = runpy.run_path(str(source/"tests/probes/rc6_budget_primary_custody_probe.py"))
    before = helpers["verify_archive"](source, args.source_repo, args.source_sha)
    runner = "tests/probes/rc6_budget_source_custody_pytest_runner.py"
    if hashlib.sha256(helpers["raw"](Path(__file__))).hexdigest() != before[runner]["sha256"]:
        raise ValueError("RUNNER_NOT_BOUND_TO_SOURCE")
    fixture.mkdir(mode=0o700, parents=True)
    scratch = fixture/"scratch"
    scratch.mkdir(mode=0o700)
    os.environ["TMPDIR"] = str(scratch)
    for key in list(os.environ):
        if key.startswith("POROTA_SQLITE_SCRATCH_"):
            del os.environ[key]
    tempfile.tempdir = str(scratch)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    os.chdir(source)
    import pytest
    from scripts.porota_dependency_repro_audit import installed_distribution_audit
    policy_path = source/"ops/policy/rc6-supply-chain-v1.json"
    environment = installed_distribution_audit(json.loads(helpers["raw"](policy_path)))
    if environment["status"] != "GREEN" or environment["installed_total"] != 157:
        raise ValueError("FROZEN_157_EXACT_NATIVE_VERSIONS_REQUIRED")
    network, sqlite_opens = [], []
    def blocked_dns(*a, **kw):
        network.append("DNS")
        raise AssertionError("NETWORK_FORBIDDEN")
    for method in ("connect", "connect_ex", "sendto", "sendmsg"):
        if not hasattr(socket.socket, method):
            continue
        original = getattr(socket.socket, method)
        def guarded(self, *a, _method=method, _original=original, **kw):
            if self.family in {socket.AF_INET, socket.AF_INET6}:
                network.append(_method)
                raise AssertionError("NETWORK_FORBIDDEN")
            return _original(self, *a, **kw)
        setattr(socket.socket, method, guarded)
    socket.getaddrinfo = blocked_dns
    def audit(event, values):
        if event == "sqlite3.connect":
            from urllib.parse import unquote
            value = str(values[0])
            if value == ":memory:":
                sqlite_opens.append("IN_MEMORY")
                return
            path = Path(unquote(value.split("?", 1)[0].removeprefix("file:"))).absolute()
            if not path.is_relative_to(fixture):
                raise AssertionError("SQLITE_OUTSIDE_PRIVATE_FIXTURE_FORBIDDEN")
            sqlite_opens.append(str(path.relative_to(fixture)))
    sys.addaudithook(audit)
    command = [*args.nodes, "-q", "--junitxml="+str(args.junit.absolute()), "--basetemp="+str(fixture/"pytest")]
    exit_code = pytest.main(command)
    after = helpers["verify_archive"](source, args.source_repo, args.source_sha)
    imports, alien = [], []
    roots = {path.split("/", 1)[0].removesuffix(".py") for path in before}
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename is None or name.split(".", 1)[0] not in roots:
            continue
        path = Path(filename).absolute()
        if not path.is_relative_to(source):
            alien.append({"module": name, "path": str(path)})
        else:
            relative = str(path.relative_to(source))
            imports.append({"module": name, "path": relative, "sha256": before[relative]["sha256"]})
    junit_bytes = helpers["raw"](args.junit)
    suites = list(ET.fromstring(junit_bytes))
    cases = [case for suite in suites for case in suite.findall("testcase")]
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    green = exit_code == 0 and not (failures or errors or skipped or network or alien) and before == after
    receipt = {"schema": "rc6.budget-custody-native-pytest-receipt.v1", "classification": "NATIVE_SCOPED_GREEN" if green else "NATIVE_SCOPED_NOT_GREEN",
        "phase": "INTERMEDIATE_EXACT_COMPLETE_OWN_GIT_SOURCE_NOT_FINAL_ARTIFACT", "source_sha": args.source_sha,
        "source_tree": helpers["git"](args.source_repo, "rev-parse", args.source_sha+"^{tree}"),
        "source_files": len(before), "source_inventory_sha256": helpers["canonical_digest"](before),
        "all_git_blob_bytes_modes_verified": True, "source_unchanged": before == after,
        "environment_metadata_closure": environment, "policy_sha256": hashlib.sha256(helpers["raw"](policy_path)).hexdigest(),
        "python": sys.version, "executable": sys.executable, "command": command,
        "junit": {"path": str(args.junit), "sha256": hashlib.sha256(junit_bytes).hexdigest(), "bytes": len(junit_bytes),
            "tests": len(cases), "failures": failures, "errors": errors, "skipped": skipped,
            "cases": [{"classname": c.attrib["classname"], "name": c.attrib["name"], "seconds": c.attrib.get("time"),
                "status": "FAIL" if c.find("failure") is not None else "ERROR" if c.find("error") is not None else "SKIP" if c.find("skipped") is not None else "PASS"} for c in cases]},
        "imports": imports, "alien_imports": alien, "provider_attempts": network, "sqlite_opens": sqlite_opens,
        "pytest_exit_code": exit_code, "real_routes": "NOT_CALLED", "real_orders_sent": 0,
        "artifact_validated": False, "deployed": False, "runtime_validated": False,
        "limits": ["Synthetic native PAPER fixtures and SDK fake wire; no provider timing or live contract claim.",
            "This is one scoped execution, not a sum of previous raw JUnit runs or final governed acceptance."]}
    args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2)+"\n")
    print(json.dumps({"classification": receipt["classification"], "receipt": str(args.receipt),
        "receipt_sha256": hashlib.sha256(args.receipt.read_bytes()).hexdigest(), "source_sha": args.source_sha,
        "cases": len(cases), "failures": failures, "errors": errors, "skipped": skipped,
        "source_unchanged": before == after, "provider_attempts": len(network), "alien_imports": len(alien)}))
    return 0 if green else 1


if __name__ == "__main__":
    raise SystemExit(main())

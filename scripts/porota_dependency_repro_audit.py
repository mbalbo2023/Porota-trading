#!/usr/bin/env python3
"""Audit Action commits and hash-locked Python inputs, with explicit boundaries."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys

EXACT_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^,;\s]+(?:\s*;.*)?$")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")
FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.MULTILINE)
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)


def normalized(name):
    return re.sub(r"[-_.]+", "-", name.lower())


def requirement_rows(text):
    logical, current = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current += (" " if current else "") + line.removesuffix("\\").strip()
        if line.endswith("\\"):
            continue
        logical.append(current)
        current = ""
    if current:
        # A final continuation is malformed pip input, never an exact lock row.
        logical.append(current + " \\")
    rows = []
    for line in logical:
        requirement = line.split(" --hash=", 1)[0].strip()
        hashes = HASH_RE.findall(line)
        remainder = HASH_RE.sub("", line[len(requirement):]).strip()
        row = {"requirement": requirement,
               "exact_pin": bool(EXACT_RE.fullmatch(requirement)) and not remainder}
        if hashes:
            row["hashes"] = hashes
        rows.append(row)
    return rows


def current_platform_identity():
    libc_name, libc_version = platform.libc_ver()
    return {"os": sys.platform, "architecture": platform.machine(),
            "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
            "libc_name": libc_name, "libc_version": libc_version}


def platform_supported(current, spec):
    def version(value):
        if not isinstance(value, str) or re.fullmatch(r"[0-9]+\.[0-9]+", value) is None:
            return None
        return tuple(map(int, value.split(".")))
    observed, minimum = version(current.get("libc_version")), version(spec.get("glibc_minimum"))
    return (current.get("os") == spec.get("os")
            and current.get("architecture") == spec.get("architecture")
            and current.get("python_minor") in spec.get("python_minors", [])
            and current.get("libc_name") == "glibc"
            and observed is not None and minimum is not None and observed >= minimum)


def installed_distribution_audit(policy, installed=None):
    """Check the installed closure, rather than equating a freeze with a lock."""
    expected = {normalized(row["name"]): row["version"]
                for row in policy.get("packages", []) + policy.get("build_tools", [])}
    if installed is None:
        rows = [(distribution.metadata["Name"], distribution.version)
                for distribution in metadata.distributions()]
    else:
        rows = list(installed.items())
    observed, duplicates = {}, set()
    for name, version in rows:
        key = normalized(name)
        if key in observed:
            duplicates.add(key)
        observed[key] = version
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    versions = sorted(name for name in set(expected) & set(observed) if expected[name] != observed[name])
    return {"schema": "rc6.installed-distribution-closure.v1",
            "status": "GREEN" if expected and not missing and not unexpected and not versions and not duplicates else "REPRODUCIBILITY_GAP",
            "expected_total": len(expected), "installed_total": len(rows),
            "installed_unique_total": len(observed), "duplicate_names": sorted(duplicates),
            "missing": missing, "unexpected": unexpected, "version_mismatch": versions}


def _hash_gaps(rows, packages):
    known = {normalized(row["name"]): row for row in packages}
    gaps, names = [], set()
    for row in rows:
        requirement = row["requirement"]
        if not row["exact_pin"]:
            gaps.append(requirement)
            continue
        name, version = requirement.split("==", 1)
        name = normalized(name)
        approved = known.get(name)
        permitted = {entry["sha256"] for entry in (approved or {}).get("distributions", [])}
        observed = row.get("hashes", [])
        if (name in names or not approved or approved["version"] != version
                or not observed or len(observed) != len(set(observed))
                or set(observed) != permitted or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in permitted)):
            gaps.append(requirement)
        names.add(name)
    gaps.extend("POLICY_ONLY:" + name for name in sorted(set(known) - names))
    return gaps


def audit(requirements_text, lock_text, dockerfile_text, *, supply_chain_policy=None,
          build_lock_text="", workflow_texts=None, current_platform=None):
    source_rows, lock_rows = requirement_rows(requirements_text), requirement_rows(lock_text)
    build_rows = requirement_rows(build_lock_text)
    policy = supply_chain_policy or {}
    non_exact = [row["requirement"] for row in lock_rows if not row["exact_pin"]]
    hash_gaps = _hash_gaps(lock_rows, policy.get("packages", []))
    build_gaps = _hash_gaps(build_rows, policy.get("build_tools", []))
    bases = FROM_RE.findall(dockerfile_text)
    digest_pinned = bool(bases) and all(re.search(r"@sha256:[0-9a-f]{64}$", base) for base in bases)
    docker = "\n".join(line for line in dockerfile_text.splitlines() if not line.lstrip().startswith("#"))
    docker = docker.replace("\\\n", " ")
    commands = re.findall(r"^RUN\s+([^\n]+)", docker, re.MULTILINE)
    lock_commands = [command for command in commands if "-r requirements.lock.txt" in command]
    build_commands = [command for command in commands if "-r requirements.build.lock.txt" in command]
    uses_lock = bool(lock_commands)
    hash_install = bool(lock_commands) and all("--require-hashes" in command for command in lock_commands)
    build_hash_install = bool(build_commands) and all("--require-hashes" in command and "--only-binary=:all:" in command for command in build_commands)
    no_isolation = bool(lock_commands) and all("--no-build-isolation" in command for command in lock_commands)
    binary_policy = bool(lock_commands) and all("--only-binary=:all:" in command and
        "--no-binary=" + ",".join(policy.get("allowed_sdists", [])) in command for command in lock_commands)
    action_gaps = []
    if workflow_texts is None:
        action_gaps.append("CANONICAL_WORKFLOWS_NOT_AUDITED")
    else:
        observed_actions = set()
        for path, text in workflow_texts.items():
            for used in USES_RE.findall(text):
                action, separator, revision = used.partition("@")
                observed_actions.add(action)
                if (not separator or re.fullmatch(r"[0-9a-f]{40}", revision) is None
                        or policy.get("actions", {}).get(action) != revision):
                    action_gaps.append(path + ":" + used)
        action_gaps.extend("MISSING_ACTION:" + action for action in sorted(set(policy.get("actions", {})) - observed_actions))
    current_platform = current_platform if current_platform is not None else current_platform_identity()
    spec = policy.get("platform", {})
    platform_ok = platform_supported(current_platform, spec)
    schema_ok = policy.get("schema") == "rc6.hashed-distribution-lock.v1"
    green = (lock_rows and build_rows and not non_exact and not hash_gaps and not build_gaps
             and digest_pinned and uses_lock and hash_install and build_hash_install and no_isolation
             and binary_policy and not action_gaps and platform_ok and schema_ok)
    return {"schema_version": 3, "status": "GREEN" if green else "REPRODUCIBILITY_GAP",
        "reproducibility_scope": "APPROVED_ACTION_COMMITS_AND_HASHED_PYTHON_DISTRIBUTIONS",
        "hermetic_build": False, "residual_boundaries": policy.get("residual_boundaries", ["unreviewed supply-chain policy"]),
        "source_requirements_total": len(source_rows), "lock_requirements_total": len(lock_rows),
        "lock_exact_requirements": sum(bool(row["exact_pin"]) for row in lock_rows),
        "lock_non_exact_requirements": non_exact, "lock_distribution_hash_gaps": hash_gaps,
        "lock_hashed_requirements": sum(bool(row.get("hashes")) for row in lock_rows),
        "build_tools_total": len(build_rows), "build_distribution_hash_gaps": build_gaps,
        "base_image": bases[0] if bases else "", "base_image_digest_pinned": digest_pinned,
        "docker_uses_lock": uses_lock, "docker_requires_hashes": hash_install,
        "build_tools_require_hashes": build_hash_install, "build_isolation_disabled": no_isolation,
        "binary_distribution_policy_enforced": binary_policy, "action_commit_gaps": action_gaps,
        "platform": current_platform, "platform_supported": platform_ok, "supply_chain_schema_valid": schema_ok}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--fail-on-gap", action="store_true")
    parser.add_argument("--check-installed", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    workflow_paths = (".github/workflows/porota-predeploy-v2.yml", ".github/workflows/porota-deploy-v2-promote.yml")
    result = audit((root / "requirements.txt").read_text(), (root / "requirements.lock.txt").read_text(),
        (root / "Dockerfile").read_text(), build_lock_text=(root / "requirements.build.lock.txt").read_text(),
        supply_chain_policy=json.loads((root / "ops/policy/rc6-supply-chain-v1.json").read_text()),
        workflow_texts={path: (root / path).read_text() for path in workflow_paths})
    if args.check_installed:
        policy = json.loads((root / "ops/policy/rc6-supply-chain-v1.json").read_text())
        result["installed_closure"] = installed_distribution_audit(policy)
        if result["installed_closure"]["status"] != "GREEN":
            result["status"] = "REPRODUCIBILITY_GAP"
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(payload, end="")
    if args.json_out:
        args.json_out.write_text(payload)
    print("POROTA_DEPENDENCY_REPRO=" + result["status"])
    return 1 if args.fail_on_gap and result["status"] != "GREEN" else 0


if __name__ == "__main__":
    raise SystemExit(main())

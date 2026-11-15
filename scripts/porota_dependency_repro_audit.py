#!/usr/bin/env python3
"""Audit whether the RC6 Python/image dependency inputs are reproducible."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)(.*)$")
EXACT_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^,;\s]+(?:\s*;.*)?$")
FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.MULTILINE)
PYTHON_IMAGE_RE = re.compile(r"^python:(\d+\.\d+)(?:[-@]|$)")
RUN_LOCK_INSTALL_RE = re.compile(
    r"^\s*RUN\s+(?:(?:python(?:3(?:\.\d+)?)?)\s+-m\s+)?pip\s+install\b"
    r"[^\n#]*\s(?:-r|--requirement)\s+requirements\.lock\.txt(?:\s|\\|$)",
    re.MULTILINE,
)


def requirement_rows(text: str) -> list[dict[str, str | bool]]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append({"requirement": line, "exact_pin": bool(EXACT_RE.match(line))})
    return rows


def _target_environment(images: list[str]) -> dict[str, str] | None:
    """Only the Python minor is known from FROM; patch and OS are NOT known."""
    if not images:
        return None
    image = images[-1]
    match = PYTHON_IMAGE_RE.match(image)
    if not match:
        return None
    return {"python_version": match.group(1)}


def _static_marker_known(marker: object) -> bool:
    """Source-only checks may evaluate Python minor markers, never host values."""
    without_values = re.sub(r'''(["'])(?:\\.|(?!\1).)*?\1''', "", str(marker))
    variables = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", without_values))
    return variables <= {"python_version", "and", "or", "not", "in"}


def _pinned_requirements(
    rows: list[dict[str, str | bool]], environment: dict[str, str] | None = None,
    *, exact_environment: bool = False,
) -> tuple[dict[str, str], list[str], list[str]]:
    pins: dict[str, str] = {}
    duplicates: set[str] = set()
    invalid: list[str] = []
    seen: set[str] = set()
    for row in rows:
        line = str(row["requirement"])
        try:
            req = Requirement(line)
        except InvalidRequirement:
            invalid.append(line)
            continue
        name = canonicalize_name(req.name)
        if name in seen:
            duplicates.add(name)
        seen.add(name)
        exact = list(req.specifier)
        if not row["exact_pin"] or len(exact) != 1 or exact[0].operator != "==":
            invalid.append(line)
            continue
        try:
            if "*" in exact[0].version:
                raise InvalidVersion(exact[0].version)
            Version(exact[0].version)
        except InvalidVersion:
            invalid.append(line)
            continue
        if req.marker and environment is not None:
            if exact_environment or _static_marker_known(req.marker):
                if not req.marker.evaluate({**environment, "extra": ""}):
                    continue
        pins[name] = exact[0].version
    return pins, sorted(duplicates), invalid


def audit(requirements_text: str, lock_text: str, dockerfile_text: str) -> dict:
    source_rows = requirement_rows(requirements_text)
    lock_rows = requirement_rows(lock_text)
    lock_non_exact = [r["requirement"] for r in lock_rows if not r["exact_pin"]]
    bases = FROM_RE.findall(dockerfile_text)
    base = bases[0] if bases else ""
    digest_pinned = bool(bases) and all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in bases)
    docker_uses_lock = bool(RUN_LOCK_INSTALL_RE.search(dockerfile_text))
    environment = _target_environment(bases)
    pins, duplicate_lock_names, invalid_lock_rows = _pinned_requirements(lock_rows, environment)
    missing_direct_pins: set[str] = set()
    incompatible_direct_pins: set[str] = set()
    invalid_source_rows: list[str] = []
    unresolved_environment_markers: list[str] = []
    unsupported_requested_extras: set[str] = set()
    unsupported_direct_urls: set[str] = set()
    for row in lock_rows:
        try:
            lock_req = Requirement(str(row["requirement"]))
        except InvalidRequirement:
            continue  # Already captured by _pinned_requirements.
        if lock_req.marker and not _static_marker_known(lock_req.marker):
            unresolved_environment_markers.append(str(row["requirement"]))
        if lock_req.extras:
            unsupported_requested_extras.add(
                f"{canonicalize_name(lock_req.name)}[{','.join(sorted(lock_req.extras))}]"
            )
    for row in source_rows:
        line = str(row["requirement"])
        try:
            req = Requirement(line)
        except InvalidRequirement:
            invalid_source_rows.append(line)
            continue
        if req.extras:
            unsupported_requested_extras.add(
                f"{canonicalize_name(req.name)}[{','.join(sorted(req.extras))}]"
            )
        if req.url:
            unsupported_direct_urls.add(line)
        if req.marker and not _static_marker_known(req.marker):
            unresolved_environment_markers.append(line)
            continue
        if environment is None:
            continue
        if req.marker and not req.marker.evaluate({**environment, "extra": ""}):
            continue
        name = canonicalize_name(req.name)
        pinned_version = pins.get(name)
        if pinned_version is None:
            missing_direct_pins.add(name)
        elif not req.specifier.contains(pinned_version, prereleases=True):
            incompatible_direct_pins.add(str(req))
    input_errors = bool(
        not lock_rows or lock_non_exact or not digest_pinned or not docker_uses_lock
        or environment is None or duplicate_lock_names or invalid_lock_rows
        or invalid_source_rows or missing_direct_pins or incompatible_direct_pins
        or unresolved_environment_markers or unsupported_requested_extras
        or unsupported_direct_urls
    )
    return {
        "schema_version": 2,
        "status": "REPRODUCIBILITY_GAP" if input_errors else "INPUTS_PINNED_ONLY",
        "certifies_installed_closure": False,
        "source_requirements_total": len(source_rows),
        "lock_requirements_total": len(lock_rows),
        "lock_exact_requirements": sum(bool(r["exact_pin"]) for r in lock_rows),
        "lock_non_exact_requirements": lock_non_exact,
        "base_image": base,
        "build_stage_images": bases,
        "unpinned_stage_images": [image for image in bases if not re.search(r"@sha256:[0-9a-f]{64}$", image)],
        "base_image_digest_pinned": digest_pinned,
        "docker_uses_lock": docker_uses_lock,
        "target_python_version": environment["python_version"] if environment else None,
        "missing_direct_pins": sorted(missing_direct_pins),
        "incompatible_direct_pins": sorted(incompatible_direct_pins),
        "duplicate_lock_names": duplicate_lock_names,
        "invalid_source_rows": invalid_source_rows,
        "invalid_lock_rows": invalid_lock_rows,
        "unresolved_environment_markers": sorted(unresolved_environment_markers),
        "unsupported_requested_extras": sorted(unsupported_requested_extras),
        "unsupported_direct_urls": sorted(unsupported_direct_urls),
    }


def audit_installed_closure(
    lock_text: str, inspect_report: dict, *, image_id: str, pip_check_ok: bool,
) -> dict:
    """Validate a supplied pip-inspect receipt; caller must bind its image identity."""
    result = {
        "schema_version": 1, "status": "NO_VERIFICADO", "image_id": image_id,
        "graph_status": "NOT_CHECKED", "physical_binding_verified": False,
        "pip_check_ok": pip_check_ok, "missing_transitive_pins": [],
        "missing_runtime_dependencies": [], "incompatible_transitive_versions": [],
        "missing_locked_distributions": [], "incompatible_installed": [],
        "unexpected_installed": [], "invalid_distribution_metadata": [],
        "unsupported_requested_extras": [],
        "unsupported_direct_urls": [],
        "invalid_lock_rows": [],
        "baseline_tools": {}, "locked_packages_verified": 0,
    }
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id or "") or not isinstance(inspect_report, dict):
        return result
    env, rows = inspect_report.get("environment"), inspect_report.get("installed")
    if (not isinstance(env, dict) or not set(default_environment()).issubset(env)
            or not all(isinstance(env[k], str) for k in default_environment())):
        return result
    try:
        full_python = Version(env["python_full_version"])
    except InvalidVersion:
        return result
    if (not re.fullmatch(r"\d+\.\d+\.\d+", env["python_full_version"])
            or env["python_version"] != f"{full_python.major}.{full_python.minor}"):
        return result
    if not isinstance(rows, list) or not rows or not all(isinstance(x, dict) for x in rows):
        return result
    pins, duplicates, invalid_locks = _pinned_requirements(
        requirement_rows(lock_text), env, exact_environment=True,
    )
    result["invalid_lock_rows"] = sorted(invalid_locks)
    installed: dict[str, tuple[str, list[str]]] = {}
    invalid_metadata: list[str] = []
    for index, row in enumerate(rows):
        meta = row.get("metadata")
        if not isinstance(meta, dict):
            invalid_metadata.append(str(index))
            continue
        name, version, requires = meta.get("name"), meta.get("version"), meta.get("requires_dist", [])
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version or not isinstance(requires, list):
            invalid_metadata.append(str(index))
            continue
        try:
            Version(version)
        except InvalidVersion:
            invalid_metadata.append(str(index))
            continue
        key = canonicalize_name(name)
        if key in installed or not all(isinstance(x, str) for x in requires):
            invalid_metadata.append(key)
            continue
        installed[key] = (version, requires)
    if invalid_metadata or invalid_locks or duplicates or not pins:
        result["invalid_distribution_metadata"] = sorted(set(invalid_metadata + invalid_locks + duplicates))
        result["status"] = "REPRODUCIBILITY_GAP"
        return result

    transitive_missing, runtime_missing, transitive_bad = set(), set(), set()
    requested_extras: set[str] = set()
    unsupported_urls: set[str] = set()
    for parent, (_, requirements) in installed.items():
        for line in requirements:
            try:
                req = Requirement(line)
            except InvalidRequirement:
                invalid_metadata.append(parent)
                continue
            if req.marker and not req.marker.evaluate({**env, "extra": ""}):
                continue
            name = canonicalize_name(req.name)
            if req.extras:
                requested_extras.add(f"{name}[{','.join(sorted(req.extras))}]")
            if req.url:
                unsupported_urls.add(line)
            if name not in pins:
                transitive_missing.add(name)
            found = installed.get(name)
            if found is None:
                runtime_missing.add(name)
            elif not req.specifier.contains(found[0], prereleases=True):
                transitive_bad.add(name)
    # A pinned Python FROM digest does not independently attest pip's installed version.
    # The lock must cover it until a verified base-distribution receipt exists.
    baseline: dict[str, str] = {}
    result.update({
        "missing_transitive_pins": sorted(transitive_missing),
        "missing_runtime_dependencies": sorted(runtime_missing),
        "incompatible_transitive_versions": sorted(transitive_bad),
        "missing_locked_distributions": sorted(set(pins) - set(installed)),
        "incompatible_installed": sorted(name for name, version in pins.items()
                                         if name in installed and installed[name][0] != version),
        "unexpected_installed": sorted(set(installed) - set(pins) - set(baseline)),
        "invalid_distribution_metadata": sorted(set(invalid_metadata)),
        "unsupported_requested_extras": sorted(requested_extras),
        "unsupported_direct_urls": sorted(unsupported_urls),
        "baseline_tools": baseline,
        "locked_packages_verified": len(set(pins) & set(installed)),
        "target_python_version": env["python_version"],
    })
    gaps = ("missing_transitive_pins", "missing_runtime_dependencies", "incompatible_transitive_versions",
            "missing_locked_distributions", "incompatible_installed", "unexpected_installed",
            "invalid_distribution_metadata", "unsupported_requested_extras",
            "unsupported_direct_urls")
    gap = pip_check_ok is not True or any(result[k] for k in gaps)
    result["graph_status"] = "GAP" if gap else "CONSISTENT"
    # Image identity requires a separate, trustworthy workflow/host attestation;
    # callers can supply any syntactically valid image_id in this pure function.
    result["status"] = "REPRODUCIBILITY_GAP" if gap else "NO_VERIFICADO"
    return result


def _inspect_live_distributions() -> dict:
    """Collect only installed package metadata, without network or installers."""
    packages = []
    for dist in importlib.metadata.distributions():
        packages.append({"metadata": {
            "name": dist.metadata.get("Name"),
            "version": dist.version,
            "requires_dist": list(dist.requires or []),
        }})
    return {"environment": default_environment(), "installed": packages}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json-out")
    ap.add_argument("--fail-on-gap", action="store_true")
    ap.add_argument("--verify-installed-runtime", action="store_true")
    ap.add_argument("--image-id", default="")
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    lock_text = (root / "requirements.lock.txt").read_text(encoding="utf-8")
    result = audit(
        (root / "requirements.txt").read_text(encoding="utf-8"),
        lock_text,
        (root / "Dockerfile").read_text(encoding="utf-8"),
    )
    if args.verify_installed_runtime:
        target_python = result["target_python_version"]
        current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
        if (result["status"] != "INPUTS_PINNED_ONLY" or current_python != target_python
                or not os.getenv("POROTA_BUILD_SHA") or not os.getenv("POROTA_CANDIDATE_TREE_SHA")):
            closure = {"status": "NO_VERIFICADO", "reason": "EXACT_IMAGE_RUNTIME_CONTEXT_UNVERIFIED"}
        else:
            try:
                pip_check = subprocess.run(
                    [sys.executable, "-m", "pip", "check"],
                    capture_output=True, text=True, timeout=45, check=False,
                )
                closure = audit_installed_closure(
                    lock_text, _inspect_live_distributions(),
                    image_id=args.image_id, pip_check_ok=pip_check.returncode == 0,
                )
            except (OSError, subprocess.TimeoutExpired):
                closure = {"status": "NO_VERIFICADO", "reason": "PIP_CHECK_OR_METADATA_UNAVAILABLE"}
        closure["lock_sha256"] = hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
        result["installed_closure"] = closure
        print("POROTA_EXACT_IMAGE_DEPENDENCIES=" + closure["status"])
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    print("POROTA_DEPENDENCY_REPRO=" + result["status"])
    if args.verify_installed_runtime:
        # Never return 0 for exact-image certification without externally bound
        # digest, checkout/tree, manifest and image-executed pip receipt.
        return 1
    return 1 if args.fail_on_gap and result["status"] != "INPUTS_PINNED_ONLY" else 0


if __name__ == "__main__":
    raise SystemExit(main())

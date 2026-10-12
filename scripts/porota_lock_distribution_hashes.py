#!/usr/bin/env python3
"""Freeze reviewed versions to PyPI SHA256 distributions in an isolated branch.

Never run during CI or deployment: changing the lock requires a new candidate.
Only registry metadata is fetched; distributions and executable code are not run.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

BUILD_TOOLS = {"pip": "25.2", "setuptools": "80.9.0", "wheel": "0.45.1"}
ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
}
EXACT = re.compile(r"([A-Za-z0-9_.-]+)==([^;\s\\]+)")


def fetch_distribution(item):
    from packaging.tags import compatible_tags, cpython_tags, platform_tags
    from packaging.utils import canonicalize_name, parse_wheel_filename
    name, version = item
    url = "https://pypi.org/pypi/" + urllib.parse.quote(name, safe="") + "/" + urllib.parse.quote(version, safe="") + "/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read()
    metadata = json.loads(raw)
    if canonicalize_name(metadata["info"]["name"]) != canonicalize_name(name) or metadata["info"]["version"] != version:
        raise ValueError("REGISTRY_PACKAGE_IDENTITY_MISMATCH")
    # CPython3.11 runtime and CPython3.12 tooling on amd64 Linux. Select only
    # manylinux wheels compatible with this glibc floor, plus universal wheels.
    platforms = [tag for tag in platform_tags() if tag.endswith("x86_64")
                 and (not tag.startswith("manylinux_2_") or int(tag.split("_")[2]) <= 36)]
    tags = set()
    for minor in (11, 12):
        tags.update(cpython_tags(python_version=(3, minor), platforms=platforms))
        tags.update(compatible_tags(python_version=(3, minor), interpreter=f"cp3{minor}", platforms=platforms))
    wheels, sdists = [], []
    for row in metadata["urls"]:
        if row.get("yanked"):
            continue
        digest = row["digests"]["sha256"]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None or row["size"] <= 0:
            raise ValueError("REGISTRY_DISTRIBUTION_DIGEST_INVALID")
        entry = {"filename": row["filename"], "sha256": digest, "bytes": row["size"],
                 "packagetype": row["packagetype"]}
        if row["packagetype"] == "bdist_wheel":
            wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(row["filename"])
            if str(wheel_version) != version or canonicalize_name(wheel_name) != canonicalize_name(name):
                raise ValueError("REGISTRY_WHEEL_IDENTITY_MISMATCH")
            if wheel_tags & tags:
                wheels.append(entry)
        elif row["packagetype"] == "sdist":
            sdists.append(entry)
    # Preserve source-only versions. Build backends are separately hash-locked;
    # source compilation remains an explicitly measured non-hermetic boundary.
    distributions = wheels or sdists
    if not distributions:
        raise ValueError("LOCK_PLATFORM_DISTRIBUTION_UNAVAILABLE:" + name)
    return {"name": name, "version": version, "metadata_url": url,
            "metadata_sha256": hashlib.sha256(raw).hexdigest(),
            "distribution_policy": "COMPATIBLE_WHEEL" if wheels else "HASHED_SDIST_NO_BUILD_ISOLATION",
            "distributions": sorted(distributions, key=lambda entry: entry["filename"])}


def render_lock(rows, header):
    lines = [header, "# CPython3.11/3.12 Linux amd64 distributions; see ops/policy/rc6-supply-chain-v1.json."]
    for row in rows:
        hashes = sorted({item["sha256"] for item in row["distributions"]})
        lines.append(f"{row['name']}=={row['version']} \\")
        lines.extend("    --hash=sha256:" + digest + (" \\" if index < len(hashes) - 1 else "")
                     for index, digest in enumerate(hashes))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    lock = root / "requirements.lock.txt"
    versions = []
    for line in lock.read_text().splitlines():
        if line.strip().startswith(("#", "--hash=")) or not line.strip():
            continue
        match = EXACT.fullmatch(line.strip().removesuffix("\\").strip())
        if not match:
            raise ValueError("LOCK_INPUT_MUST_CONTAIN_EXACT_VERSIONS")
        versions.append(match.groups())
    if not any(name.lower() == "packaging" for name, _ in versions):
        versions.append(("packaging", "26.3"))
    with ThreadPoolExecutor(max_workers=12) as pool:
        packages = list(pool.map(fetch_distribution, versions))
        build = list(pool.map(fetch_distribution, BUILD_TOOLS.items()))
    metadata = {"schema": "rc6.hashed-distribution-lock.v1", "platform": {
        "os": "linux", "architecture": "x86_64", "python_minors": ["3.11", "3.12"],
        "glibc_minimum": "2.36", "runtime_base_digest_pinned": True},
        "actions": ACTIONS, "packages": packages, "build_tools": build,
        "allowed_sdists": sorted(row["name"].lower().replace("_", "-") for row in packages
                                  if row["distribution_policy"] == "HASHED_SDIST_NO_BUILD_ISOLATION"),
        "hermetic_build": False, "residual_boundaries": [
            "ubuntu-24.04 hosted runner and kernel are externally managed",
            "apt repository package bytes are not snapshot/hash locked",
            "source-only packages compile under the pinned image/runtime and hash-locked build backends",
            "pip resolver and installed closure are checked at Predeploy; same version does not guarantee equal wheel bytes",
        ]}
    (root / "ops/policy/rc6-supply-chain-v1.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    lock.write_text(render_lock(packages, "# Existing 153 reviewed versions preserved; distribution bytes now hash-locked."))
    (root / "requirements.build.lock.txt").write_text(render_lock(build, "# Hash-locked Python installer/build tools; install before runtime lock."))
    print(json.dumps({"packages": len(packages), "build_tools": len(build),
                      "allowed_sdists": metadata["allowed_sdists"], "hermetic_build": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

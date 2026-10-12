"""Reject known RC6 source-scope defects before the expensive full-Git gate.

This is a negative preflight only. A GREEN result grants no native gate credit;
the complete G0/G1 producer still verifies original objects and receipts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
MATRIX = "docs/audits/convergence/REQUIREMENT_CLOSURE_MATRIX.json"


def git(root: Path, *args: str, timeout: int = 180) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode:
        raise ValueError("FAST_PREFLIGHT_GIT_SCOPE_UNAVAILABLE")
    return result.stdout


def validate(root: Path, admission: dict) -> dict:
    # Reuse the producer's exact PR scope before building two locked Python
    # environments. A mismatch has no possible successful heavy continuation.
    sys.path.insert(0, str(root))
    from scripts.rc6_material_derive import expected_candidate_scope
    if admission.get("candidate_scope") != expected_candidate_scope():
        raise ValueError("FAST_PREFLIGHT_BUILDER_AUTHENTICATED_PR_SCOPE_MISMATCH")
    source = admission.get("source_sha")
    tree = admission.get("source_tree")
    base = admission.get("development_base_sha")
    if not all(isinstance(value, str) and SHA.fullmatch(value)
               for value in (source, tree, base)):
        raise ValueError("FAST_PREFLIGHT_EXACT_SOURCE_BASE_REQUIRED")
    if git(root, "rev-parse", "HEAD").decode().strip() != source:
        raise ValueError("FAST_PREFLIGHT_CHECKOUT_CHANGED")
    if git(root, "rev-parse", source + "^{tree}").decode().strip() != tree:
        raise ValueError("FAST_PREFLIGHT_TREE_CHANGED")
    changed = [p.decode() for p in git(
        root, "diff", "--name-only", "-z", "--no-renames", base, source
    ).split(b"\0") if p]
    matrix = json.loads((root / MATRIX).read_text(encoding="utf-8"))
    evolution = matrix.get("path_evolution")
    if not isinstance(evolution, dict):
        raise ValueError("FAST_PREFLIGHT_EVOLUTION_MATRIX_REQUIRED")
    missing = sorted(path for path in changed if path not in evolution)
    if missing:
        raise ValueError("FAST_PREFLIGHT_UNEXPLAINED_SOURCE_EVOLUTION:" + ",".join(missing))
    if any(re.search(r"(^|/)ppi[-_]watch(/|\.|$)", path, re.I) for path in changed):
        raise ValueError("FAST_PREFLIGHT_PPI_WATCH_SOURCE_CHANGED")
    cheap = admission.get("cheap_files")
    if not isinstance(cheap, list) or not all(isinstance(p, str) for p in cheap):
        raise ValueError("FAST_PREFLIGHT_CHEAP_SCOPE_REQUIRED")
    untested = sorted(p for p in changed if re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", p)
                      and p not in cheap)
    if untested:
        raise ValueError("FAST_PREFLIGHT_CHANGED_TEST_OUTSIDE_CHEAP:" + ",".join(untested))
    return {"status": "NEGATIVE_PREFLIGHT_GREEN_NO_GATE_CREDIT",
            "source_sha": source, "source_tree": tree, "development_base_sha": base,
            "changed_paths": len(changed), "real_orders_sent": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admission", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    admission = json.loads(args.admission.read_text(encoding="utf-8"))
    base = admission.get("development_base_sha")
    if not isinstance(base, str) or not SHA.fullmatch(base):
        raise ValueError("FAST_PREFLIGHT_EXACT_SOURCE_BASE_REQUIRED")
    # Source checkout is shallow; fetch only the authenticated transfer base.
    # It is public and is not persisted into the candidate checkout or droplet.
    git(root, "fetch", "--no-tags", "--filter=blob:none", "--depth=1",
        "origin", base, timeout=180)
    print(json.dumps(validate(root, admission), sort_keys=True))


if __name__ == "__main__":
    main()

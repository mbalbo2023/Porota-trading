#!/usr/bin/env python3
"""Validate a Porota deploy artifact before any droplet mutation.

Stdlib-only by design. This validator compares the deployable artifact against
tracked runtime-relevant source, checks local Python import closure and emits a
SHA256 manifest. It is intended to run in PRE-DEPLOY CI.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

RUNTIME_SUFFIXES = {".py", ".sh", ".service", ".timer"}
RUNTIME_EXACT = {
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
}


def is_runtime_relevant(path: str) -> bool:
    p = Path(path)
    if path in RUNTIME_EXACT:
        return True
    if p.suffix in RUNTIME_SUFFIXES:
        return True
    if path.startswith("systemd/") or path.startswith("ops/systemd/"):
        return True
    return False


def git_tracked_runtime_files(repo_root: Path) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        text=False,
    )
    files = [x.decode("utf-8") for x in out.split(b"\0") if x]
    return sorted(p for p in files if is_runtime_relevant(p))


def artifact_files(artifact_root: Path) -> set[str]:
    return {
        p.relative_to(artifact_root).as_posix()
        for p in artifact_root.rglob("*")
        if p.is_file()
    }


def root_local_modules(repo_root: Path, expected: Iterable[str]) -> dict[str, str]:
    modules: dict[str, str] = {}
    for rel in expected:
        p = Path(rel)
        if p.suffix != ".py":
            continue
        if len(p.parts) == 1:
            modules[p.stem] = rel
        elif p.name == "__init__.py":
            modules[p.parts[0]] = rel
    return modules


def imported_top_levels(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call):
            # Detect common literal dynamic imports.
            fn = node.func
            dynamic = (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "importlib"
                and fn.attr == "import_module"
            ) or (isinstance(fn, ast.Name) and fn.id == "__import__")
            if dynamic and node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and value:
                    found.add(value.split(".", 1)[0])
    return found


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate(repo_root: Path, artifact_root: Path, expected: list[str]) -> dict:
    present = artifact_files(artifact_root)
    missing_runtime = sorted(p for p in expected if p not in present)

    local_modules = root_local_modules(repo_root, expected)
    missing_imports: list[dict[str, str]] = []
    parse_errors: list[dict[str, str]] = []

    for rel in sorted(p for p in present if p.endswith(".py")):
        source = artifact_root / rel
        try:
            imports = imported_top_levels(source)
        except (SyntaxError, UnicodeDecodeError) as exc:
            parse_errors.append({"file": rel, "error": str(exc)})
            continue
        for module in sorted(imports):
            target = local_modules.get(module)
            if target and target not in present:
                missing_imports.append(
                    {"file": rel, "module": module, "expected_path": target}
                )

    manifest_files = []
    for rel in sorted(p for p in present if is_runtime_relevant(p)):
        manifest_files.append(
            {
                "path": rel,
                "sha256": sha256(artifact_root / rel),
                "bytes": (artifact_root / rel).stat().st_size,
            }
        )

    result = {
        "schema_version": 1,
        "status": "GREEN"
        if not (missing_runtime or missing_imports or parse_errors)
        else "FAILED",
        "expected_runtime_files": len(expected),
        "artifact_runtime_files": len(manifest_files),
        "missing_runtime_files": missing_runtime,
        "missing_local_imports": missing_imports,
        "parse_errors": parse_errors,
        "files": manifest_files,
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--artifact-root", required=True)
    ap.add_argument("--manifest-out")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    expected = git_tracked_runtime_files(repo_root)
    result = validate(repo_root, artifact_root, expected)

    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.manifest_out:
        Path(args.manifest_out).write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if result["status"] == "GREEN":
        print("POROTA_ARTIFACT_INTEGRITY=GREEN")
        return 0

    if result["missing_runtime_files"]:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_MISSING_RUNTIME_FILES", file=sys.stderr)
    if result["missing_local_imports"]:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_IMPORT_CLOSURE", file=sys.stderr)
    if result["parse_errors"]:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_PARSE", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

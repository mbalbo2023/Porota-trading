#!/usr/bin/env python3
"""Validate a Porota deploy artifact before any droplet mutation.

Stdlib-only by design. This validator compares the deployable artifact against
tracked runtime-relevant source, checks local Python import closure and emits a
SHA256 manifest. It is intended to run in PRE-DEPLOY CI.
"""

from __future__ import annotations

import argparse
import ast
from collections import deque
import hashlib
import json
import re
import shlex
import stat
from importlib.machinery import EXTENSION_SUFFIXES
import subprocess
import sys
from pathlib import Path
from typing import Iterable

RUNTIME_SUFFIXES = {".py", ".c", ".h", ".so", ".sh", ".service", ".timer"}
RUNTIME_EXACT = {
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "requirements.lock.txt",
    "n_instrument_watchlist.json",
    "POROTA_SECTOR_MAP_V1.csv",
}


def is_runtime_relevant(path: str) -> bool:
    p = Path(path)
    if p.parts and p.parts[0] in {"tests", "docs", ".github", ".agents"}:
        return False
    if path in RUNTIME_EXACT:
        return True
    if p.suffix in RUNTIME_SUFFIXES:
        return True
    if path.startswith("systemd/") or path.startswith("ops/systemd/"):
        return True
    return False


def _git_tracked_files(repo_root: Path) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        text=False,
    )
    files = [x.decode("utf-8") for x in out.split(b"\0") if x]
    return sorted(files)


def git_tracked_runtime_files(repo_root: Path) -> list[str]:
    return [p for p in _git_tracked_files(repo_root) if is_runtime_relevant(p)]


def git_tracked_python_files(repo_root: Path) -> list[str]:
    """Count all tracked Python, including tests and hidden CI helpers."""
    return [p for p in _git_tracked_files(repo_root) if p.endswith(".py")]


def _python_source_role(path: str) -> str:
    parts = Path(path).parts
    if parts[0] == "tests" or (len(parts) == 1 and parts[0].startswith("test_")):
        return "TEST_ONLY"
    if parts[0] == ".github":
        return "CI_ONLY"
    return "REVIEW_REQUIRED"


def _module_names(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _local_import_paths(tree: ast.AST, rel: str,
                        modules: dict[str, str]) -> tuple[dict[str, set[str]], bool]:
    """Resolve only literal local imports; unknown dependencies stay unproven."""
    imports: dict[str, set[str]] = {}
    unresolved_dynamic = False
    package = _module_names(rel).split(".")
    if Path(rel).name != "__init__.py":
        package = package[:-1]
    bare_import_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
        and node.module == "importlib"
        for alias in node.names if alias.name == "import_module"
    }
    importlib_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names if alias.name == "importlib"
    }
    for node in ast.walk(tree):
        candidates: list[tuple[str, str]] = []
        if isinstance(node, ast.Import):
            candidates.extend((alias.name, "STATIC_IMPORT") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if node.level > len(package):
                    continue
                base = ".".join([*package[:len(package) - node.level + 1],
                                  *([base] if base else [])])
            if base:
                candidates.append((base, "STATIC_IMPORT"))
                candidates.extend((base + "." + alias.name, "STATIC_IMPORT")
                                  for alias in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            literal_import = (
                isinstance(fn, ast.Name) and fn.id == "__import__"
            ) or (
                isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id in importlib_aliases and fn.attr == "import_module"
            ) or (
                isinstance(fn, ast.Name) and fn.id in bare_import_aliases
            )
            if literal_import:
                if (node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                        and node.args[0].value and not node.args[0].value.startswith(".")):
                    candidates.append((node.args[0].value, "LITERAL_DYNAMIC_IMPORT"))
                else:
                    unresolved_dynamic = True
        for candidate, kind in candidates:
            # Importing pkg.worker also executes pkg/__init__.py if present.
            bits = candidate.split(".")
            for index in range(1, len(bits) + 1):
                target = modules.get(".".join(bits[:index]))
                if target:
                    imports.setdefault(target, set()).add(kind)
    return imports, unresolved_dynamic


def _source_cycle_stage_paths(tree: ast.AST) -> set[str]:
    """Recognize literal STAGES passed as Python subprocess arguments."""
    def python_runner_call(node: ast.AST) -> bool:
        if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name)
                or node.func.id != "runner" or not node.args):
            return False
        command = node.args[0]
        if not isinstance(command, (ast.List, ast.Tuple)) or not command.elts:
            return False
        first = command.elts[0]
        python_executable = (
            isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name)
            and first.value.id == "sys" and first.attr == "executable"
        ) or (
            isinstance(first, ast.Constant) and isinstance(first.value, str)
            and Path(first.value).name in {"python", "python3"}
        )
        return python_executable and any(
            isinstance(x, ast.Name) and x.id == "relative"
            for x in ast.walk(command)
        )

    executes_relative = any(
        python_runner_call(node)
        for node in ast.walk(tree)
    )
    if not executes_relative:
        return set()
    paths = set()
    for node in getattr(tree, "body", []):
        if (not isinstance(node, ast.Assign)
                or not any(isinstance(target, ast.Name) and target.id == "STAGES"
                           for target in node.targets)):
            continue
        try:
            rows = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
        if not isinstance(rows, (tuple, list)):
            continue
        for row in rows:
            if (isinstance(row, (tuple, list)) and len(row) >= 2
                    and isinstance(row[1], str) and row[1].endswith(".py")):
                paths.add(row[1])
    return paths


def _repo_regular_file(repo_root: Path, rel: str) -> bool:
    """Reject path escapes and symlink components before reading inventory files."""
    path = Path(rel)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        return False
    try:
        if repo_root.is_symlink():
            return False
        root = repo_root.resolve(strict=True)
        target = root / path
        if not root.is_dir() or not target.resolve(strict=True).is_relative_to(root):
            return False
        cursor = root
        for index, part in enumerate(path.parts):
            cursor = cursor / part
            mode = cursor.lstat().st_mode
            if stat.S_ISLNK(mode):
                return False
            if index < len(path.parts) - 1 and not stat.S_ISDIR(mode):
                return False
        return stat.S_ISREG(cursor.lstat().st_mode)
    except (OSError, RuntimeError, ValueError):
        return False


def _direct_python_script(command: list[str]) -> str | None:
    """Only a direct Python interpreter invocation can declare a host root."""
    args = command
    if args and Path(args[0]).name == "env":
        args = args[1:]
        while args and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", args[0]):
            args = args[1:]
    if not args or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(args[0]).name):
        return None
    args = args[1:]
    # -m/-c and unknown options are not evidence of direct script execution.
    while args and args[0] in {"-B", "-E", "-I", "-O", "-OO", "-q", "-s", "-S", "-u", "-v", "-x"}:
        args = args[1:]
    if not args or args[0].startswith("-"):
        return None
    for prefix in ("/opt/porota-trading/", "/app/"):
        if args[0].startswith(prefix) and args[0].endswith(".py"):
            return args[0][len(prefix):]
    return None


def _host_python_entrypoints(repo_root: Path, host_services: dict[str, str],
                             paths: set[str]) -> tuple[dict[str, set[str]], list[str]]:
    roots: dict[str, set[str]] = {}
    unresolved = []
    for unit, role in sorted(host_services.items()):
        if (not unit.endswith(".service") or role != "ACTIVE_SERVICE"
                or not _repo_regular_file(repo_root, unit)):
            raise ValueError("PYTHON_HOST_SERVICE_INVALID:" + unit)
        found = False
        for line in (repo_root / unit).read_text(encoding="utf-8").splitlines():
            if not line.startswith("ExecStart="):
                continue
            try:
                command = shlex.split(line.partition("=")[2])
            except ValueError:
                continue
            rel = _direct_python_script(command)
            if rel in paths:
                roots.setdefault(rel, set()).add("HOST_ENTRYPOINT_DECLARED:" + unit)
                found = True
        if not found:
            unresolved.append(unit)
    return roots, unresolved


def python_source_provenance(
    repo_root: Path,
    tracked_python_paths: Iterable[str],
    *,
    entrypoints: Iterable[str] = (),
    host_services: dict[str, str] | None = None,
) -> dict:
    """Classify declared Python wiring; never claim it was observed on a host.

    An unreferenced file is *unreviewed*, not proven obsolete. This report is
    diagnostic and cannot be used as authorization to remove deployment files.
    """
    paths = sorted(set(tracked_python_paths))
    for rel in paths:
        if not rel.endswith(".py") or not _repo_regular_file(repo_root, rel):
            raise ValueError("PYTHON_SOURCE_INVENTORY_INVALID:" + rel)
    roots = set(entrypoints)
    if not roots <= set(paths):
        raise ValueError("PYTHON_ENTRYPOINT_NOT_TRACKED:" + ",".join(sorted(roots - set(paths))))

    reasons: dict[str, set[str]] = {path: set() for path in paths}
    for rel in roots:
        reasons[rel].add("ENTRYPOINT_DECLARED:" + rel)
    host_roots, unresolved_host = _host_python_entrypoints(
        repo_root, host_services or {}, set(paths)
    )
    for rel, origins in host_roots.items():
        reasons[rel].update(origins)
    modules = {_module_names(path): path for path in paths}
    imports: dict[str, dict[str, set[str]]] = {}
    stages: dict[str, set[str]] = {}
    parse_errors = []
    unresolved_dynamic_imports = []
    for rel in paths:
        try:
            tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"),
                             filename=rel)
        except (SyntaxError, UnicodeDecodeError) as error:
            parse_errors.append({"path": rel, "reason": type(error).__name__})
            continue
        imports[rel], unresolved = _local_import_paths(tree, rel, modules)
        if unresolved:
            unresolved_dynamic_imports.append({
                "source": rel, "reason": "DYNAMIC_IMPORT_ARGUMENT_NOT_LITERAL",
            })
        stages[rel] = _source_cycle_stage_paths(tree)
    unknown_stages = set()
    pending = deque(sorted(set(roots) | set(host_roots)))
    seen = set()
    while pending:
        source = pending.popleft()
        if source in seen:
            continue
        seen.add(source)
        for target, kinds in sorted(imports.get(source, {}).items()):
            reasons[target].update(kind + ":" + source for kind in kinds)
            pending.append(target)
        for target in sorted(stages.get(source, ())):
            if target not in reasons:
                unknown_stages.add((source, target))
                continue
            reasons[target].add("HOST_SOURCE_CYCLE_STAGE:" + source)
            pending.append(target)

    unresolved_files = {row["source"] for row in unresolved_dynamic_imports}
    parse_error_files = {row["path"] for row in parse_errors}
    missing_stage_sources = {source for source, _target in unknown_stages}
    sources = []
    for rel in paths:
        provenance = sorted(reasons[rel])
        source_role = _python_source_role(rel)
        classification = source_role
        if provenance:
            if source_role != "REVIEW_REQUIRED":
                classification = "REVIEW_REQUIRED"
            elif any(p.startswith("HOST_ENTRYPOINT_DECLARED:") for p in provenance):
                classification = "HOST_ENTRYPOINT_DECLARED"
            elif any(p.startswith("ENTRYPOINT_DECLARED:") for p in provenance):
                classification = "ENTRYPOINT_DECLARED"
            else:
                classification = "DEPENDENCY_DECLARED"
        if (rel in parse_error_files or rel in unresolved_files
                or rel in missing_stage_sources):
            classification = "REVIEW_REQUIRED"
        sources.append({
            "path": rel,
            "classification": classification,
            "provenance": provenance,
            "review_required": classification == "REVIEW_REQUIRED",
            "runtime_proven": "NO_VERIFICADO",
        })
    return {
        "schema_version": 1,
        "status": "NO_VERIFICADO",
        "source_count": len(sources),
        "unresolved_host_services": unresolved_host,
        "unresolved_source_cycle_stages": [
            {"source": source, "target": target}
            for source, target in sorted(unknown_stages)
        ],
        "unresolved_dynamic_imports": unresolved_dynamic_imports,
        "parse_errors": parse_errors,
        "sources": sources,
    }


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



def native_extension_closure(artifact_root: Path, present: set[str], expected: list[str]) -> tuple[list, list]:
    """Derive binary requirements from tracked CPython module definitions."""
    modules, missing = [], []
    for rel in sorted(path for path in expected if path.endswith(".c") and path in present):
        source = artifact_root / rel
        definitions = re.findall(r"\bPyMODINIT_FUNC\s+PyInit_([A-Za-z0-9_]+)\s*\(", source.read_text(encoding="utf-8"))
        for name in sorted(set(definitions)):
            parent = Path(rel).parent
            names = {(parent / (name + suffix)).as_posix() for suffix in EXTENSION_SUFFIXES}
            matches = sorted(names & present)
            valid = len(matches) == 1
            if valid:
                binary = artifact_root / matches[0]
                valid = not binary.is_symlink() and binary.stat().st_nlink == 1
            if not valid:
                missing.append({"source": rel, "module": name, "reason": "MISSING_AMBIGUOUS_OR_ALIASED_NATIVE_BINARY"})
                continue
            modules.append({"source": rel, "module": name, "path": matches[0],
                "sha256": sha256(binary), "bytes": binary.stat().st_size})
    return modules, missing


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

    native_extensions, missing_native = native_extension_closure(artifact_root, present, expected)

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
        if not (missing_runtime or missing_imports or parse_errors or missing_native)
        else "FAILED",
        "expected_runtime_files": len(expected),
        "artifact_runtime_files": len(manifest_files),
        "missing_runtime_files": missing_runtime,
        "missing_local_imports": missing_imports,
        "parse_errors": parse_errors,
        "native_extensions": native_extensions,
        "missing_native_extensions": missing_native,
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
    if result["missing_native_extensions"]:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_NATIVE_EXTENSION_CLOSURE", file=sys.stderr)
    if result["parse_errors"]:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_PARSE", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

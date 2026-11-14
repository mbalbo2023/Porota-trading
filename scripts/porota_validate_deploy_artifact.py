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
import os
import re
import shlex
import stat
from importlib.machinery import EXTENSION_SUFFIXES
import subprocess
import sys
from pathlib import Path
from typing import Iterable

RUNTIME_SUFFIXES = {".py", ".c", ".h", ".so", ".sh", ".service", ".timer"}
RUNTIME_METADATA_PREFIXES = ("ops/policy/", "ops/state/")
SENSITIVE_EXTRA_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".p8", ".jks", ".kdbx"}
SENSITIVE_EXTRA_NAMES = {
    ".netrc", ".npmrc", ".pypirc", ".git-credentials",
    "credentials", "credentials.json", "client_secret.json",
    "secret.txt", "acceso_dashboard_droplet.txt",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}
SENSITIVE_EXTRA_DIRS = {".aws", ".ssh", ".gnupg", ".secrets", "secrets"}
# Local virtualenvs and generated caches can legitimately contain publicly
# distributed CA .pem certificates; none of these directories belongs to the
# frozen Docker build context. They are not credential leakage evidence.
IGNORED_LOCAL_CACHE_DIRS = {
    "venv", ".venv", "__pycache__", ".pytest_cache", ".hypothesis", "htmlcov",
}
RUNTIME_EXACT = {
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "requirements.lock.txt",
    "n_instrument_watchlist.json",
    "POROTA_SECTOR_MAP_V1.csv",
}
# These are two declared PAPER roots used by porota_mode_manager.py. The
# Dockerfile CMD and literal Python subprocess children are discovered below;
# none of these roots is a runtime-file allowlist or runtime proof.
PAPER_IMAGE_ENTRYPOINTS = ("bv_paper_runtime.py", "o_dashboard.py")
HOST_POLICY = "ops/policy/host-control-plane-reconciliation-v2.json"
HOST_POLICY_ROLES = {
    "ACTIVE_SERVICE", "ACTIVE_TIMER", "QUARANTINED_SERVICE",
    "QUARANTINED_TIMER", "RETIRED_SERVICE", "RETIRED_TIMER",
}


def is_runtime_relevant(path: str) -> bool:
    p = Path(path)
    if p.parts and p.parts[0] in {"tests", "docs", ".github", ".agents"}:
        return False
    if path in RUNTIME_EXACT:
        return True
    if path.startswith(RUNTIME_METADATA_PREFIXES):
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


def _dockerfile_top_level_instructions(text: str) -> list[tuple[str, str]]:
    """Read directives outside Docker heredocs and continuations only."""
    instructions: list[tuple[str, str]] = []
    heredocs: deque[tuple[str, bool]] = deque()
    pending: tuple[str, str] | None = None
    marker_re = re.compile(
        r"<<(-?)(?:'([A-Za-z_][A-Za-z0-9_]*)'|"
        r'"([A-Za-z_][A-Za-z0-9_]*)"|([A-Za-z_][A-Za-z0-9_]*))'
    )
    for line in text.splitlines():
        if heredocs:
            marker, strip_tabs = heredocs[0]
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == marker:
                heredocs.popleft()
            continue
        # Alternate Docker parser escapes change instruction boundaries. Until
        # explicitly parsed, they cannot authorize an apparent runtime CMD.
        if re.match(r"^\s*#\s*escape\s*=", line, re.IGNORECASE):
            if line.partition("=")[2].strip() != "\\":
                raise ValueError("CONTAINER_DOCKER_ESCAPE_UNSUPPORTED")
        # Docker discards full-line comments and blank lines even while an
        # instruction is continued. Neither terminates its escape sequence.
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if pending is not None:
            kind, previous = pending
            body = previous + " " + line.strip()
        else:
            match = re.match(r"^([A-Z]+)\s+(.+)$", line)
            if match is None:
                continue
            kind, body = match.groups()
        if line.rstrip().endswith("\\"):
            pending = (kind, body[:-1].rstrip())
            continue
        pending = None
        instructions.append((kind, body))
        if "<<" in body:
            markers = list(marker_re.finditer(body))
            if not markers:
                raise ValueError("CONTAINER_DOCKER_HEREDOC_UNRECOGNIZED")
            for match in markers:
                name = next(value for value in match.groups()[1:] if value)
                heredocs.append((name, match.group(1) == "-"))
    if pending is not None or heredocs:
        raise ValueError("CONTAINER_DOCKER_MULTILINE_UNTERMINATED")
    return instructions


def _container_cmd_python_roots(repo_root: Path, paths: set[str]) -> tuple[set[str], list[str]]:
    """Discover a literal Python Docker CMD; opaque forms stay unverified."""
    dockerfile = repo_root / "Dockerfile"
    if not dockerfile.exists():
        return set(), []  # Older isolated provenance fixtures have no Dockerfile.
    if not _repo_regular_file(repo_root, "Dockerfile"):
        return set(), ["DOCKERFILE_NOT_REGULAR"]
    try:
        instructions = _dockerfile_top_level_instructions(
            dockerfile.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        return set(), [str(exc)]
    from_indices = [i for i, (kind, _) in enumerate(instructions)
                    if kind == "FROM"]
    if not from_indices:
        return set(), ["CONTAINER_FINAL_STAGE_UNVERIFIED"]
    commands = [(kind, value) for kind, value in instructions[from_indices[-1] + 1:]
                if kind in {"CMD", "ENTRYPOINT"}]
    if any(kind == "ENTRYPOINT" for kind, _ in commands):
        return set(), ["CONTAINER_ENTRYPOINT_REQUIRES_REVIEW"]
    cmd = [value for kind, value in commands if kind == "CMD"]
    if len(cmd) != 1:
        return set(), ["CONTAINER_CMD_NOT_UNIQUE"]
    try:
        argv = json.loads(cmd[0])
    except (ValueError, TypeError):
        return set(), ["CONTAINER_CMD_NOT_LITERAL_JSON"]
    if (not isinstance(argv, list) or len(argv) < 2
            or not all(isinstance(arg, str) for arg in argv)
            or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(argv[0]).name)):
        return set(), ["CONTAINER_CMD_NOT_LITERAL_PYTHON"]
    script = argv[1]
    if (script not in paths or not script.endswith(".py")
            or not _repo_regular_file(repo_root, script)):
        return set(), ["CONTAINER_CMD_PYTHON_SCRIPT_UNVERIFIED"]
    return {script}, []


def _literal_python_subprocess_paths(tree: ast.AST, paths: set[str]) -> tuple[set[str], bool]:
    """Trace literal Python Popen children without executing their commands."""
    imports = [alias for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names]
    subprocess_names = {alias.asname or alias.name for alias in imports
                        if alias.name == "subprocess"}
    sys_names = {alias.asname or alias.name for alias in imports
                 if alias.name == "sys"}
    direct_popen_names = {
        alias.asname or alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names if alias.name == "Popen"
    }
    if not subprocess_names and not direct_popen_names:
        return set(), False
    children = set()
    unresolved = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_alias = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in subprocess_names and node.func.attr == "Popen"
        )
        direct_alias = isinstance(node.func, ast.Name) and node.func.id in direct_popen_names
        if not call_alias and not direct_alias:
            continue
        if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            unresolved = True
            continue
        argv = node.args[0].elts
        if not argv:
            unresolved = True
            continue
        first = argv[0]
        executable_from_sys = (
            isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name)
            and first.value.id in sys_names and first.attr == "executable"
        )
        executable_is_python = executable_from_sys or (
            isinstance(first, ast.Constant) and isinstance(first.value, str)
            and re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(first.value).name) is not None
        )
        if not executable_is_python:
            continue
        if (direct_alias or (call_alias and node.func.value.id != "subprocess")
                or (executable_from_sys and first.value.id != "sys")
                or len(node.args) != 1
                or len(argv) < 2 or not isinstance(argv[1], ast.Constant)
                or not isinstance(argv[1].value, str)
                or argv[1].value not in paths or not argv[1].value.endswith(".py")
                or any(kw.arg is None or kw.arg in {"cwd", "executable", "shell"}
                       for kw in node.keywords)):
            unresolved = True
            continue
        children.add(argv[1].value)
    return children, unresolved


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
    docker_roots, unresolved_container = _container_cmd_python_roots(repo_root, set(paths))
    roots = set(entrypoints) | docker_roots
    if not roots <= set(paths):
        raise ValueError("PYTHON_ENTRYPOINT_NOT_TRACKED:" + ",".join(sorted(roots - set(paths))))

    reasons: dict[str, set[str]] = {path: set() for path in paths}
    for rel in roots:
        if rel in docker_roots:
            reasons[rel].add("CONTAINER_CMD_DECLARED:Dockerfile")
        else:
            reasons[rel].add("ENTRYPOINT_DECLARED:" + rel)
    host_roots, unresolved_host = _host_python_entrypoints(
        repo_root, host_services or {}, set(paths)
    )
    for rel, origins in host_roots.items():
        reasons[rel].update(origins)
    modules = {_module_names(path): path for path in paths}
    imports: dict[str, dict[str, set[str]]] = {}
    stages: dict[str, set[str]] = {}
    subprocesses: dict[str, set[str]] = {}
    unresolved_subprocess_sources: set[str] = set()
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
        subprocesses[rel], subprocess_unresolved = _literal_python_subprocess_paths(
            tree, set(paths))
        if subprocess_unresolved:
            unresolved_subprocess_sources.add(rel)
    unknown_stages = set()
    reached_unresolved_subprocesses = set()
    pending = deque(sorted(set(roots) | set(host_roots)))
    seen = set()
    while pending:
        source = pending.popleft()
        if source in seen:
            continue
        seen.add(source)
        if source in unresolved_subprocess_sources:
            reached_unresolved_subprocesses.add(source)
        for target, kinds in sorted(imports.get(source, {}).items()):
            reasons[target].update(kind + ":" + source for kind in kinds)
            pending.append(target)
        for target in sorted(stages.get(source, ())):
            if target not in reasons:
                unknown_stages.add((source, target))
                continue
            reasons[target].add("HOST_SOURCE_CYCLE_STAGE:" + source)
            pending.append(target)
        for target in sorted(subprocesses.get(source, ())):
            reasons[target].add("LITERAL_PYTHON_SUBPROCESS:" + source)
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
            elif any(p.startswith(("ENTRYPOINT_DECLARED:", "CONTAINER_CMD_DECLARED:"))
                     for p in provenance):
                classification = "ENTRYPOINT_DECLARED"
            else:
                classification = "DEPENDENCY_DECLARED"
        if (rel in parse_error_files or rel in unresolved_files
                or rel in missing_stage_sources
                or rel in reached_unresolved_subprocesses):
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
        "unresolved_container_entrypoints": unresolved_container,
        "unresolved_python_subprocesses": [
            {"source": source, "reason": "PYTHON_SUBPROCESS_TARGET_NOT_LITERAL"}
            for source in sorted(reached_unresolved_subprocesses)
        ],
        "unresolved_source_cycle_stages": [
            {"source": source, "target": target}
            for source, target in sorted(unknown_stages)
        ],
        "unresolved_dynamic_imports": unresolved_dynamic_imports,
        "parse_errors": parse_errors,
        "sources": sources,
    }


def active_host_python_services(repo_root: Path) -> dict[str, str]:
    """Read only canonical ACTIVE_SERVICE unit declarations, never external units."""
    if not _repo_regular_file(repo_root, HOST_POLICY):
        raise ValueError("PYTHON_HOST_POLICY_INVALID:" + HOST_POLICY)
    policy = json.loads((repo_root / HOST_POLICY).read_text(encoding="utf-8"))
    if (not isinstance(policy, dict) or policy.get("schema_version") != 2
            or not isinstance(policy.get("units"), dict)):
        raise ValueError("PYTHON_HOST_POLICY_INVALID:" + HOST_POLICY)
    active = {}
    for unit, contract in policy["units"].items():
        if (not isinstance(unit, str) or not isinstance(contract, dict)
                or contract.get("role") not in HOST_POLICY_ROLES):
            raise ValueError("PYTHON_HOST_POLICY_INVALID:" + HOST_POLICY)
        if contract.get("role") == "ACTIVE_SERVICE":
            active[unit] = "ACTIVE_SERVICE"
    return active


def artifact_files(artifact_root: Path) -> set[str]:
    return {
        p.relative_to(artifact_root).as_posix()
        for p in artifact_root.rglob("*")
        if p.is_file()
    }


def _preflight_artifact_tree(artifact_root: Path) -> None:
    """Reject aliases and special files before parsing or hashing image data.

    The tree must remain stable for the subsequent validation; this scan is
    not an atomic snapshot or a defense against concurrent replacement.
    """
    root = Path(os.path.abspath(artifact_root))
    for path in reversed((root, *root.parents)):
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise ValueError("ARTIFACT_ANCESTOR_NOT_DIRECTORY")
    pending = [root]
    count = total_bytes = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                count += 1
                if count > 1_000_000:
                    raise ValueError("ARTIFACT_MEMBER_LIMIT")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total_bytes += info.st_size
                    if info.st_size > 16 * 1024**3 or total_bytes > 64 * 1024**3:
                        raise ValueError("ARTIFACT_BYTE_LIMIT")
                else:
                    raise ValueError("ARTIFACT_MEMBER_UNSAFE")


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



def is_sensitive_untracked_extra(path: str) -> bool:
    """Fail closed if an untracked image addition looks like credential material.

    This does not inspect or print file contents, and deliberately keeps the
    existing runtime relevance / generated native extension rules separate.
    """
    parts = tuple(part.lower() for part in Path(path).parts)
    name = parts[-1] if parts else ""
    return bool(
        name in SENSITIVE_EXTRA_NAMES
        or (name.startswith(".env") and name != ".env.example")
        or Path(name).suffix in SENSITIVE_EXTRA_SUFFIXES
        or any(part in SENSITIVE_EXTRA_DIRS for part in parts[:-1])
    )


class SourceImageProvenanceError(ValueError):
    """Exact Git source / frozen image identity could not be established."""


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), *args], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceImageProvenanceError("GIT_BLOB_EVIDENCE_UNAVAILABLE") from exc


def _git_blob_sha256(repo_root: Path, blob_oid: str) -> str:
    """Read the exact committed Git blob, not potentially changed worktree."""
    if re.fullmatch(r"[0-9a-f]{40,64}", blob_oid) is None:
        raise SourceImageProvenanceError("GIT_BLOB_OBJECT_INVALID")
    digest = hashlib.sha256()
    try:
        with subprocess.Popen(
            ["git", "-C", str(repo_root), "cat-file", "blob", blob_oid],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ) as proc:
            assert proc.stdout is not None
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                digest.update(chunk)
            if proc.wait() != 0:
                raise SourceImageProvenanceError("GIT_BLOB_CONTENT_UNAVAILABLE")
    except OSError as exc:
        raise SourceImageProvenanceError("GIT_BLOB_CONTENT_UNAVAILABLE") from exc
    return digest.hexdigest()


def exact_git_image_bytes(repo_root: Path, artifact_root: Path,
                          candidate_sha: str) -> dict:
    """Attest *each* Git-tracked file present in the extracted image.

    Required runtime files (including ops/policy) remain a separate strict
    completeness check. Intentional .dockerignore omissions such as docs/data
    are not treated as runtime inputs. Untracked runtime inputs and dirty Git
    worktrees fail closed. Additional untracked credential-like files also
    fail closed, even if they are not Python or other runtime source. Generated
    native .so files have no tracked blob and retain their independent
    native_extension_closure / ABI verification.
    """
    if re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None:
        raise SourceImageProvenanceError("CANDIDATE_SHA_INVALID")
    head = _git_bytes(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
    if head != candidate_sha:
        raise SourceImageProvenanceError("GIT_HEAD_CANDIDATE_SHA_MISMATCH")
    # Git's tree SHA alone is not a proof that tests did not mutate checkout.
    tracked_dirty = _git_bytes(repo_root, "diff", "--name-only", "HEAD", "--").decode("utf-8").splitlines()
    staged_dirty = _git_bytes(repo_root, "diff", "--cached", "--name-only", "--").decode("utf-8").splitlines()
    untracked = [p.decode("utf-8") for p in _git_bytes(
        repo_root, "ls-files", "--others", "--exclude-standard", "-z",
    ).split(b"\0") if p]
    untracked_runtime = sorted(p for p in untracked if is_runtime_relevant(p))
    untracked_sensitive = sorted(p for p in untracked if is_sensitive_untracked_extra(p))
    # `--exclude-standard` intentionally hides .env/.secrets via .gitignore.
    # Query ignored files by name as a separate fail-closed checkout proof,
    # without opening sensitive bytes or falsely blocking virtualenv CA roots.
    ignored = [p.decode("utf-8") for p in _git_bytes(
        repo_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
    ).split(b"\0") if p]
    ignored_sensitive = sorted(
        p for p in ignored
        if (Path(p).parts and Path(p).parts[0].lower() not in IGNORED_LOCAL_CACHE_DIRS
            and is_sensitive_untracked_extra(p))
    )

    rows = _git_bytes(repo_root, "ls-tree", "-rz", "--full-tree", candidate_sha)
    tracked: dict[str, str] = {}
    tracked_aliases: list[str] = []
    for item in rows.split(b"\0"):
        if not item:
            continue
        record, rel_raw = item.split(b"\t", 1)
        mode, obj_type, oid = record.decode("ascii").split()
        rel = rel_raw.decode("utf-8")
        if obj_type != "blob":
            if is_runtime_relevant(rel):
                tracked_aliases.append(rel)
            continue
        tracked[rel] = oid
        if mode == "120000" and is_runtime_relevant(rel):
            tracked_aliases.append(rel)

    image = artifact_files(artifact_root)
    # `rglob` does not traverse a symlinked directory: inspect the directory
    # entries themselves so a policy path redirected outside /app fails rather
    # than silently appearing missing (or being skipped by the blob checker).
    image_aliases = sorted(
        p.relative_to(artifact_root).as_posix()
        for p in artifact_root.rglob("*") if p.is_symlink()
    )
    # The image may legitimately contain the compiled native extension built
    # from its tracked C input; its ABI/module proof is independent of Git.
    native_extensions, _ = native_extension_closure(
        artifact_root, image, git_tracked_runtime_files(repo_root),
    )
    generated_native = {item["path"] for item in native_extensions}
    untracked_image_runtime = sorted(
        rel for rel in image
        if is_runtime_relevant(rel) and rel not in tracked
        and rel not in generated_native
    )
    untracked_image_sensitive = sorted(
        rel for rel in image
        if rel not in tracked and is_sensitive_untracked_extra(rel)
    )
    mismatches: list[dict[str, str]] = []
    verified = 0
    for rel, oid in sorted(tracked.items()):
        if rel not in image:
            continue  # Required absences are rejected by validate(...expected).
        path = artifact_root / rel
        if (path.is_symlink() or path.stat().st_nlink != 1 or
                any(parent.is_symlink() for parent in path.parents if parent != artifact_root
                    and artifact_root in parent.parents)):
            mismatches.append({"path": rel, "reason": "SOURCE_IMAGE_ALIAS_UNSAFE"})
            continue
        expected_sha256 = _git_blob_sha256(repo_root, oid)
        actual_sha256 = sha256(path)
        if expected_sha256 != actual_sha256:
            mismatches.append({"path": rel, "reason": "SOURCE_IMAGE_BYTES_DRIFT"})
        else:
            verified += 1

    return {
        "status": "GREEN" if not (mismatches or tracked_dirty or staged_dirty or
                                      untracked_runtime or untracked_image_runtime or
                                      untracked_sensitive or ignored_sensitive or
                                      untracked_image_sensitive or
                                      tracked_aliases or image_aliases or not verified) else "FAILED",
        "candidate_sha": candidate_sha,
        "source": "COMMITTED_GIT_BLOB_NOT_WORKTREE",
        "verified_tracked_image_files": verified,
        "image_tracked_source_mismatches": mismatches,
        "dirty_tracked_files": sorted(set(tracked_dirty + staged_dirty)),
        "untracked_runtime_files": untracked_runtime,
        "untracked_image_runtime_files": untracked_image_runtime,
        "untracked_sensitive_files": untracked_sensitive,
        "ignored_sensitive_files": ignored_sensitive,
        "untracked_image_sensitive_files": untracked_image_sensitive,
        "runtime_gitlinks_or_symlinks": sorted(set(tracked_aliases)),
        "image_symlink_paths": image_aliases,
    }



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
    _preflight_artifact_tree(artifact_root)
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
    # R33 workflow owner passes the checkout's immutable PR SHA. Keep the
    # existing invocation valid until that integration lands.
    ap.add_argument("--candidate-sha")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    artifact_root = Path(os.path.abspath(args.artifact_root))
    if args.candidate_sha is not None:
        try:
            checkout_sha = subprocess.check_output(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            # Git index and worktree are checked separately: checking HEAD
            # alone would incorrectly certify dirty tracked candidate files.
            checkout_diffs = [
                subprocess.run(
                    ["git", "-C", str(repo_root), *command],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                for command in (
                    ("diff-index", "--cached", "--quiet", "HEAD", "--"),
                    ("diff-files", "--quiet", "--"),
                )
            ]
        except (OSError, subprocess.CalledProcessError):
            print("POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_GIT", file=sys.stderr)
            return 1
        if any(code not in (0, 1) for code in checkout_diffs):
            print("POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_GIT", file=sys.stderr)
            return 1
        if (not re.fullmatch(r"[0-9a-f]{40}", args.candidate_sha)
                or checkout_sha != args.candidate_sha):
            print("POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_SHA", file=sys.stderr)
            return 1
        if any(checkout_diffs):
            print("POROTA_ARTIFACT_INTEGRITY=FAILED_CANDIDATE_CHECKOUT_DIRTY",
                  file=sys.stderr)
            return 1

    expected = git_tracked_runtime_files(repo_root)
    try:
        result = validate(repo_root, artifact_root, expected)
    except (ValueError, OSError) as exc:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_ARTIFACT_PREFLIGHT:" + str(exc),
              file=sys.stderr)
        return 1
    try:
        result["python_source_provenance"] = python_source_provenance(
            repo_root, git_tracked_python_files(repo_root),
            entrypoints=PAPER_IMAGE_ENTRYPOINTS,
            host_services=active_host_python_services(repo_root),
        )
    except ValueError as exc:
        print("POROTA_ARTIFACT_INTEGRITY=FAILED_PYTHON_SOURCE_PROVENANCE:" + str(exc),
              file=sys.stderr)
        return 1
    result["candidate_sha"] = args.candidate_sha
    result["candidate_checkout_match"] = (
        "MATCH" if args.candidate_sha is not None else "NO_VERIFICADO"
    )
    if args.candidate_sha is not None:
        try:
            result["source_image_provenance"] = exact_git_image_bytes(
                repo_root, artifact_root, args.candidate_sha,
            )
        except (SourceImageProvenanceError, OSError, UnicodeError, ValueError) as exc:
            print("POROTA_ARTIFACT_INTEGRITY=FAILED_SOURCE_IMAGE_PROVENANCE:" + str(exc),
                  file=sys.stderr)
            return 1
        if result["source_image_provenance"]["status"] != "GREEN":
            result["status"] = "FAILED"

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

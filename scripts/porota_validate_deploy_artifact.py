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
import re
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


def is_runtime_relevant(path: str) -> bool:
    p = Path(path)
    if p.parts and p.parts[0] in {"tests", "docs", ".github", ".agents"}:
        return False
    if path in RUNTIME_EXACT:
        return True
    # The frozen bundle already treats these as operational inputs. They must
    # also be required inside the image, not omitted by a suffix-only filter.
    if path.startswith(RUNTIME_METADATA_PREFIXES):
        return True
    if p.suffix in RUNTIME_SUFFIXES:
        return True
    if path.startswith("systemd/") or path.startswith("ops/systemd/"):
        return True
    return False


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
    ap.add_argument("--candidate-sha", required=False)
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    expected = git_tracked_runtime_files(repo_root)
    result = validate(repo_root, artifact_root, expected)
    if args.candidate_sha:
        try:
            provenance = exact_git_image_bytes(repo_root, artifact_root, args.candidate_sha)
        except SourceImageProvenanceError as exc:
            provenance = {"status": "FAILED", "reason": str(exc),
                          "candidate_sha": args.candidate_sha}
        result["source_image_provenance"] = provenance
        if provenance["status"] != "GREEN":
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

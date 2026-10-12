#!/usr/bin/env python3
"""Generate source-bound ROOT supplemental membership outside Git worktrees.

This is a declarative Git/AST/JSON inventory. It does not execute mapped guards,
Gov, native producers, product imports, provider requests, image or runtime.
"""
from __future__ import annotations

import argparse
import ast
import collections
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib

MATRIX_PATH = "docs/audits/convergence/REQUIREMENT_CLOSURE_MATRIX.json"
REGISTRY_PATH = "docs/audits/convergence/REGISTRO_CONVERGENCIA_RC6.csv"
REGISTRY_SHA256 = "9c1432c006516d23418bf059adf8c4f8d37e869186cb0ef15e3bb6768c86df21"
FUTURE_ROOT_PATH = "docs/audits/convergence/ROOT_SUPPLEMENTAL_NAMED_FINDING_BINDINGS.json"
DEFAULT_IDS = (
    "NEW_ARCHIVE_DIRECTORY_ATIME_MUTATION",
    "NEW_CANONICAL_LIVE_FILE_QUOTA_DRIFT",
    "NEW_GIT_REPLACEMENT_OBJECT_AUTHORITY",
    "NEW_INSTALLED_DISTRIBUTION_METADATA_DUPLICATE",
    "NEW_SIGNED_ZERO_SUBTREE_COLLAPSE",
    "NEW_SOURCE_GIT_MODE_EVOLUTION",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RefResolutionError(ValueError):
    """Unresolvable documentary XML/gzip is unavailable, never execution."""


def decode_known_xml(data, path):
    """Only exact XML or one known gzip XML member; no arbitrary archives."""
    if path.endswith(".xml.gz"):
        if len(data) < 18 or data[:3] != b"\x1f\x8b\x08":
            raise RefResolutionError("known gzip XML member has an invalid header")
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        limit = 32 * 1024 * 1024
        try:
            decoded = inflater.decompress(data, limit + 1)
            if len(decoded) > limit or inflater.unconsumed_tail:
                raise RefResolutionError("gzip XML exceeds32MiB uncompressed bound")
            decoded += inflater.flush(limit + 1 - len(decoded))
        except zlib.error as error:
            raise RefResolutionError("gzip XML CRC/stream validation failed") from error
        if len(decoded) > limit or not inflater.eof or inflater.unused_data:
            raise RefResolutionError("gzip XML truncated, concatenated or oversized")
        encoding = {"encoding": "gzip-single-XML-member", "gzip_mtime": int.from_bytes(data[4:8], "little"),
                    "compressed_sha256": digest(data), "compressed_bytes": len(data),
                    "uncompressed_sha256": digest(decoded), "uncompressed_bytes": len(decoded)}
    elif path.endswith(".xml"):
        if len(data) > 32 * 1024 * 1024:
            raise RefResolutionError("XML exceeds32MiB metadata bound")
        decoded = data
        encoding = {"encoding": "identity-XML", "compressed_sha256": digest(data), "compressed_bytes": len(data),
                    "uncompressed_sha256": digest(data), "uncompressed_bytes": len(data)}
    else:
        raise RefResolutionError("metadata resolver only accepts XML or known.xml.gz members")
    return decoded, encoding


def xml_metadata_from_raw(data, tracked):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise RefResolutionError("RAW XML metadata is not a valid XML member") from error
    cases = []
    for case in root.iter("testcase"):
        path = case.get("file")
        name = case.get("name", "")
        node = path + "::" + name if path in tracked else None
        if node is None:
            parts = case.get("classname", "").split(".")
            for end in range(len(parts), 0, -1):
                candidate = "/".join(parts[:end]) + ".py"
                if candidate in tracked:
                    suffix = "::".join(parts[end:])
                    node = candidate + "::" + (suffix + "::" if suffix else "") + name
                    break
        status = "FAIL" if case.find("failure") is not None else "ERROR" if case.find("error") is not None else "SKIP" if case.find("skipped") is not None else "PASS"
        cases.append({"node": node, "raw_classname": case.get("classname"), "raw_name": case.get("name"),
                      "recorded_status": status, "seconds": case.get("time")})
    return {"root_tag": root.tag, "root_attributes": dict(root.attrib),
            "suites": [dict(suite.attrib) for suite in root.iter("testsuite")], "cases": cases,
            "actual_case_count": len(cases),
            "actual_case_outcomes": dict(collections.Counter(case["recorded_status"] for case in cases)),
            "source_binding_not_inferred_from_this_rebind": True}


def declared_xml_receipt_members(source, receipt):
    """Resolve explicit raw_refs XML members without changing receipt scope.

    Only named XML/XML.gz Git paths declared by a raw_refs mapping are opened.
    Absolute capture paths stay literal metadata and are never read locally.
    Each member keeps its own case count; counts across members are not summed.
    """
    members = []

    def visit(value, pointer=(), depth=0):
        if depth > 64:
            raise RefResolutionError("RAW receipt declaration graph exceeds64levels")
        if isinstance(value, dict):
            if "raw_refs" in value:
                references = value["raw_refs"]
                if not isinstance(references, dict):
                    raise RefResolutionError("declared raw_refs must be a mapping")
                for path, declaration in references.items():
                    if not isinstance(path, str) or not path.endswith((".xml", ".xml.gz")):
                        continue
                    if path.startswith("/"):
                        # Captured /tmp locations are retained by the parent JSON,
                        # with no invented byte availability or local read.
                        continue
                    if "\\" in path or "\0" in path or any(part in ("", ".", "..") for part in path.split("/")):
                        raise RefResolutionError("declared XML path is not an exact relative Git member")
                    if not isinstance(declaration, dict):
                        raise RefResolutionError("declared XML raw_refs entry must preserve a metadata object")
                    data = source.read(path)  # Missing compressed member rejects.
                    decoded, encoding = decode_known_xml(data, path)
                    declared_checks = {
                        "sha256": encoding["compressed_sha256"],
                        "bytes": encoding["compressed_bytes"],
                        "original_uncompressed_sha256": encoding["uncompressed_sha256"],
                        "original_uncompressed_bytes": encoding["uncompressed_bytes"],
                    }
                    for key, actual in declared_checks.items():
                        if key in declaration and (type(declaration[key]) is not type(actual) or declaration[key] != actual):
                            raise RefResolutionError("declared XML compressed/uncompressed hash or length mismatch: " + key)
                    if declaration.get("encoding") == "gzip-mtime0" and (
                        encoding["encoding"] != "gzip-single-XML-member" or encoding["gzip_mtime"] != 0
                    ):
                        raise RefResolutionError("declared gzip-mtime0 encoding differs from the RAW member")
                    binding = source.binding(path)
                    members.append({
                        "receipt_declaration_pointer": list(pointer + ("raw_refs", path)),
                        "declared_reference_verbatim": copy.deepcopy(declaration),
                        "publication_binding": {key: binding[key] for key in ("source_sha", "source_tree", "path", "blob", "git_mode", "sha256", "bytes")},
                        "xml_encoding": encoding,
                        "raw_xml_metadata": xml_metadata_from_raw(decoded, source.entries),
                        "case_count_scope": "THIS_RAW_MEMBER_ONLY_NO_SUM_OR_ROOT_CURRENT_EXECUTION_INFERENCE",
                    })
            for key, child in value.items():
                if key != "raw_refs":
                    visit(child, pointer + (key,), depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, pointer + (index,), depth + 1)

    visit(receipt)
    return members


def strict_json(data):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj
    return json.loads(data, object_pairs_hook=pairs)


class GitSource:
    def __init__(self, repo, sha):
        self.repo = repo.resolve(strict=True)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("source requires a full immutable lower-case commit SHA")
        self.sha = sha
        if self.git("rev-parse", "--verify", sha + "^{commit}").decode().strip() != sha:
            raise ValueError("source is not the requested raw commit")
        self.tree = self.git("rev-parse", sha + "^{tree}").decode().strip()
        self.entries = {}
        self.cache = {}
        for raw in self.git("ls-tree", "-r", "-z", "--full-tree", sha).split(b"\0"):
            if not raw:
                continue
            header, path = raw.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            self.entries[path.decode()] = (mode, kind, oid)

    def git(self, *args):
        return subprocess.run(["git", "--no-replace-objects", "--no-optional-locks", "-C", str(self.repo), *args],
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout

    def read(self, path):
        if path not in self.entries:
            raise ValueError(f"declared source path not committed: {path}")
        mode, kind, oid = self.entries[path]
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError(f"source path is not a regular committed blob: {path}")
        if oid not in self.cache:
            length = int(self.git("cat-file", "-s", oid))
            if length > 64 * 1024 * 1024:
                raise ValueError("source member exceeds documentary 64MiB bound")
            data = self.git("cat-file", "blob", oid)
            if len(data) != length or hashlib.sha1(b"blob " + str(length).encode() + b"\0" + data).hexdigest() != oid:
                raise ValueError("raw Git blob identity mismatch")
            self.cache[oid] = data
        return self.cache[oid]

    def binding(self, path):
        data = self.read(path)
        mode, _, oid = self.entries[path]
        return {"path": path, "blob": oid, "git_mode": mode, "sha256": digest(data), "bytes": len(data),
                "source_sha": self.sha, "source_tree": self.tree,
                "source_scope": "ROOT_COMMITTED_PUBLICATION_NOT_CAPTURE_SOURCE", "workingtree_bytes_used": False}

    def json(self, path):
        return strict_json(self.read(path))


def external_output(source, path):
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError("out requires an absolute external path with an existing parent")
    for candidate in [path, *path.parents]:
        if candidate.is_symlink():
            raise ValueError("output symlink alias is not permitted")
    target = path.resolve(strict=False)
    roots = [source.repo]
    roots += [Path(line[9:]).resolve(strict=False) for line in source.git("worktree", "list", "--porcelain").decode().splitlines()
              if line.startswith("worktree ")]
    if any(target == root or root in target.parents for root in roots):
        raise ValueError("output must stay outside ROOT and every linked worktree")
    if target.exists():
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("output target must be a regular single-link file")
    return target


def owner_index(obj):
    for key in ("programming_requirements", "findings", "requirements", "entries"):
        if isinstance(obj.get(key), list):
            rows = obj[key]
            indexed = {row["id"]: row for row in rows}
            if len(indexed) != len(rows):
                raise ValueError("upstream owner index has duplicate IDs")
            return indexed
    guard = obj.get("guard_mapping", {})
    return guard.get("mapping", {}) if isinstance(guard, dict) else {}


def unique_guard(source, node, cache):
    base = node.split("[", 1)[0]
    path, *parts = base.split("::")
    if path not in cache:
        cache[path] = ast.parse(source.read(path), filename=path)
    children = cache[path].body
    selected = None
    for part in parts:
        matches = [item for item in children if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part]
        if len(matches) != 1:
            raise ValueError(f"guard AST declaration must be unique: {base}")
        selected = matches[0]
        children = selected.body
    if not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError(f"guard node is not a function: {base}")
    return {"pytest_node": node, "base_node": base, "source_binding": source.binding(path),
            "line": selected.lineno, "ast_sha256": digest(ast.dump(selected, include_attributes=False).encode()),
            "ast_hash_python_version": sys.version.split()[0], "unique_AST_definition": True,
            "execution_status": "NOT_EXECUTED_BY_THIS_SOURCE_ONLY_GENERATOR"}


def build(source, finding_ids):
    matrix = source.json(MATRIX_PATH)
    if matrix.get("schema") != "rc6.convergence-closure.v1":
        raise ValueError("unsupported closure schema")
    registry_bytes = source.read(REGISTRY_PATH)
    if digest(registry_bytes) != REGISTRY_SHA256:
        raise ValueError("original registry literal byte authority changed")
    rows = list(csv.DictReader(io.StringIO(registry_bytes.decode("utf-8-sig"), newline="")))
    original = {row["id"]: row for row in rows}
    if len(rows) != 55 or len(original) != 55:
        raise ValueError("original registry must retain exactly55 unique rows")
    requirements = {row["id"]: row for row in matrix["requirements"]}
    if len(matrix["requirements"]) != 55 or set(requirements) != set(original):
        raise ValueError("current closure must retain the original55 IDs")
    for identifier, row in requirements.items():
        if row["original_requirement"] != original[identifier]:
            raise ValueError("current closure parent differs from original literal registry")
    findings = {row["id"]: row for row in matrix["additional_findings"]}
    if len(findings) != len(matrix["additional_findings"]):
        raise ValueError("duplicate current named finding ID")
    if len(set(finding_ids)) != len(finding_ids):
        raise ValueError("duplicate requested named finding ID")
    ast_cache = {}
    bindings = []
    for identifier in finding_ids:
        if identifier not in findings:
            raise ValueError(f"named finding absent from committed closure: {identifier}")
        finding = findings[identifier]
        parents = finding["linked_requirement_ids"]
        if not parents or len(set(parents)) != len(parents) or not set(parents) <= set(original):
            raise ValueError("named finding must retain unique valid original parents")
        owner_path = finding["owner_mapping"]
        original_workstream = finding["workstream"]
        if finding.get("ownership_scope") == "ROOT_INTEGRATION_SUPPLEMENTAL_NAMED_FINDING_BINDINGS":
            previous = source.json(owner_path)
            if previous.get("schema") != "rc6.root-supplemental-named-finding-bindings.v1":
                raise ValueError("previous ROOT supplement has another schema")
            previous_rows = previous.get("bindings", [])
            candidates = [row for row in previous_rows if row.get("id") == identifier]
            if len(candidates) != 1 or len({row.get("id") for row in previous_rows}) != len(previous_rows):
                raise ValueError("previous ROOT supplement has no unique exact finding membership")
            prior = candidates[0]
            if prior.get("exact_root_named_membership") is not True or prior.get("exact_upstream_owner_membership") is not False:
                raise ValueError("previous ROOT supplement cannot replace an upstream owner membership")
            owner_path = prior["upstream_owner_mapping"]["source_binding"]["path"]
            original_workstream = prior["original_workstream"]
        owner = source.json(owner_path)
        indexed = owner_index(owner)
        if identifier in indexed:
            raise ValueError("finding already has exact upstream ownership; supplementary upstream=False is inapplicable")
        receipt_path = finding["evidence_receipt"]
        receipt = source.json(receipt_path)
        guards = [unique_guard(source, node, ast_cache) for node in finding["test_nodes"]]
        if not guards:
            raise ValueError("named finding has no current native guard definition")
        binding = {
            "id": identifier, "root_binding_owner": "ROOT_INTEGRATION",
            "exact_root_named_membership": True,
            "exact_upstream_owner_membership": False,
            "ownership_scope": "ROOT_INTEGRATION_SUPPLEMENTAL_NAMED_FINDING_BINDINGS",
            "membership_authority": "EXACT_ROOT_NAMED_FINDING_FROM_COMMITTED_CLOSURE_ONLY",
            "original_workstream": original_workstream, "root_closure_finding_verbatim": finding,
            "linked_requirement_ids": parents,
            "original_parent_clauses": [original[parent] for parent in parents],
            "upstream_owner_mapping": {
                "source_binding": source.binding(owner_path),
                "exact_new_finding_id_present_in_upstream_typed_index": identifier in indexed,
                "original_parent_ids_present_in_upstream_index": [parent for parent in parents if parent in indexed],
                "boundary": "Original owner document is unchanged. ROOT supplementary membership does not create upstream OWNER exact membership.",
            },
            "current_code_bindings": [source.binding(path) for path in finding["code_paths"]],
            "current_native_guards": guards,
            "actual_receipt": {"publication_binding": source.binding(receipt_path),
                "original_raw_metadata_verbatim": receipt,
                "declared_xml_receipt_members": declared_xml_receipt_members(source, receipt),
                "explicit_top_level_scope": receipt.get("scope"),
                "scope_limits_verbatim": receipt.get("scope_limits", receipt.get("limits")),
                "capture_identity_policy": "All capture source/environment/count/scope fields retain their original RAW values; ROOT publication source is not a rewrite of execution source.",
            },
            "current_source_execution_status": "PENDING_EXACT_FROZEN_NATIVE_GOVERNED_EXECUTION",
            "new_original_requirement_or_attack": False,
        }
        bindings.append(binding)
    return {
        "schema": "rc6.root-supplemental-named-finding-bindings.v1",
        "status": "ROOT_SOURCE_ONLY_DECLARATIVE_NAMED_BINDINGS_NOT_EXECUTED",
        "scope": "ROOT_INTEGRATION_SUPPLEMENTAL_SOURCE_MAPPING_NO_GOV_NATIVE_RUNTIME_IMAGE_PROVIDER_OR_FINANCIAL_REPLAY",
        "future_canonical_path": FUTURE_ROOT_PATH,
        "source_snapshot": {"source_sha": source.sha, "source_tree": source.tree,
            "selection": "Explicit immutable Git source; no replace objects; committed raw blobs+modes; no workingtree overlays.",
            "source_closure_binding": source.binding(MATRIX_PATH),
            "source_original_registry_binding": source.binding(REGISTRY_PATH),
            "rebind_required_if_source_advances": True, "final_frozen_source_sha": None,
            "final_fip_digest": None, "final_governed_junit_digest": None, "final_artifact_digest": None,
            "document_order": "Pin stable source S; commit document D referencing S and explicit ROOT ownership; freeze C after D; actual Gov/FIP/image/artifact bind C. No output selfhash.",
        },
        "bindings": bindings,
        "validation": {"named_finding_count": len(bindings), "original_parent_ids_verified_against_literal55": True,
            "exact_root_named_membership": True,
            "exact_upstream_owner_membership_for_requested_IDS": {row["id"]: row["exact_upstream_owner_membership"] for row in bindings},
            "code_and_guard_regular_blobs_modes_bound": True,
            "unique_guard_AST_base_count": len({guard["base_node"] for row in bindings for guard in row["current_native_guards"]}),
            "raw_receipt_metadata_scope_preserved_verbatim": True,
            "known_xml_gzip_receipts_validated_without_summing_case_counts": True,
            "upstream_owner_documents_modified": False, "workingtree_bytes_read": False,
            "native_tests_executed": 0, "governed_suite_executed": False, "self_hash_included": False},
        "acceptance_boundary": "ROOT can declare these exact supplemental IDs in its own versioned mapping. Presence/AST/source hashes are not proof of current execution, runtime, provider capacity, deployed image, or all parent-clause semantics.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--finding-id", action="append", help="Explicit repeated ROOT finding ID; default is the six reviewed supplemental IDs.")
    args = parser.parse_args(argv)
    temporary = None
    try:
        source = GitSource(args.repo, args.source)
        target = external_output(source, args.out)
        output = build(source, args.finding_id or DEFAULT_IDS)
        data = (json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        descriptor, temporary = tempfile.mkstemp(prefix=".rc6-root-named-bindings-", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        external_output(source, target)
        os.replace(temporary, target)
        temporary = None
        print(json.dumps({"status": output["status"], "source_sha": source.sha, "source_tree": source.tree,
            "out": str(target), "sha256": digest(data), "bytes": len(data),
            "finding_count": len(output["bindings"]), "native_tests_executed": 0}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "ROOT_SUPPLEMENTAL_SOURCE_BINDING_REJECTED", "error": str(error)}), file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Documentary rebind from immutable Git bytes; never execute product or tests.

Usage: --repo REPO --source FULL_SHA --out EXTERNAL_JSON
The earlier committed register is a historical template, not a source of final
execution authority. Receipts retain their own capture source, scope and counts.
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

BASE = "docs/audits/convergence/"
MATRIX_PATH = BASE + "REQUIREMENT_CLOSURE_MATRIX.json"
REGISTER_PATH = BASE + "REMEDIATION_REGISTER.json"
CSV_PATH = BASE + "REGISTRO_CONVERGENCIA_RC6.csv"
SCENARIOS_PATH = BASE + "ORIGINAL_SCENARIOS_469.json"
OWNERS = {
    "BUDGET": BASE + "RC6_BUDGET_F01_CONVERGENCE.json",
    "FINANCE": "docs/audits/rc6_convergence_finance/consolidated_finance_evidence.json",
    "HISTORY": "rc6_audit_evidence/history_convergence/owned_exact_node_matrix.json",
    "UX": "docs/dashboard/evidence/rc6-convergence-ux/id-test-mapping.json",
    "SRE": "docs/audits/RC6_CONVERGENCE_SRE_OFFLINE.json",
    "PERSISTENCE": "docs/audits/rc6-convergence-persistence-evidence/requirement-matrix-wip.json",
}
ORIGINAL_PINS = {
    "INPUT_MANIFEST_RC6_CONVERGENCIA.json": "3295e3d001e6a28e21fb227f5aed98a5119337d68abb5c2c1b50ee8e528d1ec4",
    "ISSUE469_REAUDITORIA_INDEPENDIENTE_466_C27DFD9.md": "270630106697702935da3aeeca7fd936afd48471fb746c2cd56a9089b63a25fb",
    "ORDEN_UNICA_CODEX_RC6_CONVERGENCIA_468_469_470.md": "d41e404d893a2fedfb10017b5f5732282f6e695220305e9b9cf1049b7c310731",
    "REGISTRO_CONVERGENCIA_RC6.csv": "9c1432c006516d23418bf059adf8c4f8d37e869186cb0ef15e3bb6768c86df21",
    "ORIGINAL_RA_A_F01_F02.md": "74d2d852c9c62754cd185dfb41e86b144c450c31b155d955348e16dce47c84c9",
    "ORIGINAL_RA_A_F03_F05.md": "289f7a89ce48e56a7c3770ece9195d9c58f9d1ea9d23a2004552621c2d7bd87b",
}
EVIDENCE_DIRS = (
    BASE + "evidence/", "rc6_audit_evidence/history_convergence/",
    "docs/dashboard/evidence/rc6-convergence-ux/",
    "docs/audits/rc6_convergence_finance/",
    "docs/audits/rc6-convergence-persistence-evidence/",
)
BOUNDARY = "Historical or intermediate receipt only; not execution on this rebind source, a final artifact, deployed runtime, real provider or financial authority."
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def node_base(node: str) -> str:
    return node.split("[", 1)[0]


def strict_json(data: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(data, object_pairs_hook=pairs)


class Source:
    def __init__(self, repo: Path, revision: str):
        self.repo = repo.resolve(strict=True)
        if not HEX40.fullmatch(revision):
            raise ValueError("--source must be a full lower-case 40-hex commit SHA")
        self.sha = revision
        actual = self.git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
        if actual != revision:
            raise ValueError("requested SHA differs from raw Git commit authority")
        self.tree = self.git("rev-parse", revision + "^{tree}").decode().strip()
        self.head_at_start = self.git("rev-parse", "HEAD").decode().strip()
        self.status_at_start = self.status()
        self.entries = {}
        for line in self.git("ls-tree", "-r", "-z", "--full-tree", revision).split(b"\0"):
            if not line:
                continue
            header, raw_path = line.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            path = raw_path.decode("utf-8")
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError(f"unsupported committed source mode/type: {path}:{mode}:{kind}")
            self.entries[path] = (mode, oid)
        self.cache = {}
        self.bindings = {}
        self.batch = subprocess.Popen(
            self.command("cat-file", "--batch"), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def command(self, *args):
        return ["git", "--no-replace-objects", "--no-optional-locks", "-C", str(self.repo), *args]

    def git(self, *args):
        return subprocess.run(self.command(*args), check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30).stdout

    def status(self):
        return self.git("status", "--porcelain=v1", "--untracked-files=no").decode().splitlines()

    def blob(self, oid):
        if oid not in self.cache:
            self.batch.stdin.write((oid + "\n").encode())
            self.batch.stdin.flush()
            header = self.batch.stdout.readline(256).decode().strip().split()
            if len(header) != 3 or header[0] != oid or header[1] != "blob":
                raise ValueError(f"Git did not return the requested raw blob: {oid}")
            length = int(header[2])
            if not 0 <= length <= 64 * 1024 * 1024:
                raise ValueError(f"blob exceeds documentary safety bound: {oid}:{length}")
            data = self.batch.stdout.read(length)
            if len(data) != length or self.batch.stdout.read(1) != b"\n":
                raise ValueError("incomplete raw Git blob stream")
            digest = hashlib.sha1(b"blob " + str(length).encode() + b"\0" + data).hexdigest()
            if digest != oid:
                raise ValueError("raw Git blob bytes do not match the requested object")
            self.cache[oid] = data
        return self.cache[oid]

    def read(self, path):
        if path not in self.entries:
            raise FileNotFoundError(f"not committed in source {self.sha}: {path}")
        return self.blob(self.entries[path][1])

    def json(self, path):
        return strict_json(self.read(path))

    def binding(self, path):
        if path not in self.bindings:
            data = self.read(path)
            mode, blob = self.entries[path]
            self.bindings[path] = {
                "path": path, "blob": blob, "git_mode": mode,
                "sha256": sha256(data), "bytes": len(data),
                "source_sha": self.sha, "source_tree": self.tree,
                "source_scope": "ROOT_COMMITTED_SNAPSHOT", "workingtree_bytes_used": False,
            }
        return copy.deepcopy(self.bindings[path])

    def historical(self, revision, path):
        if not isinstance(revision, str) or not HEX40.fullmatch(revision):
            raise ValueError("historical receipt has no full immutable binding source SHA")
        if self.git("rev-parse", "--verify", revision + "^{commit}").decode().strip() != revision:
            raise ValueError("historical publication SHA is not the exact raw commit")
        result = self.git("ls-tree", "-z", revision, "--", path).split(b"\0")
        matches = [r for r in result if r and r.split(b"\t", 1)[1].decode() == path]
        if len(matches) != 1:
            raise FileNotFoundError(f"historical receipt not uniquely committed: {revision}:{path}")
        mode, kind, oid = matches[0].split(b"\t", 1)[0].decode().split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("historical receipt is not a regular committed blob")
        data = self.blob(oid)
        tree = self.git("rev-parse", revision + "^{tree}").decode().strip()
        return data, {"path": path, "blob": oid, "git_mode": mode, "sha256": sha256(data),
                      "bytes": len(data), "source_sha": revision, "source_tree": tree,
                      "source_scope": "HISTORICAL_COMMITTED_RECEIPT", "workingtree_bytes_used": False}

    def close(self):
        if hasattr(self, "batch"):
            self.batch.stdin.close()
            self.batch.wait(timeout=5)
            self.batch.stdout.close()
            self.batch.stderr.close()


def outside_output(source, path):
    if not path.is_absolute():
        raise ValueError("--out must be an absolute path outside all repository worktrees")
    # No symlink directory, symlink target or multiply-linked target is writable.
    for part in [path, *path.parents]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"output path has a symlink alias: {part}")
    resolved = path.resolve(strict=False)
    roots = [source.repo]
    for line in source.git("worktree", "list", "--porcelain").decode().splitlines():
        if line.startswith("worktree "):
            roots.append(Path(line[9:]).resolve(strict=False))
    if any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("--out cannot write inside ROOT or another Git worktree")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("existing --out must be a regular single-link file")
    if not path.parent.is_dir():
        raise ValueError("external output directory must already exist")
    return resolved


REF_SCHEMA = "rc6.remediation-derived-ref.v1"
COMPACT_SCHEMA = "rc6.remediation-register.v2"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ORIGINAL_CATALOG_IDS = tuple(f"E{i:03d}" for i in range(1, 211))


class RefResolutionError(ValueError):
    """An unresolved/mismatched documentary ref never confers authority."""


def canonical(value):
    # JSON types, signed zero and original string contents are retained.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def raw_member_ref(binding):
    return {"source_sha": binding["source_sha"], "source_tree": binding["source_tree"],
            "path": binding["path"], "blob": binding["blob"], "git_mode": binding["git_mode"],
            "sha256": binding["sha256"], "bytes": binding["bytes"]}


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
                    "compressed_sha256": sha256(data), "compressed_bytes": len(data),
                    "uncompressed_sha256": sha256(decoded), "uncompressed_bytes": len(decoded)}
    elif path.endswith(".xml"):
        if len(data) > 32 * 1024 * 1024:
            raise RefResolutionError("XML exceeds32MiB metadata bound")
        decoded = data
        encoding = {"encoding": "identity-XML", "compressed_sha256": sha256(data), "compressed_bytes": len(data),
                    "uncompressed_sha256": sha256(data), "uncompressed_bytes": len(data)}
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


class RefResolver:
    """Verify immutable Git RAW members and local derived payload references.

    RAW members remain available through the source repository's object database.
    A distribution without those objects must package the exact referenced RAW
    members and a separately verified source inventory before it can resolve
    them. Absence is an error, never a PASS or a final-candidate attestation.
    """
    def __init__(self, source, document):
        self.source = source
        self.document = document
        self.tables = document.get("derived_payload_tables", {})
        self.member_cache = {}
        self.active = set()
        self.resolved_refs = 0
        self.verified_members = set()

    def member(self, ref):
        required = {"source_sha", "source_tree", "path", "blob", "git_mode", "sha256", "bytes"}
        if not isinstance(ref, dict) or set(ref) != required:
            raise RefResolutionError("RAW member ref has unknown/missing fields")
        for key in ("source_sha", "source_tree", "blob"):
            if not isinstance(ref[key], str) or not HEX40.fullmatch(ref[key]):
                raise RefResolutionError("RAW member ref requires full immutable Git identities")
        if not isinstance(ref["sha256"], str) or not HEX64.fullmatch(ref["sha256"]):
            raise RefResolutionError("RAW member SHA256 is not full typed hex")
        path = ref["path"]
        if not isinstance(path, str) or not path or path.startswith("/") or "\0" in path or "\\" in path or any(part in ("", ".", "..") for part in path.split("/")):
            raise RefResolutionError("RAW member path is not an exact relative Git member")
        if ref["git_mode"] not in ("100644", "100755") or type(ref["bytes"]) is not int or not 0 <= ref["bytes"] <= 64 * 1024 * 1024:
            raise RefResolutionError("RAW member mode/length is invalid")
        key = (ref["source_sha"], ref["path"], ref["blob"], ref["sha256"], ref["git_mode"], ref["bytes"], ref["source_tree"])
        if key not in self.member_cache:
            try:
                data, binding = self.source.historical(ref["source_sha"], path)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                raise RefResolutionError("immutable RAW member unavailable or invalid: " + path) from error
            if raw_member_ref(binding) != ref:
                raise RefResolutionError("RAW source/blob/mode/hash/length binding differs from immutable Git")
            self.member_cache[key] = data
            self.verified_members.add(key)
        return self.member_cache[key]

    def resolve(self, value, depth=0):
        if depth > 64:
            raise RefResolutionError("derived reference graph exceeds64levels")
        if isinstance(value, dict) and set(value) == {"$rc6_ref"}:
            ref = value["$rc6_ref"]
            if not isinstance(ref, dict) or ref.get("schema") != REF_SCHEMA:
                raise RefResolutionError("unknown documentary reference schema")
            if not isinstance(ref.get("value_sha256"), str) or not HEX64.fullmatch(ref["value_sha256"]):
                raise RefResolutionError("derived value hash is not full SHA256")
            if ref.get("new_execution_claimed") is not False:
                raise RefResolutionError("derived ref cannot claim new execution")
            kind = ref.get("kind")
            if kind == "local-payload":
                expected = {"schema", "kind", "table", "key", "value_sha256", "new_execution_claimed"}
                if set(ref) != expected or not isinstance(ref["table"], str) or not isinstance(ref["key"], str):
                    raise RefResolutionError("local ref has unknown/missing fields")
                identity = (ref["table"], ref["key"])
                if identity in self.active:
                    raise RefResolutionError("cycle in derived reference graph")
                if ref["table"] == "original_catalog_entries":
                    allowed = self.document.get("original_catalog_preservation", {}).get("ids", [])
                    table = {identifier: self.document["evidence_catalog"][identifier] for identifier in allowed}
                else:
                    table = self.tables.get(ref["table"])
                if not isinstance(table, dict) or ref["key"] not in table:
                    raise RefResolutionError("derived payload table/key is absent")
                raw = table[ref["key"]]
                if sha256(canonical(raw)) != ref["value_sha256"]:
                    raise RefResolutionError("derived payload hash mismatch")
                self.active.add(identity)
                try:
                    # Referenced original objects are still literal data, even
                    # when the historical object includes a ref-shaped field.
                    result = copy.deepcopy(raw) if ref["table"] == "original_catalog_entries" else self.resolve(raw, depth + 1)
                finally:
                    self.active.remove(identity)
                # Source-file bindings are also verified as actual immutable RAW
                # members, not merely a matching hash inside an unsigned JSON.
                if isinstance(result, dict) and all(k in result for k in ("source_sha", "source_tree", "path", "blob", "git_mode", "sha256", "bytes")):
                    self.member(raw_member_ref(result))
            elif kind in ("raw-json-metadata", "raw-xml-metadata"):
                expected = {"schema", "kind", "member", "value_sha256", "new_execution_claimed"}
                if kind == "raw-xml-metadata":
                    expected |= {"node_resolution_source_sha", "node_resolution_source_tree", "xml_encoding"}
                if set(ref) != expected:
                    raise RefResolutionError("RAW metadata ref has unknown/missing fields")
                data = self.member(ref["member"])
                if kind == "raw-json-metadata":
                    result = strict_json(data)
                else:
                    if ref["node_resolution_source_sha"] != self.source.sha or ref["node_resolution_source_tree"] != self.source.tree:
                        raise RefResolutionError("XML node resolution cannot borrow another source snapshot")
                    decoded, encoding = decode_known_xml(data, ref["member"]["path"])
                    if canonical(encoding) != canonical(ref["xml_encoding"]):
                        raise RefResolutionError("XML compressed/uncompressed identity or encoding was changed")
                    result = xml_metadata_from_raw(decoded, self.source.entries)
                if sha256(canonical(result)) != ref["value_sha256"]:
                    raise RefResolutionError("resolved RAW metadata differs from the captured exact value")
                # RAW receipt contents are literal data; never interpret a ref-shaped
                # object within a receipt as a new control/reference instruction.
            else:
                raise RefResolutionError("unknown documentary reference kind")
            self.resolved_refs += 1
            return result
        if isinstance(value, dict):
            return {key: self.resolve(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, depth + 1) for item in value]
        return copy.deepcopy(value)

    def expand(self):
        if self.document.get("schema") != COMPACT_SCHEMA:
            raise RefResolutionError("resolver requires compact register v2")
        snapshot = self.document.get("source_snapshot", {})
        if snapshot.get("sha") != self.source.sha or snapshot.get("tree") != self.source.tree:
            raise RefResolutionError("compact document/source SHA/tree mismatch")
        if not isinstance(self.tables, dict) or set(self.tables) != {"source_file_bindings", "derived_execution_lists"}:
            raise RefResolutionError("unknown/missing derived payload table schema")
        # Validate the entire derived graph, including an orphan payload. Original
        # receipt/catalog contents remain literal and cannot become ref commands.
        for table_name, table in self.tables.items():
            if not isinstance(table, dict):
                raise RefResolutionError("derived table is not an object")
            for key, payload in table.items():
                if not isinstance(key, str) or not HEX64.fullmatch(key) or sha256(canonical(payload)) != key:
                    raise RefResolutionError("derived table payload key/hash mismatch")
                self.resolve(payload)
                if table_name == "source_file_bindings" and isinstance(payload, dict) and all(k in payload for k in ("source_sha", "source_tree", "path", "blob", "git_mode", "sha256", "bytes")):
                    self.member(raw_member_ref(payload))
        preservation = self.document.get("original_catalog_preservation", {})
        ids = preservation.get("ids")
        if not isinstance(ids, list) or ids != list(ORIGINAL_CATALOG_IDS):
            raise RefResolutionError("original210 catalog identity/preservation metadata is incomplete")
        catalog = self.document.get("evidence_catalog", {})
        if not all(identifier in catalog for identifier in ids):
            raise RefResolutionError("original catalog object absent")
        original = {identifier: catalog[identifier] for identifier in ids}
        if sha256(canonical(original)) != preservation.get("objects_sha256"):
            raise RefResolutionError("original210 catalog fields were changed")
        origin = strict_json(self.member(preservation["input_register_member"]))
        origin_catalog = origin.get("evidence_catalog", {})
        if {identifier: origin_catalog.get(identifier) for identifier in ids} != original or sha256(canonical({identifier: origin_catalog.get(identifier) for identifier in ids})) != preservation["objects_sha256"]:
            raise RefResolutionError("original catalog differs from its immutable input register")
        # Keep all210 original objects literal. Only the explicitly derived parts
        # are interpreted/resolved; receipt data cannot become control syntax.
        result = {}
        ignored = {"derived_payload_tables", "current_evidence_derivation", "original_catalog_preservation", "derived_layout", "evidence_catalog"}
        for key, value in self.document.items():
            if key not in ignored:
                result[key] = self.resolve(value)
        result["evidence_catalog"] = copy.deepcopy(catalog)
        overlays = self.document.get("current_evidence_derivation", {})
        if not isinstance(overlays, dict) or set(overlays) - set(catalog):
            raise RefResolutionError("derived catalog overlay has an unknown evidence ID")
        for identifier, overlay in overlays.items():
            if not isinstance(overlay, dict):
                raise RefResolutionError("derived catalog overlay is not a field mapping")
            result["evidence_catalog"][identifier].update(self.resolve(overlay))
        result["schema"] = "rc6.remediation-register.v1"
        return result


def compact_derived(document, original_catalog, source):
    ids = sorted(original_catalog)
    if ids != list(ORIGINAL_CATALOG_IDS):
        raise RefResolutionError("compact layout requires the exact original210 catalog objects")
    result = copy.deepcopy(document)
    tables = {"source_file_bindings": {}, "derived_execution_lists": {}}

    def local(table, value):
        key = sha256(canonical(value))
        if key in tables[table] and canonical(tables[table][key]) != canonical(value):
            raise RefResolutionError("derived payload hash collision")
        tables[table][key] = copy.deepcopy(value)
        return {"$rc6_ref": {"schema": REF_SCHEMA, "kind": "local-payload", "table": table,
                              "key": key, "value_sha256": key, "new_execution_claimed": False}}

    def metadata_ref(entry, kind, value):
        binding = entry.get("current_publication_binding") or entry.get("historical_publication_binding")
        if not binding or binding.get("sha256") != entry["sha256"] or binding.get("bytes") != entry["bytes"]:
            raise RefResolutionError("RAW metadata has no exact available committed member binding")
        ref = {"schema": REF_SCHEMA, "kind": kind, "member": raw_member_ref(binding),
               "value_sha256": sha256(canonical(value)), "new_execution_claimed": False}
        if kind == "raw-xml-metadata":
            ref.update(node_resolution_source_sha=source.sha, node_resolution_source_tree=source.tree)
            data, _ = source.historical(binding["source_sha"], binding["path"])
            _, encoding = decode_known_xml(data, binding["path"])
            ref["xml_encoding"] = encoding
        return {"$rc6_ref": ref}

    compact_catalog = {}
    overlays = {}
    for identifier, entry in document["evidence_catalog"].items():
        if identifier in original_catalog:
            original = original_catalog[identifier]
            compact_catalog[identifier] = copy.deepcopy(original)
            current = {key: copy.deepcopy(value) for key, value in entry.items()
                       if key not in original or canonical(value) != canonical(original[key])}
        else:
            current = copy.deepcopy(entry)
            # New catalog headers remain independently inspectable without resolving
            # payloads. This does not change the older protected210 objects.
            compact_catalog[identifier] = {key: current.pop(key) for key in ("path", "sha256", "bytes", "source_scope", "binding_source_sha", "evidence_kind", "boundary", "independent_external_authentication") if key in current}
        for field, kind in (("original_raw_json_metadata", "raw-json-metadata"), ("original_raw_xml_metadata", "raw-xml-metadata")):
            if field in current:
                current[field] = metadata_ref(entry, kind, current[field])
        if identifier in original_catalog and "previous_publication_definition" in current and canonical(current["previous_publication_definition"]) == canonical(original_catalog[identifier]):
            value = current["previous_publication_definition"]
            current["previous_publication_definition"] = {"$rc6_ref": {"schema": REF_SCHEMA, "kind": "local-payload", "table": "original_catalog_entries", "key": identifier, "value_sha256": sha256(canonical(value)), "new_execution_claimed": False}}
        if current:
            overlays[identifier] = current
    result["evidence_catalog"] = compact_catalog
    result["current_evidence_derivation"] = overlays
    inventory = result.get("current_source_inventory", {})
    inventory["files"] = [local("source_file_bindings", value) for value in inventory.get("files", [])]
    for guard in inventory.get("guards", []):
        if "execution_evidence" in guard:
            guard["execution_evidence"] = local("derived_execution_lists", guard["execution_evidence"])
        if "committed_source_binding" in guard:
            guard["committed_source_binding"] = local("source_file_bindings", guard["committed_source_binding"])
    for evolution in result.get("current_path_evolution", {}).values():
        binding = evolution.get("committed_source_binding")
        if binding and "sha256" in binding:
            evolution["committed_source_binding"] = local("source_file_bindings", binding)
    # This field was newly introduced by the documentary rebind; preserve all
    # original per-clause variants/evidence arrays and only share that new binding.
    for rows_key in ("requirements", "additional_findings", "restored_controls"):
        for row in result.get(rows_key, []):
            for field in ("GUARD", "guard_bindings", "GUARD_cross_requirement_refs"):
                for guard in row.get(field, []):
                    if "committed_source_binding" in guard:
                        guard["committed_source_binding"] = local("source_file_bindings", guard["committed_source_binding"])
    # Original catalog entries are already inline; don't serialize a second copy.
    result["derived_payload_tables"] = tables
    result["original_catalog_preservation"] = {"ids": ids, "objects_sha256": sha256(canonical(original_catalog)),
        "input_register_member": raw_member_ref(source.binding(REGISTER_PATH)),
        "policy": "All210 original catalog objects/fields are unaltered. Only explicitly added derived copies are references."}
    result["schema"] = COMPACT_SCHEMA
    result["derived_layout"] = {"schema": "rc6.remediation-derived-layout.v1",
        "raw_members": "Immutable Git member refs bind full SHA/tree/blob/mode/SHA256/bytes. Missing members fail closed.",
        "scope": "DERIVED_DOCUMENTARY_REFS_ONLY_NO_NEW_EXECUTION_OR_UPSTREAM_OWNER_MEMBERSHIP",
        "rendering": "Resolve and verify refs before treating their contents as available; resolver does not certify native execution.",
        "legacy_expansion": "RefResolver.expand reconstructs v1 derived values; original catalog remains exact in compact v2."}
    return result


class Builder:
    def __init__(self, source):
        self.s = source
        self.matrix = source.json(MATRIX_PATH)
        input_template = source.json(REGISTER_PATH)
        if input_template.get("schema") == COMPACT_SCHEMA:
            ids = input_template["original_catalog_preservation"]["ids"]
            self.original_catalog = {identifier: copy.deepcopy(input_template["evidence_catalog"][identifier]) for identifier in ids}
            previous_source = Source(source.repo, input_template["source_snapshot"]["sha"])
            try:
                self.template = RefResolver(previous_source, input_template).expand()
            finally:
                previous_source.close()
        else:
            self.template = input_template
            original_ids = [f"E{i:03d}" for i in range(1, 211)]
            if not all(identifier in input_template["evidence_catalog"] for identifier in original_ids):
                raise ValueError("the protected original210 catalog is not complete")
            self.original_catalog = {identifier: copy.deepcopy(input_template["evidence_catalog"][identifier]) for identifier in original_ids}
        if self.template.get("schema") != "rc6.remediation-register.v1":
            raise ValueError("unsupported historical register schema")
        self.doc = copy.deepcopy(self.template)
        self.catalog = copy.deepcopy(self.doc["evidence_catalog"])
        self.catalog_by_bytes = {}
        self.values = {}
        self.executions = collections.defaultdict(list)
        self.issues = []
        self.asts = {}
        self.guard_cache = {}
        self.owner_rows = {}
        self.owner_data = {}
        self.definition_paths = {MATRIX_PATH, *OWNERS.values()}

    def issue(self, kind, **details):
        self.issues.append({"kind": kind, **details})

    def original_validation(self):
        pins = {}
        for filename, expected in ORIGINAL_PINS.items():
            path = BASE + filename
            binding = self.s.binding(path)
            if binding["sha256"] != expected:
                raise ValueError(f"original literal byte authority changed: {path}")
            pins[path] = binding
        rows = list(csv.DictReader(io.StringIO(self.s.read(CSV_PATH).decode("utf-8-sig"), newline="")))
        by_id = {row["id"]: row for row in rows}
        requirements = self.matrix["requirements"]
        if len(rows) != 55 or len(by_id) != 55 or len(requirements) != 55 or {r["id"] for r in requirements} != set(by_id):
            raise ValueError("original55 requirement ID/row authority is incomplete or duplicated")
        for requirement in requirements:
            if requirement["original_requirement"] != by_id[requirement["id"]]:
                raise ValueError("closure requirement differs from literal original registry")
        scenario_rows = self.s.json(SCENARIOS_PATH)["rows"]
        parsed_original_rows = []
        for line in self.s.read(BASE + "ISSUE469_REAUDITORIA_INDEPENDIENTE_466_C27DFD9.md").decode().splitlines():
            if not re.match(r"^\| R\d{2}\s", line):
                continue
            columns = [value.strip() for value in line.strip("|").split("|")]
            if len(columns) != 7:
                raise ValueError("original scenario markdown table has invalid columns")
            identifier, attack = columns[0].split(" ", 1)
            parsed_original_rows.append(dict(id=identifier, attack=attack, expected=columns[1],
                original_observed=columns[2], original_code_path=columns[3], original_test=columns[4],
                original_gap=columns[5], original_verdict=columns[6]))
        if scenario_rows != parsed_original_rows:
            raise ValueError("derived scenario JSON differs from byte-pinned original469 markdown")
        scenarios = {row["id"]: row for row in scenario_rows}
        if len(scenarios) != 80 or len(self.matrix["scenarios"]) != 80:
            raise ValueError("original80 scenario count changed")
        if {r["id"] for r in self.matrix["scenarios"]} != set(scenarios):
            raise ValueError("original80 scenario membership changed")
        for scenario in self.matrix["scenarios"]:
            if scenario["original_scenario"] != scenarios[scenario["id"]]:
                raise ValueError("closure scenario differs from pinned original record")
        fronts = {}
        sections = [
            ("F01", "ORIGINAL_RA_A_F01_F02.md", "## 6. Variantes obligatorias §7 — F-01", 35),
            ("F02", "ORIGINAL_RA_A_F01_F02.md", "## 7. Variantes obligatorias §8 — F-02", 20),
            ("F03F05", "ORIGINAL_RA_A_F03_F05.md", "## Matriz adversarial ejecutada", 35),
        ]
        for prefix, filename, heading, expected in sections:
            text = self.s.read(BASE + filename).decode()
            if text.count(heading) != 1:
                raise ValueError("original front heading is missing or ambiguous")
            section = text.split(heading, 1)[1].split("\n## ", 1)[0]
            local = []
            for line in section.splitlines():
                if not line.startswith("| "):
                    continue
                columns = [v.strip() for v in line.strip("|").split("|")]
                if prefix != "F03F05" and not columns[0].isdigit():
                    continue
                if prefix == "F03F05" and columns[0] == "Escenario":
                    continue
                local.append(columns)
            if len(local) != expected:
                raise ValueError("literal original front section count changed")
            fronts.update({f"{prefix}-{i:02d}": row for i, row in enumerate(local, 1)})
        if len(self.matrix["front_variants"]) != 90 or {r["id"] for r in self.matrix["front_variants"]} != set(fronts):
            raise ValueError("original90 front membership changed")
        for front in self.matrix["front_variants"]:
            if front["original_columns"] != fronts[front["id"]]:
                raise ValueError("literal original90 clause changed")
        controls = self.matrix["restored_controls"]
        if len(controls) != 6 or len({r["id"] for r in controls}) != 6:
            raise ValueError("original six restored controls are incomplete or duplicated")
        historical_control_ids = {r["id"] for r in self.template["restored_controls"]}
        if {r["id"] for r in controls} != historical_control_ids:
            raise ValueError("restored-control identity differs from historical authority")
        self.original_pins = pins

    def xml_node(self, case):
        filename = case.get("file")
        name = case.get("name", "")
        if filename in self.s.entries:
            return filename + "::" + name
        parts = case.get("classname", "").split(".")
        for end in range(len(parts), 0, -1):
            candidate = "/".join(parts[:end]) + ".py"
            if candidate in self.s.entries:
                suffix = "::".join(parts[end:])
                return candidate + "::" + (suffix + "::" if suffix else "") + name
        return None

    def metadata(self, eid, entry, data):
        path = entry["path"]
        if path.endswith(".json"):
            try:
                obj = strict_json(data)
            except (ValueError, UnicodeError) as error:
                self.issue("RAW_JSON_METADATA_UNREADABLE", evidence_id=eid, path=path, reason=str(error))
                return
            self.values[eid] = obj
            # Full raw metadata preserves OWN source/env/command/count and any new fields.
            # Reviewed compact metadata is retained separately and never overwritten.
            entry["original_raw_json_metadata"] = obj
            entry["raw_json_metadata_policy"] = "VERBATIM_FROM_HASHED_COMMITTED_RECEIPT_NO_SOURCE_SCOPE_OR_COUNT_REWRITE"
        elif path.endswith((".xml", ".xml.gz")):
            try:
                decoded, encoding = decode_known_xml(data, path)
                root = ET.fromstring(decoded)
            except (ET.ParseError, RefResolutionError) as error:
                self.issue("RAW_XML_METADATA_UNREADABLE", evidence_id=eid, path=path, reason=str(error))
                return
            entry["raw_encoding_metadata"] = encoding
            suites = [dict(node.attrib) for node in root.iter("testsuite")]
            cases = []
            for case in root.iter("testcase"):
                node = self.xml_node(case)
                status = "FAIL" if case.find("failure") is not None else "ERROR" if case.find("error") is not None else "SKIP" if case.find("skipped") is not None else "PASS"
                item = {"node": node, "raw_classname": case.get("classname"), "raw_name": case.get("name"),
                        "recorded_status": status, "seconds": case.get("time")}
                cases.append(item)
                if node:
                    self.executions[node_base(node)].append({"evidence_id": eid, "node": node,
                        "recorded_status": status, "evidence_class": "RAW_JUNIT_RECEIPT_LOCAL_EXECUTION"})
            entry["original_raw_xml_metadata"] = {"root_tag": root.tag, "root_attributes": dict(root.attrib),
                "suites": suites, "cases": cases, "actual_case_count": len(cases),
                "actual_case_outcomes": dict(collections.Counter(c["recorded_status"] for c in cases)),
                "source_binding_not_inferred_from_this_rebind": True}
            entry["counting_boundary"] = "Each RAW receipt retains its own exact case scope; no sums across receipts, independent-attack or current-source execution inference."

    def catalog_seed(self):
        original_entries = list(self.catalog.items())
        for eid, entry in original_entries:
            path = entry["path"]
            expected = entry["sha256"]
            self.catalog_by_bytes[(path, expected)] = eid
            if path in self.s.entries:
                current = self.s.read(path)
                if sha256(current) == expected:
                    entry["current_publication_binding"] = self.s.binding(path)
                    entry["raw_bytes_verified_from_committed_git"] = True
                    self.metadata(eid, entry, current)
                    continue
                if path in self.definition_paths or "MAPPING" in entry.get("evidence_kind", ""):
                    previous = copy.deepcopy(entry)
                    entry.clear()
                    entry.update({"path": path, "sha256": sha256(current), "bytes": len(current),
                        "source_scope": "ROOT_COMMITTED_SNAPSHOT", "binding_source_sha": self.s.sha,
                        "evidence_kind": "DERIVED_CURRENT_SOURCE_DEFINITION_NOT_EXECUTION",
                        "boundary": BOUNDARY, "previous_publication_definition": previous,
                        "current_publication_binding": self.s.binding(path),
                        "raw_bytes_verified_from_committed_git": True,
                        "independent_external_authentication": False})
                    self.catalog_by_bytes[(path, sha256(current))] = eid
                    self.metadata(eid, entry, current)
                    continue
            if path.startswith("/"):
                # Read no /tmp or owner worktree bytes. The prior committed record is
                # documentary evidence; an identical published alias can supply bytes.
                entry["rebind_verification_scope"] = "PREVIOUS_COMMITTED_REGISTER_RECORD_ONLY_NO_LOCAL_BYTES_READ"
                entry["raw_bytes_verified_from_committed_git"] = False
                continue
            try:
                data, binding = self.s.historical(entry.get("binding_source_sha"), path)
                if sha256(data) != expected or len(data) != entry["bytes"]:
                    raise ValueError("historical raw bytes differ from original register hash/length")
                entry["historical_publication_binding"] = binding
                entry["raw_bytes_verified_from_committed_git"] = True
                self.metadata(eid, entry, data)
            except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as error:
                entry["raw_bytes_verified_from_committed_git"] = False
                self.issue("HISTORICAL_RECEIPT_BYTES_NOT_AVAILABLE", evidence_id=eid, path=path, reason=str(error))

    def evidence(self, path, kind="INTERMEDIATE_COMMITTED_OWNER_EVIDENCE_NOT_FINAL", expected=None):
        if path == REGISTER_PATH:
            raise ValueError("the register must not include its own hash as authority")
        if path not in self.s.entries:
            self.issue("CURRENT_DECLARED_EVIDENCE_NOT_PUBLISHED", path=path)
            return None
        data = self.s.read(path)
        digest = sha256(data)
        if expected and expected != digest:
            self.issue("CURRENT_DECLARED_RECEIPT_HASH_MISMATCH", path=path, expected=expected, actual=digest)
        key = (path, digest)
        if key in self.catalog_by_bytes:
            return self.catalog_by_bytes[key]
        number = max((int(k[1:]) for k in self.catalog if re.fullmatch(r"E[0-9]+", k)), default=0) + 1
        eid = f"E{number:03d}"
        entry = {"path": path, "sha256": digest, "bytes": len(data),
            "source_scope": "ROOT_COMMITTED_SNAPSHOT", "binding_source_sha": self.s.sha,
            "evidence_kind": kind, "boundary": BOUNDARY,
            "capture_source_scope_policy": "Original raw receipt fields govern; publication source is not an execution-source rewrite.",
            "current_publication_binding": self.s.binding(path),
            "raw_bytes_verified_from_committed_git": True,
            "independent_external_authentication": False}
        self.catalog[eid] = entry
        self.catalog_by_bytes[key] = eid
        self.metadata(eid, entry, data)
        return eid

    def load_evidence(self):
        self.catalog_seed()
        for path in sorted(self.s.entries):
            if path != REGISTER_PATH and path.startswith(EVIDENCE_DIRS) and path.endswith((".json", ".xml", ".xml.gz", ".txt", ".log", ".py")):
                self.evidence(path)
        for path in [MATRIX_PATH, CSV_PATH, SCENARIOS_PATH, BASE + "ORIGINAL_INPUT_DIGESTS.json", *(BASE + p for p in ORIGINAL_PINS)]:
            self.evidence(path, "PINNED_OR_CURRENT_DEFINITION_NOT_EXECUTION")
        for owner, path in OWNERS.items():
            if path not in self.s.entries:
                self.issue("OWNER_MAPPING_NOT_PUBLISHED", owner=owner, path=path)
                continue
            self.evidence(path, "OWNER_REQUIREMENT_MAPPING_NOT_FINAL_ATTESTATION")
            obj = self.s.json(path)
            self.owner_data[owner] = obj
            key = {"BUDGET": "findings", "FINANCE": "requirements", "HISTORY": "findings",
                   "UX": "entries", "PERSISTENCE": "programming_requirements"}.get(owner)
            if key:
                rows = obj[key]
                indexed = {r["id"]: r for r in rows}
                if len(indexed) != len(rows):
                    self.issue("OWNER_MAPPING_DUPLICATE_IDS", owner=owner, path=path)
                self.owner_rows[owner] = indexed
            else:
                self.owner_rows[owner] = obj["guard_mapping"]["mapping"]
        for row in self.matrix["additional_findings"] + self.matrix["restored_controls"]:
            for field in ("owner_mapping", "evidence_receipt"):
                path = row.get(field)
                if path:
                    self.evidence(path, "CURRENT_DECLARED_MAPPING_OR_RECEIPT_NOT_FINAL")
        for filename, expected in self.owner_data.get("FINANCE", {}).get("receipt_hashes", {}).items():
            self.evidence("docs/audits/rc6_convergence_finance/" + filename, expected=expected)
        for receipt in self.owner_data.get("BUDGET", {}).get("receipts", []):
            self.evidence(receipt["path"], expected=receipt["sha256"])
        # Previously local entries may now have byte-identical committed publications.
        by_sha = collections.defaultdict(list)
        for eid, entry in self.catalog.items():
            if entry.get("raw_bytes_verified_from_committed_git"):
                by_sha[(entry["sha256"], entry["bytes"])].append(eid)
        for eid, entry in self.catalog.items():
            if entry["path"].startswith("/"):
                matches = [x for x in by_sha.get((entry["sha256"], entry["bytes"]), []) if x != eid]
                if matches:
                    entry["byte_identical_published_evidence_ids"] = matches
                    entry["rebind_verification_scope"] = "ORIGINAL_LOCAL_RECORD_RETAINED_WITH_IDENTICAL_COMMITTED_ALIAS"

    def bound_file(self, path):
        try:
            return self.s.binding(path)
        except FileNotFoundError as error:
            self.issue("CURRENT_CODE_PATH_ABSENT", path=path, reason=str(error))
            return {"path": path, "source_sha": self.s.sha, "source_tree": self.s.tree,
                    "binding_status": "NOT_IN_COMMITTED_SOURCE", "workingtree_bytes_used": False}

    def guard(self, node, prior=None):
        base = node_base(node)
        if base not in self.guard_cache:
            path, *parts = base.split("::")
            binding = self.bound_file(path)
            result = {"base_node": base, "path": path, "source_file_sha256": binding.get("sha256"),
                "committed_source_binding": binding, "ast_exists_in_source_snapshot": False,
                "current_source_execution_status": "FINAL_FROZEN_REPLAY_PENDING",
                "role_classification": "SEMANTIC_ROLE_NOT_INFERRED_FROM_AST_PRESENCE_PASS_OR_PARAMETER_NAME"}
            try:
                if path not in self.asts:
                    self.asts[path] = ast.parse(self.s.read(path), filename=path)
                children = self.asts[path].body
                selected = None
                for part in parts:
                    matches = [n for n in children if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part]
                    if len(matches) != 1:
                        raise ValueError(f"native AST declaration not unique: {base}:{part}")
                    selected = matches[0]
                    children = selected.body
                if not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    raise ValueError("node does not name a native test function")
                result.update({"ast_exists_in_source_snapshot": True, "line": selected.lineno,
                    "ast_sha256": sha256(ast.dump(selected, annotate_fields=True, include_attributes=False).encode()),
                    "ast_hash_python_version": sys.version.split()[0]})
            except (ValueError, FileNotFoundError, SyntaxError) as error:
                self.issue("NATIVE_GUARD_AST_NOT_UNIQUE_OR_ABSENT", node=base, reason=str(error))
            evidence = self.executions.get(base, [])
            result["execution_evidence"] = [{**{k: v for k, v in item.items() if k != "node"},
                                             "variant": item["node"][len(base):]} for item in evidence]
            result["explicit_variants"] = sorted({item["node"][len(base):] for item in evidence if item["node"] != base})
            result["receipt_execution_boundary"] = "Historical receipt-local evidence; no execution on this documentary source is claimed."
            self.guard_cache[base] = result
        result = copy.deepcopy(self.guard_cache[base])
        if prior:
            old = copy.deepcopy(prior)
            old_binding = {key: old[key] for key in ("path", "line", "source_file_sha256", "ast_sha256") if key in old}
            # Preserve reviewed clause-specific variants and receipt links. In particular
            # AUD20 Inf crossrefs must not borrow every parameter of the AUD10 template.
            old.update({k: v for k, v in result.items() if k not in ("execution_evidence", "explicit_variants")})
            old["historical_guard_definition_binding"] = old_binding
            old["historical_execution_evidence_preserved_without_current_source_relabel"] = True
            return old
        return result

    def requirement_rows(self):
        old_by_id = {row["id"]: row for row in self.template["requirements"]}
        current = []
        for row in self.matrix["requirements"]:
            old = copy.deepcopy(old_by_id[row["id"]])
            old["ERROR"] = copy.deepcopy(row["original_requirement"])
            old["status"] = row["disposition"]
            old["status_authority"] = "CURRENT_ROOT_CLOSURE_DISPOSITION_NOT_NEW_TEST_EXECUTION"
            old["RCA"]["canonical_root"] = row["rca"]
            previous_fix = old["FIX"].get("files", [])
            old["FIX"].update({"canonical_description": row["fix"],
                "files": [self.bound_file(path) for path in row["code_paths"]],
                "previous_documentary_file_bindings": previous_fix,
                "source_rebind_does_not_invent_implementation_or_execution_commit": True})
            previous_guards = {guard["base_node"]: guard for guard in old["GUARD"]}
            unique_nodes = sorted({node_base(node) for node in row["test_nodes"]})
            old["GUARD"] = [self.guard(node, previous_guards.get(node)) for node in unique_nodes]
            retired = [guard for base, guard in previous_guards.items() if base not in unique_nodes]
            if retired:
                old["historical_guard_bindings_not_current_closure"] = retired
            if old.get("GUARD_cross_requirement_refs"):
                old["GUARD_cross_requirement_refs"] = [self.guard(guard["base_node"], guard)
                                                      for guard in old["GUARD_cross_requirement_refs"]]
            old["TEST"].update({"canonical_caller": row["native_caller"],
                "canonical_caller_source_sha": self.s.sha,
                "final_execution": "PENDING_EXACT_FROZEN_SOURCE_AND_ARTIFACT",
                "historical_receipt_fields_are_not_reexecuted_by_this_generator": True})
            owner = row["workstream"]
            path = row.get("owner_mapping") or OWNERS.get(owner)
            mapping = {"declared_canonical_path": path, "actual_path": path,
                       "source_scope": "NO_SEPARATE_OWNER_MAP" if not path else "ROOT_COMMITTED_SNAPSHOT"}
            if path:
                mapping.update({"evidence_id": self.evidence(path), "committed_source_binding": self.bound_file(path)})
                owner_row = self.owner_rows.get(owner, {}).get(row["id"])
                mapping["original_id_present_in_owner_index"] = owner_row is not None
                if owner_row is None:
                    self.issue("ORIGINAL_REQUIREMENT_ID_ABSENT_FROM_OWNER_INDEX", id=row["id"], owner=owner, path=path)
            else:
                mapping["evidence_id"] = None
            old["EVIDENCE"].update({"owner": owner, "current_owner_mapping": mapping,
                "current_remaining_uncertainty": row["remaining_uncertainty"],
                "prior_raw_receipt_source_env_count_scope_preserved": True})
            old["linked_additional_finding_ids"] = [x["id"] for x in self.matrix["additional_findings"]
                                                   if row["id"] in x.get("linked_requirement_ids", [])]
            current.append(old)
        self.doc["requirements"] = current

    def finding_rows(self):
        rows = []
        identifiers = set()
        originals = {r["id"] for r in self.matrix["requirements"]}
        for finding in self.matrix["additional_findings"]:
            row = copy.deepcopy(finding)
            if row["id"] in identifiers:
                raise ValueError("duplicate additional finding ID")
            identifiers.add(row["id"])
            if not row.get("linked_requirement_ids") or not set(row["linked_requirement_ids"]) <= originals:
                raise ValueError("additional finding has no valid original parents")
            row["counts_as_new_original_requirement_or_R_scenario"] = False
            row["guard_bindings"] = [self.guard(node) for node in sorted({node_base(n) for n in row["test_nodes"]})]
            row["current_fix_bindings"] = [self.bound_file(path) for path in row["code_paths"]]
            owner_path = row.get("owner_mapping")
            row["evidence_reference"] = {"path": row.get("evidence_receipt") or owner_path}
            if row["evidence_reference"]["path"]:
                row["evidence_reference"]["evidence_id"] = self.evidence(row["evidence_reference"]["path"])
            owner = row["workstream"]
            indexed = self.owner_rows.get(owner, {})
            previous = next((x for x in self.template["additional_findings"] if x["id"] == row["id"]), {})
            previous_membership = previous.get("owner_index_membership_status", "")
            row["historical_previous_owner_membership_status"] = previous_membership or None
            named_prefix = "OWNER_NAMED_FOLLOWUP_SECTION_VERIFIED:"
            if row.get("ownership_scope") == "ROOT_INTEGRATION_SUPPLEMENTAL_NAMED_FINDING_BINDINGS":
                supplement = self.s.json(owner_path)
                if supplement.get("schema") != "rc6.root-supplemental-named-finding-bindings.v1":
                    raise ValueError("ROOT supplemental ownership requires its exact typed schema")
                declared = supplement.get("bindings")
                if not isinstance(declared, list):
                    raise ValueError("ROOT supplemental bindings must be a list")
                candidates = [item for item in declared if item.get("id") == row["id"]]
                if len(candidates) != 1 or len({item.get("id") for item in declared}) != len(declared):
                    raise ValueError("ROOT named finding binding is absent/duplicated")
                bound = candidates[0]
                if bound.get("exact_root_named_membership") is not True or bound.get("exact_upstream_owner_membership") is not False:
                    raise ValueError("ROOT supplementary membership must be True with upstream ownership False")
                if bound.get("ownership_scope") != "ROOT_INTEGRATION_SUPPLEMENTAL_NAMED_FINDING_BINDINGS" or bound.get("root_binding_owner") != "ROOT_INTEGRATION":
                    raise ValueError("ROOT supplementary binding has another owner/scope")
                if bound.get("linked_requirement_ids") != row["linked_requirement_ids"]:
                    raise ValueError("ROOT supplementary binding cannot change original parent IDs")
                original_rows = {item["id"]: item["original_requirement"] for item in self.matrix["requirements"]}
                if bound.get("original_parent_clauses") != [original_rows[parent] for parent in row["linked_requirement_ids"]]:
                    raise ValueError("ROOT supplementary original clauses differ from literal authority")
                if [guard.get("pytest_node") for guard in bound.get("current_native_guards", [])] != row["test_nodes"]:
                    raise ValueError("ROOT supplementary guard list differs from current native closure")
                code = bound.get("current_code_bindings", [])
                if [item.get("path") for item in code] != row["code_paths"]:
                    raise ValueError("ROOT supplementary code path list differs from current closure")
                resolver = RefResolver(self.s, {})
                for binding in code + [item["source_binding"] for item in bound["current_native_guards"]]:
                    resolver.member(raw_member_ref(binding))
                    current_binding = self.s.binding(binding["path"])
                    if any(current_binding[key] != binding[key] for key in ("blob", "git_mode", "sha256", "bytes")):
                        raise ValueError("ROOT supplementary binding became stale after source advance")
                for guard in bound["current_native_guards"]:
                    current_guard = self.guard(guard["pytest_node"])
                    if guard.get("ast_sha256") != current_guard.get("ast_sha256") or not current_guard["ast_exists_in_source_snapshot"]:
                        raise ValueError("ROOT supplementary native AST binding is stale/absent")
                upstream = bound.get("upstream_owner_mapping", {})
                old_binding = upstream.get("source_binding", {})
                upstream_bytes = resolver.member(raw_member_ref(old_binding))
                upstream_data = strict_json(upstream_bytes)
                upstream_ids = set()
                for key in ("programming_requirements", "findings", "requirements", "entries"):
                    if isinstance(upstream_data.get(key), list):
                        upstream_ids.update(item["id"] for item in upstream_data[key])
                upstream_ids.update(upstream_data.get("guard_mapping", {}).get("mapping", {}))
                if upstream.get("exact_new_finding_id_present_in_upstream_typed_index") is not False:
                    raise ValueError("ROOT supplementary binding cannot invent an upstream finding ID")
                if row["id"] in upstream_ids:
                    raise ValueError("supplemental upstream=False differs from actual original owner index")
                actual_receipt = bound.get("actual_receipt", {})
                receipt_binding = actual_receipt.get("publication_binding", {})
                receipt_bytes = resolver.member(raw_member_ref(receipt_binding))
                if strict_json(receipt_bytes) != actual_receipt.get("original_raw_metadata_verbatim") or sha256(canonical(strict_json(receipt_bytes))) != sha256(canonical(actual_receipt.get("original_raw_metadata_verbatim"))):
                    raise ValueError("ROOT supplemental RAW receipt was altered/relabelled")
                receipt_source = Source(self.s.repo, receipt_binding["source_sha"])
                try:
                    expected_xml_members = declared_xml_receipt_members(receipt_source, strict_json(receipt_bytes))
                finally:
                    receipt_source.close()
                if canonical(actual_receipt.get("declared_xml_receipt_members", [])) != canonical(expected_xml_members):
                    raise ValueError("ROOT supplemental XML/gzip members or native receipt scope were altered/unavailable")
                row.update(owner_index_membership_status="EXACT_ROOT_NAMED_SUPPLEMENTAL_MEMBERSHIP_NOT_UPSTREAM_OWNER",
                           exact_root_named_membership=True, exact_upstream_owner_membership=False,
                           old_owner_mapping=old_binding["path"], affected_original_workstream=bound.get("original_workstream"),
                           original_raw_receipt_scope_preserved=True)
            elif row.get("ownership_scope") == "ROOT_INTEGRATION_SUPPLEMENTAL_SOURCE_MAPPING":
                row["owner_index_membership_status"] = "EXPLICIT_ROOT_SUPPLEMENTAL_MAPPING_RECEIPT_NOT_AN_OLD_OWNER_MATRIX_ID"
            elif row["id"] in indexed:
                row["owner_index_membership_status"] = "EXACT_FINDING_ID_PRESENT_IN_COMMITTED_OWNER_INDEX"
            elif previous_membership.startswith(named_prefix):
                section_name = previous_membership[len(named_prefix):]
                section = self.owner_data.get(owner, {}).get(section_name)
                if not isinstance(section, dict) or not set(row["linked_requirement_ids"]) <= set(section.get("linked_requirement_ids", [])):
                    row["owner_index_membership_status"] = "PREVIOUS_NAMED_OWNER_SECTION_NO_LONGER_VERIFIABLE"
                    self.issue("PREVIOUS_NAMED_OWNER_SECTION_CHANGED_OR_ABSENT", id=row["id"], path=owner_path, section=section_name)
                else:
                    row["owner_index_membership_status"] = "NAMED_OWNER_FOLLOWUP_SECTION_PRESENT_WITH_EXACT_ORIGINAL_PARENTS"
                    row["owner_named_section"] = section_name
                    owner_nodes = {node_base(n) for n in section.get("test_nodes", [])}
                    row["root_supplemental_guard_nodes_outside_named_owner_section"] = [n for n in row["test_nodes"] if node_base(n) not in owner_nodes]
                    row["owner_named_section_boundary"] = "Named followup is preserved; additional ROOT controller guards belong to ROOT closure, not an invented owner finding ID."
            elif owner_path == OWNERS.get(owner):
                parent_ids = [parent for parent in row["linked_requirement_ids"] if parent in indexed]
                row["owner_index_membership_status"] = "ROOT_NAMED_FINDING_WITH_PARENT_OWNER_INDEX_ONLY"
                row["parent_owner_ids_present"] = parent_ids
                self.issue("ADDITIONAL_FINDING_HAS_PARENT_MAPPING_ONLY", id=row["id"], path=owner_path,
                           present_parent_ids=parent_ids, severity="DOCUMENTARY_OWNER_MEMBERSHIP_GAP_NOT_PRODUCT_FAILURE")
            else:
                row["owner_index_membership_status"] = "ROOT_DECLARED_SUPPLEMENTAL_RECEIPT_NOT_A_TYPED_OWNER_INDEX"
                if not owner_path or owner_path not in self.s.entries:
                    self.issue("ADDITIONAL_OWNER_MAPPING_UNAVAILABLE", id=row["id"], path=owner_path)
            rows.append(row)
        self.doc["additional_findings"] = rows
        self.doc["restored_controls"] = [
            {**copy.deepcopy(row), "counts_as_original_requirement_or_new_R_scenario": False,
             "guard_bindings": [self.guard(node) for node in sorted({node_base(n) for n in row["test_nodes"]})]}
            for row in self.matrix["restored_controls"]]
        self.doc["original_scenario_index"] = [
            {"id": row["id"], "original_scenario": copy.deepcopy(row["original_scenario"]),
             "guard_base_nodes": sorted({node_base(n) for n in row["test_nodes"]}),
             "execution_status": row.get("execution_status", "NOT_DECLARED_IN_CLOSURE"),
             "current_definition_disposition": row.get("disposition"),
             "scope": "Original clause mapping and historical evidence; not a new governed run."}
            for row in self.matrix["scenarios"]]
        self.doc["original_front_index"] = [
            {"id": row["id"], "original_columns": copy.deepcopy(row["original_columns"]),
             "linked_requirement_ids": copy.deepcopy(row["linked_requirement_ids"]),
             "evidence_boundary": row.get("evidence_boundary"),
             "guard_base_nodes": sorted({node_base(n) for n in row["test_nodes"]}),
             "execution_status": "PENDING_FINAL_FROZEN_NATIVE_EXECUTION"}
            for row in self.matrix["front_variants"]]

    def build(self):
        self.original_validation()
        self.load_evidence()
        self.requirement_rows()
        self.finding_rows()
        all_rows = [*self.matrix["requirements"], *self.matrix["scenarios"], *self.matrix["front_variants"],
                    *self.matrix["restored_controls"], *self.matrix["additional_findings"]]
        for row in all_rows:
            for node in row["test_nodes"]:
                self.guard(node)
            for path in row["code_paths"]:
                self.bound_file(path)
        evolution = {}
        for path, definition in self.matrix["path_evolution"].items():
            if path == REGISTER_PATH:
                # The old register is a historical input at S, not the future output D.
                # Its output hash is deliberately absent from this document.
                mode, blob = self.s.entries[path]
                binding = {"path": path, "blob": blob, "git_mode": mode,
                           "source_sha": self.s.sha, "source_tree": self.s.tree,
                           "role": "PREVIOUS_COMMITTED_TEMPLATE_AT_S_NOT_THIS_OUTPUT",
                           "current_output_hash_included": False}
            else:
                binding = self.bound_file(path)
            evolution[path] = {"closure_definition": copy.deepcopy(definition), "committed_source_binding": binding}
        source_snapshot = {
            "root_path": str(self.s.repo), "sha": self.s.sha, "tree": self.s.tree,
            "scope": "ROOT_COMMITTED_SNAPSHOT_NOT_FINAL_RELEASE",
            "selection_method": "Explicit full SHA; git --no-replace-objects; raw committed regular blobs and modes; no workingtree or local overlays.",
            "root_head_at_start": self.s.head_at_start, "root_worktree_status_at_start": self.s.status_at_start,
            "root_head_at_end": self.s.git("rev-parse", "HEAD").decode().strip(),
            "root_worktree_status_at_end": self.s.status(),
            "requires_rebinding_if_root_advances": True, "register_self_hash_included": False,
            "final_frozen_source_sha": None, "final_frozen_tree_sha": None,
            "final_fip_digest": None, "final_governed_junit_digest": None, "final_artifact_digest": None,
            "independent_external_anchor": None, "rebind_needed_before_final_freeze": True,
            "document_source_freeze_order": {
                "source_S": self.s.sha, "source_tree_T": self.s.tree,
                "future_document_commit_D": None, "future_freeze_commit_C": None,
                "order": "Pin source S/tree T; document commit D references S; freeze C after D; actual Gov/FIP/image/artifact bind C. Any subsequent source advance requires a new rebind and freeze.",
                "receipts_keep_original_OWN_source_environment_scope_and_counts": True,
            },
        }
        source_snapshot["head_advanced_since_pinned_snapshot"] = source_snapshot["root_head_at_end"] != self.s.sha
        prior_review = copy.deepcopy(self.template.get("independent_readonly_review"))
        prior_material = copy.deepcopy(self.template.get("material_pending_proof", []))
        self.doc.update({"schema": "rc6.remediation-register.v1", "status": "DOCUMENTARY_REBIND_GENERATED_NOT_EXECUTED",
            "generated_at_utc": None, "generation_time_policy": "No runtime timestamp is an execution or final attestation; immutable source controls this rebind.",
            "source_snapshot": source_snapshot, "evidence_catalog": self.catalog,
            "historical_material_pending_proof_from_previous_register": prior_material,
            "material_pending_proof": [{"id": row["id"], "disposition": row["disposition"],
                "remaining_uncertainty": row["remaining_uncertainty"], "source_sha": self.s.sha}
                for row in self.matrix["requirements"] if "MATERIAL" in row["disposition"]],
            "independent_readonly_review": {"result": "NOT_INDEPENDENTLY_REVIEWED_CURRENT_OUTPUT",
                "historical_previous_register_review": prior_review,
                "boundary": "Historical byte reviews are retained with their original source. They do not attest this output."},
        })
        self.doc["current_path_evolution"] = evolution
        self.doc["counting_rule"].update({"matrix_additional_findings": len(self.matrix["additional_findings"]),
            "execution_counts_are_not_summed": True, "current_source_tests_executed_by_generator": 0})
        self.doc["authority"]["literal_originals"] = self.original_pins
        requirement_guards = {node_base(n) for row in self.matrix["requirements"] for n in row["test_nodes"]}
        self.doc["validation"] = {
            "literal_original_rows": 55, "unique_original_ids": 55,
            "literal_error_and_closure_fields_equal_pinned_csv": True,
            "original_80_scenario_records_equal_pinned_json": True,
            "original_80_scenario_records_equal_byte_pinned_markdown": True,
            "original_90_front_columns_equal_pinned_markdown": True,
            "original_six_restored_control_ids_preserved": True,
            "additional_findings": len(self.matrix["additional_findings"]),
            "path_evolution_entries": len(self.matrix["path_evolution"]),
            "distinct_requirement_guard_bases": len(requirement_guards),
            "distinct_matrix_guard_bases_AST_checked": len(self.guard_cache),
            "canonical_guard_AST_present": all(x["ast_exists_in_source_snapshot"] for x in self.guard_cache.values()),
            "distinct_bound_committed_files": len(self.s.bindings),
            "evidence_catalog_entries": len(self.catalog),
            "committed_raw_evidence_verified": sum(bool(e.get("raw_bytes_verified_from_committed_git")) for e in self.catalog.values()),
            "previous_local_receipt_records_not_read": sum(e["path"].startswith("/") for e in self.catalog.values()),
            "issues": self.issues, "issues_by_kind": dict(collections.Counter(i["kind"] for i in self.issues)),
            "new_tests_for_document": 0, "does_not_execute_native_guards_or_certify_final_candidate": True,
            "workingtree_source_bytes_read": False, "local_receipt_bytes_read": False,
            "self_hash_included": False,
        }
        self.doc["current_source_inventory"] = {"schema": "rc6.remediation-source-inventory.v1",
            "source_sha": self.s.sha, "source_tree": self.s.tree,
            "guards": [self.guard_cache[n] for n in sorted(self.guard_cache)],
            "files": [self.s.bindings[p] for p in sorted(self.s.bindings)],
            "count_scope": "AST/file inventory only, not executed cases or economic proof."}
        return self.doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--layout", choices=("derived-ref", "expanded"), default="derived-ref",
                        help="Default: compact v2 with verified DERIVED refs; expanded v1 is an explicit diagnostic option.")
    parser.add_argument("--verify-refs", type=Path, metavar="EXTERNAL_JSON",
                        help="Only resolve/verify an existing external compact JSON; do not generate a register.")
    args = parser.parse_args(argv)
    source = None
    temporary = None
    try:
        source = Source(args.repo, args.source)
        if args.verify_refs:
            if args.out is not None:
                raise ValueError("--verify-refs cannot also generate --out")
            document_path = outside_output(source, args.verify_refs)
            if not document_path.is_file() or document_path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("compact verification input must be a regular external JSON≤32MiB")
            document = strict_json(document_path.read_bytes())
            resolver = RefResolver(source, document)
            resolver.expand()
            print(json.dumps({"status": "DERIVED_REFERENCES_VERIFIED_NOT_EXECUTED", "source_sha": source.sha,
                "source_tree": source.tree, "resolved_refs": resolver.resolved_refs,
                "verified_RAW_members": len(resolver.verified_members), "original_catalog_entries": 210,
                "native_tests_executed": 0, "governed_suite_executed": False}))
            return 0
        if args.out is None:
            raise ValueError("generation requires --out EXTERNAL_JSON")
        target = outside_output(source, args.out)
        builder = Builder(source)
        document = builder.build()
        if args.layout == "derived-ref":
            expanded_document = document
            document = compact_derived(expanded_document, builder.original_catalog, source)
            resolver = RefResolver(source, document)
            verified = resolver.expand()
            if canonical(verified) != canonical(expanded_document):
                raise RefResolutionError("compact derived layout does not exactly reconstruct expanded documentary values")
            document["validation"]["derived_reference_validation"] = {
                "schema": "rc6.remediation-reference-validation.v1", "scope": "GIT_JSON_XML_DOCUMENTARY_ONLY_NOT_NATIVE_EXECUTION",
                "resolved_refs": resolver.resolved_refs, "verified_RAW_members": len(resolver.verified_members),
                "original210_catalog_objects_exact": True, "expanded_documentary_values_equal": True,
                "status": "DERIVED_REFERENCES_VERIFIED_NOT_EXECUTED"}
        data = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        fd, temporary = tempfile.mkstemp(prefix=".rc6-remediation-register-", dir=str(target.parent))
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        outside_output(source, target)
        os.replace(temporary, target)
        temporary = None
        print(json.dumps({"status": document["status"], "source_sha": source.sha, "source_tree": source.tree,
            "out": str(target), "bytes": len(data), "sha256": sha256(data),
            "original_counts": {"requirements": 55, "scenarios": 80, "front_variants": 90, "restored_controls": 6},
            "additional_findings": len(builder.matrix["additional_findings"]),
            "path_evolution_entries": len(builder.matrix["path_evolution"]),
            "matrix_AST_bases": len(builder.guard_cache), "catalog_entries": len(builder.catalog),
            "layout": args.layout, "schema": document["schema"],
            "issues_by_kind": document["validation"]["issues_by_kind"], "tests_executed": 0}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "DOCUMENTARY_REBIND_REJECTED", "error": str(error), "tests_executed": 0}), file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            os.unlink(temporary)
        if source is not None:
            source.close()


if __name__ == "__main__":
    raise SystemExit(main())

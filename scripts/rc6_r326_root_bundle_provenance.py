"""R326 pure OFFHOST six-source provenance proposal, never an authority.

R252/R163 must authenticate D externally before constructing the boundary below.
Its frozen descriptor is an integration trust boundary, NOT a signature or a
replacement ZIP/GitHub verifier. R326 only cross-binds supplied source bytes.
Code from D is never imported or executed. R308 outputs remain untrusted until
byte-identical to the reviewed trusted-main R308/R271 producer's output.
R309 review, R305 PIN GO and R321 INSTALL GO remain separate, absent authorities.
Only bounded reviewed trusted-main dependency source reads/imports are made;
no candidate-source execution, network/host operation or wall clock read occurs.
"""
from dataclasses import dataclass, field
import gzip
import hashlib
import builtins
from importlib.machinery import (BuiltinImporter, ExtensionFileLoader, FrozenImporter,
                                ModuleSpec, PathFinder, SourceFileLoader)
import os
import sys
from pathlib import Path
import io
import json
import re
import tarfile
from types import MappingProxyType, ModuleType


class ProvenanceHold(ValueError):
    exit_code = 79


def _require(ok, reason):
    if not ok:
        raise ProvenanceHold("RC6_R326_HOLD79:" + reason)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _json(raw, maximum):
    _require(type(raw) is bytes and 0 < len(raw) <= maximum, "JSON_BUDGET")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "JSON_DUPLICATE")
            result[key] = value
        return result
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ProvenanceHold("JSON_NONFINITE")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProvenanceHold("RC6_R326_HOLD79:JSON_INVALID") from exc
    _require(type(doc) is dict, "JSON_OBJECT_REQUIRED")
    return doc


SOURCE_PATHS = MappingProxyType({
    "scripts/rc6_oci_host_helper.py": "helper",
    "scripts/rc6_runtime_oci_custody.py": "custody",
    "cf_intraday_scalping.py": "cf_intraday_scalping.py",
    "co_market_sessions_hf6.py": "co_market_sessions_hf6.py",
})
_SOURCE_PATHS = SOURCE_PATHS
_BUNDLE = "porota-deploy-bundle-v2.tgz"
_OUTER = "porota-deploy-bundle-v2-manifest.json"
_INNER = "POROTA_DEPLOY_BUNDLE_MANIFEST.json"


@dataclass(frozen=True)
class ExternalReleaseBoundary:
    """Externally authenticated R252/R163 result, never self-certifying.

    member_sha256 pins the two named D ZIP members; git_blobs pins all four
    required source paths from the authenticated candidate SHA/tree. A caller
    fabricating this descriptor has NOT authenticated provenance. Trusted-main
    integration must construct it only from actual R252/R163/Git API evidence.
    """
    candidate_sha: str
    candidate_tree: str
    artifact_digest: str
    image_id: str
    release_run_id: int
    release_run_attempt: int
    release_artifact_id: int
    r252_status: str
    r163_status: str
    member_sha256: tuple
    git_blobs: tuple


def _pairs(value, names, width):
    _require(type(value) is tuple and len(value) == len(names), "BOUNDARY_ENTRIES")
    _require(all(type(row) is tuple and len(row) == 2 and type(row[0]) is str
                 and type(row[1]) is str and re.fullmatch("[0-9a-f]{" + str(width) + "}", row[1])
                 for row in value), "BOUNDARY_HASH")
    result = dict(value)
    _require(set(result) == set(names) and len(result) == len(value), "BOUNDARY_SCOPE")
    return result


def _boundary(boundary):
    _require(type(boundary) is ExternalReleaseBoundary, "EXTERNAL_VERIFICATION_REQUIRED")
    _require(boundary.r252_status == "TRUSTED_RELEASE_BYTES_BOUND" and
             boundary.r163_status == "BYTES_MATCH_EXPECTED", "EXTERNAL_VERIFICATION_REQUIRED")
    _require(all(type(value) is str and re.fullmatch("[0-9a-f]{40}", value)
                 for value in (boundary.candidate_sha, boundary.candidate_tree)) and
             all(type(value) is str and re.fullmatch("sha256:[0-9a-f]{64}", value)
                 for value in (boundary.artifact_digest, boundary.image_id)) and
             all(type(value) is int and 0 < value < 10**20 for value in (
                 boundary.release_run_id, boundary.release_run_attempt, boundary.release_artifact_id)),
             "BOUNDARY_IDENTITY")
    return (_pairs(boundary.member_sha256, {_BUNDLE, _OUTER}, 64),
            _pairs(boundary.git_blobs, _SOURCE_PATHS, 40))


def _path(name):
    return (type(name) is str and len(name) <= 240 and
            re.fullmatch(r"[A-Za-z0-9_./-]+", name) is not None and
            all(part not in {"", ".", ".."} for part in name.split("/")))


class _RegularBundleMember(tarfile.TarInfo):
    def _proc_member(self, archive):
        # tarfile processes PAX, GNU long-name and sparse records before
        # yielding a member. Reject them at the physical header boundary.
        _require(self.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.CONTTYPE),
                 "TAR_MEMBER_SCOPE")
        return super()._proc_member(archive)


def extract_bound_sources(boundary, bundle_bytes, outer_manifest_bytes):
    """Extract only four source members; never extract to disk or verify ZIP.

    Outer D authentication is the external R252/R163 boundary's obligation.
    This bounded reader verifies inner/outer manifest equality, selected member
    SHA/size and candidate Git blob identity, not a second full tar validator.
    """
    members, blobs = _boundary(boundary)
    _require(type(bundle_bytes) is bytes and 0 < len(bundle_bytes) <= 128 * 1024**2,
             "BUNDLE_BUDGET")
    _require(_hash(bundle_bytes) == members[_BUNDLE] and
             type(outer_manifest_bytes) is bytes and _hash(outer_manifest_bytes) == members[_OUTER],
             "D_MEMBER_HASH_MISMATCH")
    outer = _json(outer_manifest_bytes, 4 * 1024**2)
    _require(outer.get("bundle_sha256") == _hash(bundle_bytes), "OUTER_BUNDLE_HASH")
    selected = {}
    seen = set()
    total = 0
    try:
        # GzipFile bounds each inflate request from the uncompressed TAR
        # stream. tarfile's r|gz can expand a whole member to read one header.
        with gzip.GzipFile(fileobj=io.BytesIO(bundle_bytes), mode="rb") as plain, \
             tarfile.open(fileobj=plain, mode="r|", tarinfo=_RegularBundleMember) as archive:
            for entry in archive:
                _require(len(seen) < 20000 and _path(entry.name) and entry.name not in seen,
                         "TAR_MEMBER_SCOPE")
                seen.add(entry.name)
                total += entry.size
                _require(0 <= entry.size <= 16 * 1024**2 and total <= 256 * 1024**2,
                         "TAR_UNCOMPRESSED_BUDGET")
                if entry.name in _SOURCE_PATHS or entry.name == _INNER:
                    selected[entry.name] = archive.extractfile(entry).read(entry.size + 1)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ProvenanceHold("RC6_R326_HOLD79:TAR_INVALID") from exc
    _require(set(selected) == set(_SOURCE_PATHS) | {_INNER}, "REQUIRED_SOURCE_MISSING")
    inner = _json(selected.pop(_INNER), 4 * 1024**2)
    _require(outer == {**inner, "bundle_sha256": _hash(bundle_bytes)} and
             set(inner) == {"schema_version", "status", "file_count", "files"} and
             type(inner["schema_version"]) is int and inner["schema_version"] == 1 and
             inner["status"] == "GREEN" and type(inner["files"]) is list and
             type(inner["file_count"]) is int and 1 <= inner["file_count"] <= 20000 and
             inner["file_count"] == len(inner["files"]), "MANIFEST_CLOSURE")
    rows = {}
    for row in inner["files"]:
        _require(type(row) is dict and set(row) == {"path", "sha256", "bytes"}
                 and _path(row["path"]) and row["path"] not in rows
                 and type(row["bytes"]) is int and row["bytes"] >= 0
                 and type(row["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["sha256"]),
                 "MANIFEST_ENTRY")
        rows[row["path"]] = row
    _require(set(rows) == seen - {_INNER}, "MANIFEST_MEMBER_SET")
    for path, raw in selected.items():
        row = rows[path]
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        _require(len(raw) == row["bytes"] and _hash(raw) == row["sha256"]
                 and blob == blobs[path], "SOURCE_HASH_SIZE_OR_GIT_BLOB")
    return MappingProxyType({_SOURCE_PATHS[path]: raw for path, raw in selected.items()})


# Exact reviewed checkout source, not a release artifact or Python import cache.
_R308_SHA256 = "f9e1b8dc4d32ebd662eebe382be5989b1f72bb36b17a0fe3f2f3072691712db9"
_R271_SHA256 = "aa68cc1def24096d32fc91c77c2076bee44ca8290000fdbd58b945cfc820ae1d"
_CODE_PINS = MappingProxyType({
    "rc6_oci_host_helper": _R271_SHA256,
    "rc6_r308_root_launch_profile_freeze": _R308_SHA256,
    "rc6_host_rootowned_installer_contract":
        "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9",
    "rc6_r304_root_install_manifest_freeze":
        "6711ad67f4de122488787b5587ff947801afed19fe0df4d551db4e65f638fea9",
})
_SCRIPT_EDGES = MappingProxyType({
    "rc6_r308_root_launch_profile_freeze": "rc6_oci_host_helper",
    "rc6_r304_root_install_manifest_freeze": "rc6_host_rootowned_installer_contract",
})


def _trusted_dependency_root():
    return Path(__file__).resolve().parent


def _reviewed_source_pair(names, reason):
    # Capture/check the whole pair before executing either dependency. Execute
    # these bytes later, never reopen through an import loader or bytecode cache.
    captured = {}
    for name in names:
        _require(name in _CODE_PINS, reason)
        path = _trusted_dependency_root() / (name + ".py")
        try:
            with path.open("rb") as handle:
                raw = handle.read(2 * 1024**2 + 1)
        except OSError as exc:
            raise ProvenanceHold("RC6_R326_HOLD79:" + reason) from exc
        _require(len(raw) <= 2 * 1024**2 and _hash(raw) == _CODE_PINS[name], reason)
        captured[name] = (path, raw)
    return captured


# Direct imports of the four fixed sources and the observed secrets/datetime
# closure on CPython 3.11/3.12. Native hash accelerators vary by interpreter.
_STDLIB_NAMES = frozenset((
    "dataclasses", "datetime", "_datetime", "hashlib", "_hashlib", "json",
    "os", "pathlib", "re", "stat", "sys", "types", "importlib", "time",
    "secrets", "base64", "binascii", "hmac", "random", "math", "bisect",
    "_bisect", "_random", "_sha1", "_sha2", "_sha256", "_sha512", "_sha3",
    "_blake2", "struct", "_struct", "collections", "_collections", "copyreg",
    "enum", "functools", "_functools", "itertools", "keyword", "operator",
    "_operator", "reprlib", "warnings", "_sre", "_collections_abc",
))


def _verify_standard_library():
    """Reject shadowed standard imports without importing their target names."""
    reason = "STDLIB_DEPENDENCY_UNVERIFIED"
    root = Path(os.__file__).resolve().parent

    def reviewed_spec(spec):
        _require(type(spec) is ModuleSpec and type(spec.origin) is str, reason)
        if spec.origin in ("built-in", "frozen"):
            expected = BuiltinImporter if spec.origin == "built-in" else FrozenImporter
            _require(spec.loader is expected, reason)
            return
        _require(type(spec.loader) in (SourceFileLoader, ExtensionFileLoader), reason)
        path = Path(spec.origin).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise ProvenanceHold("RC6_R326_HOLD79:" + reason) from None
        _require(not {"site-packages", "dist-packages"}.intersection(relative.parts)
                 and type(spec.loader.path) is str and
                 Path(spec.loader.path).resolve() == path, reason)

    # Read dictionaries directly: an injected module's __getattr__ is code.
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] in _STDLIB_NAMES:
            _require(type(module) is ModuleType, reason)
            data = vars(module)
            _require("__getattr__" not in data, reason)
            spec = data.get("__spec__")
            reviewed_spec(spec)
            _require(data.get("__loader__") is spec.loader, reason)
            if spec.origin not in ("built-in", "frozen"):
                _require(type(data.get("__file__")) is str and
                         Path(data["__file__"]).resolve() == Path(spec.origin).resolve(), reason)
    for name in _STDLIB_NAMES:
        if name in sys.modules:
            continue
        # All names are top-level; PathFinder never imports a package parent.
        spec = (BuiltinImporter.find_spec(name) or FrozenImporter.find_spec(name)
                or PathFinder.find_spec(name))
        if spec is None and name.startswith("_"):
            continue  # Optional native accelerator; Python retains its fallback.
        reviewed_spec(spec)


def _execute_reviewed(name, source, *, dependency=None):
    """Four fixed code pins, two fixed import edges, no global module writes."""
    path, raw = source
    _require(name in _CODE_PINS and _hash(raw) == _CODE_PINS[name],
             "TRUSTED_DEPENDENCY_CODE_PIN")
    _verify_standard_library()
    module = ModuleType("_rc6_r326_verified_" + name)
    module.__file__ = str(path)
    module.__package__ = ""
    private_scripts = ModuleType("scripts")
    edge = _SCRIPT_EDGES.get(name)
    if edge is not None:
        _require(type(dependency) is ModuleType, "TRUSTED_DEPENDENCY_IMPORT")
        private_scripts.__dict__[edge] = dependency

    def reviewed_import(import_name, globals=None, locals=None, fromlist=(), level=0):
        if import_name == "scripts" or import_name.startswith("scripts."):
            _require(level == 0 and import_name == "scripts" and edge is not None
                     and tuple(fromlist) == (edge,), "TRUSTED_DEPENDENCY_IMPORT")
            return private_scripts
        _require(level == 0, "TRUSTED_DEPENDENCY_IMPORT")
        return builtins.__import__(import_name, globals, locals, fromlist, level)

    # Only this module's builtins change. Neither an existing scripts package,
    # a cached module/__getattr__, sys.path nor sys.modules is consulted/edited
    # for the two project edges. The pinned sources have no string dataclass
    # annotations; dont_inherit avoids introducing them from a future caller.
    module.__dict__["__builtins__"] = {**vars(builtins), "__import__": reviewed_import}
    try:
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    except Exception as exc:
        raise ProvenanceHold("RC6_R326_HOLD79:TRUSTED_DEPENDENCY_EXEC") from exc
    return module


def _trusted_freezer():
    names = ("rc6_host_rootowned_installer_contract", "rc6_r304_root_install_manifest_freeze")
    sources = _reviewed_source_pair(names, "MISSING_TRUSTED_R304")
    r280 = _execute_reviewed(names[0], sources[names[0]])
    r304 = _execute_reviewed(names[1], sources[names[1]], dependency=r280)
    _require(r304.r280 is r280, "MISSING_TRUSTED_R304")
    return r304.freeze_root_install_manifest


def _trusted_consumer(helper_bytes):
    """Bind D bytes to the reviewed local pair before executing any source."""
    names = ("rc6_oci_host_helper", "rc6_r308_root_launch_profile_freeze")
    sources = _reviewed_source_pair(names, "MISSING_TRUSTED_R308_CONSUMER")
    _require(type(helper_bytes) is bytes and sources[names[0]][1] == helper_bytes,
             "TRUSTED_R271_D_BLOB_MISMATCH")
    helper = _execute_reviewed(names[0], sources[names[0]])
    r308 = _execute_reviewed(names[1], sources[names[1]], dependency=helper)
    _require(r308.host_contract is helper, "MISSING_TRUSTED_R308_CONSUMER")
    return r308.freeze_root_launch_profiles


def validate_generated_inputs(boundary, sources, profiles_raw, unit_raw, context):
    """Require exact reviewed R308/R271 output, never hash-only equivalence."""
    _boundary(boundary)
    keys = {"daemon_id", "generation", "root_stage", "dashboard_env_sha256", "observer_env_sha256"}
    _require(type(context) is dict and set(context) == keys, "CONTEXT_SCOPE")
    _require(type(context["daemon_id"]) is str and re.fullmatch(r"[A-Za-z0-9:_./-]{6,128}", context["daemon_id"])
             and type(context["generation"]) is str and re.fullmatch("[0-9a-f]{32}", context["generation"])
             and all(type(context[key]) is str and re.fullmatch("[0-9a-f]{64}", context[key])
                     for key in ("dashboard_env_sha256", "observer_env_sha256")), "CONTEXT_IDENTITY")
    stage = context["root_stage"]
    _require(type(stage) is str and len(stage) <= 150 and
             (stage == "/opt/porota-trading" or stage.startswith("/opt/porota-trading/"))
             and _path(stage[1:]), "CONTEXT_STAGE")
    consumer = _trusted_consumer(sources["helper"])
    generated = consumer(
        candidate_sha=boundary.candidate_sha, candidate_tree=boundary.candidate_tree,
        artifact_digest=boundary.artifact_digest, image_id=boundary.image_id,
        daemon_id=context["daemon_id"], generation=context["generation"], root_stage=stage,
        dashboard_env_sha256=context["dashboard_env_sha256"],
        observer_env_sha256=context["observer_env_sha256"],
        observer_source_sha256={"/app/" + path: _hash(sources[path]) for path in
                                ("cf_intraday_scalping.py", "co_market_sessions_hf6.py")},
        critical_source_sha=boundary.candidate_sha)
    _require(type(profiles_raw) is bytes and type(unit_raw) is bytes and
             profiles_raw == generated.profiles_bytes and unit_raw == generated.unit_template_bytes,
             "R308_BYTE_EQUIVALENCE")
    doc = _json(profiles_raw, 128 * 1024)
    _require(set(doc) == {"schema_version", "candidate_sha", "artifact_digest", "image_tag", "root_stage", "observer_source_sha256", "roles"}
             and type(doc["schema_version"]) is int and doc["schema_version"] == 1
             and doc["candidate_sha"] == boundary.candidate_sha
             and doc["artifact_digest"] == boundary.artifact_digest
             and doc["image_tag"] == "porota-trading-bot:17.0.0-rc6-candidate-" + boundary.candidate_sha
             and doc["root_stage"] == stage
             and doc["observer_source_sha256"] == {
                 "/app/" + path: _hash(sources[path]) for path in
                 ("cf_intraday_scalping.py", "co_market_sessions_hf6.py")}, "PROFILE_BINDING")
    roles = doc["roles"]
    _require(type(roles) is dict and set(roles) == {"dashboard", "observer", "critical"}, "PROFILE_ROLES")
    for name, role in roles.items():
        _require(type(role) is dict and set(role) == {"env_file", "env_sha256", "environment", "mounts"}
                 and type(role["environment"]) is dict and type(role["mounts"]) is list,
                 "PROFILE_ROLE_SCHEMA")
        environment = role["environment"]
        _require(all(type(key) is str and type(value) is str for key, value in environment.items()) and
                 all(environment.get(key) == value for key, value in {
                     "POROTA_RUNTIME_MODE": "PRODUCTION_PAPER", "PPI_ORDERS": "BLOCKED", "REAL_ORDERS_ALLOWED": "false"}.items()), "PROFILE_PAPER")
        if name == "critical":
            _require(role["env_file"] is None and role["env_sha256"] is None, "PROFILE_CRITICAL_ENV")
        else:
            expected = "/var/lib/porota-rc6-oci-custody/generations/" + context["generation"] + "/env/" + name + ".env"
            _require(role["env_file"] == expected and role["env_sha256"] == context[name + "_env_sha256"], "PROFILE_ENV_SEAL")
    _require(type(unit_raw) is bytes and 0 < len(unit_raw) <= 128 * 1024, "UNIT_BUDGET")
    try:
        lines = unit_raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise ProvenanceHold("RC6_R326_HOLD79:UNIT_ASCII") from exc
    bindings = {"candidate_sha": boundary.candidate_sha, "candidate_tree": boundary.candidate_tree,
                "artifact_digest": boundary.artifact_digest, "image_id": boundary.image_id,
                "daemon_id": context["daemon_id"], "generation": context["generation"]}
    for key, expected in bindings.items():
        _require([line for line in lines if line.startswith("# " + key + "=")] == ["# " + key + "=" + expected], "UNIT_BINDING")
    _require([line for line in lines if line.startswith("ExecCondition=")] == [
        "ExecCondition=/bin/sh -c 'echo RC6_OCI_CUSTODY_HOLD79:CRITICAL_CUTOVER_NOT_AUTHORIZED >&2; exit 79'"]
        and [line for line in lines if line.startswith("ExecStart=")] == [
        "ExecStart=/usr/local/sbin/porota-rc6-oci-custody critical start-newborn --paper-only --source-sha "
        + boundary.candidate_sha + " --generation " + context["generation"]], "UNIT_HOLD_AND_SOURCE")
    return {"profiles": generated.profiles_sha256, "unit_template": generated.unit_template_sha256}


@dataclass(frozen=True)
class RootBundleProposal:
    sources: MappingProxyType
    provenance_bytes: bytes
    manifest_sha256: str
    write_order: tuple = field(default=("helper.bin", "custody.py", "profiles.json", "critical-unit-template.service", "approval.json", "manifest.json"), init=False)
    state: str = field(default="PROPUESTO", init=False)
    profile_consumer_contract_verified: bool = field(default=False, init=False)
    r309_verified: bool = field(default=False, init=False)
    r305_pin_go: bool = field(default=False, init=False)
    r321_install_go: bool = field(default=False, init=False)
    host_go: bool = field(default=False, init=False)
    deploy_authorized: bool = field(default=False, init=False)


def propose_root_bundle(*, boundary, bundle_bytes, outer_manifest_bytes,
                        profiles_bytes, unit_template_bytes, context,
                        approval_intent, now):
    """Produce six immutable source bytes, manifest last; never stage them.

    Approval intent is independently supplied, fresh and checked by trusted R304.
    Neither a previous D digest nor an old approval is rewritten/recycled.
    """
    # Snapshot mutable caller inputs before any dependency boundary can run.
    context = _json(_canonical(context), 4096)
    approval_intent = _json(_canonical(approval_intent), 65536)
    extracted = extract_bound_sources(boundary, bundle_bytes, outer_manifest_bytes)
    generated_hashes = validate_generated_inputs(boundary, extracted, profiles_bytes, unit_template_bytes, context)
    freezer = _trusted_freezer()  # D helper identity already checked before any code ran.
    _require(type(approval_intent) is dict and all(approval_intent.get(key) == value for key, value in {
        "candidate_sha": boundary.candidate_sha, "candidate_tree": boundary.candidate_tree,
        "artifact_digest": boundary.artifact_digest, "image_id": boundary.image_id,
        "daemon_id": context["daemon_id"], "generation": context["generation"],
        "run_id": str(boundary.release_run_id), "run_attempt": str(boundary.release_run_attempt)}.items()), "APPROVAL_D_CONTEXT_BINDING")
    raw = {"helper": extracted["helper"], "custody": extracted["custody"],
           "profiles": profiles_bytes, "unit_template": unit_template_bytes}
    # Raw hashes are taken from the separately D-bound INNER manifest, not
    # newly self-asserted caller source hashes. Generated hashes come from the
    # reviewed deterministic R308 producer after exact-byte equivalence.
    manifest = _json(outer_manifest_bytes, 4 * 1024**2)
    source_hashes = {role: next(row["sha256"] for row in manifest["files"] if row["path"] == path)
                     for path, role in _SOURCE_PATHS.items() if role in {"helper", "custody"}}
    source_hashes.update(generated_hashes)
    frozen = freezer(candidate_sha=boundary.candidate_sha, candidate_tree=boundary.candidate_tree,
                     source_bytes=raw, expected_source_sha256=source_hashes,
                     approval_intent=approval_intent, now=now)
    sources = {"helper.bin": raw["helper"], "custody.py": raw["custody"],
               "profiles.json": profiles_bytes, "critical-unit-template.service": unit_template_bytes,
               "approval.json": frozen.approval_bytes, "manifest.json": frozen.manifest_bytes}
    provenance = {"schema": "RC6_R326_ROOT_SOURCE_PROVENANCE_V1", "candidate_sha": boundary.candidate_sha,
                  "candidate_tree": boundary.candidate_tree, "artifact_digest": boundary.artifact_digest,
                  "image_id": boundary.image_id, "release_run_id": boundary.release_run_id,
                  "release_run_attempt": boundary.release_run_attempt, "release_artifact_id": boundary.release_artifact_id,
                  "external_authentication_required": True, "profile_consumer_contract_verified": False,
                  "host_go": False, "deploy_authorized": False,
                  "files": {key: {"sha256": _hash(value), "bytes": len(value)} for key, value in sources.items()}}
    return RootBundleProposal(MappingProxyType(sources), _canonical(provenance), frozen.manifest_sha256)

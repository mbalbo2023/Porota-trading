"""R326 pure OFFHOST six-source provenance proposal, never an authority.

R252/R163 must authenticate D externally before constructing the boundary below.
Its frozen descriptor is an integration trust boundary, NOT a signature or a
replacement ZIP/GitHub verifier. R326 only cross-binds supplied source bytes.
Candidate code is never imported or executed. R308 outputs remain untrusted
inputs; their documented bindings are checked, not the full helper contract.
R309 review, R305 PIN GO and R321 INSTALL GO remain separate, absent authorities.
No filesystem/network/host operations or wall clock reads are performed here.
"""
from dataclasses import dataclass, field
import hashlib
from importlib import import_module
import io
import json
import re
import tarfile
from types import MappingProxyType


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


_SOURCE_PATHS = {
    "scripts/rc6_oci_host_helper.py": "helper",
    "scripts/rc6_runtime_oci_custody.py": "custody",
    "cf_intraday_scalping.py": "cf_intraday_scalping.py",
    "co_market_sessions_hf6.py": "co_market_sessions_hf6.py",
}
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
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r|gz") as archive:
            for entry in archive:
                _require(len(seen) < 20000 and _path(entry.name) and entry.name not in seen
                         and entry.isfile() and not entry.pax_headers,
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


def _trusted_freezer():
    try:
        return import_module("scripts.rc6_r304_root_install_manifest_freeze").freeze_root_install_manifest
    except (ImportError, AttributeError) as exc:
        raise ProvenanceHold("RC6_R326_HOLD79:MISSING_TRUSTED_R304") from exc


def validate_generated_inputs(boundary, sources, profiles_raw, unit_raw, context):
    """Check R308 identity/source bindings, NOT its full consumer semantics."""
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
    freezer = _trusted_freezer()  # fail closed on standalone unintegrated main
    extracted = extract_bound_sources(boundary, bundle_bytes, outer_manifest_bytes)
    validate_generated_inputs(boundary, extracted, profiles_bytes, unit_template_bytes, context)
    _require(type(approval_intent) is dict and all(approval_intent.get(key) == value for key, value in {
        "candidate_sha": boundary.candidate_sha, "candidate_tree": boundary.candidate_tree,
        "artifact_digest": boundary.artifact_digest, "image_id": boundary.image_id,
        "daemon_id": context["daemon_id"], "generation": context["generation"],
        "run_id": str(boundary.release_run_id), "run_attempt": str(boundary.release_run_attempt)}.items()), "APPROVAL_D_CONTEXT_BINDING")
    raw = {"helper": extracted["helper"], "custody": extracted["custody"],
           "profiles": profiles_bytes, "unit_template": unit_template_bytes}
    frozen = freezer(candidate_sha=boundary.candidate_sha, candidate_tree=boundary.candidate_tree,
                     source_bytes=raw, expected_source_sha256={key: _hash(value) for key, value in raw.items()},
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

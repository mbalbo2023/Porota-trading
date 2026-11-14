"""Fixed R367 host protocol. Public receipts may contain the root manifest only."""
import base64
import os
import sys
from zipimport import zipimporter

from scripts import rc6_genesis_custodian as custody
from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_r304_root_install_manifest_freeze as r304
from scripts import rc6_r326_root_bundle_provenance as r326


_PACKAGE = "/usr/local/libexec/porota-rc6-custody/rc6-genesis-custodian"
_LIMIT = 1024 * 1024
_PHASES = {"prepare-env": ("PREPARE_ENV", ("INITIAL", "POST_D_E")),
           "apply": ("APPLY_GENESIS", ("SEAL_ENV", "PIN", "INSTALL"))}
_RELEASE_RECORD = {"run_id", "run_attempt", "artifact_id", "artifact_digest"}
_require = custody._require


def _installed_package(pin):
    loader = __spec__.loader
    _require(os.geteuid() == 0 and sys.flags.isolated == 1 and sys.argv[0] == _PACKAGE
             and isinstance(loader, zipimporter) and loader.archive == _PACKAGE and loader.prefix == "scripts/"
             and __file__ == _PACKAGE + "/scripts/rc6_genesis_host_transaction.py", "INSTALLED_ZIPAPP")
    directory, name = _PACKAGE.rsplit("/", 1)
    with custody._directory(directory) as (fd, ancestors):
        raw, _ = custody._leaf(fd, name, mode=0o555, maximum=_LIMIT)
        _require(custody._digest(raw) == pin, "INSTALLED_ZIPAPP_DIGEST")
        custody._check_directory(directory, ancestors)


def _canonical(raw, maximum):
    doc = r326._json(raw, maximum)
    _require(custody._json(doc) == raw, "CANONICAL_DOCUMENT")
    return doc


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _decode(value, maximum=_LIMIT):
    _require(type(value) is str and 0 < len(value) <= 4 * ((maximum + 2) // 3), "ENCODED_BYTES")
    raw = base64.b64decode(value, validate=True)
    _require(0 < len(raw) <= maximum and _b64(raw) == value, "ENCODED_BYTES")
    return raw


def _artifact(record):
    _require(type(record) is dict and set(record) == _RELEASE_RECORD and
             all(type(record[key]) is int and 0 < record[key] < 10**20
                 for key in ("run_id", "run_attempt", "artifact_id"))
             and type(record["artifact_digest"]) is str
             and r280._DIGEST.fullmatch(record["artifact_digest"]), "RELEASE_ARTIFACT")


def _bound_release(release, binding):
    """Validate the R340 material carried in the authenticated request before R308 runs."""
    _require(type(release) is dict and set(release) == {
        "boundary", "source_b64", "postcompletion_receipt_b64", "receipt"}, "RELEASE_SCOPE")
    doc = dict(release["boundary"])
    for field in ("member_sha256", "git_blobs", "source_sha256"):
        _require(type(doc[field]) is list and all(type(row) is list for row in doc[field]), "RELEASE_BOUNDARY")
        doc[field] = tuple(tuple(row) for row in doc[field])
    boundary = r326.ExternalReleaseBoundary(**doc)
    _require(boundary.candidate_sha == binding["candidate_sha"] and
             boundary.candidate_tree == binding["candidate_tree"], "RELEASE_CANDIDATE")
    sources = r326._verify_bound_sources(boundary,
        {name: _decode(raw) for name, raw in release["source_b64"].items()})
    report = release["receipt"]
    material = report["source_material"]
    d, e = material["release"], material["postcompletion"]
    _artifact(d)
    _artifact(e)
    _require(d == dict(run_id=boundary.release_run_id, run_attempt=boundary.release_run_attempt,
                       artifact_id=boundary.release_artifact_id, artifact_digest=boundary.artifact_digest),
             "RELEASE_D")
    raw_e = _decode(release["postcompletion_receipt_b64"], 32768)
    erow = _canonical(raw_e, 32768)
    # R321 verifies this same E document before R340 material crosses the
    # signed boundary. Here the exact bytes and D identifiers are bound again.
    for key in ("total_size_bytes", "release_artifact_size_bytes"):
        _require(type(erow[key]) is int and erow[key] > 0, "RELEASE_E_SIZE")
    expected_e = dict(schema_version=1, status="RELEASE_BYTES_VERIFIED_POSTCOMPLETION",
        repository="mbalbo2023/Porota-trading", candidate_sha=boundary.candidate_sha,
        candidate_tree_sha=boundary.candidate_tree, publisher_main_sha=binding["trusted_main_sha"],
        publisher_workflow_path=".github/workflows/rc6-r251-trusted-release-build.yml",
        publisher_run_id=d["run_id"], publisher_run_attempt=d["run_attempt"], release_artifact_id=d["artifact_id"],
        release_artifact_name=f"rc6-r251-release-{d['run_id']}-{d['run_attempt']}-{boundary.candidate_sha}",
        release_artifact_digest=d["artifact_digest"], release_artifact_size_bytes=erow["release_artifact_size_bytes"],
        total_size_bytes=erow["total_size_bytes"], file_count=7, release_manifest_verified=True, r144_release_go=False,
        runtime_verified=False, real_orders_physical_verified=False)
    _require(raw_e == custody._json(expected_e), "RELEASE_E")
    _require(custody.r337._hex(report["publisher_main_tree_sha"], 40), "RELEASE_MAIN_TREE")
    blobs, hashes = dict(boundary.git_blobs), dict(boundary.source_sha256)
    expected_material = dict(status="FOUR_SOURCES_BOUND_OFFHOST", image_id=boundary.image_id,
        release=d, postcompletion=e, postcompletion_receipt_sha256=custody._digest(raw_e),
        sources={path: dict(sha256=hashes[path], bytes=len(sources[role]), git_blob_sha=blobs[path])
                 for path, role in r326.SOURCE_PATHS.items()})
    expected = dict(status="OFFHOST_D_E_BYTES_AUTHENTICATED", publisher_main_sha=binding["trusted_main_sha"],
        publisher_main_tree_sha=report["publisher_main_tree_sha"], candidate_sha=boundary.candidate_sha,
        candidate_tree_sha=boundary.candidate_tree, release_run_id=d["run_id"], release_artifact_id=d["artifact_id"],
        release_artifact_digest=d["artifact_digest"], trusted_main_audit_run_id=e["run_id"],
        artifact_contents_verified_by_this_guard=True, owner_go_verified=False, host_control_verified=False,
        root_env_genesis_verified=False, deploy_authorized=False, real_orders_allowed=False, runtime_verified=False,
        source_material=expected_material)
    _require(custody._json(report) == custody._json(expected), "RELEASE_RECEIPT")
    return boundary, sources


def _freeze(proposal, auth, release, check):
    boundary, sources = _bound_release(release, auth["binding"])
    digests = proposal.output_digests
    context = dict(root_stage="/opt/porota-trading", generation=proposal.generation, daemon_id=proposal.daemon_id,
                   dashboard_env_sha256=digests["dashboard"], observer_env_sha256=digests["observer"])
    roles = {"candidate_sha": boundary.candidate_sha, "candidate_tree": boundary.candidate_tree,
             "artifact_digest": boundary.artifact_digest, "image_id": boundary.image_id}
    produce = r326._trusted_consumer(sources["helper"])
    profile = produce(**roles, **context, critical_source_sha=boundary.candidate_sha,
        observer_source_sha256={"/app/" + name: custody._digest(sources[name])
                                for name in ("cf_intraday_scalping.py", "co_market_sessions_hf6.py")})
    approval = dict(**roles, schema_version=1, phase="GENESIS", paper_mode="PRODUCTION_PAPER", real_orders_allowed=False,
        daemon_id=proposal.daemon_id, generation=proposal.generation, run_id=str(boundary.release_run_id),
        run_attempt=str(boundary.release_run_attempt), expires_at=auth["approval_expires_at"],
        helper_sha256=custody._digest(sources["helper"]), custody_sha256=custody._digest(sources["custody"]),
        profiles_sha256=profile.profiles_sha256, unit_template_sha256=profile.unit_template_sha256)
    frozen = r326.propose_root_bundle_from_bound_sources(boundary=boundary, sources=sources,
        profiles_bytes=profile.profiles_bytes, unit_template_bytes=profile.unit_template_bytes, context=context,
        approval_intent=approval, now=custody._now())
    proposal.freeze_projection({"files": {name: _b64(raw) for name, raw in frozen.sources.items()},
        "release_receipt_sha256": custody._digest(custody._json(release["receipt"])),
        "post_prepare_go_id": auth["prepare_go_id"], "post_run_id": auth["dispatch_run_id"],
        "post_run_attempt": auth["dispatch_run_attempt"]}, check_authority=check)
    return frozen.sources["manifest.json"]


def _verified_files(proposal, auth):
    stored = proposal.read_projection()["files"]
    _require(type(stored) is dict and set(stored) == set(r280.SOURCE_NAMES.values()) | {"manifest.json"},
             "FROZEN_FILES")
    files = {name: _decode(raw) for name, raw in stored.items()}
    raw = files["manifest.json"]
    manifest = r304.validate_root_manifest_bytes(raw)
    _require(raw == custody._json(manifest) and custody._digest(raw) == auth["root_manifest_sha256"]
             and manifest["candidate_sha"] == auth["binding"]["candidate_sha"]
             and manifest["candidate_tree"] == auth["binding"]["candidate_tree"]
             and proposal.generation_commitment == auth["generation_commitment"], "FROZEN_MANIFEST")
    roles = {role: files[name] for role, name in r280.SOURCE_NAMES.items()}
    _require(all(custody._digest(value) == manifest["files"][role] for role, value in roles.items()),
             "FROZEN_MANIFEST_FILES")
    approval = _canonical(roles["approval"], r280.TARGETS["approval"][2])
    _require(approval["generation"] == proposal.generation and approval["daemon_id"] == proposal.daemon_id
             and approval["phase"] == "GENESIS", "FROZEN_APPROVAL")
    r280._validate_approval(approval, manifest, roles, now=custody._now())
    return files


def _transaction(verb, raw):
    request = _canonical(raw, _LIMIT)
    _require(verb in _PHASES and set(request) == {"schema", "operation", "step", "authority", "release"}
             and request["schema"] == "RC6_R367_HOST_REQUEST_V1", "REQUEST")
    op, steps = _PHASES[verb]
    step, auth = request["step"], request["authority"]
    _require(request["operation"] == op and step in steps and type(auth) is dict and
             (type(request["release"]) is dict if step == "POST_D_E" else request["release"] is None), "STEP")
    _installed_package(auth["custodian_sha256"])
    from scripts.rc6_genesis_host_identity import signed_authority
    check = signed_authority(request)
    custody._authorized(check)
    binding = custody._binding(auth["binding"])
    result = {"schema": "RC6_R367_HOST_RECEIPT_V1", "step": step, "request_sha256": custody._digest(raw)}
    if step == "INITIAL":
        _require(binding["run_id"] == auth["dispatch_run_id"] and binding["run_attempt"] == auth["dispatch_run_attempt"]
                 and binding["prepare_go_id"] == str(auth["prepare_go_id"]) and
                 binding["prepare_nonce"] == auth["nonce"], "PREPARE_RUN")
        result.update(custody.prepare_env(binding, check_authority=check))
    else:
        result.update(proposal_id=auth["proposal_id"], commitment=auth["commitment"])
        with custody.open_proposal(binding, result["proposal_id"], result["commitment"], check_authority=check) as proposal:
            if step == "POST_D_E":
                _require(str(auth["initial_prepare_go_id"]) == binding["prepare_go_id"], "PREPARE_ORIGIN")
                raw_manifest = _freeze(proposal, auth, request["release"], check)
            else:
                files = _verified_files(proposal, auth)
                raw_manifest = files["manifest.json"]
                def fresh():
                    return check(force=True)
                if step == "SEAL_ENV":
                    proposal.publish_env(nonce=auth["nonce"], issued_at=auth["issued_at"],
                                         expires_at=auth["expires_at"], check_authority=fresh)
                    proposal.verify_env()
                else:
                    from scripts import rc6_genesis_host_install as installer
                    proposal.verify_env()
                    if step == "PIN":
                        installer.verify_transition(auth, raw_manifest)
                    else:
                        outcome = installer.install(auth, files, check_authority=check, verify_env=proposal.verify_env,
                                                    final_authority=fresh)
                        _require(outcome == "INSTALLED_FILES_ONLY_NOT_HOST_GO", "INSTALL_RECEIPT")
            if step in ("POST_D_E", "SEAL_ENV"):
                result.update(manifest_b64=_b64(raw_manifest), manifest_sha256=custody._digest(raw_manifest),
                              generation_commitment=proposal.generation_commitment)
    custody._authorized(check)
    public = custody._json(result)
    _require(len(public) <= 4096, "PUBLIC_RECEIPT_SIZE")
    return public


def host_main(verb):
    try:
        raw = sys.stdin.buffer.read(_LIMIT + 1)
        result = _transaction(verb, raw)
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
    except Exception:
        # The caller uses exit 79. Traces and evidence can include private inputs.
        return 79
    return 0

"""Authenticated runner-side authority for R367. All GitHub operations are read-only.

PREPARE and APPLY are separate ownership windows. PIN retains its R305 V1 format.
INSTALL V2 commits to the host-private generation instead of publishing it in #1093.
An ENV GO names both independent decisions and the original and post-D/E preparation.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import re
import stat
import zipfile

from scripts import rc6_ci_release_required_guard as r252
from scripts import rc6_genesis_launcher_authority as legacy
from scripts import rc6_r163_trusted_postrun_byte_audit as r163
from scripts import rc6_r295_host_bootstrap_guard as r295
from scripts import rc6_r305_root_pin_transition_guard as r305
from scripts import rc6_r305_manifest_witness_offhost as witness
from scripts import rc6_r321_root_install_actions as r321
from scripts import rc6_r340_trusted_root_env_controller as r340

ControlHold = r295.BootstrapHold
PREPARE_MARKER = "RC6_R367_PREPARE_GO_V1="
INSTALL_MARKER = "RC6_R367_ROOT_INSTALL_GO_V2="
ENV_MARKER = "RC6_R367_ENV_GO_V1="
LEASE_MARKER = legacy.CONTROL_LEASE_MARKER
PAPER = {"paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False}
R321_PATH = ".github/workflows/rc6-r321-root-install-approved.yml"
R321_NAME = "RC6 R321 ROOT evidence and file observation"
WITNESS_PATH = ".github/workflows/rc6-r305-root-manifest-witness.yml"
WITNESS_NAME = "RC6 R305 Root manifest witness"
WITNESS_ARTIFACT = "rc6-r305-root-manifest-digest-witness"
require = r295._require


def _hex(value, width):
    require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(width) + "}", value), "CONTROL_DIGEST_INVALID")
    return value


def _id(value):
    require(type(value) is int and 0 < value < 10**20, "CONTROL_IDENTIFIER_INVALID")
    return value


def _number(value):
    require(type(value) is str and re.fullmatch("[1-9][0-9]{0,19}", value), "CONTROL_DECIMAL_INVALID")
    return int(value)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _exact(actual, expected):
    # Canonical encoding also distinguishes bool from int.
    require(r305._canonical(actual) == r305._canonical(expected), "CONTROL_GO_SCOPE_MISMATCH")


def _now():
    return datetime.now(timezone.utc)


class _WitnessReader(r163.GitHubReadOnlyREST):
    """Reuse authenticated GitHub/storage handling with the witness's smaller cap."""
    @staticmethod
    def _read(response, *, limit):
        return r163.GitHubReadOnlyREST._read(response, limit=min(limit, 16384))


class _Read:
    """Track exactly which immutable identities and live-state records to reopen."""
    def __init__(self, env, operation):
        self.env = env
        require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                and env.get("GITHUB_REF") == "refs/heads/main" and env.get("GITHUB_REPOSITORY") == r252.REPO
                and env.get("GITHUB_ACTOR") == env.get("GITHUB_TRIGGERING_ACTOR") == r295.OWNER
                and env.get("GITHUB_ACTOR_ID") == str(legacy.OWNER_ID) and env.get("DO_USER") == "porotaadmin"
                and env.get("RC6_R321_OPERATION") == operation, "CONTROL_DISPATCH_REQUIRED")
        self.main = _hex(env["GITHUB_SHA"], 40)
        require(r340._checkout_head() == self.main, "CONTROL_CHECKOUT_DRIFT")
        _number(env["GITHUB_RUN_ID"])
        _number(env["GITHUB_RUN_ATTEMPT"])
        self.github = legacy._GitHub(env["GITHUB_TOKEN"])
        self.witness_reader = _WitnessReader(env["GITHUB_TOKEN"])
        self.host = legacy.host_identity_sha256(env)
        self.versions = self.public_versions()
        self.records = {}
        self.clocks = []
        self.base, self.main_tree = self.refs()
        self.lease_id = _number(env["RC6_R367_LEASE_ID"])
        self.lease, lease = self.comment(self.lease_id, LEASE_MARKER, issue=980)
        self.scope = {**self.base, "nonce": _hex(lease["nonce"], 32)}
        self.lease_scope = {**self.scope, "operation": operation}
        _exact(lease, {**self.lease_scope, "status": "ACTIVE"})

    def public_versions(self):
        count, update = legacy._ledger_version(self.github)
        issue = self.github.read("/issues/1093")
        require(issue.get("url") == legacy.API + "/issues/1093" and issue.get("number") == 1093
                and type(issue.get("comments")) is int and issue["comments"] > 0, "CONTROL_SOURCE_VERSION")
        r295._utc_timestamp(issue["updated_at"])
        return {"980": {"comments": count, "updated_at": update},
                "1093": {"comments": issue["comments"], "updated_at": issue["updated_at"]}}

    def refs(self):
        ref = self.github.read("/git/ref/heads/main")
        require(ref.get("ref") == "refs/heads/main" and ref.get("object", {}).get("type") == "commit"
                and ref["object"]["sha"] == self.main, "CONTROL_LIVE_MAIN_DRIFT")
        pr = self.github.read("/pulls/1087")
        candidate = _hex(pr["head"]["sha"], 40)
        r252._candidate_pr(pr, mode="PR_CANDIDATE", sha=candidate, product_sha="")
        def tree(sha):
            row = self.github.read("/git/commits/" + sha)
            require(row.get("sha") == sha, "CONTROL_GIT_TREE_DRIFT")
            return _hex(row["tree"]["sha"], 40)
        return ({"trusted_main_sha": self.main, "candidate_sha": candidate, "candidate_tree": tree(candidate),
                 "host_identity_source": "PROTECTED_DO_PINS", "host_identity_sha256": self.host}, tree(self.main))

    def read(self, path):
        value = self.github.read(path)
        self.records[path] = value
        return value

    def comment(self, ident, marker, *, issue=1093):
        path = f"/issues/comments/{_id(ident)}"
        row = self.read(path)
        legacy._owner_comment(row, ident, issue)
        return row, legacy._document(row["body"], marker)

    def active(self, row, doc, *, maximum=timedelta(hours=2), remaining=timedelta()):
        start, end = r295._utc_timestamp(row["created_at"]), r295._utc_timestamp(doc["expires_at"])
        require(self.lease_id < row["id"] and r295._utc_timestamp(self.lease["created_at"]) <= start,
                "CONTROL_LEASE_POSTDATES_GO")
        self.clocks.append((start, end, maximum, remaining))

    def historical_run(self, run, attempt, go_row, go_document):
        record = self.run(r321path=R321_PATH, name=R321_NAME, event="workflow_dispatch",
                          rid=_number(run), attempt=_number(attempt))
        require(r295._utc_timestamp(go_row["created_at"]) <= r252._timestamp(record["updated_at"])
                and r252._timestamp(record["run_started_at"]) < r295._utc_timestamp(go_document["expires_at"]),
                "CONTROL_PREPARE_RUN_OUTSIDE_GO")
        for key in ("actor", "triggering_actor"):
            require(record[key].get("id") == legacy.OWNER_ID and record[key].get("login") == r295.OWNER,
                    "CONTROL_PREPARE_RUN_OWNER_DRIFT")

    def run(self, *, r321path, name, event, rid, attempt):
        path = f"/actions/runs/{rid}"
        row = self.read(path)
        for check in (row, self.read(path + f"/attempts/{attempt}")):
            r252._run_matches(check, run_id=rid, attempt=attempt, workflow=r321path,
                              name=name, event=event, main_sha=self.main)
        return row

    def artifact(self, rid, row, *, name, ident=None, digest=None):
        listing = self.read(f"/actions/runs/{rid}/artifacts?per_page=100")
        require(type(listing.get("artifacts")) is list and len(listing["artifacts"]) == 1,
                "CONTROL_ARTIFACT_NOT_UNIQUE")
        selected = listing["artifacts"][0]
        ident = _id(selected["id"]) if ident is None else _id(ident)
        artifact = self.read(f"/actions/artifacts/{ident}")
        r252._unique_artifact_list(listing, artifact)
        r252._artifact_matches(artifact, artifact_id=ident, run_id=rid, name=name,
                               digest=artifact["digest"] if digest is None else digest, main_sha=self.main, run=row)
        return artifact

    def finish(self):
        version = legacy.verify_global_lease(self.github, self.lease, self.lease_id,
                                               marker=LEASE_MARKER, scope=self.lease_scope)
        for path, original in self.records.items():
            fresh = self.github.read(path)
            if path.startswith("/issues/comments/"):
                require(r305.owner_go_identity(fresh) == r305.owner_go_identity(original), "CONTROL_COMMENT_CHANGED")
            else:
                require(fresh == original, "CONTROL_GITHUB_EVIDENCE_CHANGED")
        require(self.refs() == (self.base, self.main_tree) and legacy.host_identity_sha256(self.env) == self.host
                and version == (self.versions["980"]["comments"], self.versions["980"]["updated_at"])
                and self.public_versions() == self.versions, "CONTROL_AUTHORITY_CHANGED")
        now = _now()
        for start, end, maximum, remaining in self.clocks:
            require(start <= now < end <= start + maximum and end-now >= remaining, "CONTROL_GO_EXPIRED")


def _origin(doc):
    return {"prepare_go_id": _id(doc["prepare_go_id"]), "prepare_run_id": str(_number(doc["prepare_run_id"])),
            "prepare_run_attempt": str(_number(doc["prepare_run_attempt"])),
            "proposal_id": _hex(doc["proposal_id"], 32), "commitment": _hex(doc["commitment"], 64)}


def _prep(read, row, doc, phase, *, active, release=None):
    scope = {**read.base, "nonce": _hex(doc["nonce"], 32)}
    lease_id = _id(doc["lease_comment_id"])
    expected = {**scope, **PAPER, "operation": "PREPARE_ENV", "phase": phase, "lease_comment_id": lease_id,
                "expires_at": doc["expires_at"]}
    require(lease_id < row["id"], "CONTROL_PREPARE_LEASE_ID")
    if active:
        require(lease_id == read.lease_id and scope == read.scope, "CONTROL_PREPARE_SCOPE")
        read.active(row, doc)
    initial_document = doc
    if phase == "POST_D_E":
        origin = _origin(doc)
        expected.update(origin, **release)
        require(origin["prepare_go_id"] < row["id"], "CONTROL_PREPARE_ORDER")
        expected["approval_expires_at"] = doc["approval_expires_at"]
        expiry = r295._utc_timestamp(doc["approval_expires_at"])
        issued = r295._utc_timestamp(row["created_at"])
        require(issued < expiry <= issued + timedelta(hours=4), "CONTROL_ROOT_APPROVAL_WINDOW")
        if active:
            read.clocks.append((issued, expiry, timedelta(hours=4), timedelta()))
        irow, initial = read.comment(origin["prepare_go_id"], PREPARE_MARKER)
        initial_document = _prep(read, irow, initial, "INITIAL", active=False)
        read.historical_run(origin["prepare_run_id"], origin["prepare_run_attempt"], irow, initial)
    _exact(doc, expected)
    return initial_document


def _binding(read, run, attempt, prepare_go_id, initial_document):
    return {"trusted_main_sha": read.main, "candidate_sha": read.base["candidate_sha"],
            "candidate_tree": read.base["candidate_tree"], "host_identity_sha256": read.host,
            "run_id": run, "run_attempt": attempt, "prepare_go_id": prepare_go_id,
            "prepare_nonce": initial_document["nonce"]}


def _release(read, release):
    require(type(release) is dict and release.get("status") == "OFFHOST_D_E_BYTES_AUTHENTICATED"
            and release.get("artifact_contents_verified_by_this_guard") is True
            and release.get("publisher_main_sha") == read.main and release.get("publisher_main_tree_sha") == read.main_tree
            and release.get("candidate_sha") == read.base["candidate_sha"]
            and release.get("candidate_tree_sha") == read.base["candidate_tree"], "CONTROL_AUTHENTICATED_D_E_REQUIRED")
    material = release["source_material"]
    d, e = material["release"], material["postcompletion"]
    require(material["status"] == "FOUR_SOURCES_BOUND_OFFHOST" and release["release_run_id"] == d["run_id"]
            and release["trusted_main_audit_run_id"] == e["run_id"] and release["release_artifact_id"] == d["artifact_id"]
            and release["release_artifact_digest"] == d["artifact_digest"], "CONTROL_RELEASE_MATERIAL_DRIFT")
    # E validation needs the common typed root context. These two fields are unused by E.
    context = r321.RootInstallContext(read.main, read.main_tree, read.base["candidate_sha"],
        read.base["candidate_tree"], "0"*64, "0"*64, read.env["RC6_EXPECTED_DO_ID"], read.env["RC6_EXPECTED_DO_REGION"])
    raw = release["release_e_receipt_raw"]
    parsed = r321.validate_release_e_receipt(raw, context)
    require(_sha(raw) == material["postcompletion_receipt_sha256"]
            and (parsed["publisher_run_id"], parsed["publisher_run_attempt"], parsed["release_artifact_id"],
                 parsed["release_artifact_digest"]) == (d["run_id"], d["run_attempt"], d["artifact_id"], d["artifact_digest"]),
            "CONTROL_RELEASE_E_DRIFT")
    data = {"release_e_receipt_sha256": _sha(raw)}
    for letter, record, path, name, event, prefix in (
        ("d", d, r252.D_PATH, r252.D_NAME, "workflow_dispatch", "release"),
        ("e", e, r252.E_PATH, r252.E_NAME, "workflow_run", "postcompletion")):
        rid, attempt = _id(record["run_id"]), _id(record["run_attempt"])
        run = read.run(r321path=path, name=name, event=event, rid=rid, attempt=attempt)
        artifact_name = f"rc6-r251-{prefix}-{d['run_id']}-{d['run_attempt']}-{read.base['candidate_sha']}"
        read.artifact(rid, run, name=artifact_name, ident=record["artifact_id"], digest=record["artifact_digest"])
        for key in ("run_id", "run_attempt", "artifact_id", "artifact_digest"):
            data[f"release_{letter}_{key}"] = str(record[key]) if key.startswith("run_") else record[key]
    return data


def _witness(read, link):
    rid, attempt = _number(link["witness_run_id"]), _number(link["witness_run_attempt"])
    run = read.run(r321path=WITNESS_PATH, name=WITNESS_NAME, event="workflow_dispatch", rid=rid, attempt=attempt)
    artifact = read.artifact(rid, run, name=WITNESS_ARTIFACT)
    require(0 < artifact["size_in_bytes"] <= 16384, "CONTROL_WITNESS_ZIP_BUDGET")
    archive = read.witness_reader.get_zip(artifact["id"])
    require(type(archive) is bytes and len(archive) == artifact["size_in_bytes"]
            and "sha256:" + _sha(archive) == artifact["digest"], "CONTROL_WITNESS_ZIP_DIGEST")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        entries = zf.infolist()
        require(len(entries) == 1, "CONTROL_WITNESS_ZIP_MEMBERS")
        entry = entries[0]
        require(entry.filename == witness.ARTIFACT_FILE and entry.orig_filename == entry.filename
                and not entry.is_dir() and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                and not entry.flag_bits & 1 and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                and 0 < entry.file_size <= 4096, "CONTROL_WITNESS_MEMBER_UNSAFE")
        with zf.open(entry) as stream:
            raw = stream.read(4097)
    expected = {"schema": "RC6_R305_ROOT_MANIFEST_INDEPENDENT_DIGEST_WITNESS_V1",
        "run_id": str(rid), "run_attempt": str(attempt), "trusted_main_sha": read.main, "trusted_main_tree": read.main_tree,
        "manifest_schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "manifest_sha256": link["root_manifest_sha256"],
        "candidate_sha": read.base["candidate_sha"], "candidate_tree": read.base["candidate_tree"],
        "verification_result": "PASS", **PAPER}
    require(raw == r305._canonical(expected) and _sha(raw) == link["witness_artifact_sha256"],
            "CONTROL_WITNESS_JSON_DIGEST")
    return raw


def _verify_prepare(env, release):
    read = _Read(env, "PREPARE_ENV")
    go_id = _number(env["RC6_R367_PREPARE_GO_ID"])
    row, doc = read.comment(go_id, PREPARE_MARKER)
    phase = env["RC6_R367_PREPARE_PHASE"]
    require(phase in ("INITIAL", "POST_D_E") and (release is None) == (phase == "INITIAL"), "CONTROL_PREPARE_PHASE")
    initial_document = _prep(read, row, doc, phase, active=True, release=_release(read, release) if release is not None else None)
    out = {"prepare_go_id": go_id, "lease_id": read.lease_id, "nonce": read.scope["nonce"],
           "issued_at": row["created_at"], "expires_at": doc["expires_at"]}
    if phase == "INITIAL":
        out["binding"] = _binding(read, env["GITHUB_RUN_ID"], env["GITHUB_RUN_ATTEMPT"], go_id, initial_document)
    else:
        origin = _origin(doc)
        require(env.get("RC6_R367_PREPARE_ID", origin["proposal_id"]) == origin["proposal_id"],
                "CONTROL_PROPOSAL_SELECTOR")
        out.update(binding=_binding(read, origin["prepare_run_id"], origin["prepare_run_attempt"], origin["prepare_go_id"], initial_document),
                   proposal_id=origin["proposal_id"], commitment=origin["commitment"],
                   initial_prepare_go_id=origin["prepare_go_id"], approval_expires_at=doc["approval_expires_at"])
    read.finish()
    out["public_versions"] = read.versions
    return out


def _verify_apply(env, release, initial):
    require(type(initial) is bool, "CONTROL_INITIAL_FLAG")
    read = _Read(env, "APPLY_GENESIS")
    pin_id, install_id, env_id = (_number(env["RC6_R367_" + role + "_GO_ID"]) for role in ("PIN", "INSTALL", "ENV"))
    require(read.lease_id < pin_id < install_id < env_id, "CONTROL_INDEPENDENT_GO_SEQUENCE")
    pin_row = read.read(f"/issues/comments/{pin_id}")
    legacy._owner_comment(pin_row, pin_id, 1093)
    install_row, install = read.comment(install_id, INSTALL_MARKER)
    env_row, ego = read.comment(env_id, ENV_MARKER)
    origin = _origin(ego)
    require(env.get("RC6_R367_PREPARE_ID", origin["proposal_id"]) == origin["proposal_id"], "CONTROL_PROPOSAL_SELECTOR")
    post_select = {"post_prepare_go_id": _id(ego["post_prepare_go_id"]),
                   "post_prepare_run_id": str(_number(ego["post_prepare_run_id"])),
                   "post_prepare_run_attempt": str(_number(ego["post_prepare_run_attempt"]))}
    require(origin["prepare_go_id"] < post_select["post_prepare_go_id"] < pin_id,
            "CONTROL_PREPARATION_ORDER")
    post_row, post = read.comment(post_select["post_prepare_go_id"], PREPARE_MARKER)
    bound_release = _release(read, release)
    initial_document = _prep(read, post_row, post, "POST_D_E", active=False, release=bound_release)
    _exact(_origin(post), origin)
    read.historical_run(post_select["post_prepare_run_id"], post_select["post_prepare_run_attempt"], post_row, post)
    link = {"root_manifest_sha256": _hex(ego["root_manifest_sha256"], 64),
            "witness_artifact_sha256": _hex(ego["witness_artifact_sha256"], 64),
            "witness_run_id": str(_number(ego["witness_run_id"])),
            "witness_run_attempt": str(_number(ego["witness_run_attempt"]))}
    expected = {**read.scope, **PAPER, **origin, **link, "generation_commitment": _hex(ego["generation_commitment"], 64),
                "lease_comment_id": read.lease_id}
    _exact(ego, {**expected, **post_select, "operation": "SEAL_ENV_PAIR", "pin_go_id": pin_id,
                "install_go_id": install_id, "expires_at": ego["expires_at"]})
    _exact(install, {**expected, **bound_release, "operation": "RC6_ROOT_INSTALL_NEW_FILES_ONLY",
                    "issue": 1093, "trusted_main_tree": read.main_tree, "host_control_verified": False,
                    "r305_pin_owner_go_id": pin_id, "expires_at": install["expires_at"]})
    ctx = r305.PinContext(read.main, read.main_tree, read.base["candidate_sha"], read.base["candidate_tree"],
            link["root_manifest_sha256"], link["witness_artifact_sha256"],
            env["RC6_EXPECTED_DO_ID"], env["RC6_EXPECTED_DO_REGION"])
    pgo = r305.verify_owner_go(pin_row, ctx, owner_go_id=pin_id, operation="NEW", now=_now())
    require(pgo["witness_run_id"] == link["witness_run_id"]
            and pgo["witness_run_attempt"] == link["witness_run_attempt"], "CONTROL_PIN_WITNESS_MISMATCH")
    raw = _witness(read, link)
    read.active(pin_row, pgo)
    read.active(install_row, install, remaining=r321.MIN_OWNER_GO_REMAINING if initial else timedelta())
    read.active(env_row, ego, maximum=timedelta(minutes=10))
    read.clocks.append((r295._utc_timestamp(post_row["created_at"]), r295._utc_timestamp(post["approval_expires_at"]),
                        timedelta(hours=4), timedelta()))
    read.finish()
    return {"public_versions": read.versions, "binding": _binding(read, origin["prepare_run_id"], origin["prepare_run_attempt"], origin["prepare_go_id"], initial_document),
            "proposal_id": origin["proposal_id"], "commitment": origin["commitment"],
            "generation_commitment": ego["generation_commitment"], "nonce": read.scope["nonce"],
            "issued_at": env_row["created_at"], "expires_at": min(ego["expires_at"], pgo["expires_at"],
                 install["expires_at"], post["approval_expires_at"]), **link, "witness_raw": raw,
            "pin_go_id": pin_id, "install_go_id": install_id, "env_go_id": env_id, "lease_id": read.lease_id,
            "prepare_go_id": origin["prepare_go_id"], "pin_owner_comment": pin_row, "install_owner_comment": install_row}


def _checked(action, *args):
    try:
        return action(*args)
    except ControlHold:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError, OSError,
            OverflowError, EOFError, RecursionError, zipfile.BadZipFile):
        raise ControlHold("CONTROL_AUTHORITY_UNVERIFIED") from None


def verify_prepare(env, release=None):
    """Reopen live authority before PREPARE's writes to its host-private proposal."""
    return _checked(_verify_prepare, env, release)


def verify_apply(env, release, *, initial=True):
    """Reopen the exact GOs, global lease and immutable artifacts at each physical boundary."""
    return _checked(_verify_apply, env, release, initial)

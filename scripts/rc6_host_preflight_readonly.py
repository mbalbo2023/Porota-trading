#!/usr/bin/env python3
"""R277: offline verifier of bounded *physical* RC6 host observations.

Read-only evidence is not a deploy permit, a root-owned installation, a
quiescence attestor, or proof of runtime health. GitHub Actions gathers files
using literal read-only commands; this module never runs SSH/sudo/Docker.
Missing data, mismatched attempts or unproven host authority HOLD79.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


SCHEMA = "RC6_HOST_PHYSICAL_PREFLIGHT_V1"
HELPER_PATH = "/usr/local/sbin/porota-rc6-oci-custody"
# The expected physical host identity MUST be passed by protected GitHub
# Actions secrets. Never commit a real Droplet identifier/region into a public
# repository or accept it from workflow_dispatch/freeform inputs. No fallback.
# No independent installation/protector approval exists yet. BOOTSTRAP may
# *observe* a helper hash, never trust it. Only a separately reviewed change
# on protected trusted-main may pin an approved installed helper SHA here.
# A user-controlled workflow_dispatch field MUST NOT establish that trust.
TRUSTED_HELPER_SHA256 = None
_PHASES = frozenset({"BOOTSTRAP", "PREBIRTH", "POSTSEAL"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DO_ID = re.compile(r"[1-9][0-9]{5,19}\Z")
_DO_REGION = re.compile(r"[a-z]{2,12}[0-9]{1,2}\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_DOCKER_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DAEMON = re.compile(r"[A-Za-z0-9:_./-]{6,128}\Z")
_PREBIRTH_KEYS = frozenset({
    "schema", "status", "candidate_sha", "runtime_writers", "paper_mode",
    "real_orders_sent", "ppi_watch_accessed",
})
_CURRENT_KEYS = frozenset({
    "schema", "status", "candidate_sha", "candidate_tree", "generation",
    "artifact_digest", "image_id", "daemon_id", "declared_paper_mode",
    "roles",
})
_READ_LIMITS = {
    "droplet_id.txt": 32, "region.txt": 32,
    "helper.stat": 128, "helper.sha": 256,
    "prebirth.json": 2048, "current.json": 4096,
}


class PreflightHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,65}", reason):
            reason = "UNVERIFIED"
        self.reason = reason
        super().__init__("RC6_HOST_PREFLIGHT_HOLD79:" + reason)


def _require(condition, reason):
    if not condition:
        raise PreflightHold(reason)


@dataclass(frozen=True)
class Expected:
    phase: str
    droplet_id: str
    region: str
    candidate_sha: str | None = None
    candidate_tree: str | None = None
    generation: str | None = None
    artifact_digest: str | None = None
    image_id: str | None = None
    daemon_id: str | None = None
    helper_sha256: str | None = None


_EXPECTED_ENV = (
    ("candidate_sha", "RC6_EXPECTED_CANDIDATE_SHA", _HEX40),
    ("candidate_tree", "RC6_EXPECTED_CANDIDATE_TREE", _HEX40),
    ("generation", "RC6_EXPECTED_GENERATION", _GENERATION),
    ("artifact_digest", "RC6_EXPECTED_ARTIFACT_DIGEST", _DOCKER_DIGEST),
    ("image_id", "RC6_EXPECTED_IMAGE_ID", _DOCKER_DIGEST),
    ("daemon_id", "RC6_EXPECTED_DAEMON_ID", _DAEMON),
)
_REQUIRED_BY_PHASE = {
    "BOOTSTRAP": frozenset(),
    "PREBIRTH": frozenset({"candidate_sha", "candidate_tree"}),
    "POSTSEAL": frozenset({
        "candidate_sha", "candidate_tree", "generation", "artifact_digest",
        "image_id", "daemon_id",
    }),
}


def validate_expected(environment):
    """Select stage-specific inputs before SSH; never authorize freeform hash."""
    phase = environment.get("RC6_PREFLIGHT_PHASE")
    _require(type(phase) is str and phase in _PHASES,
             "PHASE_UNKNOWN")
    # These are authority inputs exclusively from protected Actions secrets;
    # missing, malformed or empty pins must HOLD79 even for BOOTSTRAP.
    droplet_id = environment.get("RC6_EXPECTED_DO_ID")
    region = environment.get("RC6_EXPECTED_DO_REGION")
    _require(type(droplet_id) is str and _DO_ID.fullmatch(droplet_id),
             "DROPLET_TRUSTED_PIN_UNVERIFIED")
    _require(type(region) is str and _DO_REGION.fullmatch(region),
             "REGION_TRUSTED_PIN_UNVERIFIED")
    _require(not environment.get("RC6_EXPECTED_HELPER_SHA256"),
             "DISPATCH_CANNOT_APPROVE_HOST_HELPER")
    fields = {}
    for field, key, pattern in _EXPECTED_ENV:
        value = environment.get(key)
        if field in _REQUIRED_BY_PHASE[phase]:
            _require(type(value) is str and pattern.fullmatch(value),
                     "EXPECTED_SELECTOR_UNVERIFIED")
            fields[field] = value
        else:
            _require(value in (None, ""), "UNEXPECTED_PHASE_SELECTOR")
    pin = None
    if phase != "BOOTSTRAP":
        _require(type(TRUSTED_HELPER_SHA256) is str and
                 _HEX64.fullmatch(TRUSTED_HELPER_SHA256),
                 "ROOT_HELPER_TRUSTED_MAIN_PIN_ABSENT")
        pin = TRUSTED_HELPER_SHA256
    return Expected(phase=phase, droplet_id=droplet_id, region=region,
                    helper_sha256=pin, **fields)


def _read_bounded(folder, name):
    """Runner evidence is bounded, a regular non-aliased file, not a path input."""
    _require(isinstance(folder, Path) and name in _READ_LIMITS,
             "EVIDENCE_PATH_UNVERIFIED")
    path = folder / name
    try:
        inode = path.lstat()
        _require(stat.S_ISREG(inode.st_mode) and inode.st_nlink == 1 and
                 0 < inode.st_size <= _READ_LIMITS[name],
                 "EVIDENCE_FILE_UNVERIFIED")
        with path.open("rb") as stream:
            raw = stream.read(_READ_LIMITS[name] + 1)
        _require(len(raw) == inode.st_size, "EVIDENCE_FILE_CHANGED")
        return raw
    except OSError as exc:
        raise PreflightHold("EVIDENCE_FILE_MISSING") from exc


def _text(folder, name):
    try:
        raw = _read_bounded(folder, name).decode("ascii", "strict")
    except UnicodeError as exc:
        raise PreflightHold("EVIDENCE_ENCODING") from exc
    _require("\r" not in raw and "\x00" not in raw,
             "EVIDENCE_ENCODING")
    # Exactly one optional trailing newline; no log banner or extra stdout.
    value = raw[:-1] if raw.endswith("\n") else raw
    _require("\n" not in value, "EVIDENCE_OUTPUT_UNBOUNDED")
    return value


def _unique_pairs(pairs):
    doc = {}
    for key, value in pairs:
        _require(key not in doc, "RECEIPT_DUPLICATE_KEY")
        doc[key] = value
    return doc


def _reject_constant(_value):
    raise PreflightHold("RECEIPT_NONFINITE")


def _receipt(folder, name):
    raw = _read_bounded(folder, name)
    try:
        doc = json.loads(raw.decode("utf-8", "strict"),
                         object_pairs_hook=_unique_pairs,
                         parse_constant=_reject_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PreflightHold("RECEIPT_JSON_UNVERIFIED") from exc
    _require(type(doc) is dict, "RECEIPT_SCHEMA_UNVERIFIED")
    return doc


def _validate_host_identity(folder, expected):
    _require(_text(folder, "droplet_id.txt") == expected.droplet_id and
             _text(folder, "region.txt") == expected.region,
             "DROPLET_IDENTITY_UNVERIFIED")


def _validate_installation(folder, expected):
    _validate_host_identity(folder, expected)
    observed = _text(folder, "helper.stat")
    parts = observed.split("|")
    _require(len(parts) == 4 and parts[0] == "0" and parts[2] == "1" and
             parts[3] == "regular file" and
             re.fullmatch(r"[0-7]{3,4}", parts[1]),
             "ROOT_HELPER_INODE_UNVERIFIED")
    mode = int(parts[1], 8)
    _require(mode & 0o100 and not mode & 0o022,
             "ROOT_HELPER_MODE_UNVERIFIED")
    observed_digest = _text(folder, "helper.sha")
    digest_and_name = observed_digest.split("  ", 1)
    _require(len(digest_and_name) == 2 and
             _HEX64.fullmatch(digest_and_name[0]) and
             digest_and_name[1] == HELPER_PATH,
             "ROOT_HELPER_DIGEST_UNVERIFIED")
    observed_sha = digest_and_name[0]
    if expected.phase != "BOOTSTRAP":
        _require(observed_sha == expected.helper_sha256,
                 "ROOT_HELPER_TRUSTED_PIN_MISMATCH")
    return observed_sha


def _validate_prebirth(proof, expected):
    _require(type(proof) is dict and set(proof) == _PREBIRTH_KEYS and
             proof["schema"] == "RC6_PREBIRTH_QUIESCENCE_V1" and
             proof["status"] == "OK" and
             type(proof["candidate_sha"]) is str and
             proof["candidate_sha"] == expected.candidate_sha and
             proof["runtime_writers"] == "QUIESCENT" and
             proof["paper_mode"] == "PRODUCTION_PAPER" and
             type(proof["real_orders_sent"]) is int and
             proof["real_orders_sent"] == 0 and
             proof["ppi_watch_accessed"] is False,
             "PREBIRTH_PHYSICAL_PROOF_UNVERIFIED")


def _validate_current(proof, expected):
    _require(type(proof) is dict and set(proof) == _CURRENT_KEYS and
             proof["schema"] == "RC6_POSTSEAL_CUSTODY_V1" and
             proof["status"] == "OK" and
             proof["candidate_sha"] == expected.candidate_sha and
             proof["candidate_tree"] == expected.candidate_tree and
             proof["generation"] == expected.generation and
             proof["artifact_digest"] == expected.artifact_digest and
             proof["image_id"] == expected.image_id and
             proof["daemon_id"] == expected.daemon_id and
             proof["declared_paper_mode"] == "PRODUCTION_PAPER" and
             type(proof["roles"]) is dict and
             set(proof["roles"]) == {"dashboard", "observer", "critical"},
             "CURRENT_EXACT_CUSTODY_UNVERIFIED")
    fingerprints = {}
    cids = []
    for role in ("dashboard", "observer", "critical"):
        record = proof["roles"][role]
        _require(type(record) is dict and set(record) == {"cid", "state"} and
                 type(record["cid"]) is str and
                 _HEX64.fullmatch(record["cid"]) and
                 type(record["state"]) is str and record["state"] == "running",
                 "CURRENT_THREE_CIDS_UNVERIFIED")
        cids.append(record["cid"])
        fingerprints[role] = hashlib.sha256(bytes.fromhex(record["cid"])).hexdigest()[:16]
    _require(len(set(cids)) == 3, "CURRENT_THREE_CIDS_UNVERIFIED")
    return fingerprints


def validate_evidence(folder, expected):
    """Never require future POSTSEAL CIDs to authorize PREBIRTH observation."""
    _require(isinstance(expected, Expected), "EXPECTED_SELECTOR_UNVERIFIED")
    observed_helper_sha = _validate_installation(folder, expected)
    active_file = {"BOOTSTRAP": None, "PREBIRTH": "prebirth.json",
                   "POSTSEAL": "current.json"}[expected.phase]
    # Do not recycle or accidentally mix observations from different phases.
    for name in ("prebirth.json", "current.json"):
        if name != active_file:
            path = folder / name
            _require(not path.exists() and not path.is_symlink(),
                     "CROSS_PHASE_RECEIPT_FORBIDDEN")

    report = {
        "schema": SCHEMA,
        "phase": expected.phase,
        "status": {
            "BOOTSTRAP": "BOOTSTRAP_OBSERVATION_ONLY",
            "PREBIRTH": "PREBIRTH_EVIDENCE_ONLY",
            "POSTSEAL": "POSTSEAL_EVIDENCE_ONLY",
        }[expected.phase],
        "helper_sha256_observed": observed_helper_sha,
        "paper_mode": "NO_VERIFICADO",
        "real_orders_sent": "NO_VERIFICADO",
        "ppi_watch_accessed": "NO_VERIFICADO",
        "host_control_verified": False,
        "runtime_verified": False,
        "deploy_authorized": False,
    }
    if expected.phase != "BOOTSTRAP":
        report.update(candidate_sha=expected.candidate_sha,
                      candidate_tree_requested=expected.candidate_tree,
                      helper_sha256_trusted_main_pin=expected.helper_sha256)
    if expected.phase == "PREBIRTH":
        prebirth = _receipt(folder, "prebirth.json")
        _validate_prebirth(prebirth, expected)
        report.update(paper_mode="PRODUCTION_PAPER", real_orders_sent=0,
                      ppi_watch_accessed=False)
    elif expected.phase == "POSTSEAL":
        current = _receipt(folder, "current.json")
        fingerprints = _validate_current(current, expected)
        report.update(generation=expected.generation,
                      artifact_digest=expected.artifact_digest,
                      image_id=expected.image_id,
                      daemon_fingerprint=hashlib.sha256(
                          expected.daemon_id.encode()).hexdigest()[:16],
                      role_cid_fingerprints=fingerprints,
                      declared_paper_mode="PRODUCTION_PAPER")

    # A reproducible digest binds exact, unredacted bytes while public output
    # discloses no host IP, login, full CID or runtime private contents.
    digest = hashlib.sha256()
    common = ("droplet_id.txt", "region.txt", "helper.stat", "helper.sha")
    names = common + ((active_file,) if active_file is not None else ())
    for name in names:
        raw = _read_bounded(folder, name)
        digest.update(name.encode("ascii") + b"\x00" + str(len(raw)).encode("ascii") + b"\x00" + raw)
    report["source_receipts_sha256"] = digest.hexdigest()
    return report


def main(argv=None, *, env=None):
    if argv is None:
        argv = sys.argv[1:]
    if env is None:
        env = os.environ
    try:
        expected = validate_expected(env)
        if argv == ["--check-inputs"]:
            print("RC6_HOST_PREFLIGHT_INPUTS=GREEN|host_control_verified=NO_VERIFICADO")
            return 0
        if len(argv) != 3 or argv[0] not in (
                "--verify", "--check-host", "--check-helper") or argv[1] != "--evidence-dir":
            raise PreflightHold("CLI_SHAPE")
        folder = Path(argv[2])
        _require(folder.is_absolute() and folder.is_dir() and not folder.is_symlink(),
                 "EVIDENCE_DIRECTORY_UNVERIFIED")
        if argv[0] == "--check-host":
            _validate_host_identity(folder, expected)
            print("RC6_HOST_IDENTITY=PINNED|host_control_verified=NO_VERIFICADO")
            return 0
        if argv[0] == "--check-helper":
            _validate_installation(folder, expected)
            print("RC6_HOST_HELPER=OBSERVED|host_control_verified=NO_VERIFICADO")
            return 0
        report = validate_evidence(folder, expected)
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except PreflightHold as exc:
        print("RC6_HOST_PREFLIGHT=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79
    except (OSError, ValueError, TypeError):
        print("RC6_HOST_PREFLIGHT=HOLD79|reason=UNVERIFIED|"
              "host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79


if __name__ == "__main__":
    sys.exit(main())

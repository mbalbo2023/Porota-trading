"""R308: propose reproducible, non-authoritative ROOT launch-profile bytes.

This is an OFFHOST, CODE-ONLY builder. It never reads env files, stages
root-owned files, changes systemd, calls Docker, or authorizes a launch.
The independently root-installed R280 approval and physical witness remain
the only possible future authorities for these bytes.
"""

from dataclasses import dataclass
import hashlib
import json
import re

from scripts import rc6_oci_host_helper as host_contract


class FreezeHold(RuntimeError):
    """Invalid proposal or missing externally reviewed identity (exit 79)."""

    exit_code = 79

    def __init__(self, reason):
        if type(reason) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason) is None:
            reason = "UNVERIFIED"
        self.reason = reason
        super().__init__("RC6_R308_PROFILE_HOLD79:" + reason)


@dataclass(frozen=True)
class FrozenLaunchProfileProposal:
    profiles_bytes: bytes
    unit_template_bytes: bytes
    profiles_sha256: str
    unit_template_sha256: str
    host_go: bool = False
    deploy_authorized: bool = False


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_IMMUTABLE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DAEMON = re.compile(r"[A-Za-z0-9:_./-]{6,128}\Z")
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\Z")
_SOURCES = ("/app/cf_intraday_scalping.py", "/app/co_market_sessions_hf6.py")
_ROOT_GENERATIONS = "/var/lib/porota-rc6-oci-custody/generations"
_APPROVAL_PATH = "/var/lib/porota-rc6-oci-custody/host-helper-approval.json"


def _require(condition, reason):
    if not condition:
        raise FreezeHold(reason)


def _validate_stage(path):
    _require(type(path) is str and len(path) <= 150 and
             (path == "/opt/porota-trading" or
              path.startswith("/opt/porota-trading/")), "PROFILE_STAGE_ROOT")
    remainder = path[len("/opt/porota-trading"):]
    if remainder:
        _require(all(component not in (".", "..") and
                     _PATH_COMPONENT.fullmatch(component)
                     for component in remainder[1:].split("/")), "PROFILE_STAGE_ROOT")
    return path


def _canonical_json(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def freeze_root_launch_profiles(*, candidate_sha, candidate_tree,
                                artifact_digest, image_id, daemon_id,
                                generation, root_stage,
                                dashboard_env_sha256, observer_env_sha256,
                                observer_source_sha256, critical_source_sha):
    """Freeze two reviewed source-role bytes without staging or granting GO.

    Env **contents** and tokens are never arguments: only independently
    reviewed SHA-256 digests may enter the public proposal. The root installer
    later verifies raw sealed env bytes and separate short-lived approval.
    """
    fields = ((candidate_sha, _HEX40), (candidate_tree, _HEX40),
              (artifact_digest, _IMMUTABLE_ID), (image_id, _IMMUTABLE_ID),
              (daemon_id, _DAEMON), (generation, _HEX32),
              (dashboard_env_sha256, _HEX64), (observer_env_sha256, _HEX64),
              (critical_source_sha, _HEX40))
    _require(all(type(value) is str and pattern.fullmatch(value)
                 for value, pattern in fields), "PROFILE_SOURCE_IDENTITY")
    # The actual candidate helper checks this for BOTH critical start-newborn
    # and stage-protector. A distinct, syntactically valid SHA stays HOLD79.
    _require(critical_source_sha == candidate_sha,
             "CRITICAL_SOURCE_CANDIDATE_MISMATCH")
    _validate_stage(root_stage)
    _require(type(observer_source_sha256) is dict and
             set(observer_source_sha256) == set(_SOURCES) and
             all(type(value) is str and _HEX64.fullmatch(value)
                 for value in observer_source_sha256.values()),
             "PROFILE_OBSERVER_SOURCE_CLOSURE")

    # Copy all external inputs into fresh, plain values before validation and
    # serialization; no mutable caller object enters the approved bytes.
    source_hashes = {key: observer_source_sha256[key] for key in _SOURCES}
    base_environment = {
        "POROTA_RUNTIME_MODE": "PRODUCTION_PAPER",
        "PPI_ORDERS": "BLOCKED",
        "REAL_ORDERS_ALLOWED": "false",
    }
    generation_env = _ROOT_GENERATIONS + "/" + generation + "/env/"
    data_mount = {"source": root_stage + "/data",
                  "target": "/app/data", "readonly": False}
    critical_environment = dict(base_environment)
    critical_environment.update(host_contract.ApprovedLaunchProfiles._CRITICAL_REQUIRED_ENV)
    critical_mounts = [
        {"source": source, "target": target, "readonly": readonly}
        for source, target, readonly in sorted(
            host_contract.ApprovedLaunchProfiles._CRITICAL_REQUIRED_MOUNTS,
            key=lambda item: item[1])
    ]
    profile_document = {
        "schema_version": 1,
        "candidate_sha": candidate_sha,
        "artifact_digest": artifact_digest,
        "image_tag": "porota-trading-bot:17.0.0-rc6-candidate-" + candidate_sha,
        "root_stage": root_stage,
        "observer_source_sha256": source_hashes,
        "roles": {
            "dashboard": {
                "env_file": generation_env + "dashboard.env",
                "env_sha256": dashboard_env_sha256,
                "environment": dict(base_environment),
                "mounts": [dict(data_mount)],
            },
            "observer": {
                "env_file": generation_env + "observer.env",
                "env_sha256": observer_env_sha256,
                "environment": {
                    **base_environment,
                    "PPI_PRODUCTION_SECRET_FILE": "/run/secrets/ppi_production.json",
                    "MARKET_OPEN_HOUR": "10",
                    "MARKET_OPEN_MINUTE": "30",
                },
                "mounts": [dict(data_mount), {
                    "source": root_stage + "/.secrets/ppi_production.json",
                    "target": "/run/secrets/ppi_production.json",
                    "readonly": True,
                }],
            },
            "critical": {
                "env_file": None,
                "env_sha256": None,
                "environment": critical_environment,
                "mounts": critical_mounts,
            },
        },
    }
    try:
        # This is the exact candidate's real consumer contract, not a mock.
        host_contract.ApprovedLaunchProfiles(
            profile_document, candidate_sha=candidate_sha,
            artifact_digest=artifact_digest, image_id=image_id,
            generation=generation)
    except (host_contract.CustodyHold, ValueError, TypeError, OverflowError) as exc:
        raise FreezeHold("PROFILE_RUNTIME_CONTRACT_REJECTED") from exc
    profiles_raw = _canonical_json(profile_document)

    # Reuse the candidate's existing critical protector-stage semantics:
    # ExecCondition stays HOLD79 until a separate, independently reviewed GO.
    # Systemd consumes '$', so the existing shell arithmetic uses '$$' in unit
    # bytes. Nothing here runs that shell, enables the unit or stages systemd.
    # The exact root-installed helper additionally enforces its own approval.
    unit_raw = ("# RC6_ROOT_UNIT_TEMPLATE_V1 CODE_ONLY NO_HOST_GO\n"
                "# candidate_sha=" + candidate_sha + "\n"
                "# candidate_tree=" + candidate_tree + "\n"
                "# artifact_digest=" + artifact_digest + "\n"
                "# image_id=" + image_id + "\n"
                "# daemon_id=" + daemon_id + "\n"
                "# generation=" + generation + "\n"
                "[Unit]\n"
                "Description=Porota RC6 PAPER critical approval staged template\n"
                "Requires=docker.service porota-critical-github-proxy-rc6.service\n"
                "After=docker.service porota-critical-github-proxy-rc6.service\n"
                "ConditionPathExists=" + _APPROVAL_PATH + "\n"
                "[Service]\n"
                "Type=oneshot\nUser=root\nGroup=root\n"
                "RemainAfterExit=yes\nNoNewPrivileges=yes\n"
                "ExecCondition=/bin/sh -c 'echo "
                "RC6_OCI_CUSTODY_HOLD79:CRITICAL_CUTOVER_NOT_AUTHORIZED"
                " >&2; exit 79'\n"
                "ExecStartPre=/bin/sh -c 'i=0; while [ ! -S "
                "/run/porota-critical-approval-rc6/github.sock ]; do "
                "i=$$((i+1)); [ $$i -lt 30 ] || exit 1; "
                "sleep 1; done'\n"
                "ExecStart=/usr/local/sbin/porota-rc6-oci-custody critical start-newborn"
                " --paper-only --source-sha " + critical_source_sha +
                " --generation " + generation + "\n"
                "Restart=no\nRestartSec=5\n"
                "TimeoutStartSec=60\nTimeoutStopSec=30\n").encode("ascii")
    _require(len(profiles_raw) <= 128 * 1024 and len(unit_raw) <= 128 * 1024,
             "PROFILE_OUTPUT_TOO_LARGE")
    return FrozenLaunchProfileProposal(
        profiles_bytes=profiles_raw, unit_template_bytes=unit_raw,
        profiles_sha256=hashlib.sha256(profiles_raw).hexdigest(),
        unit_template_sha256=hashlib.sha256(unit_raw).hexdigest())

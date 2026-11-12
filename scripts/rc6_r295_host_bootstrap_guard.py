#!/usr/bin/env python3
"""R295 trusted-main root-bootstrap authority guard; no deployment GO.

This module checks a frozen operator receipt, protected host-identity pins,
and the EXACT R280 installer/contract bytes.  It never derives the root trust
pin from a candidate, a dispatch field, or a newly uploaded package.  Even a
successful bootstrap is INSTALLED_FILES_ONLY, not host_control_verified.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


REPOSITORY = "mbalbo2023/Porota-trading"
ISSUE = 1093
OWNER = "mbalbo2023"
OWNER_GO_MARKER = "RC6_R295_HOST_BOOTSTRAP_GO_V1="
TRUSTED_INSTALLER_CLI_SHA256 = (
    "0d46b0f99ce09fdf4d851255e79fba4fe8af98fd7ab62c98f3c1938c484f8c7a"
)
TRUSTED_INSTALLER_CONTRACT_SHA256 = (
    "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9"
)
SOURCE_FILES = {
    "cli": "rc6_host_rootowned_installer_cli.py",
    "contract": "rc6_host_rootowned_installer_contract.py",
}
# Physical root commands intentionally DO NOT live in this guard.  A later
# reviewed implementation must solve independent trust pinning, nofollow,
# atomic fsync publication, stale/replay protection, and bounded host scope
# BEFORE exposing any executable bootstrap entrypoint.
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"[1-9][0-9]{0,19}\Z")
_DO_REGION = re.compile(r"[a-z]{2,12}[0-9]{1,2}\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,30}\Z")


class BootstrapHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        self.reason = reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason) else "UNVERIFIED"
        super().__init__("RC6_R295_HOLD79:" + self.reason)


def _require(ok, reason):
    if not ok:
        raise BootstrapHold(reason)


@dataclass(frozen=True)
class BootstrapContext:
    main_sha: str
    owner_comment_id: int
    droplet_id: str
    region: str
    host: str
    user: str
    host_control_verified: bool = False


def validate_context(env):
    """Check all authority selectors before network and certainly before sudo."""
    _require(type(env) is dict or hasattr(env, "get"), "CONTEXT_UNVERIFIED")
    _require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             env.get("GITHUB_REF") == "refs/heads/main" and
             env.get("GITHUB_ACTOR") == OWNER, "TRUSTED_MAIN_OWNER_REQUIRED")
    sha = env.get("GITHUB_SHA")
    _require(type(sha) is str and _HEX40.fullmatch(sha), "MAIN_SHA_UNVERIFIED")
    comment_raw = env.get("RC6_R295_OWNER_COMMENT_ID")
    _require(type(comment_raw) is str and _DECIMAL.fullmatch(comment_raw),
             "OWNER_GO_COMMENT_ID_UNVERIFIED")
    droplet = env.get("RC6_EXPECTED_DO_ID")
    region = env.get("RC6_EXPECTED_DO_REGION")
    _require(type(droplet) is str and re.fullmatch(r"[1-9][0-9]{5,19}", droplet),
             "DROPLET_TRUSTED_PIN_UNVERIFIED")
    _require(type(region) is str and _DO_REGION.fullmatch(region),
             "REGION_TRUSTED_PIN_UNVERIFIED")
    host = env.get("DO_HOST")
    user = env.get("DO_USER")
    _require(type(host) is str and _HOST.fullmatch(host) and
             type(user) is str and _USER.fullmatch(user),
             "SSH_SELECTOR_UNVERIFIED")
    for name in ("DO_SSH_KEY", "DO_KNOWN_HOSTS", "GITHUB_TOKEN"):
        value = env.get(name)
        _require(type(value) is str and 0 < len(value) < 32768 and
                 "\x00" not in value and "\r" not in value,
                 "PROTECTED_SECRET_UNVERIFIED")
    return BootstrapContext(main_sha=sha, owner_comment_id=int(comment_raw),
                            droplet_id=droplet, region=region, host=host, user=user)


def _strict_json(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= 4096, "GO_RECEIPT_SIZE")

    def _unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "GO_RECEIPT_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8", "strict"),
                          object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _v: _require(False, "GO_RECEIPT_NONFINITE"))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise BootstrapHold("GO_RECEIPT_JSON_INVALID") from exc


def verify_owner_go(comment, main_sha, expected_comment_id):
    """Operator-approved exact reviewed main SHA; bootstrap files only."""
    _require(type(main_sha) is str and _HEX40.fullmatch(main_sha) and
             type(expected_comment_id) is int and expected_comment_id > 0,
             "EXPECTED_RECEIPT_IDENTITY")
    _require(type(comment) is dict and comment.get("id") == expected_comment_id and
             type(comment.get("user")) is dict and
             comment["user"].get("login") == OWNER and
             comment.get("author_association") == "OWNER" and
             comment.get("issue_url") ==
             f"https://api.github.com/repos/{REPOSITORY}/issues/{ISSUE}" and
             type(comment.get("created_at")) is str and
             comment.get("created_at") == comment.get("updated_at"),
             "OWNER_GO_AUTHORITY_UNVERIFIED")
    body = comment.get("body")
    _require(type(body) is str and body.startswith(OWNER_GO_MARKER) and
             len(body) <= 4096, "OWNER_GO_BODY_UNVERIFIED")
    actual = _strict_json(body[len(OWNER_GO_MARKER):].encode("utf-8"))
    expected = {
        "operation": "TRUSTED_MAIN_ROOT_BOOTSTRAP_FILES_ONLY",
        "issue": ISSUE, "trusted_main_sha": main_sha,
        "installer_cli_sha256": TRUSTED_INSTALLER_CLI_SHA256,
        "installer_contract_sha256": TRUSTED_INSTALLER_CONTRACT_SHA256,
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "host_control_verified": False,
    }
    _require(type(actual) is dict and actual == expected and
             all(type(actual[key]) is type(expected[key]) for key in expected),
             "OWNER_GO_SCOPE_OR_SHA_MISMATCH")
    return actual


def _read_reviewed_file(path, max_size):
    _require(isinstance(path, Path) and max_size > 0, "SOURCE_PATH_SHAPE")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    _require(type(nofollow) is int and nofollow != 0, "OS_NOFOLLOW_REQUIRED")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0))
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                     0 < before.st_size <= max_size, "SOURCE_REGULAR_FILE_REQUIRED")
            raw = os.read(fd, max_size + 1)
            after = os.fstat(fd)
            _require(len(raw) == before.st_size and
                     before.st_ino == after.st_ino and
                     before.st_size == after.st_size, "SOURCE_CHANGED")
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise BootstrapHold("SOURCE_FILE_UNVERIFIED") from exc


def verify_reviewed_sources(root):
    """Only the two existing R280 reviewed byte blobs, not a live bundle."""
    _require(isinstance(root, Path) and root.is_dir(), "SOURCE_ROOT_UNVERIFIED")
    files = {role: _read_reviewed_file(root / "scripts" / source, 1024 * 1024)
             for role, source in SOURCE_FILES.items()}
    _require(hashlib.sha256(files["cli"]).hexdigest() ==
             TRUSTED_INSTALLER_CLI_SHA256 and
             hashlib.sha256(files["contract"]).hexdigest() ==
             TRUSTED_INSTALLER_CONTRACT_SHA256,
             "REVIEWED_SOURCE_SHA_MISMATCH")
    _require(files["cli"].startswith(b"#!/usr/bin/python3 -I\n") and
             ("TRUSTED_CONTRACT_SHA256 = \"" +
              TRUSTED_INSTALLER_CONTRACT_SHA256 + "\"").encode() in files["cli"],
             "ROOT_CLI_SOURCE_CONTRACT_PIN_MISMATCH")
    return files


def _one_ascii_line(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= 32, "HOST_IDENTITY_SIZE")
    try:
        value = raw.decode("ascii", "strict")
    except UnicodeError as exc:
        raise BootstrapHold("HOST_IDENTITY_ENCODING") from exc
    if value.endswith("\n"):
        value = value[:-1]
    _require("\r" not in value and "\n" not in value and "\x00" not in value,
             "HOST_IDENTITY_MULTILINE")
    return value


def verify_host_identity(observed_id, observed_region, context):
    _require(type(context) is BootstrapContext and
             _one_ascii_line(observed_id) == context.droplet_id and
             _one_ascii_line(observed_region) == context.region,
             "DROPLET_IDENTITY_UNVERIFIED")


def main(argv=None, *, env=None):
    """Current public CLI intentionally stops before physical bootstrap.

    This non-host preflight exercises the review/authority logic in Actions;
    the fixed root verbs are not activated until independently reviewed as
    an executable bootstrap transaction with physical negative evidence.
    """
    if argv is None:
        argv = sys.argv[1:]
    if env is None:
        env = os.environ
    try:
        _require(argv == ["--check-offhost"], "NO_ROOT_EXECUTION_ENTRY")
        context = validate_context(env)
        verify_reviewed_sources(Path(__file__).resolve().parents[1])
        # This guard never opens SSH or invokes ROOT_COMMANDS from checkout.
        # A trusted GitHub REST review job must provide an immutable owner
        # comment; this code-only dry gate cannot self-approve its own GO.
        _require(False, "PHYSICAL_ROOT_BOOTSTRAP_AUTHORITY_UNVERIFIED")
    except BootstrapHold as exc:
        print("RC6_R295_BOOTSTRAP=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79
    except Exception:
        print("RC6_R295_BOOTSTRAP=HOLD79|reason=UNHANDLED_FAIL_CLOSED|"
              "host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79


if __name__ == "__main__":
    raise SystemExit(main())

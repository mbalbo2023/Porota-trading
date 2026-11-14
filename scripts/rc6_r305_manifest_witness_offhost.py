"""Independent R305 root-install manifest digest witness: OFFHOST ONLY.

This program can run only in an owner-dispatched trusted-main Actions job.
It validates a proposed five-role manifest, its exact bytes/digest, the
candidate GitHub commit tree and the checkout identity. It emits a small
non-secret JSON artifact. It neither reviews source roles nor supplies an
OWNER GO, a cryptographic signature, HOST GO, or any deployment permission.

No SSH, sudo, Docker, systemd, host control or runtime operation exists here.
"""

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys


REPOSITORY = "mbalbo2023/Porota-trading"
ARTIFACT_FILE = "rc6-r305-root-manifest-digest-witness.json"
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_POSITIVE = re.compile(r"[1-9][0-9]{0,19}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,70}\Z")
_ROLES = frozenset(("helper", "custody", "profiles", "unit_template", "approval"))
_MANIFEST_KEYS = frozenset((
    "schema", "candidate_sha", "candidate_tree", "paper_mode",
    "real_orders_allowed", "files",
))


class WitnessHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        value = reason if type(reason) is str and _REASON.fullmatch(reason) else "UNVERIFIED"
        self.reason = value
        super().__init__("RC6_R305_WITNESS_HOLD79:" + value)


def _require(ok, reason):
    if not ok:
        raise WitnessHold(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique_json(raw):
    def no_duplicates(pairs):
        doc = {}
        for key, value in pairs:
            _require(key not in doc, "MANIFEST_DUPLICATE_KEY")
            doc[key] = value
        return doc

    def reject_constant(_value):
        raise WitnessHold("MANIFEST_NONFINITE")

    try:
        return json.loads(raw.decode("utf-8", "strict"),
                          object_pairs_hook=no_duplicates,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise WitnessHold("MANIFEST_JSON_INVALID") from None


def _validate_manifest(raw, expected_sha256):
    _require(type(raw) is bytes and 0 < len(raw) <= 8192,
             "ROOT_MANIFEST_SIZE")
    _require(type(expected_sha256) is str and
             _HEX64.fullmatch(expected_sha256),
             "ROOT_MANIFEST_EXPECTED_DIGEST_INVALID")
    _require(_sha(raw) == expected_sha256, "ROOT_MANIFEST_DIGEST_MISMATCH")
    doc = _unique_json(raw)
    _require(type(doc) is dict and set(doc) == _MANIFEST_KEYS and
             doc["schema"] == "RC6_ROOT_INSTALL_MANIFEST_V1" and
             type(doc["candidate_sha"]) is str and
             _HEX40.fullmatch(doc["candidate_sha"]) and
             type(doc["candidate_tree"]) is str and
             _HEX40.fullmatch(doc["candidate_tree"]) and
             doc["paper_mode"] == "PRODUCTION_PAPER" and
             doc["real_orders_allowed"] is False and
             type(doc["files"]) is dict and set(doc["files"]) == _ROLES and
             all(type(value) is str and _HEX64.fullmatch(value)
                 for value in doc["files"].values()),
             "ROOT_MANIFEST_SEMANTICS_INVALID")
    canonical = (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                            allow_nan=False, ensure_ascii=True) + "\n").encode("ascii")
    _require(raw == canonical, "ROOT_MANIFEST_NOT_CANONICAL")
    return doc


def build_witness(manifest_bytes, *, expected_sha256, main_sha, main_tree,
                  run_id, run_attempt, candidate_tree_getter):
    """Return R305's exact non-secret witness JSON; never write or authorize."""
    _require(type(main_sha) is str and _HEX40.fullmatch(main_sha) and
             type(main_tree) is str and _HEX40.fullmatch(main_tree) and
             type(run_id) is str and _POSITIVE.fullmatch(run_id) and
             type(run_attempt) is str and _POSITIVE.fullmatch(run_attempt),
             "TRUSTED_MAIN_RUN_IDENTITY_INVALID")
    _require(callable(candidate_tree_getter), "CANDIDATE_GITHUB_READER_REQUIRED")
    manifest = _validate_manifest(manifest_bytes, expected_sha256)
    try:
        real_tree = candidate_tree_getter(manifest["candidate_sha"])
    except Exception:
        # API errors could contain credential headers. Never expose a chain.
        raise WitnessHold("CANDIDATE_GITHUB_TREE_UNAVAILABLE") from None
    _require(type(real_tree) is str and
             real_tree == manifest["candidate_tree"],
             "CANDIDATE_GITHUB_TREE_MISMATCH")
    artifact = {
        "schema": "RC6_R305_ROOT_MANIFEST_INDEPENDENT_DIGEST_WITNESS_V1",
        "run_id": run_id,
        "run_attempt": run_attempt,
        "trusted_main_sha": main_sha,
        "trusted_main_tree": main_tree,
        "manifest_schema": "RC6_ROOT_INSTALL_MANIFEST_V1",
        "manifest_sha256": expected_sha256,
        "candidate_sha": manifest["candidate_sha"],
        "candidate_tree": manifest["candidate_tree"],
        "verification_result": "PASS",
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
    }
    raw = (json.dumps(artifact, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n").encode("ascii")
    _require(0 < len(raw) <= 4096, "WITNESS_ARTIFACT_SIZE")
    return raw


def validate_dispatch_env(env):
    """Reject untrusted ref/actor/inputs before network or artifact creation."""
    _require(type(env) is dict or hasattr(env, "get"), "TRUSTED_ENV_REQUIRED")
    _require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             env.get("GITHUB_REPOSITORY") == REPOSITORY and
             env.get("GITHUB_ACTOR") == "mbalbo2023" and
             env.get("GITHUB_TRIGGERING_ACTOR") == "mbalbo2023" and
             env.get("GITHUB_REF") == "refs/heads/main" and
             type(env.get("GITHUB_SHA")) is str and
             _HEX40.fullmatch(env["GITHUB_SHA"]),
             "TRUSTED_MAIN_OWNER_DISPATCH_REQUIRED")
    run_id, attempt = env.get("GITHUB_RUN_ID"), env.get("GITHUB_RUN_ATTEMPT")
    _require(type(run_id) is str and _POSITIVE.fullmatch(run_id) and
             type(attempt) is str and _POSITIVE.fullmatch(attempt),
             "TRUSTED_ACTIONS_RUN_ID_REQUIRED")
    workspace = env.get("GITHUB_WORKSPACE")
    _require(type(workspace) is str and Path(workspace).is_absolute() and
             ".." not in Path(workspace).parts and len(workspace) < 4096,
             "TRUSTED_CHECKOUT_PATH_REQUIRED")
    token = env.get("GITHUB_TOKEN")
    _require(type(token) is str and 10 <= len(token) < 32768 and
             token.isascii() and all("!" <= c <= "~" for c in token),
             "GITHUB_READ_TOKEN_REQUIRED")
    encoded, expected = (env.get("RC6_ROOT_INSTALL_MANIFEST_B64"),
                         env.get("RC6_ROOT_INSTALL_MANIFEST_SHA256"))
    _require(type(encoded) is str and 0 < len(encoded) <= 12288 and
             type(expected) is str and _HEX64.fullmatch(expected),
             "ROOT_MANIFEST_DISPATCH_INPUT_INVALID")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError):
        raise WitnessHold("ROOT_MANIFEST_BASE64_INVALID") from None
    _require(0 < len(raw) <= 8192, "ROOT_MANIFEST_SIZE")
    return {
        "main_sha": env["GITHUB_SHA"], "run_id": run_id,
        "run_attempt": attempt, "workspace": Path(workspace),
        "token": token, "manifest_bytes": raw, "expected_sha256": expected,
    }


def fetch_candidate_tree(candidate_sha, github_token):
    """Read only the fixed GitHub origin; never follow a credential redirect."""
    _require(type(candidate_sha) is str and _HEX40.fullmatch(candidate_sha) and
             type(github_token) is str and 10 <= len(github_token) < 32768 and
             github_token.isascii() and
             all("!" <= c <= "~" for c in github_token),
             "GITHUB_COMMIT_READ_INPUT_INVALID")
    path = f"/repos/{REPOSITORY}/git/commits/" + candidate_sha
    connection = None
    try:
        # http.client does not implement redirect handling. A Location value
        # in any 3xx response can never trigger a second request with token.
        connection = http.client.HTTPSConnection(
            "api.github.com", timeout=10, context=ssl.create_default_context())
        connection.request("GET", path, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + github_token,
            "User-Agent": "rc6-r305-root-witness",
        })
        response = connection.getresponse()
        _require(response.status == 200, "GITHUB_COMMIT_HTTP_STATUS_UNVERIFIED")
        raw = response.read(65537)
        _require(0 < len(raw) <= 65536, "GITHUB_COMMIT_RESPONSE_SIZE")
        doc = _unique_json(raw)
    except WitnessHold:
        raise
    except Exception:
        raise WitnessHold("GITHUB_COMMIT_API_UNVERIFIED") from None
    finally:
        if connection is not None:
            connection.close()
    _require(type(doc) is dict and doc.get("sha") == candidate_sha and
             type(doc.get("tree")) is dict and
             type(doc["tree"].get("sha")) is str and
             _HEX40.fullmatch(doc["tree"]["sha"]),
             "GITHUB_COMMIT_TREE_INVALID")
    return doc["tree"]["sha"]


def checkout_tree(workspace, main_sha):
    """Check local checkout SHA/tree; R305 later rechecks GitHub ref and tree."""
    _require(isinstance(workspace, Path) and type(main_sha) is str and
             _HEX40.fullmatch(main_sha), "CHECKOUT_CONTEXT_INVALID")
    values = []
    for revision in ("HEAD", "HEAD^{tree}"):
        try:
            process = subprocess.run(
                ["/usr/bin/git", "rev-parse", "--verify", revision],
                cwd=workspace, capture_output=True, text=True,
                check=False, timeout=5,
            )
        except Exception:
            raise WitnessHold("TRUSTED_CHECKOUT_READ_FAILED") from None
        lines = process.stdout.splitlines()
        _require(process.returncode == 0 and len(lines) == 1 and
                 _HEX40.fullmatch(lines[0]),
                 "TRUSTED_CHECKOUT_REVISION_INVALID")
        values.append(lines[0])
    _require(values[0] == main_sha, "TRUSTED_CHECKOUT_IDENTITY_MISMATCH")
    return values[1]


def publish_witness(path, raw):
    """Create exactly one fixed-name non-secret artifact, never replace it."""
    _require(isinstance(path, Path) and path.name == ARTIFACT_FILE and
             type(raw) is bytes and 0 < len(raw) <= 4096,
             "WITNESS_OUTPUT_SCOPE_INVALID")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        raise WitnessHold("WITNESS_OUTPUT_ALREADY_EXISTS_OR_UNSAFE") from None
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            _require(count > 0, "WITNESS_OUTPUT_SHORT_WRITE")
            offset += count
        os.fsync(fd)
    except Exception:
        raise WitnessHold("WITNESS_OUTPUT_FAILED") from None
    finally:
        os.close(fd)


def main(env=None):
    """Actions-only. Outputs safe status marker, never manifest, token or GO."""
    try:
        ctx = validate_dispatch_env(os.environ if env is None else env)
        # Only the exact checked-out trusted main source may write a witness.
        _require(Path(__file__).resolve().parents[1] == ctx["workspace"].resolve(),
                 "WITNESS_SCRIPT_NOT_FROM_TRUSTED_CHECKOUT")
        tree = checkout_tree(ctx["workspace"], ctx["main_sha"])
        receipt = build_witness(
            ctx["manifest_bytes"], expected_sha256=ctx["expected_sha256"],
            main_sha=ctx["main_sha"], main_tree=tree,
            run_id=ctx["run_id"], run_attempt=ctx["run_attempt"],
            candidate_tree_getter=lambda sha: fetch_candidate_tree(sha, ctx["token"]),
        )
        publish_witness(ctx["workspace"] / ARTIFACT_FILE, receipt)
    except WitnessHold as exc:
        print("RC6_R305_WITNESS=HOLD79|" + exc.reason)
        return 79
    except Exception:
        # The runner may include its protected token in network exceptions.
        print("RC6_R305_WITNESS=HOLD79|OFFHOST_UNEXPECTED")
        return 79
    print("RC6_R305_WITNESS=PASS_NOT_HOST_GO|" + ctx["expected_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

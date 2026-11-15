"""Authenticate the two fixed files-only bootstraps and reconcile global physical leases."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
import re

from scripts import rc6_r163_trusted_postrun_byte_audit as r163
from scripts import rc6_r295_host_bootstrap_guard as r295
from scripts import rc6_r305_root_pin_transition_guard as r305
from scripts import rc6_r340_trusted_root_env_controller as r340


LAUNCHER_SHA256 = "76eac14930a913ff7ed5c0720ccb5ff5bfc050b7bebe5b4725a76685ee67aaff"
LAUNCHER_TARGET = "/usr/local/sbin/porota-rc6-host-installer-genesis"
CUSTODIAN_TARGET = "/usr/local/libexec/porota-rc6-custody/rc6-genesis-custodian"
CUSTODIAN_SHA256 = "2d37cd16d0b682425ceb64349d1765d4ca7cd080729faf0555d286d1d6962be9"
GO_MARKER = "RC6_R362_LAUNCHER_GO_V1="
LEASE_MARKER = "RC6_R362_DEPLOY_OWNER_V1="
CUSTODIAN_GO_MARKER = "RC6_R367_CUSTODIAN_GO_V1="
CUSTODIAN_LEASE_MARKER = "RC6_R367_CUSTODIAN_DEPLOY_OWNER_V1="
CONTROL_LEASE_MARKER = "RC6_R367_GENESIS_DEPLOY_OWNER_V1="
LEASE_MARKERS = (LEASE_MARKER, CUSTODIAN_LEASE_MARKER, CONTROL_LEASE_MARKER)
OWNER_ID = 151845499
INVENTORY_COMMENT_ID = 6881987698
CUSTODIAN_DECISION_ID = 6883049719
HISTORY_CUTOFF = 6872471009
# The two historic narrative physical acquisitions and their respective releases in #980.
# Only these four prior authority decisions are pinned; ordinary code/coordination notes can evolve.
LEGACY_EVENTS = {
    6839994925: "5d21a2663da3c06ae734458dad94288f0401f432a6d1375032daa8c825cbd85f",
    6840088258: "4e5fc51d9249fb9ad89a4e89b2841267a42e98f312c94ae9d3c0321961665950",
    6840224623: "d7780dc0159b6bdf199eaed119dfe2ecf27349f8b104a054d284434441f1731a",
    6840302374: "8b53d1d35b902e540ba51113824db4d99def51407a54f497141dc4c3015d4538",
}
BASE = "/repos/" + r295.REPOSITORY
API = "https://api.github.com" + BASE
LEDGER = "/issues/980"
MAX_PAGES = 30
PAGE_SIZE = 100
GLOBAL = r"(?<![A-Z0-9])DEPLOY[ _-]+OWNER(?![A-Z0-9])"
# These phrases describe the absence of a physical lease. Do not skip the whole
# comment: a later line or clause can still claim or revoke global authority.
NEGATED = re.compile(
    r"\b(?:NO|SIN|NI|NING[ÚU]N|NOT)\s+(?:(?:HAY|SE TOM[ÓO]|SE ADQUIRI[ÓO]|SE ADQUIERE|ADQUIRIR|UN|NEW|IMPLICIT|A)\s+)*"
    + GLOBAL + "|" + GLOBAL + r"\s*[:=]?\s*(?:NEVER_ACQUIRED|NO\s+ADQUIRIDO|NOT\s+ACQUIRED)\b",
    re.IGNORECASE,
)
ABSENCE = re.compile(
    r"(?:(?:SIN|NO(?: HAY)?|NING[ÚU]N|NO SE TOM[ÓO]|NO SE ADQUIRI[ÓO]|NO SE ADQUIERE) "
    r"(?:EL |UN )?" + GLOBAL + r"(?: NI AUTORIDAD F[ÍI]SICA)?"
    r"|NO ADQUIRIR " + GLOBAL + r" HASTA GO T[ÉE]CNICO FRESCO"
    r"|" + GLOBAL + r"\s*[:=]\s*(?:NEVER_ACQUIRED|NO ADQUIRIDO|NOT ACQUIRED))",
    re.IGNORECASE,
)
require = r295._require


def _base64(text):
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise r295.BootstrapHold("LAUNCHER_HOST_KEY_FORMAT") from None


def _matches_host(patterns, host):
    if patterns.startswith("|"):
        parts = patterns.split("|")
        require(len(parts) == 4 and parts[:2] == ["", "1"], "LAUNCHER_HOST_KEY_FORMAT")
        salt, expected = map(_base64, parts[2:])
        require(len(salt) == len(expected) == 20, "LAUNCHER_HOST_KEY_FORMAT")
        return hmac.compare_digest(hmac.digest(salt, host.encode("ascii"), "sha1"), expected)
    aliases = patterns.lower().split(",")
    require(all(re.fullmatch(r"[a-z0-9][a-z0-9.:-]*|\[[a-z0-9.:-]+\]:22", alias)
                for alias in aliases), "LAUNCHER_HOST_KEY_SELECTOR")
    # StrictSSHHost connects on the default port; OpenSSH looks up the bare hostname.
    return host in aliases


def _key_digest(algorithm, encoded):
    # Parse the actual OpenSSH length-prefixed wire fields, not an arbitrary
    # base64 string following a plausible key name. No subprocess or private key.
    blob = _base64(encoded)
    fields = []
    start = 0
    while start < len(blob):
        end = start + 4
        size = int.from_bytes(blob[start:end], "big")
        require(len(fields) < 3 and 0 < size <= 2048 and end + size <= len(blob),
                "LAUNCHER_HOST_KEY_FORMAT")
        fields.append(blob[end:end + size])
        start = end + size
    require(fields and fields[0] == algorithm.encode("ascii"), "LAUNCHER_HOST_KEY_FORMAT")
    curves = {"nistp256": 65, "nistp384": 97, "nistp521": 133}
    if algorithm == "ssh-ed25519":
        valid = len(fields) == 2 and len(fields[1]) == 32 and any(fields[1])
    elif algorithm == "ssh-rsa" and len(fields) == 3:
        exponent = int.from_bytes(fields[1], "big", signed=True)
        modulus = int.from_bytes(fields[2], "big", signed=True)
        valid = 3 <= exponent <= 2**31 - 1 and exponent % 2 and modulus.bit_length() >= 2048 and modulus % 2
    elif algorithm.startswith("ecdsa-sha2-") and len(fields) == 3:
        curve = algorithm.removeprefix("ecdsa-sha2-")
        valid = (fields[1] == curve.encode("ascii") and len(fields[2]) == curves.get(curve)
                 and fields[2][0] == 4 and any(fields[2][1:]))
    else:
        valid = False
    require(valid, "LAUNCHER_HOST_KEY_FORMAT")
    return hashlib.sha256(blob).hexdigest()


def host_identity_sha256(env):
    """Public commitment to one protected endpoint, DO pins and its strong SSH host keys."""
    host, droplet, region, known = (env.get(k) for k in (
        "DO_HOST", "RC6_EXPECTED_DO_ID", "RC6_EXPECTED_DO_REGION", "DO_KNOWN_HOSTS"))
    require(type(host) is str and r295._HOST.fullmatch(host) and type(droplet) is str
            and re.fullmatch(r"[1-9][0-9]{5,19}", droplet) and type(region) is str
            and r295._DO_REGION.fullmatch(region) and type(known) is str
            and 0 < len(known) < 32768 and known.isascii()
            and all(c in "\n\t" or " " <= c <= "~" for c in known), "LAUNCHER_HOST_PINS_INVALID")
    host = host.lower()
    keys = set()
    for line in known.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=3)
        require(len(fields) >= 3 and not line.startswith("@"), "LAUNCHER_HOST_KEY_FORMAT")
        if _matches_host(fields[0], host):
            keys.add(_key_digest(fields[1], fields[2]))
    require(keys, "LAUNCHER_HOST_PUBLIC_KEY_UNVERIFIED")
    identity = ("RC6_R362_BOOTSTRAP_HOST_IDENTITY_V1", host, droplet, region, *sorted(keys))
    return hashlib.sha256("\0".join(identity).encode("ascii")).hexdigest()


class _GitHub(r163.GitHubReadOnlyREST):
    """Authenticated GET only, fixed origin, no redirects; allow bounded JSON arrays for pages."""
    def read(self, path, limit=65536):
        try:
            with self._opener.open(self._api_request(BASE + path), timeout=8) as response:
                require(response.headers.get("Content-Type", "").split(";")[0] == "application/json",
                        "LAUNCHER_REST_CONTENT_TYPE")
                raw = self._read(response, limit=limit)
            return r295._strict_json(raw, limit=limit)
        except (OSError, http.client.HTTPException, r163.PostrunEvidenceRed) as exc:
            raise r295.BootstrapHold("LAUNCHER_REST_UNVERIFIED") from exc


def _owner_comment(comment, number, issue):
    require(type(comment) is dict and type(comment.get("id")) is int and comment["id"] == number
            and comment.get("issue_url") == f"{API}/issues/{issue}"
            and comment.get("author_association") == "OWNER" and type(comment.get("user")) is dict
            and comment["user"].get("login") == r295.OWNER
            and type(comment["user"].get("id")) is int and comment["user"]["id"] == OWNER_ID
            and type(comment.get("body")) is str and comment.get("created_at") == comment.get("updated_at"),
            "LAUNCHER_IMMUTABLE_OWNER_COMMENT_REQUIRED")
    return r295._utc_timestamp(comment.get("created_at"))


def _document(body, marker):
    require(body.startswith(marker) and len(body.splitlines()) == 1 and "\n" not in body
            and "\r" not in body, "LAUNCHER_ONE_LINE_DOCUMENT_REQUIRED")
    doc = r295._strict_json(body[len(marker):].encode("utf-8"))
    require(type(doc) is dict, "LAUNCHER_DOCUMENT_INVALID")
    return doc


def _scope(main_sha, host_hash, marker=LEASE_MARKER):
    common = {"trusted_main_sha": main_sha, "host_identity_source": "PROTECTED_DO_PINS",
              "host_identity_sha256": host_hash}
    if marker == LEASE_MARKER:
        return {**common, "operation": "install_genesis_launcher",
                "launcher_target": LAUNCHER_TARGET, "launcher_sha256": LAUNCHER_SHA256}
    require(marker == CUSTODIAN_LEASE_MARKER and _hex(CUSTODIAN_SHA256, 64),
            "CUSTODIAN_PACKAGE_PIN_UNVERIFIED")
    return {**common, "operation": "install_genesis_custodian",
            "custodian_target": CUSTODIAN_TARGET, "custodian_sha256": CUSTODIAN_SHA256}


def _check_go(comment, go_id, scope, marker, decision):
    issued = _owner_comment(comment, go_id, 1093)
    doc = _document(comment["body"], marker)
    lease = doc.get("lease_comment_id")
    expires = r295._utc_timestamp(doc.get("expires_at"))
    require(type(lease) is int and HISTORY_CUTOFF < lease < go_id
            and doc == {**scope, "inventory_comment_id": decision,
                        "lease_comment_id": lease, "expires_at": doc["expires_at"]}
            and type(doc["inventory_comment_id"]) is int, "LAUNCHER_GO_SCOPE_UNVERIFIED")
    require(issued <= datetime.now(timezone.utc) < expires <= issued + timedelta(hours=2),
            "LAUNCHER_GO_EXPIRED_OR_FUTURE")
    return lease, issued


def _ledger_version(github):
    doc = github.read(LEDGER)
    require(type(doc) is dict and doc.get("id") == 6216186179 and doc.get("number") == 980
            and doc.get("url") == API + LEDGER and type(doc.get("comments")) is int
            and 0 < doc["comments"] <= MAX_PAGES * PAGE_SIZE, "LAUNCHER_LEDGER_COUNT_UNVERIFIED")
    r295._utc_timestamp(doc.get("updated_at"))
    return doc["comments"], doc["updated_at"]


def _noncanonical_decision(body, *, reconciled=False):
    for line in body.splitlines():
        if not re.search(GLOBAL, line, re.IGNORECASE):
            continue
        plain = line.replace("*", "").replace("`", "").strip()
        if not reconciled:
            # Only an entire, unambiguous absence sentence is passive. In particular
            # never strip a negated substring from a further claim or release.
            for sentence in re.split(r"[.!?]+(?:\s+|$)", plain):
                if re.search(GLOBAL, sentence, re.IGNORECASE) and not ABSENCE.fullmatch(
                        sentence.strip().lstrip("-># ").strip()):
                    return True
            continue
        plain = NEGATED.sub("", plain)
        if not re.search(GLOBAL, plain, re.IGNORECASE):
            continue
        if (plain.startswith("#") or
            re.search(GLOBAL + r"[ :=_-]+(?:GLOBAL[ :=_-]+)?(?:ACTIV[EO]|ADQUIRID[OA]|ACQUIRED|"
                      r"LIBERAD[OA]|RELEASED|REVOKED|SUPERSEDED)\b", plain, re.IGNORECASE) or
            re.search(r"^\s*(?:[-|>]\s*)?(?:(?:role|rol|ownership|authority)\s*[:=]\s*)?" + GLOBAL,
                      plain, re.IGNORECASE) or
            re.search(r"\b(?:acqui\w*|revoc\w*|revo[kq]\w*|liber\w*|release\w*|reservo|"
                      r"tomo|asumo|adqui\w*|supersed\w*)\s+(?:(?:el|un|mi|the|global|al|"
                      r"este|nuevo|exclusive)\s+){0,3}" + GLOBAL, plain, re.IGNORECASE)):
            return True
    return False


def _hex(value, width):
    return type(value) is str and len(value) == width and re.fullmatch(r"[0-9a-f]+", value) is not None


def _valid_lease_scope(marker, scope):
    sha, host = scope.get("trusted_main_sha"), scope.get("host_identity_sha256")
    if not _hex(sha, 40) or not _hex(host, 64) or scope.get("host_identity_source") != "PROTECTED_DO_PINS":
        return False
    if marker == CONTROL_LEASE_MARKER:
        # Recognizing a lease scope does not grant a GO or enable a dispatch operation.
        return (set(scope) == {"operation", "trusted_main_sha", "candidate_sha", "candidate_tree",
                                "host_identity_source", "host_identity_sha256", "nonce"}
                and scope["operation"] in ("PREPARE_ENV", "APPLY_GENESIS", "PROMOTION_ADMISSION")
                and _hex(scope["candidate_sha"], 40) and _hex(scope["candidate_tree"], 40)
                and _hex(scope["nonce"], 32))
    return marker in (LEASE_MARKER, CUSTODIAN_LEASE_MARKER) and scope == _scope(sha, host, marker)


def verify_global_lease(github, selected, lease_id, *, marker, scope):
    """Read all owner events; only the selected immutable ACTIVE acquisition may remain."""
    require(type(scope) is dict and _valid_lease_scope(marker, scope), "LAUNCHER_GLOBAL_LEASE_MISMATCH")
    version = _ledger_version(github)
    count = version[0]
    previous = 0
    seen = set()
    active = None
    found = False
    for start in range(0, count, PAGE_SIZE):
        page = start // PAGE_SIZE + 1
        rows = github.read(f"{LEDGER}/comments?per_page={PAGE_SIZE}&page={page}", r163.MAX_JSON_BYTES)
        require(type(rows) is list and len(rows) == min(PAGE_SIZE, count - start),
                "LAUNCHER_LEDGER_INCOMPLETE")
        for row in rows:
            require(type(row) is dict and type(row.get("id")) is int and row["id"] > previous
                    and row.get("issue_url") == API + LEDGER and type(row.get("body")) is str,
                    "LAUNCHER_LEDGER_ORDER_OR_SOURCE")
            previous = ident = row["id"]
            body = row["body"]
            user = row.get("user")
            require(type(user) is dict and type(user.get("id")) is int
                    and type(user.get("login")) is str, "LAUNCHER_LEDGER_ACTOR_UNVERIFIED")
            if user["login"] != r295.OWNER and user["id"] != OWNER_ID:
                continue
            require(user["login"] == r295.OWNER and user["id"] == OWNER_ID,
                    "LAUNCHER_LEDGER_ACTOR_UNVERIFIED")
            if ident in LEGACY_EVENTS:
                _owner_comment(row, ident, 980)
                require(hashlib.sha256(body.encode("utf-8")).hexdigest() == LEGACY_EVENTS[ident],
                        "LAUNCHER_LEGACY_OWNER_DRIFT")
                seen.add(ident)
            elif ident == HISTORY_CUTOFF:
                require(not _noncanonical_decision(body, reconciled=True),
                        "LAUNCHER_AMBIGUOUS_GLOBAL_OWNER")
                seen.add(ident)
            elif body.startswith(LEASE_MARKERS):
                _owner_comment(row, ident, 980)
                event_marker = next(candidate for candidate in LEASE_MARKERS if body.startswith(candidate))
                doc = _document(body, event_marker)
                require(ident > HISTORY_CUTOFF, "LAUNCHER_LEDGER_LEGACY_UNKNOWN")
                if doc.get("status") == "ACTIVE":
                    # Honor a competing global lease across other main SHAs. The selected
                    # acquisition must match this workflow's exact scope and commit.
                    event_scope = {k: v for k, v in doc.items() if k != "status"}
                    require(_valid_lease_scope(event_marker, event_scope) and active is None,
                            "LAUNCHER_GLOBAL_LEASE_CONFLICT")
                    active = (event_marker, ident)
                else:
                    original = doc.get("lease_comment_id")
                    require(type(original) is int and active == (event_marker, original)
                            and doc == {"status": "RELEASED", "lease_comment_id": original},
                            "LAUNCHER_GLOBAL_RELEASE_AMBIGUOUS")
                    active = None
                if ident == lease_id:
                    require(event_marker == marker and doc == {**scope, "status": "ACTIVE"}
                            and r305.owner_go_identity(row) == r305.owner_go_identity(selected),
                            "LAUNCHER_GLOBAL_LEASE_MISMATCH")
                    found = True
            else:
                require(not _noncanonical_decision(body, reconciled=ident < HISTORY_CUTOFF)
                        and not any(prefix.split("_V1=")[0] in body for prefix in LEASE_MARKERS),
                        "LAUNCHER_AMBIGUOUS_GLOBAL_OWNER")
    require(seen == set(LEGACY_EVENTS) | {HISTORY_CUTOFF} and found and active == (marker, lease_id),
            "LAUNCHER_ACTIVE_GLOBAL_OWNER_UNVERIFIED")
    return version


def verify_authority(env):
    """Verify the real checkout, live main, independent GO and entire #980. No host calls."""
    context = r295.validate_context(env, require_host=False)
    require(env.get("GITHUB_REPOSITORY") == r295.REPOSITORY
            and env.get("GITHUB_TRIGGERING_ACTOR") == r295.OWNER
            and env.get("GITHUB_ACTOR_ID") == str(OWNER_ID)
            and env.get("BOOTSTRAP_ACTION") in ("install_genesis_launcher", "install_genesis_custodian")
            and env.get("DO_USER") == "porotaadmin",
            "LAUNCHER_TRUSTED_DISPATCH_REQUIRED")
    host_hash = host_identity_sha256(env)
    if env["BOOTSTRAP_ACTION"] == "install_genesis_custodian":
        marker, go_marker, decision = CUSTODIAN_LEASE_MARKER, CUSTODIAN_GO_MARKER, CUSTODIAN_DECISION_ID
    else:
        marker, go_marker, decision = LEASE_MARKER, GO_MARKER, INVENTORY_COMMENT_ID
    scope = _scope(context.main_sha, host_hash, marker)
    try:
        require(r340._checkout_head() == context.main_sha, "LAUNCHER_CHECKOUT_CHANGED")
        github = _GitHub(env["GITHUB_TOKEN"])
        def current_main():
            doc = github.read("/git/ref/heads/main")
            require(type(doc) is dict and doc.get("ref") == "refs/heads/main"
                    and type(doc.get("object")) is dict and doc["object"].get("type") == "commit"
                    and doc["object"].get("sha") == context.main_sha, "LAUNCHER_MAIN_CHANGED")
        current_main()
        go_path = f"/issues/comments/{context.owner_comment_id}"
        go = github.read(go_path)
        lease_id, issued = _check_go(go, context.owner_comment_id, scope, go_marker, decision)
        lease_path = f"/issues/comments/{lease_id}"
        lease = github.read(lease_path)
        require(_owner_comment(lease, lease_id, 980) <= issued, "LAUNCHER_LEASE_POSTDATES_GO")
        version = verify_global_lease(github, lease, lease_id, marker=marker, scope=scope)
        fresh_lease = github.read(lease_path)
        _owner_comment(fresh_lease, lease_id, 980)
        require(r305.owner_go_identity(fresh_lease) == r305.owner_go_identity(lease),
                "LAUNCHER_GLOBAL_LEASE_CHANGED")
        fresh_go = github.read(go_path)
        require(r305.owner_go_identity(fresh_go) == r305.owner_go_identity(go), "LAUNCHER_GO_CHANGED")
        current_main()
        require(_ledger_version(github) == version, "LAUNCHER_LEDGER_CHANGED")
        _check_go(fresh_go, context.owner_comment_id, _scope(context.main_sha, host_identity_sha256(env), marker),
                  go_marker, decision)
    except (r340.ControlHold, r305.PinHold, r163.PostrunEvidenceRed, UnicodeError, RecursionError) as exc:
        raise r295.BootstrapHold("LAUNCHER_AUTHORITY_UNVERIFIED") from exc

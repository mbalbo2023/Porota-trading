"""Host-side verification of a request from the trusted R321 GitHub Actions job."""
import base64
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import ssl
import subprocess
import time
import urllib.request


OWNER = "mbalbo2023"
OWNER_ID = "151845499"
REPO = OWNER + "/Porota-trading"
REPO_ID = "1338680554"
REF = "refs/heads/main"
WORKFLOW = REPO + "/.github/workflows/rc6-r321-root-install-approved.yml@" + REF
ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
API = "https://api.github.com/repos/" + REPO
HOST = "ede3589a375077b3d40d6941cd82e5e6b141c615e616cdd693a046199232d1b3"
CACHE_SECONDS = 4
MAX_TOKEN_AGE = 60
STEPS = {"PREPARE_ENV": ("INITIAL", "POST_D_E"), "APPLY_GENESIS": ("SEAL_ENV", "PIN", "INSTALL")}
SUBJECTS = {"repo:" + REPO + ":ref:" + REF,
            "repo:" + OWNER + "@" + OWNER_ID + "/Porota-trading@" + REPO_ID + ":ref:" + REF}


class IdentityHold(ValueError):
    """A sanitized host refusal, suitable for the files-only boundary."""


def _check(value):
    if not value:
        raise IdentityHold("HOST_AUTHORITY_UNVERIFIED")


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _check(key not in result)
            result[key] = value
        return result
    def invalid(_value):
        raise IdentityHold("HOST_AUTHORITY_UNVERIFIED")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def _canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("ascii")


def _dec(value, max_length):
    _check(type(value) is str and 0 < len(value) <= max_length and re.fullmatch("[A-Za-z0-9_-]+", value))
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    _check(base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == value)
    return raw


def _time(value):
    _check(type(value) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value))
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class _Direct(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _fetch(url):
    _check(url in {JWKS_URL, API + "/issues/980", API + "/issues/1093",
                    API + "/git/ref/heads/main", API + "/pulls/1087"})
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile="/etc/ssl/certs/ca-certificates.crt")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Direct(),
                                         urllib.request.HTTPSHandler(context=context))
    req = urllib.request.Request(url, headers={"User-Agent":"porota-rc6-genesis-readonly",
                                               "Accept":"application/json","Cache-Control":"no-cache"})
    with opener.open(req, timeout=3) as response:
        _check(response.status == 200 and response.geturl() == url
               and response.headers.get("Content-Type", "").split(";")[0] == "application/json")
        raw = response.read(262145)
        _check(0 < len(raw) <= 262144)
    result = _json(raw)
    _check(type(result) is dict)
    return result


def _fetch_keys():
    return _fetch(JWKS_URL)


def _der(tag, raw):
    length = len(raw)
    if length < 128:
        prefix = bytes((length,))
    else:
        size = length.to_bytes((length.bit_length()+7)//8, "big")
        prefix = bytes((128+len(size),)) + size
    return bytes((tag,)) + prefix + raw


def _rsa_key(jwks, kid, thumbprint):
    keys = jwks["keys"]
    _check(type(keys) is list and 0 < len(keys) <= 64)
    matches = [key for key in keys if type(key) is dict and key.get("kid") == kid]
    _check(len(matches) == 1)
    key = matches[0]
    _check(key.get("kty") == "RSA" and key.get("alg","RS256") == "RS256"
           and key.get("use","sig") == "sig" and key.get("key_ops",["verify"]) == ["verify"])
    if thumbprint is not None:
        expected = _dec(thumbprint, 32)
        _check(len(expected) == 20)
        if "x5t" in key:
            _check(thumbprint == key["x5t"])
        else:
            certificates = key["x5c"]
            _check(type(certificates) is list and certificates and type(certificates[0]) is str
                   and len(certificates[0]) <= 16384)
            der = base64.b64decode(certificates[0], validate=True)
            # Only an optional identifier in the already authenticated JWKS. RSA/SHA-256 verifies the JWT.
            _check(hashlib.sha1(der, usedforsecurity=False).digest() == expected)
    modulus, exponent = _dec(key["n"], 1366), _dec(key["e"], 8)
    _check(256 <= len(modulus) <= 1024 and modulus[0] >= 128 and modulus[-1] % 2
           and exponent == b"\x01\x00\x01")
    # RFC 5280 SubjectPublicKeyInfo from the authenticated RFC 7518 n/e.
    rsa = _der(48, _der(2, b"\0"+modulus) + _der(2, exponent))
    rsa_algorithm = bytes.fromhex("300d06092a864886f70d0101010500")
    return _der(48, rsa_algorithm + _der(3, b"\0"+rsa)), len(modulus)


def _verify_signature(unsigned, signature, key):
    with ExitStack() as stack:
        handles = []
        for raw in (key, signature):
            fd = os.memfd_create("rc6-oidc", os.MFD_CLOEXEC)
            stream = stack.enter_context(os.fdopen(fd, "w+b"))
            stream.write(raw)
            stream.flush()
            stream.seek(0)
            handles.append(fd)
        result = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-keyform", "DER", "-verify",
             "/proc/self/fd/"+str(handles[0]), "-signature", "/proc/self/fd/"+str(handles[1]),
             "-sigopt", "rsa_padding_mode:pkcs1"],
            input=unsigned, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd="/",
            pass_fds=tuple(handles), env={"OPENSSL_CONF":"/dev/null"}, timeout=3, check=False)
        _check(result.returncode == 0)


def _verify_token(token, audience, fields):
    _check(type(token) is str and len(token) <= 24576)
    header, payload, signature = token.split(".")
    h = _json(_dec(header, 2048))
    claims = _json(_dec(payload, 14336))
    _check(type(h) is dict and set(h) in ({"alg", "kid", "typ"}, {"alg", "kid", "typ", "x5t"}) and h["alg"] == "RS256" and h["typ"] == "JWT"
           and type(h["kid"]) is str and re.fullmatch("[A-Za-z0-9._-]{1,160}", h["kid"]))
    _check(type(claims) is dict and claims.get("sub") in SUBJECTS)
    expected = {"iss":ISSUER,"aud":audience,"repository":REPO,"repository_id":REPO_ID,
                "repository_owner":OWNER,"repository_owner_id":OWNER_ID,"actor":OWNER,"actor_id":OWNER_ID,
                "ref":REF,"ref_type":"branch","workflow_ref":WORKFLOW,"workflow_sha":fields["binding"]["trusted_main_sha"],
                "sha":fields["binding"]["trusted_main_sha"],"event_name":"workflow_dispatch",
                "run_id":fields["dispatch_run_id"],"run_attempt":fields["dispatch_run_attempt"]}
    _check(all(type(claims.get(k)) is str and claims[k] == v for k,v in expected.items()))
    _check(all(type(claims.get(k)) is int for k in ("iat","nbf","exp")) and
           0 < claims["iat"] < claims["exp"] and 0 < claims["nbf"] < claims["exp"])
    _check("x5t" not in h or type(h["x5t"]) is str)
    key, length = _rsa_key(_fetch_keys(),h["kid"],h.get("x5t"))
    sig = _dec(signature, 1366)
    _check(len(sig) == length)
    _verify_signature((header+"."+payload).encode("ascii"), sig, key)
    return claims["iat"], claims["nbf"], claims["exp"]


def _public_versions(versions):
    _check(type(versions) is dict and set(versions) == {"980","1093"})
    for value in versions.values():
        _check(type(value) is dict and set(value) == {"comments","updated_at"}
               and type(value["comments"]) is int and value["comments"] > 0)
        _time(value["updated_at"])
    return versions


def _current_authority(binding, versions):
    for num, expected in versions.items():
        row = _fetch(API+"/issues/"+num)
        _check(row.get("url") == API+"/issues/"+num and type(row.get("number")) is int
               and row["number"] == int(num) and type(row.get("comments")) is int
               and row["comments"] == expected["comments"] and row.get("updated_at") == expected["updated_at"])
    ref = _fetch(API+"/git/ref/heads/main")
    _check(ref.get("ref") == REF and ref.get("object",{}).get("type") == "commit"
           and ref["object"]["sha"] == binding["trusted_main_sha"])
    pr = _fetch(API+"/pulls/1087")
    _check(pr.get("number") == 1087 and pr.get("state") == "open" and pr.get("draft") is False
           and pr.get("merged") is False and pr["head"]["sha"] == binding["candidate_sha"]
           and str(pr["head"]["repo"]["id"]) == REPO_ID and str(pr["base"]["repo"]["id"]) == REPO_ID
           and pr["head"]["repo"]["full_name"] == REPO and pr["base"]["repo"]["full_name"] == REPO
           and pr["head"]["ref"] == "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
           and pr["base"]["ref"] == "deploy/rc6-pr69-isolated-20260915")
    return True


def _authority(request):
    _check(type(request) is dict and request.get("schema") == "RC6_R367_HOST_REQUEST_V1"
           and request.get("step") in STEPS[request["operation"]])
    fields = dict(request["authority"])
    token = fields.pop("host_identity_token")
    raw = _canonical({**request, "authority":fields})
    _check(len(raw) <= 1024**2)
    frozen = _json(raw)["authority"]
    binding = frozen["binding"]
    _check(binding.get("host_identity_sha256") == HOST and frozen.get("actor_id") == OWNER_ID
           and all(type(binding.get(k)) is str and re.fullmatch("[0-9a-f]{40}",binding[k])
                   for k in ("trusted_main_sha","candidate_sha","candidate_tree"))
           and all(type(frozen.get(k)) is str and re.fullmatch("[1-9][0-9]{0,19}",frozen[k])
                   for k in ("dispatch_run_id","dispatch_run_attempt")))
    versions = _public_versions(frozen["public_versions"])
    audience = "urn:porota:r367:"+hashlib.sha256(raw).hexdigest()
    iat, nbf, exp = _verify_token(token, audience, frozen)
    earliest = max(_time(frozen["issued_at"]),iat,nbf)
    deadline = min(_time(frozen["expires_at"]),exp,iat+MAX_TOKEN_AGE)
    signed_request_digest = hashlib.sha256(_canonical(request)).digest()
    last_started = None
    poisoned = False

    def recheck(*, force=False):
        nonlocal last_started, poisoned
        try:
            _check(type(force) is bool and not poisoned and earliest <= time.time() < deadline
                   and hashlib.sha256(_canonical(request)).digest() == signed_request_digest)
            started = time.monotonic()
            if force or last_started is None or started-last_started >= CACHE_SECONDS:
                _check(_current_authority(binding,versions) is True
                       and time.monotonic()-started < CACHE_SECONDS)
                last_started = started
            _check(earliest <= time.time() < deadline)
            return True
        except Exception:
            poisoned = True
            raise IdentityHold("HOST_AUTHORITY_UNVERIFIED") from None

    recheck()
    return recheck


def signed_authority(request):
    """Authenticate immediately. Every recheck enforces both clocks. Force resamples GitHub."""
    try:
        return _authority(request)
    except Exception:
        raise IdentityHold("HOST_AUTHORITY_UNVERIFIED") from None

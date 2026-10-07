"""Bounded GitHub artifact I/O; authentication never follows a redirect."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

try:
    from scripts.porota_artifact_provenance import decode_json
except ModuleNotFoundError:
    from porota_artifact_provenance import decode_json

REPOSITORY = "mbalbo2023/Porota-trading"
MAX_METADATA_BYTES = 4 * 1024**2
MAX_ARTIFACT_BYTES = 8 * 1024**3


class ArtifactHTTPRejected(ValueError):
    pass


class RemoveAuthorizationRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or not target.hostname or target.port not in {None, 443}:
            raise ArtifactHTTPRejected("ARTIFACT_REDIRECT_TRANSPORT_REJECTED")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            for collection in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(collection):
                    if key.lower() == "authorization": del collection[key]
        return redirected


def remaining(deadline, clock=time.monotonic):
    value = deadline - clock()
    if value <= 0:
        raise ArtifactHTTPRejected("ARTIFACT_TOTAL_DEADLINE_EXCEEDED")
    return min(30, value)


def github_request(path):
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or not os.environ.get("GH_TOKEN"):
        raise ArtifactHTTPRejected("ARTIFACT_CONTROL_PLANE_IDENTITY_MISSING")
    if not path.startswith("/") or any(ord(char) < 32 for char in path):
        raise ArtifactHTTPRejected("ARTIFACT_API_PATH_INVALID")
    return urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})


def api_get(path, *, deadline=None, opener=None, clock=time.monotonic):
    deadline = clock() + 60 if deadline is None else deadline
    opener = opener or urllib.request.build_opener(RemoveAuthorizationRedirect())
    with opener.open(github_request(path), timeout=remaining(deadline, clock)) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    remaining(deadline, clock)
    if len(raw) > MAX_METADATA_BYTES:
        raise ArtifactHTTPRejected("ARTIFACT_API_METADATA_SIZE_LIMIT")
    return decode_json(raw)


def download_artifact(artifact_id, output, *, expected_size, expected_digest, deadline,
                      opener=None, clock=time.monotonic):
    if (type(artifact_id) is not int or artifact_id <= 0 or type(expected_size) is not int
            or not 0 < expected_size <= MAX_ARTIFACT_BYTES
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) is None):
        raise ArtifactHTTPRejected("ARTIFACT_DOWNLOAD_BOUND_INVALID")
    opener = opener or urllib.request.build_opener(RemoveAuthorizationRedirect())
    output = Path(output); created = False; total = 0; digest = hashlib.sha256()
    try:
        with opener.open(github_request(f"/actions/artifacts/{artifact_id}/zip"),
                         timeout=remaining(deadline, clock)) as response:
            with output.open("xb") as destination:
                created = True
                while True:
                    remaining(deadline, clock)
                    chunk = response.read(1024**2)
                    if not chunk: break
                    total += len(chunk)
                    if total > expected_size:
                        raise ArtifactHTTPRejected("ARTIFACT_DOWNLOAD_SIZE_EXCEEDED")
                    digest.update(chunk); destination.write(chunk)
                destination.flush(); os.fsync(destination.fileno())
        remaining(deadline, clock)
        if total != expected_size or "sha256:" + digest.hexdigest() != expected_digest:
            raise ArtifactHTTPRejected("ARTIFACT_DOWNLOAD_BYTES_MISMATCH")
        return {"status": "GREEN", "bytes": total, "digest": expected_digest,
                "authorization_forwarded_to_redirect": False, "total_deadline_enforced": True}
    except BaseException:
        if created: output.unlink(missing_ok=True)
        raise

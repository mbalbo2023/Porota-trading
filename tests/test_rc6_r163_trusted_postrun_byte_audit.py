"""Adversarial offline fixtures for a proposed trusted-main postrun byte audit.

These are synthetic REST replies and TAR/ZIP bytes; never a GitHub/runtime GO.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import tarfile
import unittest
from unittest import mock
import warnings
import zipfile
import urllib.error

from scripts.rc6_r163_trusted_postrun_byte_audit import (
    GitHubReadOnlyREST,
    PostrunEvidenceRed,
    audit_from_github,
    allowed_storage_redirect,
    load_unique_json,
    verify_postrun_bytes,
)


REPO = "mbalbo2023/Porota-trading"
BASE = "deploy/rc6-pr69-isolated-20260915"
HEAD_REF = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
SHA = "a" * 40
TREE = "b" * 40
RUN = 991
ATTEMPT = 2
ARTIFACT_ID = 883
SOURCE_CID = "1" * 64
COLLECTOR_CID = "2" * 64
VOLUME = "3" * 64
IMAGE_ID = "sha256:" + "4" * 64
NAME = f"rc6-r163-offhost-{RUN}-{ATTEMPT}-{SHA}"
WORKFLOW = ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
MANIFEST = "porota-artifact-byte-manifest-v1.json"
NATIVE_PATHS = (
    "native-bound.xml",
    "session/dashboard_auth_52.xml",
    "session/publication_io_identity.xml",
    "session/scalping_capacity.xml",
    "session/scheduler_monitor_backup.xml",
    "session/signals_risk_exit_ledger.xml",
    "session/sources_readiness_network.xml",
    "session/sql_shadow_wal.xml",
    "session/treasury_caucion.xml",
    "session/stress.json",
    "catalog/full-catalog.json",
    "cadence/cadence-fresh.json",
    "cadence/cadence-legacy.json",
)


def _json(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _tar(paths=NATIVE_PATHS) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for path in paths:
            data = ("R163 synthetic evidence for " + path).encode()
            info = tarfile.TarInfo("./" + path)
            info.uid = info.gid = 1000
            info.mode = 0o600
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _native(tar_bytes):
    files = [{"path": path, "size_bytes": len(("R163 synthetic evidence for " + path).encode()),
              "sha256": hashlib.sha256(("R163 synthetic evidence for " + path).encode()).hexdigest()}
             for path in sorted(NATIVE_PATHS)]
    native = {
        "schema": "RC6_R163_NATIVE_TAR_V1", "schema_version": 1,
        "status": "OFFHOST_BYTES_ONLY", "candidate_sha": SHA,
        "candidate_tree_sha": TREE, "image_id": IMAGE_ID,
        "run_id": str(RUN), "run_attempt": str(ATTEMPT), "role": "bundle",
        "source_cid": SOURCE_CID, "collector_cid": COLLECTOR_CID,
        "volume_name": VOLUME, "tar_sha256": hashlib.sha256(tar_bytes).hexdigest(),
        "tar_size_bytes": len(tar_bytes), "file_count": len(files), "files": files,
        "host_or_runtime_verified": False,
    }
    return native


def _inspect(cid, *, collector):
    labels = {
        "porota.commit": SHA, "porota.candidate_tree": TREE,
        "porota.predeploy": "v2", "porota.native-proof": "v3",
        "porota.native-role": "bundle-collector" if collector else "bundle",
        "porota.run_id": str(RUN), "porota.run_attempt": str(ATTEMPT),
    }
    host = {
        "Privileged": False, "ReadonlyRootfs": True,
        "NetworkMode": "none", "CapDrop": ["ALL"], "CapAdd": None,
        "Devices": None, "Tmpfs": None, "PublishAllPorts": False,
        "PortBindings": None, "Links": None, "ExtraHosts": None,
        "SecurityOpt": ["no-new-privileges"], "Binds": None,
        "IpcMode": "private", "UTSMode": "", "PidMode": "",
        "UsernsMode": "", "CgroupnsMode": "private", "GroupAdd": None,
        "DeviceRequests": None, "DeviceCgroupRules": None, "Runtime": "runc",
        "Sysctls": None, "CgroupParent": "",
        "Mounts": [] if collector else [{"Type": "volume", "Source": "",
                                            "Target": "/app/data", "ReadOnly": False}],
        "VolumesFrom": [SOURCE_CID + ":ro"] if collector else None,
    }
    return {"Id": cid, "Image": IMAGE_ID,
            "Config": {"User": "1000:1000", "Labels": labels},
            "HostConfig": host,
            "Mounts": [{"Type": "volume", "Name": VOLUME,
                        "Source": "/var/lib/docker/volumes/anonymous/_data",
                        "Driver": "local", "Destination": "/app/data",
                        "Mode": "ro" if collector else "", "RW": not collector}]}


def _evidence():
    tar_bytes = _tar()
    custody = {
        "schema_version": 1, "status": "OFFHOST_CONTRACT_ONLY",
        "candidate_sha": SHA, "candidate_tree_sha": TREE, "image_id": IMAGE_ID,
        "run_id": str(RUN), "run_attempt": str(ATTEMPT), "role": "bundle",
        "source_cid": SOURCE_CID, "collector_cid": COLLECTOR_CID,
        "volume_name": VOLUME, "mount_target": "/app/data",
        "uid": 1000, "gid": 1000, "host_or_runtime_verified": False,
    }
    native = _native(tar_bytes)
    entries = {
        "native.tar": tar_bytes, "native-manifest.json": _json(native),
        "custody.json": _json(custody),
        "source-inspect.json": _json([_inspect(SOURCE_CID, collector=False)]),
        "collector-inspect.json": _json([_inspect(COLLECTOR_CID, collector=True)]),
    }
    rows = [{"path": name, "type": "file", "size_bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(entries.items())]
    internal = {"schema_version": 1, "candidate_sha": SHA,
                "candidate_tree_sha": TREE, "run_id": RUN, "run_attempt": ATTEMPT,
                "artifact_name": NAME, "file_count": len(rows),
                "total_size_bytes": sum(row["size_bytes"] for row in rows), "files": rows}
    entries[MANIFEST] = _json(internal)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    zip_bytes = payload.getvalue()
    digest = hashlib.sha256(zip_bytes).hexdigest()
    artifact = {"id": ARTIFACT_ID, "name": NAME, "digest": "sha256:" + digest,
                "size_in_bytes": len(zip_bytes), "expired": False,
                "workflow_run": {"id": RUN, "head_sha": SHA}}
    run = {"id": RUN, "run_attempt": ATTEMPT, "status": "completed",
           "conclusion": "success", "event": "pull_request",
           "name": "RC6 R163 Offhost Guard79 Proof (PROBE ONLY)",
           "path": WORKFLOW, "head_sha": SHA, "head_branch": HEAD_REF,
           "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
           "pull_requests": [{"number": 1087, "head": {"sha": SHA},
                              "base": {"ref": BASE}}]}
    pr = {"number": 1087, "state": "open", "draft": True,
          "head": {"sha": SHA, "ref": HEAD_REF, "repo": {"full_name": REPO}},
          "base": {"ref": BASE, "repo": {"full_name": REPO}}}
    event = {"action": "completed", "repository": {"full_name": REPO},
             "workflow_run": {"id": RUN, "run_attempt": ATTEMPT,
                              "conclusion": "success", "status": "completed",
                              "head_sha": SHA, "event": "pull_request",
                              "name": run["name"], "path": WORKFLOW}}
    return {"event": event, "run": run, "pr": pr,
            "commit": {"sha": SHA, "tree": {"sha": TREE}},
            "artifact_list": {"total_count": 1, "artifacts": [dict(artifact)]},
            "artifact": artifact, "zip_bytes": zip_bytes,
            "expected_run_id": RUN, "expected_run_attempt": ATTEMPT}


def _red(evidence, reason):
    try:
        verify_postrun_bytes(**evidence)
    except PostrunEvidenceRed as exc:
        assert str(exc) == reason, (str(exc), reason)
    else:
        raise AssertionError("EXPECTED_POSTRUN_RED:" + reason)


def _repack(evidence, *, rename=None, mutate=None, symlink=None):
    """Simulate a new immutable artifact with an internally consistent REST digest."""
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as original:
        members = [(info.filename, original.read(info)) for info in original.infolist()]
    if mutate:
        members = [(name, mutate[name] if name in mutate else data)
                   for name, data in members]
    rewritten = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                new_name = rename.get(name, name) if rename else name
                if new_name == symlink:
                    info = zipfile.ZipInfo(new_name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, data)
                else:
                    archive.writestr(new_name, data)
    evidence["zip_bytes"] = rewritten.getvalue()
    digest = "sha256:" + hashlib.sha256(evidence["zip_bytes"]).hexdigest()
    for receipt in (evidence["artifact"], evidence["artifact_list"]["artifacts"][0]):
        receipt["digest"] = digest
        receipt["size_in_bytes"] = len(evidence["zip_bytes"])


def _reseal_native_tar(evidence, raw_tar):
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as original:
        payload = {name: original.read(name) for name in original.namelist()}
    native = json.loads(payload["native-manifest.json"])
    native["tar_sha256"] = hashlib.sha256(raw_tar).hexdigest()
    native["tar_size_bytes"] = len(raw_tar)
    payload["native.tar"] = raw_tar
    payload["native-manifest.json"] = _json(native)
    manifest = json.loads(payload[MANIFEST])
    for row in manifest["files"]:
        data = payload[row["path"]]
        row["size_bytes"] = len(data)
        row["sha256"] = hashlib.sha256(data).hexdigest()
    manifest["total_size_bytes"] = sum(row["size_bytes"] for row in manifest["files"])
    payload[MANIFEST] = _json(manifest)
    _repack(evidence, mutate=payload)


def _reseal_inspect(evidence, file_name, mutator):
    with zipfile.ZipFile(io.BytesIO(evidence["zip_bytes"])) as original:
        payload = {name: original.read(name) for name in original.namelist()}
    inspect = json.loads(payload[file_name])
    mutator(inspect[0])
    payload[file_name] = _json(inspect)
    outer = json.loads(payload[MANIFEST])
    for row in outer["files"]:
        data = payload[row["path"]]
        row["size_bytes"] = len(data)
        row["sha256"] = hashlib.sha256(data).hexdigest()
    outer["total_size_bytes"] = sum(row["size_bytes"] for row in outer["files"])
    payload[MANIFEST] = _json(outer)
    _repack(evidence, mutate=payload)


def test_trusted_bytes_attest_exact_run_artifact_tree_and_tar_without_release_go():
    result = verify_postrun_bytes(**_evidence())
    assert result["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    assert result["run_id"] == RUN and result["run_attempt"] == ATTEMPT
    assert result["artifact_id"] == ARTIFACT_ID
    assert result["candidate_sha"] == SHA and result["candidate_tree_sha"] == TREE
    assert result["native_file_count"] == 13
    assert result["automatic_go"] is False
    assert result["release_go"] is False
    assert result["runtime_verified"] is False


MUTATIONS = [
    (lambda d: d["run"].__setitem__("status", "in_progress"), "RUN_NOT_SUCCESS"),
    (lambda d: d["run"].__setitem__("conclusion", "failure"), "RUN_NOT_SUCCESS"),
    (lambda d: d["run"].__setitem__("id", RUN + 1), "RUN_ID_MISMATCH"),
    (lambda d: d["run"].__setitem__("run_attempt", ATTEMPT + 1), "RUN_ATTEMPT_MISMATCH"),
    (lambda d: d["run"].__setitem__("head_sha", "c" * 40), "RUN_SHA_MISMATCH"),
    (lambda d: d["run"].__setitem__("path", ".github/workflows/evil.yml"), "RUN_WORKFLOW_MISMATCH"),
    (lambda d: d["run"]["repository"].__setitem__("full_name", "foreign/repo"), "RUN_REPO_MISMATCH"),
    (lambda d: d["run"].__setitem__("event", "workflow_dispatch"), "RUN_EVENT_MISMATCH"),
    (lambda d: d["pr"]["head"].__setitem__("sha", "d" * 40), "PR_HEAD_STALE"),
    (lambda d: d["pr"].__setitem__("number", 1086), "PR_IDENTITY_MISMATCH"),
    (lambda d: d["pr"]["base"].__setitem__("ref", "main"), "PR_BASE_MISMATCH"),
    (lambda d: d["commit"]["tree"].__setitem__("sha", "e" * 40), "MANIFEST_TREE_MISMATCH"),
    (lambda d: d["artifact"].__setitem__("id", ARTIFACT_ID + 1), "ARTIFACT_ID_MISMATCH"),
    (lambda d: d["artifact"].__setitem__("digest", "sha256:" + "f" * 64), "ARTIFACT_DIGEST_MISMATCH"),
    (lambda d: d["artifact"]["workflow_run"].__setitem__("id", RUN + 1), "ARTIFACT_RUN_MISMATCH"),
    (lambda d: d["artifact"].__setitem__("name", "porota-predeploy-v2-" + SHA), "ARTIFACT_NAME_MISMATCH"),
    (lambda d: d["artifact_list"].__setitem__("total_count", 2), "ARTIFACT_LIST_AMBIGUOUS"),
]


def test_wrong_run_pr_artifact_receipt_or_tree_fails_closed():
    for mutator, reason in MUTATIONS:
        evidence = _evidence()
        mutator(evidence)
        _red(evidence, reason)


def test_mutated_raw_zip_bytes_fail_even_when_zip_parser_would_accept_them():
    evidence = _evidence()
    altered = bytearray(evidence["zip_bytes"])
    altered[10] ^= 1
    evidence["zip_bytes"] = bytes(altered)
    _red(evidence, "ZIP_DIGEST_MISMATCH")


def test_resealed_zip_member_corruption_and_ambiguous_members_are_rejected():
    for rename, mutate, symlink, reason in (
        ({"native.tar": "../native.tar"}, None, None, "ZIP_PATH_UNSAFE"),
        ({"native.tar": "custody.json"}, None, None, "ZIP_DUPLICATE_MEMBER"),
        (None, None, "native.tar", "ZIP_TYPE_UNSAFE"),
        (None, {"native.tar": b"\0" * (8 * 1024 * 1024)}, None, "ZIP_EXPANSION_LIMIT"),
        (None, {"native.tar": b"mutated"}, None, "FILE_SIZE_MISMATCH"),
    ):
        evidence = _evidence()
        _repack(evidence, rename=rename, mutate=mutate, symlink=symlink)
        _red(evidence, reason)


def test_resealed_native_tar_with_missing_required_file_fails_closed():
    evidence = _evidence()
    _reseal_native_tar(evidence, _tar(paths=NATIVE_PATHS[:-1]))
    _red(evidence, "TAR_SET_INVALID")


def test_resealed_docker_inspect_cannot_escalate_host_or_collector_isolation():
    for file_name, mutator in (
        ("source-inspect.json", lambda i: i["HostConfig"].__setitem__("Privileged", True)),
        ("source-inspect.json", lambda i: i["HostConfig"].__setitem__("NetworkMode", "host")),
        ("source-inspect.json", lambda i: i["Config"].__setitem__("User", "0:0")),
        ("collector-inspect.json", lambda i: i["HostConfig"].__setitem__("VolumesFrom", [])),
    ):
        evidence = _evidence()
        _reseal_inspect(evidence, file_name, mutator)
        _red(evidence, "INSPECT_CONFIG_UNSAFE")


def test_same_run_wrong_artifact_attempt_name_and_size_fail_closed():
    for change, reason in (
        (lambda d: d["artifact"].__setitem__("name", f"rc6-r163-offhost-{RUN}-1-{SHA}"),
         "ARTIFACT_NAME_MISMATCH"),
        (lambda d: d["artifact"].__setitem__("size_in_bytes", 1),
         "ARTIFACT_SIZE_MISMATCH"),
        (lambda d: d["artifact"].__setitem__("expired", True),
         "ARTIFACT_EXPIRED"),
        (lambda d: d["event"]["workflow_run"].__setitem__("run_attempt", ATTEMPT + 1),
         "EVENT_RUN_MISMATCH"),
    ):
        evidence = _evidence()
        change(evidence)
        _red(evidence, reason)


def test_rerun_attempt_selects_only_unique_current_artifact_not_old_attempt():
    evidence = _evidence()
    previous = dict(evidence["artifact"],
                    id=ARTIFACT_ID - 1,
                    name=f"rc6-r163-offhost-{RUN}-1-{SHA}")
    evidence["artifact_list"]["total_count"] = 2
    evidence["artifact_list"]["artifacts"].insert(0, previous)
    verified = verify_postrun_bytes(**evidence)
    assert verified["artifact_id"] == ARTIFACT_ID
    assert verified["run_attempt"] == ATTEMPT


def test_duplicate_current_attempt_artifact_name_fails_closed():
    evidence = _evidence()
    impostor = dict(evidence["artifact"], id=ARTIFACT_ID + 1)
    evidence["artifact_list"]["artifacts"].append(impostor)
    evidence["artifact_list"]["total_count"] = 2
    _red(evidence, "ARTIFACT_LIST_AMBIGUOUS")


def test_missing_workflow_event_identity_fails_closed():
    evidence = _evidence()
    del evidence["event"]["workflow_run"]["id"]
    _red(evidence, "EVENT_RUN_MISMATCH")


def test_trusted_json_parser_rejects_duplicate_and_infinite_float():
    for data, reason in ((b'{"id":1,"id":2}', "JSON_DUPLICATE_KEY"),
                         (b'{"id":1,"unused":1e999}', "JSON_NONFINITE")):
        try:
            load_unique_json(data)
        except PostrunEvidenceRed as exc:
            assert str(exc) == reason, (str(exc), reason)
        else:
            raise AssertionError("EXPECTED_JSON_RED:" + reason)


class FakeGitHub:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []
        self.pr_call_count = 0
        self.change_pr_on_second_read = False

    def get_json(self, path):
        self.calls.append(("json", path))
        if path.endswith("/actions/runs/991"):
            return self.evidence["run"]
        if path.endswith("/pulls/1087"):
            self.pr_call_count += 1
            if self.change_pr_on_second_read and self.pr_call_count >= 2:
                modified = dict(self.evidence["pr"])
                modified["head"] = dict(modified["head"], sha="f" * 40)
                return modified
            return self.evidence["pr"]
        if path.endswith("/git/commits/" + SHA):
            return self.evidence["commit"]
        if path.endswith("/actions/runs/991/artifacts?per_page=100"):
            return self.evidence["artifact_list"]
        if path.endswith("/actions/artifacts/883"):
            return self.evidence["artifact"]
        raise AssertionError("UNEXPECTED_GITHUB_ENDPOINT")

    def get_zip(self, artifact_id):
        self.calls.append(("zip", artifact_id))
        assert artifact_id == ARTIFACT_ID
        return self.evidence["zip_bytes"]


def test_trusted_client_uses_rest_directly_and_rechecks_fresh_pr_after_zip():
    evidence = _evidence()
    client = FakeGitHub(evidence)
    result = audit_from_github(client, event=evidence["event"],
                               expected_run_id=RUN,
                               expected_run_attempt=ATTEMPT)
    assert result["status"] == "POSTRUN_ARTIFACT_BYTES_MATCH"
    assert client.pr_call_count == 2
    assert client.calls.count(("zip", ARTIFACT_ID)) == 1


def test_pr_head_moved_during_download_fails_closed_after_initial_match():
    evidence = _evidence()
    client = FakeGitHub(evidence)
    client.change_pr_on_second_read = True
    try:
        audit_from_github(client, event=evidence["event"],
                          expected_run_id=RUN, expected_run_attempt=ATTEMPT)
    except PostrunEvidenceRed as exc:
        assert str(exc) == "REST_RACE_RED", str(exc)
    else:
        raise AssertionError("STALE_PR_HEAD_NOT_REJECTED")


def test_zip_redirect_must_be_https_host_allowlisted_and_no_auth_in_url():
    assert allowed_storage_redirect("https://results-receiver.actions.githubusercontent.com/opaque?q=1")
    assert allowed_storage_redirect("https://productionresultssa0.blob.core.windows.net/opaque?sig=1")
    for bad in ("http://productionresultssa0.blob.core.windows.net/evil",
                "https://127.0.0.1/secret", "https://user:password@objects.githubusercontent.com/zip",
                "https://api.evil.test/artifact", "file:///etc/passwd"):
        assert not allowed_storage_redirect(bad), bad


def test_signed_zip_redirect_never_receives_github_bearer_token():
    signed = "https://productionresultssa0.blob.core.windows.net/zip?sig=private"
    raw = _evidence()["zip_bytes"]
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, count):
            return raw[:count]

    class Opener:
        def open(self, request, timeout=30):
            requests.append(request)
            if request.full_url.startswith("https://api.github.com/"):
                raise urllib.error.HTTPError(request.full_url, 302, "Found",
                                             {"Location": signed}, None)
            assert request.full_url == signed
            return Response()

    with mock.patch("scripts.rc6_r163_trusted_postrun_byte_audit.urllib.request.build_opener",
                    return_value=Opener()):
        downloaded = GitHubReadOnlyREST("read-only-secret-token").get_zip(ARTIFACT_ID)
    assert downloaded == raw
    assert requests[0].get_header("Authorization") == "Bearer read-only-secret-token"
    assert requests[1].get_header("Authorization") is None


def load_tests(loader, tests, pattern):
    """Make plain-function adversarial fixtures discoverable by stdlib unittest."""
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value, description=name))
    return suite

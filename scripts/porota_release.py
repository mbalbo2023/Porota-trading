#!/usr/bin/env python3
"""One immutable Deploy V2 release: verify bytes before loading or deploying.

The GitHub workflow authenticates run/artifact provenance. This stdlib module
checks the received files, Docker config identity, source bundle and receipts.
It does not grant host authority or infer a live PAPER counter from CI results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile

MEMBERS = frozenset({"porota-predeploy-image.tar.gz", "porota-deploy-bundle-v2.tgz",
                     "porota-deploy-bundle-v2-manifest.json", "code-tests.json", "functional.json"})
FIELDS = frozenset({"schema", "source_sha", "source_tree", "image_id", "image_size_bytes",
                    "build_run_id", "build_run_attempt", "operating_mode", "execution_mode",
                    "real_orders_sent_required", "real_order_capability", "members"})
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(data):
    return json.loads(data, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))


def _regular(path):
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "MEMBER_NOT_REGULAR_FILE")
    return info.st_size


def _hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _read_json(path):
    _require(0 < _regular(path) <= 8 * 1024 * 1024, "JSON_SIZE")
    result = _json(path.read_bytes())
    _require(isinstance(result, dict), "JSON_OBJECT_REQUIRED")
    return result


def _git(repository, *arguments):
    return subprocess.check_output(["git", "-C", str(repository), *arguments], stderr=subprocess.DEVNULL)


def _source_files(repository, sha, tree):
    try:
        from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths
    except ModuleNotFoundError:
        from porota_build_deploy_bundle_v2 import select_bundle_paths
    _require(_git(repository, "rev-parse", sha + "^{tree}").decode().strip() == tree,
             "SOURCE_TREE_MISMATCH")
    entries = {}
    for row in _git(repository, "ls-tree", "-rz", "--full-tree", sha).split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        entries[name.decode()] = (mode, kind, blob)
    result = {}
    for name in select_bundle_paths(list(entries)):
        mode, kind, blob = entries[name]
        _require(mode in {"100644", "100755"} and kind == "blob", "SOURCE_NOT_REGULAR_FILE")
        result[name] = blob
    _require(bool(result), "SOURCE_EMPTY")
    return result


def _member_bytes(archive, member, maximum=32 * 1024 * 1024):
    _require(member.isfile() and 0 <= member.size <= maximum, "ARCHIVE_MEMBER_INVALID")
    stream = archive.extractfile(member)
    _require(stream is not None, "ARCHIVE_MEMBER_MISSING")
    data = stream.read(maximum + 1)
    _require(len(data) == member.size, "ARCHIVE_MEMBER_SIZE")
    return data


def _safe_name(name):
    return (isinstance(name, str) and bool(name) and not name.startswith("/") and
            "\\" not in name and str(PurePosixPath(name)) == name and
            not any(part in {"", ".", ".."} for part in name.split("/")))


def _verify_bundle(directory, release, repository):
    manifest = _read_json(directory / "porota-deploy-bundle-v2-manifest.json")
    _require(manifest.get("schema_version") == 1 and manifest.get("status") == "GREEN",
             "BUNDLE_MANIFEST_SCHEMA")
    rows = manifest.get("files")
    _require(isinstance(rows, list) and rows and type(manifest.get("file_count")) is int
             and manifest["file_count"] == len(rows), "BUNDLE_MANIFEST_FILES")
    expected = {}
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {"path", "sha256", "bytes"}, "BUNDLE_MANIFEST_ENTRY")
        name = row["path"]
        _require(_safe_name(name) and name not in expected and DIGEST.fullmatch(str(row["sha256"]))
                 and type(row["bytes"]) is int and row["bytes"] >= 0, "BUNDLE_MANIFEST_ENTRY")
        expected[name] = row
    _require(manifest.get("bundle_sha256") == release["members"]["porota-deploy-bundle-v2.tgz"]["sha256"],
             "BUNDLE_DIGEST")
    source = _source_files(repository, release["source_sha"], release["source_tree"]) if repository else None
    if source is not None:
        _require(set(source) == set(expected), "SOURCE_MEMBERS")
    seen = set()
    total = 0
    embedded_name = "POROTA_DEPLOY_BUNDLE_MANIFEST.json"
    with tarfile.open(directory / "porota-deploy-bundle-v2.tgz", "r:gz") as archive:
        for member in archive:
            _require(_safe_name(member.name) and member.name not in seen
                     and member.name in {*expected, embedded_name}, "BUNDLE_MEMBERS")
            seen.add(member.name)
            total += member.size
            _require(total <= 256 * 1024 * 1024, "BUNDLE_SIZE")
            data = _member_bytes(archive, member)
            if member.name == embedded_name:
                _require(_json(data) == {key: value for key, value in manifest.items() if key != "bundle_sha256"},
                         "BUNDLE_EMBEDDED_MANIFEST")
                continue
            row = expected[member.name]
            _require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"],
                     "BUNDLE_MEMBER_DIGEST")
            if source is not None:
                blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                _require(blob == source[member.name], "SOURCE_BYTES_MISMATCH")
    _require(seen == {*expected, embedded_name}, "BUNDLE_MEMBERS")


def _verify_image(directory, release):
    with tarfile.open(directory / "porota-predeploy-image.tar.gz", "r:gz") as archive:
        members = archive.getmembers()
        _require(len(members) <= 100000, "IMAGE_ARCHIVE_SIZE")
        names = {entry.name: entry for entry in members}
        _require(len(names) == len(members) and "manifest.json" in names, "IMAGE_MANIFEST")
        manifest = _json(_member_bytes(archive, names["manifest.json"], 4 * 1024 * 1024))
        _require(isinstance(manifest, list) and len(manifest) == 1 and isinstance(manifest[0], dict),
                 "IMAGE_MANIFEST")
        config_name = manifest[0].get("Config")
        _require(_safe_name(config_name) and config_name in names, "IMAGE_CONFIG")
        config_bytes = _member_bytes(archive, names[config_name], 4 * 1024 * 1024)
        _require("sha256:" + hashlib.sha256(config_bytes).hexdigest() == release["image_id"], "IMAGE_ID_MISMATCH")
        config = _json(config_bytes)
        _require(isinstance(config, dict) and isinstance(config.get("config"), dict), "IMAGE_CONFIG")
        env = config.get("config", {}).get("Env", [])
        _require(isinstance(env, list) and all(isinstance(item, str) and "=" in item for item in env), "IMAGE_ENV")
        pairs = [item.split("=", 1) for item in env]
        _require(len({key for key, _ in pairs}) == len(pairs), "IMAGE_ENV_DUPLICATE")
        observed = dict(pairs)
        _require(observed.get("POROTA_BUILD_SHA") == release["source_sha"] and
                 observed.get("POROTA_CANDIDATE_TREE_SHA") == release["source_tree"], "IMAGE_SOURCE_MISMATCH")


def _verify_receipts(directory, release):
    for name, schema in (("code-tests.json", "porota.code-tests.v1"), ("functional.json", "porota.functional.v1")):
        receipt = _read_json(directory / name)
        _require(receipt.get("schema") == schema and receipt.get("status") == "PASS" and
                 all(receipt.get(key) == release[key] for key in ("source_sha", "source_tree", "image_id")),
                 "TEST_RECEIPT_IDENTITY")
        if name == "code-tests.json":
            _require(type(receipt.get("collected")) is int and receipt["collected"] > 0 and
                     type(receipt.get("executed")) is int and receipt["executed"] == receipt["collected"] and
                     all(type(receipt.get(key)) is int and receipt[key] == 0 for key in ("failed", "errors", "skipped")),
                     "CODE_TESTS_INCOMPLETE")
        else:
            _require(receipt.get("paper_mode") == "PRODUCTION_PAPER" and
                     type(receipt.get("real_orders_sent")) is int and receipt["real_orders_sent"] == 0 and
                     receipt.get("order_routes_blocked") is True and receipt.get("sqlite_integrity") == "ok" and
                     isinstance(receipt.get("http"), dict) and
                     all(type(receipt["http"].get(path)) is int and receipt["http"][path] == 200
                         for path in ("/health", "/api/dashboard/truth")), "FUNCTIONAL_PROOF_INCOMPLETE")


def verify_release(directory, *, expected_sha, expected_tree, expected_image_id=None, repository=None):
    directory = Path(directory)
    _require(directory.is_dir() and not directory.is_symlink(), "RELEASE_DIRECTORY")
    _require(set(path.name for path in directory.iterdir()) == MEMBERS | {"release.json"}, "RELEASE_MEMBERS")
    release = _read_json(directory / "release.json")
    _require(set(release) == FIELDS and release.get("schema") == "porota.release.v1", "RELEASE_SCHEMA")
    _require(isinstance(expected_sha, str) and SHA.fullmatch(expected_sha) and release["source_sha"] == expected_sha,
             "SOURCE_SHA_MISMATCH")
    _require(isinstance(expected_tree, str) and SHA.fullmatch(expected_tree) and release["source_tree"] == expected_tree,
             "SOURCE_TREE_MISMATCH")
    _require(IMAGE.fullmatch(str(release["image_id"])) and
             (expected_image_id is None or expected_image_id == release["image_id"]), "IMAGE_ID_MISMATCH")
    _require(all(type(release[key]) is int and release[key] > 0 for key in
                 ("image_size_bytes", "build_run_id", "build_run_attempt")), "RELEASE_NUMERIC_IDENTITY")
    _require(release["operating_mode"] == "PRODUCTION_PAPER" and release["execution_mode"] == "SIMULATION" and
             type(release["real_orders_sent_required"]) is int and release["real_orders_sent_required"] == 0 and
             release["real_order_capability"] == "BLOCKED", "RELEASE_PAPER_POLICY")
    _require(isinstance(release["members"], dict) and set(release["members"]) == MEMBERS, "RELEASE_MEMBERS")
    for name, item in release["members"].items():
        _require(isinstance(item, dict) and set(item) == {"sha256", "bytes"} and
                 DIGEST.fullmatch(str(item["sha256"])) and type(item["bytes"]) is int and item["bytes"] > 0,
                 "MEMBER_METADATA")
        path = directory / name
        _require(_regular(path) == item["bytes"] and _hash(path) == item["sha256"], "MEMBER_DIGEST_MISMATCH")
    _verify_image(directory, release)
    _verify_bundle(directory, release, repository)
    _verify_receipts(directory, release)
    return release


def create_release(directory, repository, *, image_id, image_size_bytes, run_id, run_attempt):
    directory, repository = Path(directory), Path(repository)
    _require(directory.is_dir() and not directory.is_symlink(), "RELEASE_DIRECTORY")
    _require(not _git(repository, "status", "--porcelain", "--untracked-files=all").strip(), "CHECKOUT_DIRTY")
    sha = _git(repository, "rev-parse", "HEAD").decode().strip()
    tree = _git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
    _require(set(path.name for path in directory.iterdir()) <= MEMBERS | {"release.json"}, "RELEASE_MEMBERS")
    path = directory / "release.json"
    if path.exists() or path.is_symlink():
        _regular(path)
    members = {}
    for name in sorted(MEMBERS):
        size = _regular(directory / name)
        members[name] = {"sha256": _hash(directory / name), "bytes": size}
    release = {"schema": "porota.release.v1", "source_sha": sha, "source_tree": tree,
               "image_id": image_id, "image_size_bytes": image_size_bytes,
               "build_run_id": run_id, "build_run_attempt": run_attempt,
               "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
               "real_orders_sent_required": 0, "real_order_capability": "BLOCKED",
               "members": members}
    path.write_text(json.dumps(release, sort_keys=True, indent=2) + "\n")
    try:
        return verify_release(directory, expected_sha=sha, expected_tree=tree, repository=repository)
    except Exception:
        path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("create")
    create.add_argument("--directory", required=True, type=Path)
    create.add_argument("--repository", required=True, type=Path)
    create.add_argument("--image-id", required=True)
    create.add_argument("--image-size-bytes", required=True, type=int)
    create.add_argument("--run-id", required=True, type=int)
    create.add_argument("--run-attempt", required=True, type=int)
    verify = sub.add_parser("verify")
    verify.add_argument("--directory", required=True, type=Path)
    verify.add_argument("--expected-sha", required=True)
    verify.add_argument("--expected-tree", required=True)
    verify.add_argument("--expected-image-id")
    verify.add_argument("--repository", type=Path)
    args = vars(parser.parse_args())
    operation = args.pop("operation")
    try:
        result = create_release(**args) if operation == "create" else verify_release(**args)
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, "POROTA_RELEASE=FAIL|" + str(error) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

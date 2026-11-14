"""Publish the six-file inbox and invoke the installed R358 during an authenticated APPLY."""
from collections.abc import Mapping
import fcntl
import hashlib
import os
import posixpath
import secrets
import stat
import subprocess
import tempfile

from scripts import rc6_genesis_custodian as custody
from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_r337_root_env_seal as seal


_VAULT, _INBOX = posixpath.split(r280.INBOX_DIR)
_LAUNCHER = "/usr/local/sbin/porota-rc6-host-installer-genesis"
_LAUNCHER_SHA = "76eac14930a913ff7ed5c0720ccb5ff5bfc050b7bebe5b4725a76685ee67aaff"
_OLD = "ba6da9439da6881c4f30708739357cdfc6da1da40e4ead74749119b27cdc9c97"
_R301 = "21b1a5f3ddabf3b77b9cb7053ae9c1c50ed57f2008917e5a3b2e9a3fd4e9ca5a"
_OWNER = "genesis-install-inbox-owner-r367.json"
_DONE = "genesis-install-complete-r367.json"
_RESULT = "INSTALLED_FILES_ONLY_NOT_HOST_GO"
_require = custody._require


def _transition_files(authority, raw):
    binding = authority["binding"]
    manifest = r280._strict_json(raw, limit=8192)
    digest = custody._digest(raw)
    _require(custody._json(manifest) == raw and digest == authority["root_manifest_sha256"]
             and manifest["schema"] == "RC6_ROOT_INSTALL_MANIFEST_V1"
             and manifest["candidate_sha"] == binding["candidate_sha"]
             and manifest["candidate_tree"] == binding["candidate_tree"]
             and manifest["paper_mode"] == "PRODUCTION_PAPER" and manifest["real_orders_allowed"] is False
             and set(manifest["files"]) == set(r280.SOURCE_ORDER), "INSTALL_MANIFEST")
    receipt = {"schema": "RC6_R305_ROOT_PIN_TRANSITION_FILES_ONLY_V1",
               "status": "PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO", "trusted_main_sha": binding["trusted_main_sha"],
               "candidate_sha": binding["candidate_sha"], "candidate_tree": binding["candidate_tree"],
               "owner_go_comment_id": authority["pin_go_id"], "root_manifest_sha256": digest,
               "witness_artifact_sha256": authority["witness_artifact_sha256"],
               "witness_run_id": authority["witness_run_id"], "witness_run_attempt": authority["witness_run_attempt"],
               "previous_manifest_sha256": _OLD, "r301_receipt_sha256": _R301, "paper_mode": "PRODUCTION_PAPER",
               "real_orders_allowed": False, "host_control_verified": False}
    return {"installer-approved-manifest.sha256": custody._digest((digest + "\n").encode("ascii")),
            "installer-approved-manifest.r305-historical-D.sha256": custody._digest((_OLD + "\n").encode("ascii")),
            "bootstrap-r297-complete.json": _R301,
            "root-manifest-pin-r305-transition.json": custody._digest(custody._json(receipt))}


def _verified_leaf(parent, name, expected, *, mode=0o400, maximum=8192):
    data, identity = custody._leaf(parent, name, mode=mode, maximum=maximum)
    _require(hashlib.sha256(data).hexdigest() == expected, "INSTALL_FILE_DIGEST")
    return identity


def verify_transition(authority, manifest_raw):
    """Correlate the installed PIN, independent witness, old archive and R301 receipt."""
    expected = _transition_files(authority, manifest_raw)
    with custody._directory(_VAULT) as (fd, directories):
        _require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "INSTALL_VAULT_MODE")
        leaves = {name: _verified_leaf(fd, name, sha) for name, sha in expected.items()}
        for name, pin in leaves.items():
            _require(custody._leaf_pin(os.stat(name, dir_fd=fd, follow_symlinks=False)) == pin, "INSTALL_PIN_CHANGED")
        custody._check_directory(_VAULT, directories)
    return [directories, leaves]


def _launcher():
    directory, name = posixpath.split(_LAUNCHER)
    with custody._directory(directory) as (fd, parents):
        pin = _verified_leaf(fd, name, _LAUNCHER_SHA, mode=0o555, maximum=128 * 1024)
        custody._check_directory(directory, parents)
        return [parents, pin]


def _targets(files, *, absent=False):
    pins = {}
    for role, (path, mode, limit) in r280.TARGETS.items():
        directory, name = posixpath.split(path)
        with custody._directory(directory) as (fd, parents):
            if absent:
                _require(not custody._exists(fd, name), "INSTALL_PREEXISTING_TARGET")
            else:
                expected = custody._digest(files[r280.SOURCE_NAMES[role]])
                pins[role] = [parents, _verified_leaf(fd, name, expected, mode=mode, maximum=limit)]
            custody._check_directory(directory, parents)
    return pins


def _inbox_snapshot(vault, name, files):
    inode = os.stat(name, dir_fd=vault, follow_symlinks=False)
    custody._directory_custody("", inode)
    _require(stat.S_IMODE(inode.st_mode) == 0o700, "INSTALL_INBOX_MODE")
    fd = os.open(name, seal._flags(True), dir_fd=vault)
    try:
        directory = custody._dir_pin(inode)
        _require(custody._dir_pin(os.fstat(fd)) == directory and set(os.listdir(fd)) == set(files),
                 "INSTALL_INBOX_CONTENTS")
        pins = {member: _verified_leaf(fd, member, custody._digest(raw), maximum=len(raw))
                for member, raw in files.items()}
        _require(set(os.listdir(fd)) == set(files) and custody._dir_pin(
            os.stat(name, dir_fd=vault, follow_symlinks=False)) == directory, "INSTALL_INBOX_CHANGED")
        return [directory, pins]
    finally:
        os.close(fd)


def _inbox(vault, files, scope, authorize):
    if not custody._exists(vault, _OWNER):
        _require(not custody._exists(vault, _INBOX) and not custody._exists(vault, _DONE), "INSTALL_INBOX_UNATTRIBUTED")
        # A scratch stage with no later durable owner record is never adopted.
        stage = ".r367-install-" + secrets.token_hex(16)
        authorize()
        os.mkdir(stage, 0o700, dir_fd=vault)
        fd = os.open(stage, seal._flags(True), dir_fd=vault)
        try:
            for name, raw in files.items():
                authorize()
                seal._write_exclusive(fd, name, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(vault)
        pins = _inbox_snapshot(vault, stage, files)
        authorize()
        custody._write_record(vault, _OWNER, {"scope": scope, "stage": stage, "pins": pins})
    owner = custody._record(vault, _OWNER)
    _require(set(owner) == {"scope", "stage", "pins"} and owner["scope"] == scope
             and isinstance(owner["stage"], str) and owner["stage"].startswith(".r367-install-")
             and seal._hex(owner["stage"][len(".r367-install-"):], 32), "INSTALL_INBOX_OTHER_OWNER")
    stage = owner["stage"]
    if not custody._exists(vault, _INBOX):
        _require(_inbox_snapshot(vault, stage, files) == owner["pins"], "INSTALL_STAGE_FOREIGN")
        authorize()
        seal._rename_no_replace(vault, stage, _INBOX)
    _require(not custody._exists(vault, stage) and _inbox_snapshot(vault, _INBOX, files) == owner["pins"],
             "INSTALL_INBOX_FOREIGN")
    os.fsync(vault)
    return owner["pins"]


def _invoke(expected, authorize):
    # The authenticated launcher emits a canonical receipt <=512 bytes. A file
    # descriptor lets the caller read at most that much without unbounded buffering.
    with tempfile.TemporaryFile() as output:
        authorize()
        result = subprocess.run(("/usr/bin/python3", "-I", _LAUNCHER, "install-approved"),
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                timeout=45, check=False, cwd="/", env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        output.seek(0)
        _require(result.returncode == 0 and output.read(513) == expected, "INSTALL_LAUNCHER_RECEIPT")


def install(authority, files, *, check_authority, verify_env, final_authority=None):
    """Acknowledge an identical completed installation or publish from proven absence."""
    _require(isinstance(files, Mapping) and set(files) == set(r280.SOURCE_NAMES.values()) | {"manifest.json"},
             "INSTALL_SOURCES")
    files = dict(files)
    raw = files["manifest.json"]
    transition = verify_transition(authority, raw)
    launcher = _launcher()
    manifest = r280._strict_json(raw, limit=8192)
    for role, name in r280.SOURCE_NAMES.items():
        limit = r280.TARGETS[role][2] if role in r280.TARGETS else 128 * 1024
        _require(type(files[name]) is bytes and 0 < len(files[name]) <= limit
                 and custody._digest(files[name]) == manifest["files"][role], "INSTALL_SOURCE_BYTES")
    scope = {key: authority[key] for key in ("binding", "proposal_id", "commitment", "nonce", "lease_id",
                                             "pin_go_id", "install_go_id", "env_go_id", "root_manifest_sha256")}
    scope.update(transition=transition, launcher=launcher)
    expected = custody._json({"schema": "RC6_ROOT_INSTALL_CODE_ONLY_V1", "status": _RESULT,
        "candidate_sha": manifest["candidate_sha"], "manifest_sha256": authority["root_manifest_sha256"],
        "host_control_verified": False})

    with custody._directory(_VAULT) as (vault, parents):
        fcntl.flock(vault, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def authorize(*, fresh=False):
            custody._check_directory(_VAULT, parents)
            _require(verify_transition(authority, raw) == transition and _launcher() == launcher,
                     "INSTALL_BOUNDARY_CHANGED")
            custody._authorized(verify_env)
            custody._authorized(final_authority if fresh and final_authority is not None else check_authority)
        authorize()
        completed = custody._exists(vault, _DONE)
        if not completed:
            _targets(files, absent=True)
        inbox = _inbox(vault, files, scope, authorize)
        if not completed:
            _targets(files, absent=True)
            _invoke(expected, lambda: authorize(fresh=True))
            targets = _targets(files)
            authorize()
            custody._write_record(vault, _DONE, {"scope": scope, "inbox": inbox, "targets": targets})
        done = custody._record(vault, _DONE)
        _require(done == {"scope": scope, "inbox": _inbox_snapshot(vault, _INBOX, files), "targets": _targets(files)},
                 "INSTALL_COMPLETION_UNVERIFIED")
        os.fsync(vault)
        authorize(fresh=True)
    return _RESULT

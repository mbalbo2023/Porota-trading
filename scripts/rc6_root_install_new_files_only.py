#!/usr/bin/python3 -I
"""Fixed GENESIS installer. Its lateral executable needs independent ROOT bootstrap."""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType


LAUNCHER = Path("/usr/local/sbin/porota-rc6-host-installer-genesis")
R280_CLI = Path("/usr/local/sbin/porota-rc6-host-installer")
R280_CONTRACT = Path("/usr/local/libexec/porota-rc6-custody/rc6_host_rootowned_installer_contract.py")
MANIFEST_PIN = Path("/var/lib/porota-rc6-oci-custody/installer-approved-manifest.sha256")
# The independent historical bootstrap fixed both executable byte streams.
CLI_SHA256 = "0d46b0f99ce09fdf4d851255e79fba4fe8af98fd7ab62c98f3c1938c484f8c7a"
CONTRACT_SHA256 = "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9"


class GenesisHold(RuntimeError):
    exit_code = 79


def _require(condition, reason):
    if not condition:
        raise GenesisHold(reason)


@contextmanager
def _directory(path):
    flags = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open("/", flags)
    try:
        for component in path.parts:
            if component != "/":
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            inode = os.fstat(fd)
            _require(stat.S_ISDIR(inode.st_mode) and inode.st_uid == inode.st_gid == 0
                     and not inode.st_mode & 0o022, "UNPROTECTED_DIRECTORY")
        yield fd
    finally:
        os.close(fd)


def _identity(inode):
    return (inode.st_dev, inode.st_ino, inode.st_uid, inode.st_gid, inode.st_mode,
            inode.st_nlink, inode.st_size, inode.st_ctime_ns)


def _read_protected(path, mode, limit):
    """Read one fixed caller-selected file. Re-open its physical parent before use."""
    with _directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            inode = os.fstat(fd)
            _require(stat.S_ISREG(inode.st_mode) and stat.S_IMODE(inode.st_mode) == mode
                     and inode.st_uid == inode.st_gid == 0 and inode.st_nlink == 1
                     and 0 < inode.st_size <= limit, "UNPROTECTED_FILE")
            raw = stream.read(limit + 1)
            with _directory(path.parent) as visible:
                name = os.stat(path.name, dir_fd=visible, follow_symlinks=False)
                _require(os.path.samestat(os.fstat(parent), os.fstat(visible))
                         and _identity(inode) == _identity(os.fstat(fd)) == _identity(name)
                         and len(raw) == inode.st_size, "FILE_CHANGED")
            return raw, _identity(inode)


def _same_directory(contract, path, held_fd):
    current = contract._open_root_directory(str(path))
    try:
        _require(os.path.samestat(os.fstat(current), os.fstat(held_fd)), "TARGET_PARENT_CHANGED")
    finally:
        os.close(current)


def _require_absent(dir_fd, name):
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise GenesisHold("TARGET_EXISTS")


def _verify_published(contract, dir_fd, name, staged, expected, mode):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        contract._protected_inode(before, is_dir=False)
        _require(os.path.samestat(before, staged) and before.st_uid == staged.st_uid
                 and before.st_gid == staged.st_gid
                 and stat.S_IMODE(before.st_mode) == mode and before.st_size == len(expected),
                 "PUBLISHED_FILE_CHANGED")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            _require(stream.read(len(expected) + 1) == expected, "PUBLISHED_FILE_CHANGED")
        after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        _require(os.path.samestat(before, after) and before.st_ctime_ns == after.st_ctime_ns,
                 "PUBLISHED_FILE_CHANGED")
    finally:
        os.close(fd)


def _verify_visible(contract, plans, published, files):
    for role, staged in published.items():
        path, dir_fd, mode = plans[role]
        _same_directory(contract, path.parent, dir_fd)
        _verify_published(contract, dir_fd, path.name, staged, files[role], mode)


def _publish_genesis(contract, manifest_raw, source_bytes, *, recheck_pin):
    """Publish from an independently pinned R280 contract; partials require forensics."""
    # Freeze caller-owned containers before performing any filesystem operation.
    _require(type(source_bytes) is dict, "SOURCE_MAP_INVALID")
    source_bytes = source_bytes.copy()
    verified = contract.verify_approved_bundle(manifest_raw, source_bytes, now=datetime.now(timezone.utc))
    approval = contract._strict_json(verified.files["approval"], limit=65536)
    _require(approval["phase"] == "GENESIS", "GENESIS_REQUIRED")
    _require(bool(getattr(os, "O_TMPFILE", 0)) and bool(getattr(os, "O_NOFOLLOW", 0)),
             "NEW_LINK_UNAVAILABLE")

    def verify_binding_now():
        # Frozen bytes only. Check expiry at the current clock after filesystem I/O.
        contract.verify_approved_bundle(manifest_raw, source_bytes, now=datetime.now(timezone.utc))

    try:
        with ExitStack() as stack:
            parents, plans, published = {}, {}, {}
            for role in contract.ORDER:
                destination, mode, _ = contract.TARGETS[role]
                path = Path(destination)
                if path.parent not in parents:
                    parent_fd = contract._open_root_directory(str(path.parent))
                    stack.callback(os.close, parent_fd)
                    parents[path.parent] = parent_fd
                dir_fd = parents[path.parent]
                _require_absent(dir_fd, path.name)
                plans[role] = (path, dir_fd, mode)

            for role, (path, dir_fd, mode) in plans.items():
                raw = verified.files[role]
                # An unnamed, fully written inode leaves no staging name to adopt
                # or clean up after failure. O_EXCL would prohibit linking it.
                file_fd = os.open(".", os.O_TMPFILE | os.O_RDWR, 0o600, dir_fd=dir_fd)
                stack.callback(os.close, file_fd)
                os.fchmod(file_fd, mode)
                if os.geteuid() == 0:
                    os.fchown(file_fd, contract.ROOT_UID, 0)
                with os.fdopen(file_fd, "wb", closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                os.fsync(file_fd)
                # Keep the fd open until the final receipt check. Snapshot its
                # identity and ownership now: a later fstat would follow chown.
                staged = os.fstat(file_fd)
                recheck_pin()
                if role == "approval":
                    _verify_visible(contract, plans, published, verified.files)
                _same_directory(contract, path.parent, dir_fd)
                verify_binding_now()
                try:
                    # /proc/self/fd identifies this still-open anonymous inode.
                    # Following the source descriptor never follows a target link.
                    os.link(f"/proc/self/fd/{file_fd}", path.name, dst_dir_fd=dir_fd,
                            follow_symlinks=True)
                except FileExistsError:
                    raise GenesisHold("TARGET_EXISTS") from None
                os.fsync(dir_fd)
                _verify_published(contract, dir_fd, path.name, staged, raw, mode)
                published[role] = staged
            # The approval link can race a final or its parent after preflight.
            # Sample all four through their fixed paths before emitting evidence.
            _verify_visible(contract, plans, published, verified.files)
            recheck_pin()
            # A concurrent creator can move a published path during the PIN read.
            _verify_visible(contract, plans, published, verified.files)
            verify_binding_now()
    except OSError as exc:
        raise GenesisHold("NEW_FILES_IO_UNVERIFIED") from exc

    return {"schema": "RC6_ROOT_GENESIS_NEW_FILES_ONLY_V1",
            "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
            "candidate_sha": verified.candidate_sha, "manifest_sha256": verified.manifest_sha256,
            "host_control_verified": False, "deploy_authorized": False}


def _read_stage(contract):
    inbox = Path(contract.INBOX_DIR)
    names = {"manifest.json", *contract.SOURCE_NAMES.values()}
    with _directory(inbox) as directory:
        initial = os.fstat(directory)
        _require(stat.S_IMODE(initial.st_mode) == 0o700 and set(os.listdir(directory)) == names,
                 "INBOX_SCOPE")
        manifest, _ = _read_protected(inbox / "manifest.json", 0o400, 8192)
        sources = {}
        for role, name in contract.SOURCE_NAMES.items():
            limit = contract.TARGETS[role][2] if role in contract.TARGETS else 128 * 1024
            sources[role], _ = _read_protected(inbox / name, 0o400, limit)
        with _directory(inbox) as visible:
            _require(_identity(os.fstat(visible)) == _identity(initial)
                     and set(os.listdir(visible)) == names, "INBOX_CHANGED")
    return manifest, sources


def _install():
    _require(sys.argv[1:] == ["install-approved"] and os.geteuid() == 0
             and sys.flags.isolated == 1 and Path(__file__) == LAUNCHER,
             "FIXED_BOOTSTRAPPED_ENTRY_REQUIRED")
    _read_protected(LAUNCHER, 0o555, 128 * 1024)
    cli_raw, _ = _read_protected(R280_CLI, 0o555, 128 * 1024)
    contract_raw, _ = _read_protected(R280_CONTRACT, 0o400, 1024 * 1024)
    _require(hashlib.sha256(cli_raw).hexdigest() == CLI_SHA256
             and hashlib.sha256(contract_raw).hexdigest() == CONTRACT_SHA256, "BOOTSTRAP_CODE_PIN")
    # No project import or executable inbox bytes. Only these two preverified buffers execute.
    cli = ModuleType("_rc6_genesis_verified_r280_cli")
    cli.__file__ = str(R280_CLI)
    exec(compile(cli_raw, cli.__file__, "exec", dont_inherit=True), cli.__dict__)
    contract = cli.load_verified_contract_module(contract_raw)
    pin_snapshot = _read_protected(MANIFEST_PIN, 0o400, 65)
    pin = cli.parse_approved_manifest_pin(pin_snapshot[0])
    contract.TRUSTED_MANIFEST_SHA256 = pin
    manifest, sources = _read_stage(contract)

    def recheck_pin():
        _require(_read_protected(MANIFEST_PIN, 0o400, 65) == pin_snapshot, "PIN_CHANGED")

    receipt = _publish_genesis(contract, manifest, sources, recheck_pin=recheck_pin)
    candidate = contract._strict_json(manifest, limit=8192)["candidate_sha"]
    expected = {"schema": "RC6_ROOT_GENESIS_NEW_FILES_ONLY_V1",
                "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO", "candidate_sha": candidate,
                "manifest_sha256": pin, "host_control_verified": False, "deploy_authorized": False}
    _require(type(receipt) is dict and receipt == expected and receipt["host_control_verified"] is False
             and receipt["deploy_authorized"] is False, "INTERNAL_RECEIPT")
    wire = {"schema": "RC6_ROOT_INSTALL_CODE_ONLY_V1", "status": receipt["status"],
            "candidate_sha": candidate, "manifest_sha256": pin, "host_control_verified": False}
    cli.validate_installer_receipt(wire, pin)
    raw = json.dumps(wire, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    _require(len(raw.encode("ascii")) <= 512, "RECEIPT_SIZE")
    return raw


def main():
    try:
        output = _install()
    except GenesisHold as exc:
        print("RC6_ROOT_GENESIS=HOLD79|reason=" + str(exc))
        return 79
    except Exception:
        print("RC6_ROOT_GENESIS=HOLD79|reason=BOOTSTRAP_OR_BUNDLE_UNVERIFIED")
        return 79
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

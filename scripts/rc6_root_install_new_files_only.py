#!/usr/bin/python3 -I
"""GENESIS-only publisher for a future independently bootstrapped ROOT launcher.

The private engine expects the reviewed R280 module with its independent
manifest pin already established. Its five source roles and four destinations
come exclusively from that contract. The command line remains on HOLD until a
separate physical bootstrap and controller authenticate the installation.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import stat


class GenesisHold(RuntimeError):
    exit_code = 79


def _require(condition, reason):
    if not condition:
        raise GenesisHold(reason)


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


def _publish_genesis(contract, manifest_raw, source_bytes):
    """Publish from an independently pinned R280 contract; partials require forensics."""
    # Freeze caller-owned containers before performing any filesystem operation.
    _require(type(source_bytes) is dict, "SOURCE_MAP_INVALID")
    source_bytes = source_bytes.copy()
    verified = contract.verify_approved_bundle(manifest_raw, source_bytes, now=datetime.now(timezone.utc))
    approval = contract._strict_json(verified.files["approval"], limit=65536)
    _require(approval["phase"] == "GENESIS", "GENESIS_REQUIRED")
    _require(bool(getattr(os, "O_TMPFILE", 0)) and bool(getattr(os, "O_NOFOLLOW", 0)),
             "NEW_LINK_UNAVAILABLE")

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
                if role == "approval":
                    _verify_visible(contract, plans, published, verified.files)
                    # Recheck the independent manifest pin and expiry at sealing.
                    contract.verify_approved_bundle(manifest_raw, source_bytes, now=datetime.now(timezone.utc))
                _same_directory(contract, path.parent, dir_fd)
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
    except OSError as exc:
        raise GenesisHold("NEW_FILES_IO_UNVERIFIED") from exc

    return {"schema": "RC6_ROOT_GENESIS_NEW_FILES_ONLY_V1",
            "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
            "candidate_sha": verified.candidate_sha, "manifest_sha256": verified.manifest_sha256,
            "host_control_verified": False, "deploy_authorized": False}


def main():
    print("RC6_ROOT_GENESIS=HOLD79|reason=INDEPENDENT_BOOTSTRAP_UNVERIFIED")
    return 79


if __name__ == "__main__":
    raise SystemExit(main())

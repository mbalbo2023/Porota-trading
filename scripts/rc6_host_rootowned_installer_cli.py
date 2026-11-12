#!/usr/bin/python3 -I
"""R280-B fixed-verb root-owned installer CLI; code-only until bootstrapped.

Run exclusively from /usr/local/sbin/porota-rc6-host-installer under the
independently approved root-only host control plane. Direct invocation from
the repository checkout always HOLD79. No SSH, sudo, Docker, systemd, DB,
network or arbitrary command is launched by this module.

Root provenance is NOT established by source code. The physical bootstrap
owner must separately install this reviewed launcher, protected contract,
and an *independent* manifest approval pin. The launcher neither writes nor
derives that pin from the incoming bundle. Absent physical authority HOLD79.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType


class CliHold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason):
            reason = "UNVERIFIED"
        self.reason = reason
        super().__init__("RC6_ROOT_INSTALLER_CLI_HOLD79:" + reason)


def _require(ok, reason):
    if not ok:
        raise CliHold(reason)


INSTALLED_CLI_PATH = "/usr/local/sbin/porota-rc6-host-installer"
INSTALLED_CONTRACT_PATH = (
    "/usr/local/libexec/porota-rc6-custody/rc6_host_rootowned_installer_contract.py"
)
APPROVED_MANIFEST_PIN_FILE = (
    "/var/lib/porota-rc6-oci-custody/installer-approved-manifest.sha256"
)
# Independent source-review SHA of the exact R280-A3 module carried to main.
# This is a compiled constant of the *installed launcher*, not an operator
# dispatch field or a checksum supplied by the import target.
TRUSTED_CONTRACT_SHA256 = "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9"
ROOT_UID = 0
NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", None)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_KEYS = frozenset({
    "schema", "status", "manifest_sha256", "candidate_sha", "host_control_verified",
})


def validate_invocation(argv, *, euid, isolated, current_path):
    """Reject every route except one fixed installed root entrypoint."""
    _require(type(argv) in (tuple, list) and argv == ["install-approved"],
             "FIXED_CLI_VERB_ONLY")
    _require(type(euid) is int and euid == ROOT_UID, "ROOT_EUID_REQUIRED")
    _require(type(isolated) is int and isolated == 1, "PYTHON_ISOLATED_REQUIRED")
    _require(type(current_path) is str and current_path == INSTALLED_CLI_PATH,
             "ROOT_INSTALLED_CLI_PATH_REQUIRED")


def _require_nofollow():
    _require(type(NOFOLLOW_FLAG) is int and NOFOLLOW_FLAG != 0,
             "OS_NOFOLLOW_REQUIRED")


def _open_root_directory(path):
    _require_nofollow()
    _require(type(path) is str and path.startswith("/") and "//" not in path and
             "/../" not in path and not path.endswith("/.."), "ROOT_FIXED_DIR_SHAPE")
    flags = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW_FLAG
    fd = os.open("/", flags)
    try:
        _validate_root_inode(os.fstat(fd), mode=None, directory=True)
        for component in Path(path).parts[1:]:
            _require(component not in ("", ".", ".."), "ROOT_FIXED_DIR_SHAPE")
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            _validate_root_inode(os.fstat(fd), mode=None, directory=True)
        return fd
    except (OSError, CliHold) as exc:
        os.close(fd)
        if isinstance(exc, CliHold):
            raise
        raise CliHold("ROOT_DIR_UNVERIFIED") from exc


def _validate_root_inode(inode, *, mode, directory):
    if directory:
        _require(stat.S_ISDIR(inode.st_mode), "ROOT_INODE_NOT_DIRECTORY")
    else:
        _require(stat.S_ISREG(inode.st_mode) and inode.st_nlink == 1,
                 "ROOT_INODE_ALIAS")
    _require(inode.st_uid == ROOT_UID and not inode.st_mode & 0o022,
             "ROOT_INODE_OWNER_OR_MODE")
    if mode is not None:
        _require(stat.S_IMODE(inode.st_mode) == mode, "ROOT_INODE_MODE")


def read_root_protected_file(path, *, max_bytes, mode):
    """Exact, bounded, non-aliased root file with every ancestor checked."""
    _require_nofollow()
    _require(type(path) is str and path in (
        INSTALLED_CLI_PATH, INSTALLED_CONTRACT_PATH, APPROVED_MANIFEST_PIN_FILE,
    ) and type(max_bytes) is int and 0 < max_bytes <= 1024 * 1024 and
        mode in (0o400, 0o555), "ROOT_FILE_FIXED_SCOPE")
    parent_fd = _open_root_directory(str(Path(path).parent))
    try:
        fd = os.open(Path(path).name,
                     os.O_RDONLY | NOFOLLOW_FLAG | getattr(os, "O_CLOEXEC", 0),
                     dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            _validate_root_inode(before, mode=mode, directory=False)
            _require(0 < before.st_size <= max_bytes, "ROOT_FILE_SIZE")
            raw = os.read(fd, max_bytes + 1)
            after = os.fstat(fd)
            _require(len(raw) == before.st_size and
                     after.st_ino == before.st_ino and after.st_size == before.st_size,
                     "ROOT_FILE_CHANGED")
            return raw
        finally:
            os.close(fd)
    except OSError as exc:
        raise CliHold("ROOT_FILE_MISSING_OR_ALIAS") from exc
    finally:
        os.close(parent_fd)


def parse_approved_manifest_pin(raw):
    """Pin is from independent root-owned authority, not install bundle."""
    _require(type(raw) is bytes and len(raw) in (64, 65),
             "ROOT_MANIFEST_APPROVAL_PIN_INVALID")
    try:
        value = raw.decode("ascii", "strict")
    except UnicodeError as exc:
        raise CliHold("ROOT_MANIFEST_APPROVAL_PIN_INVALID") from exc
    if value.endswith("\n"):
        value = value[:-1]
    _require(_HEX64.fullmatch(value), "ROOT_MANIFEST_APPROVAL_PIN_INVALID")
    return value


def load_verified_contract_module(raw):
    """Execute exactly SHA-pinned *bytes*; never re-open a mutable module path."""
    _require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024 and
             hashlib.sha256(raw).hexdigest() == TRUSTED_CONTRACT_SHA256,
             "ROOT_CONTRACT_CODE_PIN_MISMATCH")
    name = "_porota_rc6_root_installer_contract_verified"
    module = ModuleType(name)
    module.__file__ = INSTALLED_CONTRACT_PATH
    module.__package__ = ""
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        code = compile(raw, INSTALLED_CONTRACT_PATH, "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except Exception as exc:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise CliHold("ROOT_CONTRACT_EXEC_UNVERIFIED") from exc
    return module


def validate_installer_receipt(doc, pin):
    _require(type(doc) is dict and set(doc) == _RECEIPT_KEYS and
             doc["schema"] == "RC6_ROOT_INSTALL_CODE_ONLY_V1" and
             doc["status"] == "INSTALLED_FILES_ONLY_NOT_HOST_GO" and
             type(doc["manifest_sha256"]) is str and doc["manifest_sha256"] == pin and
             type(doc["candidate_sha"]) is str and _HEX40.fullmatch(doc["candidate_sha"]) and
             doc["host_control_verified"] is False,
             "ROOT_INSTALL_RECEIPT_UNVERIFIED")
    return doc


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        validate_invocation(argv, euid=os.geteuid(), isolated=sys.flags.isolated,
                            current_path=str(Path(__file__).absolute()))
        # No real launcher is trusted just because checkout source exists.
        read_root_protected_file(INSTALLED_CLI_PATH, max_bytes=128 * 1024,
                                 mode=0o555)
        raw = read_root_protected_file(INSTALLED_CONTRACT_PATH,
                                       max_bytes=1024 * 1024, mode=0o400)
        contract = load_verified_contract_module(raw)
        pin = parse_approved_manifest_pin(read_root_protected_file(
            APPROVED_MANIFEST_PIN_FILE, max_bytes=128, mode=0o400))
        # External root-only pin is the only way to lift A3's default HOLD.
        contract.TRUSTED_MANIFEST_SHA256 = pin
        receipt = validate_installer_receipt(contract.install_approved_bundle(), pin)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except CliHold as exc:
        print("RC6_ROOT_INSTALLER=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79
    except Exception:
        print("RC6_ROOT_INSTALLER=HOLD79|reason=ROOT_INSTALL_BACKEND_UNVERIFIED|"
              "host_control_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 79


if __name__ == "__main__":
    sys.exit(main())

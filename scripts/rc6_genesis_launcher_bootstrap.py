"""Publish the reviewed R358 launcher by name, without invoking it."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

from scripts import rc6_genesis_launcher_authority as authority
from scripts import rc6_r295_host_bootstrap_guard as legacy


TARGET = authority.LAUNCHER_TARGET
DIGEST = authority.LAUNCHER_SHA256
SOURCE = Path(__file__).with_name("rc6_root_install_new_files_only.py")
PARENTS = ("/", "/usr", "/usr/local", "/usr/local/sbin")
PRIVATE = "/usr/local/sbin/.porota-rc6-genesis-bootstrap"
MARKER = PRIVATE + "/owner"
STAGE = PRIVATE + "/launcher"
SUDO = "/usr/bin/sudo -n /usr/bin/"
UID = "/usr/bin/id -u"
LOGIN = "/usr/bin/id -un"


def _stat_cmd(path):
    return SUDO + "stat -c '%f|%u|%g|%h|%d|%i|%s|%z' -- " + path


def _write_cmd(path):
    return SUDO + "dd of=" + path + " conv=excl,fsync oflag=nofollow status=none"


def _sha_cmd(path):
    return SUDO + "sha256sum -- " + path


MKDIR = SUDO + "mkdir -m 0700 -- " + PRIVATE
CHMOD = SUDO + "chmod 0555 -- " + STAGE
LINK = SUDO + "ln -T -- " + STAGE + " " + TARGET
UNSTAGE = SUDO + "rm -- " + STAGE
UNMARK = SUDO + "rm -- " + MARKER
RMDIR = SUDO + "rmdir -- " + PRIVATE
MUTATIONS = frozenset((MKDIR, _write_cmd(MARKER), _write_cmd(STAGE), CHMOD,
                       LINK, UNSTAGE, UNMARK, RMDIR))
FILES = (MARKER, STAGE, TARGET)
LAUNCHER_COMMANDS = MUTATIONS | frozenset((
    UID, LOGIN, legacy.HOST_ID_COMMAND, legacy.HOST_REGION_COMMAND,
    *(_stat_cmd(path) for path in (*PARENTS, PRIVATE, *FILES)),
    *(SUDO + "test " + flag + " " + path for path in (PRIVATE, *FILES)
      for flag in ("-e", "-L")),
    *(_sha_cmd(path) for path in FILES),
    *(SUDO + "sync -- " + path for path in (PARENTS[-1], PRIVATE, TARGET)),
))


class LauncherSSHHost(legacy.StrictSSHHost):
    def _allows(self, command):
        return command in LAUNCHER_COMMANDS


def _require(condition, reason):
    if not condition:
        raise legacy.BootstrapHold(reason)


def _call(host, command, stdin=None, allowed=(0,)):
    _require(command in LAUNCHER_COMMANDS, "LAUNCHER_COMMAND_DENIED")
    _require(stdin is None or command in (_write_cmd(STAGE), _write_cmd(MARKER)),
             "LAUNCHER_STDIN_DENIED")
    rc, data = host.call(command, stdin=stdin)
    _require(type(rc) is int and rc in allowed and type(data) is bytes and len(data) <= 512,
             "LAUNCHER_COMMAND_FAILED")
    return rc, data


def _exists(host, path):
    link, _ = _call(host, SUDO + "test -L " + path, allowed=(0, 1))
    _require(link == 1, "LAUNCHER_PATH_SYMLINK")
    code, _ = _call(host, SUDO + "test -e " + path, allowed=(0, 1))
    return code == 0


@dataclass(frozen=True)
class Inode:
    mode: int
    uid: int
    gid: int
    links: int
    dev: int
    ino: int
    size: int
    ctime: bytes

    @property
    def key(self):
        return (self.dev, self.ino)


def _stat(host, path):
    _, raw = _call(host, _stat_cmd(path))
    fields = raw.rstrip(b"\n").split(b"|")
    _require(len(fields) == 8 and re.fullmatch(rb"[0-9a-f]{1,8}", fields[0]) and
             all(re.fullmatch(rb"[0-9]{1,20}", value) for value in fields[1:7]) and
             0 < len(fields[7]) < 100 and b"\n" not in fields[7], "LAUNCHER_STAT_INVALID")
    result = Inode(int(fields[0], 16), *(int(value) for value in fields[1:7]), fields[7])
    _require(result.uid == result.gid == 0 and result.ino > 0, "LAUNCHER_OWNERSHIP_INVALID")
    return result


def _directory(host, path):
    inode = _stat(host, path)
    _require(stat.S_ISDIR(inode.mode) and not inode.mode & 0o022 and
             (path != PRIVATE or stat.S_IMODE(inode.mode) == 0o700), "LAUNCHER_PARENT_UNSAFE")
    return inode.key


def _file(host, path, raw, *, links=(1,), mode=None):
    before = _stat(host, path)
    _require(stat.S_ISREG(before.mode) and before.links in links and before.size == len(raw),
             "LAUNCHER_FILE_IDENTITY_INVALID")
    bits = stat.S_IMODE(before.mode)
    if mode is None:
        # dd creates an ordinary input file with the host umask under root0700.
        _require(bits & ~0o666 == 0 and bits & 0o400, "LAUNCHER_STAGE_MODE_INVALID")
    else:
        _require(bits == mode, "LAUNCHER_MODE_INVALID")
    _, digest = _call(host, _sha_cmd(path))
    expected = hashlib.sha256(raw).hexdigest() + "  " + path + "\n"
    _require(digest == expected.encode("ascii") and _stat(host, path) == before,
             "LAUNCHER_FILE_DIGEST_OR_IDENTITY_DRIFT")
    return before


def install_launcher(host, context, source, check_authority):
    """One protected filesystem transaction. An unknown partial is left in place."""
    _require(type(source) is bytes and hashlib.sha256(source).hexdigest() == DIGEST,
             "LAUNCHER_SOURCE_SHA_MISMATCH")
    _require(context.user == "porotaadmin", "LAUNCHER_LOGIN_MISMATCH")
    _, droplet = _call(host, legacy.HOST_ID_COMMAND)
    _, region = _call(host, legacy.HOST_REGION_COMMAND)
    legacy.verify_host_identity(droplet, region, context)
    _require(_call(host, UID)[1] == b"1000\n" and
             _call(host, LOGIN)[1] == b"porotaadmin\n", "LAUNCHER_LOGIN_MISMATCH")
    parents = tuple(_directory(host, path) for path in PARENTS)
    private_key = None
    marker = f"R362 {context.main_sha} {context.owner_comment_id} {DIGEST}\n".encode("ascii")

    def check():
        _require(tuple(_directory(host, path) for path in PARENTS) == parents,
                 "LAUNCHER_PARENT_CHANGED")
        if private_key is not None:
            _require(_directory(host, PRIVATE) == private_key, "LAUNCHER_RESERVATION_CHANGED")

    def mutate(command, data=None):
        # Authority must be the last external read. Hashing and parent checks
        # can outlast a GO. Never publish or remove a name on a stale snapshot.
        check()
        check_authority()
        _call(host, command, data)

    def observed(status):
        check()
        check_authority()
        return status

    def reserve_ok():
        _file(host, MARKER, marker)

    def final():
        return _file(host, TARGET, source, mode=0o555)

    preexisting = _exists(host, TARGET)
    if not _exists(host, PRIVATE):
        if preexisting:
            previous = final()
            _require(not _exists(host, PRIVATE) and final() == previous, "LAUNCHER_FINAL_DRIFT")
            return observed("ALREADY_PRESENT_VERIFIED")
        mutate(MKDIR)
        private_key = _directory(host, PRIVATE)
        mutate(_write_cmd(MARKER), marker)
        _call(host, SUDO + "sync -- " + PRIVATE)
        _call(host, SUDO + "sync -- " + PARENTS[-1])
    else:
        private_key = _directory(host, PRIVATE)
    _require(private_key[0] == parents[-1][0], "LAUNCHER_FILESYSTEM_MISMATCH")
    if preexisting and not _exists(host, MARKER):
        # The preceding run may have lost the unlink reply. A lone directory
        # has no owner receipt. Report the proven launcher and retain evidence.
        previous = final()
        _require(not _exists(host, MARKER) and not _exists(host, STAGE) and
                 final() == previous, "LAUNCHER_UNATTRIBUTED_PARTIAL")
        return observed("ALREADY_PRESENT_RESERVATION_RETAINED")
    reserve_ok()

    expected_key = None
    if not preexisting:
        if not _exists(host, STAGE):
            reserve_ok()
            mutate(_write_cmd(STAGE), source)
        reserve_ok()
        stage = _stat(host, STAGE)
        if stat.S_IMODE(stage.mode) != 0o555:
            _file(host, STAGE, source)
            mutate(CHMOD)
        reserve_ok()
        staged = _file(host, STAGE, source, mode=0o555)
        _require(staged.dev == private_key[0], "LAUNCHER_FILESYSTEM_MISMATCH")
        expected_key = staged.key
        mutate(LINK)

    if _exists(host, STAGE):
        reserve_ok()
        staged = _file(host, STAGE, source, links=(2,), mode=0o555)
        linked = _file(host, TARGET, source, links=(2,), mode=0o555)
        _require(staged == linked and staged.dev == private_key[0] and
                 (expected_key is None or linked.key == expected_key), "LAUNCHER_LINK_MISMATCH")
        mutate(UNSTAGE)
        expected_key = linked.key
    else:
        _require(preexisting, "LAUNCHER_LINK_STATE_UNVERIFIED")
        expected_key = final().key

    _call(host, SUDO + "sync -- " + TARGET)
    _call(host, SUDO + "sync -- " + PARENTS[-1])
    reserve_ok()
    _require(not _exists(host, STAGE) and final().key == expected_key, "LAUNCHER_FINAL_DRIFT")
    mutate(UNMARK)
    _require(final().key == expected_key, "LAUNCHER_FINAL_DRIFT")
    mutate(RMDIR)  # Never remove unexpected extra contents.
    private_key = None
    _call(host, SUDO + "sync -- " + PARENTS[-1])
    _require(not _exists(host, PRIVATE) and final().key == expected_key, "LAUNCHER_FINAL_DRIFT")
    return observed("ALREADY_PRESENT_VERIFIED" if preexisting else "PUBLISHED")


def main(argv=None, *, env=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    try:
        _require(argv == ["--install-approved-launcher"] and
                 env.get("BOOTSTRAP_ACTION") == "install_genesis_launcher",
                 "FIXED_LAUNCHER_VERB_ONLY")
        context = legacy.validate_context(env)
        _require(context.user == "porotaadmin", "LAUNCHER_LOGIN_MISMATCH")
        # This call verifies the local checkout and live main before opening SSH.
        def recheck():
            authority.verify_authority(env)

        recheck()
        source = legacy._read_reviewed_file(SOURCE, 128 * 1024)
        _require(hashlib.sha256(source).hexdigest() == DIGEST, "LAUNCHER_SOURCE_SHA_MISMATCH")
        with LauncherSSHHost(context, env) as host:
            result = install_launcher(host, context, source, recheck)
        print(f"RC6_R362_LAUNCHER={result}|sha256={DIGEST}|host_control_verified=false|deploy_authorized=false")
        return 0
    except legacy.BootstrapHold as exc:
        reason = exc.reason
    except Exception:
        reason = "UNVERIFIED"
    print("RC6_R362_LAUNCHER=HOLD79|reason=" + reason + "|host_control_verified=false|deploy_authorized=false")
    return 79


if __name__ == "__main__":
    raise SystemExit(main())

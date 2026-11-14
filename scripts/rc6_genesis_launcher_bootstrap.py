"""Publish one of two reviewed GENESIS executables, without invoking either."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

from scripts import rc6_genesis_launcher_authority as authority
from scripts import rc6_r295_host_bootstrap_guard as legacy


SOURCE = Path(__file__).with_name("rc6_root_install_new_files_only.py")
SUDO = "/usr/bin/sudo -n /usr/bin/"
UID = "/usr/bin/id -u"
LOGIN = "/usr/bin/id -un"


def _stat_cmd(path):
    return SUDO + "stat -c '%f|%u|%g|%h|%d|%i|%s|%z' -- " + path


def _write_cmd(path):
    return SUDO + "dd of=" + path + " conv=excl,fsync oflag=nofollow status=none"


def _sha_cmd(path):
    return SUDO + "sha256sum -- " + path


class Publication:
    """Fixed paths, staging files and command vocabulary for one reviewed artifact."""

    def __init__(self, target, reservation, stage_name):
        self.target = target
        self.parents = tuple(str(parent) for parent in reversed(Path(target).parents))
        self.private = self.parents[-1] + "/" + reservation
        self.marker = self.private + "/owner"
        self.stage = self.private + "/" + stage_name
        self.mkdir = SUDO + "mkdir -m 0700 -- " + self.private
        self.chmod = SUDO + "chmod 0555 -- " + self.stage
        self.link = SUDO + "ln -T -- " + self.stage + " " + target
        self.unstage = SUDO + "rm -- " + self.stage
        self.unmark = SUDO + "rm -- " + self.marker
        self.rmdir = SUDO + "rmdir -- " + self.private
        self.mutations = frozenset((self.mkdir, _write_cmd(self.marker), _write_cmd(self.stage),
                                    self.chmod, self.link, self.unstage, self.unmark, self.rmdir))
        files = (self.marker, self.stage, target)
        self.commands = self.mutations | frozenset((
            UID, LOGIN, legacy.HOST_ID_COMMAND, legacy.HOST_REGION_COMMAND,
            *(_stat_cmd(path) for path in (*self.parents, self.private, *files)),
            *(SUDO + "test " + flag + " " + path for path in (self.private, *files)
              for flag in ("-e", "-L")),
            *(_sha_cmd(path) for path in files),
            *(SUDO + "sync -- " + path for path in (self.parents[-1], self.private, target)),
        ))


LAUNCHER = Publication(authority.LAUNCHER_TARGET, ".porota-rc6-genesis-bootstrap", "launcher")
CUSTODIAN = Publication(authority.CUSTODIAN_TARGET, ".porota-rc6-custodian-bootstrap", "custodian")


class LauncherSSHHost(legacy.StrictSSHHost):
    def _allows(self, command):
        return command in LAUNCHER.commands


class CustodianSSHHost(legacy.StrictSSHHost):
    def _allows(self, command):
        return command in CUSTODIAN.commands


def _require(condition, reason):
    if not condition:
        raise legacy.BootstrapHold(reason)


def _call(publication, host, command, stdin=None, allowed=(0,)):
    _require(command in publication.commands, "LAUNCHER_COMMAND_DENIED")
    _require(stdin is None or command in (_write_cmd(publication.stage), _write_cmd(publication.marker)),
             "LAUNCHER_STDIN_DENIED")
    rc, data = host.call(command, stdin=stdin)
    _require(type(rc) is int and rc in allowed and type(data) is bytes and len(data) <= 512,
             "LAUNCHER_COMMAND_FAILED")
    return rc, data


def _exists(publication, host, path):
    link, _ = _call(publication, host, SUDO + "test -L " + path, allowed=(0, 1))
    _require(link == 1, "LAUNCHER_PATH_SYMLINK")
    code, _ = _call(publication, host, SUDO + "test -e " + path, allowed=(0, 1))
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


def _stat(publication, host, path):
    _, raw = _call(publication, host, _stat_cmd(path))
    fields = raw.rstrip(b"\n").split(b"|")
    _require(len(fields) == 8 and re.fullmatch(rb"[0-9a-f]{1,8}", fields[0]) and
             all(re.fullmatch(rb"[0-9]{1,20}", value) for value in fields[1:7]) and
             0 < len(fields[7]) < 100 and b"\n" not in fields[7], "LAUNCHER_STAT_INVALID")
    result = Inode(int(fields[0], 16), *(int(value) for value in fields[1:7]), fields[7])
    _require(result.uid == result.gid == 0 and result.ino > 0, "LAUNCHER_OWNERSHIP_INVALID")
    return result


def _directory(publication, host, path):
    inode = _stat(publication, host, path)
    _require(stat.S_ISDIR(inode.mode) and not inode.mode & 0o022 and
             (path != publication.private or stat.S_IMODE(inode.mode) == 0o700), "LAUNCHER_PARENT_UNSAFE")
    return inode.key


def _file(publication, host, path, raw, *, links=(1,), mode=None):
    before = _stat(publication, host, path)
    _require(stat.S_ISREG(before.mode) and before.links in links and before.size == len(raw),
             "LAUNCHER_FILE_IDENTITY_INVALID")
    bits = stat.S_IMODE(before.mode)
    if mode is None:
        # dd creates an ordinary input file with the host umask under root0700.
        _require(bits & ~0o666 == 0 and bits & 0o400, "LAUNCHER_STAGE_MODE_INVALID")
    else:
        _require(bits == mode, "LAUNCHER_MODE_INVALID")
    _, digest = _call(publication, host, _sha_cmd(path))
    expected = hashlib.sha256(raw).hexdigest() + "  " + path + "\n"
    _require(digest == expected.encode("ascii") and _stat(publication, host, path) == before,
             "LAUNCHER_FILE_DIGEST_OR_IDENTITY_DRIFT")
    return before


def _publish(publication, host, context, source, check_authority):
    """One protected filesystem transaction. An unknown partial is left in place."""
    if publication is LAUNCHER:
        release, digest = "R362", authority.LAUNCHER_SHA256
    else:
        _require(publication is CUSTODIAN, "UNKNOWN_PUBLICATION")
        release, digest = "R367", authority.CUSTODIAN_SHA256
    _require(type(source) is bytes and 0 < len(source) <= 1024 * 1024 and hashlib.sha256(source).hexdigest() == digest,
             "LAUNCHER_SOURCE_SHA_MISMATCH")
    _require(context.user == "porotaadmin", "LAUNCHER_LOGIN_MISMATCH")
    _, droplet = _call(publication, host, legacy.HOST_ID_COMMAND)
    _, region = _call(publication, host, legacy.HOST_REGION_COMMAND)
    legacy.verify_host_identity(droplet, region, context)
    _require(_call(publication, host, UID)[1] == b"1000\n" and
             _call(publication, host, LOGIN)[1] == b"porotaadmin\n", "LAUNCHER_LOGIN_MISMATCH")
    parents = tuple(_directory(publication, host, path) for path in publication.parents)
    private_key = None
    marker = f"{release} {context.main_sha} {context.owner_comment_id} {digest}\n".encode("ascii")

    def check():
        _require(tuple(_directory(publication, host, path) for path in publication.parents) == parents,
                 "LAUNCHER_PARENT_CHANGED")
        if private_key is not None:
            _require(_directory(publication, host, publication.private) == private_key, "LAUNCHER_RESERVATION_CHANGED")

    def mutate(command, data=None):
        # Authority must be the last external read. Hashing and parent checks
        # can outlast a GO. Never publish or remove a name on a stale snapshot.
        check()
        check_authority()
        _call(publication, host, command, data)

    def observed(status):
        check()
        check_authority()
        return status

    def reserve_ok():
        _file(publication, host, publication.marker, marker)

    def final():
        return _file(publication, host, publication.target, source, mode=0o555)

    preexisting = _exists(publication, host, publication.target)
    if not _exists(publication, host, publication.private):
        if preexisting:
            previous = final()
            _require(not _exists(publication, host, publication.private) and final() == previous, "LAUNCHER_FINAL_DRIFT")
            return observed("ALREADY_PRESENT_VERIFIED")
        mutate(publication.mkdir)
        private_key = _directory(publication, host, publication.private)
        mutate(_write_cmd(publication.marker), marker)
        _call(publication, host, SUDO + "sync -- " + publication.private)
        _call(publication, host, SUDO + "sync -- " + publication.parents[-1])
    else:
        private_key = _directory(publication, host, publication.private)
    _require(private_key[0] == parents[-1][0], "LAUNCHER_FILESYSTEM_MISMATCH")
    if preexisting and not _exists(publication, host, publication.marker):
        # The preceding run may have lost the unlink reply. A lone directory
        # has no owner receipt. Report the proven launcher and retain evidence.
        previous = final()
        _require(not _exists(publication, host, publication.marker) and not _exists(publication, host, publication.stage) and
                 final() == previous, "LAUNCHER_UNATTRIBUTED_PARTIAL")
        return observed("ALREADY_PRESENT_RESERVATION_RETAINED")
    reserve_ok()

    expected_key = None
    if not preexisting:
        if not _exists(publication, host, publication.stage):
            reserve_ok()
            mutate(_write_cmd(publication.stage), source)
        reserve_ok()
        stage = _stat(publication, host, publication.stage)
        if stat.S_IMODE(stage.mode) != 0o555:
            _file(publication, host, publication.stage, source)
            mutate(publication.chmod)
        reserve_ok()
        staged = _file(publication, host, publication.stage, source, mode=0o555)
        _require(staged.dev == private_key[0], "LAUNCHER_FILESYSTEM_MISMATCH")
        expected_key = staged.key
        mutate(publication.link)

    if _exists(publication, host, publication.stage):
        reserve_ok()
        staged = _file(publication, host, publication.stage, source, links=(2,), mode=0o555)
        linked = _file(publication, host, publication.target, source, links=(2,), mode=0o555)
        _require(staged == linked and staged.dev == private_key[0] and
                 (expected_key is None or linked.key == expected_key), "LAUNCHER_LINK_MISMATCH")
        mutate(publication.unstage)
        expected_key = linked.key
    else:
        _require(preexisting, "LAUNCHER_LINK_STATE_UNVERIFIED")
        expected_key = final().key

    _call(publication, host, SUDO + "sync -- " + publication.target)
    _call(publication, host, SUDO + "sync -- " + publication.parents[-1])
    reserve_ok()
    _require(not _exists(publication, host, publication.stage) and final().key == expected_key, "LAUNCHER_FINAL_DRIFT")
    mutate(publication.unmark)
    _require(final().key == expected_key, "LAUNCHER_FINAL_DRIFT")
    mutate(publication.rmdir)  # Never remove unexpected extra contents.
    private_key = None
    _call(publication, host, SUDO + "sync -- " + publication.parents[-1])
    _require(not _exists(publication, host, publication.private) and final().key == expected_key, "LAUNCHER_FINAL_DRIFT")
    return observed("ALREADY_PRESENT_VERIFIED" if preexisting else "PUBLISHED")


def install_launcher(host, context, source, check_authority):
    return _publish(LAUNCHER, host, context, source, check_authority)


def _custodian_bytes():
    from scripts.rc6_genesis_custodian_package import build_custodian
    source = build_custodian(Path(__file__).resolve().parents[1])
    _require(type(source) is bytes and 0 < len(source) <= 1024 * 1024
             and hashlib.sha256(source).hexdigest() == authority.CUSTODIAN_SHA256,
             "CUSTODIAN_PACKAGE_PIN_UNVERIFIED")
    return source


def _prepare_custodian(env):
    _require(env.get("BOOTSTRAP_ACTION") == "install_genesis_custodian"
             and env.get("RC6_R295_OWNER_COMMENT_ID") == "PREPARE"
             and re.fullmatch(r"[a-f0-9]{40}", env.get("GITHUB_SHA", "")), "CUSTODIAN_PREPARE_CONTEXT")
    _custodian_bytes()
    fingerprint = authority.host_identity_sha256(env)
    print("RC6_R367_HOST_IDENTITY_SHA256=" + fingerprint)
    print("RC6_R367_CUSTODIAN_SHA256=" + authority.CUSTODIAN_SHA256)
    print("GITHUB_SHA=" + env["GITHUB_SHA"])


def main(argv=None, *, env=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    label = "RC6_R362_LAUNCHER"
    try:
        if argv == ["--prepare-custodian"]:
            label = "RC6_R367_CUSTODIAN_PREPARE"
            _prepare_custodian(env)
            return 0
        if argv == ["--install-approved-custodian"]:
            label = "RC6_R367_CUSTODIAN"
            publication, transport = CUSTODIAN, CustodianSSHHost
            operation, digest = "install_genesis_custodian", authority.CUSTODIAN_SHA256
        else:
            _require(argv == ["--install-approved-launcher"], "FIXED_LAUNCHER_VERB_ONLY")
            publication, transport = LAUNCHER, LauncherSSHHost
            operation, digest = "install_genesis_launcher", authority.LAUNCHER_SHA256
        _require(env.get("BOOTSTRAP_ACTION") == operation, "FIXED_LAUNCHER_VERB_ONLY")
        context = legacy.validate_context(env)
        _require(context.user == "porotaadmin", "LAUNCHER_LOGIN_MISMATCH")

        def recheck():
            authority.verify_authority(env)

        recheck()
        if publication is CUSTODIAN:
            source = _custodian_bytes()
        else:
            source = legacy._read_reviewed_file(SOURCE, 128 * 1024)
            _require(hashlib.sha256(source).hexdigest() == digest, "LAUNCHER_SOURCE_SHA_MISMATCH")
        with transport(context, env) as host:
            result = _publish(publication, host, context, source, recheck)
        print(f"{label}={result}|sha256={digest}|host_control_verified=false|deploy_authorized=false")
        return 0
    except legacy.BootstrapHold as exc:
        reason = exc.reason
    except Exception:
        reason = "UNVERIFIED"
    print(f"{label}=HOLD79|reason={reason}|host_control_verified=false|deploy_authorized=false")
    return 79


if __name__ == "__main__":
    raise SystemExit(main())

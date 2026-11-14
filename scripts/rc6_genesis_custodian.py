"""Private ENV custody inside the installed R367 zipapp. Only host_main handles the wire."""
from contextlib import contextmanager, ExitStack
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys

from scripts import rc6_oci_host_helper as r271
from scripts import rc6_r334_root_env_genesis as r334
from scripts import rc6_r337_root_env_seal as r337


_FS_ROOT = "/"
_ROOT_UID = _ROOT_GID = 0
_VAULT = r337.GENERATIONS_DIRECTORY.rsplit("/", 1)[0]
_PROPOSALS = _VAULT + "/env-proposals"
_ROLES = ("dashboard", "observer")
_SEALER = "5f0d41135c254b6d69bd1265a06ba1717053e027363c9e7c82d1bacdea610052"
_PRIVATE_LIMIT = 1024 * 1024


class CustodianHold(RuntimeError):
    exit_code = 79


def _require(condition, reason):
    if not condition:
        raise CustodianHold(reason)


def _now():
    return datetime.now(timezone.utc)


def _authorized(check):
    _require(callable(check), "AUTHORITY")
    result = check()
    _require(result is None or result is True, "AUTHORITY")


def _json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _mac(secret, label, value):
    return hmac.new(secret, b"RC6-R367\0" + label.encode("ascii") + b"\0" + _json(value),
                    hashlib.sha256).hexdigest()


def _binding(value):
    shas = ("trusted_main_sha", "candidate_sha", "candidate_tree", "host_identity_sha256")
    runs = ("run_id", "run_attempt", "prepare_go_id")
    _require(type(value) is dict and set(value) == set(shas + runs) | {"prepare_nonce"}
             and r337._hex(value.get("prepare_nonce"), 32), "BINDING")
    _require(all(r337._hex(value[key], 64 if key == shas[-1] else 40) for key in shas), "BINDING")
    _require(all(type(value[key]) in (str, int) and re.fullmatch("[1-9][0-9]{0,19}", str(value[key]))
                 for key in runs), "BINDING")
    return {**value, **{key: str(value[key]) for key in runs}}


def _daemon_id():
    command = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock", "info", "--format", "{{.ID}}")
    env = {"HOME": "/root", "PATH": "/usr/bin:/bin", "LC_ALL": "C",
           "DOCKER_CONFIG": "/root/.docker", "DOCKER_CLI_HINTS": "false"}
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, timeout=12, check=False)
        _require(result.returncode == 0 and type(result.stdout) is bytes and
                 re.fullmatch(rb"[A-Za-z0-9:_./-]{6,128}\n?", result.stdout), "DAEMON_ID")
        return result.stdout.rstrip(b"\n").decode("ascii")
    except (OSError, subprocess.SubprocessError):
        raise CustodianHold("DAEMON_ID") from None


def _directory_custody(path, info):
    owner = info.st_uid == _ROOT_UID and info.st_gid == _ROOT_GID
    if path == "/opt/porota-trading" and info.st_uid == 1000:
        owner = True
    _require(owner and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022, "DIRECTORY_CUSTODY")


def _dir_pin(info):
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode]


def _leaf_pin(info):
    return _dir_pin(info) + [info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


@contextmanager
def _directory(path):
    """Open a fixed path component by component, preserving every ancestor identity."""
    with ExitStack() as handles:
        fd = os.open(_FS_ROOT, r337._flags(True))
        handles.callback(os.close, fd)
        _directory_custody("/", os.fstat(fd))
        pins = [_dir_pin(os.fstat(fd))]
        prefix = ""
        for name in path.lstrip("/").split("/"):
            prefix += "/" + name
            fd = os.open(name, r337._flags(True), dir_fd=fd)
            handles.callback(os.close, fd)
            info = os.fstat(fd)
            _directory_custody(prefix, info)
            pins.append(_dir_pin(info))
        yield fd, pins


def _check_directory(path, pins):
    with _directory(path) as (_, current):
        _require(current == pins, "DIRECTORY_CHANGED")


def _read_leaf(fd, directory, name, *, mode, maximum):
    before = os.fstat(fd)
    _require(stat.S_ISREG(before.st_mode) and before.st_uid == _ROOT_UID and before.st_gid == _ROOT_GID
             and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == mode
             and 0 < before.st_size <= maximum, "LEAF_CUSTODY")
    raw = os.pread(fd, maximum + 1, 0)
    named = os.stat(name, dir_fd=directory, follow_symlinks=False)
    pin = _leaf_pin(before)
    _require(len(raw) == before.st_size and pin == _leaf_pin(os.fstat(fd)) == _leaf_pin(named),
             "LEAF_CHANGED")
    return raw, pin


def _leaf(directory, name, *, mode=0o400, maximum=_PRIVATE_LIMIT):
    fd = os.open(name, r337._flags(), dir_fd=directory)
    try:
        return _read_leaf(fd, directory, name, mode=mode, maximum=maximum)
    finally:
        os.close(fd)


class _Sources:
    def __init__(self, handles):
        self.directory, ancestors = handles.enter_context(_directory(r337.LEGACY_DIRECTORY))
        self.fds = {}
        for role, name in r337._LEGACY_NAMES.items():
            fd = os.open(name, r337._flags(), dir_fd=self.directory)
            handles.callback(os.close, fd)
            self.fds[role] = fd
        inputs, pins = self._read()
        self.source_digests = {role: _digest(inputs[role]) for role in _ROLES}
        self.pair = r334.propose_env_pair(dashboard_source=inputs["dashboard"], observer_source=inputs["observer"],
                                          dashboard_source_sha256=self.source_digests["dashboard"],
                                          observer_source_sha256=self.source_digests["observer"])
        self.output_digests = {role: getattr(self.pair, role).sha256 for role in _ROLES}
        self.identity = dict(ancestors=ancestors, leaves=pins, sources=self.source_digests,
                             outputs=self.output_digests)
        self.verify()

    def _read(self):
        inputs, pins = {}, {}
        for role, name in r337._LEGACY_NAMES.items():
            inputs[role], pins[role] = _read_leaf(self.fds[role], self.directory, name,
                                                  mode=0o600, maximum=65536)
        return inputs, pins

    def verify(self):
        _check_directory(r337.LEGACY_DIRECTORY, self.identity["ancestors"])
        inputs, pins = self._read()
        _require(pins == self.identity["leaves"] and
                 all(_digest(inputs[role]) == self.source_digests[role] for role in _ROLES),
                 "SOURCE_CHANGED")
        _check_directory(r337.LEGACY_DIRECTORY, self.identity["ancestors"])


def _mkdir(parent, name):
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _directory_custody("", info)
    _require(stat.S_IMODE(info.st_mode) == 0o700, "PRIVATE_DIRECTORY_MODE")
    os.fsync(parent)


def _exists(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _write_record(parent, name, doc):
    """Publish a complete 0400 record without replacing an earlier operation."""
    raw = _json(doc)
    _require(0 < len(raw) <= _PRIVATE_LIMIT, "RECORD_SIZE")
    temporary = ".record-" + secrets.token_hex(16)
    r337._write_exclusive(parent, temporary, raw)
    try:
        r337._rename_no_replace(parent, temporary, name)
        os.fsync(parent)
    except FileExistsError:
        os.unlink(temporary, dir_fd=parent)
        raise CustodianHold("RECORD_EXISTS") from None


def _record(parent, name):
    raw, _ = _leaf(parent, name)
    try:
        doc = json.loads(raw)
        _require(type(doc) is dict and raw == _json(doc), "PRIVATE_RECORD")
        return doc
    except (ValueError, UnicodeError, RecursionError):
        raise CustodianHold("PRIVATE_RECORD") from None


@contextmanager
def _session(binding, *, create, check_authority):
    """One exclusive operation per original PREPARE run, including retries."""
    binding = _binding(binding)
    _authorized(check_authority)
    try:
        with ExitStack() as handles:
            sources = _Sources(handles)
            daemon = _daemon_id()
            vault, _ = handles.enter_context(_directory(_VAULT))
            name = _digest(_json([binding["prepare_go_id"], binding["prepare_nonce"]]))
            if create:
                _authorized(check_authority)
                sources.verify()
                _mkdir(vault, "env-proposals")
            proposals, _ = handles.enter_context(_directory(_PROPOSALS))
            if create:
                _mkdir(proposals, name)
            path = _PROPOSALS + "/" + name
            fd, pins = handles.enter_context(_directory(path))
            _require(all(stat.S_IMODE(info[-1]) == 0o700 for info in pins[-2:]), "PRIVATE_DIRECTORY_MODE")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if create and not _exists(fd, "proposal.json"):
                _authorized(check_authority)
                _check_directory(path, pins)
                sources.verify()
                body = dict(binding=binding, proposal_directory=pins, source_identity=sources.identity, daemon_id=daemon,
                            proposal_id=secrets.token_hex(16), generation=secrets.token_hex(16))
                key = secrets.token_bytes(32)
                _write_record(fd, "proposal.json", {"secret": key.hex(), "body": body})
            record = _record(fd, "proposal.json")
            body, key_hex = record.get("body"), record.get("secret")
            _require(r337._hex(key_hex, 64) and type(body) is dict and body.get("binding") == binding
                     and body.get("proposal_directory") == pins and body.get("source_identity") == sources.identity
                     and body.get("daemon_id") == daemon
                     and r337._hex(body.get("proposal_id"), 32) and r337._hex(body.get("generation"), 32),
                     "PROPOSAL_ORIGIN")
            _check_directory(path, pins)
            sources.verify()
            proposal = _Proposal(path, fd, pins, body, bytes.fromhex(key_hex), sources)
            try:
                yield proposal
            finally:
                proposal._active = False
    except (OSError, r334.GenesisHold, r337.SealHold, r271.CustodyHold):
        raise CustodianHold("PRIVATE_CUSTODY") from None


def prepare_env(binding, *, check_authority):
    with _session(binding, create=True, check_authority=check_authority) as proposal:
        return {"proposal_id": proposal._body["proposal_id"], "commitment": proposal._commitment}


@contextmanager
def open_proposal(binding, proposal_id, commitment, *, check_authority):
    _require(r337._hex(proposal_id, 32) and r337._hex(commitment, 64), "PROPOSAL_REFERENCE")
    with _session(binding, create=False, check_authority=check_authority) as proposal:
        _require(hmac.compare_digest(proposal_id, proposal._body["proposal_id"]) and
                 hmac.compare_digest(commitment, proposal._commitment), "PROPOSAL_REFERENCE")
        yield proposal


@contextmanager
def _private_child(parent, name):
    fd = os.open(name, r337._flags(True), dir_fd=parent)
    try:
        info = os.fstat(fd)
        _directory_custody("", info)
        _require(stat.S_IMODE(info.st_mode) == 0o700, "PRIVATE_DIRECTORY_MODE")
        yield fd
        _require(_dir_pin(os.stat(name, dir_fd=parent, follow_symlinks=False)) == _dir_pin(info),
                 "DIRECTORY_CHANGED")
    finally:
        os.close(fd)


def _move_generation(source_fd, target_fd, name):
    """Same-filesystem atomic no-replace from the proposal's private R337 stage."""
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    _require(rename is not None, "ATOMIC_NOREPLACE_UNAVAILABLE")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    raw_name = name.encode("ascii")
    _require(rename(source_fd, raw_name, target_fd, raw_name, 1) == 0, "GENERATION_CONFLICT")


class _Proposal:
    """Privileged local data. Never serialize this object onto the control channel."""
    def __init__(self, path, fd, pins, body, key, sources):
        self._path, self._fd, self._pins = path, fd, pins
        self._body, self._key, self._sources = body, key, sources
        self._active = True
        self._commitment = _mac(key, "proposal", body)

    @property
    def generation(self):
        return self._body["generation"]

    @property
    def generation_commitment(self):
        return _mac(self._key, "generation", [self._commitment, self.generation])

    @property
    def output_digests(self):
        return dict(self._sources.output_digests)

    @property
    def daemon_id(self):
        return self._body["daemon_id"]

    def _live(self):
        _require(self._active, "PROPOSAL_CLOSED")
        _check_directory(self._path, self._pins)
        self._sources.verify()

    def _seal(self, name, value):
        return dict(value=value, mac=_mac(self._key, name, [self._commitment, value]))

    def _read(self, name):
        self._live()
        _require(_exists(self._fd, name), "PRIVATE_STEP_MISSING")
        doc = _record(self._fd, name)
        _require("value" in doc and doc == self._seal(name, doc["value"]), "PRIVATE_STEP_CHANGED")
        return doc["value"]

    def _freeze(self, name, value):
        self._live()
        if _exists(self._fd, name):
            _require(self._read(name) == value, "PRIVATE_STEP_CONFLICT")
        else:
            _write_record(self._fd, name, self._seal(name, value))

    def freeze_projection(self, value, *, check_authority):
        _authorized(check_authority)
        _require(type(value) is dict, "PROJECTION")
        self._freeze("projection.json", value)

    def read_projection(self):
        return self._read("projection.json")

    def _intent(self, window):
        doc = dict(schema=1, action="SEAL_ENV_PAIR", candidate_sha=self._body["binding"]["candidate_sha"],
                   candidate_tree=self._body["binding"]["candidate_tree"], generation=self.generation,
                   normalizer_sha256=r337._R334_SHA256, sealer_sha256=_SEALER,
                   source_sha256=self._sources.source_digests, output_sha256=self.output_digests,
                   paper_mode="PRODUCTION_PAPER", real_orders_allowed=False, **window)
        raw = _json(doc)
        try:
            return r337._validate_intent(raw, intent_sha256=_digest(raw), candidate_sha=doc["candidate_sha"],
                                         candidate_tree=doc["candidate_tree"], nonce=doc["nonce"],
                                         sealer_sha256=_SEALER, now=_now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        except r337.SealHold:
            raise CustodianHold("ENV_INTENT") from None

    def _generation_pin(self, parent, nonce):
        with _private_child(parent, self.generation) as gen:
            _require(set(os.listdir(gen)) == {"env", "receipt.json"}, "GENERATION_SHAPE")
            with _private_child(gen, "env") as env:
                _require(set(os.listdir(env)) == {role + ".env" for role in _ROLES}, "GENERATION_SHAPE")
                leaves = {}
                for role in _ROLES:
                    raw, leaves[role] = _leaf(env, role + ".env", maximum=65536)
                    _require(_digest(raw) == self.output_digests[role], "GENERATION_BYTES")
                receipt, receipt_pin = _leaf(gen, "receipt.json")
                expected = dict(schema=1, candidate_sha=self._body["binding"]["candidate_sha"],
                                candidate_tree=self._body["binding"]["candidate_tree"], generation=self.generation,
                                nonce=nonce, host_go=False, r271_semantics="NO_VERIFICADO",
                                source_sha256=[self._sources.source_digests[role] for role in _ROLES],
                                output_sha256=[self.output_digests[role] for role in _ROLES])
                _require(receipt == _json(expected), "GENERATION_RECEIPT")
                return dict(generation=_dir_pin(os.fstat(gen)), env=_dir_pin(os.fstat(env)),
                            receipt=receipt_pin, leaves=leaves)

    def publish_env(self, *, nonce, issued_at, expires_at, check_authority):
        """Claim one exact ENV operation and record its inode before final publication."""
        window = dict(nonce=nonce, issued_at=issued_at, expires_at=expires_at)
        self.read_projection()
        intent = self._intent(window)
        _authorized(check_authority)
        self._freeze("env-use.json", window)
        with _directory(_VAULT) as (vault, _):
            _mkdir(vault, "generations")
        with _directory(r337.GENERATIONS_DIRECTORY) as (target, target_pins):
            _require(stat.S_IMODE(os.fstat(target).st_mode) == 0o700, "PRIVATE_DIRECTORY_MODE")
            if not _exists(self._fd, "env-pin.json"):
                _require(not _exists(target, self.generation), "FOREIGN_GENERATION")
                # A killed R337 may leave a partial private directory. A fresh
                # stage preserves it and never consumes an unrecorded inode.
                stage_name = ".stage-" + secrets.token_hex(16)
                os.mkdir(stage_name, 0o700, dir_fd=self._fd)
                with _private_child(self._fd, stage_name) as stage:
                    pair = self._sources.pair
                    r337._stage_pair(stage, intent, pair.dashboard.payload, pair.observer.payload)
                    pin = self._generation_pin(stage, nonce)
                    self._freeze("env-pin.json", dict(target=target_pins, generation=pin,
                                                       stage=stage_name, stage_pin=_dir_pin(os.fstat(stage))))
            saved = self._read("env-pin.json")
            _require(saved["target"] == target_pins, "GENERATION_PARENT_CHANGED")
            _require(re.fullmatch(r"\.stage-[0-9a-f]{32}", saved["stage"]), "PRIVATE_STAGE")
            with _private_child(self._fd, saved["stage"]) as stage:
                _require(_dir_pin(os.fstat(stage)) == saved["stage_pin"], "STAGE_CHANGED")
                _authorized(check_authority)
                self._intent(window)
                self._live()
                _check_directory(r337.GENERATIONS_DIRECTORY, target_pins)
                if not _exists(target, self.generation):
                    _require(self._generation_pin(stage, nonce) == saved["generation"], "STAGE_CHANGED")
                    _move_generation(stage, target, self.generation)
                os.fsync(stage)
                os.fsync(target)
        self.verify_env()

    def verify_env(self):
        """Recheck original identities, the already published inode and both R271 closures."""
        saved = self._read("env-pin.json")
        nonce = self._read("env-use.json")["nonce"]
        with _directory(r337.GENERATIONS_DIRECTORY) as (target, pins):
            _require(pins == saved["target"] and self._generation_pin(target, nonce) == saved["generation"],
                     "ENV_PIN_CHANGED")
            for role in _ROLES:
                path = r337.GENERATIONS_DIRECTORY + "/" + self.generation + "/env/" + role + ".env"
                r271._verify_root_sealed_env(path, self.output_digests[role])
            _require(self._generation_pin(target, nonce) == saved["generation"], "ENV_PIN_CHANGED")
            _check_directory(r337.GENERATIONS_DIRECTORY, pins)
        self._live()


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 1 or args[0] not in {"prepare-env", "apply"}:
        return 79
    from scripts.rc6_genesis_host_transaction import host_main
    return host_main(args[0])

"""R367 private host transaction. All files and ENV values are synthetic."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import rc6_genesis_custodian as c


BINDING = dict(trusted_main_sha="1" * 40, candidate_sha="2" * 40,
               candidate_tree="3" * 40, host_identity_sha256="4" * 64,
               run_id="123", run_attempt="1", prepare_go_id="456", prepare_nonce="5" * 32)
SOURCE = {"dashboard": b"ENVIRONMENT=PRODUCTION\nDASHBOARD_PASSWORD=only-a-fixture\n",
          "observer": b"PAPER_ACTIVE_SYMBOL_LIMIT=5\n"}
NOW = datetime(2026, 11, 14, 19, 40, tzinfo=timezone.utc)
WINDOW = dict(nonce="a" * 32, issued_at="2026-11-14T19:39:40Z",
              expires_at="2026-11-14T19:42:00Z")


def grant():
    return None


def denied():
    raise c.CustodianHold("AUTHORITY")


@pytest.fixture
def fs(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "_FS_ROOT", str(tmp_path))
    monkeypatch.setattr(c, "_ROOT_UID", os.geteuid())
    monkeypatch.setattr(c, "_ROOT_GID", os.getegid())
    monkeypatch.setattr(c.r337, "_ROOT_UID", os.geteuid())
    monkeypatch.setattr(c, "_daemon_id", lambda: "private-rootful-daemon")
    monkeypatch.setattr(c, "_now", lambda: NOW)
    legacy = tmp_path / c.r337.LEGACY_DIRECTORY.lstrip("/")
    vault = tmp_path / c.r337.GENERATIONS_DIRECTORY.lstrip("/").rsplit("/", 1)[0]
    legacy.mkdir(parents=True)
    vault.mkdir(parents=True)
    vault.chmod(0o700)
    for role, raw in SOURCE.items():
        leaf = legacy / c.r337._LEGACY_NAMES[role]
        leaf.write_bytes(raw)
        leaf.chmod(0o600)
    # R271 validates its literal production path and the real R334 output. Only
    # map the final protected read onto our temporary root for unprivileged CI.
    def sealed_read(path, *, expected_mode):
        leaf = tmp_path / path.lstrip("/")
        assert stat.S_IMODE(leaf.lstat().st_mode) == expected_mode == 0o400
        return leaf.read_bytes()
    monkeypatch.setattr(c.r271, "_read_root_owned", sealed_read)
    return SimpleNamespace(root=tmp_path, legacy=legacy, vault=vault)


def prepare(binding=BINDING):
    return c.prepare_env(binding, check_authority=grant)


def reopen(ref, binding=BINDING):
    return c.open_proposal(binding, **ref, check_authority=grant)


def test_private_commitment_and_retry_stay_with_original_run(fs, capsys):
    with pytest.raises(c.CustodianHold):
        c.prepare_env(BINDING, check_authority=denied)
    assert list(fs.vault.iterdir()) == []
    a = prepare()
    assert a == prepare() and set(a) == {"proposal_id", "commitment"}
    assert len(bytes.fromhex(a["proposal_id"])) == 16
    assert len(bytes.fromhex(a["commitment"])) == 32
    with pytest.raises(c.CustodianHold):
        prepare(dict(BINDING, run_id="124"))
    b = prepare(dict(BINDING, run_id="124", prepare_go_id="457", prepare_nonce="6" * 32))
    assert a["proposal_id"] != b["proposal_id"] and a["commitment"] != b["commitment"]
    assert not (fs.vault / "generations").exists()
    with reopen(a) as proposal:
        public = repr(a) + repr(proposal) + repr(proposal.generation_commitment)
        assert proposal.generation != a["proposal_id"]
        for role in SOURCE:
            assert hashlib.sha256(SOURCE[role]).hexdigest() not in public
            assert proposal.output_digests[role] not in public
        assert proposal.generation not in public and proposal.daemon_id not in public
        assert "only-a-fixture" not in public
        with pytest.raises(c.CustodianHold):
            with reopen(a):
                pass
    for item in fs.vault.rglob("*"):
        assert stat.S_IMODE(item.lstat().st_mode) == (0o700 if item.is_dir() else 0o400)
        if item.is_file():
            assert b"only-a-fixture" not in item.read_bytes()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("change", ["bytes", "same_bytes_inode", "ancestor", "daemon",
                                    "commitment", "binding", "alias", "mode"])
def test_origin_bytes_inodes_and_binding_must_match(fs, monkeypatch, change):
    ref = prepare()
    binding = dict(BINDING)
    leaf = fs.legacy / c.r337._LEGACY_NAMES["dashboard"]
    if change == "bytes":
        leaf.write_bytes(leaf.read_bytes() + b"PAPER_FOCUS_SYMBOLS=fixture\n")
    elif change == "same_bytes_inode":
        other = leaf.with_suffix(".new")
        other.write_bytes(leaf.read_bytes())
        other.chmod(0o600)
        other.replace(leaf)
    elif change == "ancestor":
        original = fs.legacy.parent
        hidden = original.with_name("old-data")
        original.rename(hidden)
        original.mkdir()
        (hidden / fs.legacy.name).rename(fs.legacy)
    elif change == "daemon":
        monkeypatch.setattr(c, "_daemon_id", lambda: "different-daemon")
    elif change == "commitment":
        ref["commitment"] = "f" * 64
    elif change == "binding":
        binding["candidate_tree"] = "9" * 40
    elif change == "alias":
        os.link(leaf, leaf.with_suffix(".alias"))
    else:
        leaf.chmod(0o640)
    with pytest.raises(c.CustodianHold):
        with reopen(ref, binding):
            pass
    assert not (fs.vault / "generations").exists()


def test_pair_rechecked_after_transform_and_symlink_rejected(fs, monkeypatch):
    original = c.r334.propose_env_pair
    leaf = fs.legacy / c.r337._LEGACY_NAMES["dashboard"]
    def swapped(**kwargs):
        result = original(**kwargs)
        peer = leaf.with_name("replacement")
        peer.write_bytes(leaf.read_bytes())
        peer.chmod(0o600)
        peer.replace(leaf)
        return result
    monkeypatch.setattr(c.r334, "propose_env_pair", swapped)
    with pytest.raises(c.CustodianHold):
        prepare()
    assert not list(fs.vault.iterdir())
    monkeypatch.setattr(c.r334, "propose_env_pair", original)
    original_legacy = fs.legacy.with_name("untrusted")
    fs.legacy.rename(original_legacy)
    fs.legacy.symlink_to(original_legacy, target_is_directory=True)
    with pytest.raises(c.CustodianHold):
        prepare()


def test_only_fixed_legacy_ancestor_accepts_uid1000(monkeypatch):
    monkeypatch.setattr(c, "_ROOT_UID", 0)
    monkeypatch.setattr(c, "_ROOT_GID", 0)
    legacy = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000, st_gid=1000)
    c._directory_custody("/opt/porota-trading", legacy)
    for path in ("/opt", "/opt/porota-trading/data", c.r337.LEGACY_DIRECTORY, "/var/lib"):
        with pytest.raises(c.CustodianHold):
            c._directory_custody(path, legacy)
    legacy.st_mode |= 0o020
    with pytest.raises(c.CustodianHold):
        c._directory_custody("/opt/porota-trading", legacy)


def test_private_projection_is_immutable_and_verified_after_reopen(fs):
    ref = prepare()
    value = dict(root_stage="/synthetic/private", roles=["dashboard", "observer"])
    with reopen(ref) as proposal:
        with pytest.raises(c.CustodianHold):
            proposal.read_projection()
        with pytest.raises(c.CustodianHold):
            proposal.freeze_projection(value, check_authority=denied)
        proposal.freeze_projection(value, check_authority=grant)
        proposal.freeze_projection(value, check_authority=grant)
    with reopen(ref) as proposal:
        assert proposal.read_projection() == value
        with pytest.raises(c.CustodianHold):
            proposal.freeze_projection(dict(value, root_stage="foreign"), check_authority=grant)
    assert "/synthetic/private" not in repr(ref)


def test_r337_seal_r271_acceptance_and_consumption(fs, monkeypatch):
    ref = prepare()
    verifier = Mock(wraps=c.r271._verify_root_sealed_env)
    monkeypatch.setattr(c.r271, "_verify_root_sealed_env", verifier)
    with reopen(ref) as proposal:
        proposal.freeze_projection({"reviewed": True}, check_authority=grant)
        with pytest.raises(c.CustodianHold):
            proposal.publish_env(**WINDOW, check_authority=denied)
        assert not (fs.vault / "generations").exists()
        proposal.publish_env(**WINDOW, check_authority=grant)
        proposal.verify_env()
        gen = fs.vault / "generations" / proposal.generation
        published = {p: p.lstat().st_ino for p in gen.rglob("*")}
        assert set(proposal.output_digests) == {"dashboard", "observer"}
        assert {p.name for p in (gen / "env").iterdir()} == {"dashboard.env", "observer.env"}
        assert not json.loads((gen / "receipt.json").read_bytes())["host_go"]
    with reopen(ref) as proposal:
        proposal.publish_env(**WINDOW, check_authority=grant)
        proposal.verify_env()
        assert all(p.lstat().st_ino == ino for p, ino in published.items())
        with pytest.raises(c.CustodianHold):
            proposal.publish_env(**dict(WINDOW, nonce="b" * 32), check_authority=grant)
    assert verifier.call_count >= 4


def test_foreign_equal_generation_is_never_adopted(fs):
    ref = prepare()
    with reopen(ref) as proposal:
        proposal.freeze_projection({"reviewed": True}, check_authority=grant)
        gen = fs.vault / "generations" / proposal.generation
        gen.mkdir(parents=True, mode=0o700)
        (gen / "foreign").write_text("preserve")
        with pytest.raises(c.CustodianHold):
            proposal.publish_env(**WINDOW, check_authority=grant)
        assert (gen / "foreign").read_text() == "preserve"


def test_expired_grant_and_failures_never_validate_partial_generation(fs, monkeypatch):
    ref = prepare()
    with reopen(ref) as proposal:
        proposal.freeze_projection({"reviewed": True}, check_authority=grant)
        monkeypatch.setattr(c, "_now", lambda: NOW + timedelta(minutes=10))
        with pytest.raises(c.CustodianHold):
            proposal.publish_env(**WINDOW, check_authority=grant)
        assert not (fs.vault / "generations").exists()
        monkeypatch.setattr(c, "_now", lambda: NOW)
        with pytest.raises(c.CustodianHold):
            proposal.verify_env()
        proposal.publish_env(**WINDOW, check_authority=grant)
        leaf = fs.vault / "generations" / proposal.generation / "env" / "dashboard.env"
        old = leaf.with_name("foreign-copy")
        old.write_bytes(leaf.read_bytes())
        old.chmod(0o400)
        old.replace(leaf)
        with pytest.raises(c.CustodianHold):
            proposal.verify_env()


def test_daemon_lookup_fixed_rootful_endpoint_and_clean_environment(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"fixture-daemon\n"))
    monkeypatch.setattr(c.subprocess, "run", run)
    monkeypatch.setenv("DOCKER_HOST", "unix:///private-wrong")
    monkeypatch.setenv("DOCKER_CONTEXT", "unrelated")
    assert c._daemon_id() == "fixture-daemon"
    args, kwargs = run.call_args
    assert list(args[0]) == ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock",
                             "info", "--format", "{{.ID}}"]
    assert kwargs["env"] == {"HOME": "/root", "PATH": "/usr/bin:/bin", "LC_ALL": "C",
                             "DOCKER_CONFIG": "/root/.docker", "DOCKER_CLI_HINTS": "false"}
    assert kwargs["stderr"] == c.subprocess.DEVNULL and kwargs["timeout"] <= 20


def test_interrupted_after_rename_resumes_exact_authenticated_inode(fs, monkeypatch):
    ref = prepare()
    original_move = c._move_generation
    state = {}
    def interrupted(source_fd, target_fd, generation):
        original_move(source_fd, target_fd, generation)
        state["generation"] = generation
        state["inode"] = os.stat(generation, dir_fd=target_fd, follow_symlinks=False).st_ino
        raise OSError("synthetic loss after publication")
    monkeypatch.setattr(c, "_move_generation", interrupted)
    with pytest.raises(c.CustodianHold):
        with reopen(ref) as proposal:
            proposal.freeze_projection({"reviewed": True}, check_authority=grant)
            proposal.publish_env(**WINDOW, check_authority=grant)
    gen = fs.vault / "generations" / state["generation"]
    assert gen.lstat().st_ino == state["inode"]
    require_no_repeat = Mock(side_effect=AssertionError("must resume already published inode"))
    monkeypatch.setattr(c, "_move_generation", require_no_repeat)
    with reopen(ref) as proposal:
        proposal.publish_env(**WINDOW, check_authority=grant)
        proposal.verify_env()
    require_no_repeat.assert_not_called()


def test_drift_during_final_authority_check_never_exposes_generation(fs):
    ref = prepare()
    leaf = fs.legacy / c.r337._LEGACY_NAMES["dashboard"]
    calls = 0
    def change_on_final_check():
        nonlocal calls
        calls += 1
        if calls == 2:
            leaf.write_bytes(leaf.read_bytes() + b"PAPER_FOCUS_SYMBOLS=fixture\n")
    with reopen(ref) as proposal:
        proposal.freeze_projection({"reviewed": True}, check_authority=grant)
        with pytest.raises(c.CustodianHold):
            proposal.publish_env(**WINDOW, check_authority=change_on_final_check)
        assert not (fs.vault / "generations" / proposal.generation).exists()


def test_protection_survives_replacing_sealed_record_and_closed_handles(fs):
    ref = prepare()
    with reopen(ref) as proposal:
        proposal.freeze_projection({"reviewed": True}, check_authority=grant)
        leaf = next(fs.vault.rglob("projection.json"))
        record = json.loads(leaf.read_bytes())
        record["value"]["reviewed"] = False
        leaf.chmod(0o600)
        leaf.write_bytes(c._json(record))
        leaf.chmod(0o400)
        with pytest.raises(c.CustodianHold):
            proposal.read_projection()
    with pytest.raises(c.CustodianHold):
        proposal.read_projection()


def test_explicit_authority_signal_rejects_unrecognized_return(fs):
    with pytest.raises(c.CustodianHold):
        c.prepare_env(BINDING, check_authority=lambda: "DENIED")
    assert not list(fs.vault.iterdir())


def test_well_known_cli_only_delegates_to_the_installed_host_driver(monkeypatch):
    import sys
    import types
    module = types.ModuleType("scripts.rc6_genesis_host_transaction")
    module.host_main = Mock(return_value=17)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    for argv in ([], ["stage"], ["apply", "true"], ["prepare-env", "/tmp/anything"]):
        assert c.main(argv) == 79
    for verb in ("prepare-env", "apply"):
        assert c.main([verb]) == 17
    assert [call.args for call in module.host_main.call_args_list] == [("prepare-env",), ("apply",)]


@pytest.mark.parametrize("what", ["private_receipt_rename", "private_receipt_fsync", "generation_fsync"])
def test_resumes_after_private_and_final_publication_interruptions(fs, monkeypatch, what):
    ref = prepare()
    real_rename = c.r337._rename_no_replace
    real_sync = os.fsync
    tripped = False
    parent_ino = None
    def replace(parent, old, new):
        nonlocal parent_ino, tripped
        real_rename(parent, old, new)
        if new == "env-pin.json":
            parent_ino = os.fstat(parent).st_ino
            if what == "private_receipt_rename":
                tripped = True
                raise OSError("synthetic")
    def sync(fd):
        nonlocal tripped
        real_sync(fd)
        info = os.fstat(fd)
        if what == "private_receipt_fsync" and info.st_ino == parent_ino:
            tripped = True
            raise OSError("synthetic")
        if what == "generation_fsync" and stat.S_ISDIR(info.st_mode):
            visible = fs.vault / "generations"
            if visible.is_dir() and info.st_ino == visible.stat().st_ino and any(visible.iterdir()):
                tripped = True
                raise OSError("synthetic")
    monkeypatch.setattr(c.r337, "_rename_no_replace", replace)
    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(c.CustodianHold):
        with reopen(ref) as proposal:
            proposal.freeze_projection({"reviewed": True}, check_authority=grant)
            proposal.publish_env(**WINDOW, check_authority=grant)
    assert tripped
    monkeypatch.setattr(c.r337, "_rename_no_replace", real_rename)
    monkeypatch.setattr(os, "fsync", real_sync)
    with reopen(ref) as proposal:
        proposal.publish_env(**WINDOW, check_authority=grant)
        proposal.verify_env()


def test_substituting_private_proposal_root_even_with_exact_bytes_is_rejected(fs):
    ref = prepare()
    root = next(fs.vault.rglob("proposal.json")).parent
    hidden = root.with_name("previous")
    root.rename(hidden)
    root.mkdir(mode=0o700)
    original = hidden / "proposal.json"
    new = root / "proposal.json"
    new.write_bytes(original.read_bytes())
    new.chmod(0o400)
    with pytest.raises(c.CustodianHold):
        with reopen(ref):
            pass


def test_process_death_inside_r337_retains_partial_stage_and_finishes_on_retry(fs, monkeypatch):
    import multiprocessing

    ref = prepare()
    original_write = c.r337._write_exclusive
    def killed_after_dashboard(parent, name, raw):
        original_write(parent, name, raw)
        if name == "dashboard.env":
            os._exit(7)
    def child():
        with reopen(ref) as proposal:
            proposal.freeze_projection({"reviewed": True}, check_authority=grant)
            proposal.publish_env(**WINDOW, check_authority=grant)
    monkeypatch.setattr(c.r337, "_write_exclusive", killed_after_dashboard)
    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(5)
    assert process.exitcode == 7
    partial = next(path for path in fs.vault.rglob(".r337-*") if path.is_dir())
    assert {leaf.name for leaf in (partial / "env").iterdir()} == {"dashboard.env"}
    monkeypatch.setattr(c.r337, "_write_exclusive", original_write)
    with reopen(ref) as proposal:
        proposal.publish_env(**WINDOW, check_authority=grant)
        proposal.verify_env()
    assert (partial / "env" / "dashboard.env").exists()


def test_low_entropy_source_dictionary_requires_private_32_byte_key(fs):
    import hmac

    ref = prepare()
    record = json.loads(next(fs.vault.rglob("proposal.json")).read_bytes())
    key, body = bytes.fromhex(record["secret"]), record["body"]
    assert len(key) == 32 and key.hex() not in repr(ref)
    message = b"RC6-R367\0proposal\0" + c._json(body)
    assert hmac.new(key, message, hashlib.sha256).hexdigest() == ref["commitment"]
    # Even granting the other private metadata, an attacker cannot confirm
    # the correct guess with an unkeyed commitment or any public identifier.
    for limit in (3, 4, 5, 6):
        guessed = json.loads(c._json(body))
        value = f"PAPER_ACTIVE_SYMBOL_LIMIT={limit}\n".encode()
        guessed["source_identity"]["sources"]["observer"] = hashlib.sha256(value).hexdigest()
        data = b"RC6-R367\0proposal\0" + c._json(guessed)
        assert hashlib.sha256(data).hexdigest() != ref["commitment"]
        for public_key in (bytes.fromhex(ref["proposal_id"]), bytes.fromhex(BINDING["prepare_nonce"])):
            assert hmac.new(public_key, data, hashlib.sha256).hexdigest() != ref["commitment"]
    with reopen(ref) as proposal:
        generation_message = b"RC6-R367\0generation\0" + c._json([ref["commitment"], proposal.generation])
        assert hmac.new(key, generation_message, hashlib.sha256).hexdigest() == proposal.generation_commitment
        assert proposal.generation_commitment != ref["commitment"]

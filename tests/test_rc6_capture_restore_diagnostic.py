"""Custody and failure guards for the offline profiler, without large decodes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys

import pytest

from scripts import rc6_capture_restore_diagnostic as diagnostic


def private_source(tmp_path):
    data = tmp_path/"original"
    data.mkdir(mode=0o700)
    for name in diagnostic.ROOTS:
        root = data/name
        root.mkdir(parents=True, mode=0o700)
        (root/"control.json").write_bytes(b'{"unchanged":true}\n')
    (data/diagnostic.ROOTS[0]/"projection.sqlite").write_bytes(b"binary\0source"*1024)
    return data


def test_copy_preserves_all_source_bytes_and_eleven_stats_without_control_rewrite(tmp_path):
    data = private_source(tmp_path)
    before = diagnostic.snapshot(data)
    private = tmp_path/"private"
    private.mkdir(mode=0o700)
    evidence, result = diagnostic._copy(data, before, private)
    assert diagnostic.snapshot(data) == before
    assert evidence == private/diagnostic.ROOTS[0]
    assert result["source_control_bytes_rewritten"] is False
    assert result["allocated_or_logical_bytes"] <= diagnostic.COPY_LIMIT
    for name, actual_sha in result["file_hashes"].items():
        assert actual_sha == before[name]["sha256"]
        assert diagnostic._file(private/name)["sha256"] == actual_sha
    assert set(next(iter(before.values()))["stats"]) == set(diagnostic.STATS)
    assert len(diagnostic.STATS) == 11


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "fifo"))
def test_inventory_rejects_real_alias_or_fifo_without_reading_it(tmp_path, kind):
    import os
    root = tmp_path/"root"
    root.mkdir()
    target = root/"file"
    if kind == "symlink":
        target.symlink_to(tmp_path/"absent")
    elif kind == "hardlink":
        (root/"first").write_bytes(b"original")
        os.link(root/"first", target)
    else:
        os.mkfifo(target)
    with pytest.raises(ValueError, match="DIAGNOSTIC_SOURCE_ALIAS_FORBIDDEN"):
        diagnostic.snapshot(root)


def test_copy_rejects_source_replacement_after_initial_hash_inventory(tmp_path):
    data = private_source(tmp_path)
    before = diagnostic.snapshot(data)
    source = data/diagnostic.ROOTS[0]/"projection.sqlite"
    replacement = source.with_name("replacement")
    replacement.write_bytes(source.read_bytes())
    replacement.replace(source)
    private = tmp_path/"private"
    private.mkdir()
    with pytest.raises(ValueError, match="DIAGNOSTIC_SOURCE_CHANGED"):
        diagnostic._copy(data, before, private)


def test_stat_open_fifo_replacement_is_nonblocking_and_rejected_before_read(tmp_path, monkeypatch):
    import os
    target = tmp_path/"original"
    target.write_bytes(b"original")
    original_open, original_read = os.open, os.read
    calls = {"reads":0, "replaced":False}
    def swap(path, flags, *items, **kwargs):
        if Path(path) == target and not calls["replaced"]:
            calls["replaced"] = True
            assert flags & os.O_NONBLOCK
            target.unlink()
            os.mkfifo(target)
        return original_open(path, flags, *items, **kwargs)
    def read(*items):
        calls["reads"] += 1
        return original_read(*items)
    monkeypatch.setattr(os, "open", swap)
    monkeypatch.setattr(os, "read", read)
    with pytest.raises(ValueError, match="DIAGNOSTIC_SOURCE_CHANGED"):
        diagnostic._file(target)
    assert calls == {"reads":0, "replaced":True}


def test_copy_quota_rejects_before_target_generation_is_created(tmp_path, monkeypatch):
    data = private_source(tmp_path)
    before = diagnostic.snapshot(data)
    private = tmp_path/"private"
    private.mkdir()
    monkeypatch.setattr(diagnostic, "COPY_LIMIT", 1)
    with pytest.raises(ValueError, match="DIAGNOSTIC_COPY_QUOTA_REACHED"):
        diagnostic._copy(data, before, private)
    assert diagnostic.snapshot(data) == before
    assert list(private.iterdir()) == []


@pytest.mark.parametrize("name", ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"))
def test_all_outbound_socket_entrypoints_are_blocked_and_restored(tmp_path, name):
    if not hasattr(socket.socket, name):
        pytest.fail("Required network guard unavailable")
    original = getattr(socket.socket, name)
    with diagnostic._offline(tmp_path) as attempts:
        with socket.socket() as connection:
            with pytest.raises(ValueError, match="DIAGNOSTIC_NETWORK_FORBIDDEN"):
                getattr(connection, name)(b"offline synthetic probe")
        assert attempts["network"] == 1
    assert getattr(socket.socket, name) is original


def test_dns_and_both_sqlite_connect_aliases_block_original_source_and_restore(tmp_path):
    source = tmp_path/"original"
    source.mkdir()
    original = sqlite3.connect
    dbapi_original = sqlite3.dbapi2.connect
    with diagnostic._offline(source) as attempts:
        with pytest.raises(ValueError, match="DIAGNOSTIC_NETWORK_FORBIDDEN"):
            socket.getaddrinfo("synthetic.invalid", 443)
        for connect in (sqlite3.connect, sqlite3.dbapi2.connect):
            with pytest.raises(ValueError, match="DIAGNOSTIC_SOURCE_SQLITE_FORBIDDEN"):
                connect("file:"+str(source/"db.sqlite")+"?mode=ro", uri=True)
        outside = str(tmp_path/"other"/".."/"original"/"db.sqlite")
        with pytest.raises(ValueError, match="DIAGNOSTIC_SOURCE_SQLITE_FORBIDDEN"):
            sqlite3.connect("file:"+outside+"?mode=ro", uri=True)
        connection = sqlite3.connect(":memory:")
        connection.close()
        assert attempts == {"network":1, "source_sqlite":3}
    assert sqlite3.connect is original
    assert sqlite3.dbapi2.connect is dbapi_original
    assert not (source/"db.sqlite").exists()


def test_watchdog_kills_and_reaps_stalled_diagnostic_child(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(diagnostic, "WATCHDOG_SECONDS", 0.1)
    metadata = {}
    result = diagnostic._monitor([sys.executable, "-B", "-c", "import time; time.sleep(5)"],
                                 tmp_path, tmp_path, metadata)
    assert result["watchdog_reason"] == "DIAGNOSTIC_WATCHDOG_EXHAUSTED"
    assert result["native_returncode"] < 0
    assert result["native_fatal_signal"] == "SIGKILL"
    with pytest.raises(ProcessLookupError):
        os.kill(metadata["native_pid"], 0)


def test_copy_failure_keeps_actual_reason_and_compares_original_custody(tmp_path, monkeypatch):
    data = private_source(tmp_path)
    source = tmp_path/"source"
    source.mkdir()
    raw = tmp_path/"raw"
    code = {"file.py":{"sha256":"synthetic", "mode":"100644", "git_blob":"synthetic"}}
    monkeypatch.setattr(diagnostic, "_source_inventory", lambda *items: code)
    def rejected(*items):
        raise ValueError("DIAGNOSTIC_COPY_QUOTA_REACHED")
    monkeypatch.setattr(diagnostic, "_copy", rejected)
    index = tmp_path/"index.json"
    index.write_text("{}")
    args = argparse.Namespace(source_index=index, phase="restore")
    pin = {"source_sha":diagnostic.PRODUCT_SHA, "source_tree":"synthetic", "tar_sha256":"synthetic",
           "files":{"file.py":"synthetic"}}
    before = diagnostic.snapshot(data)
    assert diagnostic._run_parent(args, source, data, raw, pin, {}, {"network":0,"source_sqlite":0}) == 1
    final = json.loads((raw/"wrapper-final.json").read_bytes())
    assert final["reason"] == "DIAGNOSTIC_COPY_QUOTA_REACHED"
    assert final["native_pid"] is None
    assert final["diagnostic_completed"] is False
    assert final["acceptance_complete"] is False
    assert final["source_data_all11stats_hashes_unchanged"] is True
    assert final["source_sha_modes_blobs_unchanged"] is True
    assert final["private_copy_all11stats_hashes_unchanged"] is None
    assert diagnostic.snapshot(data) == before


def test_product_inventory_binds_git_bytes_modes_and_namespace(tmp_path):
    path = tmp_path/"module.py"
    raw = b"value = 3\n"
    path.write_bytes(raw)
    path.chmod(0o644)
    pin = {"files":{"module.py":hashlib.sha256(raw).hexdigest()}, "modes":{"module.py":"100644"},
           "blob_ids":{"module.py":hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()}}
    original = diagnostic._source_inventory(tmp_path, pin)
    path.chmod(0o755)
    with pytest.raises(ValueError, match="DIAGNOSTIC_PRODUCT_SOURCE_MISMATCH"):
        diagnostic._source_inventory(tmp_path, pin)
    path.chmod(0o644)
    assert diagnostic._source_inventory(tmp_path, pin) == original
    (tmp_path/"unexpected.py").write_bytes(b"extra\n")
    with pytest.raises(ValueError, match="DIAGNOSTIC_PRODUCT_NAMESPACE_MISMATCH"):
        diagnostic._source_inventory(tmp_path, pin)


def test_preflight_rejects_wrong_interpreter_before_private_fixture_creation(tmp_path, monkeypatch):
    source, data, raw = tmp_path/"source", tmp_path/"original", tmp_path/"raw"
    source.mkdir()
    data.mkdir()
    index = tmp_path/"source.index.json"
    index.write_text("{}")
    def rejected(*items):
        raise ValueError("DIAGNOSTIC_FROZEN157_PREFLIGHT_REJECTED")
    monkeypatch.setattr(diagnostic, "_preflight", rejected)
    with pytest.raises(ValueError, match="DIAGNOSTIC_FROZEN157_PREFLIGHT_REJECTED"):
        diagnostic.main(["--phase","restore", "--source",str(source), "--data",str(data),
                         "--source-index",str(index), "--raw",str(raw)])
    assert not raw.exists()


def test_no_sample_control_never_arms_cancels_or_enables_a_native_watcher(tmp_path, monkeypatch):
    def forbidden(*items, **kwargs):
        pytest.fail("NONE variant touched native faulthandler")
    for name in ("dump_traceback_later", "cancel_dump_traceback_later", "enable", "disable", "register", "unregister"):
        monkeypatch.setattr(diagnostic.faulthandler, name, forbidden)
    with (tmp_path/"empty-samples.log").open("wb") as stream:
        stop = diagnostic._start_sampling(stream, "none")
        stop()
    assert (tmp_path/"empty-samples.log").stat().st_size == 0


def test_timed_sampling_arms_once_and_cancels_before_stream_close(tmp_path, monkeypatch):
    calls = []
    def arm(seconds, *, repeat, file):
        assert seconds == 10 and repeat is True and not file.closed
        calls.append("armed")
    with (tmp_path/"samples.log").open("wb") as stream:
        def cancel():
            assert not stream.closed
            calls.append("cancelled")
        monkeypatch.setattr(diagnostic.faulthandler, "dump_traceback_later", arm)
        monkeypatch.setattr(diagnostic.faulthandler, "cancel_dump_traceback_later", cancel)
        stop = diagnostic._start_sampling(stream, "timed")
        assert calls == ["armed"]
        stop()
        assert calls == ["armed", "cancelled"]


def test_unknown_sampling_mode_is_rejected_before_arming_watcher(tmp_path, monkeypatch):
    def forbidden(*items, **kwargs):
        pytest.fail("Unknown mode armed watcher")
    monkeypatch.setattr(diagnostic.faulthandler, "dump_traceback_later", forbidden)
    with (tmp_path/"samples.log").open("wb") as stream:
        with pytest.raises(ValueError, match="DIAGNOSTIC_SAMPLING_MODE_REJECTED"):
            diagnostic._start_sampling(stream, "unknown")

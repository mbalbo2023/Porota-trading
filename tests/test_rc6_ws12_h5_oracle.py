import importlib
import importlib.util
import os
import errno
import json
from pathlib import Path
import socket
import stat
import threading
import time
from types import SimpleNamespace
import pytest


def module():
    name = "scripts.rc6_ci_ws12_h5_oracle"
    assert importlib.util.find_spec(name) is not None
    return importlib.import_module(name)


def test_rejects_symlink_without_calling_transport(tmp_path):
    oracle = module()
    target = tmp_path / "real"
    target.write_text("do not open")
    alias = tmp_path / "link"
    alias.symlink_to(target)
    with pytest.raises(oracle.OracleHold):
        oracle.probe(alias, expected_event={"kind": "BOOK"})


@pytest.mark.parametrize("raw", [b'{"state":"OK","state":"OK"}\n',
                                  b'{"state":"OK"}', b'not json\n'])
def test_malformed_or_ambiguous_response_rejected(raw):
    oracle = module()
    with pytest.raises(oracle.OracleHold):
        oracle.parse_response(raw)


def test_expected_states_are_not_sufficient_to_certify():
    oracle = module()
    event = {"kind": "BOOK", "synthetic": "fixture"}
    result = oracle.assess({"state": "OK", "events": [event]},
                           {"state": "DENIED_READ_ONLY"}, expected_event=event)
    assert result["protocol_probe_pass"] is True
    assert result["af_unix_independently_verified"] is False
    assert result["qa_certified"] is False


def test_non_object_event_is_not_a_valid_data_pull():
    oracle = module()
    with pytest.raises(oracle.OracleHold):
        oracle.assess({"state": "OK", "events": [None]},
                      {"state": "DENIED_READ_ONLY"}, expected_event={"kind": "BOOK"})


def test_symlink_ancestor_is_rejected_before_connect(tmp_path, monkeypatch):
    oracle = module()
    real = tmp_path / "real"
    (real / "private").mkdir(parents=True, mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    endpoint = alias / "private" / "feed.sock"
    original = Path.lstat
    def lstat(path):
        if path == endpoint:
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.geteuid())
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    def forbidden(*args):
        raise AssertionError("must not connect through a symlink ancestor")
    monkeypatch.setattr(oracle.socket, "socket", forbidden)
    with pytest.raises(oracle.OracleHold):
        oracle.probe(endpoint, expected_event={"kind": "BOOK"})


def test_wrong_or_empty_event_is_not_protocol_success():
    oracle = module()
    for event in ({}, {"kind": "TRADE"}):
        with pytest.raises(oracle.OracleHold):
            oracle.assess({"state": "OK", "events": [event]},
                          {"state": "DENIED_READ_ONLY"}, expected_event={"kind": "BOOK"})


def _server(path, *, drip=False):
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EAFNOSUPPORT, errno.ENOSYS}:
            pytest.skip("AF_UNIX unavailable in this environment; physical probe unverified")
        raise
    ready = threading.Event()
    def serve():
        with server:
            server.bind(str(path))
            os.chmod(path, 0o600)
            server.listen(2)
            server.settimeout(2)
            ready.set()
            try:
                for expected, response in (
                    ({"op": "pull", "consumer": "watch", "after_sequence": 0},
                     b'{"state":"OK","events":[{"kind":"BOOK","fixture":42}]}\n'),
                    ({"op": "place_order", "consumer": "watch"},
                     b'{"state":"DENIED_READ_ONLY"}\n'),
                ):
                    connection, _ = server.accept()
                    with connection:
                        if json.loads(connection.recv(4096)) != expected:
                            break
                        if drip:
                            for byte in response:
                                connection.sendall(bytes([byte]))
                                time.sleep(0.04)
                        else:
                            connection.sendall(response)
            except (OSError, TimeoutError):
                pass
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(1)
    return thread


def test_real_synthetic_unix_socket_matches_exact_event(tmp_path):
    oracle = module()
    endpoint = tmp_path / "fixture.sock"
    thread = _server(endpoint)
    result = oracle.probe(endpoint, expected_event={"kind": "BOOK", "fixture": 42}, timeout=1)
    thread.join(1)
    assert not thread.is_alive()
    assert result == {"protocol_probe_pass": True,
                      "af_unix_independently_verified": False, "qa_certified": False}


def test_real_socket_slow_drip_cannot_extend_total_deadline(tmp_path):
    oracle = module()
    endpoint = tmp_path / "slow.sock"
    thread = _server(endpoint, drip=True)
    start = time.monotonic()
    with pytest.raises(oracle.OracleHold):
        oracle.probe(endpoint, expected_event={"kind": "BOOK", "fixture": 42}, timeout=0.15)
    assert time.monotonic() - start < 0.75
    thread.join(1)
    assert not thread.is_alive()


def test_deadline_spans_both_requests_even_if_each_byte_arrives(tmp_path, monkeypatch):
    oracle = module()
    endpoint = tmp_path / "fixture.sock"
    original = Path.lstat
    def lstat(path):
        if path == endpoint:
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.geteuid())
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    now = [0.0]
    monkeypatch.setattr(oracle.time, "monotonic", lambda: now[0])
    sent = []
    class FakeSocket:
        def __init__(self, *args):
            self.request = b""
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            assert 0 < value <= 0.15
        def connect(self, path):
            pass
        def sendall(self, data):
            self.request = data
            sent.append(data)
        def recv(self, size):
            now[0] += 0.04
            if b'"pull"' in self.request:
                return b'{"state":"OK","events":[{"kind":"BOOK"}]}\n'
            return b' '
    monkeypatch.setattr(oracle.socket, "socket", FakeSocket)
    with pytest.raises(oracle.OracleHold):
        oracle.probe(endpoint, expected_event={"kind": "BOOK"}, timeout=0.15)
    assert len(sent) == 2
    assert now[0] <= 0.20

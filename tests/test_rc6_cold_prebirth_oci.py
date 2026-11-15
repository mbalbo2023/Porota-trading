"""A1084: pre-birth cold archival must not trust a Docker name as custody."""

import json
import subprocess

import pytest

from scripts import rc6_failed_cold_fix_forward as cold


CANDIDATE = "a" * 40
ATTESTED = {
    "schema": "RC6_PREBIRTH_QUIESCENCE_V1",
    "status": "OK",
    "candidate_sha": CANDIDATE,
    "runtime_writers": "QUIESCENT",
    "paper_mode": "PRODUCTION_PAPER",
    "real_orders_sent": 0,
    "ppi_watch_accessed": False,
}
LOCAL_RECEIPT = {
    "schema": "RC6_PREBIRTH_QUIESCENCE_V1",
    "status": "DRAINED",
    "candidate_sha": CANDIDATE,
    "real_orders_sent": 0,
    "ppi_watch_accessed": False,
}


def _fake_success(monkeypatch, response=ATTESTED):
    calls = []

    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(cold.subprocess, "run", invoke)
    return calls


def test_prebirth_attestor_confirms_exact_paper_candidate_and_uses_fixed_helper(
    monkeypatch,
):
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    monkeypatch.setenv("PYTHONPATH", "/tmp/malicious-client")
    calls = _fake_success(monkeypatch)
    assert cold.verify_prebirth_oci_quiescence(CANDIDATE, LOCAL_RECEIPT) == {
        "schema": "RC6_PREBIRTH_QUIESCENCE_V1",
        "status": "VERIFIED",
        "candidate_sha": CANDIDATE,
        "runtime_writers": "QUIESCENT",
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0,
    }
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == [
        "sudo", "-n", "/usr/local/sbin/porota-rc6-oci-custody",
        "quiescence", "verify-prebirth",
    ]
    assert kwargs["check"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert 0 < kwargs["timeout"] <= 30
    assert "DOCKER_HOST" not in kwargs["env"]
    assert "PYTHONPATH" not in kwargs["env"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": "LEGACY_UNTRUSTED"},
        {"candidate_sha": "b" * 40},
        {"status": "UNKNOWN"},
        {"real_orders_sent": 1},
        {"ppi_watch_accessed": True},
    ],
)
def test_invalid_local_prebirth_receipt_fails_before_invoking_helper(
    monkeypatch, mutation,
):
    calls = _fake_success(monkeypatch)
    with pytest.raises(ValueError, match="COLD_PREBIRTH_RECEIPT_UNTRUSTED"):
        cold.verify_prebirth_oci_quiescence(
            CANDIDATE, {**LOCAL_RECEIPT, **mutation}
        )
    assert calls == []


def test_missing_helper_is_fail_closed_and_does_not_use_docker_names(monkeypatch):
    calls = []

    def absent(argv, **_):
        calls.append(argv)
        raise FileNotFoundError("root helper absent; private path")

    monkeypatch.setattr(cold.subprocess, "run", absent)
    with pytest.raises(ValueError, match="COLD_PREBIRTH_ATTESTOR_UNAVAILABLE") as e:
        cold.verify_prebirth_oci_quiescence(CANDIDATE, LOCAL_RECEIPT)
    assert "private" not in str(e.value)
    assert calls == [[
        "sudo", "-n", "/usr/local/sbin/porota-rc6-oci-custody",
        "quiescence", "verify-prebirth",
    ]]


def test_helper_hold79_is_not_retried_or_exposed(monkeypatch):
    calls = []

    def denied(argv, **_):
        calls.append(argv)
        raise subprocess.CalledProcessError(
            79, argv, output="secret CID from host", stderr="root-path"
        )

    monkeypatch.setattr(cold.subprocess, "run", denied)
    with pytest.raises(ValueError, match="COLD_PREBIRTH_ATTESTOR_UNAVAILABLE") as e:
        cold.verify_prebirth_oci_quiescence(CANDIDATE, LOCAL_RECEIPT)
    assert "secret" not in str(e.value)
    assert "root-path" not in str(e.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": "RC6_SEALED_NEW_BIRTHS"},
        {"status": "HOLD"},
        {"candidate_sha": "b" * 40},
        {"runtime_writers": "UNVERIFIED"},
        {"paper_mode": "REAL"},
        {"real_orders_sent": True},
        {"ppi_watch_accessed": True},
        {"cid": "f" * 64},
    ],
)
def test_root_attestor_mismatch_is_never_rehabilitated(monkeypatch, mutation):
    _fake_success(monkeypatch, {**ATTESTED, **mutation})
    with pytest.raises(ValueError, match="COLD_PREBIRTH_ATTESTOR_REJECTED"):
        cold.verify_prebirth_oci_quiescence(CANDIDATE, LOCAL_RECEIPT)


@pytest.mark.parametrize(
    "invalid",
    [
        "{}",
        "not-json",
        '{"schema":"RC6_PREBIRTH_QUIESCENCE_V1","schema":"duplicate"}',
        " " * 8192,
        "\ud800",
    ],
)
def test_bad_attestor_output_is_rejected_without_leak(monkeypatch, invalid):
    def fake(argv, **_):
        return subprocess.CompletedProcess(argv, 0, invalid, "")

    monkeypatch.setattr(cold.subprocess, "run", fake)
    with pytest.raises(ValueError, match="COLD_PREBIRTH_ATTESTOR_REJECTED"):
        cold.verify_prebirth_oci_quiescence(CANDIDATE, LOCAL_RECEIPT)

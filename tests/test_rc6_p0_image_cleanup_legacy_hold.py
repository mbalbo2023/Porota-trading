"""A1093: legacy image cleanup entrypoints must not inspect or mutate Docker."""

from contextlib import contextmanager
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import rc6_deploy_failure_cleanup as failure
from scripts import rc6_pretransfer_image_cleanup as pretransfer
from scripts import rc6_offline_current_image_cleanup as offline


@contextmanager
def no_lock():
    yield


@pytest.mark.parametrize("module,args", [
    (failure, ["--candidate-sha", "a" * 40, "--run-id", "100", "--attempt", "1"]),
    (pretransfer, []),
    (offline, ["1234567890"]),
])
def test_image_cleanup_entrypoint_holds_without_root_owned_reference_proof(
        monkeypatch, capsys, module, args):
    calls = []

    def denied(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 79, stdout="", stderr="HOLD79")

    monkeypatch.setattr(module.subprocess, "run", denied)
    if hasattr(module, "promotion_lock"):
        monkeypatch.setattr(module, "promotion_lock", no_lock)

    status = module.main(args)

    assert status == 79
    result = json.loads(capsys.readouterr().out.strip())
    assert result["status"] == "RED"
    assert result["reason"] == "OCI_IMAGE_REFERENCE_CUSTODY_HOLD79"
    assert result["cleanup_deferred"] is True
    assert calls == [["sudo", "-n", "/usr/local/sbin/porota-rc6-oci-custody",
                      "image", "preflight-cleanup"]]


@pytest.mark.parametrize("module,args", [
    (failure, ["--candidate-sha", "a" * 40]),
    (pretransfer, []),
    (offline, ["1234567890"]),
])
def test_spoofed_green_or_environment_override_cannot_enable_legacy_cleanup(
        monkeypatch, capsys, module, args):
    monkeypatch.setenv("POROTA_ALLOW_LEGACY_IMAGE_CLEANUP", "1")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    calls = []

    def forged(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="GREEN", stderr="")

    monkeypatch.setattr(module.subprocess, "run", forged)
    if hasattr(module, "promotion_lock"):
        monkeypatch.setattr(module, "promotion_lock", no_lock)
    assert module.main(args) == 79
    assert json.loads(capsys.readouterr().out.strip())["cleanup_deferred"] is True
    assert calls == [["sudo", "-n", "/usr/local/sbin/porota-rc6-oci-custody",
                      "image", "preflight-cleanup"]]


@pytest.mark.parametrize("module,args", [
    (failure, ["--candidate-sha", "a" * 40, "--run-id", "100", "--attempt", "1"]),
    (pretransfer, []),
    (offline, ["1234567890"]),
])
def test_exact_actions_stdin_transport_is_standalone_and_returns_79(
        module, args, tmp_path):
    # The existing Deploy V2 literally transports each file as `python3 -`.
    # It cannot import sibling candidate files from an unknown remote cwd.
    process = subprocess.run([sys.executable, "-B", "-", *args],
                             input=Path(module.__file__).read_text(), text=True,
                             cwd=tmp_path, capture_output=True, timeout=15)
    assert process.returncode == 79, process.stderr
    report = json.loads(process.stdout)
    assert report["reason"] == "OCI_IMAGE_REFERENCE_CUSTODY_HOLD79"
    assert report["cleanup_deferred"] is True

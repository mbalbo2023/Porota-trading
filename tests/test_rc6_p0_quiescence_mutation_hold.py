"""A1093: Docker P0 legacy mutation boundary, tested without a daemon."""

import json
import subprocess

import pytest

from scripts import rc6_deploy_quiescence as quiescence


@pytest.mark.parametrize("argv", [
    ["docker", "stop", "--time", "45", "porota_production_observer"],
    ["docker", "start", "porota_production_dashboard"],
    ["docker", "exec", "porota_critical_approval_rc6", "python", "-c", "pass"],
    ["docker", "rm", "-f", "porota_production_dashboard"],
    ["docker", "--host", "unix:///var/run/docker.sock", "stop", "abc"],
    ["docker", "image", "rm", "sha256:" + "a" * 64],
    ["/usr/bin/docker", "kill", "foreign-same-name"],
    ["/tmp/foreign/docker", "inspect", "porota_production_observer"],
    ["docker-compose", "stop", "bot"],
    ["sh", "-c", "docker stop porota_production_observer"],
    ["/usr/bin/env", "docker", "stop", "porota_production_observer"],
])
def test_legacy_docker_side_effects_hold_before_subprocess(monkeypatch, argv):
    calls = []

    def prohibited(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Docker must not have been called")

    monkeypatch.setattr(quiescence.subprocess, "run", prohibited)
    with pytest.raises(quiescence.LegacyDockerCustodyHold) as error:
        quiescence.command(argv)
    assert error.value.exit_code == 79
    assert str(error.value) == "QUIESCENCE_OCI_LEGACY_MUTATION_HOLD79"
    assert calls == []


def test_existing_read_only_docker_probe_can_remain_diagnostic(monkeypatch):
    observed = []

    def fake(argv, **kwargs):
        observed.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(quiescence.subprocess, "run", fake)
    assert quiescence.command(["docker", "inspect", "porota_production_observer"]) == "[]"
    assert observed == [["/usr/bin/docker", "--host", "unix:///var/run/docker.sock",
                         "inspect", "porota_production_observer"]]


def test_read_only_docker_inspect_is_not_redirected_to_boca_socket(monkeypatch):
    invoked = []
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "boca-rootless")
    monkeypatch.setenv("DOCKER_CONFIG", "/tmp/foreign-docker-config")

    def fake(argv, **kwargs):
        invoked.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(quiescence.subprocess, "run", fake)
    try:
        assert quiescence.command(["docker", "inspect", "porota_production_observer"]) == "[]"
    except quiescence.LegacyDockerCustodyHold:
        assert invoked == []  # Explicit HOLD79 is a safe alternative to pinned rootful.
    else:
        assert len(invoked) == 1
        argv, kwargs = invoked[0]
        assert argv[0] == "/usr/bin/docker"
        assert argv[1:3] == ["--host", "unix:///var/run/docker.sock"]
        assert "DOCKER_CONTEXT" not in kwargs["env"]
        assert kwargs["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        assert kwargs["env"].get("DOCKER_CONFIG") != "/tmp/foreign-docker-config"


def test_drain_refusal_before_making_any_durable_receipt(monkeypatch, tmp_path):
    root = tmp_path / "porota"
    monkeypatch.setattr(quiescence, "ROOT", root)
    calls = []
    monkeypatch.setattr(quiescence.subprocess, "run", lambda *a, **kw: calls.append(a))

    assert quiescence.main(["--candidate-sha", "a" * 40, "--action", "drain"]) == 79
    assert not (root / "data/deploy").exists()
    assert calls == []


def test_legacy_recover_name_start_preserves_typed_hold79(monkeypatch, tmp_path):
    root = tmp_path / "porota"
    deploy = root / "data/deploy"
    deploy.mkdir(parents=True)
    candidate = "b" * 40
    expected = {
        "candidate_sha": candidate,
        "installation_started": False,
        "status": "DRAINING",
        "stop_attempted": ["porota_production_observer"],
        "containers": {"porota_production_observer": {"id": "birth-observer", "image": "birth-image"}},
        "real_orders_sent": 0,
        "ppi_watch_accessed": False,
    }
    receipt_path = deploy / ("quiescence-" + candidate + ".json")
    receipt_path.write_text(json.dumps(expected))
    monkeypatch.setattr(quiescence, "ROOT", root)
    observed = []

    def legacy_probe(args):
        observed.append(args)
        if args[1] == "inspect":
            return json.dumps([{"Id": "birth-observer", "Image": "birth-image",
                                "State": {"Status": "exited"}}])
        if args[1] == "start":
            raise quiescence.LegacyDockerCustodyHold()
        raise AssertionError("Unexpected Docker operation")

    monkeypatch.setattr(quiescence, "command", legacy_probe)
    assert quiescence.main(["--candidate-sha", candidate, "--action", "recover"]) == 79
    assert [call[1] for call in observed] == ["inspect", "start"]
    assert json.loads(receipt_path.read_text()) == expected


def test_quiescence_cli_emits_typed_hold79_not_generic_exit1(monkeypatch, capsys):
    def blocked(*args, **kwargs):
        raise quiescence.LegacyDockerCustodyHold()

    monkeypatch.setattr(quiescence, "run", blocked)
    code = quiescence.main(["--candidate-sha", "a" * 40, "--action", "drain"])
    assert code == 79
    assert capsys.readouterr().out.strip() == "QUIESCENCE_OCI_LEGACY_MUTATION_HOLD79"

"""Exercise preopen against private release files and a bounded Docker transport.

Filesystem custody, JSON/manifest checks, daemon boundary and container guards
remain real. Docker responses, socket metadata and the account's home are fixture
boundaries; no daemon, market, order route or systemd mutation is used.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import stat

import pytest

import rc6_preopen as preopen
import rc6_paper_admission_gate as gate
from scripts import porota_operator_intent as intent
from scripts import rc6_operator_paper_rearm as rearm
from scripts import porota_rootless_host as host

SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64
AT = datetime(2026, 11, 16, 10, 21, tzinfo=preopen.TZ)


class FixtureIdentity(host.Identity):
    @property
    def socket(self):
        return self.home / "run/porota.sock"


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    home, root = tmp_path / "home", tmp_path / "porota"
    home.mkdir(mode=0o700)
    root.mkdir()
    identity = FixtureIdentity(os.getuid(), os.getgid(), "porota-test", home)
    identity.socket.parent.mkdir(mode=0o700)
    # Socket creation is prohibited in this offline test environment. Simulate
    # only its lstat result; Host.boundary still checks real directory custody
    # and the Docker transport's daemon identity/security options.
    identity.socket.touch()
    identity.state.mkdir(parents=True, mode=0o700)
    (identity.state / "releases").mkdir(mode=0o700)
    release = {
        "schema": "porota.release.v1", "source_sha": SHA, "source_tree": TREE,
        "image_id": IMAGE, "image_size_bytes": 100,
        "build_run_id": 123, "build_run_attempt": 1,
        "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
        "real_orders_sent_required": 0, "real_order_capability": "BLOCKED",
        "members": {name: {"bytes": 10, "sha256": "d" * 64} for name in (
            "porota-predeploy-image.tar.gz", "porota-deploy-bundle-v2.tgz",
            "porota-deploy-bundle-v2-manifest.json", "code-tests.json", "functional.json")},
    }
    manifest = identity.state / "releases" / SHA / "release.json"
    raw = write_private(manifest, release)
    current = {
        "schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
        "source_sha": SHA, "source_tree": TREE, "image_id": IMAGE,
        "attempt": "migration1", "daemon_id": "dedicated-daemon",
        "roles": {"observer": "1" * 64, "dashboard": "2" * 64},
        "real_orders_sent": 0, "operating_mode": "PRODUCTION_PAPER",
        "execution_mode": "SIMULATION",
        "release_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "uid_mapping": {"container_uid": 0, "host_uid": identity.uid,
                        "host_gid": identity.gid, "effective_cgroup_limits": "VERIFIED"},
    }
    current_path = identity.state / "current.json"
    write_private(current_path, current)
    marker = {"candidate_sha": SHA, "candidate_tree_sha": TREE, "image_id": IMAGE,
              "validation_status": "VALIDATED_RUNTIME", "mode": "PRODUCTION_PAPER",
              "real_orders_sent": 0, "real_order_routes": "NOT_CALLED",
              "ppi_watch_untouched": True, "deployment_backend": "ROOTLESS_SPLIT"}
    marker_path = root / "data/deploy/CURRENT_STATE_V2.json"
    write_private(marker_path, marker)
    containers = {}
    for role, cid in current["roles"].items():
        mounts = [{"Type": "bind", "Destination": "/app/data", "Source": str(root / "data"), "RW": True},
                  {"Type": "bind", "Destination": "/app/operator", "Source": str(identity.operator), "RW": False}]
        if role == "observer":
            mounts.append({"Type": "bind", "Destination": "/run/secrets/ppi_production.json",
                           "Source": str(root / ".secrets/ppi_production.json"), "RW": False})
        containers[cid] = {
            "Id": cid, "Name": "/porota-rc6-" + role + "-migration1", "Image": IMAGE,
            "RestartCount": 0,
            "Config": {"Image": IMAGE, "User": "0:0", "WorkingDir": "/app", "Entrypoint": None,
                "Cmd": ["python", "o_dashboard.py" if role == "dashboard" else "bv_paper_runtime.py"],
                "Labels": {"io.porota.project": "rc6", "io.porota.role": role, "io.porota.attempt": "migration1"},
                "Env": ["POROTA_BUILD_SHA=" + SHA, "POROTA_CANDIDATE_TREE_SHA=" + TREE,
                    "DASHBOARD_OPERATION_MODE=PRODUCTION_PAPER", "EXECUTION_MODE=SIMULATION",
                    "REAL_ORDER_CAPABILITY=BLOCKED", "PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db"]},
            "Mounts": mounts,
            "State": {"Running": True, "OOMKilled": False, "Health": {"Status": "healthy"}},
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "private", "PidMode": "",
                "Memory": 512 * 1024**2, "NanoCpus": 500_000_000, "PidsLimit": 128,
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m",
                          "/home/botuser/.cache": "rw,noexec,nosuid,size=64m",
                          "/home/botuser/.config": "rw,noexec,nosuid,size=16m"},
                "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}
                    if role == "dashboard" else {}},
        }
    info = {"SecurityOptions": ["name=rootless"], "CgroupVersion": "2", "CgroupDriver": "systemd",
            "DockerRootDir": str(identity.store), "ID": "dedicated-daemon",
            "Driver": "overlay2", "DriverStatus": [["Backing Filesystem", "extfs"]]}
    state = {"identity": identity, "current": current, "current_path": current_path,
             "manifest": manifest, "release": release, "marker": marker, "marker_path": marker_path,
             "containers": containers, "info": info, "legacy": False,
             "socket_uid": identity.uid, "socket_kind": stat.S_IFSOCK}
    original_lstat = Path.lstat

    def socket_metadata(path):
        observed = original_lstat(path)
        if path == identity.socket:
            fields = list(observed)
            fields[0] = state["socket_kind"] | stat.S_IMODE(observed.st_mode)
            fields[4] = state["socket_uid"]
            return os.stat_result(fields)
        return observed

    def transport(_client, *args, **_kwargs):
        if args == ("info", "--format", "{{json .}}"):
            return json.dumps(info)
        if len(args) == 3 and args[:2] == ("container", "inspect"):
            return json.dumps([containers[args[2]]])
        raise AssertionError("Preopen requested a non-readonly or unexpected Docker operation: " + repr(args))

    def legacy_command(args, timeout=20):
        if args[:3] == ["docker", "inspect", "-f"]:
            image = preopen.EXPECTED_IMAGE if state["legacy"] else IMAGE
            return 0, "true|" + image + "|" + IMAGE + "|0|false|true", ""
        if args[:4] == ["docker", "image", "inspect", "-f"]:
            return 0, IMAGE, ""
        raise AssertionError(args)

    monkeypatch.setattr(host.Identity, "current", classmethod(lambda cls: identity))
    monkeypatch.setattr(host.Docker, "call", transport)
    monkeypatch.setattr(Path, "lstat", socket_metadata)
    monkeypatch.setattr(preopen, "ROOT", root)
    monkeypatch.setattr(preopen, "cmd", legacy_command)
    monkeypatch.setenv("POROTA_RUNTIME_BACKEND", "ROOTLESS")
    yield state


@pytest.mark.parametrize("role", ["observer", "dashboard"])
def test_exact_rootless_release_is_accepted_by_id_without_tags(runtime, role):
    result = preopen.container("porota_production_" + role, role == "observer")
    assert result["state"] == "GREEN", result
    assert result["image_id"] == IMAGE
    assert result["container_id"] == runtime["current"]["roles"][role]


def test_private_current_selects_rootless_without_environment_or_public_marker(runtime, monkeypatch):
    monkeypatch.delenv("POROTA_RUNTIME_BACKEND")
    runtime["marker_path"].unlink()
    assert preopen.container("porota_production_observer", True)["state"] == "GREEN"


def test_legacy_readonly_inspection_still_works_before_any_migration(runtime, monkeypatch):
    monkeypatch.delenv("POROTA_RUNTIME_BACKEND")
    runtime["current_path"].unlink()
    runtime["marker"].pop("deployment_backend")
    write_private(runtime["marker_path"], runtime["marker"])
    runtime["legacy"] = True
    assert preopen.container("porota_production_observer", True)["state"] == "GREEN"


@pytest.mark.parametrize("change", [
    {"state": "NO_VERIFICADO"}, {"state": "BLOQUEADO"}, {"daemon_id": "foreign"},
    {"image_id": "sha256:" + "e" * 64}, {"source_sha": "e" * 40}, {"source_tree": "e" * 40},
    {"real_orders_sent": 1}, {"real_orders_sent": None}, {"real_orders_sent": False},
    {"roles": {"observer": "1" * 64}}, {"roles": {"observer": "2" * 64, "dashboard": "1" * 64}},
    {"roles": {"observer": "1" * 64, "dashboard": "1" * 64}},
    {"release_manifest_sha256": "f" * 64}, {"uid_mapping": None},
    {"operating_mode": "PRODUCTION"}, {"execution_mode": "LIVE"},
])
def test_invalid_private_identity_never_falls_back_to_healthy_legacy(runtime, change):
    runtime["legacy"] = True
    write_private(runtime["current_path"], {**runtime["current"], **change})
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


@pytest.mark.parametrize("damage", ["missing", "malformed", "symlink", "public"])
def test_current_custody_cannot_be_replaced_by_data_marker(runtime, monkeypatch, damage):
    monkeypatch.delenv("POROTA_RUNTIME_BACKEND")
    runtime["legacy"] = True
    path = runtime["current_path"]
    if damage == "public":
        path.chmod(0o666)
    else:
        path.unlink()
        if damage == "malformed":
            path.write_text("not-json")
        elif damage == "symlink":
            path.symlink_to(runtime["marker_path"])
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


@pytest.mark.parametrize("change", [
    {"SecurityOptions": ["name=seccomp"]}, {"DockerRootDir": "/var/lib/docker"},
    {"CgroupVersion": "1"}, {"CgroupDriver": "cgroupfs"},
    {"Driver": "overlayfs", "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]},
    {"Driver": "vfs"}, {"DriverStatus": None},
])
def test_wrong_daemon_boundary_cannot_reuse_private_receipt(runtime, change):
    runtime["info"].update(change)
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


@pytest.mark.parametrize("change", [{"socket_uid": 0}, {"socket_kind": stat.S_IFREG}])
def test_wrong_socket_owner_or_type_is_rejected(runtime, change):
    runtime.update(change)
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


@pytest.mark.parametrize("change", [
    {"container_uid": 1000}, {"container_uid": False}, {"host_uid": 0}, {"host_gid": 0},
    {"effective_cgroup_limits": "NO_VERIFICADO"},
])
def test_receipt_mapping_must_bind_namespace_zero_to_current_account(runtime, change):
    runtime["current"]["uid_mapping"].update(change)
    write_private(runtime["current_path"], runtime["current"])
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


@pytest.mark.parametrize("field", ["image", "source", "tree", "mount", "uid", "namespace", "restart", "stopped", "oom"])
def test_actual_container_drift_revokes_identity(runtime, field):
    item = runtime["containers"]["1" * 64]
    if field == "image": item["Image"] = "sha256:" + "f" * 64
    elif field in {"source", "tree"}:
        item["Config"]["Env"][0 if field == "source" else 1] += "bad"
    elif field == "mount": item["Mounts"][0]["Source"] = "/foreign/data"
    elif field == "uid": item["Config"]["User"] = "1000:1000"
    elif field == "namespace": item["HostConfig"]["PidMode"] = "host"
    elif field == "restart": item["RestartCount"] = 1
    elif field == "stopped": item["State"]["Running"] = False
    elif field == "oom": item["State"]["OOMKilled"] = True
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


def test_other_candidate_argument_cannot_borrow_validated_runtime(runtime):
    assert preopen.container("porota_production_observer", True, "f" * 40)["state"] == "RED"
    assert preopen.container("porota_production_observer", True, SHA)["state"] == "GREEN"


def test_manifest_changed_after_publication_is_rejected(runtime):
    runtime["manifest"].write_text(json.dumps({**runtime["release"], "source_tree": "e" * 40}))
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


def test_dashboard_health_is_required(runtime):
    runtime["containers"]["2" * 64]["State"]["Health"]["Status"] = "unhealthy"
    assert preopen.container("porota_production_dashboard")["state"] == "RED"


def test_explicit_legacy_request_cannot_downgrade_recorded_rootless(runtime, monkeypatch):
    runtime["legacy"] = True
    monkeypatch.setenv("POROTA_RUNTIME_BACKEND", "LEGACY")
    assert preopen.container("porota_production_observer", True)["state"] == "RED"


@pytest.fixture
def operator(runtime, monkeypatch):
    record = {"schema": rearm.REARM_SCHEMA, "status": rearm.REARM_STATUS,
              "candidate_sha": "3" * 40, "candidate_tree_sha": "4" * 40,
              "image_id": "sha256:" + "5" * 64,
              "deploy_run_id": 111, "deploy_run_attempt": 1, "runtime_validation_run_id": 111,
              "operator_comment_id": 222, "operator_github_login": "mbalbo2023", "rearm_run_id": 333,
              "issued_at_ar": "2026-11-10T10:00:00-03:00", "paper_mode": "PRODUCTION_PAPER",
              "real_orders_sent": 0, "real_order_routes": "NOT_CALLED", "ppi_watch_untouched": True}
    path = runtime["identity"].operator / rearm.RECEIPT_NAME
    write_private(path, record)
    monkeypatch.setattr(gate, "HOST_ROOT", preopen.ROOT)
    runtime.update(intent=record, intent_path=path)
    return runtime


def test_host_reads_original_private_intent_and_current_release(operator):
    authorized, reason, proof = rearm.evaluate_rearm(now=AT, context="host")
    assert authorized and reason == "OPERATOR_REARM_AUTHORIZED"
    assert proof["candidate_sha"] == SHA
    assert proof["operator_comment_id"] == operator["intent"]["operator_comment_id"]
    assert json.loads(operator["intent_path"].read_bytes()) == operator["intent"]


def test_host_publishes_private_gate_only_from_green_existing_active_intent(operator):
    before = operator["intent_path"].read_bytes()
    receipt = gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"}, now=AT)
    target = operator["identity"].operator / "paper-preopen-v2.json"
    assert receipt["status"] == "GO_PAPER_SCALPING"
    assert receipt["candidate_sha"] == SHA
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_bytes()) == receipt
    assert operator["intent_path"].read_bytes() == before


def test_pause_survives_invalid_release_and_never_creates_consent(operator):
    paused = {key: value for key, value in operator["intent"].items() if key in rearm._PAUSED_KEYS}
    paused["status"] = rearm.PAUSE_STATUS
    before = write_private(operator["intent_path"], paused)
    operator["current_path"].unlink()
    assert not rearm.evaluate_rearm(now=AT, context="host")[0]
    assert rearm.trusted_operator_pause(now=AT, context="host")["operator_comment_id"] == 222
    receipt = gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"}, now=AT)
    assert receipt["status"] == "NO_GO_PAPER_SCALPING"
    assert operator["intent_path"].read_bytes() == before


def test_reading_missing_intent_never_creates_the_private_directory(runtime):
    directory = runtime["identity"].operator
    assert intent.read_intent(runtime["identity"].home, now=AT) is None
    assert not directory.exists()


@pytest.mark.parametrize("damage", ["missing", "symlink", "hardlink", "public", "wrong_owner"])
def test_private_intent_custody_is_required_for_host_authorization(operator, monkeypatch, damage):
    path = operator["intent_path"]
    if damage == "public":
        path.chmod(0o644)
    elif damage == "wrong_owner":
        real_fstat, target = os.fstat, path.stat()
        def foreign_owner(fd):
            observed = real_fstat(fd)
            if (observed.st_dev, observed.st_ino) == (target.st_dev, target.st_ino):
                fields = list(observed)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return observed
        monkeypatch.setattr(os, "fstat", foreign_owner)
    elif damage == "hardlink":
        os.link(path, path.with_name("linked.json"))
    else:
        path.unlink()
        if damage == "symlink":
            fake = preopen.ROOT / "data/forged-intent.json"
            write_private(fake, operator["intent"])
            path.symlink_to(fake)
    assert rearm.evaluate_rearm(now=AT, context="host")[0] is False
    assert rearm.trusted_operator_pause(now=AT, context="host") is None


def test_data_intent_cannot_be_substituted_for_private_authority(operator):
    fake = preopen.ROOT / "data/forged-operator"
    write_private(fake / rearm.RECEIPT_NAME, operator["intent"])
    assert rearm.evaluate_rearm(now=AT, context="host", directory=fake)[0] is False
    with pytest.raises(ValueError):
        gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"},
                             now=AT, receipt_path=fake / "paper-preopen-v2.json")


def test_public_marker_cannot_replace_private_release_validation(operator):
    operator["current_path"].unlink()
    assert rearm.evaluate_rearm(now=AT, context="host")[0] is False
    receipt = gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"}, now=AT)
    assert receipt["status"] == "NO_GO_PAPER_SCALPING"


def test_runtime_context_still_requires_namespace_root_custody(operator):
    # The same host UID is namespace zero only inside the verified container;
    # host context cannot weaken the existing runtime UID-zero reader.
    assert rearm.evaluate_rearm(now=AT, context="runtime",
                                directory=operator["identity"].operator,
                                candidate_sha=SHA, candidate_tree_sha=TREE)[0] is False


@pytest.mark.parametrize("damage", ["public", "parent_writable", "symlink", "wrong_owner"])
def test_private_operator_directory_custody_blocks_reads_and_publication(operator, monkeypatch, damage):
    directory = operator["identity"].operator
    if damage == "public":
        directory.chmod(0o755)
    elif damage == "parent_writable":
        directory.parent.chmod(0o777)
    elif damage == "symlink":
        moved = directory.with_name("moved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        original_stat = Path.stat
        def foreign_directory(path, *args, **kwargs):
            observed = original_stat(path, *args, **kwargs)
            if path == directory:
                fields = list(observed)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return observed
        monkeypatch.setattr(Path, "stat", foreign_directory)
    assert rearm.evaluate_rearm(now=AT, context="host")[0] is False
    with pytest.raises((OSError, ValueError)):
        gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"}, now=AT)


def test_rootless_host_reader_cannot_run_as_root(operator, monkeypatch):
    def root_account(cls):
        raise host.Hold("NONROOT_SSH_IDENTITY_REQUIRED")
    monkeypatch.setattr(host.Identity, "current", classmethod(root_account))
    assert preopen.container("porota_production_observer", True)["state"] == "RED"
    assert rearm.evaluate_rearm(now=AT, context="host")[0] is False
    with pytest.raises(ValueError, match="NONROOT_SSH_IDENTITY_REQUIRED"):
        gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"}, now=AT)


@pytest.mark.parametrize("damage", ["public", "symlink"])
def test_existing_unsafe_gate_cannot_be_overwritten(operator, damage):
    path = operator["identity"].operator / "paper-preopen-v2.json"
    if damage == "public":
        original = write_private(path, {"status": "NO_GO_PAPER_SCALPING"})
        path.chmod(0o644)
    else:
        original = operator["intent_path"].read_bytes()
        path.symlink_to(operator["intent_path"])
    with pytest.raises((OSError, ValueError)):
        gate.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"}, now=AT)
    assert path.read_bytes() == original


@pytest.mark.parametrize("result, at", [
    ({"status": "RED", "phase": "T_MINUS_10"}, AT),
    ({"status": "GREEN", "phase": "T_MINUS_45"}, AT),
    ({"status": "GREEN", "phase": "T_MINUS_10"}, AT.replace(hour=11)),
])
def test_private_publication_keeps_full_green_and_time_requirements(operator, result, at):
    receipt = gate.publish_preopen(result, now=at)
    assert receipt["status"] == "NO_GO_PAPER_SCALPING"


@pytest.fixture
def byma_review(runtime, monkeypatch):
    source = runtime["manifest"].parent / "source"
    source.mkdir(mode=0o700)
    module = source / "rc6_preopen.py"
    module.touch()
    relative = "ops/policy/rc6-byma-reviewed-authority-v1.json"
    paths = {"release": source / relative, "legacy": preopen.ROOT / relative}
    review = {"schema": "porota-byma-authority-review-v1", "decision_effect": "OBSERVE_ONLY",
              "real_money_authorized": False, "valid_through_ar": "2026-12-31",
              "pages": {"HOURS": {"url": "https://www.byma.com.ar/mercado/horarios",
                                   "sha256": "a" * 64}}}
    for path in paths.values():
        write_private(path, review)
    watch = preopen.ROOT / "data/market/byma_morning_watch_latest.json"
    write_private(watch, {"observed_at": AT.isoformat(), "state": "CHANGED_REVIEW_REQUIRED",
                          "changed_components": ["HOURS"],
                          "pages": {"HOURS": {**review["pages"]["HOURS"], "status": "OK",
                                              "scope": "h1-to-footer-v2"}},
                          "pending_review": {"HOURS": {"sha256": "a" * 64}}})
    monkeypatch.setattr(preopen, "__file__", str(module))
    monkeypatch.setattr(preopen, "BYMA_MORNING_WATCH", watch)
    monkeypatch.setattr(preopen, "BYMA_AUTHORITY_REVIEW", paths["legacy"])
    return paths, review


@pytest.mark.parametrize("explicit_backend", [True, False])
def test_rootless_byma_review_comes_from_release_source(runtime, byma_review, monkeypatch, explicit_backend):
    paths, _review = byma_review
    paths["legacy"].write_text("{}")
    if not explicit_backend:
        monkeypatch.delenv("POROTA_RUNTIME_BACKEND")
    result = preopen.byma_morning_watch(AT.date())
    assert result["state"] == "AMBER", result
    assert result["reason"] == "BYMA_CRITICAL_REVIEWED_EXACT"


@pytest.mark.parametrize("damage", ["missing", "digest", "expired", "real_money"])
def test_rootless_byma_review_never_falls_back_to_legacy_policy(byma_review, damage):
    paths, review = byma_review
    if damage == "missing":
        paths["release"].unlink()
    else:
        if damage == "digest": review["pages"]["HOURS"]["sha256"] = "b" * 64
        elif damage == "expired": review["valid_through_ar"] = "2026-11-15"
        elif damage == "real_money": review["real_money_authorized"] = True
        write_private(paths["release"], review)
    result = preopen.byma_morning_watch(AT.date())
    assert result["state"] == "RED", result
    assert result["reason"] == "BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED"


def test_legacy_byma_review_keeps_original_host_path(runtime, byma_review, monkeypatch):
    paths, _review = byma_review
    monkeypatch.delenv("POROTA_RUNTIME_BACKEND")
    runtime["current_path"].unlink()
    runtime["marker_path"].unlink()
    paths["release"].write_text("{}")
    assert preopen.byma_morning_watch(AT.date())["reason"] == "BYMA_CRITICAL_REVIEWED_EXACT"
    paths["legacy"].unlink()
    assert preopen.byma_morning_watch(AT.date())["reason"] == "BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED"

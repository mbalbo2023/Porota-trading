from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.porota_dependency_repro_audit import audit, installed_distribution_audit
from scripts.rc6_deploy_scoped_cleanup import cleanup
from tests.test_porota_dependency_repro_audit import hashed_inputs


@pytest.mark.parametrize("drift", ["missing_hash", "changed_hash", "mutable_action", "other_action_commit",
    "unpinned_base", "hash_install_disabled", "build_isolation", "extra_lock_dependency",
    "wrong_platform", "old_glibc", "missing_glibc", "musl_platform", "bad_build_hash", "runtime_only_versions",
    "broken_continuation"])
def test_supply_chain_drift_fails_closed(drift):
    lock, docker, kwargs = hashed_inputs(); kwargs = deepcopy(kwargs)
    if drift == "missing_hash": lock = lock.replace(" --hash=sha256:" + "a"*64, "", 1)
    elif drift == "changed_hash": lock = lock.replace("a"*64, "e"*64, 1)
    elif drift == "mutable_action": kwargs["workflow_texts"]["predeploy"] = "uses: actions/checkout@v4"
    elif drift == "other_action_commit": kwargs["workflow_texts"]["predeploy"] = "uses: actions/checkout@" + "f"*40
    elif drift == "unpinned_base": docker = docker.replace("@sha256:" + "d"*64, "")
    elif drift == "hash_install_disabled": docker = docker.replace("--require-hashes", "")
    elif drift == "build_isolation": docker = docker.replace("--no-build-isolation", "")
    elif drift == "extra_lock_dependency": lock += "ghost==1.0 --hash=sha256:" + "a"*64 + "\n"
    elif drift == "wrong_platform": kwargs["current_platform"]["architecture"] = "aarch64"
    elif drift == "old_glibc": kwargs["current_platform"]["libc_version"] = "2.31"
    elif drift == "missing_glibc": kwargs["current_platform"].pop("libc_version")
    elif drift == "musl_platform": kwargs["current_platform"]["libc_name"] = "musl"
    elif drift == "bad_build_hash": kwargs["build_lock_text"] = kwargs["build_lock_text"].replace("b"*64, "f"*64)
    elif drift == "runtime_only_versions": lock = "requests==2.34.2\npandas==2.3.3\n"
    elif drift == "broken_continuation": lock = lock.rstrip() + " \\\n"
    assert audit("requests>=2\npandas>=2\n", lock, docker, **kwargs)["status"] == "REPRODUCIBILITY_GAP"


def test_real_checked_in_supply_chain_policy_and_platform_lock_are_green():
    root = Path(".")
    result = audit((root/"requirements.txt").read_text(), (root/"requirements.lock.txt").read_text(), (root/"Dockerfile").read_text(),
        supply_chain_policy=json.loads((root/"ops/policy/rc6-supply-chain-v1.json").read_text()),
        build_lock_text=(root/"requirements.build.lock.txt").read_text(), workflow_texts={name: (root/name).read_text() for name in (
            ".github/workflows/porota-predeploy-v2.yml", ".github/workflows/porota-deploy-v2-promote.yml")})
    assert result["status"] == "GREEN" and result["lock_hashed_requirements"] == 154
    assert result["build_tools_total"] == 3 and result["hermetic_build"] is False
    assert result["residual_boundaries"]


@pytest.mark.parametrize("drift", ["none", "missing", "extra", "version"])
def test_installed_closure_distinguishes_extra_transitives_and_build_tools(drift):
    _, _, kwargs = hashed_inputs()
    installed = {"requests": "2.34.2", "pandas": "2.3.3", "wheel": "0.45.1"}
    if drift == "missing": installed.pop("wheel")
    elif drift == "extra": installed["unreviewed-transitive"] = "1.0"
    elif drift == "version": installed["wheel"] = "0.46.0"
    result = installed_distribution_audit(kwargs["supply_chain_policy"], installed)
    assert result["status"] == ("GREEN" if drift == "none" else "REPRODUCIBILITY_GAP")


def test_scoped_cleanup_keeps_active_stopped_stable_and_explicit_pins():
    ids = {key: "sha256:" + char*64 for key, char in zip(("active", "stopped", "stable", "pinned", "unused"), "abcde")}
    tags = {key: "porota-trading-bot:17.0.0-rc6-candidate-" + char*40 for key, char in zip(ids, "abcde")}
    calls = []
    def docker(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"): return ids["stable"]
        if args[:2] == ("image", "ls"): return "\n".join(tags[key]+"|"+identity for key, identity in ids.items())
        if args[0] == "ps": return "container-id" if args[-1].split("=",1)[1] in {ids["active"], ids["stopped"]} else ""
        if args[:2] == ("image", "rm"): return "removed"
        raise AssertionError(args)
    measurements = iter((1000, 1200))
    result = cleanup(pinned_ids=[ids["pinned"]], run_docker=docker, disk_probe=lambda: next(measurements))
    assert result["removed_candidate_tags"] == [tags["unused"]]
    assert len(result["retained_candidate_tags"]) == 4 and result["space_recovered"] == 200
    assert all("prune" not in args and "-f" not in args for args in calls)
    assert all(args[:2] != ("inspect", "--format") for args in calls)
    assert result["ppi_watch"] == "UNTOUCHED_NOT_INSPECTED"


def test_cleanup_inventory_failure_or_unknown_namespace_never_deletes():
    calls = []
    def docker(*args):
        calls.append(args)
        if args[:2] == ("image", "inspect"): return "sha256:" + "a"*64
        return "foreign:tag|sha256:" + "b"*64
    with pytest.raises(ValueError, match="INVENTORY_INVALID"):
        cleanup(run_docker=docker, disk_probe=lambda: 1000)
    assert not any(args[:2] == ("image", "rm") for args in calls)

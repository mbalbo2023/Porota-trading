import pytest

from scripts.porota_apply_host_control_plane_v2 import build_legacy_retire_plan, build_plan


def test_plan_is_explicit_and_blocks_ppi_watch():
    policy={"units":{
        "systemd/porota-preopen-rc6.timer":{
            "role":"ACTIVE_TIMER","install":True,"enabled":True,"active":True
        }
    }}
    plan=build_plan(policy)
    assert plan[0]["unit_name"]=="porota-preopen-rc6.timer"
    assert plan[0]["role"]=="ACTIVE_TIMER"

    bad={"units":{
        "systemd/porota-ppi-watch-rc6.timer":{
            "role":"ACTIVE_TIMER","install":True,"enabled":True,"active":True
        }
    }}
    with pytest.raises(ValueError,match="PPI_WATCH_FORBIDDEN"):
        build_plan(bad)


def test_plan_rejects_non_rc6_or_unsafe_unit_names():
    for path in ("systemd/ssh.service","systemd/../porota-demo-rc6.timer"):
        with pytest.raises(ValueError):
            build_plan({"units":{path:{"role":"ACTIVE_TIMER","install":True}}})


def test_legacy_retire_plan_is_exact_and_preserves_external_control_plane():
    policy={
        "external_host_units":{
            "porota-critical-approval-rc6.service":{"preserve":True},
        },
        "legacy_retire_units":[
            "porota-preopen.timer",
            "porota-scheduler-export-hf6.service",
            "porota-live-decision-cockpit-rc6.timer",
        ],
    }
    assert build_legacy_retire_plan(policy)==[
        "porota-preopen.timer",
        "porota-scheduler-export-hf6.service",
        "porota-live-decision-cockpit-rc6.timer",
    ]

    for bad in (
        ["ssh.service"],
        ["../porota-preopen.timer"],
        ["porota-ppi-watch-rc6.timer"],
        ["porota-critical-approval-rc6.service"],
        ["porota-preopen.timer","porota-preopen.timer"],
    ):
        with pytest.raises(ValueError):
            build_legacy_retire_plan({
                "external_host_units":policy["external_host_units"],
                "legacy_retire_units":bad,
            })


def test_versioned_legacy_retirement_contract_is_complete_and_ppi_watch_safe():
    import json
    from pathlib import Path

    policy=json.loads(Path("ops/policy/host-control-plane-reconciliation-v2.json").read_text())
    retired=build_legacy_retire_plan(policy)
    assert len(retired)==27
    assert len(set(retired))==27
    assert "porota-critical-approval-rc6.service" not in retired
    assert "porota-critical-github-proxy-rc6.service" not in retired
    assert all(not ("ppi" in name.lower() and "watch" in name.lower()) for name in retired)
    assert policy["legacy_retire_contract"]["unknown_units"]=="FAIL_CLOSED"
    assert policy["legacy_retire_contract"]["external_host_units"]=="PRESERVE"

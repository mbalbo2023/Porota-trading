import pytest

from scripts.porota_apply_host_control_plane_v2 import build_plan


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

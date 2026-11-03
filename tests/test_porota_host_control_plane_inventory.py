from scripts.porota_host_control_plane_inventory import build_inventory


def test_complete_policy_matches_every_tracked_rc6_unit():
    tracked=[
        "systemd/porota-good-rc6.service",
        "systemd/porota-good-rc6.timer",
        "systemd/porota-old-rc4.timer",
    ]
    result=build_inventory(tracked,[
        "systemd/porota-good-rc6.service",
        "systemd/porota-good-rc6.timer",
    ])
    assert result["status"]=="GREEN"
    assert result["counts"]["tracked_rc6_units"]==2


def test_missing_or_extra_policy_unit_fails_provenance():
    tracked=["systemd/porota-good-rc6.service"]
    result=build_inventory(tracked,["systemd/porota-extra-rc6.timer"])
    assert result["status"]=="FAILED_POLICY_PROVENANCE"
    assert result["missing_policy_units"]==["systemd/porota-good-rc6.service"]
    assert result["extra_policy_units"]==["systemd/porota-extra-rc6.timer"]

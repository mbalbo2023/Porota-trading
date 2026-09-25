from scripts.porota_host_control_plane_inventory import build_inventory


def test_detects_untracked_systemd_input_referenced_by_workflow():
    tracked = [
        "systemd/porota-good-rc6.service",
        "systemd/porota-good-rc6.timer",
    ]
    workflow = """
    sudo install systemd/porota-good-rc6.service /etc/systemd/system/
    sudo install systemd/porota-residual-rc6.service /etc/systemd/system/
    """
    result = build_inventory(tracked, workflow)

    assert result["status"] == "FAILED_UNTRACKED_WORKFLOW_INPUT"
    assert result["untracked_workflow_inputs"] == [
        "systemd/porota-residual-rc6.service"
    ]


def test_reports_tracked_rc6_units_not_managed_by_canonical_workflow():
    tracked = [
        "systemd/porota-used-rc6.service",
        "systemd/porota-unused-rc6.timer",
        "systemd/porota-legacy-rc4.timer",
    ]
    workflow = "systemd/porota-used-rc6.service"
    result = build_inventory(tracked, workflow)

    assert result["status"] == "GREEN"
    assert result["tracked_rc6_units"] == [
        "systemd/porota-unused-rc6.timer",
        "systemd/porota-used-rc6.service",
    ]
    assert result["tracked_rc6_units_not_referenced_by_canonical_deploy"] == [
        "systemd/porota-unused-rc6.timer"
    ]

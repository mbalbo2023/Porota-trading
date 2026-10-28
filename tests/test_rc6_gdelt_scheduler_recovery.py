from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_gdelt_units_are_canonical_retired_tombstones():
    policy=json.loads((ROOT/"ops/policy/host-control-plane-reconciliation-v2.json").read_text(encoding="utf-8"))
    units=policy["units"]
    service=units["systemd/porota-gdelt-event-risk-rc6.service"]
    timer=units["systemd/porota-gdelt-event-risk-rc6.timer"]
    assert service["role"] == "RETIRED_SERVICE"
    assert service["install"] is False
    assert service["remove_on_deploy"] is True
    assert timer["role"] == "RETIRED_TIMER"
    assert timer["install"] is False
    assert timer["enabled"] is False
    assert timer["active"] is False
    assert timer["remove_on_deploy"] is True


def test_tracked_unit_files_are_inert_even_if_copied_accidentally():
    service=(ROOT/"systemd/porota-gdelt-event-risk-rc6.service").read_text(encoding="utf-8")
    timer=(ROOT/"systemd/porota-gdelt-event-risk-rc6.timer").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/true" in service
    assert "docker" not in service.lower()
    assert "python" not in service.lower()
    assert "DEPRECATED_EXCLUDED" in service
    assert "DEPRECATED_EXCLUDED" in timer

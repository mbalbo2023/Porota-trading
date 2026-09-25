from scripts.porota_host_policy_v2 import validate_policy


def manifest(paths):
    return {
        "units": [
            {"source_path": p, "managed_by_canonical_deploy": False}
            for p in paths
        ]
    }


def test_policy_requires_exact_coverage_of_unmanaged_units():
    m = manifest(["systemd/a.timer"])
    p = {"units": {}}
    r = validate_policy(m, p)
    assert r["status"] == "FAILED"
    assert r["missing_policy_units"] == ["systemd/a.timer"]


def test_active_and_quarantined_timer_contracts_are_explicit():
    m = manifest(["systemd/a.timer", "systemd/q.timer"])
    p = {"units": {
        "systemd/a.timer": {
            "role": "ACTIVE_TIMER", "install": True,
            "enabled": True, "active": True, "remove_on_deploy": False,
            "reason": "required",
        },
        "systemd/q.timer": {
            "role": "QUARANTINED_TIMER", "install": False,
            "enabled": False, "active": False, "masked": True,
            "remove_on_deploy": False, "reason": "quarantine",
        },
    }}
    r = validate_policy(m, p)
    assert r["status"] == "GREEN"
    assert r["tracked_unmanaged_units"] == 2


def test_retired_timer_must_be_removed_and_disabled():
    m = manifest(["systemd/old.timer"])
    p = {"units": {
        "systemd/old.timer": {
            "role": "RETIRED_TIMER", "install": False,
            "enabled": False, "active": False, "remove_on_deploy": True,
            "reason": "retired",
        }
    }}
    assert validate_policy(m, p)["status"] == "GREEN"

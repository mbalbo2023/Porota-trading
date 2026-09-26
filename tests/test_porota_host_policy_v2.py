from scripts.porota_host_policy_v2 import validate_policy


def manifest(paths):
    return {"units": [{"source_path": p} for p in paths]}


def test_policy_requires_exact_coverage_of_all_tracked_units():
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
    assert r["tracked_rc6_units"] == 2


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


def test_current_all_family_policy_keeps_a3_history_active():
    import json
    from pathlib import Path
    policy=json.loads(Path("ops/policy/host-control-plane-reconciliation-v2.json").read_text())
    units=policy["units"]
    for name in (
        "systemd/porota-a3-history-daily-rc6.timer",
        "systemd/porota-a3-history-reconcile-rc6.timer",
        "systemd/porota-a3-history-weekend-rc6.timer",
    ):
        row=units[name]
        assert row["role"]=="ACTIVE_TIMER"
        assert row["install"] is True
        assert row["enabled"] is True
        assert row["active"] is True
    svc=units["systemd/porota-a3-history-rc6@.service"]
    assert svc["role"]=="ACTIVE_SERVICE"
    assert svc["install"] is True


def test_repository_policy_covers_every_packaged_rc6_unit():
    import json
    from pathlib import Path
    root=Path(".")
    packaged=set()
    for prefix in (Path("systemd"), Path("ops/systemd")):
        if not prefix.exists():
            continue
        for p in prefix.iterdir():
            rel=p.as_posix()
            if p.is_file() and p.suffix in {".service",".timer"} and "rc6" in p.name.lower():
                packaged.add(rel)
    policy=json.loads(Path("ops/policy/host-control-plane-reconciliation-v2.json").read_text())
    assert set(policy["units"]) == packaged

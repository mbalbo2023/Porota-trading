from scripts.porota_host_manifest_v2 import build_manifest


def test_host_manifest_records_hash_target_and_lifecycle(tmp_path):
    repo=tmp_path/"repo"
    (repo/"systemd").mkdir(parents=True)
    unit=repo/"systemd/porota-demo-rc6.timer"
    unit.write_text("[Timer]\nOnCalendar=daily\n",encoding="utf-8")
    modes={"systemd/porota-demo-rc6.timer":"100644"}
    policy={"units":{"systemd/porota-demo-rc6.timer":{
        "role":"ACTIVE_TIMER","install":True,"enabled":True,"active":True,
        "remove_on_deploy":False,"reason":"fixture"
    }}}
    result=build_manifest(repo,policy,"abc123",modes=modes)
    assert result["status"]=="GREEN"
    assert result["counts"]["tracked_rc6_units"]==1
    row=result["units"][0]
    assert row["install_target"]=="/etc/systemd/system/porota-demo-rc6.timer"
    assert row["expected_enabled"] is True
    assert row["expected_active"] is True
    assert row["role"]=="ACTIVE_TIMER"
    assert len(row["source_sha256"])==64


def test_host_manifest_fails_when_policy_and_tracked_units_diverge(tmp_path):
    repo=tmp_path/"repo"
    (repo/"systemd").mkdir(parents=True)
    unit=repo/"systemd/porota-present-rc6.service"
    unit.write_text("[Service]\nType=oneshot\n",encoding="utf-8")
    modes={"systemd/porota-present-rc6.service":"100644"}
    policy={"units":{"systemd/porota-missing-rc6.service":{
        "role":"ACTIVE_SERVICE","install":True,"reason":"fixture"
    }}}
    result=build_manifest(repo,policy,"abc123",modes=modes)
    assert result["status"]=="FAILED_POLICY_PROVENANCE"
    assert result["missing_policy_units"]==["systemd/porota-present-rc6.service"]
    assert result["extra_policy_units"]==["systemd/porota-missing-rc6.service"]

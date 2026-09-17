import iol_shadow_observation_rc6 as shadow

def test_refresh_stays_shadow_and_cannot_change_live_authority(tmp_path):
    def tool(name,args):
        return {"last":100,"bid":99,"ask":101} if name=="get_asset_quote" else {"type":"ACCIONES","currency":"ARS","units_per_lot":1}
    data=shadow.refresh(["YPFD"],tool,root=tmp_path,primary_last_by_symbol={"YPFD":96},tolerance_pct=2)
    assert data["mode"]=="SHADOW"
    assert data["decision_effect"]=="OBSERVE_ONLY"
    assert data["live_decision_authority"] is False
    assert data["real_money_authorized"] is False
    assert data["symbols"][0]["primary_comparison"]=="BACKGROUND_DIVERGENCE"

def test_collect_is_cache_only_and_nonblocking(tmp_path):
    missing=shadow.collect(tmp_path)
    assert missing["state"]=="UNAVAILABLE"
    assert missing["decision_effect"]=="OBSERVE_ONLY"
    shadow.refresh(["GGAL"],lambda name,args: {"last":100} if name=="get_asset_quote" else {"type":"ACCIONES"},root=tmp_path)
    cached=shadow.collect(tmp_path)
    assert cached["state"]=="READY"
    assert cached["symbols"][0]["symbol"]=="GGAL"

def test_ppi_remains_primary_for_actions():
    import cy_market_source_arbitration_hf6 as policy
    assert policy.primary_live_source("ACCIONES")=="PPI"
    assert policy.SYNCHRONOUS_SECONDARY_VALIDATION_ALLOWED is False
    assert policy.BACKGROUND_DIVERGENCE_CAN_HOLD_LIVE is False

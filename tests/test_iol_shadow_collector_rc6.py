import iol_shadow_collector_rc6 as collector
import iol_shadow_observation_rc6 as observation

class FakeClient:
    def __init__(self): self.calls=[]
    def call(self, tool_name, arguments):
        self.calls.append((tool_name,dict(arguments)))
        if tool_name=="get_asset_quote":
            return {"unit_price":100,"bid":99,"ask":101,"trade":{"lot_price":100},"variation":1.2}
        return {"type":"ACCIONES","currency":"ARS","units_per_lot":1}

def policy(): return collector.CollectionPolicy(min_interval_seconds=1,max_calls_per_minute=40)

def test_quote_uses_required_term_and_normalizes_real_iol_shape(tmp_path):
    client=FakeClient()
    result=collector.run_batch(["YPFD"],client,root=tmp_path,policy=policy())
    row=result["symbols"][0]
    assert client.calls[0]==("get_asset_quote",{"symbol":"YPFD","market":"BCBA","term":"t1"})
    assert row["quote"]["last"]==100
    assert row["state"]=="READY"

def test_metadata_is_not_recalled_on_second_quote_cycle(tmp_path):
    client=FakeClient()
    first=collector.run_batch(["YPFD"],client,root=tmp_path,policy=policy())
    second=collector.run_batch(["YPFD"],client,root=tmp_path,policy=policy())
    assert first["run_id"] != second["run_id"]
    assert [name for name,args in client.calls].count("get_asset_quote")==2
    assert [name for name,args in client.calls].count("get_asset_info")==1

def test_repeat_same_run_is_explicitly_resumable(tmp_path):
    client=FakeClient()
    collector.run_batch(["YPFD"],client,root=tmp_path,policy=policy(),run_id="resume",resume=True)
    collector.run_batch(["YPFD"],client,root=tmp_path,policy=policy(),run_id="resume",resume=True)
    assert len(client.calls)==2

def test_execution_tool_is_denied():
    try: collector._safe_call(FakeClient(),"place_order",{},collector.RateGovernor(policy()),policy())
    except PermissionError as exc: assert "IOL_SHADOW_TOOL_DENIED" in str(exc)
    else: raise AssertionError("execution tool was not denied")

def test_429_trips_circuit_and_does_not_fall_back_to_orders(tmp_path):
    class Limited(FakeClient):
        def call(self,tool_name,arguments):
            exc=RuntimeError("HTTP 429")
            exc.status_code=429
            raise exc
    governor=collector.RateGovernor(policy(),clock=lambda:0,sleep=lambda _:None,jitter=lambda:0)
    result=collector.run_batch(["YPFD"],Limited(),root=tmp_path,policy=policy(),governor=governor)
    assert result["symbols"][0]["state"]=="UNAVAILABLE"
    assert governor.open_until>0
    try: governor.acquire()
    except collector.CircuitOpenError: pass
    else: raise AssertionError("circuit should be open")

def test_observation_parser_accepts_trade_lot_price():
    assert observation._quote_summary({"trade":{"lot_price":123.4}})["last"]==123.4

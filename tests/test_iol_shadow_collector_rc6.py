from pathlib import Path

import iol_shadow_collector_rc6 as collector


class FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, tool_name, arguments):
        self.calls.append((tool_name, arguments["symbol"]))
        if tool_name == "get_asset_quote":
            return {"last": 100, "bid": 99, "ask": 101}
        return {"type": "ACCIONES", "currency": "ARS", "units_per_lot": 1}


def test_batch_is_observation_only_and_has_no_secret_output(tmp_path):
    client = FakeClient()
    result = collector.run_batch(["YPFD", "GGAL"], client, root=tmp_path,
                                 run_id="test-1", policy=collector.CollectionPolicy(min_interval_seconds=0))
    assert result["mode"] == "SHADOW"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["live_decision_authority"] is False
    assert result["real_money_authorized"] is False
    assert [row["symbol"] for row in result["symbols"]] == ["GGAL", "YPFD"]
    rendered = (tmp_path / "iol_shadow_checkpoint.json").read_text()
    assert "token" not in rendered.lower()
    assert "password" not in rendered.lower()


def test_same_run_resumes_idempotently_without_recalling_completed_symbols(tmp_path):
    client = FakeClient()
    policy = collector.CollectionPolicy(min_interval_seconds=0)
    collector.run_batch(["YPFD"], client, root=tmp_path, run_id="same-run", policy=policy)
    assert len(client.calls) == 2
    collector.run_batch(["YPFD"], client, root=tmp_path, run_id="same-run", policy=policy)
    assert len(client.calls) == 2


def test_only_read_only_market_tools_are_allowed():
    client = FakeClient()
    try:
        collector._safe_call(client, "place_order", {"symbol": "YPFD"})
    except PermissionError as exc:
        assert "IOL_SHADOW_TOOL_DENIED" in str(exc)
    else:
        raise AssertionError("an execution tool was not denied")


def test_checkpoint_is_written_after_each_symbol_even_if_one_fails(tmp_path):
    class PartialClient(FakeClient):
        def call(self, tool_name, arguments):
            if arguments["symbol"] == "YPFD":
                raise RuntimeError("temporary upstream failure")
            return super().call(tool_name, arguments)

    result = collector.run_batch(["YPFD", "GGAL"], PartialClient(), root=tmp_path,
                                 run_id="partial", policy=collector.CollectionPolicy(min_interval_seconds=0))
    assert {row["symbol"] for row in result["symbols"]} == {"YPFD", "GGAL"}
    assert next(row for row in result["symbols"] if row["symbol"] == "YPFD")["state"] == "UNAVAILABLE"
    assert Path(tmp_path, "iol_shadow_checkpoint.json").exists()

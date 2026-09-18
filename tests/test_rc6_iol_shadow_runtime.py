from pathlib import Path
from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP
from iol_shadow_collector_rc6 import CollectionPolicy
from scripts.rc6_iol_shadow_collect import _universe

def test_iol_collector_policy_is_bounded():
    policy=CollectionPolicy()
    policy.validate()
    assert policy.min_interval_seconds >= 1
    assert policy.max_calls_per_minute <= 40

def test_iol_adapter_denies_mutating_tools(tmp_path: Path):
    client=OAuthStoreReadOnlyMCP(tmp_path/"missing.json")
    try:
        client.call("place_order", {})
    except PermissionError as exc:
        assert "IOL_SHADOW_TOOL_DENIED" in str(exc)
    else:
        raise AssertionError("mutation unexpectedly admitted")

def test_default_universe_is_small_and_unique(monkeypatch):
    monkeypatch.delenv("POROTA_IOL_SHADOW_UNIVERSE", raising=False)
    universe=_universe()
    assert 1 <= len(universe) <= 25
    assert len(universe) == len(set(universe))


def test_systemd_collector_exposes_repository_root_to_python():
    service = (Path(__file__).parents[1] / "systemd" / "porota-iol-shadow-collector-rc6.service").read_text(encoding="utf-8")
    assert "Environment=PYTHONPATH=/opt/porota-trading" in service
    assert "rc6_iol_shadow_collect.py && /bin/chmod 0644 /opt/porota-trading/data/market/iol_shadow_latest.json" in service

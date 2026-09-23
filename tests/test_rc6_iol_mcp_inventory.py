from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rc6_iol_mcp_inventory as inventory


def test_inventory_is_read_only_and_does_not_call_tools():
    assert inventory.FORBIDDEN_WORDS
    source = Path(inventory.__file__).read_text(encoding="utf-8")
    assert "list_tools" in source
    assert "tools/call" not in source


def test_inventory_payload_is_explicitly_shadow(tmp_path, monkeypatch):
    output = tmp_path / "iol_mcp_tools_latest.json"
    monkeypatch.setattr(inventory, "ROOT", tmp_path)
    monkeypatch.setattr(inventory, "OUTPUT", output)
    inventory._atomic_write({"mode": "SHADOW", "decision_effect": "OBSERVE_ONLY", "real_money_authorized": False})
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mode"] == "SHADOW"
    assert payload["decision_effect"] == "OBSERVE_ONLY"
    assert payload["real_money_authorized"] is False

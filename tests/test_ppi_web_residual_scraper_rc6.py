import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "ppi_web_residual_scraper_rc6.py"
spec = importlib.util.spec_from_file_location("ppi_web_residual_scraper_rc6", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def write_jsonl(tmp_path, rows):
    path = tmp_path / "manifest.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def row(**overrides):
    base = {
        "symbol": "GGAL",
        "instrument_type": "ACCIONES",
        "market": "BYMA",
        "settlement": "A-48HS",
        "residual_class": "PARTIAL_VALID",
    }
    base.update(overrides)
    return base


def test_accepts_all_residual_classes(tmp_path):
    rows = [row(symbol=f"X{i}", residual_class=cls) for i, cls in enumerate(sorted(mod.ALLOWED_RESIDUAL_CLASSES))]
    loaded = mod.load_jsonl(write_jsonl(tmp_path, rows))
    assert len(loaded) == 4
    summary = mod.summarize(loaded)
    assert summary["mode"] == "READ_ONLY_SCAFFOLD"
    assert summary["mass_scraping_started"] is False
    assert summary["identities"] == 4


def test_rejects_missing_required_field(tmp_path):
    bad = row()
    del bad["market"]
    with pytest.raises(ValueError, match="missing required fields"):
        mod.load_jsonl(write_jsonl(tmp_path, [bad]))


def test_rejects_unknown_residual_class(tmp_path):
    with pytest.raises(ValueError, match="invalid residual_class"):
        mod.load_jsonl(write_jsonl(tmp_path, [row(residual_class="UNKNOWN")]))


def test_rejects_duplicate_identity(tmp_path):
    rows = [row(), row(residual_class="NO_PROVIDER_ROWS")]
    with pytest.raises(ValueError, match="duplicate residual identity"):
        mod.load_jsonl(write_jsonl(tmp_path, rows))


def test_summary_groups_family_and_class(tmp_path):
    rows = [
        row(symbol="A", instrument_type="ACCIONES", residual_class="PARTIAL_VALID"),
        row(symbol="B", instrument_type="BONOS", residual_class="NO_PROVIDER_ROWS"),
        row(symbol="C", instrument_type="BONOS", residual_class="HARD_PROVIDER_ERROR"),
    ]
    summary = mod.summarize(mod.load_jsonl(write_jsonl(tmp_path, rows)))
    assert summary["by_family"] == {"ACCIONES": 1, "BONOS": 2}
    assert summary["by_residual_class"] == {
        "HARD_PROVIDER_ERROR": 1,
        "NO_PROVIDER_ROWS": 1,
        "PARTIAL_VALID": 1,
    }


def test_source_has_no_web_or_order_execution_capability():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = [
        "requests.post(",
        "requests.put(",
        "requests.delete(",
        "place_order",
        "send_order",
        "buy_order",
        "sell_order",
        "selenium",
        "playwright",
    ]
    assert not [token for token in forbidden if token in source]

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "ppi_history_residual_manifest_rc6.py"
spec = importlib.util.spec_from_file_location("ppi_history_residual_manifest_rc6", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_classification_matrix():
    assert mod.classify_task("DONE_EMPTY", 0, 0) == "NO_PROVIDER_ROWS"
    assert mod.classify_task("DONE_EMPTY", 15, 0) == "PROVIDER_INVALID"
    assert mod.classify_task("DONE_PARTIAL", 20, 8) == "PARTIAL_VALID"
    assert mod.classify_task("ERROR", 0, 0) == "HARD_PROVIDER_ERROR"
    assert mod.classify_task("DONE_VALID", 20, 20) is None
    assert mod.classify_task("ALREADY_COVERED", 0, 0) is None


def test_build_manifest_keeps_only_residuals_and_normalizes_identity():
    rows = [
        (" al30 ", "bonos", "byma", "a-48hs", "DONE_EMPTY", 0, 0, "", ""),
        ("gd30", "BONOS", "BYMA", "A-48HS", "DONE_VALID", 245, 245, "", ""),
        ("x", "opciones", "byma", "inmediata", "DONE_PARTIAL", 30, 12, "", "partial"),
        ("y", "futuros", "rofex", "inmediata", "ERROR", 0, 0, "Instrument not found", ""),
    ]
    out = mod.build_manifest(rows)
    assert [x["residual_class"] for x in out] == ["NO_PROVIDER_ROWS", "PARTIAL_VALID", "HARD_PROVIDER_ERROR"]
    assert out[0]["symbol"] == "AL30"
    assert out[0]["instrument_type"] == "BONOS"


def test_build_manifest_rejects_duplicate_task_identity():
    rows = [
        ("AL30", "BONOS", "BYMA", "A-48HS", "DONE_EMPTY", 0, 0, "", ""),
        ("al30", "bonos", "byma", "a-48hs", "ERROR", 0, 0, "x", ""),
    ]
    try:
        mod.build_manifest(rows)
    except RuntimeError as exc:
        assert "duplicate task identity" in str(exc)
    else:
        raise AssertionError("duplicate was not rejected")

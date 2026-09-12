from pathlib import Path

import fo_eod_overnight_dashboard_rc6 as eod


def _replay():
    return {
        "state": "READY",
        "total": 24,
        "covered": 23,
        "missing": 1,
        "stop_first": 14,
        "target_first": 4,
        "neither": 5,
    }


def test_trading_estrategias_does_not_duplicate_scalping():
    text = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    start = text.index("if section=='estrategias':")
    end = text.index("families=families_for_group(section)", start)
    block = text[start:end]
    assert "_main_fragment(scalping_page())" not in block
    assert "render_eod_overnight()" in block
    assert "Scalping permanece exclusivamente en su menú principal." in block


def test_scalping_keeps_its_own_top_level_destination():
    text = Path("da_dashboard_ux_hf6.py").read_text(encoding="utf-8")
    assert 'NavItem("/scalping", "Scalping")' in text


def test_missing_readiness_fails_closed(monkeypatch):
    monkeypatch.setattr(eod, "_true_overnight_snapshot", _replay)
    monkeypatch.setattr(eod, "_readiness_state", lambda: {})
    page = eod.render()
    assert "EVALUATING" in page
    assert "CURRENT_EOD" in page
    assert "Auto-promoción" in page
    assert ">OFF<" in page
    assert "ACTIVE_PAPER" in page
    assert "23/24" in page


def test_all_green_means_ready_for_promotion_not_activation(monkeypatch):
    monkeypatch.setattr(eod, "_true_overnight_snapshot", _replay)
    monkeypatch.setattr(
        eod,
        "_readiness_state",
        lambda: {
            "all_gates_green": True,
            "readiness": "READY_FOR_PROMOTION",
            "gates": {},
        },
    )
    page = eod.render()
    assert "READY FOR PROMOTION" in page
    assert "CURRENT_EOD" in page
    assert ">OFF<" in page
    assert "ACTIVE_PAPER" in page
    assert "ACTIVE_OVERNIGHT" not in page


def test_eod_dashboard_module_is_read_only_by_contract():
    text = Path("fo_eod_overnight_dashboard_rc6.py").read_text(encoding="utf-8")
    assert "mode=ro" in text
    assert "PRAGMA query_only=ON" in text
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "REPLACE "):
        assert verb not in text

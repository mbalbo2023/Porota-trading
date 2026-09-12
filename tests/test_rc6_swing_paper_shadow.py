import ast
import inspect
import json

import fi_swing_paper_shadow_rc6 as swing


def _position(style="SWING_PAPER", *, asset_class="ACCIONES", mark="105"):
    return {
        "paper_id": "PAPER-TEST",
        "asset_class": asset_class,
        "stop_price": "95",
        "target_price": "120",
        "features_json": json.dumps({"execution_style": style}),
        "_mark": mark,
    }


def _evaluate(position=None, **overrides):
    position = position or _position()
    kwargs = dict(
        mark_price=position.get("_mark", "105"),
        quote_fresh=True,
        calendar_verified=True,
        next_session_verified=True,
        thesis_valid=True,
        holding_sessions=1,
        max_holding_sessions=5,
        gap_risk_fraction="0.02",
        max_gap_risk_fraction="0.04",
        underlying_calendar_verified=True,
        underlying_next_session_open=True,
    )
    kwargs.update(overrides)
    return swing.evaluate_overnight_shadow(position, **kwargs)


def test_explicit_swing_can_carry_only_in_shadow():
    verdict = _evaluate()
    assert verdict.action == "CARRY_OVERNIGHT"
    assert verdict.overnight_eligible is True
    assert verdict.mode == "SHADOW_ONLY"
    assert verdict.eod_exit_binding is False
    assert verdict.real_execution_allowed is False
    assert verdict.economics_model == "SWING_NON_INTRADAY"


def test_intraday_and_scalping_remain_force_flat_at_eod():
    for style in ("INTRADAY_PAPER", "SCALPING_PAPER"):
        verdict = _evaluate(_position(style))
        assert verdict.action == "FORCE_FLAT"
        assert verdict.reason == "INTRADAY_STYLE_EOD"
        assert verdict.overnight_eligible is False


def test_legacy_or_unclassified_position_is_never_reinterpreted_as_swing():
    position = _position("SWING_PAPER")
    position["features_json"] = "{}"
    verdict = _evaluate(position)
    assert verdict.action == "FAIL_CLOSED"
    assert verdict.reason == "SWING_STYLE_NOT_EXPLICIT"


def test_missing_calendar_or_stale_eod_mark_fails_closed():
    assert _evaluate(calendar_verified=False).reason == "NEXT_SESSION_CALENDAR_UNVERIFIED"
    assert _evaluate(next_session_verified=False).reason == "NEXT_SESSION_CALENDAR_UNVERIFIED"
    assert _evaluate(quote_fresh=False).reason == "EOD_MARK_NOT_FRESH"


def test_invalid_thesis_holding_limit_stop_target_and_gap_force_flat():
    assert _evaluate(thesis_valid=False).reason == "SWING_THESIS_INVALID"
    assert _evaluate(holding_sessions=5, max_holding_sessions=5).reason == \
        "SWING_MAX_HOLDING_SESSIONS_REACHED"
    assert _evaluate(mark_price="94").reason == "STOP_ALREADY_REACHED"
    assert _evaluate(mark_price="121").reason == "TARGET_ALREADY_REACHED"
    assert _evaluate(gap_risk_fraction="0.05", max_gap_risk_fraction="0.04").reason == \
        "OVERNIGHT_GAP_RISK_EXCEEDS_LIMIT"


def test_cedear_requires_verified_open_underlying_next_session():
    position = _position(asset_class="CEDEARS")
    unverified = _evaluate(position, underlying_calendar_verified=False)
    assert unverified.action == "FAIL_CLOSED"
    assert unverified.reason == "UNDERLYING_CALENDAR_UNVERIFIED"
    closed = _evaluate(position, underlying_next_session_open=False)
    assert closed.action == "FORCE_FLAT"
    assert closed.reason == "UNDERLYING_NEXT_SESSION_CLOSED"


def test_shadow_module_has_no_db_network_or_execution_surface():
    swing.assert_shadow_only()
    source = inspect.getsource(swing)
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    forbidden_imports = {"sqlite3", "requests", "httpx", "be_paper_engine", "bm_exit_supervisor"}
    assert not forbidden_imports.intersection(imports)
    for token in ("send_order", "place_order", "allocate_caucion", "/Operar"):
        assert token not in source

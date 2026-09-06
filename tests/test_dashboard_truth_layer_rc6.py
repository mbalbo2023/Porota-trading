import ep_dashboard_truth_layer_rc6 as truth


def test_closed_market_running_watchdog_is_not_presented_as_market_activity():
    assert truth.market_sensitive_display_state(
        "RUNNING", session_state="MARKET_CLOSED", open_positions=0
    ) == "EN_ESPERA_MERCADO_CERRADO"
    assert truth.market_sensitive_display_state(
        "RUNNING", session_state="WAITING_MARKET", open_positions=2
    ) == "MONITOREO_PASIVO"


def test_market_open_preserves_real_worker_state():
    assert truth.market_sensitive_display_state(
        "RUNNING", session_state="MARKET_OPEN", open_positions=0
    ) == "RUNNING"


def test_faults_are_never_hidden_by_closed_market_semantics():
    for raw in ("ERROR", "FAILED", "DEGRADED", "STALE", "UNKNOWN"):
        assert truth.market_sensitive_display_state(
            raw, session_state="MARKET_CLOSED", open_positions=0
        ) == raw


def test_binding_is_a_gate_policy_not_global_mode():
    text = truth.policy_description("BINDING")
    assert "señal PAPER" in text
    assert "no es el modo global" in text


def test_unknown_policy_is_explicit_not_silently_green():
    assert "requiere revisión" in truth.policy_description("MYSTERY")

from rc6_dynamic_universe.iol_coverage import (
    book_followup, coverage_feasibility, current_trade_receipts, plan_stream_coverage, stream_batches,
)


def key(index, family="ACCIONES"):
    return f"S{index:03d}", family, "BYMA", "ARS", "INMEDIATA"


BUDGET = {"scanner_batch_limit": 12, "scanner_calls_per_instrument": 3, "batches_per_minute": 1}


def test_one_stream_sweeps_540_exact_keys_without_consuming_deep_slots():
    keys = tuple(key(i, "ACCIONES" if i < 97 else "CEDEARS") for i in range(540))
    priority = keys[:24]
    seen = set(priority)
    previous = None
    for _ in range(20):
        plan = plan_stream_coverage(keys, priority, previous, slots=50, rest_budget=BUDGET)
        assert plan["eligible_by_family"] == {"ACCIONES": 97, "CEDEARS": 443}
        assert plan["priority"] == priority
        assert plan["nominal_discovery_cycles"] == 20
        assert plan["trade_connections_for_concurrent_coverage"] == 11
        assert tuple(map(len, stream_batches(plan, 25))) == (25, 25)
        assert plan["nominal_rest_round_seconds"] == 45 * 60
        dispatch = coverage_feasibility(plan, cadence_seconds=60, freshness_seconds=120,
                                        window_minutes=90, required_samples=6)
        assert dispatch["best_case_rotation_seconds"] == 20 * 60
        assert dispatch["rest_max_samples_per_key_in_signal_window"] == 2
        assert dispatch["rotation_within_freshness"] is False
        assert dispatch["rest_full_signal_window_possible"] is False
        seen.update(plan["discovery"])
        previous = {**plan, "prepared": True}
    assert seen == set(keys)


def test_cursor_retries_without_acknowledgment_and_survives_catalog_changes():
    keys = tuple(key(i) for i in range(1, 7))
    plan = plan_stream_coverage(keys, (), None, slots=2, rest_budget=BUDGET)
    retry = plan_stream_coverage(keys, (), plan, slots=2, rest_budget=BUDGET)
    assert retry["discovery"] == keys[:2]
    advanced = plan_stream_coverage((key(0), *keys[3:]), (), {**plan, "prepared": True},
                                    slots=2, rest_budget=BUDGET)
    assert advanced["discovery"] == keys[3:5]
    assert advanced["previous_targets"] == ()


def test_receipt_distinguishes_a_fresh_trade_from_waiting_and_future_clock():
    at = "2026-11-27T14:00:00+00:00"
    results = [
        {"identity": key(1), "state": "TRADE_OBSERVED", "trade_at": "2026-11-27T10:59:00-03:00"},
        {"identity": key(2), "state": "TRADE_OBSERVED", "trade_at": "2026-11-27T14:00:01+00:00"},
        {"identity": key(3), "state": "IOL_TRADE_SAMPLE_ABSENT"},
        {"identity": key(4), "state": "TRADE_OBSERVED", "trade_at": "2026-11-27T13:25:00"},
    ]
    statuses, receipt = current_trade_receipts(results, at=at, max_age_seconds=120)
    assert statuses == {
        key(1): "TRADE_FRESH", key(2): "TRADE_OUTSIDE_WINDOW", key(3): "IOL_TRADE_SAMPLE_ABSENT",
        key(4): "TRADE_CLOCK_UNVERIFIED",
    }
    assert receipt["by_state"]["TRADE_FRESH"] == 1
    assert receipt["by_family"]["ACCIONES"] == receipt["by_state"]
    assert len(receipt["per_identity"]) == 4


def test_scanner_polls_previous_slice_before_replacing_subscriptions(monkeypatch):
    import bf_production_paper_observer as observer

    keys = tuple(key(i) for i in range(6))
    previous = plan_stream_coverage(keys, (), None, slots=2, rest_budget=BUDGET)
    current = plan_stream_coverage(keys, (), {**previous, "prepared": True}, slots=2, rest_budget=BUDGET)
    monkeypatch.setattr(observer, "now_iso", lambda: "2026-11-27T14:00:00+00:00")
    calls = []

    class Reader:
        def trade_coverage(self, identities):
            calls.append(("observe", identities))
            return [{"identity": item, "state": "IOL_TRADE_SAMPLE_ABSENT"} for item in identities]

        def prepare_trades(self, identities):
            calls.append(("schedule", identities))
            return len(identities)

    states, followed = observer._advance_iol_discovery(Reader(), current)

    assert calls == [("observe", keys[:2]), ("schedule", keys[2:4])]
    assert current["prepared"] is True
    assert current["scheduled_subscriptions"] == 2
    assert current["observed_previous_slice"]["by_state"] == {"IOL_TRADE_SAMPLE_ABSENT": 2}
    assert set(states) == set(keys[:2])
    assert followed is None


def test_stream_trade_reuses_the_cold_rest_slot_without_opening_authority(monkeypatch):
    import bf_production_paper_observer as observer

    keys = tuple(key(i) for i in range(8))
    previous = plan_stream_coverage(keys, keys[:1], None, slots=3, rest_budget=BUDGET)
    current = plan_stream_coverage(keys, (keys[0], keys[3]), {**previous, "prepared": True},
                                   slots=3, rest_budget=BUDGET)
    pinned, cold, discovered = [(item[0], item[1], item[4]) for item in (keys[0], keys[3], keys[1])]
    plan = {"cold": [cold], "warm": [], "pinned": [pinned], "opening_identities": [pinned],
            "next_iol_subscriptions": [keys[0]], "read_scopes": [
                {"request": pinned, "identity": keys[0], "priority": "STRATEGY_HOT"},
                {"request": cold, "identity": keys[3], "priority": "DISCOVERY"}]}
    monkeypatch.setattr(observer, "now_iso", lambda: "2026-11-27T14:00:00+00:00")

    class Reader:
        def trade_coverage(self, identities):
            assert identities == keys[:3]
            return [{"identity": item, "state": "IOL_TRADE_SAMPLE_ABSENT"} for item in identities]

        def recent_trades(self, limit):
            assert limit == 25
            return [{"identity": keys[1], "state": "TRADE_OBSERVED", "trade_at": "2026-11-27T13:59:50+00:00",
                     "received_at": "2026-11-27T13:59:55+00:00"}]

        def prepare_trades(self, identities):
            assert identities == (keys[0], keys[1], keys[4])
            return len(identities)

    _, followed = observer._advance_iol_discovery(Reader(), current, active_plan=plan)

    assert followed == (cold, discovered)
    assert plan["read_scopes"][1] == {"request": discovered, "identity": keys[1], "priority": "DISCOVERY"}
    assert plan["opening_identities"] == [pinned]
    assert plan["cold"] == [discovered]
    assert current["rest_followed_trade"] == keys[1]
    assert current["recent_stream_activity"]["by_family"] == {"ACCIONES": {"TRADE_FRESH": 1}}
    book_at = "2026-11-27T13:59:59+00:00"
    cut = "2026-11-27T14:00:00+00:00"
    receipt = book_followup(current["followed_trade_received_at"], book_at, cut)
    assert receipt["trade_to_capture_seconds"] == 5
    assert book_followup("bad", book_at, cut)["trade_to_capture_seconds"] is None


def test_acknowledged_stream_shrinks_dispatch_interval_at_same_rest_rate(tmp_path, monkeypatch):
    import bf_production_paper_observer as observer
    from be_paper_engine import PaperStore

    keys = tuple(key(i) for i in range(540))
    store = PaperStore(tmp_path / "paper.sqlite")
    observer._support_schema(store)
    monkeypatch.setattr(observer.financial_catalog, "iol_spot_catalog", lambda *args, **kwargs: keys)
    monkeypatch.setattr(observer, "now_iso", lambda: "2026-11-27T14:00:00+00:00")
    monkeypatch.setattr(observer, "INTERVAL", 60)
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 20)
    governor = {"wire_budget": {"capacity_per_minute": 40, "reserved_exit": 2, "reserved_auth": 1}}

    slow = observer._cycle_plan(store, iol=True, iol_metrics=governor)[-1]
    fast = observer._cycle_plan(store, iol=True,
                                iol_metrics={**governor, "trade_coverage": {"continuous_subscribed": 540}})[-1]

    assert slow["cadence_seconds"] == 60 and slow["iol_rest_budget"]["scanner_batch_limit"] == 12
    assert fast["cadence_seconds"] == 30 and fast["iol_rest_budget"]["scanner_batch_limit"] == 6
    assert fast["iol_rest_budget"]["batches_per_minute"] == 2
    assert fast["iol_stream_coverage"]["rest_scanner_keys_per_minute"] == 12
    assert fast["iol_stream_coverage"]["dispatch"]["rest_full_signal_window_possible"] is False

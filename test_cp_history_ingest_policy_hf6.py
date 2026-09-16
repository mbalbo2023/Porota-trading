from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import cp_history_ingest_policy_hf6 as policy

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def row(day, *, o="100", h="110", l="90", c="105", v="10"):
    return {
        "date": f"2026-08-{day:02d}T17:00:00-03:00",
        "openingPrice": o,
        "max": h,
        "min": l,
        "price": c,
        "volume": v,
    }


def test_valid_rows_are_salvaged_without_inventing_bad_row():
    payload = [row(1), row(2, h="95"), row(3)]
    result = policy.validate_provider_history(
        payload, as_of=datetime(2026, 9, 2, 20, tzinfo=TZ)
    )
    assert result.provider_rows == 3
    assert result.valid_count == 2
    assert result.rejected_count == 1
    assert result.storage_quality == "VALID_ROWS_WITH_REJECTIONS"
    assert result.rejected_rows[0].reason == "OHLC_INCONSISTENT"
    assert [r["date"] for r in result.valid_rows] == [payload[0]["date"], payload[2]["date"]]


def test_duplicate_provider_date_is_rejected_not_overwritten():
    payload = [row(1), row(1, c="106")]
    result = policy.validate_provider_history(
        payload, as_of=datetime(2026, 9, 2, 20, tzinfo=TZ)
    )
    assert result.valid_count == 1
    assert result.rejected_rows[0].reason == "DUPLICATE_DATE"


def test_context_thresholds_do_not_equal_paper_readiness():
    payload = [row((i % 28) + 1) | {"date": f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}T17:00:00-03:00"}
               for i in range(90)]
    result = policy.validate_provider_history(
        payload, as_of=datetime(2026, 9, 2, 20, tzinfo=TZ)
    )
    assert result.context_state == "PREFERRED_CONTEXT"
    # Legacy history remains auditable, but no longer enters a new refresh.
    assert policy.history_collection_capability("BONOS", status="AVAILABLE") == "OUT_OF_SCOPE_READONLY_LEGACY"
    assert "READY_PAPER" not in policy.history_collection_capability("BONOS", status="AVAILABLE")


def test_non_operational_family_is_out_of_scope_before_new_history_collection():
    assert policy.history_collection_capability("OPCIONES", status="AVAILABLE", identity_complete=True) == "OUT_OF_SCOPE_READONLY_LEGACY"
    assert policy.history_collection_capability("FUTUROS", status="AVAILABLE", identity_complete=True) == "OUT_OF_SCOPE_READONLY_LEGACY"
    assert policy.history_collection_capability("BONOS", status="AVAILABLE", identity_complete=True) == "OUT_OF_SCOPE_READONLY_LEGACY"
    assert policy.history_collection_capability("BONOS", status="AVAILABLE", identity_complete=False) == "HOLD_IDENTITY_INCOMPLETE"


def test_specialized_legacy_families_are_out_of_scope_for_new_collection():
    assert policy.history_collection_capability("CAUCIONES", status="AVAILABLE") == "OUT_OF_SCOPE_READONLY_LEGACY"
    assert policy.history_collection_capability("FCI", status="AVAILABLE") == "OUT_OF_SCOPE_READONLY_LEGACY"
    assert policy.history_collection_capability("LICITACIONES", status="AVAILABLE") == "OUT_OF_SCOPE_READONLY_LEGACY"


def test_retry_backoff_is_bounded():
    assert policy.retry_delay("ERROR", 1) == timedelta(minutes=15)
    assert policy.retry_delay("EMPTY_OR_INVALID", 1) == timedelta(hours=2)
    assert policy.retry_delay("VALID_PAYLOAD", 9) == timedelta(hours=24)
    assert policy.retry_delay("PARTIAL", 99) <= timedelta(hours=24)


def test_rank_prioritizes_missing_context_over_strong_context():
    now = datetime(2026, 9, 2, 20, tzinfo=TZ)
    missing = policy.rank_history_target(
        context_state="NO_VALID_HISTORY", latest_status="ERROR",
        last_attempt_at=now - timedelta(hours=3), attempt_count=2, now=now,
    )
    strong = policy.rank_history_target(
        context_state="STRONG_CONTEXT", latest_status="VALID_PAYLOAD",
        last_attempt_at=now - timedelta(days=2), attempt_count=1, now=now,
    )
    assert missing < strong
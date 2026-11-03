"""A175's exact A174 predecessor cannot launder PAPER fee authority."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from scripts import rc6_caucion_fee_restatement_apply as apply
from scripts import rc6_caucion_fee_restatement_continuity as continuity


MODE = "CLOSED_A174_PENDING_FIX_FORWARD"
CANDIDATE = "c" * 40


def receipt():
    return {
        "candidate_sha": CANDIDATE,
        "status": "FIX_FORWARD_VALIDATION_REQUIRED",
        "installation_started": True,
        "real_orders_sent": 0,
        "ppi_watch_accessed": False,
        "market_open_suspended": False,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "inflight": "ABSENT_OWNED_RUNTIME_STOPPED",
        "a174_pending_adopted": True,
        "a174_origin_candidate": continuity.A174_FAILED_CANDIDATE,
        "a174_origin_sha256": continuity.A174_FAILED_RECEIPT_SHA256,
        "a174_previous_proof": {
            "origin_candidate": continuity.A174_FAILED_CANDIDATE,
            "origin_sha256": continuity.A174_FAILED_RECEIPT_SHA256,
            "image_id": continuity.A174_LOADED_IMAGE_ID,
            "marker_sha256": continuity.A174_MARKER_SHA256,
            "frozen_sha256": continuity.A174_FROZEN_SHA256,
            "flat_book": {"process_state": "WAITING_MARKET",
                          "session_state": "MARKET_CLOSED", "real_orders_sent": 0},
            "failure_flat_book": {"process_state": "DEGRADED",
                                  "session_state": "CLOSED", "real_orders_sent": 0},
            "containers": {name: {"id": str(i) * 64,
                                  "image": continuity.A174_LOADED_IMAGE_ID}
                           for i, name in enumerate(sorted(continuity.OWNED), 1)},
            "operator_receipt_sha256": "d" * 64,
        },
    }


def test_a174_mode_requires_exact_failed_predecessor_and_intraday_literal():
    assert MODE in apply.INTRADAY_MODES and MODE in continuity.INTRADAY_MODES
    assert continuity.validate_prior_receipt(receipt(), CANDIDATE, MODE).tzinfo


@pytest.mark.parametrize("field,value", [
    ("a174_pending_adopted", False),
    ("a174_origin_sha256", "0" * 64),
    ("inflight", "UNKNOWN"),
])
def test_a174_origin_tamper_fails_closed(field, value):
    row = receipt()
    row[field] = value
    with pytest.raises(ValueError, match="PAPER_RESTATEMENT_A174_ORIGIN_RED"):
        continuity.validate_prior_receipt(row, CANDIDATE, MODE)


def test_a174_historical_failure_cannot_be_rewritten_as_healthy():
    row = deepcopy(receipt())
    row["a174_previous_proof"]["failure_flat_book"]["process_state"] = "WAITING_MARKET"
    with pytest.raises(ValueError, match="PAPER_RESTATEMENT_A174_ORIGIN_RED"):
        continuity.validate_prior_receipt(row, CANDIDATE, MODE)

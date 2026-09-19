import json
from decimal import Decimal

from be_paper_engine import PaperStore, Quote


def quote():
    return Quote(
        symbol="GGAL", asset_class="ACCIONES", settlement="A-24HS",
        last=Decimal("100"), bid=Decimal("99"), ask=Decimal("101"),
        bid_size=Decimal("10"), ask_size=Decimal("12"),
        observed_at="2026-09-19T14:00:00+00:00",
        currency="ARS", market="BYMA", book_at="2026-09-19T14:00:00+00:00",
        trade_at="2026-09-19T14:00:00+00:00", last_kind="TRADE",
    )


def test_gate_evidence_is_contemporaneous_redacted_and_immutable(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    first = {
        "score": "0.70",
        "source_state": {"iol": "USED", "ppi": "USED"},
        "api_token": "must-not-persist",
    }
    store.record_gates(
        quote(), "decision-1", "APPROVE", "NOT_USED", "APPROVE",
        "OPENED_SIMULATED", "fixture", paper_id="PAPER-1", detail=first,
    )
    with store.connect() as c:
        row = c.execute(
            "SELECT captured_at,schema_version,payload_sha256,payload_json "
            "FROM decision_evidence_snapshots WHERE decision_key='decision-1'"
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert row["captured_at"] == quote().observed_at
    assert row["schema_version"] == "rc6.decision-inputs.v1"
    assert len(row["payload_sha256"]) == 64
    assert payload["quote_used"]["ask"] == "101"
    assert payload["inputs_used"]["api_token"] == "[REDACTED]"
    assert payload["runtime"]["real_money_authorized"] is False

    # The mutable gate projection may be refreshed on retry, but its first
    # contemporaneous evidence record is append-only.
    store.record_gates(
        quote(), "decision-1", "APPROVE", "NOT_USED", "BLOCKED",
        "BLOCKED", "retry", detail={"score": "0.01"},
    )
    with store.connect() as c:
        after = c.execute(
            "SELECT payload_json FROM decision_evidence_snapshots WHERE decision_key='decision-1'"
        ).fetchone()["payload_json"]
    assert after == row["payload_json"]


def test_evidence_snapshot_does_not_need_a_paper_position(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    store.record_gates(
        quote(), "decision-blocked", "APPROVE", "VETO", "NOT_EVALUATED",
        "BLOCKED", "blocked before simulated position", detail={"samples": 8},
    )
    with store.connect() as c:
        count = c.execute("SELECT COUNT(*) FROM decision_evidence_snapshots").fetchone()[0]
    assert count == 1

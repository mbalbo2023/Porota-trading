"""A199: diagnostic evidence is bounded and never authorizes a Scalping fill.

The provider's published Intraday JSON schema does not establish what its
``volume`` unit or accumulation convention means. Rejected closed revisions
must stay rejected while future private analysis gets one-way causal evidence.
"""
from datetime import timedelta

from tests.test_intraday_scalping_rc6_integration import START, make_store, record
import cf_intraday_scalping as scalping


def _at(minute):
    return (START + timedelta(minutes=minute)).isoformat()


def _points(price="100", volume="20", *, minute=0):
    return [(_at(minute), price, volume)]


def _rows(store):
    with store.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM ppi_intraday_revision_forensics ORDER BY session_day,symbol,event_at,incoming_sha256")]


def test_real_closed_revision_is_sampled_without_replacing_baseline_or_relaxing_hold(tmp_path):
    store, instrument = make_store(tmp_path), record()
    scalping.persist_payload(store, instrument, _points(), received_at=_at(10))
    changed = scalping.persist_payload(store, instrument,
        _points("100.05", "21"), received_at=_at(11))
    assert changed["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"
    assert changed["changed"] == changed["revision_forensics_sampled"] == 1
    assert changed["revision_forensics_capped"] == 0
    forensic = _rows(store)[0]
    assert forensic["policy_schema"] == scalping.INTRADAY_REVISION_FORENSICS_SCHEMA
    assert forensic["price_changed"] == forensic["volume_changed"] == 1
    assert forensic["age_seconds"] == "660.0"
    assert len(forensic["baseline_sha256"]) == len(forensic["incoming_sha256"]) == 64
    assert forensic["baseline_sha256"] != forensic["incoming_sha256"]
    with store.connect() as conn:
        stored = conn.execute("SELECT price,volume FROM ppi_intraday_points").fetchone()
        state = conn.execute("SELECT state,changed_closed_points FROM ppi_intraday_contract_state").fetchone()
        names = {row[1] for row in conn.execute("PRAGMA table_info(ppi_intraday_revision_forensics)")}
    assert tuple(stored) == ("100", "20")  # baseline never rewritten
    assert tuple(state) == ("REJECTED_MUTABLE_CLOSED_POINTS", 1)
    assert {"old_price", "new_price", "old_volume", "new_volume", "payload_json"}.isdisjoint(names)


def test_same_economic_number_with_different_json_format_creates_no_forensic(tmp_path):
    store, instrument = make_store(tmp_path), record()
    scalping.persist_payload(store, instrument, _points(), received_at=_at(10))
    equal = scalping.persist_payload(store, instrument,
        _points("1e2", "20.000"), received_at=_at(11))
    assert equal["changed"] == 0
    assert equal["revision_forensics_sampled"] == 0
    assert _rows(store) == []
    assert scalping._revision_digest("100", "20") == scalping._revision_digest("100.0", "2e1")
    assert scalping._revision_digest("-0.0", "0") == scalping._revision_digest("0", "0.000")


def test_repeat_negative_read_is_idempotent_and_remains_hold(tmp_path):
    store, instrument = make_store(tmp_path), record()
    scalping.persist_payload(store, instrument, _points(), received_at=_at(10))
    first = scalping.persist_payload(store, instrument,
        _points("100.05", "20"), received_at=_at(11))
    second = scalping.persist_payload(store, instrument,
        _points("100.05", "20"), received_at=_at(12))
    assert first["revision_forensics_sampled"] == 1
    assert second["revision_forensics_sampled"] == 0
    assert second["changed"] == 1
    assert second["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"
    assert len(_rows(store)) == 1
    assert _rows(store)[0]["price_changed"] == 1
    assert _rows(store)[0]["volume_changed"] == 0


def test_per_identity_budget_is_hard_bound_without_changing_rejection(tmp_path):
    store, instrument = make_store(tmp_path), record()
    scalping.persist_payload(store, instrument, _points(), received_at=_at(10))
    outcomes = [scalping.persist_payload(store, instrument,
        _points(str(101 + i), "20"), received_at=_at(11 + i)) for i in range(7)]
    assert sum(item["revision_forensics_sampled"] for item in outcomes) == 4
    assert sum(item["revision_forensics_capped"] for item in outcomes) == 3
    assert all(item["state"] == "REJECTED_MUTABLE_CLOSED_POINTS" for item in outcomes)
    assert len(_rows(store)) == scalping.INTRADAY_REVISION_FORENSICS_MAX_IDENTITY_DAILY


def test_global_daily_budget_is_hard_bound_across_many_instruments(tmp_path):
    store = make_store(tmp_path)
    for i in range(68):
        instrument = dict(record(), ticker=f"SAMPLE{i:03d}")
        scalping.persist_payload(store, instrument, _points(), received_at=_at(10))
        outcome = scalping.persist_payload(store, instrument,
            _points("101", "20"), received_at=_at(11))
        assert outcome["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"
        assert outcome["revision_forensics_sampled"] == int(i < 64)
        assert outcome["revision_forensics_capped"] == int(i >= 64)
    assert len(_rows(store)) == scalping.INTRADAY_REVISION_FORENSICS_MAX_DAILY


def test_no_authentic_volume_contract_stays_fail_closed_without_forensic(tmp_path):
    store, instrument = make_store(tmp_path), record()
    instrument.pop("raw", None)
    instrument.pop("metadata_json", None)
    first = scalping.persist_payload(store, instrument, _points(), received_at=_at(10))
    second = scalping.persist_payload(store, instrument, _points(), received_at=_at(11))
    assert first["state"] == second["state"] == "PPI_VOLUME_CONTRACT_UNVERIFIED"
    assert _rows(store) == []

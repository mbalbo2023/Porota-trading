from datetime import datetime, timedelta
import json

from be_paper_engine import PaperStore
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping


START = datetime.fromisoformat("2026-09-07T10:30:00-03:00")


def _record():
    return dict(
        ticker="GGAL", instrument_type="ACCIONES", market="BYMA", currency="ARS",
        settlement="A-24HS", capability="READY_PAPER_SPOT", status="AVAILABLE",
    )


def _payload(count, *, changed_index=None, price_delta=0, volume_delta=0):
    rows = []
    for i in range(count):
        price = 100 + i / 10
        volume = 20 if i % 2 == 0 else 10
        if changed_index == i:
            price += price_delta
            volume += volume_delta
        rows.append({
            "date": (START + timedelta(minutes=i)).isoformat(),
            "price": price,
            "volume": volume,
        })
    return rows


def _store(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    catalog.init_schema(store)
    scalping.init_schema(store)
    return store


def _events(store):
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT detail FROM paper_events "
            "WHERE event_type='SCALPING_INTRADAY_REVISION_TELEMETRY' ORDER BY id"
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def test_mutable_revision_emits_age_without_changing_refresh_verdict(tmp_path):
    store = _store(tmp_path)
    record = _record()
    first = scalping.normalize_payload(
        _payload(10), received_at="2026-09-07T10:40:00-03:00")
    scalping.persist_payload(
        store, record, first, received_at="2026-09-07T10:40:00-03:00")

    revised = scalping.normalize_payload(
        _payload(10, changed_index=9, price_delta=0.05, volume_delta=1),
        received_at="2026-09-07T10:40:30-03:00")
    result = scalping.persist_payload(
        store, record, revised, received_at="2026-09-07T10:40:30-03:00")

    assert result["changed"] == 0
    assert result["refreshed"] == 1
    assert result["revision_telemetry_events"] == 1
    events = _events(store)
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "REFRESH_MUTABLE"
    assert event["age_seconds"] == 90.0
    assert event["mutable_seconds"] == 120
    assert event["price_changed"] is True
    assert event["volume_changed"] is True
    assert event["source"] == "PPI_MARKETDATA_INTRADAY"


def test_closed_revision_emits_age_and_remains_fail_closed(tmp_path):
    store = _store(tmp_path)
    record = _record()
    first = scalping.normalize_payload(
        _payload(10), received_at="2026-09-07T10:40:00-03:00")
    scalping.persist_payload(
        store, record, first, received_at="2026-09-07T10:40:00-03:00")

    revised = scalping.normalize_payload(
        _payload(11, changed_index=0, price_delta=0.05),
        received_at="2026-09-07T10:41:00-03:00")
    result = scalping.persist_payload(
        store, record, revised, received_at="2026-09-07T10:41:00-03:00")

    assert result["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"
    assert result["changed"] == 1
    events = _events(store)
    assert len(events) == 1
    assert events[0]["action"] == "REJECT_CLOSED_REVISION"
    assert events[0]["age_seconds"] == 660.0
    assert events[0]["price_changed"] is True


def test_telemetry_failure_cannot_change_contract_verdict(tmp_path):
    store = _store(tmp_path)
    record = _record()
    first = scalping.normalize_payload(
        _payload(10), received_at="2026-09-07T10:40:00-03:00")
    scalping.persist_payload(
        store, record, first, received_at="2026-09-07T10:40:00-03:00")

    def broken_event(*args, **kwargs):
        raise RuntimeError("TELEMETRY_STORAGE_FAILURE")

    store.event = broken_event
    revised = scalping.normalize_payload(
        _payload(11, changed_index=0, price_delta=0.05),
        received_at="2026-09-07T10:41:00-03:00")
    result = scalping.persist_payload(
        store, record, revised, received_at="2026-09-07T10:41:00-03:00")

    assert result["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"
    assert result["changed"] == 1
    assert result["revision_telemetry_events"] == 0

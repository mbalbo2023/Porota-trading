"""A1115: repeated fresh futures Book must not suppress later PAPER exits."""

from dataclasses import replace

from test_rc6_future_paper_engine import broker, quote
from rc6_paper_family_lifecycle import future_positions


def test_fresh_repeated_book_does_not_suppress_max_hold_exit(tmp_path, monkeypatch):
    """A second observation of an unchanged Book still advances exit supervision."""
    monkeypatch.setenv("PAPER_MAX_HOLD_MINUTES", "2")
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = broker(tmp_path, clock)
    opened, _, lifecycle_id = paper._open_future(quote(clock[0]), 0.8, {"samples": 8})
    assert opened

    clock[0] = "2026-10-05T12:01:00-03:00"
    paper._on_future_quote(quote(clock[0], bid="1499", ask="1500"))
    assert len(future_positions(paper.store, "ARS", active_only=True)) == 1

    clock[0] = "2026-10-05T12:02:00-03:00"
    repeated = replace(
        quote(clock[0], bid="1499", ask="1500"),
        book_at="2026-10-05T12:01:00-03:00",
    )
    paper._on_future_quote(repeated)

    assert future_positions(paper.store, "ARS", active_only=True) == []
    with paper.store.connect() as connection:
        row = connection.execute(
            "SELECT state,cause FROM paper_future_exit_intents WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()
    assert tuple(row) == ("CLOSED", "MAX_HOLD_PAPER")


def test_reused_book_identity_with_changed_price_cannot_replay_mark(tmp_path, monkeypatch):
    """The replay branch must not authorize a different economic mark."""
    monkeypatch.setenv("PAPER_MAX_HOLD_MINUTES", "2")
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = broker(tmp_path, clock)
    opened, _, lifecycle_id = paper._open_future(quote(clock[0]), 0.8, {"samples": 8})
    assert opened
    clock[0] = "2026-10-05T12:01:00-03:00"
    paper._on_future_quote(quote(clock[0], bid="1499", ask="1500"))

    clock[0] = "2026-10-05T12:02:00-03:00"
    contradictory = replace(
        quote(clock[0], bid="1498", ask="1500"),
        book_at="2026-10-05T12:01:00-03:00",
    )
    paper._on_future_quote(contradictory)

    assert len(future_positions(paper.store, "ARS", active_only=True)) == 1
    with paper.store.connect() as connection:
        row = connection.execute(
            "SELECT state,cause FROM paper_future_exit_intents WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()
        marks = connection.execute(
            "SELECT COUNT(*) FROM paper_future_marks WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()[0]
    assert tuple(row) == ("EXIT_PENDING_INVALID_MARK", "MAX_HOLD_PAPER")
    assert marks == 1


def test_reused_book_stale_at_new_observation_fails_closed(tmp_path, monkeypatch):
    """Idempotence must not grant a fresh price to an expired Book snapshot."""
    monkeypatch.setenv("PAPER_MAX_HOLD_MINUTES", "2")
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = broker(tmp_path, clock)
    opened, _, lifecycle_id = paper._open_future(quote(clock[0]), 0.8, {"samples": 8})
    assert opened
    clock[0] = "2026-10-05T12:01:00-03:00"
    paper._on_future_quote(quote(clock[0], bid="1499", ask="1500"))

    clock[0] = "2026-10-05T12:04:00-03:00"
    stale = replace(quote(clock[0], bid="1499", ask="1500"),
                    book_at="2026-10-05T12:01:00-03:00")
    paper._on_future_quote(stale)

    assert len(future_positions(paper.store, "ARS", active_only=True)) == 1
    with paper.store.connect() as connection:
        row = connection.execute(
            "SELECT state,cause FROM paper_future_exit_intents WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()
    assert tuple(row) == ("EXIT_PENDING_STALE_QUOTE", "MAX_HOLD_PAPER")


def test_repeated_book_retries_eod_close_after_committed_paper_variation(tmp_path, monkeypatch):
    """A transient CLOSE failure after EOD variation must not strand an active future."""
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = broker(tmp_path, clock)
    opened, _, lifecycle_id = paper._open_future(quote(clock[0]), 0.8, {"samples": 8})
    assert opened

    original_callback = paper._future_quote_callback
    reject_close_once = {"pending": True}

    def transient_close_failure(q, lc_id, event_id, phase, at):
        if phase == "CLOSE" and reject_close_once["pending"]:
            reject_close_once["pending"] = False
            raise ValueError("TRANSIENT_CLOSE_EVIDENCE_UNAVAILABLE")
        return original_callback(q, lc_id, event_id, phase, at)

    monkeypatch.setattr(paper, "_future_quote_callback", transient_close_failure)
    clock[0] = "2026-10-05T14:50:00-03:00"
    first_book = quote(clock[0], bid="1520", ask="1521")
    paper._on_future_quote(first_book)

    assert len(future_positions(paper.store, "ARS", active_only=True)) == 1
    with paper.store.connect() as connection:
        settlement_count = connection.execute(
            "SELECT COUNT(*) FROM paper_future_marks WHERE lifecycle_id=? AND is_settlement=1",
            (lifecycle_id,),
        ).fetchone()[0]
    assert settlement_count == 1

    clock[0] = "2026-10-05T14:51:00-03:00"
    repeated = replace(quote(clock[0], bid="1520", ask="1521"), book_at=first_book.book_at)
    paper._on_future_quote(repeated)

    assert future_positions(paper.store, "ARS", active_only=True) == []
    with paper.store.connect() as connection:
        settlement_count = connection.execute(
            "SELECT COUNT(*) FROM paper_future_marks WHERE lifecycle_id=? AND is_settlement=1",
            (lifecycle_id,),
        ).fetchone()[0]
        exit_intent = connection.execute(
            "SELECT state,cause,attempts FROM paper_future_exit_intents WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()
    assert settlement_count == 1
    assert tuple(exit_intent) == ("CLOSED", "EOD_PAPER", 2)


def test_repeated_fresh_book_retries_paper_stop_after_close_failure(tmp_path, monkeypatch):
    """A STOP stays actionable on a fresh replay when CLOSE evidence failed once."""
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = broker(tmp_path, clock)
    opened, _, lifecycle_id = paper._open_future(quote(clock[0]), 0.8, {"samples": 8})
    assert opened

    original_callback = paper._future_quote_callback
    rejected = {"once": True}

    def fail_first_close(q, lc_id, event_id, phase, at):
        if phase == "CLOSE" and rejected["once"]:
            rejected["once"] = False
            raise ValueError("TEMPORARY_CLOSE_RECEIPT_FAILURE")
        return original_callback(q, lc_id, event_id, phase, at)

    monkeypatch.setattr(paper, "_future_quote_callback", fail_first_close)
    clock[0] = "2026-10-05T12:01:00-03:00"
    stop_book = quote(clock[0], bid="1460", ask="1461")
    paper._on_future_quote(stop_book)
    assert len(future_positions(paper.store, "ARS", active_only=True)) == 1

    clock[0] = "2026-10-05T12:02:00-03:00"
    paper._on_future_quote(replace(
        quote(clock[0], bid="1460", ask="1461"), book_at=stop_book.book_at))

    assert future_positions(paper.store, "ARS", active_only=True) == []
    with paper.store.connect() as connection:
        exit_intent = connection.execute(
            "SELECT state,cause,attempts FROM paper_future_exit_intents WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()
    assert tuple(exit_intent) == ("CLOSED", "STOP_PAPER", 2)


def test_expiry_close_uses_one_paper_settlement_after_repeated_book(tmp_path, monkeypatch):
    """Expiry retry may reuse a prior variation, never book a second PnL cut."""
    clock = ["2026-10-30T12:00:00-03:00"]
    paper = broker(tmp_path, clock)
    opened, _, lifecycle_id = paper._open_future(quote(clock[0]), 0.8, {"samples": 8})
    assert opened

    original_callback = paper._future_quote_callback
    blocked = {"once": True}

    def fail_first_close(q, lc_id, event_id, phase, at):
        if phase == "CLOSE" and blocked["once"]:
            blocked["once"] = False
            raise ValueError("TEMPORARY_EXPIRY_CLOSE_FAILURE")
        return original_callback(q, lc_id, event_id, phase, at)

    monkeypatch.setattr(paper, "_future_quote_callback", fail_first_close)
    clock[0] = "2026-10-30T15:00:00-03:00"
    expiry_book = quote(clock[0], bid="1515", ask="1516")
    paper._on_future_quote(expiry_book)
    assert len(future_positions(paper.store, "ARS", active_only=True)) == 1

    clock[0] = "2026-10-30T15:01:00-03:00"
    paper._on_future_quote(replace(
        quote(clock[0], bid="1515", ask="1516"), book_at=expiry_book.book_at))
    assert future_positions(paper.store, "ARS", active_only=True) == []
    with paper.store.connect() as connection:
        marks = connection.execute(
            "SELECT COUNT(*) FROM paper_future_marks WHERE lifecycle_id=? AND is_settlement=1",
            (lifecycle_id,),
        ).fetchone()[0]
        exit_row = connection.execute(
            "SELECT state,cause,attempts FROM paper_future_exit_intents WHERE lifecycle_id=?",
            (lifecycle_id,),
        ).fetchone()
    assert marks == 1
    assert tuple(exit_row) == ("CLOSED", "EXPIRY", 2)

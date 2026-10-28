"""No provider calls: durable, release-bound PAPER preopen admission."""
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperBroker, PaperStore, Quote, D
import rc6_paper_admission_gate as gate
from tests.rc6_convergence_fixtures import with_synthetic_volume_contract

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SHA = "a" * 40
DAY = datetime(2026, 10, 23, 10, 21, tzinfo=TZ)


def state(path, *, candidate=SHA):
    path.write_text(json.dumps({"candidate_sha": candidate, "validation_status": "VALIDATED_RUNTIME",
                                "mode": "PRODUCTION_PAPER", "real_orders_sent": 0}))


def result(*, green=True, focus=True):
    return {"status": "GREEN" if green else "RED", "phase": "T_MINUS_10",
            "checks": {"paper_focus_contracts": {"state": "GREEN" if focus else "RED"}}}


def test_persisted_gate_is_fail_closed_across_restart_and_release(tmp_path):
    release, receipt = tmp_path / "release.json", tmp_path / "gate.json"
    state(release)
    assert gate.admission_status(now=DAY, candidate_sha=SHA, receipt_path=receipt)[0] is False
    gate.publish_preopen(result(green=False), now=DAY, state_path=release, receipt_path=receipt)
    assert gate.admission_status(now=DAY, candidate_sha=SHA, receipt_path=receipt)[0] is False
    gate.publish_preopen(result(focus=False), now=DAY, state_path=release, receipt_path=receipt)
    assert gate.admission_status(now=DAY, candidate_sha=SHA, receipt_path=receipt)[0] is False
    issued = gate.publish_preopen(result(), now=DAY, state_path=release, receipt_path=receipt)
    assert issued["status"] == "GO_PAPER_SCALPING"
    assert gate.admission_status(now=DAY + timedelta(hours=1), candidate_sha=SHA, receipt_path=receipt) == (True, "")
    for date, candidate in [(DAY + timedelta(days=1), SHA), (DAY, "b" * 40)]:
        assert gate.admission_status(now=date, candidate_sha=candidate, receipt_path=receipt)[0] is False
    receipt.write_text('{"status":"GO_PAPER_SCALPING"}')
    assert gate.admission_status(now=DAY, candidate_sha=SHA, receipt_path=receipt)[0] is False


def test_only_real_t_minus_ten_can_publish_go(tmp_path):
    release, receipt = tmp_path / "release.json", tmp_path / "gate.json"
    state(release)
    for at in [DAY.replace(hour=9, minute=45), DAY.replace(hour=10, minute=31)]:
        gate.publish_preopen(result(), now=at, state_path=release, receipt_path=receipt)
        assert gate.admission_status(now=at, candidate_sha=SHA, receipt_path=receipt)[0] is False
    state(release, candidate="not-a-sha")
    gate.publish_preopen(result(), now=DAY, state_path=release, receipt_path=receipt)
    assert gate.admission_status(now=DAY, candidate_sha=SHA, receipt_path=receipt)[0] is False


@pytest.mark.parametrize("paused_day", [27, 28])
def test_operator_pause_vetoes_even_a_valid_go_for_the_argentine_day(tmp_path, paused_day):
    release, receipt = tmp_path / "release.json", tmp_path / "gate.json"
    state(release)
    day = DAY.replace(day=paused_day)
    reason = f"OPERATOR_PAPER_PAUSE_2026_10_{paused_day:02d}"
    issued = gate.publish_preopen(result(), now=day, state_path=release, receipt_path=receipt)
    assert issued["status"] == "NO_GO_PAPER_SCALPING"
    assert issued["reason"] == reason
    # Even a pre-existing or externally written valid-looking GO cannot bypass
    # the independent admission veto inside the PAPER write transaction.
    receipt.write_text(json.dumps({**issued, "status": "GO_PAPER_SCALPING"}))
    for at in (day, day + timedelta(hours=1), day.replace(hour=23, minute=59)):
        assert gate.admission_status(now=at, candidate_sha=SHA, receipt_path=receipt) == (
            False, reason)
    # 02:59 UTC is still the prior Argentine trading day.
    assert gate.admission_status(now=datetime(2026, 10, paused_day + 1, 2, 59,
                                              tzinfo=ZoneInfo("UTC")),
                                 candidate_sha=SHA, receipt_path=receipt) == (False, reason)
    assert gate.admission_status(now=day + timedelta(days=1), candidate_sha=SHA,
                                 receipt_path=receipt)[0] is False  # prior-day GO expires


def test_operator_pause_persists_across_candidate_release_validation_and_paper_insert(tmp_path,
                                                                                       monkeypatch):
    at = DAY.replace(day=28)

    class OperatorDayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz or TZ)

    release, receipt = tmp_path / "release.json", tmp_path / "gate.json"
    monkeypatch.setattr(gate, "datetime", OperatorDayClock)
    monkeypatch.setattr(gate, "RUNTIME_RECEIPT", receipt)
    monkeypatch.setenv("POROTA_BUILD_SHA", SHA)
    for marker in ("DEPLOYED_VALIDATION_PENDING", "VALIDATED_RUNTIME"):
        state(release)
        data = json.loads(release.read_text())
        data["validation_status"] = marker
        release.write_text(json.dumps(data))
        issued = gate.publish_preopen(result(), now=at, state_path=release, receipt_path=receipt)
        assert issued["status"] == "NO_GO_PAPER_SCALPING"
        assert issued["reason"] == "OPERATOR_PAPER_PAUSE_2026_10_28"
        # A tampered valid-looking T-10 GO cannot defeat the independent veto.
        receipt.write_text(json.dumps({**issued, "status": "GO_PAPER_SCALPING",
                                       "candidate_sha": SHA, "phase": "T_MINUS_10"}))
        assert gate.admission_status(now=at, candidate_sha=SHA, receipt_path=receipt) == (
            False, "OPERATOR_PAPER_PAUSE_2026_10_28")
        db_path = tmp_path / f"paper-{marker}.db"
        monkeypatch.setenv("PAPER_V17_DB_PATH", str(db_path))
        store = PaperStore(str(db_path))
        quote = Quote("GGAL", "ACCIONES", "A-24HS", D("100"), D("99"), D("101"),
                      D("100"), D("100"), at.isoformat(), currency="ARS", market="BYMA")
        broker = PaperBroker(store, initial_cash="10000", admission_guard=lambda *_: (True, ""))
        assert broker._open(quote, D("0.8"), {})[:2] == (
            False, "OPERATOR_PAPER_PAUSE_2026_10_28")
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
            assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_operator_pause_expires_without_automatically_authorizing_29_october(tmp_path):
    release, receipt = tmp_path / "release.json", tmp_path / "gate.json"
    state(release)
    paused = DAY.replace(day=28)
    following = DAY.replace(day=29)
    gate.publish_preopen(result(), now=paused, state_path=release, receipt_path=receipt)
    assert gate.admission_status(now=following, candidate_sha=SHA, receipt_path=receipt)[0] is False
    assert gate.operator_pause_reason(following) == ""
    # Merely changing the day or even the candidate cannot grant PAPER GO.
    state(release, candidate="b" * 40)
    gate.publish_preopen(result(), now=following, state_path=release, receipt_path=receipt)
    assert gate.admission_status(now=following, candidate_sha=SHA, receipt_path=receipt)[0] is False
    # A fresh release-valid T-10 receipt for the exact identity is a separate gate.
    state(release)
    assert gate.publish_preopen(result(), now=following, state_path=release,
                                receipt_path=receipt)["status"] == "GO_PAPER_SCALPING"
    assert gate.admission_status(now=following, candidate_sha=SHA, receipt_path=receipt) == (True, "")


@pytest.mark.parametrize("day", [28, 29])
def test_host_publish_never_overwrites_immutable_a125_gate(tmp_path, monkeypatch, day):
    release, receipt = tmp_path / "release.json", tmp_path / "live-gate.json"
    state(release)
    original = ("{\"status\":\"NO_GO_PAPER_SCALPING\","
                "\"trade_day_ar\":\"2026-10-27\"}\n")
    receipt.write_text(original)
    monkeypatch.setattr(gate, "HOST_RECEIPT", receipt)
    with pytest.raises(ValueError, match="HISTORICAL_A125_LIVE_GATE_IMMUTABLE"):
        gate.publish_preopen(result(), now=DAY.replace(day=day),
                             state_path=release, receipt_path=receipt)
    assert receipt.read_text() == original


def test_focus_requires_eight_exact_ready_contracts(tmp_path):
    from bf_production_paper_observer import DEFAULT_FOCUS_SYMBOLS
    assert gate.FOCUS == DEFAULT_FOCUS_SYMBOLS
    path = tmp_path / "focus.db"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT,instrument_type TEXT,settlement TEXT,market TEXT,currency TEXT,
          status TEXT,capability TEXT);
          CREATE TABLE candidate_identity_v2(ticker TEXT,instrument_type TEXT,
          settlement TEXT,market TEXT,currency TEXT,status TEXT,can_simulate INTEGER);""")
        for ticker, kind, term in gate.FOCUS:
            values = (ticker, kind, term, "BYMA", "ARS")
            db.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,'AVAILABLE','READY_PAPER_SPOT')", values)
            db.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,'AVAILABLE',1)", values)
    assert gate.focus_contracts(path)["ready"] == 8
    with sqlite3.connect(path) as db:
        db.execute("UPDATE financial_instrument_catalog SET capability='NEEDS_CONTRACT' WHERE ticker='GGAL'")
    assert gate.focus_contracts(path) == {"state": "RED", "ready": 7, "required": 8, "missing": ["GGAL"]}


def test_live_broker_guard_blocks_fill_but_preserves_paper_state(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    quote = Quote("GGAL", "ACCIONES", "A-24HS", D("100"), D("99"), D("101"),
                  D("100"), D("100"), DAY.isoformat(), currency="ARS", market="BYMA")
    broker = PaperBroker(store, admission_guard=lambda q, s: (False, "NO_GO_PAPER_SCALPING"))
    opened, reason, _ = broker.admit_paper_candidate(quote, D("0.8"), {})
    assert (opened, reason) == (False, "NO_GO_PAPER_SCALPING")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_partial_focus_records_buy_evaluation_as_hold_without_fill(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    quote = Quote("GGAL", "ACCIONES", "A-24HS", D("100"), D("99"), D("101"),
                  D("100"), D("100"), DAY.isoformat(), currency="ARS", market="BYMA")
    broker = PaperBroker(store, admission_guard=lambda q, s: (False, "NO_GO_PAPER_SCALPING"))
    monkeypatch.setattr(broker, "decide", lambda q: ("BUY", D("0.8"), "fixture", {}))
    broker.on_quote(quote, allow_new_openings=False, evaluate_when_blocked=True,
                    opening_block_reason="FOCUS_3_OF_8")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert tuple(db.execute("SELECT final_result,reason FROM trade_gate_evaluations").fetchone()) == (
            "BLOCKED", "FOCUS_3_OF_8")


def test_unsafe_receipt_symlink_is_rejected(tmp_path):
    real = tmp_path / "actual.json"
    link = tmp_path / "gate.json"
    real.write_text("{}")
    link.symlink_to(real)
    assert gate.admission_status(now=DAY, candidate_sha=SHA, receipt_path=link)[0] is False
    with pytest.raises(ValueError, match="UNSAFE_GATE_PATH"):
        gate.publish_preopen(result(), now=DAY, state_path=tmp_path / "release.json", receipt_path=link)


def test_per_candidate_contract_must_match_exact_monetary_identity(tmp_path, monkeypatch):
    import bu_instrument_catalog as catalog
    store = PaperStore(str(tmp_path / "paper.db"))
    record = with_synthetic_volume_contract({"ticker": "GGAL", "instrument_type": "ACCIONES",
        "settlement": "A-24HS", "market": "BYMA", "currency": "ARS", "status": "AVAILABLE",
        "capability": "READY_PAPER_SPOT"})
    quote = Quote("GGAL", "ACCIONES", "A-24HS", D("100"), D("99"), D("101"),
                  D("100"), D("100"), DAY.isoformat(), currency="ARS", market="BYMA")
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    monkeypatch.setattr(catalog, "lookup", lambda *a, **k: record)
    assert gate.paper_admission_guard(quote, store) == (True, "")
    record["capability"] = "NEEDS_OPTION_CONTRACT"
    assert gate.paper_admission_guard(quote, store) == (False, "PAPER_EXACT_CONTRACT_NOT_READY")


def test_eod_caucion_worker_holds_placement_on_global_no_go(tmp_path, monkeypatch):
    import bv_paper_runtime
    import di_caucion_cash_sweep_runtime_hf6 as sweep

    store = PaperStore(str(tmp_path / "paper.db"))
    monkeypatch.setattr(bv_paper_runtime, "broker_from_environment", lambda *_a, **_k:
                        type("Broker", (), {"participation": D("0.1")})())
    monkeypatch.setattr(sweep, "offers_from_store", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(sweep, "obligation_snapshot_from_ledger", lambda *_a, **_k: {})
    monkeypatch.setattr(sweep, "run_paper_sweep", lambda *_a, **_k:
                        (_ for _ in ()).throw(AssertionError("placement despite NO_GO")))
    monkeypatch.setattr(gate, "admission_status", lambda **_k: (False, "NO_GO_PAPER_SCALPING"))

    class OneTick:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, _seconds):
            with store.connect() as db:
                state = db.execute("SELECT state,code FROM paper_caucion_cash_sweep_state WHERE id=1").fetchone()
            assert tuple(state) == ("HOLD", "NO_GO_PAPER_SCALPING")
            self.stopped = True

    sweep.run_worker(store, OneTick(), clock_fn=lambda: "2026-10-15T16:40:00-03:00")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


@pytest.mark.parametrize("path", ["direct", "allocator"])
def test_caucion_final_insert_rechecks_global_receipt_in_runtime_image(tmp_path, monkeypatch, path):
    from test_production_paper_v1634 import caucion_offer, caucion_policy

    monkeypatch.setenv("POROTA_BUILD_SHA", SHA)
    offer = caucion_offer()

    def attempt(broker):
        if path == "direct":
            return broker.place_caucion(offer, "1000", "receipt-test", offer.quoted_at)
        return broker.allocate_caucion([offer], caucion_policy(), "receipt-test",
                                       as_of=offer.quoted_at)

    # A valid receipt lets both existing PAPER paths operate in an isolated book.
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "go.db"))
    green = PaperBroker(PaperStore(str(tmp_path / "go.db")), initial_cash="10000",
                        daily_loss_pct="1")
    accepted = attempt(green)
    assert accepted.get("paper_id")

    # The same input under NO_GO cannot insert a new position or allocation.
    monkeypatch.setattr(gate, "admission_status", lambda: (False, "NO_GO_PAPER_SCALPING"))
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "no-go.db"))
    red = PaperBroker(PaperStore(str(tmp_path / "no-go.db")), initial_cash="10000",
                      daily_loss_pct="1")
    with pytest.raises(ValueError, match="NO_GO_PAPER_SCALPING"):
        attempt(red)
    with red.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_caucion_allocations").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_runtime_spot_book_requires_guard_and_matching_fixture_receipt(tmp_path, monkeypatch):
    from test_production_paper_v1634 import quote

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return DAY.astimezone(tz or TZ)

    path = tmp_path / "runtime.db"
    receipt, release = tmp_path / "receipt.json", tmp_path / "release.json"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    monkeypatch.setenv("POROTA_BUILD_SHA", SHA)
    monkeypatch.setattr(gate, "RUNTIME_RECEIPT", receipt)
    monkeypatch.setattr(gate, "datetime", FrozenDateTime)
    store = PaperStore(str(path))
    candidate = quote(ask_size="100")
    direct = PaperBroker(store, initial_cash="10000")
    assert direct._open(candidate, D("0.8"), {})[:2] == (
        False, "PAPER_ADMISSION_GUARD_MISSING")

    def readable_contract(_quote, live_store):
        # The production callback reads catalog rows through a second SQLite
        # connection. Keep that shape working when the write lock is held.
        with live_store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM paper_workspace").fetchone()[0] == 1
        return True, ""

    guarded = PaperBroker(store, initial_cash="10000", admission_guard=readable_contract)
    assert guarded._open(candidate, D("0.8"), {})[:2] == (
        False, "PAPER_PREOPEN_RECEIPT_UNSAFE")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0

    state(release)
    gate.publish_preopen(result(), now=DAY, state_path=release, receipt_path=receipt)
    assert guarded._open(candidate, D("0.8"), {})[0] is True
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_runtime_spot_receipt_revoked_before_locked_insert_rolls_back(tmp_path, monkeypatch):
    from test_production_paper_v1634 import quote

    path = tmp_path / "runtime.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    store = PaperStore(str(path))
    broker = PaperBroker(store, initial_cash="10000", admission_guard=lambda *_: (True, ""))
    checks = iter([(True, ""), (False, "NO_GO_PAPER_SCALPING")])
    monkeypatch.setattr(gate, "admission_status", lambda: next(checks))
    assert broker._open(quote(ask_size="100"), D("0.8"), {})[:2] == (
        False, "NO_GO_PAPER_SCALPING")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_runtime_spot_contract_revoked_before_locked_insert_rolls_back(tmp_path, monkeypatch):
    from test_production_paper_v1634 import quote

    path = tmp_path / "runtime.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    checks = []

    def contract_guard(_quote, _store):
        checks.append(1)
        return ((True, "") if len(checks) == 1 else
                (False, "PAPER_EXACT_CONTRACT_NOT_READY"))

    broker = PaperBroker(PaperStore(str(path)), initial_cash="10000", admission_guard=contract_guard)
    assert broker._open(quote(ask_size="100"), D("0.8"), {})[:2] == (
        False, "PAPER_EXACT_CONTRACT_NOT_READY")
    assert len(checks) == 2
    with broker.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_runtime_future_contract_revoked_under_locked_admission_rolls_back(tmp_path, monkeypatch):
    from test_rc6_future_paper_engine import broker as future_broker, quote as future_quote

    clock = ["2026-10-05T12:00:00-03:00"]
    broker = future_broker(tmp_path, clock)
    monkeypatch.setenv("PAPER_V17_DB_PATH", broker.store.path)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    checks = []

    def contract_guard(_quote, _store):
        checks.append(1)
        return ((True, "") if len(checks) == 1 else
                (False, "PAPER_EXACT_CONTRACT_NOT_READY"))

    broker.admission_guard = contract_guard
    assert broker._open_future(future_quote(clock[0]), D("0.8"), {"samples": 8})[:2] == (
        False, "PAPER_EXACT_CONTRACT_NOT_READY")
    assert len(checks) == 2
    with broker.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_future_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0


def test_runtime_future_locked_gate_rejects_missing_receipt(tmp_path, monkeypatch):
    class BeforeOperatorPause(datetime):
        @classmethod
        def now(cls, tz=None):
            return DAY.astimezone(tz or TZ)

    path = tmp_path / "runtime.db"
    monkeypatch.setattr(gate, "datetime", BeforeOperatorPause)
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    monkeypatch.setenv("POROTA_BUILD_SHA", SHA)
    monkeypatch.setattr(gate, "RUNTIME_RECEIPT", tmp_path / "missing.json")
    broker = PaperBroker(PaperStore(str(path)), admission_guard=lambda *_: (True, ""))
    assert broker._future_locked_admission(None, None, None, None, None, connection=None) == (
        "PAPER_PREOPEN_RECEIPT_UNSAFE")

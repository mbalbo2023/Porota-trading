"""Contratos defensivos del hotfix RC3-HF2."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import be_paper_engine as engine
import bf_production_paper_observer as observer
import bg_paper_dashboard as dashboard


def quote(symbol="GGAL", price="100", minute=0):
    at = (datetime.now(timezone.utc) + timedelta(minutes=minute)).isoformat()
    value = Decimal(price)
    return engine.Quote(
        symbol, "ACCIONES", "A-24HS", value, value - Decimal("0.10"),
        value + Decimal("0.10"), Decimal("1000"), Decimal("1000"), at,
        currency="ARS", market="BYMA", metadata_source="TEST",
        book_at=at, trade_at=at, last_kind="TRADE",
    )


def add_focus_catalog(store, identities, *, capability="READY_PAPER_SPOT"):
    with store.connect() as connection:
        for ticker, kind, settlement in identities:
            connection.execute("""INSERT INTO financial_instrument_catalog VALUES(
              ?,?,'BYMA','ARS',?,'PPI_FIELD','test',?,'run','AVAILABLE',?,?)""",
              (ticker, kind, settlement, engine.now_iso(), capability,
               json.dumps({"ticker": ticker, "instrumentType": kind,
                           "market": "BYMA", "currency": "ARS",
                           "settlement": settlement})))


def test_focus_8_de_8_verde_y_habilita_aperturas(tmp_path):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    add_focus_catalog(store, observer.FOCUS_SYMBOLS)
    result = observer._focus_coverage(store)
    assert result["state"] == "VERDE"
    assert result["matched_count"] == 8
    assert result["allow_new_openings"] is True


def test_focus_identidad_distinta_no_se_acepta_silenciosamente(tmp_path):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    add_focus_catalog(store, [("GGAL", "ACCIONES", "INMEDIATA")])
    result = observer._focus_coverage(store)
    assert result["state"] == "ROJO"
    assert result["matched_count"] == 0
    assert result["allow_new_openings"] is False
    assert result["missing"][0]["reason"].startswith("IDENTIDAD_DISTINTA")


def test_bloqueo_de_aperturas_no_impide_cerrar_posicion(tmp_path):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    broker = engine.PaperBroker(store, initial_cash="100000")
    opened, _, _ = broker._open(quote(), Decimal("0.9"), {})
    assert opened
    position = store.open_positions()[0]
    closing = replace(quote(price=str(Decimal(position["target_price"]) + 2), minute=1),
                      bid=Decimal(position["target_price"]) + 1,
                      ask=Decimal(position["target_price"]) + 2)
    broker.on_quote(closing, allow_new_openings=False,
                    opening_block_reason="Cobertura 0/8")
    assert store.open_positions() == []
    assert store.recent_closed(1)[0]["close_reason"] == "TAKE_PROFIT_PAPER"


def test_factibilidad_separa_foco_de_rotacion(monkeypatch):
    monkeypatch.setattr(observer, "INTERVAL", 60)
    monkeypatch.setattr(observer, "PPI_CALL_BUDGET_SECONDS", 2)
    monkeypatch.setattr(observer, "SIGNAL_MIN_SAMPLES", 6)
    monkeypatch.setattr(observer, "SIGNAL_WINDOW_MINUTES", 90)
    result = observer._sampling_feasibility(
        20, eligible_total=200, focus_count=8, open_count=0
    )
    assert result["focus_feasible"] is True
    assert result["rotation_feasible"] is False
    assert result["focus_estimated_samples_per_window"] >= 6
    assert result["rotation_estimated_samples_per_window"] < 6


def test_economia_shadow_cuenta_apertura_con_fallo(tmp_path):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    with store.connect() as connection:
        connection.execute("""INSERT INTO trade_gate_evaluations
          (evaluated_at,decision_key,symbol,technical_gate,ai_gate,
           patrimonial_gate,final_result,reason,paper_id,detail_json)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (engine.now_iso(), "key", "GGAL", "APPROVE", "NOT_USED", "APPROVE",
           "OPENED_SIMULATED", "test", "PAPER-test",
           json.dumps({"economics": {"passed": False}})))
    result = observer._economic_shadow_summary(store)
    assert result == {"evaluated": 1, "passed": 0, "failed": 1,
                      "opened_with_failure": 1, "blocked_with_failure": 0}


def test_ingesta_background_se_limita_a_una_vez_por_rueda_postcierre(tmp_path):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    first_attempt = datetime(2026, 9, 28, 17, 5, tzinfo=observer.TZ)
    with store.connect() as connection:
        connection.execute("INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
                           ("PPI_PRODUCTION_HISTORY", "ROJO", first_attempt.isoformat(),
                            None, 0, "falló"))
    assert observer._background_ingest_due(
        store, now=datetime(2026, 9, 28, 23, 30, tzinfo=observer.TZ)
    ) is False
    assert observer._background_ingest_due(
        store, now=datetime(2026, 9, 29, 16, 59, tzinfo=observer.TZ)
    ) is False
    assert observer._background_ingest_due(
        store, now=datetime(2026, 9, 29, 17, 1, tzinfo=observer.TZ)
    ) is True


def test_ingesta_background_usa_history_y_nunca_current_book(tmp_path, monkeypatch):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    calls = []

    class Reader:
        def current(self, *_args):
            raise AssertionError("current no debe usarse fuera de rueda")

        def book(self, *_args):
            raise AssertionError("book no debe usarse fuera de rueda")

    reader = Reader()
    monkeypatch.setattr(
        observer,
        "_download_histories",
        lambda received, received_store: (
            calls.append(("history", received, received_store)) or 1
        ),
    )
    monkeypatch.setattr(observer, "_background_ingest_due", lambda _store: False)
    assert observer._background_ingest(reader, store, force=True) is None
    assert calls == []

    monkeypatch.setattr(observer, "_background_ingest_due", lambda _store: True)
    assert observer._background_ingest(reader, store, force=True) == 1
    assert calls == [("history", reader, store)]


def test_dashboard_expone_binding_y_guardas_hf3(tmp_path, monkeypatch):
    store = engine.PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    observer._health(store, "PAPER_FOCUS_COVERAGE", "ROJO", "Foco 0/8", "test")
    observer._health(store, "PPI_BACKGROUND_INGEST", "VERDE", "Lote correcto", "test", True)
    monkeypatch.setattr(dashboard, "DB_PATH", store.path)
    monkeypatch.setattr(dashboard, "MODE", "PRODUCTION_PAPER")
    keys = {item["key"] for item in dashboard._health_components()}
    assert "PAPER_FOCUS_COVERAGE" in keys
    assert "PPI_BACKGROUND_INGEST" in keys
    page = dashboard.motor_page()
    assert "PORTÓN ECONÓMICO OBLIGATORIO" in page
    # RC6 policy: economic gate remains SHADOW; sector concentration is the BINDING gate.
    assert "Economía matemática SHADOW" in page

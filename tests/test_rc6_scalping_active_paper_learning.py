"""Prueba aislada de promoción del scalper RC6 a ACTIVE_PAPER.

No usa red ni clientes de órdenes. Demuestra candidato -> fill simulado ->
cierre simulado -> muestra de aprendizaje etiquetada, preservando
real_orders_sent=0.
"""
import json

from be_paper_engine import PaperBroker, PaperStore
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping
import bv_paper_runtime as runtime
from test_production_paper_v1634 import quote


def _record():
    return dict(
        ticker="GGAL",
        instrument_type="ACCIONES",
        market="BYMA",
        currency="ARS",
        settlement="A-24HS",
        capability="READY_PAPER_SPOT",
        status="AVAILABLE",
    )


def test_active_paper_scalping_creates_only_simulated_fill_and_learning(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    catalog.init_schema(store)
    scalping.init_schema(store)

    opened_at = "2026-09-07T11:00:00-03:00"
    closed_at = "2026-09-07T11:10:00-03:00"
    record = _record()
    q = quote(price="100", at=opened_at)
    store.add_quote(q)

    # El broker de producción PAPER exige supervisor/reader vivos. Esta prueba
    # los modela explícitamente, sin relajar esa guarda en el código productivo.
    with store.connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO paper_supervisor_state VALUES(1,?,'RUNNING','fixture')",
            (opened_at,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,'READY','fixture')",
            (opened_at,),
        )
        connection.execute(
            """INSERT INTO scalping_candidates
               (evaluated_at,symbol,asset_class,market,currency,settlement,action,
                score,price,volume,points,reason,economics_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                scalping._stamp(opened_at),
                "GGAL",
                "ACCIONES",
                "BYMA",
                "ARS",
                "A-24HS",
                "BUY_CANDIDATE",
                "0.90",
                "100",
                "1000",
                20,
                "VALIDATED_SCALPING_CANDIDATE",
                json.dumps({"binding": True, "passed": True}),
            ),
        )

    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_SCALPING_MAX_OPEN_POSITIONS", "1")
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.setattr(runtime, "now_iso", lambda: opened_at)

    result = scalping.promote_paper_candidate(store, record, at=opened_at)
    assert result == "OPENED_SIMULATED"

    positions = store.open_positions()
    assert len(positions) == 1
    position = positions[0]
    features = json.loads(position["features_json"])
    assert features["execution_style"] == "SCALPING_PAPER"

    with store.connect() as connection:
        buy = connection.execute(
            "SELECT side FROM paper_fills WHERE paper_id=? ORDER BY id LIMIT 1",
            (position["paper_id"],),
        ).fetchone()
        learning = connection.execute(
            "SELECT label_timestamp,outcome FROM paper_learning_samples WHERE paper_id=?",
            (position["paper_id"],),
        ).fetchone()
        orders = connection.execute(
            "SELECT real_orders_sent FROM observer_state WHERE id=1"
        ).fetchone()[0]
    assert buy[0] == "BUY_SIMULATED"
    assert learning[0] is None and learning[1] is None
    assert int(orders) == 0

    # Cierre simulado: debe etiquetar la misma muestra que se creó al abrir.
    close_quote = quote(price="102", at=closed_at)
    closing_broker = PaperBroker(store, clock_fn=lambda: closed_at)
    assert closing_broker._close(position, close_quote, "SCALPING_TEST_CLOSE", as_of=closed_at)

    with store.connect() as connection:
        sell = connection.execute(
            "SELECT side FROM paper_fills WHERE paper_id=? ORDER BY id DESC LIMIT 1",
            (position["paper_id"],),
        ).fetchone()
        learning = connection.execute(
            """SELECT label_timestamp,net_return_pct,outcome,duration_minutes
               FROM paper_learning_samples WHERE paper_id=?""",
            (position["paper_id"],),
        ).fetchone()
        orders_after = connection.execute(
            "SELECT real_orders_sent FROM observer_state WHERE id=1"
        ).fetchone()[0]
    assert sell[0] == "SELL_SIMULATED"
    assert learning[0] is not None
    assert learning[1] is not None
    assert learning[2] in {"WIN", "LOSS", "FLAT"}
    assert learning[3] == 10
    assert int(orders_after) == 0

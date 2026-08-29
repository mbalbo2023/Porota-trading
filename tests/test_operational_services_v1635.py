import gzip
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import PaperStore
from bt_caucion_paper import record_sale
import bi_operational_services as services


def test_resultado_del_periodo_usa_fecha_cierre_y_separa_monedas(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    services.init_schema(store)
    with store.connect() as c:
        for key, currency, opened, closed, pnl in [
            ("ARS", "ARS", "2026-08-27T11:00:00-03:00", "2026-08-28T11:00:00-03:00", "50"),
            ("MEP", "USD_MEP", "2026-08-28T11:00:00-03:00", "2026-08-28T12:00:00-03:00", "10"),
            ("FUTURE", "ARS", "2026-08-28T11:00:00-03:00", "2026-08-31T11:00:00-03:00", "900"),
            ("PREVIOUS", "ARS", "2026-08-27T11:00:00-03:00", "2026-08-28T01:00:00+00:00", "800"),
        ]:
            price=str(services.Decimal(102)+services.Decimal(pnl))
            gross=str(services.Decimal(pnl)+2)
            c.execute("""INSERT INTO paper_positions(paper_id,source,strategy_version,symbol,asset_class,
                settlement,status,quantity,entry_price,entry_cost,stop_price,target_price,opened_at,closed_at,
                exit_price,exit_cost,gross_pnl,net_pnl,features_json,currency)
                VALUES(?,'PRODUCTION_PAPER','fixture',?,'ACCIONES','INMEDIATA','CLOSED','1','100','1','98','104',?,?,?,'1',?,?,'{}',?)""",
                (key, key, opened, closed, price, gross, pnl, currency))
            for side,at,fill_price in (('BUY_SIMULATED',opened,'100'),('SELL_SIMULATED',closed,price)):
                c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                    (key,'PRODUCTION_PAPER',side,at,'1',fill_price,'1','0'))
            record_sale(c,key,'INMEDIATA',closed,services.Decimal(price)-1,currency)
    data = services._period_data(store, "2026-08-28T00:00:00-03:00", "2026-08-29T00:00:00-03:00")
    assert data["pnl"] == 50
    assert data["pnl_by_currency"] == {"ARS": "50", "USD_MEP": "10"}
    assert {p["paper_id"] for p in data["closed"]} == {"ARS", "MEP"}
    story = services._report_story("Fixture", "2026-08-28", data)
    assert story["resumen"]["pnl_neto_ars"] == 50
    assert story["resumen"]["pnl_por_moneda"]["USD_MEP"] == "10"
    assert next(p for p in story["operaciones"] if p["instrumento"] == "MEP")["moneda_plaza"] == "USD_MEP"
    future = next(p for p in story["operaciones"] if p["instrumento"] == "FUTURE")
    assert future["estado"] == "OPEN" and future["pnl_neto"] is None and future["cierre"] is None
    services._pdf(story, tmp_path / "report_fx.pdf")


def test_reporte_caucion_cuenta_interes_neto_al_acreditar_sin_sumar_principal(tmp_path):
    from be_paper_engine import PaperBroker
    from bt_caucion_paper import CaucionOffer
    store = PaperStore(str(tmp_path / "paper.db"))
    services.init_schema(store)
    broker = PaperBroker(store, initial_cash_by_currency={"USD_MEP": "2000"})
    offer = CaucionOffer(instrument_id="TEST-CAUCION-MEP", currency="USD_MEP",
        annual_rate_fraction="0.365", start_date="2026-08-28",
        maturity_at="2026-08-31T15:00:00-03:00", quoted_at="2026-08-28T11:00:00-03:00",
        available_principal="100000", minimum_principal="100", principal_step="1",
        day_count_basis=365, fee_payment="MATURITY", metadata_source="TEST_NOT_BROKER",
        quoted_total_fees="1", fee_quote_principal="1000")
    broker.place_caucion(offer, "1000", "pedido", offer.quoted_at)
    before = services._period_data(store, "2026-08-28T00:00:00-03:00", "2026-08-29T00:00:00-03:00")
    assert before["pnl_by_currency"] == {}
    broker.settle_cauciones(offer.maturity_at)
    data = services._period_data(store, "2026-08-31T00:00:00-03:00", "2026-09-01T00:00:00-03:00")
    assert data["pnl"] == 0 and data["pnl_by_currency"] == {"USD_MEP": "2.00"}
    assert data["closed"] == [] and data["win_rate"] is None
    story = services._report_story("Fixture caución", "2026-08-31", data)
    assert story["resumen"]["cauciones_vencidas"] == 1
    assert services.Decimal(story["cauciones_vencidas"][0]["capital"]) == 1000
    services._pdf(story, tmp_path / "report_caucion.pdf")


def test_sre_y_backup_incluyen_restore_real(tmp_path, monkeypatch):
    db = tmp_path / "observer.db"
    store = PaperStore(str(db))
    monkeypatch.setattr(services, "BACKUP_DIR", tmp_path / "backups")
    services.init_schema(store)
    metric = services.collect_sre(store)
    assert metric["integrity"] == "ok"
    result = services.create_backup(store, force=True)
    assert result and Path(result).exists()
    with gzip.open(result, "rb") as source:
        restored = tmp_path / "restored.db"
        restored.write_bytes(source.read())
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    with store.connect() as connection:
        row = dict(connection.execute("SELECT * FROM backup_runs").fetchone())
    assert row["restore_test"] == "OK"
    assert row["state"] == "VERDE"
    assert not list((tmp_path / "backups").glob(".restore_test_*.db*"))
    assert not list((tmp_path / "backups").glob("observer_*.db-wal"))
    assert not list((tmp_path / "backups").glob("observer_*.db-shm"))


def test_remove_sqlite_bundle_elimina_principal_wal_y_shm(tmp_path):
    test = tmp_path / ".restore_test_42.db"
    paths = [test, Path(str(test) + "-wal"), Path(str(test) + "-shm")]
    for path in paths:
        path.write_bytes(b"fixture")
    services._remove_sqlite_bundle(test)
    assert not any(path.exists() for path in paths)


def test_reporte_pdf_y_paquete_ia_sin_secretos(tmp_path, monkeypatch):
    db = tmp_path / "observer.db"
    store = PaperStore(str(db))
    services.init_schema(store)
    monkeypatch.setattr(services, "REPORT_DIR", tmp_path / "reports")
    now = datetime.now(services.TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    with store.connect() as connection:
        connection.execute("""INSERT INTO paper_positions(
          paper_id,source,strategy_version,symbol,asset_class,settlement,status,quantity,
          entry_price,entry_cost,stop_price,target_price,opened_at,closed_at,exit_price,
          exit_cost,gross_pnl,net_pnl,close_reason,features_json)
          VALUES('PAPER-X','PRODUCTION_PAPER','v','GGAL','ACCIONES','A-24HS','CLOSED',
          '1','100','1','98','104',?,?, '105','1','5','3','TAKE_PROFIT_PAPER','{"spread":"0.01"}')""",
          (start.isoformat(), (start + timedelta(hours=2)).isoformat()))
        closed_at=(start+timedelta(hours=2)).isoformat()
        for side,at,price in (('BUY_SIMULATED',start.isoformat(),'100'),('SELL_SIMULATED',closed_at,'105')):
            connection.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                ('PAPER-X','PRODUCTION_PAPER',side,at,'1',price,'1','0'))
        record_sale(connection,'PAPER-X','A-24HS',closed_at,'104')
    pdf, ai = services.generate_report(store, "DIARIO", start.date().isoformat(),
                                        start.isoformat(), end.isoformat())
    assert Path(pdf).read_bytes().startswith(b"%PDF")
    payload = json.loads(Path(ai).read_text(encoding="utf-8"))
    assert payload["ordenes_reales"] == 0
    assert payload["operaciones"][0]["leccion"]
    assert "api_secret" not in Path(ai).read_text(encoding="utf-8").lower()


def test_mensual_elimina_semanales_integrados(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    weekly = tmp_path / "weekly.pdf"; weekly.write_bytes(b"%PDF")
    with store.connect() as connection:
        connection.execute("""INSERT INTO report_registry
          (period_type,period_key,created_at,pdf_path,ai_path,state,detail)
          VALUES('SEMANAL','2026-08-01_2026-08-07','2026-08-07',?,NULL,'VERDE','x')""",
          (str(weekly),))
    # La regla de borrado se valida sin depender de la fecha real del runner.
    with store.connect() as connection:
        rows = connection.execute("SELECT pdf_path FROM report_registry WHERE period_type='SEMANAL' AND period_key LIKE '2026-08%'").fetchall()
        for row in rows: Path(row[0]).unlink(missing_ok=True)
        connection.execute("DELETE FROM report_registry WHERE period_type='SEMANAL' AND period_key LIKE '2026-08%'")
    assert not weekly.exists()
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_registry").fetchone()[0] == 0

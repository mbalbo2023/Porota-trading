import gzip
import inspect
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


def test_unknown_fi_fx_report_preserves_none_and_never_invents_zero_orders(tmp_path):
    """No positive native maturity authority is assumed by this report-only guard."""
    unknown = {
        "positions": [], "closed": [], "realizations": [], "decisions": [],
        "news": [], "gates": [], "cauciones_matured": [],
        "pnl": None, "wins": None, "win_rate": None,
        "pnl_by_currency": {"ARS": None},
        "unverified_closed_by_currency": {"ARS": 1},
    }
    story = services._report_story("Synthetic unknown FI FX", "2026-11-10", unknown)
    assert story["ordenes_reales"] == "NO_VERIFICADO"
    assert story["resumen"]["pnl_neto_ars"] is None
    assert story["resumen"]["pnl_por_moneda"] == {"ARS": None}
    assert story["resumen"]["pnl_no_verificado_por_moneda"] == {"ARS": 1}
    assert services._report_pnl_label(None) == "NO_VERIFICADO"
    assert services._report_pnl_label("0") == "0.00"
    services._pdf(story, tmp_path / "unknown.pdf")
    assert (tmp_path / "unknown.pdf").read_bytes().startswith(b"%PDF")


def test_resultado_del_periodo_usa_fecha_cierre_y_separa_monedas(tmp_path, monkeypatch):
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
    # The reports have no independent host/SQLite attestation of real orders.
    assert story["ordenes_reales"] == "NO_VERIFICADO"
    assert story["resumen"]["pnl_neto_ars"] == 50
    assert story["resumen"]["pnl_por_moneda"]["USD_MEP"] == "10"
    assert next(p for p in story["operaciones"] if p["instrumento"] == "MEP")["moneda_plaza"] == "USD_MEP"
    future = next(p for p in story["operaciones"] if p["instrumento"] == "FUTURE")
    assert future["estado"] == "OPEN" and future["pnl_neto"] is None and future["cierre"] is None
    # Capture actual ReportLab paragraphs without requiring a PDF parser in CI.
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    original_build = SimpleDocTemplate.build
    paragraphs = []

    def capture_pdf_content(self, flowables, *args, **kwargs):
        paragraphs.extend(element.text for element in flowables if isinstance(element, Paragraph))
        return original_build(self, flowables, *args, **kwargs)

    monkeypatch.setattr(SimpleDocTemplate, "build", capture_pdf_content)
    services._pdf(story, tmp_path / "report_fx.pdf")
    assert any("órdenes reales: NO_VERIFICADO" in line for line in paragraphs)
    assert not any("órdenes reales: 0" in line for line in paragraphs)


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
    monkeypatch.setattr(services, "backups_suspended", lambda: False)
    db = tmp_path / "observer.db"
    store = PaperStore(str(db))
    monkeypatch.setattr(services, "BACKUP_DIR", tmp_path / "backups")
    services.init_schema(store)
    metric = services.collect_sre(store)
    assert metric["integrity"] == "DELEGATED_TO_RC6_FULL_DB_INTEGRITY"
    assert metric["full_integrity_check_performed"] is False
    source = inspect.getsource(services.collect_sre)
    assert "PRAGMA quick_check" not in source
    assert "DELEGATED_TO_RC6_FULL_DB_INTEGRITY" in source
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


def test_backup_defers_before_materializing_when_disk_reserve_is_insufficient(
        tmp_path, monkeypatch):
    monkeypatch.setattr(services, "backups_suspended", lambda: False)
    db = tmp_path / "observer.db"
    store = PaperStore(str(db))
    monkeypatch.setattr(services, "BACKUP_DIR", tmp_path / "backups")
    services.init_schema(store)
    monkeypatch.setattr(services, "_backup_disk_admission",
                        lambda *_args: (False, 100, 200))
    assert services.create_backup(store, force=True) is None
    assert not list((tmp_path / "backups").iterdir())
    with store.connect() as connection:
        state, detail = connection.execute(
            "SELECT state,detail FROM operational_jobs WHERE job_key='DAILY_BACKUP'").fetchone()
    assert state == "DEFERRED_DISK" and "available=100|required=200" in detail


def test_backup_admission_accounts_for_two_copies_and_reserve(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    source.write_bytes(b"x" * 1024)
    reserve = 2 * 1024 ** 3
    monkeypatch.setattr(services.shutil, "disk_usage",
                        lambda _path: type("Usage", (), {"free": reserve + 2047})())
    assert services._backup_disk_admission(source, tmp_path) == (
        False, reserve + 2047, reserve + 2048)


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
    assert payload["ordenes_reales"] == "NO_VERIFICADO"
    with store.connect() as connection:
        record = connection.execute("""SELECT state,detail FROM report_registry
          WHERE period_type='DIARIO' AND period_key=?""", (start.date().isoformat(),)).fetchone()
    assert record["state"] == "AMARILLO"
    assert "CONTENIDO_NO_VERIFICADO" in record["detail"]
    assert payload["operaciones"][0]["leccion"]
    assert "api_secret" not in Path(ai).read_text(encoding="utf-8").lower()


def test_cierre_telegram_no_inventa_cero_ordenes_reales(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    assert services.send_closing_summary(store) == "EN_COLA"
    with store.connect() as connection:
        row = connection.execute("""SELECT body,state FROM paper_notification_outbox
          WHERE kind='CLOSING_SUMMARY'""").fetchone()
    assert row is not None and row["state"] == "PENDING"
    assert "Órdenes reales: NO_VERIFICADO" in row["body"]
    assert "Órdenes reales: 0" not in row["body"]


def test_successful_report_artifact_does_not_turn_unattested_content_green(
        tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    monkeypatch.setattr(services, "REPORT_DIR", tmp_path / "reports")
    services.ensure_reports(store)
    with store.connect() as connection:
        job = connection.execute("""SELECT state,detail,last_success_at
          FROM operational_jobs WHERE job_key='REPORTS'""").fetchone()
    assert job["state"] == "AMARILLO"
    assert "CONTENIDO_NO_VERIFICADO" in job["detail"]
    assert job["last_success_at"] is not None  # Artifact generated; content still unknown.


def test_pdf_generation_failure_cannot_mark_reports_generated_or_successful(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    monkeypatch.setattr(services, "REPORT_DIR", tmp_path / "reports")

    def fail_pdf(*_args):
        raise ValueError("SYNTHETIC_PDF_FAILURE")

    monkeypatch.setattr(services, "_pdf", fail_pdf)
    services.ensure_reports(store)
    with store.connect() as connection:
        job = connection.execute("""SELECT state,detail,last_success_at
          FROM operational_jobs WHERE job_key='REPORTS'""").fetchone()
        report = connection.execute("""SELECT state FROM report_registry
          WHERE period_type='DIARIO'""").fetchone()
        generated = connection.execute("SELECT COUNT(*) FROM report_registry").fetchone()[0]
    assert report is not None and report["state"] == "ROJO"
    assert generated == 1  # Stop before writing other daily/weekly outputs.
    assert job is not None and job["state"] == "ROJO"
    assert "REPORT_ARTIFACT_INCOMPLETE" in job["detail"]
    assert "Informes generados" not in job["detail"]
    assert job["last_success_at"] is None


def test_missing_report_artifact_fails_closed_and_preserves_prior_success(
        tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    services._job(store, "REPORTS", "AMARILLO", "previous artifact", success=True)
    prior_pdf = tmp_path / "report_historico.pdf"
    prior_ai = tmp_path / "report_historico.json"
    prior_pdf.write_bytes(b"%PDF-historical")
    prior_ai.write_text('{"historico":true}', encoding="utf-8")
    with store.connect() as connection:
        previous_stamp = connection.execute("""SELECT last_success_at FROM operational_jobs
          WHERE job_key='REPORTS'""").fetchone()[0]
        connection.execute("""INSERT INTO report_registry
          (period_type,period_key,created_at,pdf_path,ai_path,state,detail)
          VALUES('DIARIO','2026-09-02','2026-09-02',?,?,'VERDE','historical evidence')""",
          (str(prior_pdf), str(prior_ai)))

    # Deliberately simulate a generator that falsely returns plausible paths.
    monkeypatch.setattr(services, "generate_report", lambda *_args: (
        str(tmp_path / "missing_new.pdf"), str(tmp_path / "missing_new.json")))
    services.ensure_reports(store)

    with store.connect() as connection:
        job = connection.execute("""SELECT state,detail,last_success_at
          FROM operational_jobs WHERE job_key='REPORTS'""").fetchone()
        historical = connection.execute("""SELECT state,pdf_path,ai_path FROM report_registry
          WHERE period_type='DIARIO' AND period_key='2026-09-02'""").fetchone()
    assert job["state"] == "ROJO"
    assert "REPORT_ARTIFACT_INCOMPLETE" in job["detail"]
    assert job["last_success_at"] == previous_stamp
    assert historical["state"] == "VERDE"
    assert historical["pdf_path"] == str(prior_pdf)
    assert historical["ai_path"] == str(prior_ai)
    assert prior_pdf.read_bytes() == b"%PDF-historical"
    assert prior_ai.read_text(encoding="utf-8") == '{"historico":true}'


def test_missing_existing_daily_evidence_is_red_without_rewriting_history(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    monkeypatch.setattr(services, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(services, "_observed_report_days", lambda _connection: ["2026-09-02"])
    old_pdf = tmp_path / "historic.pdf"
    old_pdf.write_bytes(b"%PDF-historic")
    absent_ai = tmp_path / "historic-missing.json"
    with store.connect() as connection:
        connection.execute("""INSERT INTO report_registry
          (period_type,period_key,created_at,pdf_path,ai_path,state,detail)
          VALUES('DIARIO','2026-09-02','2026-09-02',?,?,'AMARILLO','historical evidence')""",
          (str(old_pdf), str(absent_ai)))
    services.ensure_reports(store)
    with store.connect() as connection:
        job = connection.execute("""SELECT state,detail,last_success_at FROM operational_jobs
          WHERE job_key='REPORTS'""").fetchone()
        historic = connection.execute("""SELECT state,detail,pdf_path,ai_path
          FROM report_registry WHERE period_type='DIARIO' AND period_key='2026-09-02'""").fetchone()
    assert job["state"] == "ROJO"
    assert "DIARIO_HISTORICO:2026-09-02" in job["detail"]
    assert job["last_success_at"] is None
    assert historic["detail"] == "historical evidence"
    assert historic["pdf_path"] == str(old_pdf) and historic["ai_path"] == str(absent_ai)
    assert old_pdf.read_bytes() == b"%PDF-historic"


def test_report_file_stat_permission_error_marks_reports_red_without_crashing_observer(
        tmp_path, monkeypatch):
    """An unreadable report must be recorded as failed without tearing down the scheduler."""
    store = PaperStore(str(tmp_path / "observer.db"))
    services.init_schema(store)
    pdf_path = tmp_path / "report.pdf"
    ai_path = tmp_path / "report.json"
    pdf_path.write_bytes(b"%PDF-1.4 mock")
    ai_path.write_text('{"ordenes_reales":"NO_VERIFICADO"}', encoding="utf-8")
    monkeypatch.setattr(services, "generate_report", lambda *_args: (
        str(pdf_path), str(ai_path)))

    original_stat = services.Path.stat
    pdf_checks = 0

    def permission_denied_after_is_file(path, *args, **kwargs):
        nonlocal pdf_checks
        if path == pdf_path:
            pdf_checks += 1
            if pdf_checks > 1:
                raise PermissionError("EACCES on report artifact")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(services.Path, "stat", permission_denied_after_is_file)
    services.ensure_reports(store)
    with store.connect() as connection:
        job = connection.execute("""SELECT state,detail,last_success_at
          FROM operational_jobs WHERE job_key='REPORTS'""").fetchone()
    assert job is not None and job["state"] == "ROJO"
    assert "REPORT_ARTIFACT_INCOMPLETE" in job["detail"]
    assert job["last_success_at"] is None


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


def _fixed_postclose_clock(monkeypatch):
    """A real BYMA business day after 17:00, independent of runner wall time."""
    real_clock = datetime

    class Clock(real_clock):
        @classmethod
        def now(cls, tz=None):
            return real_clock(2026, 10, 27, 18, 30, tzinfo=services.TZ)

    monkeypatch.setattr(services, "datetime", Clock)


def test_invalid_caucion_fee_evidence_quarantines_postclose_report_without_restart(
        tmp_path, monkeypatch):
    """An invalid PAPER ledger cannot produce a report or crash the scanner."""
    _fixed_postclose_clock(monkeypatch)
    store = PaperStore(str(tmp_path / "paper.db"))
    services.init_schema(store)
    monkeypatch.setattr(services, "backups_suspended", lambda: True)
    monkeypatch.setattr(services, "collect_sre", lambda *_: None)
    monkeypatch.setattr(services, "refresh_financial", lambda *_: None)
    attempts = []
    summaries = []

    def invalid_report(*_args, **_kwargs):
        attempts.append("report")
        raise ValueError("CAUCION_LEDGER_INVALID: Autoridad de costos PAPER incompatible")

    monkeypatch.setattr(services, "ensure_reports", invalid_report)
    monkeypatch.setattr(services, "send_closing_summary", lambda *_: summaries.append("sent"))

    services.service_tick(store, "CLOSED")
    services.service_tick(store, "CLOSED")
    assert attempts == ["report"]
    assert summaries == []
    with store.connect() as connection:
        record = connection.execute(
            "SELECT state,detail,last_success_at FROM operational_jobs WHERE job_key='REPORTS'"
        ).fetchone()
    assert record[0] == "ROJO"
    assert record[1] == "POSTCLOSE_CAUCION_LEDGER_INVALID"
    assert record[2] is None


def test_postclose_successful_reports_run_once_per_trading_date(tmp_path, monkeypatch):
    _fixed_postclose_clock(monkeypatch)
    store = PaperStore(str(tmp_path / "paper.db"))
    services.init_schema(store)
    monkeypatch.setattr(services, "backups_suspended", lambda: True)
    monkeypatch.setattr(services, "collect_sre", lambda *_: None)
    monkeypatch.setattr(services, "refresh_financial", lambda *_: None)
    attempts = []

    def completed_report(*_args, **_kwargs):
        attempts.append("report")
        services._job(store, "REPORTS", "VERDE", "Informes actualizados", success=True)

    monkeypatch.setattr(services, "ensure_reports", completed_report)
    summaries = []
    monkeypatch.setattr(services, "send_closing_summary", lambda *_:
                        summaries.append("summary") or "EN_COLA")
    services.service_tick(store, "CLOSED")
    services.service_tick(store, "CLOSED")
    assert attempts == ["report"]
    assert summaries == ["summary"]


def test_unrecognized_report_value_error_is_not_swallowed(tmp_path, monkeypatch):
    import pytest

    _fixed_postclose_clock(monkeypatch)
    store = PaperStore(str(tmp_path / "paper.db"))
    services.init_schema(store)
    monkeypatch.setattr(services, "backups_suspended", lambda: True)
    monkeypatch.setattr(services, "collect_sre", lambda *_: None)
    monkeypatch.setattr(services, "refresh_financial", lambda *_: None)
    monkeypatch.setattr(services, "ensure_reports", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(ValueError("UNEXPECTED_BAD_RESULT")))
    with pytest.raises(ValueError, match="UNEXPECTED_BAD_RESULT"):
        services.service_tick(store, "CLOSED")

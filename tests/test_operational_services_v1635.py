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
import bi_operational_services as services


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

"""R162: PDF/Telegram EOD must distinguish provisional from exact-source proof.

OFFHOST fixtures only; no broker requests or runtime/PPI Watch mutation.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from be_paper_engine import PaperStore
import bi_operational_services as reports


DAY = "2026-11-02"
RECEIPT = "session=2026-11-02; source_session_verified=2026-11-02:1/1; readiness=NONE"
FULL_RECEIPT = (
    f"session={DAY}; source_session_verified={DAY}:1/1; "
    "source_raw_provenance=OBSERVER_PPI_HISTORY_CANONICAL_V2_RAW_FULL_KEY; "
    "Lote aceptado=1/1; parciales=0; errores=0; cobertura exacta 1/1; "
    "suficientes30=1; faltantes=0; cohort=" + "c" * 64 + "; "
    f"consumer=PPI_HISTORY_INGEST_MONITOR; cut={DAY}T18:40:00-03:00; "
    f"session_requested={DAY}; readiness=NONE"
)


def clock(at="2026-11-02T18:45:00-03:00"):
    return datetime.fromisoformat(at)


def source_store(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    reports.init_schema(store)
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS source_sync(
          source TEXT PRIMARY KEY,status TEXT,last_attempt_at TEXT,
          last_success_at TEXT,items INTEGER,detail TEXT)""")
    return store


def put_source(store, *, state="VERDE", success="2026-11-02T18:40:00-03:00",
               attempt=None, detail=FULL_RECEIPT):
    with store.connect() as c:
        c.execute("""INSERT OR REPLACE INTO source_sync
          (source,status,last_attempt_at,last_success_at,items,detail)
          VALUES('PPI_PRODUCTION_HISTORY',?,?,?,?,?)""",
                  (state, success if attempt is None else attempt, success, 1, detail))


@pytest.mark.parametrize("case,fields", [
    ("AMBER", {"state": "AMARILLO"}),
    ("NO_ATTESTATION", {"detail": "session=2026-11-02; readiness=NONE"}),
    ("LEGACY_MARKER_WITHOUT_RAW_PROOF", {"detail": RECEIPT}),
    ("WRONG_RAW", {"detail": FULL_RECEIPT.replace(
        "OBSERVER_PPI_HISTORY_CANONICAL_V2_RAW_FULL_KEY", "REQUESTED_ONLY")}),
    ("MISSING_COHORT", {"detail": FULL_RECEIPT.replace("cohort=" + "c" * 64 + "; ", "")}),
    ("BAD_CONSUMER", {"detail": FULL_RECEIPT.replace(
        "consumer=PPI_HISTORY_INGEST_MONITOR", "consumer=OTHER")}),
    ("MISSING_READINESS", {"detail": FULL_RECEIPT.replace("readiness=NONE", "")}),
    ("EMPTY", {"detail": ""}),
    ("ZERO", {"detail": "source_session_verified=2026-11-02:0/1"}),
    ("PARTIAL", {"detail": "source_session_verified=2026-11-02:1/2"}),
    ("STALE", {"success": "2026-11-01T18:40:00-03:00"}),
    ("EARLY", {"success": "2026-11-02T18:14:59-03:00"}),
    ("FUTURE", {"success": "2026-11-02T18:46:00-03:00"}),
    ("STALE_ATTEMPT", {"attempt": "2026-11-01T18:40:00-03:00"}),
    ("FUTURE_ATTEMPT", {"attempt": "2026-11-02T18:46:00-03:00"}),
    ("NAIVE_ATTEMPT", {"attempt": "2026-11-02T18:40:00"}),
    ("OTHER_SESSION", {"detail": "source_session_verified=2026-11-01:1/1"}),
    ("REPLAY", {"detail": RECEIPT + "; " + RECEIPT}),
    ("NAIVE", {"success": "2026-11-02T18:40:00"}),
    ("MALFORMED", {"detail": "source_session_verified=2026-11-02:bogus"}),
    ("SIGNED", {"detail": "source_session_verified=2026-11-02:+1/+1"}),
    ("PADDED", {"detail": "source_session_verified=2026-11-02:01/01"}),
    ("MISSING_SESSION", {"detail": "source_session_verified=2026-11-02:1/1; session_requested=2026-11-02"}),
    ("YESTERDAY_SESSION", {"detail": "session=2026-11-01; source_session_verified=2026-11-02:1/1"}),
    ("DUPLICATE_SESSION", {"detail": "session=2026-11-02; session=2026-11-02; source_session_verified=2026-11-02:1/1"}),
    ("CONTRADICTORY_SESSIONS", {"detail": "session=2026-11-01; session=2026-11-02; source_session_verified=2026-11-02:1/1"}),
    ("CONTRADICTORY_VERIFIED_MARKERS", {"detail": "session=2026-11-02; source_session_verified=2026-11-01:1/1; source_session_verified=2026-11-02:1/1"}),
    ("MISMATCH_REQUESTED", {"detail": FULL_RECEIPT.replace(
        "session_requested=2026-11-02", "session_requested=2026-11-01")}),
    ("DUPLICATE_REQUESTED", {"detail": FULL_RECEIPT + "; session_requested=2026-11-02"}),
])
def test_unverified_source_never_means_final_eod(tmp_path, case, fields):
    store = source_store(tmp_path)
    put_source(store, **fields)
    assert reports._postclose_history_source_ready(store, clock()) is False, case


def test_missing_source_and_pre_floor_are_provisional(tmp_path):
    store = source_store(tmp_path)
    assert reports._postclose_history_source_ready(store, clock()) is False
    put_source(store)
    assert reports._postclose_history_source_ready(store, clock("2026-11-02T18:08:00-03:00")) is False
    assert reports._postclose_history_source_ready(store, clock()) is True
    assert reports._report_eod_source_status(
        store, "DIARIO", DAY, f"{DAY}T00:00:00-03:00",
        "2026-11-03T00:00:00-03:00", datetime(2026, 11, 2, 18, 45)) == (
        "PPI_HISTORY_PROVISIONAL")


def test_existing_producer_receipt_with_session_requested_is_accepted(tmp_path):
    store = source_store(tmp_path)
    put_source(store, detail=FULL_RECEIPT)
    # `session_requested` is a diagnostic coverage input, never a substitute
    # for the authoritative `session` and verified exact-key publication.
    assert reports._postclose_history_source_ready(store, clock()) is True


def test_nontrading_day_cannot_certify_eod_source(tmp_path, monkeypatch):
    import ak_byma_calendar

    store = source_store(tmp_path)
    put_source(store)
    monkeypatch.setattr(ak_byma_calendar, "es_dia_habil_operativo", lambda _day: False)
    assert reports._postclose_history_source_ready(store, clock()) is False


def test_bi_delegates_raw_validation_to_shared_pure_parser(tmp_path, monkeypatch):
    import rc6_postclose_source_contract as contract

    store = source_store(tmp_path)
    put_source(store)
    seen = []

    def reject(detail, session):
        seen.append((detail, session))
        return None

    monkeypatch.setattr(contract, "verified_ppi_eod_detail", reject)
    monkeypatch.setattr(contract, "postclose_eod_receipt", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("NO_EXTRA_SNAPSHOT_READ")))
    assert reports._postclose_history_source_ready(store, clock()) is False
    assert seen == [(FULL_RECEIPT, DAY)]


def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock().astimezone(tz) if tz else clock()

    monkeypatch.setattr(reports, "datetime", Clock)


def empty_period(*_args):
    return {
        "positions": [], "closed": [], "realizations": [], "decisions": [],
        "news": [], "gates": [], "cauciones_matured": [], "pnl": 0,
        "wins": None, "win_rate": None, "pnl_by_currency": {},
        "unverified_closed_by_currency": {},
    }


def render_report_and_summary(tmp_path, monkeypatch, *, source_detail):
    store = source_store(tmp_path)
    fixed_clock(monkeypatch)
    monkeypatch.setattr(reports, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(reports, "_period_data", empty_period)
    if source_detail is not None:
        put_source(store, detail=source_detail)
    pdf_path, ai_path = reports.generate_report(
        store, "DIARIO", DAY, f"{DAY}T00:00:00-03:00", "2026-11-03T00:00:00-03:00")
    assert pdf_path is not None and Path(pdf_path).read_bytes().startswith(b"%PDF")
    payload = json.loads(Path(ai_path).read_text(encoding="utf-8"))
    with store.connect() as c:
        registry = c.execute("SELECT state,detail FROM report_registry WHERE period_type='DIARIO'").fetchone()
    assert reports.send_closing_summary(store) == "EN_COLA"
    with store.connect() as c:
        body = c.execute("SELECT body FROM paper_notification_outbox WHERE kind='CLOSING_SUMMARY'").fetchone()[0]
    return payload, registry, body


@pytest.mark.parametrize("source_detail,expected", [
    (None, "PPI_HISTORY_PROVISIONAL"),
    (RECEIPT, "PPI_HISTORY_PROVISIONAL"),
    (FULL_RECEIPT, "PPI_HISTORY_EXACT_SESSION_VERIFIED"),
])
def test_pdf_json_and_telegram_use_only_scoped_source_status(
        tmp_path, monkeypatch, source_detail, expected):
    payload, registry, body = render_report_and_summary(
        tmp_path, monkeypatch, source_detail=source_detail)
    assert payload["fuente_historial_eod"] == expected
    assert f"Fuente histórica EOD PPI: {expected}" in body
    assert registry["state"] == "AMARILLO"  # Real orders are still unattested.
    assert expected in registry["detail"]
    assert "CONTENIDO_NO_VERIFICADO" in registry["detail"]
    assert payload["ordenes_reales"] == "NO_VERIFICADO"
    assert "Órdenes reales: NO_VERIFICADO" in body
    assert "Órdenes reales: 0" not in body
    assert payload["fuente_historial_eod"] != "VERIFIED"  # Never imply whole product certification.


def test_old_daily_and_multi_day_reports_remain_provisional(tmp_path, monkeypatch):
    store = source_store(tmp_path)
    put_source(store)
    fixed_clock(monkeypatch)
    monkeypatch.setattr(reports, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(reports, "_period_data", empty_period)
    monkeypatch.setattr(reports, "_pdf", lambda _story, path: path.write_bytes(b"%PDF-synthetic"))
    for period_type, period_key, start, end in (
        ("DIARIO", "2026-11-01", "2026-11-01T00:00:00-03:00", "2026-11-02T00:00:00-03:00"),
        ("IA_SEMANAL", "2026-10-27_2026-11-02", "2026-10-27T00:00:00-03:00", "2026-11-03T00:00:00-03:00"),
    ):
        _, ai_path = reports.generate_report(store, period_type, period_key, start, end)
        story = json.loads(Path(ai_path).read_text(encoding="utf-8"))
        assert story["fuente_historial_eod"] == "PPI_HISTORY_PROVISIONAL"


def test_pdf_visibly_prints_scope_and_provisional_state(tmp_path, monkeypatch):
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    paragraphs = []
    original = SimpleDocTemplate.build

    def collect(self, flowables, *args, **kwargs):
        paragraphs.extend(item.text for item in flowables if isinstance(item, Paragraph))
        return original(self, flowables, *args, **kwargs)

    monkeypatch.setattr(SimpleDocTemplate, "build", collect)
    story = reports._report_story("Fixture", DAY, empty_period())
    reports._pdf(story, tmp_path / "provisional.pdf")
    assert any("Fuente histórica EOD PPI: PPI_HISTORY_PROVISIONAL" in value
               for value in paragraphs)
    assert any("no certifica PnL ni órdenes reales" in value for value in paragraphs)


def test_provisional_source_does_not_block_paper_service_or_summary(tmp_path, monkeypatch):
    store = source_store(tmp_path)
    fixed_clock(monkeypatch)
    monkeypatch.setattr(reports, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(reports, "_period_data", empty_period)
    monkeypatch.setattr(reports, "backups_suspended", lambda: True)
    monkeypatch.setattr(reports, "collect_sre", lambda *_: None)
    monkeypatch.setattr(reports, "refresh_financial", lambda *_: None)
    reports.service_tick(store, "CLOSED", force=True)
    with store.connect() as c:
        report = c.execute("SELECT state,detail FROM operational_jobs WHERE job_key='REPORTS'").fetchone()
        summary = c.execute("SELECT body,state FROM paper_notification_outbox "
                            "WHERE kind='CLOSING_SUMMARY'").fetchone()
    assert report["state"] == "AMARILLO" and "CONTENIDO_NO_VERIFICADO" in report["detail"]
    assert summary["state"] == "PENDING" and "PPI_HISTORY_PROVISIONAL" in summary["body"]

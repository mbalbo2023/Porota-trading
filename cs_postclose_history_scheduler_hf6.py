"""HF6-v2: scheduler lógico del batch histórico post-cierre.

No reemplaza el calendario de mercado. Sólo permite Data912 cuando la fase
está CLOSED, el día es hábil y ya pasó la hora configurada. Registra una
corrida diaria y métricas del History Store v2.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma_calendar
import cr_data912_reconcile_hf6 as reconcile

TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
SOURCE = "DATA912_HISTORICAL_BATCH_V2"

DDL = """
CREATE TABLE IF NOT EXISTS postclose_history_runs_v2(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  local_date TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  source TEXT NOT NULL,
  state TEXT NOT NULL,
  selected INTEGER NOT NULL DEFAULT 0,
  successful INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  without_history INTEGER NOT NULL DEFAULT 0,
  versions_appended INTEGER NOT NULL DEFAULT 0,
  canonical_updates INTEGER NOT NULL DEFAULT 0,
  protected_rows INTEGER NOT NULL DEFAULT 0,
  detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_postclose_history_runs_v2_date
  ON postclose_history_runs_v2(local_date,source,id DESC);
"""


def init_schema(store) -> None:
    with store.connect() as c:
        c.executescript(DDL)


def _local(now=None) -> datetime:
    value = now or datetime.now(TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def should_run(store, *, phase: str, now=None) -> tuple[bool, str]:
    current = _local(now)
    if str(phase).upper() != "CLOSED":
        return False,"PHASE_NOT_CLOSED"
    try:
        if not byma_calendar.es_dia_habil_operativo(current.date()):
            return False,"NON_BUSINESS_DAY"
    except Exception:
        return False,"CALENDAR_UNAVAILABLE"
    if not reconcile.due_now(current):
        return False,"BEFORE_POSTCLOSE_WINDOW"

    init_schema(store)
    day = current.date().isoformat()
    with store.connect() as c:
        done = c.execute(
            """SELECT 1 FROM postclose_history_runs_v2
               WHERE local_date=? AND source=?
                 AND state IN ('OK','PARTIAL','ERROR') LIMIT 1""",
            (day,SOURCE),
        ).fetchone()
    if done:
        return False,"ALREADY_ATTEMPTED_TODAY"
    return True,"DUE"


def run_if_due(store, *, phase: str, now=None, batch_limit=None) -> dict:
    current = _local(now)
    allowed,reason = should_run(store,phase=phase,now=current)
    if not allowed:
        return {"ran":False,"reason":reason,"execution_allowed":False}

    started_at=current.isoformat()
    local_date=current.date().isoformat()
    init_schema(store)
    with store.connect() as c:
        cur=c.execute(
            """INSERT INTO postclose_history_runs_v2(
               local_date,started_at,source,state,detail_json)
               VALUES(?,?,?,'RUNNING','{}')""",
            (local_date,started_at,SOURCE),
        )
        run_id=int(cur.lastrowid)

    try:
        result=reconcile.run(store,batch_limit=batch_limit)
        state="PARTIAL" if result.get("failed") or result.get("without_history") else "OK"
        finished_at=datetime.now(TZ).isoformat()
        with store.connect() as c:
            c.execute(
                """UPDATE postclose_history_runs_v2 SET
                   finished_at=?,state=?,selected=?,successful=?,failed=?,
                   without_history=?,versions_appended=?,canonical_updates=?,
                   protected_rows=?,detail_json=? WHERE id=?""",
                (
                    finished_at,state,int(result.get("selected") or 0),
                    int(result.get("successful") or 0),int(result.get("failed") or 0),
                    int(result.get("without_history") or 0),
                    int(result.get("versions_appended") or 0),
                    int(result.get("canonical_updates") or 0),
                    int(result.get("protected_rows") or 0),
                    json.dumps(result,ensure_ascii=False,sort_keys=True,default=str)[:20000],
                    run_id,
                ),
            )
        return {"ran":True,"run_id":run_id,"state":state,
                "execution_allowed":False,**result}
    except Exception as exc:
        finished_at=datetime.now(TZ).isoformat()
        detail={"error_class":type(exc).__name__,"detail":str(exc)[:1000],
                "execution_allowed":False}
        with store.connect() as c:
            c.execute(
                """UPDATE postclose_history_runs_v2 SET
                   finished_at=?,state='ERROR',detail_json=? WHERE id=?""",
                (finished_at,json.dumps(detail,ensure_ascii=False,sort_keys=True),run_id),
            )
        return {"ran":True,"run_id":run_id,"state":"ERROR",
                "execution_allowed":False,**detail}

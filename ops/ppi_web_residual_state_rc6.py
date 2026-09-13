#!/usr/bin/env python3
"""Durable orchestration state for RC6 PPI Web residual completion.

No network/browser/broker capability exists here. This module only owns the
server-side control SQLite and atomic status.json used to resume independently
of SSH, GitHub Actions, or a ChatGPT conversation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VALID_STATES = {"PENDING", "RUNNING", "DONE_VALID", "DONE_PARTIAL", "DONE_EMPTY", "ERROR"}
TERMINAL = {"DONE_VALID", "DONE_PARTIAL", "DONE_EMPTY", "ERROR"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ResidualState:
    def __init__(self, db_path: str | Path, status_path: str | Path):
        self.db_path = Path(db_path)
        self.status_path = Path(status_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema()

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        return c

    def _schema(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS residual_run(
              run_id TEXT PRIMARY KEY,
              manifest_sha256 TEXT NOT NULL,
              manifest_rows INTEGER NOT NULL,
              status TEXT NOT NULL,
              heartbeat TEXT NOT NULL,
              started_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS residual_task(
              run_id TEXT NOT NULL,
              symbol TEXT NOT NULL,
              instrument_type TEXT NOT NULL,
              market TEXT NOT NULL,
              settlement TEXT NOT NULL,
              residual_class TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'PENDING',
              attempts INTEGER NOT NULL DEFAULT 0,
              provider_rows INTEGER NOT NULL DEFAULT 0,
              valid_rows INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT '',
              started_at TEXT,
              finished_at TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(run_id,symbol,instrument_type,market,settlement)
            );
            CREATE INDEX IF NOT EXISTS idx_residual_task_state
              ON residual_task(run_id,state,instrument_type,symbol);
            """)

    def seed(self, run_id: str, manifest_sha256: str, rows: list[dict]):
        now = now_iso()
        with self.connect() as c:
            prior = c.execute("SELECT manifest_sha256,manifest_rows FROM residual_run WHERE run_id=?", (run_id,)).fetchone()
            if prior and (prior[0] != manifest_sha256 or int(prior[1]) != len(rows)):
                raise RuntimeError("RUN_ID_MANIFEST_MISMATCH")
            c.execute("""INSERT OR IGNORE INTO residual_run
              (run_id,manifest_sha256,manifest_rows,status,heartbeat,started_at,updated_at)
              VALUES(?,?,?,?,?,?,?)""", (run_id, manifest_sha256, len(rows), "READY", now, now, now))
            for r in rows:
                key = tuple(str(r[k]).strip().upper() for k in ("symbol","instrument_type","market","settlement"))
                c.execute("""INSERT OR IGNORE INTO residual_task
                  (run_id,symbol,instrument_type,market,settlement,residual_class,state,updated_at)
                  VALUES(?,?,?,?,?,?,?,?)""", (run_id,*key,str(r["residual_class"]),"PENDING",now))
            count = c.execute("SELECT count(*) FROM residual_task WHERE run_id=?", (run_id,)).fetchone()[0]
            if int(count) != len(rows):
                raise RuntimeError(f"TASK_COUNT_MISMATCH:{count}:{len(rows)}")
        self.publish(run_id)

    def recover_orphan_running(self, run_id: str) -> int:
        now = now_iso()
        with self.connect() as c:
            cur = c.execute("""UPDATE residual_task SET state='PENDING',detail='RECOVERED_ORPHAN_RUNNING',
                started_at=NULL,updated_at=? WHERE run_id=? AND state='RUNNING'""", (now, run_id))
            n = cur.rowcount
        self.publish(run_id)
        return int(n)

    def claim_next(self, run_id: str, *, family: str | None = None):
        now = now_iso()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            sql = "SELECT symbol,instrument_type,market,settlement,residual_class FROM residual_task WHERE run_id=? AND state='PENDING'"
            args: list[object] = [run_id]
            if family:
                sql += " AND instrument_type=?"; args.append(family.upper())
            sql += " ORDER BY instrument_type,symbol,market,settlement LIMIT 1"
            row = c.execute(sql, args).fetchone()
            if not row:
                c.commit(); return None
            key = row[:4]
            c.execute("""UPDATE residual_task SET state='RUNNING',attempts=attempts+1,started_at=?,updated_at=?
                WHERE run_id=? AND symbol=? AND instrument_type=? AND market=? AND settlement=? AND state='PENDING'""",
                (now,now,run_id,*key))
            if c.total_changes != 1:
                c.rollback(); return None
            c.commit()
        self.publish(run_id)
        return {"symbol":row[0],"instrument_type":row[1],"market":row[2],"settlement":row[3],"residual_class":row[4]}

    def finish(self, run_id: str, task: dict, state: str, *, provider_rows=0, valid_rows=0, detail=""):
        state = state.upper()
        if state not in TERMINAL:
            raise ValueError("terminal state required")
        now = now_iso()
        key = tuple(str(task[k]).strip().upper() for k in ("symbol","instrument_type","market","settlement"))
        with self.connect() as c:
            cur = c.execute("""UPDATE residual_task SET state=?,provider_rows=?,valid_rows=?,detail=?,finished_at=?,updated_at=?
               WHERE run_id=? AND symbol=? AND instrument_type=? AND market=? AND settlement=? AND state='RUNNING'""",
               (state,int(provider_rows),int(valid_rows),str(detail)[:1500],now,now,run_id,*key))
            if cur.rowcount != 1:
                raise RuntimeError("TASK_NOT_RUNNING")
        self.publish(run_id)

    def summary(self, run_id: str) -> dict:
        with self.connect() as c:
            run = c.execute("SELECT manifest_sha256,manifest_rows,status,heartbeat,started_at,updated_at,completed_at FROM residual_run WHERE run_id=?",(run_id,)).fetchone()
            if not run:
                raise RuntimeError("RUN_NOT_FOUND")
            counts = dict(c.execute("SELECT state,count(*) FROM residual_task WHERE run_id=? GROUP BY state",(run_id,)).fetchall())
            fam = dict(c.execute("SELECT instrument_type,count(*) FROM residual_task WHERE run_id=? AND state='PENDING' GROUP BY instrument_type",(run_id,)).fetchall())
            current = c.execute("SELECT symbol,instrument_type,market,settlement,started_at FROM residual_task WHERE run_id=? AND state='RUNNING' ORDER BY started_at LIMIT 1",(run_id,)).fetchone()
        total=int(run[1]); terminal=sum(int(counts.get(s,0)) for s in TERMINAL)
        return {
            "run_id":run_id,"manifest_sha256":run[0],"total":total,"terminal":terminal,
            "progress_pct":round((100.0*terminal/total) if total else 100.0,2),
            "counts":{k:int(v) for k,v in sorted(counts.items())},
            "pending_by_family":{k:int(v) for k,v in sorted(fam.items())},
            "current":list(current) if current else None,"updated_at":now_iso(),
        }

    def publish(self, run_id: str) -> dict:
        data=self.summary(run_id)
        payload=json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+"\n"
        fd,tmp=tempfile.mkstemp(prefix="status.",suffix=".tmp",dir=self.status_path.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                f.write(payload); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,self.status_path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return data

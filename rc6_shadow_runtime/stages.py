"""Read existing native signal/risk evidence; SHADOW results have no authority."""
from datetime import timedelta
from pathlib import Path
import sqlite3
import time

from co_market_sessions_hf6 import TZ
from bq_exit_policy import PaperSessionPolicy
from rc6_dynamic_universe.common import stamp
from rc6_dynamic_universe.economics import shadow_economics
from rc6_performance.costs import paper_fee_model


def enrich_pipeline(database, report, *, as_of):
    at = stamp(as_of)
    c = sqlite3.connect(Path(database).resolve(strict=True).as_uri() + "?mode=ro",
                        uri=True, timeout=.005)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA query_only=ON")
        end = time.monotonic() + .25
        c.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        risks = {}
        if "paper_daily_risk" in tables:
            for row in c.execute("SELECT currency,state,evaluated_at,latched_at FROM paper_daily_risk WHERE day=? LIMIT 8",
                    (at.astimezone(TZ).date().isoformat(),)):
                risks[row["currency"]] = dict(row)
        candidates = {}
        if "scalping_candidates" in tables:
            for row in c.execute("""SELECT symbol,asset_class,market,currency,settlement,
                    evaluated_at,action,score,reason,points FROM scalping_candidates
                    WHERE julianday(evaluated_at)<=julianday(?)
                    AND julianday(evaluated_at)>=julianday(?) ORDER BY id DESC LIMIT 2000""",
                    (at.isoformat(), (at - timedelta(seconds=120)).isoformat())):
                key = tuple(row[n] for n in ("symbol", "asset_class", "market", "currency", "settlement"))
                candidates.setdefault(key, dict(row))
        books = {}
        for row in c.execute("""SELECT * FROM market_snapshots
                WHERE julianday(observed_at)<=julianday(?)
                AND julianday(observed_at)>=julianday(?) ORDER BY id DESC LIMIT 2000""",
                (at.isoformat(), (at - timedelta(seconds=120)).isoformat())):
            key = tuple(row[n] for n in ("symbol", "asset_class", "market", "currency", "settlement"))
            books.setdefault(key, dict(row))
        session_policy = PaperSessionPolicy()
        deadline = at.astimezone(TZ).replace(hour=session_policy.close_time.hour,
            minute=session_policy.close_time.minute, second=0, microsecond=0)
        deadline -= timedelta(minutes=session_policy.exit_minutes)
        for engine, plan in report["engines"].items():
            for row in plan["telemetry"]:
                key = tuple(row["identity"])
                ready = row["pipeline"]["SIGNAL_READY"]
                signal = {"status": "NOT_READY", "reason": "WARMUP_OR_CAPACITY_INCOMPLETE",
                          "score_is_probability": False, "entry_authority": False}
                if ready:
                    native = candidates.get(key) if engine == "SCALPING" else None
                    signal = {"status": "NO_VERIFICADO", "reason": "NATIVE_SIGNAL_UNAVAILABLE",
                              "score_is_probability": False, "entry_authority": False}
                    if native:
                        signal.update(status="OBSERVED_NATIVE_SIGNAL", action=native["action"],
                            score=native["score"], reason=native["reason"],
                            evaluated_at=native["evaluated_at"], samples=native["points"],
                            provenance="cf_intraday_scalping.evaluate_candidate; unchanged native guards")
                row["signal_shadow"] = signal
                economics = {"status": "NO_VERIFICADO", "reason": "SIGNAL_OR_BOOK_UNAVAILABLE",
                             "mode": "SHADOW", "decision_effect": "NONE", "real_order_routes": []}
                if ready and key in books and deadline > at:
                    economics = shadow_economics(books[key], decision_at=at, eod_at=deadline,
                        quantity=1, multiplier=1, model=None, fees=paper_fee_model(key[1]))
                    economics["sizing_basis"] = "UNIT_DIAGNOSTIC; factual sizing NOT_CALLED"
                    economics["eod_at"] = deadline.isoformat()
                    economics["movement_model_basis"] = "NO_VERIFICADO; prospective entry models in economic_exit_lab"
                risk = {"status": "NO_VERIFICADO", "reason": "FRESH_NATIVE_RISK_UNAVAILABLE",
                        "decision_effect": "NONE", "admission": "NOT_CALLED", "entry_authority": False}
                native_risk = risks.get(key[3])
                if native_risk and 0 <= (at - stamp(native_risk["evaluated_at"])).total_seconds() <= 120:
                    risk.update(status="OBSERVED_NATIVE_RISK", native_state=native_risk["state"],
                                evaluated_at=native_risk["evaluated_at"],
                                hard_stop_latched=bool(native_risk["latched_at"]))
                row.update(economics_shadow=economics, risk_shadow=risk,
                           factual_execution="NOT_CALLED")
                row["pipeline"].update(SIGNAL_READY_SHADOW=ready,
                    ECONOMICS_SHADOW=economics["status"], RISK_SHADOW=risk["status"])
                row["economics_result"], row["risk_result"] = economics["status"], risk["status"]
                assert not row["entry_authority"]
        return report
    finally:
        c.close()

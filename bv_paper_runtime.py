"""Runtime PAPER: reloj, escáner, lector, avisos y archivo en procesos distintos.

No envía órdenes. Compartir SQLite permite que una llamada lenta a PPI/Gemini
no detenga los vencimientos ni el estado de salida. El lector de salidas usa
una sesión PPI propia; cuotas y coexistencia de sesiones requieren prueba PPI
antes de promover. Las excepciones no se convierten en fills.
"""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from be_paper_engine import PaperBroker, PaperStore, now_iso
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy
from cg_paper_workspace import DB_ENV, runtime_store

ROOT = Path(__file__).resolve().parent


def broker_from_environment(store, **overrides):
    values = dict(
        initial_cash=os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000"),
        initial_cash_usd=os.getenv("PAPER_INITIAL_CAPITAL_USD", "0"),
        initial_cash_by_currency={"USD_MEP": os.getenv("PAPER_INITIAL_CAPITAL_USD_MEP", "0"),
                                  "USD_CCL": os.getenv("PAPER_INITIAL_CAPITAL_USD_CCL", "0")},
        risk_pct=os.getenv("PAPER_RISK_PER_TRADE", "0.002"),
        max_positions=os.getenv("PAPER_MAX_OPEN_POSITIONS", "5"),
        max_position_pct=os.getenv("PAPER_MAX_POSITION_PCT", "0.25"),
        max_total_exposure_pct=os.getenv("PAPER_MAX_TOTAL_EXPOSURE_PCT", "0.60"),
        clock_fn=now_iso, session_policy=PaperSessionPolicy(), require_supervisor=True,
        quote_max_age_seconds=int(os.getenv("PAPER_BOOK_MAX_AGE_SECONDS", "120")),
        trade_max_age_seconds=int(os.getenv("PAPER_TRADE_MAX_AGE_SECONDS", "900")),
        signal_min_samples=int(os.getenv("PAPER_SIGNAL_MIN_SAMPLES", "6")),
        signal_window_minutes=int(os.getenv("PAPER_SIGNAL_WINDOW_MINUTES", "90")),
        score_threshold=os.getenv("PAPER_SCORE_THRESHOLD", "0.62"),
        ai_mode=os.getenv("PAPER_AI_GATE_MODE", "OFF"),
        economics_mode=os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW"),
        min_net_reward_risk=os.getenv("PAPER_MIN_NET_REWARD_RISK", "1.20"),
        stop_loss_pct=os.getenv("PAPER_STOP_LOSS_PCT", "0.02"),
        target_gain_pct=os.getenv("PAPER_TARGET_GAIN_PCT", "0.05"),
        daily_loss_pct=os.getenv('MAX_DAILY_LOSS_PCT','2.5'),
        daily_soft_stop_pct=os.getenv('PAPER_DAILY_SOFT_STOP_PCT'))
    values.update(overrides)
    return PaperBroker(store, **values)


class ChildProcesses:
    """Reinicia sólo el hijo caído; no espera su trabajo para avanzar el reloj."""
    def __init__(self, commands, *, cooldown_seconds=30, spawn=subprocess.Popen, clock=time.monotonic):
        self.commands, self.cooldown = commands, cooldown_seconds
        self.spawn, self.clock = spawn, clock
        self.processes, self.next_start = {}, {}

    def poll(self):
        now = self.clock()
        for name, command in self.commands.items():
            process = self.processes.get(name)
            if process is not None and process.poll() is not None:
                self.processes.pop(name)
                self.next_start[name] = now + self.cooldown
            if name not in self.processes and now >= self.next_start.get(name, 0):
                try:
                    self.processes[name] = self.spawn(command, cwd=ROOT)
                except OSError:
                    self.next_start[name] = now + self.cooldown

    def close(self):
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        for process in self.processes.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def run_clock(store, children, stop, *, clock_fn=now_iso, interval=5):
    broker = broker_from_environment(store, clock_fn=clock_fn)
    supervisor = PositionExitSupervisor(broker, clock_fn=clock_fn,
        session_policy=broker.session_policy,
        max_hold_minutes=int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360")))
    last_valuation = 0.0
    try:
        while not stop.is_set():
            children.poll()
            try:
                supervisor.tick()
                if time.monotonic() - last_valuation >= 30:
                    broker.mark_equity({}, as_of=clock_fn())
                    last_valuation = time.monotonic()
            except Exception as exc:
                supervisor.heartbeat(clock_fn(), "DEGRADED", type(exc).__name__)
            stop.wait(interval)
    finally:
        try:
            supervisor.heartbeat(clock_fn(), "STOPPED", "Runtime detenido; nuevas entradas bloqueadas")
        finally:
            children.close()


def collect_exit_books(reader, store, policy, at, *, should_stop=lambda: False,
                       pause=lambda: None, beat=lambda: None):
    """Un book por abierta; jamás depende de último negocio o de Gemini."""
    from bf_production_paper_observer import normalize_quote
    from bd_ppi_readonly_guard import retry_read, session_invalid
    import bu_instrument_catalog as catalog
    positions, invalid = store.exit_positions()
    failures = len(invalid)
    for p in positions:
        if should_stop():
            break
        beat()
        try:
            if policy.execution_error(p, at):
                continue
            book = retry_read(lambda: reader.book(
                p["symbol"],p["asset_class"],p["settlement"]), retries=1)
            metadata = catalog.lookup(store,p["symbol"],p["asset_class"],p["settlement"])
            q = normalize_quote(p["symbol"],p["asset_class"],p["settlement"],{},book,metadata=metadata)
            store.add_quote(q)
            if q.time_error(q.observed_at):
                failures += 1
            q.monetary_identity()
        except Exception as exc:
            if session_invalid(exc):
                raise
            failures += 1
            store.event("EXIT_BOOK_ERROR", f"{p['symbol']}: {type(exc).__name__}", p["paper_id"])
        pause()
    return failures


def run_reader(store, stop):
    # Imports de red sólo en el hijo. El reloj padre no instala interceptores
    # ni comparte cliente HTTP mutable con el escáner.
    from bd_ppi_readonly_guard import ProductionMarketReader, session_invalid
    from bf_production_paper_observer import _secret, _support_schema, _market_phase
    if os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() != "1":
        _support_schema(store)
    policy = PaperSessionPolicy()
    reader, next_login = None, 0.0
    def status(state,detail=""):
        with store.connect() as c:
            c.execute("INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,?,?)",
                      (now_iso(),state,detail))
    try:
        while not stop.is_set():
            if _market_phase() != "OPEN":
                status("WAITING_MARKET")
                stop.wait(5)
                continue
            if reader is None:
                if time.monotonic() < next_login:
                    status("COOLDOWN")
                    stop.wait(5)
                    continue
                try:
                    reader = ProductionMarketReader(*_secret(), audit=store.audit_http)
                    reader.login_once()
                    store.event("PPI_LOGIN", "owner=exit_reader")
                    status("READY", "Sesión de lectura iniciada; sin órdenes")
                except Exception as exc:
                    if reader:
                        reader.close()
                    reader = None
                    next_login = time.monotonic() + 900
                    store.event("EXIT_READER_LOGIN_ERROR", type(exc).__name__)
                    status("ERROR",type(exc).__name__)
                    continue
            try:
                failures = collect_exit_books(
                    reader, store, policy, now_iso(), should_stop=stop.is_set,
                    pause=lambda: stop.wait(1),
                    beat=lambda: status("READY", "Recorriendo posiciones abiertas"))
            except Exception as exc:
                if not session_invalid(exc):
                    raise
                store.event("EXIT_READER_SESSION_INVALID", type(exc).__name__)
                status("ERROR", "Sesión PPI expirada; nuevas aperturas bloqueadas hasta reautenticar")
                reader.close()
                reader = None
                next_login = time.monotonic() + 60
                stop.wait(5)
                continue
            status("DEGRADED" if failures else "READY",f"{failures} errores de lectura de abiertas")
            stop.wait(5)
    finally:
        try:
            status("STOPPED")
        finally:
            if reader:
                reader.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ['--exit-reader'], ['--notification-worker'], ['--candle-worker'],
                    ['--intraday-scalping-worker']):
        raise ValueError("Argumentos desconocidos del runtime paper")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    # El padre completa el esquema antes de crear workers. Los hijos reciben
    # esta marca y sólo abren la base, sin volver a ejecutar migraciones pesadas.
    os.environ.pop("POROTA_RUNTIME_SCHEMA_READY", None)
    store = runtime_store()
    # Todas las migraciones operativas se completan una vez en el padre.
    # Scanner, reader, velas y avisos sólo consumen el esquema ya preparado.
    from bf_production_paper_observer import _support_schema
    _support_schema(store)
    os.environ["POROTA_RUNTIME_SCHEMA_READY"] = "1"
    # Los hijos cambian cwd; todos deben heredar la misma ruta absoluta.
    os.environ[DB_ENV] = store.path
    if argv == ["--exit-reader"]:
        run_reader(store, stop)
        return 0
    if argv == ["--notification-worker"]:
        from bn_telegram_bus import run_worker
        run_worker(store,stop,clock_fn=now_iso)
        return 0
    if argv == ["--candle-worker"]:
        from bl_candle_engine import run_worker
        run_worker(store,stop,clock_fn=now_iso)
        return 0
    if argv == ["--intraday-scalping-worker"]:
        from cf_intraday_scalping import run_worker
        run_worker(store,stop,clock_fn=now_iso)
        return 0
    # Un reloj por libro, incluso si se intenta iniciar otro contenedor.
    with open(store.path + ".runtime.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        children = ChildProcesses({
            "scanner": [sys.executable, str(ROOT / "bf_production_paper_observer.py")],
            "exit_reader": [sys.executable, str(Path(__file__).resolve()), "--exit-reader"],
            "notifications": [sys.executable, str(Path(__file__).resolve()), "--notification-worker"],
            "candles": [sys.executable, str(Path(__file__).resolve()), "--candle-worker"],
            **({"intraday_scalping": [sys.executable, str(Path(__file__).resolve()), "--intraday-scalping-worker"]}
              if os.getenv("PAPER_SCALPING_MODE", "OFF").upper() in {"ACTIVE_PAPER", "ACTIVE_OBSERVE"} else {}),
        })
        run_clock(store,children,stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

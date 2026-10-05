"""Runtime PAPER: reloj, escáner, lector, avisos y archivo en procesos distintos.

No envía órdenes. Compartir SQLite permite que una llamada lenta a PPI/Gemini
no detenga los vencimientos ni el estado de salida. El lector de salidas usa
una sesión PPI propia; cuotas y coexistencia de sesiones requieren prueba PPI
antes de promover. Las excepciones no se convierten en fills.
"""
from __future__ import annotations

# Capture native fatal-signal traceback in the container PID 1 runtime.
import faulthandler
faulthandler.enable(all_threads=True)

import fcntl
import json
import re
import tempfile
from contextlib import nullcontext
import os
import signal
import sqlite3
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from be_paper_engine import PaperBroker, PaperStore, now_iso
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy
from cg_paper_workspace import DB_ENV, database_path, runtime_store, artifact_root

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("paper_runtime")


def _sqlite_contention(exc):
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = str(getattr(exc, "sqlite_errorname", "") or "").upper()
    msg = str(exc).lower()
    return code in {"SQLITE_BUSY", "SQLITE_LOCKED"} or "database is locked" in msg or "database table is locked" in msg


def _reader_status_write(store, state, detail=""):
    """Bounded health write; transient SQLite contention cannot kill exit-reader."""
    try:
        with sqlite3.connect(store.path, timeout=0.35) as connection:
            connection.execute("PRAGMA busy_timeout=350")
            connection.execute(
                "INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,?,?)",
                (now_iso(), str(state), str(detail)[:500]),
            )
        return True
    except sqlite3.OperationalError as exc:
        if _sqlite_contention(exc):
            LOG.warning("EXIT_READER_STATUS_SQLITE_CONTENTION:%s",
                        getattr(exc, "sqlite_errorname", "SQLITE_LOCKED"))
            return False
        raise


def _best_effort_runtime_event(store, event_type, detail, paper_id=None):
    """Diagnostic persistence must not turn a lock into an exit-reader crash."""
    try:
        store.event(event_type, detail, paper_id)
        return True
    except sqlite3.OperationalError as exc:
        if _sqlite_contention(exc):
            LOG.warning("%s_DB_EVENT_DROPPED_SQLITE_CONTENTION:%s", event_type, detail)
            return False
        raise



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
    def __init__(self, commands, *, cooldown_seconds=30, startup_grace_seconds=25,
                 spawn=subprocess.Popen, clock=time.monotonic):
        self.commands, self.cooldown = commands, cooldown_seconds
        self.spawn, self.clock = spawn, clock
        # El scanner recibe el primer acceso a SQLite/PPI. Arrancar todos los
        # consumidores juntos generaba I/O local antes de su primer pulso.
        started = self.clock()
        self.processes = {}
        self.health = {name: {"state": "STARTUP_WAIT", "pid": None,
            "restarts": 0, "spawn_failures": 0, "started_at": None,
            "last_exit_at": None, "last_error": None} for name in commands}
        self.next_start = {
            name: (started if name == "scanner" else started + startup_grace_seconds)
            for name in commands
        }

    def poll(self):
        now = self.clock()
        for name, command in self.commands.items():
            process = self.processes.get(name)
            if process is not None and process.poll() is not None:
                self.processes.pop(name)
                self.next_start[name] = now + self.cooldown
                self.health[name].update(state="CRASH_BACKOFF", pid=None,
                    restarts=self.health[name]["restarts"] + 1,
                    last_exit_at=now_iso(), last_error="CHILD_EXITED")
            if name not in self.processes and now >= self.next_start.get(name, 0):
                try:
                    self.processes[name] = self.spawn(command, cwd=ROOT)
                    self.health[name].update(state="RUNNING",
                        pid=getattr(self.processes[name], "pid", None),
                        started_at=now_iso(), last_error=None)
                except OSError:
                    self.next_start[name] = now + self.cooldown
                    self.health[name].update(state="SPAWN_FAILED", pid=None,
                        spawn_failures=self.health[name]["spawn_failures"] + 1,
                        last_error="CHILD_SPAWN_FAILED")

    def snapshot(self):
        return {name: dict(value) for name, value in self.health.items()}

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
        for value in self.health.values():
            value.update(state="STOPPED", pid=None)


def publish_child_health(store, children, *, recorded_at):
    """Atomic allowlisted child health; generation evidence is checked separately."""
    metadata = {}
    try:
        path = ROOT / "POROTA_SOURCE_PROVENANCE.json"
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 32 * 1024**2:
            metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    sha = os.getenv("POROTA_BUILD_SHA") or metadata.get("candidate_sha") or metadata.get("source_sha")
    tree = os.getenv("POROTA_CANDIDATE_TREE_SHA") or metadata.get("candidate_tree_sha") or metadata.get("source_tree_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        sha = None
    if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        tree = None
    budget_observation = {"status": "UNVERIFIED"}
    try:
        from rc6_ppi_global_budget import runtime_budget_snapshot
        budget_observation = runtime_budget_snapshot(store.path, as_of=recorded_at)
    except (ImportError, OSError, ValueError, sqlite3.Error):
        pass
    payload = {"schema": "rc6.runtime-child-health.v1", "recorded_at": recorded_at,
        "ppi_budget": budget_observation,
        "source_sha": sha, "candidate_tree_sha": tree,
        "children": children.snapshot(), "mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    root = artifact_root(store.path)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "runtime-health.json"
    if root.is_symlink() or target.is_symlink() or target.exists() and target.stat().st_nlink != 1:
        raise ValueError("RUNTIME_CHILD_HEALTH_PATH_ALIAS")
    fd, temporary = tempfile.mkstemp(prefix=".runtime-health-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def run_clock(store, children, stop, *, clock_fn=now_iso, interval=5):
    broker = broker_from_environment(store, clock_fn=clock_fn)
    telemetry = None
    try:
        from rc6_performance.capture import ExitTelemetry
        telemetry = ExitTelemetry(str(store.path)+".performance.sqlite")
    except (OSError, ValueError, sqlite3.Error):
        LOG.warning("EXIT_TELEMETRY_INITIALIZATION_UNVERIFIED")
    supervisor = PositionExitSupervisor(broker, clock_fn=clock_fn,
        session_policy=broker.session_policy,
        max_hold_minutes=int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360")), telemetry=telemetry)
    last_valuation = 0.0
    try:
        while not stop.is_set():
            children.poll()
            try:
                publish_child_health(store, children, recorded_at=clock_fn())
            except Exception:
                # Missing/stale health evidence fails the deploy gate, while
                # local IO failure must not interrupt existing-position exits.
                LOG.warning("RUNTIME_CHILD_HEALTH_PUBLICATION_FAILED")
            try:
                supervisor.tick()
                broker.supervise_futures(clock_fn())
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
    from rc6_paper_family_lifecycle import future_position_contract
    for future in getattr(store, 'active_future_positions', lambda: [])():
        positions.append({**future, "asset_class": "FUTUROS", "paper_id": future["lifecycle_id"]})
    failures = len(invalid)
    for p in positions:
        if should_stop():
            break
        beat()
        stage = "EXECUTION_POLICY"
        try:
            is_future = p.get("asset_class") == "FUTUROS"
            if not is_future and policy.execution_error(p, at):
                continue
            original_contract = future_position_contract(p) if is_future else None
            stage = "BROKER_BOOK"
            scope = getattr(reader, "read_scope", None)
            identity = (p["symbol"], p["asset_class"], p["market"], p["currency"], p["settlement"])
            with (scope(priority="EXIT_CRITICAL", identity=identity) if callable(scope) else nullcontext()):
                book = retry_read(lambda: reader.book(
                    p["symbol"],p["asset_class"],p["settlement"]), retries=1)
            stage = "CATALOG_LOOKUP"
            metadata = catalog.lookup(store,p["symbol"],p["asset_class"],p["settlement"],
                                      market=p["market"], currency=p["currency"])
            stage = "NORMALIZE_QUOTE"
            q = normalize_quote(p["symbol"],p["asset_class"],p["settlement"],{},book,metadata=metadata)
            if is_future:
                from dataclasses import replace
                # Request identity is the exact position; catalog changes cannot
                # invent new exit terms or grant fresh entry authority.
                q = replace(q, contract=original_contract, currency=p["currency"],
                            market=p["market"], opening_block_reason="FUTURES_EXIT_ONLY_DURABLE_CONTRACT")
            stage = "PERSIST_QUOTE"
            store.add_quote(q)
            stage = "VALIDATE_QUOTE"
            if q.time_error(q.observed_at):
                failures += 1
            q.monetary_identity()
        except Exception as exc:
            if session_invalid(exc):
                raise
            failures += 1
            sqlite_code = str(getattr(exc, "sqlite_errorname", "") or "")
            suffix = (";sqlite=" + sqlite_code) if sqlite_code else ""
            _best_effort_runtime_event(
                store, "EXIT_BOOK_ERROR",
                f"{p['symbol']}: {type(exc).__name__};stage={stage}{suffix}",
                p["paper_id"])
        pause()
    return failures


def exit_reader_cadence(store, at):
    """Only a verified feasible dynamic envelope can change the read schedule."""
    from rc6_dynamic_universe.promotion import capacity_controller_from_environment
    state=capacity_controller_from_environment(store.path).state(at)
    if state.get("status")!="APPROVED_DYNAMIC":
        return None
    exit_capacity=state.get("exit_capacity",{})
    if exit_capacity.get("status")!="READY":
        return None
    cadence=state["budget_settings"]["critical_book_seconds"]
    if isinstance(cadence,bool) or not isinstance(cadence,int) or cadence<=0:
        return None
    return float(cadence)


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
        return _reader_status_write(store, state, detail)
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
                    reader = ProductionMarketReader(*_secret(), audit=store.audit_http,
                                                    consumer="EXIT_READER", priority="EXIT_CRITICAL")
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
                round_started=time.monotonic()
                cadence=exit_reader_cadence(store,now_iso())
                failures = collect_exit_books(
                    reader, store, policy, now_iso(), should_stop=stop.is_set,
                    pause=(lambda: None) if cadence is not None else (lambda: stop.wait(1)),
                    beat=lambda: status("READY", "Recorriendo posiciones abiertas"))
            except sqlite3.OperationalError as exc:
                if not _sqlite_contention(exc):
                    raise
                status("DEGRADED", "SQLite contention; reintento acotado, sin fill ni orden")
                LOG.warning("EXIT_READER_SQLITE_CONTENTION:%s",
                            getattr(exc, "sqlite_errorname", "SQLITE_LOCKED"))
                stop.wait(1)
                continue
            except Exception as exc:
                if not session_invalid(exc):
                    raise
                _best_effort_runtime_event(store, "EXIT_READER_SESSION_INVALID", type(exc).__name__)
                status("ERROR", "Sesión PPI expirada; nuevas aperturas bloqueadas hasta reautenticar")
                reader.close()
                reader = None
                next_login = time.monotonic() + 60
                stop.wait(5)
                continue
            elapsed=time.monotonic()-round_started
            missed=cadence is not None and elapsed>cadence
            detail=f"{failures} errores de lectura de abiertas; round_seconds={elapsed:.6f}; "
            detail+=(f"critical_cadence_seconds={cadence}; deadline_missed={missed}"
                if cadence is not None else "baseline_cadence=NO_VERIFICADO")
            status("DEGRADED" if failures or missed else "READY",detail)
            # Do not add per-position sleeps and a second five-second pause to
            # an approved EXIT round. Slow transport remains observable.
            stop.wait(max(0.0,cadence-elapsed) if cadence is not None else 5)
    finally:
        try:
            status("STOPPED")
        finally:
            if reader:
                reader.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ['--exit-reader'], ['--notification-worker'], ['--candle-worker'], ['--performance-worker'], ['--dynamic-shadow-worker'],
                    ['--intraday-scalping-worker'], ['--caucion-cash-sweep-worker']):
        raise ValueError("Argumentos desconocidos del runtime paper")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    if argv == ['--dynamic-shadow-worker']:
        # Local-only reader: no runtime_store/PaperBroker/schema/provider in
        # this child. The existing supervisor owns periodic execution/restart.
        from rc6_shadow_runtime.worker import run_worker
        run_worker(database_path(), stop, clock_fn=now_iso)
        return 0
    if argv == ['--performance-worker']:
        from rc6_performance.capture import run_worker
        run_worker(database_path(), stop)
        return 0
    # Sólo el proceso padre prepara el esquema. Los hijos heredan la marca
    # y deben abrir SQLite sin ejecutar DDL ni reconstruir índices: al borrar
    # esa marca cada worker volvía a tomar el lock y el scanner nunca llegaba
    # a su primer login PPI.
    inherited_schema = os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() == "1"
    if argv and inherited_schema:
        # El padre ya verificó identidad, capital y esquema. Evitamos el lock
        # de inicialización y las lecturas WAL concurrentes en cada hijo.
        store = PaperStore(str(database_path()))
    else:
        os.environ.pop("POROTA_RUNTIME_SCHEMA_READY", None)
        store = runtime_store()
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
    if argv == ["--caucion-cash-sweep-worker"]:
        from di_caucion_cash_sweep_runtime_hf6 import run_worker
        run_worker(store, stop, clock_fn=now_iso)
        return 0
    # Un reloj por libro, incluso si se intenta iniciar otro contenedor.
    with open(store.path + ".runtime.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        children = ChildProcesses({
            "scanner": [sys.executable, str(ROOT / "bf_production_paper_observer.py")],
            "performance": [sys.executable, str(Path(__file__).resolve()), "--performance-worker"],
            "dynamic_shadow": [sys.executable, str(Path(__file__).resolve()), "--dynamic-shadow-worker"],
            "exit_reader": [sys.executable, str(Path(__file__).resolve()), "--exit-reader"],
            "notifications": [sys.executable, str(Path(__file__).resolve()), "--notification-worker"],
            "candles": [sys.executable, str(Path(__file__).resolve()), "--candle-worker"],
            **({"intraday_scalping": [sys.executable, str(Path(__file__).resolve()), "--intraday-scalping-worker"]}
              if os.getenv("PAPER_SCALPING_MODE", "OFF").upper() in {"ACTIVE_PAPER", "ACTIVE_OBSERVE"} else {}),
            **({"caucion_cash_sweep": [sys.executable, str(Path(__file__).resolve()), "--caucion-cash-sweep-worker"]}
              if os.getenv("PAPER_CAUCION_SWEEP_MODE", "OFF").upper() == "ACTIVE_PAPER" else {}),
        })
        run_clock(store,children,stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

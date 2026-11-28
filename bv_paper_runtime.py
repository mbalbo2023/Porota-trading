"""Runtime PAPER: reloj, gateway, escáner, lector y archivo aislados.

No envía órdenes. Un hijo gateway es dueño del único login PPI de estos
consumidores; scanner/EXIT/scalping sólo demandan lectura por AF_UNIX.
Esto no prueba exclusividad account-wide ni aislamiento físico del secret
compartido en compose; A670/OPEN permanecen fail-closed sin ese test.
"""
from __future__ import annotations

# Capture native fatal-signal traceback in the container PID 1 runtime.
import faulthandler
faulthandler.enable(all_threads=True)

import fcntl
import errno
import json
import re
import tempfile
from contextlib import closing, contextmanager, nullcontext
import os
import signal
import socket
import sqlite3
import stat
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


def _ppi_gateway_catalog(store, *, expected_publication=None):
    from rc6_ppi_shared_session_owner import (
        PrimaryPPICatalogAuthority, load_primary_ppi_catalog_snapshot,
    )

    rows, query_keys, digest, provider_attested_keys = load_primary_ppi_catalog_snapshot(
        store.path, include_diagnostic_candidates=True,
        expected_publication=expected_publication)
    # Query keys stay separate from the literal PPI_FIELD authority.
    return dict(
        catalog_authority=PrimaryPPICatalogAuthority(
            provider_attested_keys, query_keys=query_keys),
        ppi_catalog_rows=rows, catalog_five_keys=query_keys,
        catalog_source_digest=digest,
        consumer_five_keys={role: list(query_keys)
                            for role in ("scanner", "exit_reader", "scalping")},
    )


def _make_ppi_gateway_owner(store):
    """Concreto owner PAPER0: una fábrica SDK, catálogo literal, guard real."""
    from bd_ppi_readonly_guard import ProductionMarketReader
    from bf_production_paper_observer import _secret
    from rc6_ppi_global_budget import PaperDirectReadBudget
    from rc6_ppi_gateway_reader_adapter import gateway_socket_path
    from rc6_ppi_native_request_receipt import SettlementReceiptSigner, release_coordinates
    from rc6_ppi_shared_session_owner import (
        PPISharedSessionOwner, ObservedPaperCapacityAuthority,
    )

    if os.getenv("POROTA_PPI_DIRECT_REST_RECOVERY") != "1":
        raise RuntimeError("PPI_OBSERVED_DIRECT_BUDGET_NOT_ENABLED")
    catalog = _ppi_gateway_catalog(store)
    budget = PaperDirectReadBudget(store.path)
    capacity = ObservedPaperCapacityAuthority(budget)
    release = release_coordinates()

    class _NoUnverifiedStreamPermit:
        def permits(self, action, *, five_key=None):
            return False  # no account/Watch subscription entitlement assumed

    def reader_factory():
        return ProductionMarketReader(
            *_secret(), audit=store.audit_http, budget=budget,
            consumer="SCANNER", priority="DISCOVERY", owner_managed_refresh=True)

    def sdk_instrument(ticker, family, settlement):
        from ppi_client.models.instrument import Instrument
        return Instrument(ticker, family, settlement)

    return PPISharedSessionOwner(
        reader_factory=reader_factory, instrument_factory=sdk_instrument,
        stream_authority=_NoUnverifiedStreamPermit(),
        external_capacity_authority=capacity,
        socket_path=gateway_socket_path(store.path), **catalog,
        settlement_signer=SettlementReceiptSigner(release) if release else None,
    )


@contextmanager
def ppi_gateway_lifetime_lease(socket_path):
    """Cross-PID lock held from BEFORE login to AFTER reader close.

    Unlike the 900s login interlock or per-GET flock, this prevents a second
    gateway worker in this PAPER workspace even after the cooldown expires.
    It does not exclude unrelated legacy jobs or another workspace/account.
    """
    private = Path(socket_path).parent
    if private.is_symlink():
        raise RuntimeError("PPI_GATEWAY_SOCKET_PATH_ALIAS")
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    if private.is_symlink() or (private.stat().st_mode & 0o077):
        raise RuntimeError("PPI_GATEWAY_SOCKET_DIRECTORY_NOT_PRIVATE")
    lock_path = private / "owner.lock"
    if lock_path.is_symlink():
        raise RuntimeError("PPI_GATEWAY_LIFETIME_LEASE_ALIAS")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_mode & 0o077):
            raise RuntimeError("PPI_GATEWAY_LIFETIME_LEASE_UNTRUSTED")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("PPI_GATEWAY_LIFETIME_LEASE_HELD") from None
        try:
            _reclaim_stale_gateway_socket_under_lease(Path(socket_path))
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _reclaim_stale_gateway_socket_under_lease(path):
    """Inode-checked AF_UNIX crash cleanup, only after lifetime flock.

    No regular file, symlink, foreign UID, open listener, changed inode or
    ambiguous connection error is ever removed. A SIGKILL leaves the socket
    node; this is the sole supported automatic stale-node recovery.
    """
    try:
        original = os.lstat(path)
    except FileNotFoundError:
        return False
    if (not stat.S_ISSOCK(original.st_mode)
            or original.st_uid != os.geteuid()
            or original.st_nlink != 1
            or original.st_mode & 0o077):
        raise RuntimeError("PPI_GATEWAY_STALE_SOCKET_UNTRUSTED")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(.25)
            probe.connect(str(path))
    except OSError as exc:
        if exc.errno != errno.ECONNREFUSED:
            raise RuntimeError("PPI_GATEWAY_SOCKET_LIVENESS_UNVERIFIED") from None
    else:
        raise RuntimeError("PPI_GATEWAY_SOCKET_LIVE_OWNER")
    try:
        observed = os.lstat(path)
    except FileNotFoundError:
        raise RuntimeError("PPI_GATEWAY_STALE_SOCKET_RACED") from None
    if (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_uid,
            observed.st_nlink, observed.st_ctime_ns) != (
            original.st_dev, original.st_ino, original.st_mode,
            original.st_uid, original.st_nlink, original.st_ctime_ns):
        raise RuntimeError("PPI_GATEWAY_STALE_SOCKET_RACED")
    path.unlink()
    return True


def run_ppi_gateway_worker(store, stop):
    """One dedicated SDK process; NO reader login in any other PAPER child."""
    from rc6_ppi_gateway_reader_adapter import gateway_socket_path
    socket_path = gateway_socket_path(store.path)
    with ppi_gateway_lifetime_lease(socket_path):
        _run_ppi_gateway_owned_loop(store, stop)


def _make_iol_gateway_owner(store, socket_path):
    from datetime import datetime, timezone
    from iol_rest_readonly_rc6 import IOLRestReadOnly
    from iol_shadow_collector_rc6 import CollectionPolicy, RateGovernor
    from rc6_iol_market_gateway import IOLMarketGateway, iol_gateway_identities
    from rc6_iol_trade_stream import IOLTradeStream
    from rc6_performance.costs import local_etf_reference_keys

    with store.connect() as connection:
        rows = connection.execute("""SELECT ticker,instrument_type,market,currency,settlement
            FROM financial_instrument_catalog WHERE status IN ('AVAILABLE','STALE')""").fetchall()
    scan_keys = iol_gateway_identities(tuple(row) for row in rows)
    scan_keys |= frozenset(local_etf_reference_keys(datetime.now(timezone.utc)))
    opened, _ = store.exit_positions()
    exit_keys = iol_gateway_identities(
        (position['symbol'], position['asset_class'], position['market'],
         position['currency'], position['settlement']) for position in opened)
    # Share the bounded collector policy across scanner and EXIT via this owner.
    config = os.getenv("POROTA_IOL_REST_CONFIG")
    credentials = {"config": config} if config else {}
    rest = IOLRestReadOnly(socket_path.parent / "rest-session",
        governor=RateGovernor(CollectionPolicy(), max_wait_seconds=1.0), owner_managed_auth=True,
        **credentials)
    return IOLMarketGateway(rest, allowed={"scanner": scan_keys, "exit_reader": scan_keys | exit_keys},
                            socket_path=socket_path, supervisor_pid=os.getppid(),
                            trade_stream=IOLTradeStream(rest.stream_bearer))


def run_iol_gateway_worker(store, stop):
    from rc6_iol_market_gateway import iol_gateway_socket_path

    socket_path = iol_gateway_socket_path(store.path)
    with ppi_gateway_lifetime_lease(socket_path):
        owner = _make_iol_gateway_owner(store, socket_path)
        owner.start()
        trade_stop = threading.Event()
        trade_reader = threading.Thread(target=owner.trade_stream.run, args=(trade_stop,),
                                        name="iol-owner-trade-stream", daemon=True)
        trade_reader.start()
        server = threading.Thread(target=owner.serve_forever, daemon=True,
                                  name="iol-owner-unix-server")
        server.start()
        try:
            while not stop.wait(1):
                if not server.is_alive():
                    raise RuntimeError("IOL_GATEWAY_SERVER_THREAD_EXITED")
        finally:
            trade_stop.set()
            if server.is_alive():
                owner.shutdown()
                server.join(timeout=5)
                if server.is_alive():
                    raise RuntimeError("IOL_GATEWAY_SOCKET_DRAIN_UNVERIFIED")
            owner.close()
            trade_reader.join(timeout=5)


def _start_ppi_gateway_server(owner):
    owner.start_gateway()
    server_thread = threading.Thread(
        target=owner.serve_gateway_forever,
        name="ppi-single-owner-unix-server", daemon=True)
    server_thread.start()
    return server_thread


def _close_ppi_gateway_server(owner, server_thread):
    if server_thread is not None and server_thread.is_alive():
        owner.shutdown_gateway()
        server_thread.join(timeout=5)
        if server_thread.is_alive():
            # Never restore the guard while a listener can dispatch reads.
            raise RuntimeError("PPI_GATEWAY_SOCKET_DRAIN_UNVERIFIED")
    owner.close_gateway()  # refuses an in-flight wire executor


def _run_ppi_gateway_owned_loop(store, stop):
    from rc6_scanner_login_interlock import ScannerLoginInterlock
    from rc6_ppi_shared_session_owner import (
        AUTH_COOLDOWN_SECONDS, complete_ppi_catalog_publication,
    )
    interlock = ScannerLoginInterlock(store.path, AUTH_COOLDOWN_SECONDS)
    next_attempt = 0.0
    while not stop.is_set():
        if time.monotonic() < next_attempt:
            stop.wait(5)
            continue
        owner = None
        server_thread = None
        try:
            # Validate source and PAPER identity BEFORE reserving a login.
            observed_publication = complete_ppi_catalog_publication(store.path)
            owner = _make_ppi_gateway_owner(store)
            decision = interlock.reserve()
            if decision != "ALLOWED":
                raise RuntimeError("PPI_GATEWAY_LOGIN_INTERLOCK_" + decision)
            owner.open_once()  # the only login_api in these four children
            server_thread = _start_ppi_gateway_server(owner)
            status = owner.status()
            _best_effort_runtime_event(store, "PPI_GATEWAY_OWNER_OPEN",
                "pid=" + str(status["owner_pid"]) +
                ";generation=" + status["session_generation"] +
                ";login_count=1;provider_quota=UNKNOWN;runtime_global=NO_VERIFICADO")
            while not stop.wait(5):
                if not server_thread.is_alive():
                    raise RuntimeError("PPI_GATEWAY_SERVER_THREAD_EXITED")
                status = owner.status()
                if status["session_recovery_pending"]:
                    refresh = owner.refresh_invalid_session()
                    if refresh:
                        _best_effort_runtime_event(store, "PPI_GATEWAY_AUTH_REFRESH", refresh)
                    if owner.session_recovery_due():
                        _best_effort_runtime_event(store, "PPI_GATEWAY_AUTH_RECOVERY", "PPI_HTTP_401")
                        break
                    continue
                try:
                    publication = complete_ppi_catalog_publication(store.path)
                    if publication is None or publication == observed_publication:
                        continue
                    catalog = _ppi_gateway_catalog(
                        store, expected_publication=publication)
                except ValueError as exc:
                    if str(exc) in {"PPI_PRIMARY_CATALOG_UNAVAILABLE",
                                    "PPI_PRIMARY_CATALOG_PUBLICATION_CHANGED"}:
                        # Keep the existing complete snapshot on a transient
                        # read or a newer catalog write. Check the marker again.
                        continue
                    _best_effort_runtime_event(
                        store, "PPI_GATEWAY_CATALOG_HOLD", type(exc).__name__)
                    raise
                digest = catalog["catalog_source_digest"]
                if digest != status["catalog_source_digest"]:
                    if not owner.begin_gateway_catalog_refresh():
                        continue
                    _close_ppi_gateway_server(owner, server_thread)
                    server_thread = None
                    if stop.is_set():
                        break
                    owner.bind_refreshed_gateway_catalog(**catalog)
                    server_thread = _start_ppi_gateway_server(owner)
                    _best_effort_runtime_event(
                        store, "PPI_GATEWAY_CATALOG_REFRESHED", digest)
                observed_publication = publication
        except Exception as exc:
            LOG.warning("PPI_GATEWAY_HOLD:%s", type(exc).__name__)
            _best_effort_runtime_event(store, "PPI_GATEWAY_HOLD", type(exc).__name__)
            next_attempt = time.monotonic() + AUTH_COOLDOWN_SECONDS
        finally:
            if owner is not None:
                _close_ppi_gateway_server(owner, server_thread)
                if owner.status()["state"] in {"OPEN", "CATALOG_REFRESH"}:
                    owner.close()


def _sqlite_contention(exc):
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = str(getattr(exc, "sqlite_errorname", "") or "").upper()
    msg = str(exc).lower()
    return code in {"SQLITE_BUSY", "SQLITE_LOCKED"} or "database is locked" in msg or "database table is locked" in msg


def _reader_status_write(store, state, detail=""):
    """Bounded health write; transient SQLite contention cannot kill exit-reader."""
    try:
        # sqlite3's transaction context commits or rolls back, but does not close.
        # Close after that context exits so WAITING_MARKET cannot accumulate FDs.
        with closing(sqlite3.connect(store.path, timeout=0.35)) as connection:
            with connection:
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


def _best_effort_supervisor_heartbeat(supervisor, at, state, detail):
    """A contended health write must not terminate the independent exit clock.

    Admission reads the durable heartbeat and fails closed while it is stale.
    The next clock iteration retries supervision and its heartbeat.
    """
    try:
        supervisor.heartbeat(at, state, detail)
        return True
    except sqlite3.OperationalError as exc:
        if not _sqlite_contention(exc):
            raise
        LOG.warning("EXIT_SUPERVISOR_HEARTBEAT_SQLITE_CONTENTION:%s",
                    getattr(exc, "sqlite_errorname", "SQLITE_LOCKED"))
        return False



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
        self._gateway_registered = {}
        self._gateway_generation = None
        self._gateway_registration_due = started
        gateway_roles = {"iol_gateway", "ppi_gateway"}.intersection(commands)
        if len(gateway_roles) > 1:
            raise ValueError("RC6_MULTIPLE_MARKET_GATEWAYS")
        self.gateway_role = next(iter(gateway_roles), None)
        from rc6_fixed_income_sale_authority import deployed_exit_signing_model
        from rc6_fixed_income_sale_witness import ExitReaderKeyChannel

        model = deployed_exit_signing_model() if "exit_reader" in commands else None
        self.exit_sale = ExitReaderKeyChannel(model) if model else None
        self.health = {name: {"state": "STARTUP_WAIT", "pid": None,
            "restarts": 0, "spawn_failures": 0, "started_at": None,
            "last_exit_at": None, "last_error": None} for name in commands}
        first = self.gateway_role or "scanner"
        # Gateway owns the session first; scanner must still precede the
        # read-only shadow workload to avoid simultaneous startup SQLite IO.
        self.next_start = {
            name: (started if name == first else started + startup_grace_seconds * (
                2 if name == "dynamic_shadow" and self.gateway_role else 1))
            for name in commands
        }

    def poll(self):
        now = self.clock()
        for name, command in self.commands.items():
            process = self.processes.get(name)
            if process is not None and process.poll() is not None:
                if name == "exit_reader" and self.exit_sale:
                    self.exit_sale.clear()
                self.processes.pop(name)
                self.next_start[name] = now + self.cooldown
                self.health[name].update(state="CRASH_BACKOFF", pid=None,
                    restarts=self.health[name]["restarts"] + 1,
                    last_exit_at=now_iso(), last_error="CHILD_EXITED")
            if name not in self.processes and now >= self.next_start.get(name, 0):
                try:
                    self.processes[name] = (self.exit_sale.launch(self.spawn, command, cwd=ROOT)
                        if name == "exit_reader" and self.exit_sale else self.spawn(command, cwd=ROOT))
                    self.health[name].update(state="RUNNING",
                        pid=getattr(self.processes[name], "pid", None),
                        started_at=now_iso(), last_error=None)
                    if name in {self.gateway_role, "scanner", "exit_reader", "intraday_scalping"}:
                        # A fresh PID/role must be registered immediately;
                        # an earlier owner-status poll may have deferred its
                        # next census for 15s before this child even existed.
                        self._gateway_registration_due = now
                except OSError:
                    self.next_start[name] = now + self.cooldown
                    self.health[name].update(state="SPAWN_FAILED", pid=None,
                        spawn_failures=self.health[name]["spawn_failures"] + 1,
                        last_error="CHILD_SPAWN_FAILED")
        if self.gateway_role and now >= self._gateway_registration_due:
            self._register_gateway_clients(now)
        if self.exit_sale:
            self.exit_sale.poll(self.processes.get("exit_reader"))

    def _register_gateway_clients(self, now):
        """Only runtime parent registers real child PID + Linux start tick.

        The gateway independently compares our AF_UNIX SO_PEERCRED PID to its
        captured parent PID. Failed registration leaves children denied; no
        consumer gains a provider credential or retries a local login.
        """
        roles = {"scanner": "scanner", "exit_reader": "exit_reader"}
        try:
            from rc6_ppi_shared_feed import (ReadOnlyUnixFeedClient,
                                             ReadOnlyUnixFeedServer)
            gateway = self.processes.get(self.gateway_role)
            if gateway is None or gateway.poll() is not None:
                raise RuntimeError("RC6_GATEWAY_PROCESS_NOT_RUNNING")
            if self.gateway_role == "iol_gateway":
                from rc6_iol_market_gateway import IOLGatewayClient, iol_gateway_socket_path

                client = IOLGatewayClient(socket_path=iol_gateway_socket_path(database_path()),
                                          timeout_seconds=.4)
            else:
                from rc6_ppi_gateway_reader_adapter import gateway_socket_path

                roles["intraday_scalping"] = "scalping"
                client = ReadOnlyUnixFeedClient(socket_path=gateway_socket_path(database_path()),
                                                timeout_seconds=.4)
            status = client.owner_status()
            if self.gateway_role == "iol_gateway" and status.get("owner_pid") != gateway.pid:
                raise RuntimeError("IOL_GATEWAY_SUPERVISED_PID_MISMATCH")
            if self.gateway_role == "ppi_gateway" and "settlement_signer_public_key" in status:
                from rc6_ppi_native_request_receipt import pin_gateway_signer

                try:
                    pin_gateway_signer(database_path(), status, expected_pid=gateway.pid)
                except (OSError, ValueError) as exc:
                    # A broken settlement pin blocks only new settlement
                    # authority. Existing PPI/EXIT roles still register.
                    LOG.warning("PPI_SETTLEMENT_SIGNER_HOLD:%s", type(exc).__name__)
            generation = status["session_generation"]
            if generation != self._gateway_generation:
                self._gateway_registered.clear()
                self._gateway_generation = generation
            waiting = False
            for child_name, role in roles.items():
                child = self.processes.get(child_name)
                if child is None or child.poll() is not None:
                    continue
                pid = getattr(child, "pid", None)
                birth_tick = ReadOnlyUnixFeedServer.process_starttime(pid)
                if birth_tick is None:
                    waiting = True
                    continue
                identity = (pid, birth_tick, generation)
                if self._gateway_registered.get(role) == identity:
                    continue
                client.register_client(role, pid=pid, proc_starttime=birth_tick)
                self._gateway_registered[role] = identity
            self._gateway_registration_due = now + (2 if waiting else 15)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            self._gateway_registration_due = now + 2

    def snapshot(self):
        return {name: dict(value) for name, value in self.health.items()}

    def close(self):
        if self.exit_sale:
            self.exit_sale.clear()
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        for name, process in self.processes.items():
            try:
                # A sealed audit must drain within30s before orderly stop.
                # Signals were sent to ALL children above; financial readers
                # retain their existing5s rule and are not held by SHADOW.
                process.wait(timeout=35 if name == 'dynamic_shadow' else 5)
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
    iol = getattr(children, "gateway_role", None) == "iol_gateway"
    budget_observation = {"status": "NOT_APPLICABLE" if iol else "UNVERIFIED"}
    if not iol:
        try:
            from rc6_ppi_global_budget import runtime_budget_snapshot
            budget_observation = runtime_budget_snapshot(store.path, as_of=recorded_at)
        except (ImportError, OSError, ValueError, sqlite3.Error):
            pass
    payload = {"schema": "rc6.runtime-child-health.v1", "recorded_at": recorded_at,
        "ppi_budget": budget_observation, "market_provider": "IOL_REST" if iol else "PPI",
        "source_sha": sha, "candidate_tree_sha": tree,
        "children": children.snapshot(), "mode": "PRODUCTION_PAPER",
        "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    if getattr(children, "exit_sale", None):
        payload["fixed_income_sale"] = children.exit_sale.status()
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
    # Operator-approved Performance ingestion disable: this optional exit
    # telemetry would otherwise keep writing to the capped evidence database
    # even after the dedicated capture child is removed. EXIT stays active.
    supervisor = PositionExitSupervisor(broker, clock_fn=clock_fn,
        session_policy=broker.session_policy,
        max_hold_minutes=int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360")), telemetry=None)
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
                _best_effort_supervisor_heartbeat(
                    supervisor, clock_fn(), "DEGRADED", type(exc).__name__)
            stop.wait(interval)
    finally:
        try:
            _best_effort_supervisor_heartbeat(
                supervisor, clock_fn(), "STOPPED", "Runtime detenido; nuevas entradas bloqueadas")
        finally:
            children.close()


def _exit_portfolio(store):
    positions, invalid = store.exit_positions()
    futures = getattr(store, "active_future_positions", lambda: [])()
    return [*positions, *({**future, "asset_class": "FUTUROS", "paper_id": future["lifecycle_id"]}
                         for future in futures)], invalid


def collect_exit_books(reader, store, policy, at, *, should_stop=lambda: False,
                       pause=lambda: None, beat=lambda: None, sale_witness=None,
                       portfolio=None, verified=None):
    """Un book por abierta; jamás depende de último negocio o de Gemini."""
    from bf_production_paper_observer import normalize_quote
    from dataclasses import replace
    from bs_instrument_contracts import contract_from_metadata
    from rc6_fixed_income_sale_authority import SALE_FAMILIES, native_book_fields, native_market_sale_enabled
    from rc6_fixed_income_iol_evidence import iol_contract_reason
    from fe_fixed_income_nominal_contract_rc6 import FAMILIES as FIXED_INCOME_FAMILIES
    from bd_ppi_readonly_guard import retry_read, session_invalid
    from iol_rest_readonly_rc6 import IOLRestError
    from rc6_dynamic_universe.routing import EQUITY_FAMILIES
    from rc6_iol_market_gateway import IOLGatewayMarketReader, iol_gateway_reader_needs_reattach
    from rc6_ppi_gateway_reader_adapter import gateway_reader_needs_reattach
    import bu_instrument_catalog as catalog
    from rc6_paper_family_lifecycle import future_position_contract
    positions, invalid = _exit_portfolio(store) if portfolio is None else portfolio
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
            if isinstance(reader, IOLGatewayMarketReader):
                if p["asset_class"] not in EQUITY_FAMILIES | FIXED_INCOME_FAMILIES | {"OPCIONES"}:
                    raise IOLRestError("IOL_SPECIALIZED_EXIT_EVIDENCE_REQUIRED")
                terms = json.loads(p.get("features_json") or "{}").get("financial_contract")
                held_contract = contract_from_metadata(p["symbol"], p["asset_class"], terms) if terms else None
                if p["asset_class"] == "OPCIONES" and (
                        held_contract is None or held_contract.settlement != "INMEDIATA"):
                    raise IOLRestError("IOL_OPTION_HELD_CONTRACT_UNVERIFIED")
                if p["asset_class"] in FIXED_INCOME_FAMILIES:
                    reason = iol_contract_reason(identity, held_contract)
                    if reason:
                        raise ValueError(reason)
                decision = None
                if p["asset_class"] in SALE_FAMILIES and sale_witness:
                    try:
                        decision = sale_witness.acknowledge(p["paper_id"])
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        LOG.warning("FI_EXIT_ACK_UNAVAILABLE:%s", type(exc).__name__)
                q = reader.observe(identity, contract=held_contract, priority="EXIT_CRITICAL", exit_only=True).quote
                if decision:
                    try:
                        sale_witness.witness_first_later_book(decision)
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        LOG.warning("FI_EXIT_BOOK2_UNAVAILABLE:%s", type(exc).__name__)
            else:
                native_book_reader = getattr(reader, "book_with_native_receipt", None)
                native_book = None
                decision = None
                native_sale = (p["asset_class"] in SALE_FAMILIES and callable(native_book_reader)
                               and native_market_sale_enabled(identity))
                if native_sale and sale_witness:
                    try:
                        decision = sale_witness.acknowledge(p["paper_id"])
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        LOG.warning("FI_EXIT_ACK_UNAVAILABLE:%s", type(exc).__name__)
                with (scope(priority="EXIT_CRITICAL", identity=identity) if callable(scope) else nullcontext()):
                    if native_sale:
                        native_book = retry_read(lambda: native_book_reader(
                            p["symbol"], p["asset_class"], p["settlement"]), retries=1)
                        book = native_book["raw_payload"]
                    else:
                        book = retry_read(lambda: reader.book(
                            p["symbol"],p["asset_class"],p["settlement"]), retries=1)
                if decision:
                    try:
                        sale_witness.witness_first_later_book(decision)
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        LOG.warning("FI_EXIT_BOOK2_UNAVAILABLE:%s", type(exc).__name__)
                stage = "CATALOG_LOOKUP"
                metadata = catalog.lookup(store,p["symbol"],p["asset_class"],p["settlement"],
                                          market=p["market"], currency=p["currency"])
                stage = "NORMALIZE_QUOTE"
                q = normalize_quote(p["symbol"],p["asset_class"],p["settlement"],{},book,
                                    metadata=metadata, identity=identity)
                if native_book is not None:
                    try:
                        native_book_fields(native_book, identity)
                        held_contract = contract_from_metadata(p["symbol"], p["asset_class"],
                            json.loads(p["features_json"])["financial_contract"])
                        if held_contract.key == identity:
                            q = replace(q, contract=held_contract, ppi_native_rest_book=native_book)
                    except (ValueError, KeyError, TypeError, ArithmeticError):
                        # Preserve the ordinary exit mark, without binding an ambiguous witness.
                        pass
                if is_future:
                    # Request identity is the exact position; catalog changes cannot
                    # invent new exit terms or grant fresh entry authority.
                    q = replace(q, contract=original_contract, currency=p["currency"],
                                market=p["market"], opening_block_reason="FUTURES_EXIT_ONLY_DURABLE_CONTRACT")
            stage = "PERSIST_QUOTE"
            if verified is not None:
                # The IOL round only credits a book after exact identity,
                # venue time and the durable snapshot have all been checked.
                error = q.time_error(q.observed_at)
                if error or q.monetary_identity() != (p["currency"], p["market"]):
                    raise ValueError(error or "IOL_EXIT_BOOK_IDENTITY_MISMATCH")
            store.add_quote(q)
            stage = "VALIDATE_QUOTE"
            if q.time_error(q.observed_at):
                failures += 1
            q.monetary_identity()
            if verified is not None:
                verified.add(p["paper_id"])
        except Exception as exc:
            if session_invalid(exc) or (stage == "BROKER_BOOK" and (
                    gateway_reader_needs_reattach(exc) or isinstance(reader, IOLGatewayMarketReader)
                    and iol_gateway_reader_needs_reattach(exc))):
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


def _collect_iol_exit_round(reader, store, policy, at, stop, *, beat, deadline_seconds, sale_witness=None):
    """Bind an observed IOL traversal to a stable portfolio and Gateway owner."""
    portfolio = _exit_portfolio(store)
    positions, invalid = portfolio
    start = time.monotonic()
    before = reader.metrics
    verified = set()
    failures = collect_exit_books(reader, store, policy, at, should_stop=stop.is_set,
                                  beat=beat, portfolio=portfolio, verified=verified,
                                  sale_witness=sale_witness)
    after = reader.metrics
    remaining, now_invalid = _exit_portfolio(store)
    elapsed = time.monotonic() - start
    scoped = {p["paper_id"]: p for p in positions}
    current = {p["paper_id"]: p for p in remaining}
    generation = after.get("session_generation")
    registration = (after.get("registered_clients") or {}).get("exit_reader") or {}
    ready = (not stop.is_set() and not failures and not invalid and not now_invalid
             and scoped == current and set(scoped) == verified and elapsed <= deadline_seconds
             and isinstance(generation, str) and re.fullmatch(r"[0-9a-f]{64}", generation)
             and before.get("session_generation") == generation and after.get("provider") == "IOL_REST"
             and registration.get("pid") == os.getpid()
             and after.get("authenticated") is True and after.get("wire_budget_attached") is True)
    outcome = ("COMPLETE" if scoped else "IDLE_NO_OPEN_POSITIONS") if ready else "NO_VERIFICADO"
    detail = (f"{failures} errores de lectura de abiertas; round_seconds={elapsed:.6f}; "
              f"critical_cadence_seconds={deadline_seconds}; provider=IOL_REST; "
              f"open_positions={len(positions)}; verified_books={len(verified)}; budget_round={outcome}")
    return ("READY" if ready else "DEGRADED"), detail, elapsed


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
    from bd_ppi_readonly_guard import session_invalid
    from bf_production_paper_observer import _support_schema, _market_phase
    from rc6_iol_market_gateway import (
        connect_iol_gateway_reader, iol_gateway_reader_needs_reattach, iol_primary_enabled,
    )
    from rc6_ppi_gateway_reader_adapter import (
        connect_paper_gateway_reader, gateway_reader_needs_reattach)
    from rc6_dynamic_universe.promotion import DEFAULT_POLICY
    from rc6_fixed_income_sale_witness import exit_reader_witness_from_environment
    if os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() != "1":
        _support_schema(store)
    policy = PaperSessionPolicy()
    sale_witness = exit_reader_witness_from_environment(store)
    iol = iol_primary_enabled()
    connect_gateway = connect_iol_gateway_reader if iol else connect_paper_gateway_reader
    reader, next_login = None, 0.0
    pending_scope_reset = False
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
                    status("GATEWAY_ATTACH_RETRY")
                    stop.wait(5)
                    continue
                try:
                    reader = connect_gateway(store.path, consumer="exit_reader")
                    if pending_scope_reset:
                        reader.discard_exit_round_scope()
                        pending_scope_reset = False
                    store.event("IOL_GATEWAY_ATTACHED" if iol else "PPI_GATEWAY_ATTACHED",
                                "consumer=exit_reader;no_sdk_login")
                    status("VERIFYING", "Lector conectado; verificando cobertura de salida")
                except Exception as exc:
                    if reader:
                        reader.close()
                    reader = None
                    next_login = time.monotonic() + 15  # local AF_UNIX only, never PPI login
                    store.event("EXIT_READER_GATEWAY_ATTACH_ERROR", type(exc).__name__)
                    status("ERROR",type(exc).__name__)
                    continue
            try:
                if iol:
                    cadence = DEFAULT_POLICY["budget"]["critical_book_seconds"]
                    state, detail, elapsed = _collect_iol_exit_round(
                        reader, store, policy, now_iso(), stop,
                        beat=lambda: status("VERIFYING", "Recorriendo posiciones abiertas"),
                        deadline_seconds=cadence, sale_witness=sale_witness)
                    status(state, detail)
                    stop.wait(max(0.0, cadence-elapsed))
                    continue
                round_started=time.monotonic()
                cadence=exit_reader_cadence(store,now_iso())
                budget_round="NOT_OBSERVED"
                try:
                    failures = collect_exit_books(
                        reader, store, policy, now_iso(), should_stop=stop.is_set,
                        sale_witness=sale_witness,
                        pause=(lambda: None) if cadence is not None else (lambda: stop.wait(1)),
                        beat=lambda: status("VERIFYING", "Recorriendo posiciones abiertas"))
                    elapsed=time.monotonic()-round_started
                    # A completed producer traversal is the only place that
                    # may report factual EXIT coverage to the shared budget.
                    # The budget itself verifies current PAPER ledger identity,
                    # native leases, failures and deadline before COMPLETE.
                    # A stop can make collect_exit_books return zero after a
                    # partial traversal; it must never certify that round.
                    if not stop.is_set():
                        try:
                            observed=reader.observe_exit_round(
                                elapsed_seconds=elapsed,
                                deadline_seconds=(cadence if cadence is not None else
                                    DEFAULT_POLICY["budget"]["critical_book_seconds"]),
                                failures=failures)
                            # Only the shared budget may certify a completed
                            # EXIT observation. Never show READY when it
                            # explicitly returned DEGRADED/unmeasured.
                            if isinstance(observed,dict) and observed.get("status") in {
                                    "COMPLETE","DEGRADED","BASELINE_NOT_MEASURED"}:
                                budget_round=observed["status"]
                            else:
                                budget_round="NO_VERIFICADO"
                        except Exception as exc:
                            LOG.warning("EXIT_READER_ROUND_OBSERVATION_UNAVAILABLE:%s",
                                        type(exc).__name__)
                finally:
                    # A partial/failed traversal must not carry a frozen
                    # admission scope into the next EXIT round. This local
                    # scope reset performs no PPI GET or sidecar mutation.
                    # If reset itself fails, stop this reader; reusing its
                    # unproven scope could authorize a false COMPLETE later.
                    try:
                        reader.discard_exit_round_scope()
                    except Exception as exc:
                        if gateway_reader_needs_reattach(exc):
                            # Verify reset on the next pin before any read.
                            pending_scope_reset = True
                            raise
                        # A reset failure cannot be recovered as Book contention.
                        raise RuntimeError("EXIT_READER_SCOPE_RESET_UNVERIFIED") from exc
            except sqlite3.OperationalError as exc:
                if not _sqlite_contention(exc):
                    raise
                status("DEGRADED", "SQLite contention; reintento acotado, sin fill ni orden")
                LOG.warning("EXIT_READER_SQLITE_CONTENTION:%s",
                            getattr(exc, "sqlite_errorname", "SQLITE_LOCKED"))
                stop.wait(1)
                continue
            except Exception as exc:
                if not (session_invalid(exc) or gateway_reader_needs_reattach(exc)
                        or iol and iol_gateway_reader_needs_reattach(exc)):
                    raise
                _best_effort_runtime_event(store, "EXIT_READER_GATEWAY_REATTACH", type(exc).__name__)
                status("ERROR", "Gateway ausente, rotado o auth inválida; reconexión local")
                reader.close()
                reader = None
                next_login = time.monotonic() + 60
                stop.wait(5)
                continue
            missed=cadence is not None and elapsed>cadence
            detail=f"{failures} errores de lectura de abiertas; round_seconds={elapsed:.6f}; "
            detail+=(f"critical_cadence_seconds={cadence}; deadline_missed={missed}"
                if cadence is not None else "baseline_cadence=NO_VERIFICADO")
            detail+=f"; budget_round={budget_round}"
            status("DEGRADED" if failures or missed or budget_round!="COMPLETE" else "READY",detail)
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
    from rc6_iol_market_gateway import iol_primary_enabled

    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ['--ppi-gateway-worker'], ['--iol-gateway-worker'], ['--exit-reader'], ['--notification-worker'], ['--candle-worker'], ['--dynamic-shadow-worker'],
                    ['--intraday-scalping-worker'], ['--caucion-cash-sweep-worker']):
        raise ValueError("Argumentos desconocidos del runtime paper")
    iol = iol_primary_enabled()
    if (iol and argv == ['--ppi-gateway-worker'] or
            not iol and argv == ['--iol-gateway-worker']):
        raise ValueError("RC6_WORKER_MARKET_PROVIDER_MISMATCH")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    if argv == ['--dynamic-shadow-worker']:
        # Local-only reader: no runtime_store/PaperBroker/schema/provider in
        # this child. The existing supervisor owns periodic execution/restart.
        from rc6_shadow_runtime.worker import run_worker
        run_worker(database_path(), stop, clock_fn=now_iso)
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
    if argv == ["--ppi-gateway-worker"]:
        run_ppi_gateway_worker(store, stop)
        return 0
    if argv == ["--iol-gateway-worker"]:
        run_iol_gateway_worker(store, stop)
        return 0
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
        gateway = "iol_gateway" if iol else "ppi_gateway"
        gateway_flag = "--iol-gateway-worker" if iol else "--ppi-gateway-worker"
        children = ChildProcesses({
            gateway: [sys.executable, str(Path(__file__).resolve()), gateway_flag],
            "scanner": [sys.executable, str(ROOT / "bf_production_paper_observer.py")],
            "dynamic_shadow": [sys.executable, str(Path(__file__).resolve()), "--dynamic-shadow-worker"],
            "exit_reader": [sys.executable, str(Path(__file__).resolve()), "--exit-reader"],
            "notifications": [sys.executable, str(Path(__file__).resolve()), "--notification-worker"],
            "candles": [sys.executable, str(Path(__file__).resolve()), "--candle-worker"],
            **({"intraday_scalping": [sys.executable, str(Path(__file__).resolve()), "--intraday-scalping-worker"]}
              if iol or os.getenv("PAPER_SCALPING_MODE", "OFF").upper() in {"ACTIVE_PAPER", "ACTIVE_OBSERVE"} else {}),
            **({"caucion_cash_sweep": [sys.executable, str(Path(__file__).resolve()), "--caucion-cash-sweep-worker"]}
              if iol or os.getenv("PAPER_CAUCION_SWEEP_MODE", "OFF").upper() == "ACTIVE_PAPER" else {}),
        })
        run_clock(store,children,stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

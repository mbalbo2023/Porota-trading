"""Offline fixtures written by the canonical PAPER and SHADOW producers.

The instruments and prices are synthetic. Schema, decision capture, family
accounting, experiments, committed generations and readers are production code.
No client, provider route or production workspace participates.
"""
from dataclasses import asdict, dataclass
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gc
import os
import hashlib
import json
from pathlib import Path
import selectors
import subprocess
import sys
from time import monotonic
from unittest.mock import patch

from be_paper_engine import PaperBroker, PaperStore, Quote
from bf_production_paper_observer import _support_schema
from bq_exit_policy import PaperSessionPolicy
from cf_intraday_scalping import init_schema
from cg_paper_workspace import artifact_root
from rc6_dynamic_universe.common import identity
from rc6_paper_family_lifecycle import FamilyPaperExecutor
from rc6_shadow_runtime.persistence import read_committed_generation, shadow_evidence_root
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_rc6_future_programming_complete import dlr

AS_OF = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)


class LiveHealthFixture:
    """A real isolated SHADOW child, separate from the immutable BIG fixture."""
    def __init__(self, temporary, source_root, index, *, executable, source_index):
        self.temporary, self.source_root, self.index = temporary, source_root, index
        self.executable, self.source_index = executable, source_index
        self.guards, self.children, self.health_observation = ExitStack(), None, None
        self.closed, self.ready, self.cleanup_forced = False, None, False

    def __enter__(self):
        from bv_paper_runtime import ChildProcesses, publish_child_health
        from tests.rc6_browser_ipc import MAX_FRAME, parse_frame, require
        self.publish = publish_child_health
        database = self.temporary / "data/paper_v17/observer_v17.db"
        root = artifact_root(database) / "dynamic-shadow"
        self.guards.enter_context(patch.dict(os.environ, {"DATA_DIR": str(self.temporary / "data"),
            "PAPER_V17_DB_PATH": str(database),
            "HIST_DB_PATH": str(self.temporary / "absent-history.db"),
            "POROTA_DYNAMIC_SHADOW_ROOT": str(root), "POROTA_SHADOW_RUNTIME_ROOT": str(root),
            "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT": str(artifact_root(database) / "dynamic-shadow-archive"),
            "POROTA_IOL_SHADOW_ROOT": str(self.temporary / "data/market"), "POROTA_IOL_SHADOW_CACHE_PATH": "",
            "POROTA_BUILD_SHA": self.source_index["source_sha"],
            "POROTA_CANDIDATE_TREE_SHA": self.source_index["candidate_tree_sha"],
            "POROTA_DYNAMIC_CAPACITY_MODE": "SHADOW",
            "POROTA_CAPACITY_POLICY_PATH": str(self.source_root / "ops/policy/rc6-dynamic-capacity-v1.json"),
            "POROTA_CAPACITY_REPORT_PATH": "", "POROTA_CAPACITY_RECOMMENDATION_PATH": "",
            "POROTA_CAPACITY_APPROVAL_PATH": "", "POROTA_CAPACITY_SHADOW_PATH": str(root / "CURRENT.json")}))
        try:
            # Only seed the canonical schema/catalog/quote writers here. The
            # first SHADOW cut is produced by the real child after its actual
            # start, so no fixed PREOPEN timestamp can precede or exceed now.
            self.store, self.database, self.root = _health_seed(self.temporary)
            require(self.database == database and self.root == root, "NATIVE_HEALTH_PRIVATE_DATASET_ROOT_MISMATCH")
            command = [str(self.executable), "-I", "-B", str(self.source_root / "tests/ci_rc6_browser_product.py"),
                "--expected-python", str(self.executable), "--expected-python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
                "--index", str(self.index), "--require-complete-index", "--health-worker-database", str(self.database)]
            def spawn(actual, **kwargs):
                return subprocess.Popen(actual, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), **kwargs)
            self.children = ChildProcesses({"dynamic_shadow": command}, startup_grace_seconds=0, spawn=spawn)
            self.children.poll()
            self.process = self.children.processes.get("dynamic_shadow")
            require(self.process is not None, "NATIVE_HEALTH_CHILD_SPAWN_FAILED")
            deadline, pending = monotonic() + 20, bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                while b"\n" not in pending:
                    remaining = deadline - monotonic()
                    require(len(pending) < MAX_FRAME and remaining > 0 and selector.select(remaining),
                            "NATIVE_HEALTH_CHILD_START_DEADLINE")
                    chunk = os.read(self.process.stdout.fileno(), min(65536, MAX_FRAME - len(pending)))
                    require(chunk, "NATIVE_HEALTH_CHILD_DIED_BEFORE_PUBLICATION")
                    pending.extend(chunk)
            ready = parse_frame(bytes(pending))
            require(ready["id"] == 0 and ready.get("ok") is True,
                    "NATIVE_HEALTH_CHILD_PUBLICATION_REJECTED", ready.get("error", {}))
            self.ready = ready["result"]
            require(self.ready["pid"] == self.process.pid and self.ready["source_proof_pass"] is True
                    and self.ready["environment"]["installed_count"] == 157, "NATIVE_HEALTH_CHILD_PROOF_MISMATCH")
            self.published = self.publish(self.store, self.children, recorded_at=datetime.now(timezone.utc).isoformat())
            self.started_at = self.published["children"]["dynamic_shadow"]["started_at"]
            require(datetime.fromisoformat(self.ready["as_of"]) >= datetime.fromisoformat(self.started_at),
                    "NATIVE_HEALTH_CUT_PRECEDES_REAL_CHILD_START")
            return self
        except BaseException:
            self.close()
            raise

    def consumer_inventory(self):
        from tests.ci_rc6_projection_large_reader import custody_inventory
        from tests.rc6_browser_ipc import protected_bytes
        result = custody_inventory(self.database, self.root)
        for path, member in result.items():
            info = Path(path).stat()
            member.update(mode=info.st_mode, links=info.st_nlink)
        path = artifact_root(self.database) / "runtime-health.json"
        info = path.stat()
        result[str(path)] = {"sha256": hashlib.sha256(protected_bytes(path)).hexdigest(), "bytes": info.st_size,
            "inode": info.st_ino, "atime_ns": info.st_atime_ns, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": info.st_mode, "links": info.st_nlink}
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        process = getattr(self, "process", None)
        try:
            if self.children is not None:
                try:
                    if process is not None:
                        process.stdin.close()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self.cleanup_forced = True
                finally:
                    # Even a failed orderly stop must reap the real child.
                    try:
                        self.children.close()
                    finally:
                        if process is not None and process.poll() is None:
                            self.cleanup_forced = True
                            process.kill()
                            process.wait(timeout=5)
                if self.ready is not None:
                    self.stopped = self.publish(self.store, self.children,
                        recorded_at=datetime.now(timezone.utc).isoformat())
        finally:
            try:
                if process is not None:
                    process.stdout.close()
            finally:
                self.guards.close()

    def receipt(self):
        process = getattr(self, "process", None)
        return {"scope": "OFFLINE_SEPARATE_SMALL_LIVE_SHADOW_HEALTH_NOT_BIG_RENDER",
            "clock_basis": "ACTUAL_UTC_PROCESS_START_PUBLICATION_AND_CONSUMPTION",
            "producer_ready": self.ready, "health_published_running": getattr(self, "published", None),
            "health_observation": self.health_observation, "health_published_stopped": getattr(self, "stopped", None),
            "producer_pid": process.pid if process is not None else None,
            "producer_exitcode": process.returncode if process is not None else None,
            "producer_reaped": process is not None and process.poll() is not None,
            "producer_cleanup_forced": self.cleanup_forced,
            "runtime_live_after_fixture_cleanup": False}

    def __exit__(self, *_args):
        self.close()


@dataclass
class NativeFixture:
    database: Path
    as_of: datetime
    root: Path
    cut: dict
    store: PaperStore
    worker: ShadowRuntime
    broker: PaperBroker
    clock: list


def native_fixture(tmp_path, *, as_of=AS_OF, count=25, with_future=True, with_spot=True, multifamily=False):
    fixture = _build_native_fixture(tmp_path, as_of=as_of, count=count,
                                    with_future=with_future, with_spot=with_spot, multifamily=multifamily)
    # SQLite's transaction context commits but does not close a connection.
    # Native writer UDFs may retain cycles until GC; finalize those writers
    # before measuring read custody, so their last-close WAL checkpoint cannot
    # run as a side effect of allocations during the subsequent render.
    gc.collect()
    return fixture


def _health_seed(tmp_path):
    """Private native writer inputs; no SHADOW publication or clock override."""
    from bs_instrument_contracts import InstrumentContract
    from bu_instrument_catalog import normalize_record, persist
    database = Path(tmp_path) / "data/paper_v17/observer_v17.db"
    store = PaperStore(str(database))
    _support_schema(store)
    init_schema(store)
    at = datetime.now(timezone.utc).isoformat()
    with closing(store.connect()) as connection, connection:
        for symbol in ("HEALTH000", "HEALTH001"):
            contract = InstrumentContract(symbol, "ACCIONES", "ARS", "BYMA", "A-24HS",
                Decimal(1), Decimal(1), "OFFLINE_SYNTHETIC_CONTRACT")
            raw = {"ticker": symbol, "type": contract.family, "currency": contract.currency,
                "market": contract.market, "settlement": contract.settlement,
                "financial_contract_v17": asdict(contract), "_discovery_source": "OFFLINE_SYNTHETIC_CONTRACT"}
            persist(connection, normalize_record(raw, contract.settlement, at, "OFFLINE_NATIVE_HEALTH"))
    for symbol in ("HEALTH000", "HEALTH001"):
        store.add_quote(Quote(symbol, "ACCIONES", "A-24HS", Decimal(100), Decimal(100), Decimal("100.1"),
            Decimal(1000), Decimal(1000), at, currency="ARS", market="BYMA",
            metadata_source="OFFLINE_SYNTHETIC_CONTRACT", book_at=at, trade_at=at, last_kind="TRADE"))
    # Finalize fixture writers before real spawn, as the normal fixture does;
    # no GC policy changes occur in the child or measured health consumer.
    gc.collect()
    return store, database, shadow_evidence_root(database)


def _multifamily_records(as_of, count):
    """Synthetic typed contracts pass the native catalog capability producer.

    This is a separate small fixture. Missing quotes, analytics and strategy
    validation remain missing; the contracts do not grant entry authority.
    """
    from bs_instrument_contracts import FAMILIES, InstrumentContract
    from bu_instrument_catalog import normalize_record
    from rc6_paper_family_lifecycle import PaperFundTerms
    assert type(count) is int and 0 < count <= 40
    at = as_of.replace(hour=13, minute=19, second=0, microsecond=0).isoformat()
    for family in sorted(FAMILIES):
        # The native standard DLR contract is deliberately limited to 2026.
        # Preserve the three remaining proven series rather than invent years.
        for index in range(3 if family == "FUTUROS" else count):
            symbol = f"T{index:03d}" if family == "ACCIONES" else f"{family[:4]}{index:03d}"
            raw = {"ticker": symbol, "type": family, "currency": "ARS", "market": "BYMA",
                   "settlement": "A-24HS", "description": "OFFLINE SYNTHETIC TYPED CONTRACT",
                   "_discovery_source": "OFFLINE_SYNTHETIC_CONTRACT"}
            if family == "FUTUROS":
                contract = dlr(("DLR/OCT26", "DLR/NOV26", "DLR/DIC26")[index])
                raw.update(ticker=contract.symbol, market=contract.market, currency=contract.currency,
                           settlement=contract.settlement, financial_contract_v17=asdict(contract))
            elif family == "FCI":
                terms = PaperFundTerms(symbol, family, "ARS", "BYMA", "INMEDIATA", "OFFLINE_SYNTHETIC_CONTRACT",
                    paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT")
                raw.update(settlement=terms.settlement, paper_family_contract_v1=asdict(terms))
            elif family == "CAUCIONES":
                raw.update(settlement="INMEDIATA", paper_caucion_contract_v1={"family": family, "market": "BYMA",
                    "metadata_source": "OFFLINE_SYNTHETIC_CONTRACT"})
            else:
                nominal = family in {"BONOS", "LETRAS", "OBLIGACIONES"}
                option = family == "OPCIONES"
                contract = InstrumentContract(symbol, family, "ARS", "BYMA", "INMEDIATA" if option else "A-24HS",
                    Decimal("100") if option else Decimal(".01") if nominal else Decimal(1),
                    Decimal(100) if nominal else Decimal(1), "OFFLINE_SYNTHETIC_CONTRACT",
                    minimum_quantity=Decimal(100) if nominal else Decimal(1),
                    expires_at=(as_of + timedelta(days=30)).isoformat() if option else None,
                    underlying="T000" if option else None, strike=Decimal(100) if option else None,
                    option_right="CALL" if option else None)
                raw.update(settlement=contract.settlement, financial_contract_v17=asdict(contract))
            record = normalize_record(raw, raw["settlement"], at, "OFFLINE_MULTIFAMILY_NATIVE")
            assert record["capability"].startswith("READY_PAPER_"), (family, record["capability"])
            yield record


def _build_native_fixture(tmp_path, *, as_of, count, with_future, with_spot, multifamily):
    path = Path(tmp_path) / "native-paper.db"
    store = PaperStore(str(path))
    _support_schema(store)
    init_schema(store)
    start = as_of - timedelta(minutes=10)
    preopen = as_of.replace(hour=13, minute=20, second=0, microsecond=0)
    with store.connect() as connection:
        if multifamily:
            from bu_instrument_catalog import persist
            for record in _multifamily_records(as_of, count):
                persist(connection, record)
        else:
            for index in range(count):
                asset = dict(ticker=f"T{index:03d}", instrument_type="ACCIONES", market="BYMA",
                             currency="ARS", settlement="A-24HS")
                connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*identity(asset), "OFFLINE SYNTHETIC", "fixture", (preopen-timedelta(minutes=1)).isoformat(), "test",
                     "AVAILABLE", "READY_PAPER_SPOT", "{}"))
    clock = [start]
    def native_clock():
        clock[0] += timedelta(microseconds=1)
        return clock[0].isoformat(timespec="microseconds")
    broker = PaperBroker(store, initial_cash="10000000", initial_cash_by_currency={"USD": "1000"},
        risk_pct=".005", participation=".1", max_position_pct="1", max_total_exposure_pct="1",
        clock_fn=native_clock, session_policy=PaperSessionPolicy(), require_supervisor=False,
        ai_mode="OFF", economics_mode="SHADOW", signal_min_samples=8)
    # Resolve exactly the root the launcher/worker/dashboard share. No caller
    # constructs a .shadow fallback or publishes manually fabricated bundles.
    worker = ShadowRuntime.from_environment(path, source_roots=[])
    worker.tick(preopen)
    prices = ("100", "101", "100.5", "102", "101.5", "103", "103.5", "104")
    for index, price in enumerate(prices):
        at = start + timedelta(minutes=index + 1)
        quote = Quote("T000", "ACCIONES", "A-24HS", Decimal(price), Decimal(price),
            Decimal(price) + Decimal(".1"), Decimal(1000), Decimal(1000), at.isoformat(),
            currency="ARS", market="BYMA", metadata_source="PPI_NATIVE_OFFLINE_FIXTURE",
            book_at=at.isoformat(), trade_at=at.isoformat(), last_kind="TRADE")
        store.add_quote(quote)
        clock[0] = at
        if index == len(prices) - 1 and with_spot:
            # Synthetic tickers have no sector authority. The native sector
            # policy is explicitly observational for this offline fixture.
            with patch.dict(os.environ, {"PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY"}):
                broker.on_quote(quote)
            assert store.open_positions(), "Native PAPER caller did not open the synthetic spot"
        worker.tick(at + timedelta(seconds=1))
    if with_future:
        executor = FamilyPaperExecutor(store)
        opened = as_of - timedelta(minutes=1)
        executor.open_future(dlr(), lifecycle_id="FUT-UI", event_id="FUT-UI:OPEN",
            entry_price="1500", quantity="1", entry_cost="100", occurred_at=opened.isoformat())
        mark = as_of - timedelta(seconds=1)
        executor.mark_future(dlr(), lifecycle_id="FUT-UI", event_id="FUT-UI:MARK",
            mark_price="1510", book_at=mark.isoformat(), occurred_at=mark.isoformat())
    store.state(process_state="RUNNING", session_state="MARKET_OPEN", ppi_auth="OK",
                heartbeat_at=as_of.isoformat(), last_market_data_at=as_of.isoformat(), real_orders_sent=0)
    clock[0] = as_of
    broker.mark_equity({"T000": quote}, as_of=as_of.isoformat())
    worker.tick(as_of)
    root = shadow_evidence_root(path)
    assert root == artifact_root(path) / "dynamic-shadow"
    cut = read_committed_generation(root)
    return NativeFixture(path, as_of, root, cut, store, worker, broker, clock)

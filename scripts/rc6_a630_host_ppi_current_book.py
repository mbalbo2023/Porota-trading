#!/usr/bin/env python3
"""A630: bounded PPI Current+Book sample, no deployment and no trading calls.

This source is streamed via stdin to (a) a PRIVATE read-only host mount namespace
for exact PPI catalog identity and (b) the existing pinned PAPER Docker container.
The latter may write ONLY through the already activated shared RuntimePPIBudget's
native acquire/start/finish. Missing identity, EXIT reserve, lease or credentials
means zero new GETs. The public artifact carries hashes, NOT provider payload.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import resource
import sqlite3
import stat
import sys
from urllib.parse import parse_qsl, quote, urlsplit
import uuid

SCHEMA = "rc6.a630.ppi.current-book.sample.v1"
PRODUCT_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
PPI_HOST = "clientapi.portfoliopersonal.com"
MAX_BODY = 128 * 1024
MAX_LEVELS = 200
MAX_AGE_MS = 120_000
MAX_EXIT_ROUND_AGE_MS = 300_000
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
PPI_TYPES = {"ACCIONES": "ACCIONES", "BONOS": "BONOS"}
# One reviewed target per intraday window. Catalog must prove the FULL 5-key.
SLOTS = {
    "1045": ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    "1120": ("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
    "1430": ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    "1630": ("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
}
REASONS = frozenset({
    "NOT_REQUESTED", "HOST_RO_MOUNT_REQUIRED", "DB_PAPER_IDENTITY_HOLD",
    "PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE", "PPI_PRIMARY_SOURCE_NOT_ATTESTED",
    "HOST_PREFLIGHT_OR_RUNTIME_HOLD", "SLOT_OR_DAY_HOLD",
    "PRODUCT_IMAGE_OR_MODE_HOLD", "PPI_KEYPAIR_UNAVAILABLE",
    "SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED", "ATOMIC_LEASE_DENIED",
    "PPI_BUDGET_SIDECAR_ABSENT", "PPI_BUDGET_SIDECAR_PATH_UNSAFE",
    "PPI_BUDGET_SNAPSHOT_NOT_OBSERVED", "PPI_RETENTION_NOT_READY",
    "PPI_EXIT_LOWER_SUSPENDED", "PPI_EXIT_ROUND_NOT_ATTESTED",
    "PPI_EXIT_ROUND_STALE_OR_CLOCK_INVALID",
    "PPI_BUDGET_RUNTIME_BACKEND_MISMATCH", "PPI_BUDGET_PROBE_UNAVAILABLE",
    "PROVIDER_GET_SCOPE_OR_COUNT_INVALID", "PPI_PROVIDER_OR_AUTH_FAILED",
    "PROVIDER_RESPONSE_SHAPE_INVALID", "PPI_NATIVE_CLOCK_INVALID",
    "PPI_NATIVE_CLOCK_STALE_OR_FUTURE", "BODY_BUDGET_OR_DIGEST_INVALID",
    "PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT", "RECEIPT_INVALID",
})


def target_for_slot(slot):
    if slot not in SLOTS:
        raise ValueError("SLOT_OR_DAY_HOLD")
    return SLOTS[slot]


def decode_target(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 256:
            raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) != 5 or not all(
                isinstance(item, str) for item in data):
            raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
        target = tuple(data)
        if target not in SLOTS.values():
            raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE")
        return target
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE") from None


def _private_readonly_mount(path):
    chosen = (-1, False)
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split(" - ", 1)[0].split()
            if len(fields) < 6:
                continue
            at = fields[4].replace("\\040", " ")
            if at != "/" and path != Path(at) and not str(path).startswith(at.rstrip("/") + "/"):
                continue
            if len(at) > chosen[0]:
                chosen = (len(at), "ro" in fields[5].split(","))
    except (OSError, UnicodeError):
        return False
    return chosen[0] >= len("/opt/porota-trading/data") and chosen[1]


def _paper_db_identity(conn):
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(paper_workspace)")}
    if not {"id", "namespace", "origin", "imported_legacy", "dataset_id"} <= columns:
        return False
    rows = conn.execute("SELECT id,namespace,origin,imported_legacy,dataset_id "
                        "FROM paper_workspace LIMIT 2").fetchall()
    if len(rows) != 1:
        return False
    try:
        row = rows[0]
        return (row[:4] == (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0)
                and str(uuid.UUID(row[4])) == row[4])
    except (TypeError, ValueError, AttributeError):
        return False


def _catalog_primary_identity(conn, target):
    """Catalog identity cannot infer PPI authority from plain name/market alone."""
    tables = {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "financial_instrument_catalog" not in tables:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    columns = {str(row[1]) for row in conn.execute(
        "PRAGMA table_info(financial_instrument_catalog)")}
    required = {"ticker", "instrument_type", "market", "currency",
                "settlement", "settlement_source"}
    if not required <= columns:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    # Mirror the reviewed A595 product pre-GET rule. A complementary-only
    # IOL/BYMA sibling MUST NOT veto a unique PPI identity, but a second PPI
    # sibling (even stale) does veto the 3-field SDK request. REQUEST_CANDIDATE
    # is selection authority, NOT independent native source PIT attestation.
    rows = conn.execute("SELECT ticker,instrument_type,market,currency,settlement,settlement_source "
        "FROM financial_instrument_catalog WHERE ticker=? AND instrument_type=? "
        "AND settlement=? AND settlement_source IN ('PPI_FIELD','REQUEST_CANDIDATE') "
        "LIMIT 3", (target[0], target[1], target[4])).fetchall()
    if not rows:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    if len(rows) != 1 or tuple(rows[0][:5]) != target:
        return False, "PPI_PRIMARY_FIVE_KEY_NOT_UNIQUE"
    if rows[0][5] not in {"PPI_FIELD", "REQUEST_CANDIDATE"}:
        return False, "PPI_PRIMARY_SOURCE_NOT_ATTESTED"
    return True, "PPI_PRIMARY_FIVE_KEY_UNIQUE"


def host_preflight(slot, db=DB, *, enforce_mount=True):
    report = {"schema": SCHEMA, "phase": "HOST_PREFLIGHT", "state": "NO_VERIFICADO",
              "reason": "HOST_PREFLIGHT_OR_RUNTIME_HOLD", "ppi_gets": 0,
              "target": None, "db_opened": False, "sqlite_ro_mount_protected": False,
              "paper_db_identity_verified": False, "ppi_primary_5key_unique": False,
              "orders_sent": 0, "source_pit_verified": False}
    try:
        target = target_for_slot(slot)
        if not db.is_absolute() or db.is_symlink() or not (db.exists() and db.is_file()):
            raise ValueError("DB_PAPER_IDENTITY_HOLD")
        if enforce_mount and not _private_readonly_mount(db):
            raise ValueError("HOST_RO_MOUNT_REQUIRED")
        report["sqlite_ro_mount_protected"] = enforce_mount
        uri = "file:" + quote(str(db), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.25, isolation_level=None) as conn:
            conn.execute("PRAGMA query_only=ON")
            report["db_opened"] = True
            if not _paper_db_identity(conn):
                raise ValueError("DB_PAPER_IDENTITY_HOLD")
            report["paper_db_identity_verified"] = True
            okay, reason = _catalog_primary_identity(conn, target)
            if not okay:
                raise ValueError(reason)
        report.update(state="IDENTITY_ONLY_NO_GET", reason="PPI_PRIMARY_FIVE_KEY_UNIQUE",
                      target=list(target), ppi_primary_5key_unique=True)
    except (sqlite3.Error, OSError, ValueError, TypeError) as err:
        code = str(err)
        report["reason"] = code if code in REASONS else "HOST_PREFLIGHT_OR_RUNTIME_HOLD"
    return report


def _utc(raw):
    if not isinstance(raw, str) or not 1 <= len(raw) <= 64:
        raise ValueError("PPI_NATIVE_CLOCK_INVALID")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("PPI_NATIVE_CLOCK_INVALID") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("PPI_NATIVE_CLOCK_INVALID")
    return dt.astimezone(timezone.utc)


def _exit_round_fresh(cycle, *, now=None):
    if not isinstance(cycle, dict):
        return False
    try:
        # runtime_budget_snapshot() exposes normalized UNIX epoch seconds,
        # not an ISO string. It has already structurally checked its own
        # current ledger coverage and basis when returning OBSERVED.
        at = cycle.get("recorded_at")
        if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
            return False
        current = now or datetime.now(timezone.utc)
        if not isinstance(current, datetime) or current.tzinfo is None:
            return False
        age_ms = (current.astimezone(timezone.utc).timestamp() - at) * 1000
        return 0 <= age_ms <= MAX_EXIT_ROUND_AGE_MS
    except (TypeError, ValueError):
        return False


def _ppi_url_path(method, url, target):
    """Independently pre-check the PreparedRequest before HTTPAdapter.send."""
    try:
        parsed = urlsplit(str(url))
        if (parsed.scheme != "https" or parsed.hostname != PPI_HOST
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or bool(parsed.fragment)):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        path = parsed.path.lower().rstrip("/")
        if method == "POST" and path in {
                "/api/1.0/account/loginapi", "/api/1.0/account/refreshtoken"}:
            if parsed.query:
                raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
            return path
        if method != "GET" or path not in {
                "/api/1.0/marketdata/current", "/api/1.0/marketdata/book"}:
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        query = [(k.lower(), value) for k, value in
                 parse_qsl(parsed.query, keep_blank_values=True)]
        if (len(query) != 3 or len({key for key, _ in query}) != 3 or
                dict(query) != {"ticker": target[0],
                                "type": PPI_TYPES[target[1]],
                                "settlement": target[4]}):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        return path
    except (ValueError, TypeError):
        raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID") from None


class _Silence:
    def write(self, content):
        return len(content)

    def flush(self):
        pass

    def isatty(self):
        return False


def existing_container_keypair():
    path = os.environ.get("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 4096:
            return None
        with os.fdopen(fd, "rb") as source:
            fd = None
            pairs = source.read(4097)
        def unique(items):
            found = {}
            for name, value in items:
                if name in found:
                    raise ValueError("DUPLICATE_SECRET_FIELD")
                found[name] = value
            return found
        data = json.loads(pairs, object_pairs_hook=unique)
        if isinstance(data, dict) and all(isinstance(data.get(key), str) and
                1 <= len(data[key]) <= 2048 for key in ("api_key", "api_secret")):
            return data["api_key"], data["api_secret"]
    except (OSError, ValueError, UnicodeError, TypeError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return None


def _existing_shared_budget():
    from cg_paper_workspace import artifact_root, database_path
    from rc6_ppi_global_budget import (RuntimePPIBudget, budget_from_environment,
                                       runtime_budget_snapshot)
    database = database_path()
    sidecar = artifact_root(database) / "ppi-budget/global.sqlite"
    if any(path.is_symlink() for path in (sidecar, *sidecar.parents)):
        return None, "PPI_BUDGET_SIDECAR_PATH_UNSAFE"
    try:
        before = sidecar.stat()
        if not sidecar.is_file() or before.st_nlink != 1:
            return None, "PPI_BUDGET_SIDECAR_PATH_UNSAFE"
        snapshot = runtime_budget_snapshot(database)
        if snapshot.get("status") != "OBSERVED":
            return None, "PPI_BUDGET_SNAPSHOT_NOT_OBSERVED"
        if (snapshot.get("receipt_retention") or {}).get("status") != "READY":
            return None, "PPI_RETENTION_NOT_READY"
        exit_service = snapshot.get("exit_service") or {}
        if exit_service.get("lower_suspended") is not False:
            return None, "PPI_EXIT_LOWER_SUSPENDED"
        cycle = exit_service.get("round") or {}
        if (cycle.get("status") != "COMPLETE" or
                cycle.get("required_scope_verified_current") is not True or
                cycle.get("required_scope_basis") != "CURRENT_VERIFIED_PAPER_LEDGER"):
            return None, "PPI_EXIT_ROUND_NOT_ATTESTED"
        if not _exit_round_fresh(cycle):
            return None, "PPI_EXIT_ROUND_STALE_OR_CLOCK_INVALID"
        shared = budget_from_environment(database)
        after = sidecar.stat()
        if (type(shared) is not RuntimePPIBudget or Path(shared.path) != sidecar
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            return None, "PPI_BUDGET_RUNTIME_BACKEND_MISMATCH"
        return shared, None
    except FileNotFoundError:
        return None, "PPI_BUDGET_SIDECAR_ABSENT"
    except (OSError, ValueError, TypeError, AttributeError):
        return None, "PPI_BUDGET_PROBE_UNAVAILABLE"


class TwoGetAtomicBudget:
    """Exactly one lease for Current then one for Book, no retries/fallback."""
    def __init__(self, shared):
        self.shared = shared
        self.acquired = []
        self.started = set()
        self.finished = set()

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        expected = ("current", "book")
        if len(self.acquired) >= 2 or endpoint != expected[len(self.acquired)]:
            return {"allowed": False, "reason": "A630_ENDPOINT_ORDER_DENIED"}
        if consumer != "UNSCOPED" or priority != "DISCOVERY":
            return {"allowed": False, "reason": "A630_CONSUMER_PRIORITY_DENIED"}
        # Append BEFORE contacting the budget: no second lease attempt on errors.
        self.acquired.append((endpoint, None))
        admitted = self.shared.acquire(endpoint, consumer=consumer, priority=priority)
        lease = admitted.get("lease") if isinstance(admitted, dict) else None
        if not isinstance(admitted, dict) or admitted.get("allowed") is not True or not (
                isinstance(lease, str) and 1 <= len(lease) <= 256):
            return {"allowed": False, "reason": "ATOMIC_LEASE_DENIED"}
        if any(lease == old for _, old in self.acquired[:-1]):
            return {"allowed": False, "reason": "ATOMIC_LEASE_DENIED"}
        self.acquired[-1] = (endpoint, lease)
        return admitted

    def _check(self, lease):
        if lease not in [value for _, value in self.acquired if value]:
            raise ValueError("ATOMIC_LEASE_DENIED")

    def start(self, lease):
        self._check(lease)
        if lease in self.started:
            raise ValueError("ATOMIC_LEASE_DENIED")
        self.shared.start(lease)
        self.started.add(lease)

    def wire_scope(self, lease):
        """Return a DEFERRED context, matching pinned product guard ordering.

        Product guarded_send obtains ownership = wire_scope(lease), ENTERs it,
        and only then invokes start(lease) BEFORE delegating to the adapter.
        An eager started-check here deadlocks every Current GET. The strict
        HTTPAdapter pre-wire guard checks started immediately before socket.
        """
        self._check(lease)
        if lease in self.finished:
            raise ValueError("ATOMIC_LEASE_DENIED")
        @contextmanager
        def entered():
            if lease in self.finished:
                raise ValueError("ATOMIC_LEASE_DENIED")
            with self.shared.wire_scope(lease):
                yield
        return entered()

    def finish(self, lease, **outcome):
        self._check(lease)
        if lease not in self.started or lease in self.finished:
            raise ValueError("ATOMIC_LEASE_DENIED")
        self.shared.finish(lease, **outcome)
        self.finished.add(lease)

    def report_error(self, endpoint, code):
        if hasattr(self.shared, "report_error"):
            self.shared.report_error(endpoint, code)

    def completed(self):
        return (len(self.acquired) == 2 and all(value for _, value in self.acquired)
                and len(self.started) == len(self.finished) == 2)


@contextmanager
def _strict_adapter_send(target, budget):
    """Deny Search/Estimate/Book2/redirect BEFORE any adapter wire send.

    ProductMarketReader's own SDK guard may use configuration/search GETs in
    other workflows; no such route has authority in this two-slot diagnostic.
    The native shared lease is still mandatory for each of the two sends.
    """
    from requests.adapters import HTTPAdapter
    original = HTTPAdapter.send
    sends = []
    posts = []

    def guarded(adapter, request, *args, **kwargs):
        method = getattr(request, "method", None)
        if method not in {"POST", "GET"} or kwargs.get("verify", True) in (False, None):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        path = _ppi_url_path(method, getattr(request, "url", None), target)
        if method == "POST":
            if path in posts or len(posts) >= 2:
                raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
            posts.append(path)
        else:
            expected = ("/api/1.0/marketdata/current", "/api/1.0/marketdata/book")
            if len(sends) >= 2 or path != expected[len(sends)]:
                raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
            if len(budget.acquired) != len(sends) + 1:
                raise ValueError("ATOMIC_LEASE_DENIED")
            endpoint, lease = budget.acquired[-1]
            if (endpoint != path.rsplit("/", 1)[1] or not lease
                    or lease not in budget.started or lease in budget.finished):
                raise ValueError("ATOMIC_LEASE_DENIED")
            sends.append(path)  # increment before calling adapter; retry denied
        return original(adapter, request, *args, **kwargs)

    HTTPAdapter.send = guarded
    try:
        yield sends
    finally:
        HTTPAdapter.send = original


@contextmanager
def _http_body_hashes(target):
    """Hash bounded decompressed HTTP response body in the ephemeral process.

    Digest is NOT the original compressed TLS wire bytes, nor a PPI signature.
    Only the SDK's allowed path/host can be recorded; duplicates are rejected.
    """
    from requests.models import Response
    original = Response.iter_content
    hits = []

    def capture(response, chunk_size=1, decode_unicode=False):
        parsed = urlsplit(str(response.url))
        path = parsed.path.lower().rstrip("/")
        if path not in {"/api/1.0/marketdata/current", "/api/1.0/marketdata/book"}:
            return original(response, chunk_size=chunk_size, decode_unicode=decode_unicode)
        _ppi_url_path("GET", response.url, target)
        if any(hit[0] == path for hit in hits):
            raise ValueError("PROVIDER_GET_SCOPE_OR_COUNT_INVALID")
        row = [path, sha256(), 0]
        hits.append(row)
        iterator = original(response, chunk_size=chunk_size, decode_unicode=decode_unicode)

        def limited():
            for chunk in iterator:
                raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                row[2] += len(raw)
                if row[2] > MAX_BODY:
                    raise ValueError("BODY_BUDGET_OR_DIGEST_INVALID")
                row[1].update(raw)
                yield chunk
        return limited()

    Response.iter_content = capture
    try:
        yield hits
    finally:
        Response.iter_content = original


def _base_receipt(target):
    return {"schema": SCHEMA, "phase": "CONTAINER_CAPTURE", "state": "NO_VERIFICADO",
        "reason": "NOT_REQUESTED", "target": list(target), "ppi_type": PPI_TYPES[target[1]],
        "source": "PPI_SDK_HTTPS_ALLOWLIST_NOT_PROVIDER_SIGNED",
        "budget_consumer": "UNSCOPED", "budget_priority": "DISCOVERY",
        "expected_product_sha": PRODUCT_SHA, "mode": "PRODUCTION_PAPER",
        "real_order_capability": "BLOCKED", "orders_sent": 0,
        "trading_db_writes": 0, "history_db_writes": 0, "ppi_watch_mutations": 0,
        "global_budget_lease_only": True, "source_pit_verified": False,
        "independent_provider_signature_verified": False, "paper_sim_verified": False,
        "current_gets": 0, "book_gets": 0, "lease_acquires": 0,
        "adapter_current_sends": 0, "adapter_book_sends": 0,
        "lease_starts": 0, "lease_finishes": 0,
        "first_known_at_utc": None, "current_provider_at_utc": None,
        "book_provider_at_utc": None, "current_http_body_sha256": None,
        "book_http_body_sha256": None, "current_body_bytes": 0,
        "book_body_bytes": 0, "book_bid_levels": 0, "book_offer_levels": 0,
        "http_digest_semantics": "SDK_RESPONSE_DECOMPRESSED_BYTES_NOT_TLS_WIRE",
        "credential_location": "EXISTING_PRODUCT_CONTAINER_ONLY",
        "execution_allowed": False, "raw_provider_payload_persisted": False}


def unverified_transport_receipt(reason="HOST_PREFLIGHT_OR_RUNTIME_HOLD"):
    """No false zero counters after SSH timeout/partial Current or lost stdio.

    A transport timeout can happen AFTER a shared-budget Current GET and before
    Book or receipt emission. Absence of a verified receipt is not proof that
    no provider quota was consumed; counters MUST be unknown, not zero.
    """
    if reason not in REASONS:
        reason = "HOST_PREFLIGHT_OR_RUNTIME_HOLD"
    return {"schema": SCHEMA, "state": "NO_VERIFICADO", "reason": reason,
            "current_gets": None, "book_gets": None,
            "transport_count_authority": "NO_VERIFICADO",
            "orders_sent": 0, "execution_allowed": False,
            "source_pit_verified": False}


def capture_two(target, credentials, shared_budget, reader_factory,
                *, clock=None, http_limiter=None, adapter_guard=None,
                budget_reason=None):
    receipt = _base_receipt(target)
    if credentials is None:
        receipt["reason"] = "PPI_KEYPAIR_UNAVAILABLE"
        return receipt
    if shared_budget is None:
        receipt["reason"] = budget_reason if budget_reason in REASONS else (
            "SHARED_BUDGET_OR_EXIT_RESERVE_NOT_ATTESTED")
        return receipt
    budget = TwoGetAtomicBudget(shared_budget)
    reads = {"current": None, "book": None}
    events = []
    reader = None
    adapter_sends = []
    reader_close_failed = False
    limiter = http_limiter or (lambda: _http_body_hashes(target))
    guard = adapter_guard or (lambda: _strict_adapter_send(target, budget))
    observed = clock or (lambda: datetime.now(timezone.utc))
    try:
        def audit(method, path, action):
            if len(events) < 12:
                events.append((method, path, action))
            else:
                events.append(("OVERFLOW", "", "BLOCKED"))
        with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
            # CRITICAL TRANSPORT ORDER: ProductMarketReader.__init__ installs
            # its own ReadOnlyTransportGuard.guarded_send, which acquires and
            # STARTS the native budget lease before calling its saved adapter.
            # Install strict send first so native guarded_send wraps it. Then
            # close the reader BEFORE restoring our strict adapter seam.
            with guard() as adapter_sends:
                try:
                    reader = reader_factory(credentials[0], credentials[1], budget=budget,
                        consumer="UNSCOPED", priority="DISCOVERY", audit=audit)
                    reader.login_once()
                    with limiter() as body:
                        reads["current"] = reader.current(target[0], PPI_TYPES[target[1]], target[4])
                        reads["book"] = reader.book(target[0], PPI_TYPES[target[1]], target[4])
                finally:
                    if reader is not None:
                        try:
                            reader.close()
                        except Exception:
                            reader_close_failed = True
                        reader = None
        now = observed()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("PPI_NATIVE_CLOCK_INVALID")
        now = now.astimezone(timezone.utc)
        current, book = reads["current"], reads["book"]
        if (not isinstance(current, dict) or not isinstance(book, dict)
                or not isinstance(book.get("bids"), list)
                or not isinstance(book.get("offers"), list)
                or len(book["bids"]) > MAX_LEVELS or len(book["offers"]) > MAX_LEVELS):
            raise ValueError("PROVIDER_RESPONSE_SHAPE_INVALID")
        cp, bp = _utc(current.get("date")), _utc(book.get("date"))
        if not all(0 <= (now - src).total_seconds() * 1000 <= MAX_AGE_MS for src in (cp, bp)):
            raise ValueError("PPI_NATIVE_CLOCK_STALE_OR_FUTURE")
        expected_paths = ["/api/1.0/marketdata/current", "/api/1.0/marketdata/book"]
        if ([row[0] for row in body] != expected_paths
                or any(not 0 < row[2] <= MAX_BODY for row in body)):
            raise ValueError("BODY_BUDGET_OR_DIGEST_INVALID")
        receipt.update(first_known_at_utc=now.isoformat(),
            current_provider_at_utc=cp.isoformat(), book_provider_at_utc=bp.isoformat(),
            current_http_body_sha256=body[0][1].hexdigest(),
            book_http_body_sha256=body[1][1].hexdigest(),
            current_body_bytes=body[0][2], book_body_bytes=body[1][2],
            book_bid_levels=len(book["bids"]), book_offer_levels=len(book["offers"]))
    except Exception as error:
        receipt["reason"] = (str(error) if str(error) in REASONS
                             else "PPI_PROVIDER_OR_AUTH_FAILED")
    finally:
        if reader_close_failed:
            receipt["reason"] = "PPI_PROVIDER_OR_AUTH_FAILED"
        receipt["lease_acquires"] = len(budget.acquired)
        receipt["lease_starts"] = len(budget.started)
        receipt["lease_finishes"] = len(budget.finished)
        receipt["adapter_current_sends"] = adapter_sends.count("/api/1.0/marketdata/current")
        receipt["adapter_book_sends"] = adapter_sends.count("/api/1.0/marketdata/book")
        receipt["current_gets"] = sum(e == ("GET", "/api/1.0/marketdata/current", "ALLOWED")
                                      for e in events)
        receipt["book_gets"] = sum(e == ("GET", "/api/1.0/marketdata/book", "ALLOWED")
                                   for e in events)
        allowed = {("POST", "/api/1.0/account/loginapi", "ALLOWED"),
                   ("POST", "/api/1.0/account/refreshtoken", "ALLOWED"),
                   ("GET", "/api/1.0/marketdata/current", "ALLOWED"),
                   ("GET", "/api/1.0/marketdata/book", "ALLOWED")}
        if (any(event not in allowed for event in events) or
                receipt["current_gets"] > 1 or receipt["book_gets"] > 1 or
                receipt["adapter_current_sends"] > 1 or receipt["adapter_book_sends"] > 1 or
                sum(e == ("POST", "/api/1.0/account/loginapi", "ALLOWED")
                    for e in events) > 1):
            receipt["reason"] = "PROVIDER_GET_SCOPE_OR_COUNT_INVALID"
        if (receipt["reason"] == "NOT_REQUESTED" and budget.completed() and
                receipt["current_gets"] == receipt["book_gets"] == 1 and
                receipt["adapter_current_sends"] == receipt["adapter_book_sends"] == 1 and
                receipt["current_http_body_sha256"] and receipt["book_http_body_sha256"]):
            receipt["state"] = "EVIDENCE_ONLY_NO_GO"
            receipt["reason"] = "PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT"
        else:
            receipt.update(first_known_at_utc=None, current_provider_at_utc=None,
                book_provider_at_utc=None, current_http_body_sha256=None,
                book_http_body_sha256=None, current_body_bytes=0,
                book_body_bytes=0, book_bid_levels=0, book_offer_levels=0)
            if receipt["reason"] == "NOT_REQUESTED":
                receipt["reason"] = "ATOMIC_LEASE_DENIED"
    return receipt


def verify_receipt(obj, target):
    if not isinstance(obj, dict) or set(obj) != set(_base_receipt(target)):
        return False
    fixed = ("schema", "phase", "target", "ppi_type", "source", "expected_product_sha",
        "mode", "real_order_capability", "orders_sent", "trading_db_writes",
        "history_db_writes", "ppi_watch_mutations", "global_budget_lease_only",
        "source_pit_verified", "independent_provider_signature_verified",
        "paper_sim_verified", "http_digest_semantics", "credential_location",
        "budget_consumer", "budget_priority",
        "execution_allowed", "raw_provider_payload_persisted")
    if any(obj[key] != _base_receipt(target)[key] for key in fixed):
        return False
    if obj["reason"] not in REASONS or obj["state"] not in {"NO_VERIFICADO", "EVIDENCE_ONLY_NO_GO"}:
        return False
    for field in ("current_gets", "book_gets", "adapter_current_sends",
                  "adapter_book_sends", "lease_acquires", "lease_starts",
                  "lease_finishes", "current_body_bytes", "book_body_bytes",
                  "book_bid_levels", "book_offer_levels"):
        if type(obj[field]) is not int or not 0 <= obj[field] <= (
                MAX_BODY if "bytes" in field else MAX_LEVELS if "levels" in field else 2):
            return False
    digests = (obj["current_http_body_sha256"], obj["book_http_body_sha256"])
    dates = (obj["first_known_at_utc"], obj["current_provider_at_utc"], obj["book_provider_at_utc"])
    if obj["state"] == "EVIDENCE_ONLY_NO_GO":
        if (obj["reason"] != "PPI_GET_RECEIPT_SDK_ONLY_NO_CAUSAL_PIT" or
                obj["current_gets"] != 1 or obj["book_gets"] != 1 or
                obj["adapter_current_sends"] != 1 or obj["adapter_book_sends"] != 1 or
                any(obj[k] != 2 for k in ("lease_acquires", "lease_starts", "lease_finishes")) or
                not all(isinstance(x, str) and SHA256.fullmatch(x) for x in digests) or
                not all(isinstance(x, str) for x in dates) or
                not all(0 < obj[x] <= MAX_BODY for x in ("current_body_bytes", "book_body_bytes"))):
            return False
        try:
            known, current, book = map(_utc, dates)
            return all(0 <= (known - t).total_seconds() * 1000 <= MAX_AGE_MS for t in (current, book))
        except ValueError:
            return False
    return all(x is None for x in (*digests, *dates))


def container_main():
    try:
        from _version import MODE, REAL_ORDER_CAPABILITY
        if (MODE != "PRODUCTION_PAPER" or REAL_ORDER_CAPABILITY != "BLOCKED" or
                os.environ.get("RC6_A630_EXPECTED_PRODUCT_SHA") != PRODUCT_SHA):
            raise ValueError("PRODUCT_IMAGE_OR_MODE_HOLD")
        target = decode_target(os.environ.get("RC6_A630_TARGET_B64"))
        credentials = existing_container_keypair()
        with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
            budget, budget_reason = (_existing_shared_budget() if credentials is not None
                                     else (None, "PPI_KEYPAIR_UNAVAILABLE"))
            if credentials and budget:
                from bd_ppi_readonly_guard import ProductionMarketReader
            else:
                ProductionMarketReader = None
            receipt = capture_two(target, credentials, budget, ProductionMarketReader,
                                  budget_reason=budget_reason)
        if not verify_receipt(receipt, target):
            raise ValueError("RECEIPT_INVALID")
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0 if receipt["state"] == "EVIDENCE_ONLY_NO_GO" else 2
    except Exception:
        print(json.dumps({**unverified_transport_receipt("PRODUCT_IMAGE_OR_MODE_HOLD"),
            "phase": "CONTAINER_CAPTURE"}, sort_keys=True))
        return 2


def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.argv[1:] and sys.argv[1] == "host-preflight" and len(sys.argv) == 3:
        result = host_preflight(sys.argv[2])
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["state"] == "IDENTITY_ONLY_NO_GET" else 2
    if sys.argv[1:] == ["container-capture"]:
        return container_main()
    raise SystemExit("A630_EXPLICIT_MODE_REQUIRED")


if __name__ == "__main__":
    raise SystemExit(main())

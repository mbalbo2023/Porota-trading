"""A338/A340: one fail-closed PPI Book observation through the EXISTING global lease.

Streamed by the manual A331 GitHub Action into the PINNED PAPER container.
Only the shared PPI budget sidecar may be written by its native acquire/start/
finish implementation. Never write trading/history DBs or PPI Watch state.
This SDK-parsed observation is NOT independent signed PIT or PAPER readiness.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import stat
from urllib.parse import urlsplit


SCHEMA = "rc6.a338.host.ppi.book.atomic.v1"
PRODUCT_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
MAX_TARGET_BYTES = 512
MAX_PARSED_BYTES = 128 * 1024
MAX_LEVELS = 200
MAX_STALE_SECONDS = 120
SYMBOL = re.compile(r"[A-Z0-9./_-]{1,40}\Z")
SETTLEMENT = re.compile(r"[A-Z0-9-]{1,25}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
PPI_TYPES = {
    "ACCIONES": "ACCIONES", "CEDEARS": "CEDEARS", "ETFS": "ETF",
    "BONOS": "BONOS", "LETRAS": "LETRAS", "OBLIGACIONES": "ON",
    "OPCIONES": "OPCIONES", "FUTUROS": "FUTUROS",
    "CAUCIONES": "CAUCIONES", "FCI": "FCI",
}
MARKETS = frozenset({"BYMA", "BCBA", "ROFEX", "A3", "NYSE", "NASDAQ", "AMEX"})
CURRENCIES = frozenset({"ARS", "USD"})


def existing_container_keypair():
    """Read observer's existing mounted PPI API keypair, never runner secrets.

    The pinned observer uses PPI_PRODUCTION_SECRET_FILE and its default Docker
    secret path, not PPI_API_KEY/PPI_API_SECRET environment variables. Read a
    small, regular, non-symlink file without writing or logging credentials.
    """
    path = os.environ.get("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 4096:
            return None
        with os.fdopen(fd, "rb") as source:
            fd = None
            payload = source.read(4097)
        if not 0 < len(payload) <= 4096:
            return None
        def unique_fields(fields):
            value = {}
            for name, item in fields:
                if name in value:
                    raise ValueError("A343_DUPLICATE_SECRET_FIELD")
                value[name] = item
            return value
        pair = json.loads(payload, object_pairs_hook=unique_fields)
        if not isinstance(pair, dict):
            return None
        key, secret = pair.get("api_key"), pair.get("api_secret")
        if all(isinstance(item, str) and 1 <= len(item) <= 2048 for item in (key, secret)):
            return key, secret
    except (OSError, ValueError, UnicodeError, TypeError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return None
REASONS = frozenset({
    "NO_PROVIDER_CALL", "EXISTING_CONTAINER_CREDENTIALS_ABSENT",
    "PPI_BUDGET_EXISTING_SIDECAR_NOT_CERTIFIED", "PPI_BUDGET_ATOMIC_LEASE_UNVERIFIED",
    "PPI_BOOK_AUTH_OR_PROVIDER_FAILURE", "PPI_BOOK_GET_SCOPE_OR_COUNT_INVALID",
    "PPI_BOOK_SDK_PARSED_DIGEST_ONLY", "PPI_BOOK_PAYLOAD_SHAPE_UNVERIFIED",
    "PPI_BOOK_PAYLOAD_BUDGET_EXCEEDED", "PPI_BOOK_PROVIDER_CLOCK_UNVERIFIED",
    "PPI_BOOK_PROVIDER_STALE_OR_FUTURE", "PPI_BOOK_GUARD_CLOSE_FAILED",
    "PPI_BOOK_OBSERVATION_CLOCK_UNVERIFIED",
})


class _Silence:
    def write(self, value):
        return len(value)

    def flush(self):
        pass

    def isatty(self):
        return False


@contextmanager
def _cap_book_body():
    """Bound actual decompressed Book HTTP body while product guard reads it.

    requests.Response.content uses iter_content. The product allowlist invokes
    it at the adapter seam while the native global budget lease is still live.
    This patch affects only this ephemeral `docker exec python -B -` process.
    """
    from requests.models import Response
    original = Response.iter_content

    def capped(response, chunk_size=1, decode_unicode=False):
        iterator = original(response, chunk_size=chunk_size,
                            decode_unicode=decode_unicode)
        if urlsplit(str(response.url)).path.lower().rstrip("/") != "/api/1.0/marketdata/book":
            return iterator

        def limited():
            total = 0
            for chunk in iterator:
                total += len(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
                if total > MAX_PARSED_BYTES:
                    raise ValueError("PPI_BOOK_PAYLOAD_BUDGET_EXCEEDED")
                yield chunk

        return limited()

    Response.iter_content = capped
    try:
        yield
    finally:
        Response.iter_content = original


def preflight(encoded):
    """Validate exactly one full 5-key identity BEFORE accessing credentials."""
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 1024:
        raise ValueError("A338_EXACT_TARGET_BUDGET_INVALID")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_TARGET_BYTES:
            raise ValueError("A338_EXACT_TARGET_BUDGET_INVALID")
        targets = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("A338_EXACT_TARGET_MALFORMED") from None
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], list):
        raise ValueError("A338_EXACT_ONE_TARGET_REQUIRED")
    fields = targets[0]
    if len(fields) != 5 or any(not isinstance(value, str) for value in fields):
        raise ValueError("A338_FULL_FIVE_FIELD_IDENTITY_REQUIRED")
    ticker, family, market, currency, settlement = fields
    if (not SYMBOL.fullmatch(ticker) or family not in PPI_TYPES
            or market not in MARKETS or currency not in CURRENCIES
            or not SETTLEMENT.fullmatch(settlement)):
        raise ValueError("A338_EXACT_IDENTITY_INVALID")
    return tuple(fields)


def _utc(raw):
    if not isinstance(raw, str) or not 1 <= len(raw) <= 64:
        raise ValueError("PPI_BOOK_PROVIDER_CLOCK_UNVERIFIED")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("PPI_BOOK_PROVIDER_CLOCK_UNVERIFIED") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("PPI_BOOK_PROVIDER_CLOCK_UNVERIFIED")
    return value.astimezone(timezone.utc)


def _receipt(target):
    return {
        "schema": SCHEMA, "source": "PPI", "requested_identity": list(target),
        "ppi_type": PPI_TYPES[target[1]], "state": "NO_VERIFICADO",
        "reason": "NO_PROVIDER_CALL", "expected_product_sha": PRODUCT_SHA,
        "credential_location": "EXISTING_PRODUCT_CONTAINER_ONLY",
        "budget_scope": "EXISTING_GLOBAL_PPI_SIDECAR_ATOMIC_LEASE_ONLY",
        "budget_snapshot": "NOT_CERTIFIED", "lease_acquires": 0,
        "lease_started": False, "lease_finished": False,
        "book_invocations": 0, "book_gets": 0, "provider_at_utc": None,
        "first_known_at_utc": None, "provider_age_ms": None,
        "sdk_parsed_sha256": None, "sdk_parsed_bytes": 0,
        "bid_levels": 0, "offer_levels": 0,
        "provenance": "SDK_PARSED_BOOK_UNVERIFIED_NOT_WIRE_RAW",
        "independent_ed25519_verified": False,
        "source_pit_verified": False, "paper_sim_verified": False,
        "paper_mode": "PRODUCTION_PAPER", "real_order_capability": "BLOCKED",
        "real_orders_sent_by_action": 0, "trading_db_writes_by_action": 0,
        "history_v2_writes_by_action": 0, "ppi_watch_mutations_by_action": 0,
        "execution_allowed": False, "raw_provider_payload_persisted": False,
    }


class OneGetAtomicBudget:
    """One wire Book acquire; a missing lease MUST NOT become an unbudgeted GET.

    RuntimePPIBudget.acquire can return allowed=True, lease=None when there is
    no activated authority. Product readers can tolerate that for other use;
    this optional diagnostic CANNOT. The real shared budget serializes writes.
    """

    def __init__(self, shared):
        if shared is None:
            raise ValueError("PPI_BUDGET_ATOMIC_LEASE_UNVERIFIED")
        self.shared = shared
        self.acquires = 0
        self.lease = None
        self.started = False
        self.finished = False

    def acquire(self, endpoint, *, consumer="UNSCOPED", priority="DISCOVERY"):
        if (endpoint != "book" or consumer != "RC6_A338_BOOK_DIAGNOSTIC"
                or priority != "DISCOVERY" or self.acquires != 0):
            return {"allowed": False, "reason": "PPI_A338_BOOK_SCOPE_OR_COUNT_DENIED"}
        # Increment BEFORE shared acquisition: failures/retries get no second GET.
        self.acquires = 1
        admitted = self.shared.acquire(endpoint, consumer=consumer, priority=priority)
        if not isinstance(admitted, dict) or not admitted.get("allowed"):
            return {"allowed": False, "reason": "PPI_A338_SHARED_BUDGET_DENIED"}
        lease = admitted.get("lease")
        if not isinstance(lease, str) or not lease or len(lease) > 256:
            return {"allowed": False, "reason": "PPI_A338_ATOMIC_LEASE_REQUIRED"}
        self.lease = lease
        return admitted

    def _check(self, lease):
        if not self.lease or lease != self.lease:
            raise ValueError("PPI_BUDGET_ATOMIC_LEASE_UNVERIFIED")

    def start(self, lease):
        self._check(lease)
        if self.started:
            raise ValueError("PPI_A338_DUPLICATE_LEASE_START")
        self.shared.start(lease)
        self.started = True

    def wire_scope(self, lease):
        self._check(lease)
        return self.shared.wire_scope(lease)

    def finish(self, lease, **outcome):
        self._check(lease)
        if self.finished:
            raise ValueError("PPI_A338_DUPLICATE_LEASE_FINISH")
        self.shared.finish(lease, **outcome)
        self.finished = True

    def report_error(self, endpoint, code):
        if hasattr(self.shared, "report_error"):
            self.shared.report_error(endpoint, code)


def verify_sanitized_receipt(value, target):
    """A strict public allowlist: never pass untrusted container JSON through."""
    expected = _receipt(target)
    if not isinstance(value, dict) or set(value) != set(expected):
        return False
    fixed = ("schema", "source", "requested_identity", "ppi_type",
             "expected_product_sha", "credential_location", "budget_scope",
             "provenance", "independent_ed25519_verified", "source_pit_verified",
             "paper_sim_verified", "paper_mode", "real_order_capability",
             "real_orders_sent_by_action", "trading_db_writes_by_action",
             "history_v2_writes_by_action", "ppi_watch_mutations_by_action",
             "execution_allowed", "raw_provider_payload_persisted")
    if any(value.get(key) != expected[key] for key in fixed):
        return False
    if value.get("state") not in {"NO_VERIFICADO", "EVIDENCE_ONLY_NO_GO"}:
        return False
    if value.get("reason") not in REASONS or value.get("budget_snapshot") not in {"OBSERVED", "NOT_CERTIFIED"}:
        return False
    for key in ("lease_acquires", "book_invocations", "book_gets"):
        if type(value.get(key)) is not int or not 0 <= value[key] <= 1:
            return False
    for key in ("lease_started", "lease_finished"):
        if type(value.get(key)) is not bool:
            return False
    for key, limit in (("sdk_parsed_bytes", MAX_PARSED_BYTES),
                       ("bid_levels", MAX_LEVELS), ("offer_levels", MAX_LEVELS)):
        if type(value.get(key)) is not int or not 0 <= value[key] <= limit:
            return False
    digest = value.get("sdk_parsed_sha256")
    if digest is not None and (not isinstance(digest, str) or not SHA256.fullmatch(digest)):
        return False
    known = value.get("first_known_at_utc")
    provided = value.get("provider_at_utc")
    age_ms = value.get("provider_age_ms")
    if any(x is not None for x in (known, provided, age_ms)):
        if not all(x is not None for x in (known, provided, age_ms)):
            return False
        if type(age_ms) is not int or not 0 <= age_ms <= MAX_STALE_SECONDS * 1000:
            return False
        try:
            first = _utc(known)
            source = _utc(provided)
            if abs((first - source).total_seconds() * 1000 - age_ms) > 1:
                return False
            if first > datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
    if value["state"] == "EVIDENCE_ONLY_NO_GO":
        return (value["reason"] == "PPI_BOOK_SDK_PARSED_DIGEST_ONLY"
                and value["budget_snapshot"] == "OBSERVED"
                and value["lease_acquires"] == value["book_invocations"] == value["book_gets"] == 1
                and value["lease_started"] and value["lease_finished"]
                and digest is not None and 0 < value["sdk_parsed_bytes"] <= MAX_PARSED_BYTES
                and known is not None)
    return (value["reason"] != "PPI_BOOK_SDK_PARSED_DIGEST_ONLY"
            and digest is None and known is None and provided is None and age_ms is None
            and value["sdk_parsed_bytes"] == value["bid_levels"] == value["offer_levels"] == 0)


def capture_one(target, credentials, shared_budget, reader_factory, *, clock=None,
                limiter=None):
    """One transient SDK Book response; serializes only sanitized summary."""
    result = _receipt(target)
    if not isinstance(credentials, tuple) or len(credentials) != 2 or any(
            not isinstance(x, str) or not x for x in credentials):
        result["reason"] = "EXISTING_CONTAINER_CREDENTIALS_ABSENT"
        return result
    if shared_budget is None:
        result["reason"] = "PPI_BUDGET_EXISTING_SIDECAR_NOT_CERTIFIED"
        return result
    result["budget_snapshot"] = "OBSERVED"
    observed = clock or (lambda: datetime.now(timezone.utc))
    budget = OneGetAtomicBudget(shared_budget)
    reader = None
    events = []

    def audit(method, path, action):
        # Fixed route/method only; never URLs, tokens, headers or payloads.
        if len(events) < 8:
            events.append((method, path, action))
        else:
            events.append(("OVER_BUDGET", "", "BLOCKED"))

    try:
        with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
            reader = reader_factory(credentials[0], credentials[1], budget=budget,
                consumer="RC6_A338_BOOK_DIAGNOSTIC", priority="DISCOVERY", audit=audit)
            reader.login_once()
            result["book_invocations"] = 1
            with (limiter() if limiter is not None else _cap_book_body()):
                value = reader.book(target[0], PPI_TYPES[target[1]], target[4])
        now = observed()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("PPI_BOOK_OBSERVATION_CLOCK_UNVERIFIED")
        now = now.astimezone(timezone.utc)
        if not isinstance(value, dict) or not isinstance(value.get("bids"), list) or not isinstance(value.get("offers"), list):
            raise ValueError("PPI_BOOK_PAYLOAD_SHAPE_UNVERIFIED")
        if len(value["bids"]) > MAX_LEVELS or len(value["offers"]) > MAX_LEVELS:
            raise ValueError("PPI_BOOK_PAYLOAD_BUDGET_EXCEEDED")
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
        if not raw or len(raw) > MAX_PARSED_BYTES:
            raise ValueError("PPI_BOOK_PAYLOAD_BUDGET_EXCEEDED")
        provider = _utc(value.get("date"))
        delta_ms = round((now - provider).total_seconds() * 1000)
        if not 0 <= delta_ms <= MAX_STALE_SECONDS * 1000:
            raise ValueError("PPI_BOOK_PROVIDER_STALE_OR_FUTURE")
        result.update(provider_at_utc=provider.isoformat(),
            first_known_at_utc=now.isoformat(), provider_age_ms=delta_ms,
            sdk_parsed_sha256=sha256(raw).hexdigest(), sdk_parsed_bytes=len(raw),
            bid_levels=len(value["bids"]), offer_levels=len(value["offers"]))
    except Exception as error:
        # SDK errors may contain secrets; only a fixed local code is public.
        result["reason"] = (str(error) if str(error) in REASONS
                            else "PPI_BOOK_AUTH_OR_PROVIDER_FAILURE")
    finally:
        result["lease_acquires"] = budget.acquires
        result["lease_started"] = budget.started
        result["lease_finished"] = budget.finished
        result["book_gets"] = sum(event == ("GET", "/api/1.0/marketdata/book", "ALLOWED")
                                  for event in events)
        if reader is not None:
            try:
                with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
                    reader.close()
            except Exception:
                result["reason"] = "PPI_BOOK_GUARD_CLOSE_FAILED"
        allowed = {("POST", "/api/1.0/account/loginapi", "ALLOWED"),
                   ("POST", "/api/1.0/account/refreshtoken", "ALLOWED"),
                   ("GET", "/api/1.0/marketdata/book", "ALLOWED")}
        if (any(event not in allowed for event in events)
                or result["book_gets"] != result["book_invocations"]
                or result["book_gets"] > 1
                or sum(e == ("POST", "/api/1.0/account/loginapi", "ALLOWED") for e in events) > 1):
            result["reason"] = "PPI_BOOK_GET_SCOPE_OR_COUNT_INVALID"
        if (result["reason"] == "NO_PROVIDER_CALL" and budget.acquires == 1
                and budget.started and budget.finished and result["book_gets"] == 1
                and result["sdk_parsed_sha256"]):
            result["state"] = "EVIDENCE_ONLY_NO_GO"
            result["reason"] = "PPI_BOOK_SDK_PARSED_DIGEST_ONLY"
        if not budget.lease or not budget.started or not budget.finished:
            result["state"] = "NO_VERIFICADO"
            if result["reason"] == "NO_PROVIDER_CALL":
                result["reason"] = "PPI_BUDGET_ATOMIC_LEASE_UNVERIFIED"
        if result["state"] != "EVIDENCE_ONLY_NO_GO":
            # Failure receipts may expose only the fixed reason, never a
            # partially parsed book or a misleading PIT/freshness claim.
            result.update(provider_at_utc=None, first_known_at_utc=None,
                provider_age_ms=None, sdk_parsed_sha256=None,
                sdk_parsed_bytes=0, bid_levels=0, offer_levels=0)
    return result


def _existing_shared_budget():
    """Snapshot is only a prerequisite; the actual lease authorizes the GET."""
    from cg_paper_workspace import artifact_root, database_path
    from rc6_ppi_global_budget import (
        RuntimePPIBudget, budget_from_environment, runtime_budget_snapshot)
    database = database_path()
    sidecar = artifact_root(database) / "ppi-budget/global.sqlite"
    if any(path.is_symlink() for path in (sidecar, *sidecar.parents)):
        return None
    try:
        before = sidecar.stat()
        if not sidecar.is_file() or before.st_nlink != 1:
            return None
        snapshot = runtime_budget_snapshot(database)
        if (snapshot.get("status") != "OBSERVED"
                or (snapshot.get("exit_service") or {}).get("lower_suspended") is True):
            return None
        budget = budget_from_environment(database)
        after = sidecar.stat()
        if (type(budget) is not RuntimePPIBudget or Path(budget.path) != sidecar
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            return None
        return budget
    except (OSError, ValueError, TypeError):
        return None


def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Unlike A331 History, the explicitly allowed shared SQLite lease MUST
    # be writable; do not set RLIMIT_FSIZE=0 or disable the native budget.
    try:
        from _version import MODE, REAL_ORDER_CAPABILITY
        if MODE != "PRODUCTION_PAPER" or REAL_ORDER_CAPABILITY != "BLOCKED":
            raise ValueError("A338_PRODUCT_PAPER_SAFETY_MISMATCH")
        if os.environ.get("RC6_A338_EXPECTED_PRODUCT_SHA") != PRODUCT_SHA:
            raise ValueError("A338_PRODUCT_PIN_MISMATCH")
        target = preflight(os.environ.get("RC6_A338_TARGET_B64"))
        credentials = existing_container_keypair()
        with redirect_stdout(_Silence()), redirect_stderr(_Silence()):
            budget = _existing_shared_budget()
            if credentials:
                from bd_ppi_readonly_guard import ProductionMarketReader
            else:
                ProductionMarketReader = None
            receipt = capture_one(target, credentials, budget, ProductionMarketReader)
        if not verify_sanitized_receipt(receipt, target):
            raise ValueError("A338_SANITIZED_RECEIPT_INVALID")
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0 if receipt["state"] == "EVIDENCE_ONLY_NO_GO" else 2
    except Exception:
        print(json.dumps({"schema": SCHEMA, "state": "NO_VERIFICADO",
            "reason": "A338_RUNTIME_GUARD_OR_RECEIPT_INVALID",
            "book_gets": None, "real_orders_sent_by_action": 0,
            "trading_db_writes_by_action": 0, "history_v2_writes_by_action": 0,
            "ppi_watch_mutations_by_action": 0, "execution_allowed": False},
            sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""A331: one PPI History GET from existing PAPER Docker credentials, no host writes.

Stream this source to `docker exec -i ... python -B -` over a pinned SSH Action.
No token, raw quote, account row, DB, host-file or order output is permitted.
The resulting digest-only manifest is RETROSPECTIVE, never causal PIT/READY.

The function layer is importable for offline adversarial tests without PPI.
Only `main()` imports the product reader from the already running container.
"""

from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import os
import re
import resource
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a331.host.ppi.history.readonly.v1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
MAX_TARGET_BYTES = 512
MAX_ROWS = 500
MAX_PAYLOAD_BYTES = 256 * 1024
MAX_HTTP_ALLOWED = 4  # login, one market-data Search, at most two SDK auth internals
MAX_DAY = date(2026, 10, 30)
SHA_PRODUCT = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
SHA = re.compile(r"[a-f0-9]{64}\Z")
SYMBOL = re.compile(r"[A-Z0-9./_-]{1,40}\Z")
SETTLEMENT = re.compile(r"[A-Z0-9-]{1,25}\Z")
PPI_TYPE = {
    "ACCIONES": "ACCIONES", "CEDEARS": "CEDEARS", "ETFS": "ETF",
    "BONOS": "BONOS", "LETRAS": "LETRAS", "OBLIGACIONES": "ON",
    "OPCIONES": "OPCIONES", "FUTUROS": "FUTUROS",
    "CAUCIONES": "CAUCIONES", "FCI": "FCI",
}
MARKETS = frozenset({"BYMA", "BCBA", "ROFEX", "A3", "NYSE", "NASDAQ", "AMEX"})
CURRENCIES = frozenset({"ARS", "USD"})


class _NoOutput:
    """Discard SDK progress/errors without persisting response or credentials."""
    def write(self, chunk):
        return len(chunk)

    def flush(self):
        return None

    def isatty(self):
        return False


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def preflight(encoded, from_day, to_day):
    """Fail before login for any ambiguous, broad, unsupported ART request."""
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 1024:
        raise ValueError("A331_EXACT_TARGET_BUDGET_EXCEEDED")
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_TARGET_BYTES:
            raise ValueError("A331_EXACT_TARGET_BUDGET_EXCEEDED")
        target = json.loads(data)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("A331_EXACT_TARGET_MALFORMED") from None
    if not isinstance(target, list) or len(target) != 1 or not isinstance(target[0], list):
        raise ValueError("A331_EXACT_ONE_TARGET_REQUIRED")
    fields = target[0]
    if len(fields) != 5 or any(not isinstance(item, str) for item in fields):
        raise ValueError("A331_FULL_FIVE_FIELD_IDENTITY_REQUIRED")
    symbol, family, market, currency, settlement = fields
    if (not SYMBOL.fullmatch(symbol) or family not in PPI_TYPE
            or market not in MARKETS or currency not in CURRENCIES
            or not SETTLEMENT.fullmatch(settlement)):
        raise ValueError("A331_PPI_EXACT_IDENTITY_INVALID")
    try:
        one = date.fromisoformat(from_day)
        two = date.fromisoformat(to_day)
    except (ValueError, TypeError):
        raise ValueError("A331_ART_DAY_INVALID") from None
    if one != two or one > MAX_DAY or one < date(2020, 1, 1):
        raise ValueError("A331_ONE_HISTORICAL_ART_DAY_REQUIRED")
    return tuple(fields), one


def _date_utc(raw):
    if not isinstance(raw, str) or len(raw) > 64:
        raise ValueError("A331_PPI_ROW_CLOCK_UNVERIFIED")
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("A331_PPI_ROW_CLOCK_UNVERIFIED") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("A331_PPI_ROW_CLOCK_UNVERIFIED")
    return timestamp.astimezone(timezone.utc)


def _result(target, day):
    return {"schema": SCHEMA, "source": "PPI",
            "requested_identity": list(target), "ppi_instrument_type": PPI_TYPE[target[1]],
            "art_day": day.isoformat(), "state": "NO_VERIFICADO",
            "reason": "NO_PROVIDER_CALL", "expected_product_sha": SHA_PRODUCT,
            "credential_location": "EXISTING_PRODUCT_CONTAINER_ENV_ONLY",
            "provider_requests": 0, "history_invocations": 0, "rows_in_day": 0,
            "total_provider_rows": 0, "canonical_payload_sha256": None,
            "canonical_payload_bytes": 0, "first_known_at_utc": None,
            "source_knowledge": "RETROSPECTIVE_COUNTERFACTUAL_ONLY",
            "causal_pit_rows_verified": 0, "paper_sim_rows_verified": 0,
            "paper_mode": "PRODUCTION_PAPER", "paper_mode_evidence": "PINNED_CODE_ONLY",
            "real_order_capability": "BLOCKED", "real_orders_scope": "THIS_A331_ACTION_ONLY",
            "real_orders_sent": 0, "host_db_writes": 0, "history_v2_writes": 0,
            "read_only_transport": "PPI_MARKETDATA_SEARCH_GET_AUTH_ONLY",
            "row_identity": "REQUEST_ONLY_NOT_IN_HISTORY_ROWS",
            "row_settlement": "REQUEST_ONLY_NOT_IN_HISTORY_ROWS",
            "row_currency": "NOT_IN_PPI_HISTORY_RESPONSE",
            "row_price_basis": "NOT_IN_PPI_HISTORY_RESPONSE",
            "row_volume_kind": "NOT_IN_PPI_HISTORY_RESPONSE",
            "ready_implication": "NONE", "execution_allowed": False,
            "raw_provider_payload_persisted": False}


PUBLIC_REASON_CODES = frozenset({
    "EXISTING_CONTAINER_CREDENTIALS_ABSENT", "RETROSPECTIVE_HISTORY_ROW_DIGEST_ONLY",
    "EMPTY_EXACT_ART_DAY", "A331_PROVIDER_PAYLOAD_BUDGET_EXCEEDED",
    "A331_PROVIDER_ROW_BUDGET_OR_SHAPE_INVALID", "A331_PROVIDER_ROW_SHAPE_INVALID",
    "A331_PPI_ROW_CLOCK_UNVERIFIED", "A331_OBSERVATION_CLOCK_UNVERIFIED",
    "A331_PROVIDER_ROWS_OUTSIDE_REQUESTED_DAY",
    "PPI_READONLY_AUTH_OR_SOURCE_FAILURE", "PPI_READONLY_GUARD_CLOSE_FAILED",
    "PPI_HTTP_ROUTE_BUDGET_OR_GUARD_UNVERIFIED",
    "PPI_EXACT_HISTORY_GET_ROUTE_NOT_ATTESTED",
})


def verify_sanitized_receipt(result, expected_identity, expected_day):
    """Prevent a compromised host stdout from reaching a public artifact.

    Exact schema/keys, fixed codes, bounded numbers and expected identity only.
    Never upload unknown metadata or free-form provider/SDK response text.
    """
    expected = _result(expected_identity, expected_day)
    if not isinstance(result, dict) or set(result) != set(expected):
        return False
    fixed = ("schema", "source", "requested_identity", "ppi_instrument_type",
             "art_day", "expected_product_sha", "credential_location", "source_knowledge",
             "causal_pit_rows_verified", "paper_sim_rows_verified", "paper_mode",
             "paper_mode_evidence", "real_order_capability", "real_orders_scope",
             "real_orders_sent", "host_db_writes",
             "history_v2_writes", "read_only_transport", "row_currency",
             "row_price_basis", "row_identity", "row_settlement", "row_volume_kind",
             "ready_implication", "execution_allowed",
             "raw_provider_payload_persisted")
    if any(result.get(key) != expected[key] for key in fixed):
        return False
    if result.get("reason") not in PUBLIC_REASON_CODES:
        return False
    if result.get("state") not in {"EVIDENCE_ONLY_NO_GO", "NO_VERIFICADO"}:
        return False
    for key, limit in (("provider_requests", MAX_HTTP_ALLOWED),
                       ("history_invocations", 1), ("rows_in_day", MAX_ROWS),
                       ("total_provider_rows", MAX_ROWS),
                       ("canonical_payload_bytes", MAX_PAYLOAD_BYTES)):
        value = result.get(key)
        if type(value) is not int or not 0 <= value <= limit:
            return False
    if result["rows_in_day"] > result["total_provider_rows"]:
        return False
    digest = result.get("canonical_payload_sha256")
    if digest is not None and (not isinstance(digest, str) or not SHA.fullmatch(digest)):
        return False
    instant = result.get("first_known_at_utc")
    if instant is not None:
        try:
            at = _date_utc(instant)
            if not datetime.combine(expected_day, time.min, tzinfo=ART).astimezone(
                    timezone.utc) <= at <= datetime.now(timezone.utc):
                return False
        except (TypeError, ValueError):
            return False
    if result["state"] == "EVIDENCE_ONLY_NO_GO":
        if (result["reason"] != "RETROSPECTIVE_HISTORY_ROW_DIGEST_ONLY"
                or not result["rows_in_day"] or result["history_invocations"] != 1
                or result["provider_requests"] < 2 or not digest
                or not result["canonical_payload_bytes"] or instant is None):
            return False
    elif result["reason"] == "RETROSPECTIVE_HISTORY_ROW_DIGEST_ONLY":
        return False
    return True


def _metrics(reader):
    try:
        data = reader.metrics
        return {key: value for key in ("http_allowed", "http_blocked", "login_calls")
                if type((value := data.get(key))) is int}
    except (AttributeError, TypeError, ValueError):
        return {}


def capture_one(target, day, credentials, reader_factory, *, clock=None):
    """One History call in memory; final response may only contain hash/counts."""
    result = _result(target, day)
    if (not isinstance(credentials, tuple) or len(credentials) != 2
            or any(not isinstance(v, str) or not v for v in credentials)):
        result["reason"] = "EXISTING_CONTAINER_CREDENTIALS_ABSENT"
        return result
    observe = clock or (lambda: datetime.now(timezone.utc))
    reader = None
    http_audit = []
    def on_readonly_http(method, path, action):
        # Product guard emits method/path only, never URL/body/headers. A
        # suspicious additional route fails the final receipt closed.
        if len(http_audit) < 16:
            http_audit.append((method, path, action))
        else:
            http_audit.append(("OVER_BUDGET", "", "BLOCKED"))
    try:
        # budget=False disables automatic process-global budget allocation; a
        # one-shot History GET is not admitted as a live priority book consumer.
        with redirect_stdout(_NoOutput()), redirect_stderr(_NoOutput()):
            reader = reader_factory(credentials[0], credentials[1], budget=False,
                                    consumer="RC6_A331_HOST_HISTORY_ONESHOT",
                                    priority="DISCOVERY", audit=on_readonly_http)
            reader.login_once()
            start = datetime.combine(day, time.min, tzinfo=ART)
            end = datetime.combine(day, time.max, tzinfo=ART)
            result["history_invocations"] = 1
            payload = reader.history(target[0], PPI_TYPE[target[1]], target[4], start, end)
        observed = observe()
        if not isinstance(observed, datetime) or observed.tzinfo is None:
            raise ValueError("A331_OBSERVATION_CLOCK_UNVERIFIED")
        raw = _canonical(payload)
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ValueError("A331_PROVIDER_PAYLOAD_BUDGET_EXCEEDED")
        if not isinstance(payload, list) or len(payload) > MAX_ROWS:
            raise ValueError("A331_PROVIDER_ROW_BUDGET_OR_SHAPE_INVALID")
        match = 0
        outside = 0
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("A331_PROVIDER_ROW_SHAPE_INVALID")
            instant = _date_utc(row.get("date"))
            if instant.astimezone(ART).date() == day:
                match += 1
            else:
                outside += 1
        if outside:
            raise ValueError("A331_PROVIDER_ROWS_OUTSIDE_REQUESTED_DAY")
        result["total_provider_rows"] = len(payload)
        result["rows_in_day"] = match
        result["canonical_payload_sha256"] = sha256(raw).hexdigest()
        result["canonical_payload_bytes"] = len(raw)
        result["first_known_at_utc"] = observed.astimezone(timezone.utc).isoformat()
        result["state"] = "EVIDENCE_ONLY_NO_GO" if match else "NO_VERIFICADO"
        result["reason"] = "RETROSPECTIVE_HISTORY_ROW_DIGEST_ONLY" if match else "EMPTY_EXACT_ART_DAY"
    except Exception as error:
        # Never serialize `str(error)`: brokers/SDKs may echo auth headers,
        # payloads, URLs, secrets or account data in exception messages.
        result["state"] = "NO_VERIFICADO"
        result["reason"] = (str(error) if str(error) in {
            "A331_PROVIDER_PAYLOAD_BUDGET_EXCEEDED", "A331_PROVIDER_ROW_BUDGET_OR_SHAPE_INVALID",
            "A331_PROVIDER_ROW_SHAPE_INVALID", "A331_PPI_ROW_CLOCK_UNVERIFIED",
            "A331_OBSERVATION_CLOCK_UNVERIFIED",
            "A331_PROVIDER_ROWS_OUTSIDE_REQUESTED_DAY"} else "PPI_READONLY_AUTH_OR_SOURCE_FAILURE")
        result["canonical_payload_sha256"] = None
        result["canonical_payload_bytes"] = 0
        result["first_known_at_utc"] = None
        result["rows_in_day"] = 0
        result["total_provider_rows"] = 0
    finally:
        if reader is not None:
            result["provider_requests"] = _metrics(reader).get("http_allowed", 0)
            blocked = _metrics(reader).get("http_blocked", 0)
            try:
                with redirect_stdout(_NoOutput()), redirect_stderr(_NoOutput()):
                    reader.close()
            except Exception:
                result["state"] = "NO_VERIFICADO"
                result["reason"] = "PPI_READONLY_GUARD_CLOSE_FAILED"
            if (blocked != 0 or result["provider_requests"] > MAX_HTTP_ALLOWED
                    or result["provider_requests"] < 2 and result["history_invocations"] == 1):
                result["state"] = "NO_VERIFICADO"
                result["reason"] = "PPI_HTTP_ROUTE_BUDGET_OR_GUARD_UNVERIFIED"
            allowed_paths = {("POST", "/api/1.0/account/loginapi", "ALLOWED"),
                             ("POST", "/api/1.0/account/refreshtoken", "ALLOWED"),
                             ("GET", "/api/1.0/marketdata/search", "ALLOWED")}
            actual_searches = sum(event == ("GET", "/api/1.0/marketdata/search", "ALLOWED")
                                  for event in http_audit)
            if (any(event not in allowed_paths for event in http_audit)
                    or actual_searches != (1 if result["history_invocations"] else 0)
                    or sum(event == ("POST", "/api/1.0/account/loginapi", "ALLOWED")
                           for event in http_audit) > 1):
                result["state"] = "NO_VERIFICADO"
                result["reason"] = "PPI_EXACT_HISTORY_GET_ROUTE_NOT_ATTESTED"
    return result


def _read_only_process_limits():
    os.umask(0o077)
    # Host Python may not create files, core dumps or bytecode. Pipes/stdout
    # do not consume RLIMIT_FSIZE. Network GET is guarded by product allowlist.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def main():
    _read_only_process_limits()
    try:
        from _version import MODE, REAL_ORDER_CAPABILITY
        if MODE != "PRODUCTION_PAPER" or REAL_ORDER_CAPABILITY != "BLOCKED":
            raise ValueError("A331_PRODUCT_PAPER_SAFETY_MISMATCH")
        expected = os.environ.get("RC6_A331_EXPECTED_PRODUCT_SHA")
        if expected != SHA_PRODUCT:
            raise ValueError("A331_PRODUCT_PIN_MISMATCH")
        encoded = os.environ.get("RC6_A331_TARGET_B64")
        first = os.environ.get("RC6_A331_FROM_DAY")
        last = os.environ.get("RC6_A331_TO_DAY")
        target, day = preflight(encoded, first, last)
        key = os.getenv("PPI_API_KEY")
        secret = os.getenv("PPI_API_SECRET")
        if key and secret:
            from bd_ppi_readonly_guard import ProductionMarketReader
            reader_factory = ProductionMarketReader
        else:
            reader_factory = None
        receipt = capture_one(target, day, (key, secret) if key and secret else None,
                              reader_factory)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0 if receipt["state"] == "EVIDENCE_ONLY_NO_GO" else 2
    except Exception:
        # Preflight failure is intentionally a fixed code, with no raw input.
        print(json.dumps({"schema": SCHEMA, "state": "NO_VERIFICADO",
                          "reason": "A331_PRECHECK_OR_RUNTIME_GUARD_FAILED",
                          "provider_requests": 0, "history_invocations": 0,
                          "real_orders_sent": 0, "host_db_writes": 0,
                          "history_v2_writes": 0, "causal_pit_rows_verified": 0,
                          "paper_sim_rows_verified": 0, "execution_allowed": False},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

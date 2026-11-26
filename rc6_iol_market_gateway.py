"""Read-only IOL market RPCs over the existing RC6 local peer transport."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
import os
import re
from secrets import token_hex

from bs_instrument_contracts import aware_datetime
from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly, SYMBOL
from iol_shadow_collector_rc6 import CircuitOpenError, RateGovernor
from rc6_iol_market_data import IOLMarketReader, MARKETS, SPOT, TERMS, read_iol_market_payload
from rc6_ppi_shared_feed import (
    NATIVE_RPC_WAIT_SECONDS, ReadOnlyUnixFeedClient, ReadOnlyUnixFeedServer, _canonical_bytes, _sha,
)


CONTROL_SCHEMA = "rc6.iol.market-gateway-control.v1"
DATA_SCHEMA = "rc6.iol.market-gateway-receipt.v1"
ROLE_PRIORITIES = {
    "scanner": frozenset({"DISCOVERY", "WARM", "HOT", "STRATEGY_HOT", "OPENED_CRITICAL"}),
    "exit_reader": frozenset({"EXIT_CRITICAL"}),
}
ROLE_ENDPOINTS = {"scanner": frozenset({"asset", "quote", "depth"}),
                  "exit_reader": frozenset({"asset", "depth"})}
REQUEST_FIELDS = {"op", "consumer", "endpoint", "identity", "priority", "session_generation"}
_REASON = re.compile(r"IOL_[A-Z0-9_]{1,100}\Z")


def _identity(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 5
            or any(type(part) is not str for part in value)):
        raise ValueError("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED")
    symbol, family, market, currency, term = value
    if (not SYMBOL.fullmatch(symbol) or family not in SPOT or market not in MARKETS
            or currency not in {"ARS", "USD"} or term not in TERMS):
        raise ValueError("IOL_GATEWAY_EXACT_IDENTITY_UNSUPPORTED")
    return tuple(value)


class IOLMarketGateway(ReadOnlyUnixFeedServer):
    """Keep the REST session and budget in the owner, one endpoint per demand."""

    control_schema = CONTROL_SCHEMA

    def __init__(self, rest, *, allowed, socket_path, supervisor_pid, clock=None):
        if (not isinstance(rest, IOLRestReadOnly) or not isinstance(rest.governor, RateGovernor)
                or rest.governor.max_wait_seconds is None
                or rest.governor.max_wait_seconds >= NATIVE_RPC_WAIT_SECONDS):
            raise ValueError("IOL_GATEWAY_BOUNDED_WIRE_BUDGET_REQUIRED")
        rest.governor.policy.validate()
        if set(allowed) != set(ROLE_PRIORITIES):
            raise ValueError("IOL_GATEWAY_ROLE_SCOPE_REQUIRED")
        self.allowed = {role: frozenset(_identity(key) for key in keys) for role, keys in allowed.items()}
        self._rest = rest
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._generation = token_hex(32)
        super().__init__(None, socket_path=socket_path, supervisor_pid=supervisor_pid,
                         native_acquirer=self._capture, owner_status=self._status)

    def _status(self):
        return {"state": "OPEN", "provider": "IOL_REST", "wire_budget_attached": True,
                "owner_pid": os.getpid(), "session_generation": self._generation}

    def _response(self, state, *, wire_calls=0, **fields):
        return {"schema": DATA_SCHEMA, **self._status(), "state": state,
                "wire_calls": wire_calls, "paper_ready": False, "order_route": "NOT_PRESENT", **fields}

    def _capture(self, *, endpoint, identity, priority, consumer, peer_pid):
        if not self._role_permitted(consumer, peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        governor = self._rest.governor
        before = governor.calls_total
        try:
            payload = read_iol_market_payload(self._rest, endpoint, identity)
            received_at = aware_datetime(self._clock()).astimezone(timezone.utc).isoformat()
            body = {"endpoint": endpoint, "identity": list(identity),
                    "received_at": received_at, "payload": payload}
            if not isinstance(payload, dict) or len(_canonical_bytes(body)) > 120_000:
                raise ValueError("IOL_GATEWAY_MARKET_PAYLOAD_INVALID")
            proof = {"provider": "IOL_REST", "session_generation": self._generation, **body}
            return self._response("OK", wire_calls=governor.calls_total-before,
                                  receipt_sha256=_sha(proof), **body)
        except CircuitOpenError:
            return self._response("IOL_GATEWAY_RATE_HOLD", wire_calls=governor.calls_total-before)
        except IOLRestError as exc:
            state = str(exc)
            status = exc.status_code if type(exc.status_code) is int else None
            retry = exc.retry_after if type(exc.retry_after) is int and exc.retry_after >= 0 else None
            return self._response(state if _REASON.fullmatch(state) else "IOL_GATEWAY_READ_FAILED",
                                  wire_calls=governor.calls_total-before, http_status=status,
                                  retry_after_seconds=retry)
        except (ValueError, TypeError, OSError):
            return self._response("IOL_GATEWAY_MARKET_PAYLOAD_INVALID",
                                  wire_calls=governor.calls_total-before)

    def dispatch(self, request, *, peer_pid=None):
        if not isinstance(request, dict):
            return self._response("IOL_GATEWAY_REQUEST_DENIED")
        operation = request.get("op")
        if operation in ("register_client", "owner_status"):
            if operation == "register_client" and request.get("consumer") not in ("scanner", "exit_reader"):
                return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
            return super().dispatch(request, peer_pid=peer_pid)
        if operation != "iol_market_read" or set(request) != REQUEST_FIELDS:
            return self._response("IOL_GATEWAY_REQUEST_DENIED")
        role, endpoint, priority = (request[k] for k in ("consumer", "endpoint", "priority"))
        if (type(role) is not str or role not in ROLE_PRIORITIES
                or type(endpoint) is not str or type(priority) is not str
                or endpoint not in ROLE_ENDPOINTS[role] or priority not in ROLE_PRIORITIES[role]):
            return self._response("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        if peer_pid is None or not self._role_permitted(role, peer_pid):
            return self._response("IOL_GATEWAY_UNREGISTERED_CLIENT")
        if request["session_generation"] != self._generation:
            return self._response("IOL_GATEWAY_OWNER_CHANGED")
        try:
            identity = _identity(request["identity"])
        except ValueError:
            return self._response("IOL_GATEWAY_EXACT_IDENTITY_REQUIRED")
        if identity not in self.allowed[role]:
            return self._response("IOL_GATEWAY_IDENTITY_SCOPE_DENIED")
        try:
            return self._native_router.demand(endpoint=endpoint, identity=identity, priority=priority,
                                              consumer=role, peer_pid=peer_pid)
        except RuntimeError:
            return self._response("IOL_GATEWAY_SCHEDULER_HOLD", wire_calls="NO_VERIFICADO")
        except TimeoutError:
            return self._response("IOL_GATEWAY_READ_DEADLINE", wire_calls="NO_VERIFICADO")


class IOLGatewayClient(ReadOnlyUnixFeedClient):
    control_schema = CONTROL_SCHEMA

    def _owner_status_matches_provider(self, response):
        return (response.get("provider") == "IOL_REST" and
                response.get("wire_budget_attached") is True)

    def market_read(self, endpoint, identity, *, consumer, priority):
        if self._pinned_owner is None:
            raise IOLRestError("IOL_GATEWAY_OWNER_NOT_PINNED")
        identity = _identity(identity)
        owner_pid, generation = self._pinned_owner
        response = self._request({"op": "iol_market_read", "consumer": consumer,
            "endpoint": endpoint, "identity": list(identity), "priority": priority,
            "session_generation": generation}, require_owner_identity=True)
        if (response.get("schema") != DATA_SCHEMA or response.get("provider") != "IOL_REST"
                or response.get("owner_pid") != owner_pid or response.get("session_generation") != generation
                or response.get("paper_ready") is not False or response.get("order_route") != "NOT_PRESENT"):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        state = response.get("state")
        if state != "OK":
            if (not isinstance(state, str) or not _REASON.fullmatch(state)
                    or "payload" in response or "receipt_sha256" in response):
                raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
            raise IOLRestError(state, status_code=response.get("http_status"),
                               retry_after=response.get("retry_after_seconds"))
        proof = {key: response.get(key) for key in
                 ("provider", "session_generation", "endpoint", "identity", "received_at", "payload")}
        if (proof["endpoint"] != endpoint or proof["identity"] != list(identity)
                or not isinstance(proof["payload"], dict)
                or type(response.get("wire_calls")) is not int or response["wire_calls"] < 1
                or response.get("receipt_sha256") != _sha(proof)):
            raise IOLRestError("IOL_GATEWAY_RECEIPT_UNVERIFIED")
        return proof["payload"], aware_datetime(proof["received_at"])


class IOLGatewayMarketReader:
    """Consumer adapter with zero provider credentials or HTTP client."""

    def __init__(self, client, *, consumer, trade_clock=None, clock=None):
        if consumer not in ROLE_PRIORITIES:
            raise ValueError("IOL_GATEWAY_ROLE_SCOPE_REQUIRED")
        self.client, self.consumer = client, consumer
        self.trade_clock, self.clock = trade_clock, clock

    def observe(self, identity, *, contract=None, priority="DISCOVERY", exit_only=False):
        if exit_only != (self.consumer == "exit_reader"):
            raise IOLRestError("IOL_GATEWAY_ROLE_SCOPE_DENIED")
        capture = partial(self.client.market_read, consumer=self.consumer, priority=priority)
        return IOLMarketReader(None, capture=capture, trade_clock=self.trade_clock,
                               clock=self.clock).observe(identity, contract=contract, exit_only=exit_only)

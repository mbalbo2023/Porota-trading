"""One explicit, fingerprinted contract for native SHADOW Source reads.

Capture and final Source verification each have a bounded I/O phase. Query
budgets start after capture, retain the incumbent consumer limits, and are not
sleeps or additions to an external stress/lifecycle deadline.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import hashlib
import json
import math


SCHEMA = "rc6.source-read-contract.v1"
_ACTIVE_CONTRACT = ContextVar("rc6_source_read_contract", default=None)


@dataclass(frozen=True)
class SourceReadContract:
    # Capture previously consumed every consumer's query allowance. Giving
    # that separate I/O phase one bound does not widen any SQL query budget.
    capture_budget_seconds: float = 1.5
    verification_budget_seconds: float = 1.5
    runtime_query_seconds: float = .5
    metadata_query_seconds: float = .15
    stages_query_seconds: float = .25
    families_query_seconds: float = .5
    lab_query_seconds: float = .25
    entry_signals_query_seconds: float = .25
    funnel_query_seconds: float = .25
    preopen_query_seconds: float = 2.0
    maximum_source_bytes: int = 512 * 1024 * 1024

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "maximum_source_bytes":
                if type(value) is not int or not 0 < value <= 512 * 1024 * 1024:
                    raise ValueError("INVALID_SOURCE_READ_CONTRACT")
            elif (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= 2.0):
                raise ValueError("INVALID_SOURCE_READ_CONTRACT")

    def query_budget_seconds(self, consumer):
        if type(consumer) is not str or consumer not in QUERY_CONSUMERS:
            raise ValueError("UNKNOWN_SOURCE_QUERY_CONSUMER")
        return float(getattr(self, consumer + "_query_seconds"))

    def document(self):
        return {"schema": SCHEMA, **asdict(self),
            "capture_scope": "ONE_PRIMARY_IMAGE_PER_TICK",
            "connection_scope": "PRIVATE_READ_ONLY_PER_CONSUMER",
            "source_validation": "MAIN_WAL_SHM_INVENTORY_AND_SHA256_BEFORE_AFTER",
            "additional_capture": "DISTINCT_SOURCE_IDENTITY_ONLY",
            "publication": "AFTER_SOURCE_VALIDATION_AND_CLEANUP"}

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


QUERY_CONSUMERS = ("runtime", "metadata", "stages", "families", "lab",
                   "entry_signals", "funnel", "preopen")
DEFAULT_READ_CONTRACT = SourceReadContract()


def current_read_contract():
    return _ACTIVE_CONTRACT.get() or DEFAULT_READ_CONTRACT


def query_budget_seconds(consumer):
    """The worker and standalone readers obtain the same productive defaults."""
    return current_read_contract().query_budget_seconds(consumer)


def require_query_budget_binding(consumer, configured):
    """An active productive tick cannot mix a fixture-only SQL allowance."""
    contract = _ACTIVE_CONTRACT.get()
    if contract is not None and configured != contract.query_budget_seconds(consumer):
        raise ValueError("SOURCE_QUERY_CONTRACT_MISMATCH")


@contextmanager
def activate_read_contract(contract):
    if type(contract) is not SourceReadContract:
        raise ValueError("INVALID_SOURCE_READ_CONTRACT")
    if _ACTIVE_CONTRACT.get() is not None:
        raise ValueError("SOURCE_READ_CONTRACT_REENTRY_FORBIDDEN")
    token = _ACTIVE_CONTRACT.set(contract)
    try:
        yield contract
    finally:
        _ACTIVE_CONTRACT.reset(token)

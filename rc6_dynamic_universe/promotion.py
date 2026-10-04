"""Explicit, read-only promotion of measured PPI capacity.

The benchmark produces evidence and recommendations. Only a separately
reviewed configuration can enable selection; neither file grants entry or
concurrency authority. The shipped mode is OFF and preserves the 20/40 path.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path

from .capacity import safe_capacity
from .common import digest, identity, number, stamp

SCHEMA = "RC6_PPI_CAPACITY_RECOMMENDATION_V1"
POLICY_SCHEMA = "RC6_DYNAMIC_CAPACITY_POLICY_V1"
APPROVAL_SCHEMA = "RC6_CAPACITY_APPROVAL_V1"
DEFAULT_POLICY = {
    "schema": POLICY_SCHEMA, "version": "rc6-capacity-runtime-v1",
    "mode": "OFF", "mode_safety": "PAPER_SHADOW_ONLY",
    "max_parallel_requests": 1, "safety_factor": .75,
    "baseline_limits": {"EQUITY_SPOT": 20, "SCALPING": 40},
    "engines": {
        "SCALPING": {"endpoints": ["intraday", "book"], "hot_seconds": 30,
            "warm_seconds": 120, "discovery_seconds": 300,
            "warmup_samples": 15, "window_seconds": 2700, "max_age_seconds": 120},
        "EQUITY_SPOT": {"endpoints": ["current", "book"], "hot_seconds": 120,
            "warm_seconds": 300, "discovery_seconds": 600,
            "warmup_samples": 6, "window_seconds": 5400, "max_age_seconds": 120}},
    "budget": {"critical_book_seconds": 5, "lease_seconds": 60,
        "breaker_seconds": 60, "session_breaker_seconds": 900,
        "server_error_threshold": 2, "maximum_bytes": 8 * 1024**2},
}
POLICY_PATH = Path(__file__).resolve().parents[1] / "ops/policy/rc6-dynamic-capacity-v1.json"


def _integer(value, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("CAPACITY_POLICY_INTEGER_INVALID")
    return value


def validate_policy(value):
    policy = deepcopy(value)
    if (policy.get("schema") != POLICY_SCHEMA or policy.get("mode") not in {"OFF", "SHADOW", "APPROVED"}
            or policy.get("mode_safety") != "PAPER_SHADOW_ONLY"
            or policy.get("max_parallel_requests") != 1
            or policy.get("baseline_limits") != DEFAULT_POLICY["baseline_limits"]):
        raise ValueError("CAPACITY_POLICY_INVALID")
    if not 0 < number(policy["safety_factor"]) <= 1 or not policy.get("version"):
        raise ValueError("CAPACITY_POLICY_INVALID")
    if set(policy["engines"]) != {"SCALPING", "EQUITY_SPOT"}:
        raise ValueError("CAPACITY_ENGINES_INVALID")
    for engine, spec in policy["engines"].items():
        if set(spec["endpoints"]) != set(DEFAULT_POLICY["engines"][engine]["endpoints"]):
            raise ValueError("CAPACITY_ENDPOINT_CONTRACT_INVALID")
        for name in ("hot_seconds", "warm_seconds", "discovery_seconds", "warmup_samples", "window_seconds", "max_age_seconds"):
            _integer(spec[name])
        if not spec["hot_seconds"] < spec["warm_seconds"] <= spec["discovery_seconds"]:
            raise ValueError("CAPACITY_CADENCE_INVALID")
    for name in DEFAULT_POLICY["budget"]:
        _integer(policy["budget"][name])
    if policy["budget"]["lease_seconds"] < 45 or not 65536 <= policy["budget"]["maximum_bytes"] <= 32 * 1024**2:
        raise ValueError("CAPACITY_BUDGET_BOUNDS_INVALID")
    return policy


def runtime_policy_fingerprint(policy):
    # A reviewer can change the feature mode without changing the measured
    # contract. Every scheduling, reserve, breaker and safety input is pinned.
    valid = validate_policy(policy)
    return digest({k: v for k, v in valid.items() if k not in {"mode", "approved_recommendation_digest"}})


def _sealed(payload, field="recommendation_digest"):
    return payload.get(field) == digest({k: v for k, v in payload.items() if k != field})


def build_recommendation(report, *, policy=None, as_of=None):
    """Create an immutable recommendation; never generate approval/config."""
    policy = validate_policy(policy or DEFAULT_POLICY)
    at = stamp(as_of or report.get("generated_at") or datetime.now(timezone.utc))
    result = {"schema": SCHEMA, "version": policy["version"], "status": "NO_VERIFICADO",
        "mode": "SHADOW", "created_at": at.isoformat(),
        "runtime_policy_fingerprint": runtime_policy_fingerprint(policy),
        "evidence_digest": report.get("evidence_digest"),
        "benchmark_configuration_fingerprint": report.get("configuration_fingerprint"),
        "engines": {}, "global_budget": {}, "reason_codes": [],
        "max_parallel_requests": 1, "automatic_activation": False,
        "production_limit_modified": False, "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    for engine, spec in policy["engines"].items():
        cap = safe_capacity(report, endpoints=spec["endpoints"], cadence_seconds=spec["hot_seconds"],
            window_seconds=spec["window_seconds"], required_samples=spec["warmup_samples"],
            latency_budget_seconds=spec["hot_seconds"], safety_factor=policy["safety_factor"], as_of=at)
        result["engines"][engine] = {"profile": spec, "capacity": cap}
        result["reason_codes"].extend(cap["reason_codes"])
    quantum = min(s["hot_seconds"] for s in policy["engines"].values())
    shared = safe_capacity(report, endpoints=("current", "book", "intraday"), cadence_seconds=quantum,
        window_seconds=min(s["window_seconds"] for s in policy["engines"].values()),
        required_samples=1, latency_budget_seconds=quantum, safety_factor=policy["safety_factor"], as_of=at)
    result["reason_codes"].extend(shared["reason_codes"])
    if all(e["capacity"]["status"] == "SHADOW_RECOMMENDATION" for e in result["engines"].values()) and shared["status"] == "SHADOW_RECOMMENDATION":
        slots = shared["slots_by_endpoint"]
        result["global_budget"] = {"window_seconds": quantum, "endpoint_limits": slots,
            "global_limit": min(sum(slots.values()), shared["shared_global_slots"] * len(slots)),
            "safety_factor": policy["safety_factor"],
            "safety_reserve": "WITHHELD_BY_MEASURED_SAFETY_FACTOR; not spendable",
            "measured_endpoint_mix": ["current", "book", "intraday"],
            "max_parallel_requests": 1, "capacity_configuration_fingerprint": shared["configuration_fingerprint"]}
        result["status"] = "SHADOW_RECOMMENDATION"
        result["expires_at"] = min([shared["expires_at"]] + [e["capacity"]["expires_at"] for e in result["engines"].values()])
    result["reason_codes"] = list(dict.fromkeys(result["reason_codes"]))
    result["recommendation_digest"] = digest(result)
    return result


def resolve_capacity_policy(policy, recommendation=None, report=None, approval=None, *, as_of=None):
    """Revalidate at consumption time, including the independently pinned hash."""
    at = stamp(as_of or datetime.now(timezone.utc))
    result = {"status": "BASELINE_FAIL_CLOSED", "mode": "OFF", "reason_codes": [],
        "baseline_limits": deepcopy(DEFAULT_POLICY["baseline_limits"]), "engine_profiles": {},
        "production_limits_modified": False, "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "configuration_fingerprint": None, "recommendation_digest": None}
    try:
        policy = validate_policy(policy)
        result["configuration_fingerprint"] = runtime_policy_fingerprint(policy)
        result["mode"] = policy["mode"]
        if policy["mode"] == "OFF":
            return result | {"status": "OFF_BASELINE"}
        if policy["mode"] == "SHADOW":
            return result | {"status": "SHADOW_BASELINE", "reason_codes": ["EXPLICIT_APPROVAL_REQUIRED"]}
        if not recommendation or not report or not approval:
            raise ValueError("CAPACITY_APPROVAL_OR_EVIDENCE_MISSING")
        if recommendation.get("schema") != SCHEMA or not _sealed(recommendation):
            raise ValueError("CAPACITY_RECOMMENDATION_DIGEST_INVALID")
        if recommendation.get("runtime_policy_fingerprint") != result["configuration_fingerprint"]:
            raise ValueError("CAPACITY_RUNTIME_FINGERPRINT_MISMATCH")
        created = stamp(recommendation["created_at"])
        if not created <= at < stamp(recommendation["expires_at"]):
            raise ValueError("CAPACITY_RECOMMENDATION_STALE_OR_FUTURE")
        expected = build_recommendation(report, policy=policy, as_of=created)
        if expected != recommendation or expected["status"] != "SHADOW_RECOMMENDATION":
            raise ValueError("CAPACITY_RECOMMENDATION_NOT_REPRODUCIBLE")
        current = build_recommendation(report, policy=policy, as_of=at)
        if current["status"] != "SHADOW_RECOMMENDATION":
            raise ValueError("CAPACITY_OPEN_EVIDENCE_NOT_CURRENT")
        if (approval.get("schema") != APPROVAL_SCHEMA or approval.get("approved") is not True
                or not isinstance(approval.get("reviewer"), str) or not approval["reviewer"].strip()
                or not _sealed(approval, "approval_digest")
                or approval.get("recommendation_digest") != recommendation["recommendation_digest"]
                or approval.get("runtime_policy_fingerprint") != result["configuration_fingerprint"]
                or policy.get("approved_recommendation_digest") != recommendation["recommendation_digest"]):
            raise ValueError("CAPACITY_EXPLICIT_APPROVAL_INVALID")
        if not created <= stamp(approval["reviewed_at"]) <= at:
            raise ValueError("CAPACITY_APPROVAL_CLOCK_INVALID")
        # The configured digest is a review control, not part of the measured
        # profile fingerprint; otherwise approving a digest would recurse.
        return result | {"status": "APPROVED_DYNAMIC", "reason_codes": [],
            "recommendation_digest": recommendation["recommendation_digest"],
            "engine_profiles": deepcopy(recommendation["engines"]),
            "global_budget": deepcopy(recommendation["global_budget"]),
            "expires_at": recommendation["expires_at"], "budget_settings": deepcopy(policy["budget"]),
            "production_limits_modified": True}
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        code = str(error)
        return result | {"reason_codes": [code if code.startswith("CAPACITY_") and len(code) < 100 else "CAPACITY_CONFIGURATION_INVALID"]}


def read_json(path, *, compressed=False, limit=8 * 1024**2):
    """Bounded trusted input: reject aliases and never follow a temp file."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > limit:
        raise ValueError("CAPACITY_INPUT_UNAVAILABLE_OR_ALIAS")
    opener = gzip.open if compressed else open
    with opener(path, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("CAPACITY_INPUT_TOO_LARGE")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("CAPACITY_INPUT_NOT_OBJECT")
    return value


class RuntimeCapacityController:
    """Config-only runtime interface; reading it never creates or edits files."""
    def __init__(self, database=None, *, environ=None, policy=None, report=None, recommendation=None,
                 approval=None, shadow=None):
        self.database = Path(database) if database is not None else None
        self.environ = os.environ if environ is None else environ
        self.inputs = dict(policy=policy, report=report, recommendation=recommendation, approval=approval)
        self.shadow = shadow

    @property
    def input_paths(self):
        paths = [self.environ.get("POROTA_CAPACITY_POLICY_PATH", str(POLICY_PATH))]
        paths += [v for k, v in self.environ.items() if k.startswith("POROTA_CAPACITY_") and k.endswith("_PATH")]
        return list(dict.fromkeys(Path(p).absolute() for p in paths if p))

    @property
    def protected_paths(self):
        paths = self.input_paths
        if self.database is not None:
            from cg_paper_workspace import artifact_root
            paths += [artifact_root(self.database) / "ppi-budget/global.sqlite"]
        return paths

    @staticmethod
    def _fingerprinted(state):
        state["fingerprint"] = digest({k: state.get(k) for k in (
            "status", "mode", "configuration_fingerprint", "recommendation_digest", "approval_digest", "reason_codes")})
        return state

    def state(self, as_of=None):
        try:
            policy = self.inputs["policy"]
            if policy is None:
                policy = read_json(self.environ.get("POROTA_CAPACITY_POLICY_PATH", str(POLICY_PATH)))
            policy = deepcopy(policy)
            # Runtime feature configuration is explicit. No benchmark can edit it.
            policy["mode"] = self.environ.get("POROTA_DYNAMIC_CAPACITY_MODE", policy["mode"]).upper()
            if policy["mode"] in {"OFF", "SHADOW"}:
                return self._fingerprinted(resolve_capacity_policy(policy, as_of=as_of))
            values = {}
            for key in ("report", "recommendation", "approval"):
                value = self.inputs[key]
                if value is None:
                    path = self.environ.get("POROTA_CAPACITY_" + key.upper() + "_PATH")
                    value = read_json(path) if path else None
                values[key] = value
            resolved = resolve_capacity_policy(policy, **values, as_of=as_of)
            resolved["approval_digest"] = (values.get("approval") or {}).get("approval_digest")
            return self._fingerprinted(resolved)
        except (OSError, ValueError, KeyError, TypeError):
            return self._fingerprinted({"status": "BASELINE_FAIL_CLOSED", "mode": "OFF", "configuration_fingerprint": None,
                "reason_codes": ["CAPACITY_INPUT_UNAVAILABLE_OR_INVALID"], "engine_profiles": {},
                "baseline_limits": deepcopy(DEFAULT_POLICY["baseline_limits"]),
                "production_limits_modified": False, "real_orders_sent": 0, "real_routes": "NOT_CALLED"})

    def shadow_report(self):
        if self.shadow is not None:
            return self.shadow
        path = self.environ.get("POROTA_CAPACITY_SHADOW_PATH")
        if not path and self.database is not None:
            from cg_paper_workspace import artifact_root
            path = artifact_root(self.database) / "dynamic-shadow/latest.json.gz"
        value = read_json(path, compressed=str(path).endswith(".gz"), limit=64 * 1024**2) if path else {}
        if "payload" in value:
            if value.get("digest") != digest(value["payload"]):
                raise ValueError("CAPACITY_SHADOW_DIGEST_INVALID")
            value = value["payload"]
        return value

    def validated_report(self, as_of=None, *, expected_fingerprint=None):
        """Return the exact raw report pinned by a current approved state."""
        before = self.state(as_of)
        if (before["status"] != "APPROVED_DYNAMIC"
                or expected_fingerprint and before["fingerprint"] != expected_fingerprint):
            raise ValueError("CAPACITY_APPROVED_STATE_CHANGED")
        value = self.inputs["report"]
        if value is None:
            value = read_json(self.environ["POROTA_CAPACITY_REPORT_PATH"])
        expected = before["engine_profiles"]["SCALPING"]["capacity"]["evidence_digest"]
        if (value.get("evidence_digest") != expected
                or value.get("evidence_digest") != digest({k: v for k, v in value.items() if k != "evidence_digest"})
                or self.state(as_of)["fingerprint"] != before["fingerprint"]):
            raise ValueError("CAPACITY_APPROVED_REPORT_CHANGED")
        return value

    def selection(self, engine, baseline, *, as_of=None, opened=(), shadow=None):
        """Select exact full identities; this contains no signal/fill callback."""
        at = stamp(as_of or datetime.now(timezone.utc))
        state = self.state(at)
        fallback = {"status": state["status"], "dynamic": False, "selected": list(baseline),
            "limit": state["baseline_limits"][engine], "entry_identities": [],
            "reason_codes": state["reason_codes"], "policy_state": state}
        if state["status"] != "APPROVED_DYNAMIC":
            return fallback
        try:
            report = shadow if shadow is not None else self.shadow_report()
            profile = state["engine_profiles"][engine]
            spec, cap = profile["profile"], profile["capacity"]
            if (report.get("mode") != "SHADOW" or report.get("real_orders_sent") != 0
                    or report.get("real_routes") != "NOT_CALLED"
                    or not 0 <= (at - stamp(report["as_of"])).total_seconds() <= spec["hot_seconds"]
                    or report.get("capacity_policy", {}).get("configuration_fingerprint") != state["configuration_fingerprint"]):
                raise ValueError("CAPACITY_SHADOW_STATE_STALE_OR_INCOMPATIBLE")
            plan = report["engines"][engine]
            if (plan["capacity"].get("evidence_digest") != cap["evidence_digest"]
                    or plan["capacity"].get("configuration_fingerprint") != cap["configuration_fingerprint"]
                    or not plan.get("preopen_digest") or report.get("phase") != "OPEN"):
                raise ValueError("CAPACITY_SHADOW_CONTRACT_MISMATCH")
            rows = {tuple(r["identity"]): r for r in plan["telemetry"]}
            opened = list(dict.fromkeys(map(tuple, opened)))
            selected = list(dict.fromkeys(opened + list(map(tuple, plan["selected"]))))
            # The planner retains opened priority on overflow. Actual sends
            # still obey the arbiter; overflow never grants unmeasured traffic.
            if any(len(k) != 5 or k not in rows for k in selected):
                raise ValueError("CAPACITY_DYNAMIC_IDENTITY_INVALID")
            selected = sorted(selected, key=lambda k: (k not in opened,
                {"HOT": 0, "WARM": 1, "DISCOVERY": 2}.get(rows[k]["state"], 3),
                -float(rows[k].get("tradeability_score") or 0), k))
            selected = opened + [k for k in selected if k not in opened][:max(0, cap["safe_limit"] - len(opened))]
            entry = [k for k in selected if rows[k]["state"] == "HOT"
                and rows[k].get("signal_result") == "SAMPLES_READY"
                and rows[k].get("pipeline", {}).get("TRADEABLE") is True]
            return fallback | {"status": "APPROVED_DYNAMIC", "dynamic": True, "selected": selected,
                "limit": cap["safe_limit"], "cadence_seconds": spec["hot_seconds"],
                "entry_identities": entry, "rows": rows, "reason_codes": [],
                "opened_priority": opened,
                "deep_priority_authority": "STRATEGY_TRADEABILITY_CAPACITY", "family_quota": False}
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
            return fallback | {"status": "BASELINE_FAIL_CLOSED", "reason_codes": [
                str(error) if str(error).startswith("CAPACITY_") else "CAPACITY_SHADOW_INPUT_INVALID"]}


def capacity_controller_from_environment(database=None):
    return RuntimeCapacityController(database)


def runtime_capacity_state(database=None, *, as_of=None):
    return capacity_controller_from_environment(database).state(as_of)

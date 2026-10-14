"""Native spot clocks and frozen source provenance; no trading authority."""
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, is_dataclass
from functools import wraps
from pathlib import Path

from .common import digest, stamp

_cache = {}


def frozen_source(root=None, metadata=None):
    """Verify the canonical manifest's entire Python closure before naming a SHA.

    Deployment installs these existing attestations in the mounted data volume.
    Unknown or old metadata never falls back to an environment SHA. Verification
    runs before signal evaluation, outside the ledger write transaction.
    """
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    metadata = Path(metadata or root / "data/deploy")
    key = (str(root), str(metadata))
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return dict(cached[1])
    result = {"git_sha": None, "source_status": "NO_VERIFICADO"}
    try:
        if (metadata / "porota-deploy-bundle-v2-manifest.json").stat().st_size > 4 * 1024 * 1024:
            raise ValueError("MANIFEST_SIZE_LIMIT")
        frozen = json.loads((metadata / "porota-frozen-candidate.json").read_text())
        manifest = json.loads((metadata / "porota-deploy-bundle-v2-manifest.json").read_text())
        sha = frozen["candidate_sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or manifest["status"] != "GREEN":
            raise ValueError("INVALID_FROZEN_ATTESTATION")
        files = manifest["files"]
        if len(files) != manifest["file_count"] or not 1 <= len(files) <= 10000:
            raise ValueError("INVALID_MANIFEST_CLOSURE")
        paths = set()
        verified = 0
        for record in files:
            rel = Path(record["path"])
            if rel.is_absolute() or ".." in rel.parts or str(rel) in paths:
                raise ValueError("INVALID_MANIFEST_PATH")
            paths.add(str(rel))
            if rel.suffix != ".py":
                continue
            path = (root / rel).resolve()
            if not path.is_relative_to(root):
                raise ValueError("INVALID_MANIFEST_PATH")
            data = path.read_bytes()
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("RUNTIME_SOURCE_MISMATCH")
            verified += 1
        # Every application Python source must belong to the canonical closure.
        # Runtime caches, mounted data, tooling environments and tests are not
        # executable application source in this attestation scope.
        actual = set()
        ignored = {".git", ".github", ".venv", "venv", "data", "tests", "docs", ".agents", "__pycache__", "model_cache", "sre_vector_db", ".cache"}
        for directory, folders, names in os.walk(root):
            folders[:] = [name for name in folders if name not in ignored]
            actual.update(str((Path(directory) / name).relative_to(root)) for name in names if name.endswith(".py"))
            if len(actual) > 10000:
                raise ValueError("SOURCE_CLOSURE_SIZE_LIMIT")
        declared = {p for p in paths if p.endswith(".py")}
        if not verified or not actual <= declared or "be_paper_engine.py" not in declared:
            raise ValueError("RUNTIME_SOURCE_CLOSURE_MISSING")
        result = {"git_sha": sha, "source_status": "FROZEN_CANDIDATE_PYTHON_CLOSURE_VERIFIED",
                  "candidate_tree_sha": frozen.get("candidate_tree_sha"),
                  "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                  "verified_python_files": verified}
    except (OSError, ValueError, TypeError, KeyError):
        pass
    _cache[key] = (time.monotonic(), result)
    return dict(result)


def resolved_configuration(broker):
    """Fingerprint resolved constructor settings plus private environment inputs.

    Only the hash leaves this function. Callback internals are explicitly opaque;
    this fingerprint certifies the spot engine configuration, not AI internals.
    """
    names = ("initial_balances", "risk_pct", "max_positions", "fee_rate", "slippage",
             "participation", "max_position_pct", "max_total_exposure_pct", "require_ai",
             "require_supervisor", "quote_max_age_seconds", "trade_max_age_seconds",
             "signal_min_samples", "signal_window_minutes", "score_threshold", "ai_mode",
             "economics_mode", "min_net_reward_risk", "stop_loss_pct", "target_gain_pct",
             "intraday_fee_rebate")
    import au_fee_schedule as fees
    config = {name: getattr(broker, name) for name in names}
    config["tariff"] = {family: asdict(fees.arancel_de(family)) for family in
                        ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES")}
    config["vat"] = fees.IVA_PCT
    policy = broker.session_policy
    config["session_policy"] = asdict(policy) if is_dataclass(policy) else {
        "class": type(policy).__qualname__, "settings": getattr(policy, "__dict__", {})}
    config["daily_risk"] = {name: getattr(broker.daily_risk, name, None) for name in
                            ("limit_pct", "soft_limit_pct", "soft_limit_explicit")}
    # These values remain private; recording their digest avoids disclosing
    # account amounts and catches runtime env overrides used by other guards.
    config["environment"] = {k: v for k, v in os.environ.items()
                             if k.startswith(("PAPER_", "POROTA_OPERATIONAL_"))}
    config["callbacks"] = {name: type(getattr(broker, name)).__qualname__
                           for name in ("ai_gate", "context_fn")}
    safe = json.loads(json.dumps(config, sort_keys=True, default=str))
    return {"configuration_fingerprint": digest(safe),
            "configuration_scope": "RESOLVED_SPOT_ENGINE+PAPER_ENV+TARIFF+SESSION+DAILY_RISK",
            "opaque_configuration": ["AI_MODEL_INTERNALS", "CALLBACK_INTERNALS"]}


def native_clock(broker):
    # A direct historical call without a native clock is event-time simulation.
    if broker.clock_fn is None:
        return None
    return stamp(broker.clock_fn()).isoformat(timespec="microseconds")


def trace_spot_signal(function):
    @wraps(function)
    def traced(broker, q):
        if str(q.asset_class).upper() == "FUTUROS":
            return function(broker, q)
        context = {**frozen_source(), **resolved_configuration(broker),
                   "strategy_id": "SPOT_MOMENTUM_BASELINE",
                   "signal_started_at": native_clock(broker)}
        action, score, reason, features = function(broker, q)
        context["signal_at"] = native_clock(broker)
        context["clock_mode"] = "NATIVE" if broker.clock_fn else "EVENT_TIME_SIMULATION_UNVERIFIED"
        features["performance_lineage"] = context
        features.setdefault("reason_code", "BASELINE_BUY" if action == "BUY" else "PRE_SIGNAL_OR_DATA_HOLD_UNMAPPED")
        return action, score, reason, features
    return traced


def mark(features, stage, broker):
    lineage = features.get("performance_lineage")
    if lineage is not None:
        lineage[stage + "_at"] = native_clock(broker)

"""Read-only, identity-bound Contract Evidence v2 -> existing PAPER contract.

Does not infer order increments from quote bases or create executors. Dynamic
quotes, costs, session and risk stay with the PAPER engine. Unsupported lifecycle
families remain evidence-only. No network, broker client or second DB writer.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from bs_instrument_contracts import contract_from_metadata
from cp_contract_evidence_v2_hf6 import SOURCE_RANK, evidence_hash
from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement

SUPPORTED = {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"}
MAP = {"cash_multiplier": "cash_multiplier", "quantity_step": "quantity_step",
       "quantity_min": "minimum_quantity", "minimum_quantity": "minimum_quantity",
       "expiry_at": "expires_at", "expires_at": "expires_at", "underlying": "underlying",
       "strike": "strike", "put_call": "option_right", "option_right": "option_right"}
NUMERIC = {"cash_multiplier", "quantity_step", "minimum_quantity", "strike"}


def _value(field, value):
    if field in NUMERIC:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("INVALID_NUMERIC_TERM:" + field) from exc
        if not number.is_finite() or number <= 0:
            raise ValueError("INVALID_NUMERIC_TERM:" + field)
        return str(number.normalize())
    if field == "option_right":
        return {"C": "CALL", "V": "PUT"}.get(str(value).upper(), str(value).upper())
    return str(value)


def normalize_group(records, *, now=None):
    """Full keys/provenance required; conflicts and changed terms fail closed."""
    now = now or datetime.now(timezone.utc)
    fields, provenance, keys, errors = {}, {}, set(), []
    for r in records:
        e = r.get("evidence") or {}
        source = r.get("source_class")
        if source not in SOURCE_RANK or source == "POROTA_LEGACY_EVIDENCE":
            errors.append("UNVERIFIED_SOURCE")
            continue
        if not r.get("source_ref") or r.get("evidence_hash") != evidence_hash(e):
            errors.append("INVALID_PROVENANCE")
            continue
        try:
            seen = datetime.fromisoformat(str(r.get("observed_at")).replace("Z", "+00:00"))
            # Contract snapshots are versioned/static.  Age alone cannot make
            # lot size or quote basis stale; only future/naive timestamps are
            # invalid here. Dynamic field TTLs live in the readiness evaluator.
            if seen.tzinfo is None or (now-seen).total_seconds() < 0:
                raise ValueError("invalid time")
            effective = r.get("effective_at")
            if effective:
                at = datetime.fromisoformat(str(effective).replace("Z", "+00:00"))
                if at.tzinfo is None or at > now:
                    raise ValueError("not effective")
        except (ValueError, TypeError):
            errors.append("EVIDENCE_NOT_CURRENT")
        if r.get("change_pending") or e.get("revoked") or e.get("adjusted_series_unverified"):
            errors.append("CHANGE_REVIEW_REQUIRED")
        stored_currency = str(r.get("currency") or "").upper()
        payload_currency = str(e.get("currency") or "").upper()
        if (stored_currency and payload_currency
                and stored_currency != payload_currency):
            errors.append("CONTRACT_V2_CURRENCY_MISMATCH")
        key = (str(r.get("ticker") or "").upper(), canonical_family(r.get("family")),
               canonical_market(r.get("market")), stored_currency or payload_currency,
               canonical_settlement(r.get("settlement")) if r.get("settlement") else "")
        if any(v in {"", "*", "UNKNOWN", "NO_VERIFICADO"} for v in key):
            errors.append("IDENTITY_INCOMPLETE")
        keys.add(key)
        for src, dst in MAP.items():
            if e.get(src) in (None, ""):
                continue
            try:
                value = _value(dst, e[src])
            except ValueError as exc:
                errors.append(str(exc)); continue
            if dst in fields and fields[dst] != value:
                errors.append("CONFLICT:" + dst)
            else:
                fields[dst] = value
                provenance[dst] = {"source_class": source, "source_ref": r["source_ref"],
                    "evidence_hash": r["evidence_hash"], "observed_at": r["observed_at"],
                    "effective_at": r.get("effective_at")}
    if len(keys) != 1:
        errors.append("IDENTITY_CONFLICT")
    key = next(iter(keys)) if len(keys)==1 else ("", "", "", "", "")
    ticker, family, market, currency, settlement = key
    for field in ("cash_multiplier", "quantity_step", "minimum_quantity"):
        if field not in fields:
            errors.append("MISSING:" + field)
    if family not in SUPPORTED:
        errors.append("EXECUTOR_GAP:" + family)
    contract = {**fields, "family": family, "market": market, "currency": currency,
                "settlement": settlement, "metadata_source": "CONTRACT_EVIDENCE_V2_BOUND",
                "field_provenance": provenance}
    if not errors:
        try:
            contract_from_metadata(ticker, family, contract)
        except ValueError as exc:
            errors.append("CONTRACT_INVALID:" + str(exc))
    return {"ticker": ticker, "instrument_type": family, "market": market,
            "currency": currency, "settlement": settlement,
            "source": "CONTRACT_EVIDENCE_V2", "observed_at": max((r.get("observed_at") or "" for r in records), default=""),
            "financial_contract_v17": contract if not errors else None,
            "contract_bridge": {"status": "NORMALIZED" if not errors else "BLOCKED",
                "gaps": sorted(set(errors)), "field_provenance": provenance,
                "observed_at": max((r.get("observed_at") or "" for r in records), default="") },
            "identity_evidence": {"market_explicit": True, "currency_explicit": True, "settlement_explicit": True}}


def complements_from_store(store, *, now=None):
    """Read current evidence using one connection; absent tables remain absent."""
    with store.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"contract_evidence_v2_current", "contract_evidence_v2_snapshots"} <= tables:
            return []
        rows = c.execute("""SELECT c.*,s.source_ref,s.effective_at,s.evidence_json
            FROM contract_evidence_v2_current c JOIN contract_evidence_v2_snapshots s
            ON s.snapshot_id=c.snapshot_id""").fetchall()
        pending = set()
        if "contract_evidence_v2_changes" in tables:
            pending = {tuple(r) for r in c.execute("""SELECT family,ticker,market,currency,settlement
                FROM contract_evidence_v2_changes WHERE status='CHANGED_REVIEW_REQUIRED'""")}
    grouped = defaultdict(list)
    for row in rows:
        r = dict(row)
        try:
            r["evidence"] = json.loads(r.pop("evidence_json"))
        except (ValueError, TypeError):
            continue
        r["change_pending"] = tuple(r[k] for k in (
            "family", "ticker", "market", "currency", "settlement")) in pending
        grouped[(r["family"],r["ticker"],r["market"],r["currency"],r["settlement"])].append(r)
    result = []
    for records in grouped.values():
        normalized = normalize_group(records, now=now)
        if normalized["ticker"]:
            result.append(normalized)
            continue
        # A conflict must invalidate every explicitly implicated identity,
        # not disappear because normalization deliberately refused a winner.
        identities = {(r["ticker"],canonical_family(r["family"]),canonical_market(r["market"]),
                       str(r["evidence"].get("currency") or "").upper(),canonical_settlement(r["settlement"]))
                      for r in records}
        for key in sorted(identities):
            if all(v and v not in {"UNKNOWN","NO_VERIFICADO"} for v in key):
                result.append(dict(normalized,**dict(zip(("ticker","instrument_type","market","currency","settlement"),key))))
    return result


def caucion_offer_from_evidence(records, primary, *, now=None):
    """Bind verified evidence to the existing placing/maturity PAPER executor.

    Caller supplies the exact persisted PPI identity; this does not assign
    capital or choose a term. All fees must be an explicit principal budget.
    """
    from bt_caucion_paper import CaucionOffer
    from bs_instrument_contracts import aware_datetime
    from bu_instrument_catalog import _candidate_has_ppi_primary, _candidate_timestamp_is_fresh
    now = now or datetime.now(timezone.utc)
    normalized = normalize_group(records, now=now)
    permitted = {"MISSING:cash_multiplier", "MISSING:quantity_step", "MISSING:minimum_quantity", "EXECUTOR_GAP:CAUCIONES"}
    errors = set(normalized["contract_bridge"]["gaps"]) - permitted
    key = tuple(normalized.get(k) for k in ("ticker","instrument_type","market","currency","settlement"))
    pkey = tuple(primary.get(k) for k in ("ticker","instrument_type","market","currency","settlement"))
    if errors or key != pkey or key[1] != "CAUCIONES":
        raise ValueError("CAUCION_EVIDENCE_INVALID:" + ",".join(sorted(errors or {"IDENTITY_MISMATCH"})))
    if (primary.get("status") != "AVAILABLE"
            or not _candidate_has_ppi_primary(primary.get("settlement_source"),json.dumps(primary.get("raw") or {}))
            or not _candidate_timestamp_is_fresh(primary.get("last_seen_at"),now.isoformat(),86400)):
        raise ValueError("PPI_PRIMARY_IDENTITY_NOT_CURRENT")
    fields = {}
    for row in records:
        for name,value in row["evidence"].items():
            if name in fields and fields[name] != value:
                raise ValueError("CAUCION_EVIDENCE_CONFLICT:" + name)
            fields[name] = value
    if fields.get("side") != "COLOCADORA":
        raise ValueError("CAUCION_SIDE_NOT_AUTHORIZED")
    required = ("annual_rate_fraction","start_date","maturity_at","quoted_at","available_principal",
                "minimum_principal","principal_step","day_count_basis","fee_payment")
    if key[3] != "ARS":
        required += ("quoted_total_fees", "fee_quote_principal")
    missing = [name for name in required if fields.get(name) is None]
    if missing:
        raise ValueError("CAUCION_MISSING:" + ",".join(missing))
    if not 0 <= (now-aware_datetime(fields["quoted_at"])).total_seconds() <= 60:
        raise ValueError("CAUCION_QUOTE_STALE_OR_FUTURE")
    if fields.get("market_session_state") != "OPEN" or fields.get("operable") is not True:
        raise ValueError("CAUCION_MARKET_NOT_OPEN")
    # The origin of the dynamic terms must itself be recent, even if an old
    # payload contains a fresh-looking quoted_at string.
    if any(not 0 <= (now-aware_datetime(r["observed_at"])).total_seconds() <= 60
           for r in records if any(k in r["evidence"] for k in ("quoted_at","annual_rate_fraction","available_principal"))):
        raise ValueError("CAUCION_DYNAMIC_EVIDENCE_STALE")
    optional_cost = {name: fields[name] for name in
                     ("quoted_total_fees", "fee_quote_principal")
                     if fields.get(name) is not None}
    return CaucionOffer(instrument_id=key[0],currency=key[3],
        metadata_source="CONTRACT_EVIDENCE_V2:" + ";".join(sorted(r["evidence_hash"] for r in records)),
        **{name:fields[name] for name in required}, **optional_cost)

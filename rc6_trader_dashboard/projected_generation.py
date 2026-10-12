"""Bounded adapter to the sealed, derived SHADOW projection.

The native reader verifies all four members, custody and projection semantics.
This adapter checks the returned cut and bounded query contract. It never
claims to decode the complete logical report/checkpoint during a render.
"""
from __future__ import annotations

import hashlib
import json
from math import isfinite
import sqlite3
from time import monotonic

GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v2"
EXPORT_SCHEMA = "rc6.shadow-ui-committed-projection.v1"
VERIFICATION_LEVEL = "WIRE_AND_PROJECTION_SEMANTICS"
CUSTODY = "LOCAL_DURABLE_CUSTODY_NOT_EXTERNAL_AUTHENTICATION"
OUTPUT_LIMIT = 4 * 1024 * 1024
ROLES = ("report", "checkpoint", "status", "projection")
DATASETS = {"opportunities", "discovery", "tradeability", "exclusions", "events", "capacity",
            "families", "strategies", "signals", "experiments", "exits", "event-risk"}
SAFETY = {"mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
          "provider_requests": 0, "source_database_effect": "READ_ONLY",
          "factual_execution": "NOT_CALLED", "ppi_watch": "UNTOUCHED"}
FUNNEL_STAGES = {"READY": "CATALOG_READY", "ELIGIBLE": "STRATEGY_ELIGIBLE", "TRADEABLE": "TRADEABLE",
                 "DISCOVERY": "DISCOVERY_TOUCHED", "WARM": "WARM", "HOT": "HOT", "SIGNAL": "SIGNAL_CANDIDATE",
                 "ECONOMICS": "ECONOMICS_PASS", "RISK": "RISK_PASS", "PAPER": "PAPER_OPENED"}


def _safety(value):
    if (not isinstance(value, dict) or set(value) != set(SAFETY)
            or any(type(value.get(key)) is not type(expected) or value[key] != expected
                   for key, expected in SAFETY.items())):
        raise ValueError("PROJECTION_SAFETY_MISMATCH")


def _same_cut(value, pointer, manifest):
    watermark = manifest["source_watermark"]
    if (not isinstance(value, dict) or value.get("generation_schema") != GENERATION_SCHEMA
            or value.get("generation_id") != pointer["generation_id"]
            or type(value.get("sequence")) is not int or value["sequence"] != pointer["sequence"]
            or value.get("as_of") != watermark["as_of"]
            or value.get("source_watermark") != watermark
            or value.get("configuration_fingerprint") != manifest["configuration_fingerprint"]):
        raise ValueError("PROJECTION_CUT_MISMATCH")
    _safety(value.get("safety"))


def requested_identity(value):
    """The UI selector is ticker/family/market/currency/settlement."""
    if isinstance(value, str):
        value = json.loads(value) if value.startswith("[") else value.split("|")
    if (not isinstance(value, (list, tuple)) or len(value) != 5
            or any(not isinstance(part, str) or not part for part in value)):
        raise ValueError("PROJECTION_QUERY_IDENTITY_INVALID")
    return tuple(part.upper() for part in value)


def _funnel_matches(row, filters):
    identity = row.get("identity")
    native = (tuple(identity[at] for at in (0, 1, 4, 3, 2))
              if isinstance(identity, (tuple, list)) and len(identity) == 5 else None)
    fields = {key: row.get(key) for key in ("currency", "channel", "family", "market", "settlement", "session")}
    if native:
        for key, value in zip(("symbol", "family", "market", "currency", "settlement"), native):
            fields[key] = fields.get(key) or value
    fields["strategy"] = row.get("strategy_id") or row.get("strategy_version")
    if any(filters.get(key) and str(fields.get(key) or "").upper() != str(filters[key]).upper()
           for key in ("currency", "channel", "family", "market", "settlement", "strategy", "session")):
        return False
    if filters.get("q") and str(filters["q"]).upper() not in str(row.get("symbol") or fields.get("symbol") or "").upper():
        return False
    if filters.get("identity") and (native is None or requested_identity(native) != requested_identity(filters["identity"])):
        return False
    if filters.get("cohort"):
        from .projection import funnel_cohort_id
        if funnel_cohort_id(row).lower() != str(filters["cohort"]).lower():
            return False
    return True


def read_projected(root, reader, *, filters=None, offset=0, deadline=None):
    try:
        if deadline is not None and (type(deadline) not in (int, float) or not isfinite(deadline)):
            raise ValueError("PROJECTION_QUERY_DEADLINE_INVALID")
        cut = reader(root, filters={key: value for key, value in (filters or {}).items() if key != "offset"},
                     offset=offset, limit=10, deadline=deadline)
        pointer, manifest, contract = cut["pointer"], cut["manifest"], cut["export_contract"]
        if not all(isinstance(value, dict) for value in (pointer, manifest, contract)):
            raise ValueError("PROJECTION_ENVELOPE_SHAPE_INVALID")
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()
        if (pointer.get("schema") != GENERATION_SCHEMA or manifest.get("schema") != GENERATION_SCHEMA
                or not pointer.get("generation_id") or manifest.get("generation_id") != pointer["generation_id"]
                or type(pointer.get("sequence")) is not int or pointer["sequence"] <= 0
                or type(manifest.get("sequence")) is not int or manifest["sequence"] != pointer["sequence"]
                or hashlib.sha256(encoded).hexdigest() != pointer.get("manifest_sha256")
                or set(manifest.get("files", {})) != set(ROLES)):
            raise ValueError("PROJECTION_ENVELOPE_MISMATCH")
        if (contract.get("schema") != EXPORT_SCHEMA
                or contract.get("verification_level") != VERIFICATION_LEVEL
                or contract.get("custody") != CUSTODY):
            raise ValueError("PROJECTION_VERIFICATION_CONTRACT_UNSUPPORTED")
        _same_cut(contract, pointer, manifest)
        verified, headers, derivation = (contract["verified_payloads"], contract["role_headers"], contract["derivation"])
        if set(verified) != set(ROLES) or set(headers) != set(ROLES) or set(derivation) != set(ROLES[:3]):
            raise ValueError("PROJECTION_ROLE_SET_MISMATCH")
        for role in ROLES:
            value = verified[role]
            if (not isinstance(value, dict) or type(value.get("logical_bytes")) is not int
                    or value["logical_bytes"] <= 0
                    or value.get("payload_digest") != manifest["files"][role]["payload_digest"]
                    or not isinstance(value.get("storage_schema"), str) or not value["storage_schema"]):
                raise ValueError("PROJECTION_VERIFIED_DIGEST_MISMATCH")
            _same_cut(headers[role], pointer, manifest)
            if role != "projection" and derivation[role] != value["payload_digest"]:
                raise ValueError("PROJECTION_DERIVATION_MISMATCH")
        _same_cut(cut["status"], pointer, manifest)
        _same_cut(cut["report"], pointer, manifest)
        pages = cut["dataset_pages"]
        if not isinstance(pages, dict) or set(pages) != DATASETS:
            raise ValueError("PROJECTION_DATASET_SET_MISMATCH")
        for page in pages.values():
            if (not isinstance(page, dict) or not isinstance(page.get("rows"), list)
                    or any(not isinstance(row, dict) for row in page["rows"])
                    or len(page["rows"]) > 10 or type(page.get("offset")) is not int
                    or page["offset"] != offset or type(page.get("limit")) is not int or page["limit"] != 10
                    or not isinstance(page.get("state"), str) or not isinstance(page.get("reason"), str)
                    or not isinstance(page.get("source_path"), str)
                    or not isinstance(page.get("source_schema"), str)):
                raise ValueError("PROJECTION_PAGE_CONTRACT_INVALID")
            if page["state"] == "AVAILABLE":
                if (type(page.get("total")) is not int or page["total"] < 0
                        or len(page["rows"]) != min(10, max(0, page["total"] - offset))
                        or page.get("as_of") != manifest["source_watermark"]["as_of"]
                        or not isinstance(page.get("source_path"), str)
                        or not isinstance(page.get("source_schema"), str)):
                    raise ValueError("PROJECTION_PAGE_TOTAL_OR_SOURCE_INVALID")
            elif page["rows"]:
                raise ValueError("PROJECTION_UNVERIFIED_ROWS_FORBIDDEN")
        scoped = cut["funnel_scope"]
        try:
            funnel_offset = max(0, min(100000, int((filters or {}).get("funnel_offset", 0))))
        except (TypeError, ValueError):
            funnel_offset = 0
        if (not isinstance(scoped, dict) or not isinstance(scoped.get("counts"), dict)
                or any(not isinstance(scoped.get(key), str) for key in ("state", "reason", "label"))
                or not isinstance(scoped.get("groups"), list) or len(scoped["groups"]) > 10
                or any(not isinstance(row, dict) for row in scoped["groups"])
                or type(scoped.get("total_groups")) is not int or scoped["total_groups"] < len(scoped["groups"])
                or type(scoped.get("groups_offset")) is not int or scoped["groups_offset"] != funnel_offset
                or type(scoped.get("groups_limit")) is not int or scoped["groups_limit"] != 10
                or len(scoped["groups"]) != min(10, max(0, scoped["total_groups"] - funnel_offset))
                or scoped.get("as_of") != manifest["source_watermark"]["as_of"]
                or scoped.get("generation_id") != pointer["generation_id"]):
            raise ValueError("PROJECTION_FUNNEL_SCOPE_INVALID")
        if any(type(value) is not int or value < 0 for value in scoped["counts"].values() if value is not None):
            raise ValueError("PROJECTION_FUNNEL_CARDINALITY_INVALID")
        if scoped.get("selected") is None and scoped["counts"]:
            raise ValueError("PROJECTION_FUNNEL_SELECTION_MISSING")
        selected = scoped.get("selected")
        if selected is not None:
            if (not isinstance(selected, dict) or not isinstance(selected.get("stages"), dict)
                    or scoped["counts"] != {key: selected["stages"].get(stage) for key, stage in FUNNEL_STAGES.items()}):
                raise ValueError("PROJECTION_FUNNEL_CARD_SCOPE_MISMATCH")
        if any(not isinstance(group.get("stages"), dict) for group in scoped["groups"]):
            raise ValueError("PROJECTION_FUNNEL_GROUP_SCHEMA_INVALID")
        if any(not _funnel_matches(group, filters or {}) for group in
               ([selected] if selected is not None else []) + scoped["groups"]):
            raise ValueError("PROJECTION_FUNNEL_QUERY_SCOPE_MISMATCH")
        query_bytes = json.dumps(cut, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()
        if len(query_bytes) > OUTPUT_LIMIT:
            raise ValueError("PROJECTION_QUERY_BYTE_BUDGET_EXHAUSTED")
        if deadline is not None and monotonic() >= deadline:
            raise ValueError("PROJECTION_QUERY_TIME_BUDGET_EXHAUSTED")
        return {"state": "COMMITTED_COHERENT_SHADOW", "reason": "", "report": cut["report"],
                "pointer": pointer, "manifest": manifest, "status": cut["status"],
                "verification_level": VERIFICATION_LEVEL, "export_contract": contract,
                "dataset_pages": pages, "funnel_scope": scoped, "query_bytes": len(query_bytes)}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError, sqlite3.Error) as error:
        return {"state": "NO_VERIFICADO", "reason": "COMMITTED_PROJECTION_REJECTED",
                "error_class": type(error).__name__, "report": {}}

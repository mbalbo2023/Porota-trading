"""Adapter to the read-only committed generation reader owned by #466.

There is deliberately no fallback reader for latest/checkpoint/status and no
copy of the engine's persistence implementation in this workstream.
"""
from importlib import import_module
import hashlib
import json
from pathlib import Path

AWAITING = "NO_VERIFICADO_AWAITING_RECONCILIATION"
PAYLOAD_LIMIT = 4 * 1024 * 1024
GENERATION_SCHEMA = "rc6.shadow-evidence-generation.v2"


def read_shadow(root: Path, reader=None, *, filters=None, offset=0, deadline=None):
    missing = {"state": AWAITING, "reason": "CANONICAL_GENERATION_READER_NOT_AVAILABLE", "report": {}}
    if reader is None:
        try:
            persistence = import_module("rc6_shadow_runtime.persistence")
            projection_reader = getattr(persistence, "read_committed_projection", None)
            if projection_reader is not None:
                from .projected_generation import read_projected
                return read_projected(root, projection_reader, filters=filters, offset=offset, deadline=deadline)
            reader = persistence.read_committed_generation
        except (ImportError, AttributeError):
            return missing
    try:
        cut = reader(root, payload_limit=PAYLOAD_LIMIT)
        pointer, manifest = cut["pointer"], cut["manifest"]
        ident = pointer["generation_id"]
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str,
                             allow_nan=False).encode()
        if (pointer.get("schema") != GENERATION_SCHEMA or manifest.get("schema") != GENERATION_SCHEMA
                or not ident or manifest["generation_id"] != ident
                or type(pointer.get("sequence")) is not int or pointer["sequence"] <= 0
                or pointer["sequence"] != manifest["sequence"]
                or hashlib.sha256(encoded).hexdigest() != pointer["manifest_sha256"]):
            raise ValueError("GENERATION_ENVELOPE_MISMATCH")
        watermark, fingerprint = manifest["source_watermark"], manifest["configuration_fingerprint"]
        for role in ("report", "checkpoint", "status"):
            member = cut[role]
            if (member["generation_id"] != ident or member["source_watermark"] != watermark
                    or member["configuration_fingerprint"] != fingerprint
                    or member["as_of"] != watermark["as_of"]
                    or member.get("generation_schema") != GENERATION_SCHEMA
                    or type(member.get("sequence")) is not int
                    or member.get("sequence") != pointer["sequence"]):
                raise ValueError("GENERATION_MEMBER_MISMATCH")
            payload_bytes = json.dumps(member, sort_keys=True, separators=(",", ":"), default=str,
                                       allow_nan=False).encode()
            if hashlib.sha256(payload_bytes).hexdigest() != manifest["files"][role]["payload_digest"]:
                raise ValueError("GENERATION_MEMBER_DIGEST_MISMATCH")
            safety = {"mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                      "provider_requests": 0, "source_database_effect": "READ_ONLY",
                      "factual_execution": "NOT_CALLED", "ppi_watch": "UNTOUCHED"}
            nested = member.get("safety")
            if (not isinstance(nested, dict) or set(nested) != set(safety)
                    or any(type(nested.get(k)) is not type(v) or nested[k] != v for k, v in safety.items())
                    or any(type(member.get(k)) is not type(v) or member[k] != v for k, v in safety.items())):
                raise ValueError("GENERATION_MEMBER_SAFETY_MISMATCH")
        report = cut["report"]
        if report.get("real_orders_sent") != 0 or report.get("real_routes") != "NOT_CALLED":
            raise ValueError("GENERATION_SAFETY_UNVERIFIED")
        return {"state": "COMMITTED_COHERENT_SHADOW", "reason": "", "report": report,
                "pointer": pointer, "manifest": manifest, "status": cut["status"]}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        # Never surface an arbitrary provider/path/exception message.
        return {"state": "NO_VERIFICADO", "reason": "COMMITTED_GENERATION_REJECTED",
                "error_class": type(exc).__name__, "report": {}}

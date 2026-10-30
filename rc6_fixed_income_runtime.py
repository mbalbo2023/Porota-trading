"""Bounded read-only producer for the existing fixed-income PAPER strategy.

The release owner must pin the SHA256 of a reviewed manifest through
POROTA_FI_EVIDENCE_MANIFEST_SHA256, separately from the evidence directory.
The manifest attests issuer/OOS review; hashing alone never proves either.
No unpinned JSON, IOL analytics flag, or quote can attest its own authority.

This module verifies that exact reviewed bytes and clocks are still present,
then computes the scenario using the existing immutable model. It neither
imports receipts into SQLite nor implements the native event/cash exit. The
strategy must still HOLD at that capability gate until that separate lifecycle
is implemented and tested. No orders, network calls, fills or DB writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from bs_instrument_contracts import aware_datetime, family_name

PAPER_ONLY = True
REAL_ROUTES_USED = ()
MANIFEST_SCHEMA = "rc6.fixed-income-reviewed-manifest.v1"
RECEIPT_SCHEMA = "rc6.fixed-income-strategy-input.v1"
MAX_JSON_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_RECEIPTS = 4096
MAX_FLOWS = 512
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class FixedIncomeEvidenceError(ValueError):
    """A stable input-blocker code, not a provider exception or secret dump."""


def _fail(code):
    raise FixedIncomeEvidenceError(code)


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail("FI_EVIDENCE_DIGEST_INVALID")
    return value


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("FI_EVIDENCE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda _value: _fail("FI_EVIDENCE_NONFINITE_JSON"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FixedIncomeEvidenceError("FI_EVIDENCE_JSON_INVALID") from exc
    if not isinstance(result, dict):
        _fail("FI_EVIDENCE_OBJECT_REQUIRED")
    return result


def _read(root, name, expected, *, limit=MAX_JSON_BYTES):
    # Single directory, regular files, no symlinks or nested paths. Read from
    # one fd so replacing a path after open cannot change the checked bytes.
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or Path(name).name != name or "/" in name or "\\" in name):
        _fail("FI_EVIDENCE_PATH_INVALID")
    expected = _digest(expected)
    try:
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                _fail("FI_EVIDENCE_SIZE_OR_TYPE_INVALID")
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise FixedIncomeEvidenceError("FI_EVIDENCE_FILE_UNAVAILABLE") from exc
    if len(raw) > limit:
        _fail("FI_EVIDENCE_SIZE_OR_TYPE_INVALID")
    if hashlib.sha256(raw).hexdigest() != expected:
        _fail("FI_EVIDENCE_HASH_MISMATCH")
    return raw


def _proof(root, value, *, json_document=False):
    if not isinstance(value, dict) or set(value) != {"file", "sha256"}:
        _fail("FI_EVIDENCE_FILE_PROOF_INVALID")
    raw = _read(root, value["file"], value["sha256"],
                limit=MAX_JSON_BYTES if json_document else MAX_SOURCE_BYTES)
    return _json(raw) if json_document else raw


def _window(row, at):
    try:
        issued = aware_datetime(row["known_at"])
        expires = aware_datetime(row["valid_until"])
        if not issued <= at < expires:
            _fail("FI_EVIDENCE_NOT_YET_KNOWN_OR_EXPIRED")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FixedIncomeEvidenceError):
            raise
        raise FixedIncomeEvidenceError("FI_EVIDENCE_CLOCK_INVALID") from exc


def quote_sha256(quote):
    """Exact native quote/contract binding; never reconstruct a fresh clock."""
    return hashlib.sha256(json.dumps(asdict(quote), sort_keys=True,
                                    separators=(",", ":"), default=str,
                                    allow_nan=False).encode()).hexdigest()


def contract_economics(contract):
    """Stable contract economics; refreshed evidence IDs are not price data."""
    return {key: str(value) if value is not None else None
            for key, value in asdict(contract).items() if key != "metadata_source"}


def fixed_income_strategy_inputs(store, quote, decision_at, *, directory=None,
                                 manifest_sha256=None):
    """Load one exact identity receipt and compute its factual PIT scenario.

    Keyword parameters are explicit offline-test/tool injection. Production
    PaperStore supplies neither: its directory/hash come from release-owned
    configuration. Source documents stay private next to the PAPER database.
    Missing pin or document raises a typed blocker; never fall back to an old
    receipt, another ticker/currency, a hypothetical OOS result or live IOL.
    The pin covers static reviewed economics, NOT a live quote. Each decision
    uses the native PPI quote supplied by the existing collector and hashes
    that exact quote for the decision receipt. It needs no per-tick release.
    """
    from rc6_fixed_income_strategy_shadow import (
        ContractProof, ProjectedFlow, RiskLimits, evaluate_shadow,
    )

    pin = (manifest_sha256 if manifest_sha256 is not None else
           os.getenv("POROTA_FI_EVIDENCE_MANIFEST_SHA256", ""))
    if not pin:
        _fail("FI_EVIDENCE_RELEASE_PIN_MISSING")
    root = Path(directory if directory is not None else os.getenv(
        "POROTA_FI_EVIDENCE_DIRECTORY", str(Path(store.path).parent / "fixed-income-evidence")))
    try:
        if root.is_symlink() or not root.is_dir():
            _fail("FI_EVIDENCE_DIRECTORY_UNAVAILABLE")
        at = aware_datetime(decision_at)
        key = (quote.symbol, family_name(quote.asset_class), quote.market,
               quote.currency, quote.settlement)
        if quote.contract is None or quote.contract.key != key:
            _fail("FI_EVIDENCE_EXACT_CONTRACT_REQUIRED")
        if key[1] not in {"BONOS", "LETRAS", "OBLIGACIONES"}:
            _fail("FI_EVIDENCE_FAMILY_UNSUPPORTED")
        if not str(quote.metadata_source or "").startswith("PPI_CATALOG:"):
            _fail("FI_EVIDENCE_NATIVE_PPI_PROVENANCE_REQUIRED")
        if quote.time_error(at, require_trade=True, max_age_seconds=120,
                            max_trade_age_seconds=900):
            _fail("FI_EVIDENCE_NATIVE_QUOTE_CLOCK_INVALID")
        manifest = _json(_read(root, "manifest.json", pin))
        if (manifest.get("schema") != MANIFEST_SCHEMA
                or manifest.get("paper_only") is not True
                or manifest.get("real_routes_used") != []
                or manifest.get("review_authority") != "RELEASE_REVIEWED_OFFICIAL_AND_OOS"):
            _fail("FI_EVIDENCE_RELEASE_ATTESTATION_INVALID")
        _window(manifest, at)
        entries = manifest.get("receipts")
        if not isinstance(entries, list) or not 0 < len(entries) <= MAX_RECEIPTS:
            _fail("FI_EVIDENCE_MANIFEST_RECEIPTS_INVALID")
        found = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("identity"), list):
                _fail("FI_EVIDENCE_MANIFEST_IDENTITY_INVALID")
            identity = tuple(entry["identity"])
            if len(identity) != 5 or any(not isinstance(v, str) or not v for v in identity):
                _fail("FI_EVIDENCE_MANIFEST_IDENTITY_INVALID")
            if identity in seen:
                _fail("FI_EVIDENCE_IDENTITY_AMBIGUOUS")
            seen.add(identity)
            if identity == key:
                found.append(entry)
        if len(found) != 1:
            _fail("FI_EVIDENCE_EXACT_IDENTITY_MISSING")
        entry = found[0]
        receipt = _proof(root, entry.get("proof"), json_document=True)
        if (receipt.get("schema") != RECEIPT_SCHEMA
                or tuple(receipt.get("identity") or ()) != key):
            _fail("FI_EVIDENCE_RECEIPT_IDENTITY_MISMATCH")
        _window(receipt, at)
        if aware_datetime(receipt["known_at"]) > aware_datetime(manifest["known_at"]):
            _fail("FI_EVIDENCE_REVIEW_CLOCK_INVALID")
        if receipt.get("contract_economics") != contract_economics(quote.contract):
            _fail("FI_EVIDENCE_CONTRACT_ECONOMICS_MISMATCH")
        terms = _proof(root, receipt.get("terms"), json_document=True)
        validation = _proof(root, receipt.get("validation"), json_document=True)
        issuer = receipt.get("issuer_document")
        artifact = receipt.get("oos_artifact")
        dataset = receipt.get("oos_dataset")
        for proof in (issuer, artifact, dataset):
            _proof(root, proof)
        if (terms.get("source_sha256") != issuer["sha256"]
                or validation.get("artifact_sha256") != artifact["sha256"]
                or validation.get("dataset_sha256") != dataset["sha256"]):
            _fail("FI_EVIDENCE_REFERENCED_DOCUMENT_MISMATCH")
        if issuer["sha256"] in {artifact["sha256"], dataset["sha256"]}:
            _fail("FI_EVIDENCE_INDEPENDENT_DOCUMENTS_REQUIRED")
        if (tuple(terms.get("identity") or ()) != key
                or tuple(validation.get("identity") or ()) != key
                or terms.get("source_authority") != "OFFICIAL_ISSUER_VERIFIED"
                or validation.get("verification_is_independent") is not True
                or validation.get("artifact_attested_by_release") is not True):
            _fail("FI_EVIDENCE_SOURCE_OR_OOS_ATTESTATION_INVALID")
        if any(aware_datetime(row["known_at"]) > aware_datetime(receipt["known_at"])
               for row in (terms, validation)):
            _fail("FI_EVIDENCE_REVIEW_CLOCK_INVALID")
        proof = ContractProof(quote.contract, terms["source_reference"],
                              terms["source_sha256"], terms["source_at"], terms["known_at"])
        rows = receipt.get("flows")
        if not isinstance(rows, list) or not 0 < len(rows) <= MAX_FLOWS:
            _fail("FI_EVIDENCE_FLOW_SET_INVALID")
        minutes = receipt.get("market_exit_horizon_minutes")
        if type(minutes) is not int or not 0 < minutes <= 360:
            _fail("FI_EVIDENCE_EXIT_HORIZON_INVALID")
        exit_at = at + timedelta(minutes=minutes)
        flows, obligation_ids = [], set()
        for row in rows:
            flow = ProjectedFlow(**row)
            # Flow economics must derive from the same reviewed issuer bytes,
            # not an unauthenticated provider schedule in the receipt.
            if (flow.source_sha256 != issuer["sha256"]
                    or flow.source_reference != terms["source_reference"]
                    or aware_datetime(flow.source_at) != aware_datetime(terms["source_at"])
                    or aware_datetime(flow.known_at) != aware_datetime(terms["known_at"])):
                _fail("FI_EVIDENCE_FLOW_PROVENANCE_MISMATCH")
            if not flow.obligation_id or flow.obligation_id in obligation_ids:
                _fail("FI_EVIDENCE_FLOW_OBLIGATION_DUPLICATE")
            obligation_ids.add(flow.obligation_id)
            if at < aware_datetime(flow.due_at) <= exit_at:
                flows.append(flow)
        if not flows:
            _fail("FI_EVIDENCE_NO_FLOWS_IN_HOLDING_HORIZON")
        live_quote_sha = quote_sha256(quote)
        scenario = evaluate_shadow(
            quote, proof, flows, RiskLimits(**receipt["risk_limits"]),
            quantity_vn=receipt["quantity_vn"], decision_at=decision_at,
            planned_exit_at=exit_at.isoformat(), exit_mode="MARKET_EXIT",
            book_reference="native-ppi-quote:" + live_quote_sha,
            book_sha256=live_quote_sha, book_source_kind="PPI_PRIMARY")
        return {"scenario": scenario, "terms": terms, "validation": validation,
                "upstream_authenticated": True, "receipt_attested_by_release": True,
                "source_kind": "PPI_PRIMARY",
                "runtime_evidence": {"manifest_sha256": pin,
                                     "receipt_sha256": entry["proof"]["sha256"],
                                     "quote_sha256": live_quote_sha,
                                     "identity": list(key),
                                     "native_event_cash_lifecycle": "NO_VERIFICADO"}}
    except FixedIncomeEvidenceError:
        raise
    except (KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
        raise FixedIncomeEvidenceError("FI_EVIDENCE_INPUT_INVALID") from exc

"""A667: synthetic external Watch/provider receipts must bind to signed quota.

No PPI login, real provider GET, external Watch process, or host filesystem.
The fixture public key is ephemeral and never authorizes a real deployment.
"""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

import rc6_ppi_global_budget as budget_module
from be_paper_engine import PaperStore
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure, RuntimePPIBudget, exit_capacity_contract,
    static_shared_capacity_state,
)
from tests.test_rc6_a586_ppi_missing_lease_guard import (
    sign_synthetic_authority, sign_synthetic_external_owner,
    static_authority_file,
)
from tests.test_rc6_ppi_capacity_benchmark import wire


def _write_material(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def _signed_with_material(tmp_path, monkeypatch, clock, *, provider_scope=None,
                          watch_scope=None, watch_retries=True, watch_age_seconds=0,
                          provider_age_seconds=0, provider_current_delta=0,
                          legacy_watch_v1=False, watch_current_over_max=0,
                          watch_global_over_max=0, watch_valid_from_late=0,
                          watch_valid_until_short=0, watch_window_basis=None,
                          watch_callers_covered=None, watch_hard_cap_kind=None,
                          watch_owner_signature_tamper=False,
                          watch_owner_is_reviewer=False):
    environ, signer, external_owner = static_authority_file(
        tmp_path, monkeypatch, clock, return_external_owner=True)
    authority_path = Path(environ["POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH"])
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    for field in ("signer_public_key_b64", "signature_b64"):
        authority.pop(field)
    account_scope = "e" * 64
    provider = {
        "schema": "RC6_PPI_PROVIDER_QUOTA_EVIDENCE_V1",
        "quota_scope_sha256": provider_scope or account_scope,
        "reviewed_at": (clock.now() - timedelta(seconds=provider_age_seconds)).isoformat(),
        "window_seconds": authority["window_seconds"],
        "endpoint_limits": {**authority["provider_endpoint_limits"],
            "current": authority["provider_endpoint_limits"]["current"] + provider_current_delta},
        "global_limit": authority["provider_global_limit"],
    }
    watch = {
        "schema": budget_module.STATIC_WATCH_EVIDENCE_SCHEMA,
        "quota_scope_sha256": watch_scope or account_scope,
        "reviewed_at": (clock.now() - timedelta(seconds=watch_age_seconds)).isoformat(),
        "window_seconds": authority["window_seconds"],
        "endpoint_reserve": authority["watch_external_endpoint_reserve"],
        "global_reserve": authority["watch_external_global_reserve"],
        "retries_included": watch_retries,
    }
    if legacy_watch_v1:
        watch["schema"] = "RC6_PPI_EXTERNAL_WATCH_USAGE_EVIDENCE_V1"
    else:
        watch.update({
            "window_basis": watch_window_basis or budget_module.STATIC_WATCH_WINDOW_BASIS,
            "external_callers_covered": (
                watch_callers_covered or budget_module.STATIC_WATCH_COVERAGE),
            "max_endpoint_wire_attempts": {
                **authority["watch_external_endpoint_reserve"],
                "current": authority["watch_external_endpoint_reserve"]["current"] +
                    watch_current_over_max,
            },
            "max_global_wire_attempts": (
                authority["watch_external_global_reserve"] + watch_global_over_max),
            "hard_cap_kind": watch_hard_cap_kind or budget_module.STATIC_WATCH_HARD_CAP_KIND,
            "guarantee_valid_from": (
                clock.now() - timedelta(seconds=(
                    authority["window_seconds"] - watch_valid_from_late))
            ).isoformat(),
            "guarantee_valid_until": (
                clock.now() + timedelta(hours=1, seconds=(
                    authority["window_seconds"] - watch_valid_until_short))
            ).isoformat(),
        })
        watch = sign_synthetic_external_owner(
            watch, signer if watch_owner_is_reviewer else external_owner)
        if watch_owner_is_reviewer:
            reviewer_public = signer.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            monkeypatch.setattr(
                budget_module, "PINNED_EXTERNAL_PPI_CONSUMPTION_OWNER_KEY_SHA256",
                hashlib.sha256(reviewer_public).hexdigest())
        if watch_owner_signature_tamper:
            signature = watch["owner_signature_b64"]
            watch["owner_signature_b64"] = (
                signature[:10] + ("A" if signature[10] != "A" else "B") +
                signature[11:])
    provider_name = "a667-provider-synthetic.json"
    watch_name = "a667-watch-synthetic.json"
    authority.update({
        "quota_scope_sha256": account_scope,
        "provider_evidence_file": provider_name,
        "watch_evidence_file": watch_name,
        "provider_evidence_sha256": _write_material(tmp_path / provider_name, provider),
        "watch_evidence_sha256": _write_material(tmp_path / watch_name, watch),
    })
    authority_path.write_text(json.dumps(sign_synthetic_authority(authority, signer),
                          sort_keys=True), encoding="utf-8")
    authority_path.chmod(0o600)
    return environ, authority_path


def _store(tmp_path):
    store = PaperStore(str(tmp_path / "a667-synthetic-paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    return store


def test_random_64hex_provider_and_watch_hashes_do_not_grant_authority(wire, tmp_path, monkeypatch):
    clock, _requests, _ = wire
    store = _store(tmp_path)
    environ, signer = static_authority_file(tmp_path, monkeypatch, clock)
    path = Path(environ["POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH"])
    payload = json.loads(path.read_text())
    payload.pop("signer_public_key_b64")
    payload.pop("signature_b64")
    payload["provider_evidence_sha256"] = "a" * 64
    payload["watch_evidence_sha256"] = "b" * 64
    path.write_text(json.dumps(sign_synthetic_authority(payload, signer), sort_keys=True))
    # Old code treated a reviewed signature covering random 64-hex strings
    # as proof of real underlying provider and Watch source evidence.
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())


@pytest.mark.parametrize("missing", ["a667-provider-synthetic.json", "a667-watch-synthetic.json"])
def test_absent_evidence_artifact_denies_before_budget_boot(wire, tmp_path, monkeypatch, missing):
    clock, _requests, _ = wire
    store = _store(tmp_path)
    environ, _authority = _signed_with_material(tmp_path, monkeypatch, clock)
    (tmp_path / missing).unlink()
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())


def test_signed_authority_does_not_override_provider_receipt_digest_mismatch(wire, tmp_path, monkeypatch):
    clock, _requests, _ = wire
    store = _store(tmp_path)
    environ, _authority = _signed_with_material(tmp_path, monkeypatch, clock)
    watch = tmp_path / "a667-watch-synthetic.json"
    original = json.loads(watch.read_text())
    original["global_reserve"] += 1
    _write_material(watch, original)  # signer still commits old SHA256
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())


@pytest.mark.parametrize("change", [
    {"watch_scope": "f" * 64},
    {"provider_scope": "f" * 64},
    {"watch_retries": False},
    {"watch_age_seconds": 19 * 3600},
    {"provider_age_seconds": 19 * 3600},
    {"provider_current_delta": 1},
])
def test_signed_material_scope_retry_and_freshness_require_real_agreement(
    wire, tmp_path, monkeypatch, change,
):
    clock, _requests, _ = wire
    store = _store(tmp_path)
    environ, _authority = _signed_with_material(tmp_path, monkeypatch, clock, **change)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())


def test_signed_material_filename_alias_is_denied_without_reading_outside_authority_dir(
    wire, tmp_path, monkeypatch,
):
    clock, _requests, _ = wire
    store = _store(tmp_path)
    environ, authority_path = _signed_with_material(tmp_path, monkeypatch, clock)
    payload = json.loads(authority_path.read_text())
    # A malicious signed authority cannot redirect evidence reads to another
    # private resource. This receipt is intentionally invalid and offhost.
    _public = payload.pop("signer_public_key_b64")
    _signature = payload.pop("signature_b64")
    _environ, signer = static_authority_file(tmp_path, monkeypatch, clock)
    payload["provider_evidence_file"] = "../untrusted-provider.json"
    authority_path.write_text(json.dumps(sign_synthetic_authority(payload, signer), sort_keys=True))
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        static_shared_capacity_state(store.path, environ, as_of=clock.now())


def test_material_changed_between_lease_acquire_and_start_revokes_send(
    wire, tmp_path, monkeypatch,
):
    clock, requests_seen, _ = wire
    store = _store(tmp_path)
    environ, _authority = _signed_with_material(tmp_path, monkeypatch, clock)
    budget = RuntimePPIBudget(store.path,
        RuntimeCapacityController(store.path, environ=environ,
                                  policy=deepcopy(DEFAULT_POLICY)), clock=clock.now)
    admitted = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert admitted["allowed"] and admitted["lease"]
    watch = tmp_path / "a667-watch-synthetic.json"
    body = json.loads(watch.read_text())
    body["global_reserve"] += 1
    _write_material(watch, body)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        budget.start(admitted["lease"])
    assert not requests_seen


def test_consistent_synthetic_material_keeps_existing_exit_and_lower_lease_contract(
    wire, tmp_path, monkeypatch,
):
    clock, _requests_seen, _ = wire
    store = _store(tmp_path)
    environ, _authority = _signed_with_material(tmp_path, monkeypatch, clock)
    state = static_shared_capacity_state(store.path, environ, as_of=clock.now())
    assert state["status"] == "APPROVED_STATIC_SHARED"
    assert exit_capacity_contract(state, opened_count=0)["status"] == "READY"
    # The existing A586 fake-wire suites separately prove that approved
    # material continues to fund signed static lower and EXIT leases.

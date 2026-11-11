"""A586: an expected global budget cannot silently become an unleased PPI sender.

All HTTP in these tests runs exclusively through the in-memory A375 fake wire:
no broker credentials, provider network, host filesystem or production sidecar.
"""

from copy import deepcopy
from datetime import timedelta
import base64
import hashlib
import json
from pathlib import Path

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import rc6_ppi_global_budget as budget_module
from bd_ppi_readonly_guard import ReadOnlyTransportGuard
from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import (
    BudgetBackpressure, RuntimePPIBudget, budget_from_environment,
)
from tests.test_rc6_capacity_promotion import approved, controller
from tests.test_rc6_ppi_capacity_benchmark import wire


BOOK_URL = "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book"


def sign_synthetic_authority(payload, signer):
    """CI-only key; no material retained after test or used for runtime trust."""
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {**payload, "signer_public_key_b64": base64.b64encode(public).decode(),
            "signature_b64": base64.b64encode(signer.sign(raw)).decode()}


def sign_synthetic_external_owner(payload, signer):
    """Independent CI-only owner attestation, NOT an actual Watch hard cap."""
    signed = sign_synthetic_authority(payload, signer)
    signed["owner_signer_public_key_b64"] = signed.pop("signer_public_key_b64")
    signed["owner_signature_b64"] = signed.pop("signature_b64")
    return signed


def static_authority_file(tmp_path, monkeypatch, clock, *, opened=False,
                          shared_book=2, watch_book=1, release_match=True,
                          return_external_owner=False):
    """Synthetic approval for fake-wire only; no real provider quota claim."""
    # Predeploy passes a 40-hex Git commit in POROTA_BUILD_SHA, never 64-hex SHA256.
    release = "c" * 40
    paper = {"current": 2, "book": shared_book, "intraday": 1}
    watch = {"current": 1, "book": watch_book, "intraday": 1}
    provider = {name: paper[name] + watch[name] for name in paper}
    # A667: the fixture mimics independent, bounded provider/Watch material;
    # these are invented COUNTS FOR TESTS ONLY, never production PPI quotas.
    account_scope = "e" * 64  # opaque synthetic quota-scope pseudonym
    window = 15 if opened else 30
    # The external owner is *not* the quota reviewer; both keys are ephemeral
    # and are deliberately unrelated to PPI or a real Watch installation.
    external_owner = Ed25519PrivateKey.generate()
    external_public = external_owner.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(budget_module,
        "PINNED_EXTERNAL_PPI_CONSUMPTION_OWNER_KEY_SHA256",
        hashlib.sha256(external_public).hexdigest())
    material = {
        "a586-provider-synthetic.json": {
            "schema": budget_module.STATIC_PROVIDER_EVIDENCE_SCHEMA,
            "quota_scope_sha256": account_scope,
            "reviewed_at": clock.now().isoformat(),
            "window_seconds": window,
            "endpoint_limits": provider, "global_limit": sum(provider.values()),
        },
        "a586-watch-synthetic.json": sign_synthetic_external_owner({
            "schema": budget_module.STATIC_WATCH_EVIDENCE_SCHEMA,
            "quota_scope_sha256": account_scope,
            "reviewed_at": clock.now().isoformat(),
            "window_seconds": window,
            "endpoint_reserve": watch, "global_reserve": sum(watch.values()),
            "retries_included": True,
            "window_basis": budget_module.STATIC_WATCH_WINDOW_BASIS,
            "external_callers_covered": budget_module.STATIC_WATCH_COVERAGE,
            "max_endpoint_wire_attempts": dict(watch),
            "max_global_wire_attempts": sum(watch.values()),
            "hard_cap_kind": budget_module.STATIC_WATCH_HARD_CAP_KIND,
            "guarantee_valid_from": (clock.now() - timedelta(seconds=window)).isoformat(),
            "guarantee_valid_until": (
                clock.now() + timedelta(hours=1, seconds=window)).isoformat(),
        }, external_owner),
    }
    material_digests = {}
    for name, item in material.items():
        content = json.dumps(item, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        path = tmp_path / name
        path.write_bytes(content)
        path.chmod(0o600)
        material_digests[name] = hashlib.sha256(content).hexdigest()
    content = {
        "schema": budget_module.STATIC_SHARED_SCHEMA,
        "approved": True, "mode_safety": "PAPER_SHADOW_ONLY",
        "max_parallel_requests": 1, "issued_at": clock.now().isoformat(),
        "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
        "release_git_sha": release,
        "quota_scope_sha256": account_scope,
        "provider_evidence_file": "a586-provider-synthetic.json",
        "watch_evidence_file": "a586-watch-synthetic.json",
        "provider_evidence_sha256": material_digests["a586-provider-synthetic.json"],
        "watch_evidence_sha256": material_digests["a586-watch-synthetic.json"],
        "window_seconds": 15 if opened else 30,
        "provider_endpoint_limits": provider,
        "watch_external_endpoint_reserve": watch,
        "paper_shared_endpoint_limits": paper,
        "provider_global_limit": sum(provider.values()),
        "watch_external_global_reserve": sum(watch.values()),
        "paper_shared_global_limit": sum(paper.values()),
    }
    signer = Ed25519PrivateKey.generate()  # ephemeral CI-only, A620 test pattern
    path = tmp_path / "static-approved-synthetic.json"
    path.write_text(json.dumps(sign_synthetic_authority(content, signer), sort_keys=True),
                    encoding="utf-8")
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(budget_module, "PINNED_STATIC_SHARED_SIGNER_KEY_SHA256",
        hashlib.sha256(public).hexdigest())
    environ = {budget_module.STATIC_SHARED_AUTHORITY_ENV: str(path),
        "POROTA_BUILD_SHA": release if release_match else "d" * 40}
    if return_external_owner:
        return environ, signer, external_owner
    return environ, signer


@pytest.mark.parametrize("priority", ["DISCOVERY", "EXIT_CRITICAL"])
def test_approved_intent_without_authority_denies_before_any_provider_send(
    wire, tmp_path, priority,
):
    clock, provider_calls, _ = wire
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = "APPROVED"
    authority = RuntimeCapacityController(environ={}, policy=policy)
    state = authority.state(clock.now())
    assert state["mode"] == "APPROVED"
    assert state["status"] != "APPROVED_DYNAMIC"

    runtime = RuntimePPIBudget(tmp_path / "paper.sqlite", authority, clock=clock.now)
    sidecar = runtime.path
    assert not sidecar.exists()
    guard = ReadOnlyTransportGuard(budget=runtime, consumer="SCANNER").install()
    try:
        with guard.read_scope(priority=priority):
            with pytest.raises(BudgetBackpressure, match="PPI_BUDGET_LEASE_UNVERIFIED"):
                requests.get(BOOK_URL)
        assert guard.calls_allowed == 0
        assert not provider_calls
        assert runtime.budget is None
        assert not sidecar.exists()
    finally:
        guard.restore()


@pytest.mark.parametrize("mode", ["OFF", "SHADOW"])
def test_off_shadow_with_absent_shared_budget_never_sends_market_get(
    wire, tmp_path, monkeypatch, mode,
):
    clock, provider_calls, _ = wire
    policy = deepcopy(DEFAULT_POLICY)
    policy["mode"] = mode
    authority = RuntimeCapacityController(environ={}, policy=policy)
    monkeypatch.setattr(
        "rc6_dynamic_universe.promotion.capacity_controller_from_environment",
        lambda _database: authority,
    )
    database = tmp_path / "paper.sqlite"
    assert budget_from_environment(database=database, clock=clock.now) is None

    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="PPI_SHARED_BUDGET_UNAVAILABLE"):
            requests.get(BOOK_URL)
        assert guard.calls_allowed == 0
        assert not provider_calls
        assert not (database.parent / "artifacts" / database.name /
                    "ppi-budget" / "global.sqlite").exists()
    finally:
        guard.restore()

    # The explicit RuntimePPIBudget path previously advertised allowed=True
    # with lease=None. It must not offer a second way around the shared ledger.
    direct_runtime = RuntimePPIBudget(database, authority, clock=clock.now)
    assert direct_runtime.acquire("book") == {
        "allowed": False, "reason": "PPI_BUDGET_LEASE_UNVERIFIED", "lease": None}
    direct_guard = ReadOnlyTransportGuard(budget=direct_runtime, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="PPI_BUDGET_LEASE_UNVERIFIED"):
            requests.get(BOOK_URL)
        assert direct_guard.calls_allowed == 0
        assert not provider_calls
        assert not direct_runtime.path.exists()
    finally:
        direct_guard.restore()


def test_genuine_approved_global_budget_keeps_durable_book_lease(wire, tmp_path, monkeypatch):
    from be_paper_engine import PaperStore

    clock, provider_calls, _ = wire
    validated = approved(wire)  # capacity evidence from in-memory fake SDK
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    runtime = RuntimePPIBudget(store.path, controller(validated), clock=clock.now)
    assert not runtime.path.exists()

    admitted = []
    original_acquire = runtime.acquire

    def capture_admission(*args, **kwargs):
        result = original_acquire(*args, **kwargs)
        admitted.append(result)
        return result

    monkeypatch.setattr(runtime, "acquire", capture_admission)
    provider_calls.clear()  # excludes capacity fixture's fake SDK measurements
    guard = ReadOnlyTransportGuard(budget=runtime, consumer="EXIT_READER").install()
    try:
        with guard.read_scope(priority="EXIT_CRITICAL"):
            result = requests.get(BOOK_URL)
        assert result.status_code == 200
        assert len(provider_calls) == 1
        assert len(admitted) == 1
        assert admitted[0]["allowed"] is True
        assert isinstance(admitted[0]["lease"], str) and admitted[0]["lease"]
        assert runtime.path.is_file()
        assert runtime.path.stat().st_mode & 0o777 == 0o600
        assert runtime.budget.metrics()["by_endpoint"]["book"]["used"] == 1
    finally:
        guard.restore()


def test_static_contract_unpinned_or_wrong_release_never_bootstraps_or_gets(wire, tmp_path, monkeypatch):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, signer = static_authority_file(tmp_path, monkeypatch, clock)
    authority = RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY))
    runtime = RuntimePPIBudget(store.path, authority, clock=clock.now)
    sidecar = runtime.path
    monkeypatch.setattr(budget_module, "PINNED_STATIC_SHARED_SIGNER_KEY_SHA256", None)
    guard = ReadOnlyTransportGuard(budget=runtime, consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_SIGNER_UNPINNED"):
            requests.get(BOOK_URL)
        assert not provider_calls and not sidecar.exists()
    finally:
        guard.restore()

    environ["POROTA_BUILD_SHA"] = "d" * 40
    assert not runtime.budget
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(budget_module, "PINNED_STATIC_SHARED_SIGNER_KEY_SHA256",
        hashlib.sha256(public).hexdigest())
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        runtime.acquire("book")
    assert not sidecar.exists()


def test_static_signed_release_identity_matches_build_once_workflow():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/porota-predeploy-v2.yml").read_text()
    dockerfile = (root / "Dockerfile").read_text()
    assert "CANDIDATE_SHA: " + "$" + "{{ github.event.pull_request.head.sha }}" in workflow
    assert "POROTA_BUILD_SHA=" + "$" + "{{ env.CANDIDATE_SHA }}" in workflow
    assert "ARG POROTA_BUILD_SHA=" in dockerfile
    assert "ENV POROTA_BUILD_SHA=" + "$" + "{POROTA_BUILD_SHA}" in dockerfile
    assert budget_module.STATIC_SHARED_SCHEMA.endswith("_V2")


@pytest.mark.parametrize("mutation", [
    "old_64_field", "git_sha_64", "build_env_64", "artifact_digest_unattested",
    "git_sha_uppercase", "git_sha_short", "wrong_git_sha", "legacy_schema",
])
def test_signed_release_identity_incompatible_forms_fail_without_provider_wire(
    wire, tmp_path, monkeypatch, mutation,
):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, signer = static_authority_file(tmp_path, monkeypatch, clock)
    authority_path = tmp_path / "static-approved-synthetic.json"
    authority = json.loads(authority_path.read_text())
    payload = {key: value for key, value in authority.items()
               if key not in {"signer_public_key_b64", "signature_b64"}}
    if mutation == "old_64_field":
        payload.pop("release_git_sha")
        payload["release_sha256"] = "c" * 64
    elif mutation == "git_sha_64":
        payload["release_git_sha"] = "c" * 64
        environ["POROTA_BUILD_SHA"] = "c" * 64
    elif mutation == "build_env_64":
        environ["POROTA_BUILD_SHA"] = "c" * 64
    elif mutation == "artifact_digest_unattested":
        payload["release_artifact_sha256"] = "e" * 64
    elif mutation == "git_sha_uppercase":
        payload["release_git_sha"] = "A" * 40
        environ["POROTA_BUILD_SHA"] = "A" * 40
    elif mutation == "git_sha_short":
        payload["release_git_sha"] = "c" * 39
        environ["POROTA_BUILD_SHA"] = "c" * 39
    elif mutation == "wrong_git_sha":
        environ["POROTA_BUILD_SHA"] = "d" * 40
    elif mutation == "legacy_schema":
        payload["schema"] = "RC6_PPI_STATIC_SHARED_AUTHORITY_V1"
    authority_path.write_text(
        json.dumps(sign_synthetic_authority(payload, signer), sort_keys=True),
        encoding="utf-8",
    )
    runtime = RuntimePPIBudget(store.path,
        RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY)),
        clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        runtime.acquire("current")
    assert not runtime.path.exists() and not provider_calls


def test_two_static_off_readers_share_real_lease_and_watch_deduction(wire, tmp_path, monkeypatch):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = static_authority_file(tmp_path, monkeypatch, clock)
    ctl = RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY))
    first = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    second = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    guards = [ReadOnlyTransportGuard(budget=first, consumer="SCANNER").install()]
    try:
        # The same durable path is shared across independent reader objects.
        assert requests.get(BOOK_URL).status_code == 200
    finally:
        guards.pop().restore()
    guards.append(ReadOnlyTransportGuard(budget=second, consumer="SCALPING").install())
    try:
        assert requests.get(BOOK_URL).status_code == 200
        with pytest.raises(BudgetBackpressure):
            requests.get(BOOK_URL)
        assert len(provider_calls) == 2
        assert first.path == second.path and first.path.is_file()
        assert first.budget.metrics()["by_endpoint"]["book"]["used"] == 2
        assert first.budget.policy["authority"] == budget_module.STATIC_SHARED_AUTHORITY
    finally:
        guards.pop().restore()


def test_pinned_authority_change_after_acquire_blocks_start_without_wire(wire, tmp_path, monkeypatch):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = static_authority_file(tmp_path, monkeypatch, clock)
    runtime = RuntimePPIBudget(store.path,
        RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY)), clock=clock.now)
    admitted = runtime.acquire("book", consumer="SCANNER")
    assert admitted["allowed"] and admitted["lease"]
    path = tmp_path / "static-approved-synthetic.json"
    authority = json.loads(path.read_text())
    authority["paper_shared_global_limit"] += 1  # forged payload, old signature
    path.write_text(json.dumps(authority, sort_keys=True), encoding="utf-8")
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        runtime.start(admitted["lease"])
    assert not provider_calls


def test_static_contract_with_insufficient_external_watch_reservation_is_denied(
    wire, tmp_path, monkeypatch,
):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, signer = static_authority_file(tmp_path, monkeypatch, clock)
    path = tmp_path / "static-approved-synthetic.json"
    authority = json.loads(path.read_text())
    authority["watch_external_endpoint_reserve"]["book"] += 1
    payload = {key: value for key, value in authority.items()
               if key not in {"signer_public_key_b64", "signature_b64"}}
    path.write_text(json.dumps(sign_synthetic_authority(payload, signer), sort_keys=True),
                    encoding="utf-8")
    runtime = RuntimePPIBudget(store.path,
        RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY)), clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        runtime.acquire("book")
    assert not runtime.path.exists() and not provider_calls


def test_valid_signature_from_wrong_reviewer_does_not_become_a_trust_pin(
    wire, tmp_path, monkeypatch,
):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = static_authority_file(tmp_path, monkeypatch, clock)
    path = tmp_path / "static-approved-synthetic.json"
    original = json.loads(path.read_text())
    body = {key: value for key, value in original.items()
            if key not in {"signer_public_key_b64", "signature_b64"}}
    path.write_text(json.dumps(sign_synthetic_authority(body, Ed25519PrivateKey.generate()),
                               sort_keys=True), encoding="utf-8")
    runtime = RuntimePPIBudget(store.path,
        RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY)), clock=clock.now)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        runtime.acquire("current")
    assert not runtime.path.exists() and not provider_calls


def test_signed_authority_expires_without_extension_or_more_gets(
    wire, tmp_path, monkeypatch,
):
    from be_paper_engine import PaperStore
    clock, provider_calls, _ = wire
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    environ, _ = static_authority_file(tmp_path, monkeypatch, clock)
    runtime = RuntimePPIBudget(store.path,
        RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY)), clock=clock.now)
    first = runtime.acquire("book")
    assert first["allowed"] and first["lease"]
    runtime.start(first["lease"])
    runtime.finish(first["lease"])
    clock.advance(3601)
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_AUTHORITY_UNVERIFIED"):
        runtime.acquire("book")
    assert not provider_calls


def test_declared_allowed_without_lease_never_passes_http_adapter(wire):
    _, provider_calls, _ = wire

    class NullLease:
        def acquire(self, *_args, **_kwargs):
            return {"allowed": True, "lease": None}

        def start(self, *_args):
            pytest.fail("NULL_LEASE_MUST_NOT_START")

    guard = ReadOnlyTransportGuard(budget=NullLease(), consumer="SCANNER").install()
    try:
        with pytest.raises(BudgetBackpressure, match="PPI_BUDGET_LEASE_UNVERIFIED"):
            requests.get(BOOK_URL)
        assert not provider_calls and guard.calls_allowed == 0
    finally:
        guard.restore()


def test_static_watch_floor_and_real_opened_exit_floor_precede_scanner(wire, tmp_path, monkeypatch):
    from tests.test_issue465_budget_adversarial import native_opened_store
    clock, provider_calls, _ = wire
    store, _ = native_opened_store(tmp_path, clock, monkeypatch, count=1)
    environ, _ = static_authority_file(tmp_path, monkeypatch, clock,
                                    opened=True, shared_book=5)
    ctl = RuntimeCapacityController(environ=environ, policy=deepcopy(DEFAULT_POLICY))
    runtime = RuntimePPIBudget(store.path, ctl, clock=clock.now)
    # 15-second window / 5-second critical cadence = 3 EXIT Book slots.
    policy = runtime._current().policy
    assert policy["priority_reserves"]["EXIT_CRITICAL"]["book"] == 3
    assert policy["endpoint_limits"]["book"] == 5  # net of external Watch
    scanner = runtime.acquire("book", consumer="SCANNER")
    assert scanner["allowed"] and scanner["lease"], scanner
    runtime.start(scanner["lease"])
    runtime.finish(scanner["lease"])
    denied = runtime.acquire("book", consumer="SCANNER")
    assert not denied["allowed"] and denied["reason"] == "PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE"
    # EXIT's reserved capacity is not lendable to speculative scanners.
    result = runtime.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert result["allowed"] and result["lease"]
    assert not provider_calls  # No PPI requests in pure budget fixture.

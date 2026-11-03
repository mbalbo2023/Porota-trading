# A620 — PAPER decision freeze and independent post-COMMIT observer

**State:** code/test in isolated PR only. Never declare this a verified PPI feed, live decision attestation, PAPER fill or runtime deployment. Scope `be_paper_engine.PaperStore.record_decision` / migration and `rc6_paper_causal_decision_receipt.py`; independent of A619/#897, FI SELL, importer and PPI Watch.

## ERROR → RCA

The original PAPER engine logs a decision to `paper_decisions`, then writes the fuller `decision_evidence_snapshots` after technical/economic/AI/patrimonial gates, potentially *after* a simulated admission. The snapshot's `captured_at` often equals **`q.observed_at`**, which is only the native quote receipt, not when a decision was persisted and known to another process. Reconstructing a past decision from this clock and a later native Book2 is subject to look-ahead. A SHA-256 merely checks local bytes; it cannot act as a decision signature or PPI broker signature.

## FIX → GUARD

1. For **BUY/SELL only**, `PaperStore.record_decision()` inserts an immutable `rc6.paper-decision-causal-freeze.v1` payload **inside the exact SQLite transaction** of the original `paper_decisions` row. It binds strategy/version, five-key identity, side, score, raw frozen `paper_decisions` hash, quote clocks/top levels and a nullable `ppi_book1_event_sha256_claim` and `release_sha256_claim`. COLD/HOLD/VETO traffic creates no freeze and no extra storage. The `prepared_before_commit_at` field is explicitly *not* a decision-known timestamp.
2. New table `paper_causal_decision_freezes` has a unique decision key and UPDATE/DELETE denial triggers. A crash before COMMIT rolls back both decision and freeze. A crash after COMMIT leaves a durable unsigned freeze; nobody may backdate or fill it.
3. The **separate observer API** `attest_after_commit(db_path, decision_key, signing_key_path, expected_release_sha256)` opens a **new SQLite connection**, reads the already committed freeze and decision, checks hashes and identity/clock/release binding, then samples the real UTC `decision_known_at` *after seeing the COMMIT*. It signs canonical receipt bytes with a **separate Ed25519 private RAW key**, rejects world-readable or symlink keys, stores a sequenced immutable anti-replay receipt (previous receipt hash). If it crashes before this second COMMIT, no attestation exists; a retry obtains a new later timestamp, never an invented past time. The PAPER engine never imports signing material or calls this observer.
4. `verify_signed_receipt` rejects signatures if its **separately reviewed release trust anchor pins are absent**. Both `PINNED_DECISION_SIGNER_KEY_SHA256` and `PINNED_RELEASE_SHA256` deliberately ship as `None`; caller-provided SHA strings, signing keys or self-signed receipts cannot bootstrap trust. Tests inject synthetic pins only in their isolated process, confirming cryptographic verification works, and separately check the default unconfigured fail-closed condition.
5. Even with synthetically verified decision timing, the verifier always returns `ppi_source_capture_attested=false`, `unit_contract_verified=false`, `tariff_contract_verified=false`, `fill_accredited=false`, `real_orders_sent=0`. Ed25519 here signs **our decision observer receipt**, never a quote purportedly signed by PPI. A separate authenticated PPI collector capture attestation is still needed.

The durable known clock for future A619/Book2 PIT must be the independently signed `decision_known_at`, **not** `q.observed_at`, the snapshot's `captured_at`, a pre-COMMIT timestamp, or a caller-selected SHA. Future validated Book2 predicate: `native_book_at > max(decision_at, independently_verified_decision_known_at) + latency`; matching five-key, known clock and release-bound PPI capture evidence still required.

## Tests / evidence

`tests/test_rc6_a620_paper_causal_decision_receipt.py` uses synthetic quotes/keys and temporary SQLite files only. It covers same-transaction freeze, rollback on synthetic crash, recovery after unsigned freeze, genuine observer clock strictly later than commit preparation, Ed25519 signature/pin mismatch, forged key and release, missing release/env pin fail-closed, private key filesystem permissions, duplicate decision/receipt anti-replay, immutable triggers, clock in the future, **post-admission legacy snapshot with old quote `captured_at`**, and HOLD no storage fanout.

```bash
python -m unittest discover -s tests -p 'test_rc6_a620_paper_causal_decision_receipt.py' -v
python -m compileall -q be_paper_engine.py rc6_paper_causal_decision_receipt.py
```

The tests do **not** contact PPI, read droplet runtime DB, sign real receipts, validate producer credentials, or execute orders.

## Remaining critical stoppers / next owner scopes

- **Independent signer runtime**: this PR supplies the reusable API but does not provision keys, configure separately reviewed release pins, schedule an observer, or establish authenticated runtime `decision_known_at`. Without those, status remains NO_VERIFICADO. GitHub Actions/droplet control must remain the operational plane; no manual SSH required for this code review.
- **PPI capture attestation**: another scope must adapt the authenticated collector to validate TLS/endpoint/request five-key, preserve native Book bytes/date/observed clock/hash, and sign *our capture receipt* with a separate trusted key. Do not claim PPI's REST API cryptographically signs quotes. The `book1_event_sha256_claim` frozen here is a claim only until that verification exists.
- **PIT binding**: A619's OFFHOST Book1+Book2 guard must consume the independently verified decision-known receipt and PPI capture artifact before elevating a *causal source witness*. Still no fill without quantity units, tariffs, venue/settlement, cash/budget and EXIT authority.
- **FI SELL**: `bm_exit_supervisor` persists its own `paper_exit_intents`; it does *not* call `PaperStore.record_decision`. FI owner must integrate a separate atomic EXIT freeze and call this observer API after EXIT_DUE COMMIT. Do not confuse a BUY freeze with a FI SELL decision.
- **Retention #478**: treat the two new immutable tables as pinned evidence in the future 365-day policy, without unsafe purge or breaking the WAL chain.

No Predeploy/Deploy is authorized. No source or family can be declared READY from this change alone.

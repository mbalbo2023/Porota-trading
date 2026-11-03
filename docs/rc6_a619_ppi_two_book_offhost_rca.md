# A619 — PPI native Book1 + decision + Book2 causal guard (OFFHOST)

**State:** DESARROLLADO/EN_GITHUB only after isolated PR; not a runtime or provider verification. **Owner:** A619 PIT causal diagnostic only. **Mode:** PAPER/SHADOW, zero broker routes. No Predeploy/Deploy, no PPI Watch changes.

## ERROR / RCA

The pre-existing `rc6_ppi_pit_replay_bridge.py` (A597) correctly chooses the *first* native Book after `decision_at + latency`, then always returns `fill_accredited=false`. Its external decision/source SHA fields are caller claims. That bridge cannot establish what PPI Book the strategy actually knew *at decision time*. A historical Book2 alone can be misread as a valid executable fill, especially if the decision was reconstructed after seeing a favorable price.

`be_paper_engine.py` records `decision_evidence_snapshots` in SQLite, including a canonical `payload_sha256` and an in-memory `quote_used` at the gate. Crucially, `_decision_evidence_payload.captured_at` is **`q.observed_at` (the PPI quote receipt)**, *not* the time the SQLite decision was durably visible. `record_gates()` inserts a candidate's snapshot later, potentially **after** `OPENED_SIMULATED`. The `ATOMIC_PAPER_ADMISSION` phase may set `captured_at=recorded_at`, already post-admission; neither clock authenticates *decision receipt known before Book2*. The local digest detects accidental corruption; it **does not** authenticate the producer, grant decision authority, sign a decision, or verify external PPI source records. The PPI Book hash chain is likewise a local integrity witness, **not** a signature from PPI.

## FIX / GUARDS

`rc6_ppi_pit_two_book_guard.py` is an independent **read-only OFFHOST diagnostic** layered on the existing A597 bridge; it does not duplicate source normalization, place orders, change admission, or add a new fill route.

1. Read a single decision snapshot from a separately exported SQLite copy via `mode=ro` and `PRAGMA query_only`; rehash the stored payload and validate decision schema, 5-key identity, action and PAPER-only mode.
2. Require matching **claimed** `decision_at` in snapshot and runtime lineage with timezone-aware timestamps; validate capture-phase semantics without treating `captured_at` as persistent decision availability. Demand the latest native PPI Book1 both `native_book_at <= decision_at` and `known_at <= decision_at`, within an explicit freshness bound. Bind Book1's original top-of-book prices, sizes, native time and receipt time to the exact `quote_used` stored in the snapshot.
3. Reuse `causal_book_witness()` for the *first* native PPI Book2 strictly after `decision_at + latency`. A later favorable Book cannot rescue zero top depth in the first Book2. Require same 5-key, same ART session, monotonic native clocks, verified local journal chain, and unchanged journal tip between both reads.
4. Treat every FCI as **NAV/cutoff/settlement authority required**; exchange Book IOC logic is not applicable to FCI subscriptions/redemptions.
5. On valid Book1/Book2 relative to **claimed** decision time, set only `market_clock_pair_relative_to_claimed_decision=true`. Preserve `two_book_clock_witness=false`, `decision_receipt_known_at_verified=false`, `decision_receipt_known_at=null`, `fill_accredited=false`, `family_ready=false`, `decision_signature_verified=false`, `decision_origin_independently_verified=false`, `source_artifact_independently_verified=false`, `quantity_unit_verified=false`, `price_unit_verified=false` and `tariff_contract_verified=false` **unconditionally**. The actual decision could have materialized after Book2. No caller-controlled `verified=true` shortcuts.

For a local exported database and native journal, the diagnostic CLI is:

```bash
python -m rc6_ppi_pit_two_book_guard \
  --journal <OFFHOST_PPI_JOURNAL_COPY> \
  --decision-db <OFFHOST_SQLITE_COPY> \
  --decision-key <EXACT_DECISION_KEY> \
  --family ACCIONES --symbol GGAL --market BYMA --currency ARS \
  --settlement A-24HS --latency 1
```

Only public bookkeeping hashes/timestamps and reason codes appear in the JSON result; no provider credentials or raw orders. Run in an isolated offhost environment with authentic, immutable copies. Never run an ad hoc mutating command on the droplet. The CLI does not fetch PPI data itself.

## TEST / CI

`tests/test_rc6_a619_ppi_two_book_guard.py` uses synthetic native Books and a synthetic SQLite copy. Regression cases cover Book1 without Book2; strict decision+latency boundary; first Book2 vs later favorable prices; zero native depth; journal chain consistency; exact price/size Book1 binding; missing or inconsistent identity; stale Book1 and cross-session Book2; tampered snapshot hash; *forged rehashed snapshot that still cannot authenticate a signature*; **post-Book2 ATOMIC_PAPER_ADMISSION snapshot** and invalid NATIVE `captured_at` (look-ahead regression); FCI NAV isolation; and proof that files are unchanged after diagnostic. The module has no provider GET or execution route.

## Next FIX — genuine decision-availability producer, separate WRITE_OWNER

This is a **proposed next code scope, not implemented or accredited by A619**. It must be coordinated with the FI exit owner because it touches the shared `be_paper_engine.py` producer:

1. At `PaperStore.record_decision`, atomically insert the original `paper_decisions` row **and a new immutable `rc6.decision-causal-freeze.v1` payload** in the same SQLite transaction **for candidate BUY and SELL, before later gate/entry fill**. Freeze `decision_key`, action, strategy/release SHA, full five-key, decision timestamp, input quote and PPI Book1 journal event SHA, pending quantity-unit/tariff authority refs. Recompute the freeze SHA from canonical bytes; repeated decision keys with different payloads must fail, never `INSERT OR REPLACE`.
2. After that transaction **commits**, an independently configured observer opens a fresh read-only connection and verifies the frozen row is durably visible, PPI Book1 known before decision, and hash content. It produces `rc6.decision-causal-receipt.v1` containing freeze SHA, `observer_seen_at` (trusted timezone-aware clock **after COMMIT visibility**), signer key ID, release binding, exact identity and chain sequence. Sign canonical bytes using an independent Ed25519 key whose public trust anchor is pinned in the controlled release/host (never passed by caller; never a claimed SHA masquerading as signature). Append to an immutable, fsynced evidence journal and retain observer acknowledgment. CI must reject missing, recycled or divergent key IDs and unsigned/late receipts.
3. External verifier must check pinned key, signature, source artifact/digest, release, monotonic observer sequence, snapshot and PPI Book1 binding, plus `observer_seen_at >= decision_at`, then use **`max(decision_at, independently_verified_observer_seen_at) + latency`** as Book2 cutoff. Ignore the legacy `captured_at` for this purpose. Any missing independent trust anchor, observer clock, signature, Book2, unit/tariff contract, EXIT budget/settlement or family execution validator remains `NO_VERIFICADO`, never a PAPER fill.
4. FCI needs an analogous independently signed NAV publication/cutoff/settlement receipt, not Book2/IOC. FI BONOS/ON SELL must add nominal-to-cash units, venue, settlement and verified tariffs in its own owner scope.

This plan prevents decision reconstruction **after** seeing a favorable Book2; it does not require PPI Watch modifications or live orders. A619 does not modify the shared runtime decision writer.

Run locally:

```bash
python -m unittest discover -s tests -p 'test_rc6_a619_ppi_two_book_guard.py' -v
python -m compileall -q rc6_ppi_pit_two_book_guard.py
```

## EVIDENCE / REMAINING STOPPERS

This fixture test can only validate code behavior. Real PPI eligibility still requires independently verified producer/source artifact, a genuine decision producer with authenticated release-bound decision receipts, native PPI Book1/Book2 collected contemporaneously during the session, verified financial contract quantity and price units, fee/tariff contract, executable session/settlement, cash/budget and EXIT authority, risk, and family-specific ledger integration. For FCI replace Book2 with timestamped NAV cutoff and settlement contract. An independent verifier must be implemented outside this diagnostic before any PAPER fill can be accredited; hardcoded signatures or hash-shaped claims are forbidden.

Until then, **NO_VERIFICADO** remains the honest answer, `fills_accredited=0` for this diagnostic, and the consolidated Predeploy/Deploy must remain blocked.

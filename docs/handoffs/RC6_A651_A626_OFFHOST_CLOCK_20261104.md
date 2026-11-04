# A651-R44 — A626 OFFHOST clock chronology / 2026-11-04 ART

## Scope and release status

- Workstream: `RC6_A651_A626_OFFHOST_CLOCK_20261104`.
- Owner declaration: [#732/6638666763](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6638666763).
- Base and target: DRAFT [#778](https://github.com/mbalbo2023/Porota-trading/pull/778), `63beb5ff3e6dfbcbfe836deed406d15cd212347f`.
- Product unchanged: `7e1e744d6ca9ab30c51b5747dd4467af5ece0145`.
- Changed code: `scripts/rc6_a626_offhost_contract_gap_join.py` and scoped adversarial tests.
- No copy/host/provider/GET/order/lease, no PPI Watch change, no predeploy/merge/deploy.
- State: **EN_GITHUB** only after branch/PR creation; **DESPLEGADO/VALIDADO_RUNTIME: NO_VERIFICADO**.

## ERROR → RCA → FIX → GUARD → TEST → EVIDENCE

**ERROR:** Diagnostic `rc6_a626_offhost_contract_gap_join.py::_current_rows` insisted that `contract_evidence_v2_current.observed_at` exactly equal its immutable snapshot `observed_at`, causing a false `V2_CURRENT_SNAPSHOT_IDENTITY_MISMATCH` for legitimate repeated identical evidence.

**RCA:** Product `cp_contract_evidence_v2_hf6.py` preserves the first snapshot timestamp for exact five-key/source/hash using `INSERT OR IGNORE`; the corresponding current row UPSERT stores the latest observation. This was already fixed in the distinct host-A626 pipeline by [PR #921](https://github.com/mbalbo2023/Porota-trading/pull/921), not in the separate OFFHOST join. Source provenance/payload hash must remain exact even when timestamps differ.

**FIX/GUARD:** Compare both timezone-aware instants in UTC and permit `current >= snapshot`, reject naive, invalid or reverse chronology. Preserve literal family+ticker+market+currency+settlement, source class, snapshot pointer, and SHA256 evidence JSON hash. Never treat later current timestamp as provider fresh, causal PIT, Current/Book receipt, instrument readiness, or deployment.

**Safety labeling:** A diagnostic copy cannot prove global runtime orders. Replace ambiguous `real_orders_sent=0` output with `real_orders_sent_by_diagnostic=0` and explicit `runtime_real_orders_sent=NO_VERIFICADO`; keep `paper_auto_enabled=false`, `runtime_verified=false`, and zero GET/writes.

**TEST:** New adversarial fixtures for repeated same-hash observations, offsets UTC vs ART, reverse/naive/invalid time, unchanged source/hash adversaries, plus historical six-family and no-mutation contract. Record scoped RED→GREEN and exact remote CI in PR body; no host involvement.

## A626 physical 821 gaps, evidence boundary

The authentic single A626+A516 host-RO [run #41868630249](https://github.com/mbalbo2023/Porota-trading/actions/runs/41868630249), sanitized ZIP ID `13343922286` checksum `sha256:93bc859209f3d23ae5fc8e00de6686803e2a48f3c31c48c3e1b87e81cce1baba`, measured `2026-11-04T16:34:55Z` (13:34:55 ART): BONOS 8, OBLIGACIONES 45, OPCIONES 576, FUTUROS 166, CAUCIONES 5, FCI 21, total **821 capability gaps of 8,821 catalog-active**, and 8,000 preliminary catalog `READY_PAPER_*` rows. Among the gaps, **all 821 had zero declared nonempty per-family contract metadata** in catalog (`financial_contract_v17`, `paper_family_contract_v1`, `paper_caucion_contract_v1`), but the aggregate **does not read/hash source `evidence_json`** or disclose detailed capability reasons and cannot prove a missing provider payload. Do not turn 747 `exact_current_v2_ppi` source labels into native PPI Current/Book/PIT/fill, and do not equate 821 with non-existent instruments. Further per-instrument classification requires an independently staged, WAL-safe, immutable offhost copy with a verified custody receipt and resource envelope—or another specifically authorized bounded read-only diagnostic; neither is done here.

**Non-goal:** No change to `MAX_SOURCE_BYTES=1 GiB` in the OFFHOST copy guard. Current host-derived DB size is roughly 2.68 GB and must not be forced through this guard or copied without a separate safety/resource design and ownership. The OFFHOST script remains fail-closed for unsuitable copies.

## Distinct dependent tracks

- DRAFT [#923](https://github.com/mbalbo2023/Porota-trading/pull/923): native literal PPI five-key contract promotion guard, separate files.
- DRAFT [#924](https://github.com/mbalbo2023/Porota-trading/pull/924) and followups #925/#926: shared PPI GET budget/legacy reader guards, separate files.
- A630 PPI Current/Book still 0 GET with `SIDECAR_ABSENT` until the budget design is reviewed, deployed and validated; no budget bypass.
- PPI Watch untouched. All real-order routes must remain blocked, runtime `real_orders_sent=0` **not certified** by this diagnostic.

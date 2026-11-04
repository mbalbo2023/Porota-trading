# A652 — Consolidated OFFHOST review manifest (single DRAFT, not a release)

Date: 04-Nov-2026 ART. Role: `WRITE_OWNER` exclusive to `integration/a652-unified-review-20261104`.
**State: `DESARROLLADO_LOCAL` until GitHub branch/PR is verified; never `DESPLEGADO` or `VALIDADO_RUNTIME`.**

## Exact base and input graph

- Historical control-plane `main` at A651-R53: `9e099bc1d2cab1183872475f4e2bf8b13e28730c`; later A652 `main` is `06028322371c850a55aedf7733e6f8e3254a0ee4`. No main merge here.
- Runtime `product`: `7e1e744d6ca9ab30c51b5747dd4467af5ece0145`; unchanged here.
- Product candidate DRAFT #778: `63beb5ff3e6dfbcbfe836deed406d15cd212347f`; unchanged here.
- Integration DRAFT #929: `94c634f295089e066cec7d0c138e72e38af511f9`; **target PR base**.
- Integration DRAFT #931: `9cd1ab00dd5de151db338bdfb281f9fe50c1add2`; **exact commit used to initialize this review**, already carries upstream scoped work.

Additional original DRAFT inputs were copied by *eight individually reviewed `cherry-pick -x` commits*, never by blind merge:

| PR | Exact original head | Scoped delta carried by this review |
| --- | --- | --- |
| #933 | `ca5f1962f53406e0cbfd0db04a6858a0680789ef` | V2→catalog phase RCA documentation plus offline regression; original `26dac354`, `ca5f1962` |
| #936 | `58849d0dbab5ae9476c763128a93639bf368b9df` | Search4 + realtime3 proof-of-concept plus offline tests/docs; original `2e8d7fd2`, `1a3a5171`, `58849d0d` |
| #935 | `50d7b05115cefc0bc83315ef7a257a0c529ca1d2` | PAPER observer's env-budget bridge with narrow allowlist and failure-close tests; original `afbb0ba7`, `50d7b051` |
| #938 | `5cc19f5f085694f47bc82e22325d16610555fdf2` | Book-only scanner PAPER *exit*-supervision guard plus regressions; original `5cc19f5f` |

## Integration guard and exact content

New original input deltas are precisely these 10 paths; **zero deleted files**:

```text
M bf_production_paper_observer.py
A docs/handoffs/RC6_A652_PPI_REALTIME_SETTLEMENT_JOIN_20261104.md
A docs/handoffs/RC6_A652_SCANNER_EXIT_BOOK_GUARD_20261104.md
A docs/handoffs/RC6_A652_V2_PROJECTION_PHASE_RCA_20261104.md
M porota_mode_manager.py
A rc6_ppi_realtime_settlement_join_offhost.py
A tests/test_rc6_a652_ppi_budget_boot_wiring.py
A tests/test_rc6_a652_ppi_realtime_settlement_join_offhost.py
A tests/test_rc6_a652_scanner_exit_only_fresh_book.py
A tests/test_rc6_a652_v2_projection_phase_regression.py
```

Each subset was independently verified byte-identical to its corresponding original PR HEAD. `git diff --check` passed and the isolated checkout is clean after commit. This manifest is an eleventh, documentation-only file belonging to this review; it does not change business code or provider contracts.

The first temporary `--no-checkout` *new clone* showed about 1,534 local `D` index entries because its worktree had never been populated. That clone was **never committed or pushed**, and no existing worktree or user files were removed. A separate clean, fully checked-out worktree was used instead; all diffs were constrained to the above 10 scoped paths plus this manifest.

## Combined verification — no external activity

Run in isolated checkout without external network, real broker, live SQLite database or host operations; PAPER exit tests may use their own disposable synthetic SQLite fixture:

```bash
python3 -B -m pytest -q -o addopts='' --disable-warnings \
  tests/test_rc6_a652_offhost_contract_gap_diagnose.py \
  tests/test_rc6_ppi_option_contract_policy.py \
  tests/test_rc6_a652_v2_projection_phase_regression.py \
  tests/test_rc6_a652_ppi_realtime_settlement_join_offhost.py \
  tests/test_rc6_a652_ppi_budget_boot_wiring.py \
  tests/test_rc6_a652_scanner_exit_only_fresh_book.py
```

Result: **64 passed in 2.74s, 0 failed** (04-Nov-2026 OFFHOST). This is a combined source/unit regression, not CI, artifact validation, provider attestation or runtime approval. No provider GET, PPI Watch action, realtime subscription, GitHub Actions dispatch, Predeploy V2, Deploy V2, Docker operation or real order was executed by this review.

## Deliberately unresolved release gates

1. `SIDECAR_ABSENT`; budget authority/signature and Watch quota isolation remain `BLOQUEADO`/`NO_VERIFICADO`. Carrying a budget-selector env to the observer **does not grant budget**. Existing PPI Watch unit, lease, provider access and competing subscription capacity are not touched.
2. The 8,821 active, ~8,000 preliminary catalog READY and 821 contract gaps are aggregate A626 evidence, not executable 8,000 PAPER READY. Actual itemized economic contracts, current/Book, full provider-attested PPI five-key identity, signatures/clock, Book1→decision→Book2 PIT and real selection remain unverified.
3. A626 V2→catalog gaps (575 primitive terms present, 172 missing and 74 no exact Current) require per-item and EOD writer evidence; OPEN skip is protective against SQLite contention. A separate owner is investigating the `caucion_ledger_blocked` short-circuit in the writer. **Not included here**; do not preempt that owner or assume fixed.
4. #936 is a **pure offline synthetic identity PoC**; response `Settlement` may echo a requested subscription tenor. It cannot authorize paper instrument readiness. #938 allows only existing PAPER position exits on fresh Book without trade, never entries.
5. Full 10-family PAPER replay, real market-source captures, family contracts and ETF HOLD, workers/timers/retention, EOD receipts, 52 dashboard routes and host runtime gates remain separately tracked. In particular no true order-routing authority or production readiness is inferred from these tests.

**Promotion rule:** keep the consolidated PR `DRAFT`, stacked to #929; do not merge main/product/#778/#929 or run Predeploy V2/Deploy V2. Coordinate one fully reviewed candidate, its mandatory blocking preflight, immutable build-once artifact and post-deploy DB/log/runtime verification when a separate global DEPLOY_OWNER and explicit release gates exist.

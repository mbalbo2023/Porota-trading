# A652 — V2→catalog projection / OPEN-CLOSED phase RCA

Date: 04/11/2026. Stacked READ_ONLY/OFFHOST DRAFT continuation of [#931](https://github.com/mbalbo2023/Porota-trading/pull/931); no main/product writes or deployment.

## Physical A626 fact versus policy and runtime causality

The completed [A626](https://github.com/mbalbo2023/Porota-trading/actions/runs/41895877546) consolidated sanitized artifact `13355368533` was measured at `2026-11-04T19:20:17Z` (**16:20:17 Argentina**), during market OPEN. It contains 821 active capability gaps across six families, of which **575** have complete *primitive* contract terms in offline V2 current, **172** lack terms and **74** have no exact Current. It does **not** show genuine provider authentication or decision-time Book/PIT.

The host A626 scanner's `_inspect_gap_terms` explicitly excludes `IOL_STRUCTURED_API` option evidence when computing the `offline_terms_present_not_admitted` column. Thus **568 OPCIONES are not explainable by IOL-only series/lot fields**; their required primitive terms are represented in one or more non-IOL-labeled snapshots. It is still impossible to know how many have valid provenance, non-conflicting sources, effective terms or executable long-option contracts without per-item evidence.

Furthermore `a626_sql.families[]` reports **zero** `catalog_financial_contract_v17_declared`, `catalog_fund_terms_v1_declared`, `catalog_caucion_terms_v1_declared` or `catalog_contract_conflicts_declared` among **all 821 gaps**. There is a measured **V2 terms available → catalog contract not projected** difference for at least 575 at that instant; it is not yet proof of a persistent reconciling defect.

## Verified code mechanism: suppressed global writer in OPEN

`bf_production_paper_observer._periodic_complementary_reconcile` skips entirely when phase `OPEN` or `EQUITY_T1_READONLY` to avoid prior long `BEGIN IMMEDIATE` / SQLite interference with live PAPER readers. In `CLOSED`, after the interval, it invokes `_reconcile_complementary_catalog`, which reads V2 current, normalizes contracts, matches a verified PPI primary, and may promote catalog capability and candidate identity. Another path invokes it after a catalog refresh. None of these source facts proves a post-close run occurred.

The synthetic test `tests/test_rc6_a652_v2_projection_phase_regression.py` exercises the **actual checked-in** phase function compiled from its AST without importing broker dependencies or opening the DB. It verifies OPEN and T+1 phases skip writes and CLOSED waits for its interval then invokes the writer callback. **It does not execute the writer against runtime data.**

## Verified pure path: proof of possibility, not 568 live promotions

With explicitly **synthetic** non-IOL `PPI_OFFICIAL_DOCUMENTATION` option terms (`cash_multiplier`, quantity, expiration, underlying, strike, right), actual `rc6_contract_bridge.normalize_group` returns `NORMALIZED` and the actual `bu_instrument_catalog.complete_with_complement` creates `financial_contract_v17`, yielding a syntactic `READY_PAPER_OPTION_LONG`. This establishes that a valid V2→catalog bridge code path **exists**, so zero projection in A626 needs phase/reconciliation/individual-evidence investigation, not an automatic new executor.

The same synthetically normalized contract paired with the **documented actual shape** of `SearchInstrument` (no `settlement` emitted) remains blocked by `ppi_full_key_evidence_reason` and `quote_terms`: `PPI_FULL_KEY_NOT_PROVIDER_ATTESTED`. A syntactic READY capability does not confer PAPER permission. The positive fixture containing settlement is explicitly hypothetical; it cannot be copied to real PPI metadata.

## Exact regression proof

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -o addopts='' tests/test_rc6_a652_v2_projection_phase_regression.py tests/test_rc6_a652_offhost_contract_gap_diagnose.py tests/test_rc6_ppi_option_contract_policy.py tests/test_rc6_a651_settlement_authority.py
```

Local result: **38 passed**, 0 fail, 0 error, no external sockets, no SQLite, no real orders. CI not triggered because stacked PR base is outside Predeploy V2 PR triggers. This is deliberate until single-candidate readiness gates are satisfied.

## Remaining evidence / solution

1. **EOD receipt, READ_ONLY**: prove whether `COMPLEMENTARY_CATALOG_RECONCILIATION` emitted after CLOSE, with bounded counters (`promoted_this_run`, `pending_contract_retry`, `ready_by_family`), and candidate V2 projection status. P1 found existing A420 Action can confirm timers/systemd but does **not** inspect event/counter DB. A617 reads DB read-only for other selectors but does not support this event. No existing direct EOD proof yet; runtime EOD = **NO_VERIFICADO**. Do not rerun A626/A331 or activate PPI GET.
2. **Itemized yet sanitized OFFHOST cohort** of 575 with full custody receipt and per-key `CONTRACT_EVIDENCE_V2` reasons. Classify invalid hash/source, future-dated contract, source conflict, missing dynamic terms, identity mismatch, blocked/retry state, normalized-but-not-projected. Extend only existing approved Actions/read-only mechanism; do not copy live database or expose tickers publicly.
3. **Zero writer in OPEN** is a deliberate SQLite safety fence. For safe intraday admission, design a bounded single-writer publication or immutable staged projection with explicit lock/latency budget; regression for starvation and total COLD/HOT independence before enabling. Do not remove OPEN skip naively.
4. **PPI settlement and PIT**: neither SearchInstrument nor Current/Book response necessarily echoes all five keys. A request selector is not a provider-emitted settlement. Review a separately defined request-response binding receipt (exact request, provider session, response digest, clock/lease, sibling-negative and ambiguity checks) and independently signed `Book1 → decision → Book2`. Fail closed until provable.

**States:** A626 artifact `ARTEFACTO_VALIDADO` for its limited aggregate; synthetic replay `DESARROLLADO_LOCAL` / subsequently `EN_GITHUB` when published; end-of-day reconciliation, 575 per-item causes, PPI full identity and PAPER runtime **NO_VERIFICADO**. `CI GREEN` is not `VALIDADO_RUNTIME`.

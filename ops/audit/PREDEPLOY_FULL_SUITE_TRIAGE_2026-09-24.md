# Predeploy V2 — Full-suite triage — 2026-09-24

## Scope

This is a provisional evidence-based classification of the first full automatic test-discovery run on the exact candidate image.

- Workflow run: `36023000447`
- Candidate SHA: `e39a296a457fff466bd5977070b1518cfb8b5604`
- Droplet touched: **NO**
- Artifact integrity: **GREEN**
- Runtime files hashed: **371**
- Missing runtime files: **0**
- Missing local imports: **0**
- Runtime import smoke: **GREEN**
- Full-suite result: **51 failing cases across 24 test files**

No test has been deleted, skipped, xfailed or hidden as a result of this triage.

## Classification summary

| Class | Cases | Meaning |
|---|---:|---|
| CURRENT-CONTRACT / RECONCILIATION_REQUIRED | **12** | The test exposes a real disagreement between desired/current product contract and the deployed baseline. Do not dismiss as legacy. |
| ENVIRONMENT-HARNESS / FIXTURE | **4** | Failure is caused by the test/harness not representing the current runtime preconditions or module context. |
| LEGACY-REGRESSION / STALE EXPECTATION | **35** | Historical/textual/UI/architecture expectation has been superseded or must be explicitly reconciled with RC6. |

These labels are provisional governance labels, not permission to delete tests.

---

## CURRENT-CONTRACT / RECONCILIATION_REQUIRED — 12 cases

### Fixed income / operational universe — 12

The deployed baseline still explicitly limits runtime openings to:

```
OPERATIONAL_FAMILIES = {"ACCIONES", "CEDEARS"}
```

and `PaperBroker._open()` rejects families outside that scope.

This directly conflicts with existing tests that expect BONOS / LETRAS / ON nominal-aware PAPER behavior and broader universe/catalog participation.

Cases currently classified here:

- `tests/test_production_paper_v1634.py::test_parcial_renta_fija_conserva_nominal_lote_y_ganancia[BONOS]`
- `tests/test_production_paper_v1634.py::test_parcial_renta_fija_conserva_nominal_lote_y_ganancia[LETRAS]`
- `tests/test_production_paper_v1634.py::test_parcial_renta_fija_conserva_nominal_lote_y_ganancia[ON]`
- `tests/test_production_paper_v1634.py::test_catalogo_real_preserva_clase_moneda_y_no_duplica_resultados`
- `tests/test_production_paper_v1634.py::test_v17_renta_fija_dimensiona_por_nominal_y_persiste_factor[BONOS]`
- `tests/test_production_paper_v1634.py::test_v17_renta_fija_dimensiona_por_nominal_y_persiste_factor[LETRAS]`
- `tests/test_production_paper_v1634.py::test_v17_renta_fija_dimensiona_por_nominal_y_persiste_factor[ON]`
- `tests/test_production_paper_v1634.py::test_universo_ampliado_mantiene_derivados_solo_contexto`
- `tests/test_production_paper_v1634.py::test_lote_por_ciclo_rota_sobre_todo_el_universo`
- `tests/test_single_close_ledger_v17.py::test_cierre_unico_respeta_nominal_por_cien[BONOS]`
- `tests/test_single_close_ledger_v17.py::test_cierre_unico_respeta_nominal_por_cien[LETRAS]`
- `tests/test_single_close_ledger_v17.py::test_cierre_unico_respeta_nominal_por_cien[ON]`

These failures must be reconciled with the current instrument-readiness work. They must not be “fixed” by weakening or deleting the tests from this governance branch.

---

## ENVIRONMENT-HARNESS / FIXTURE — 4 cases

### Dashboard test missing its own import — 1

- `tests/test_dashboard_daily_responsive_hf6.py::test_home_page_does_not_render_official_source_status_panel`

Observed failure: `NameError: Path is not defined` inside the test itself.

### Historical download fixture missing the current cutoff-repair prerequisite — 3

- `tests/test_candle_archive_v17.py::test_historial_vacio_no_borra_ultimo_valido_y_raw_se_conserva`
- `tests/test_candle_archive_v17.py::test_descarga_fallida_rota_en_vez_de_bloquear_universo`
- `tests/test_production_paper_v1634.py::test_historicos_usan_universo_completo_no_lote_activo`

Current `_download_histories()` returns before querying when the one-time cutoff repair is not marked COMPLETE. These fixtures do not establish that prerequisite, so the observed zero rows do not prove the download logic itself is broken.

---

## LEGACY-REGRESSION / STALE EXPECTATION — 35 cases

This group is not discarded. It contains valuable history, but each assertion currently tests an expectation that is older than or textually coupled to the deployed RC6 contract.

### Strong evidence examples

- `test_dashboard_live_policy_hf2.py` expects **20** rows by default, while the current function explicitly documents and implements **10 rows by default, max 50**.
- `test_exit_supervision_v17.py` expects all four child processes to start immediately; current runtime intentionally starts the scanner first and delays other children with a startup grace period to avoid simultaneous local I/O.
- `test_maintenance_scheduler.py`, `test_data912_history_fallback.py`, and `test_historical_freshness_v1633.py` expect historical maintenance jobs that the current scheduler explicitly removed so PPI Watch owns that circuit.
- `test_historical_candle_shadow_rc6.py` calls a removed `tax_diagnostic()` API and expects the old `net_pnl_known` key; current code treats personal taxes as out-of-scope and exposes `net_pnl_after_published_costs`.
- `test_rc6_validation_dynamic.py` expects old RED/GREEN milestone semantics, while current code requires `claim_status=VERIFIED_CURRENT` for GREEN and represents M11 as `BLOCKED_BY_POLICY`.
- `test_rc6_table_headers_visible_sticky.py` expects `width:max-content`, while current tablet CSS explicitly uses `width:100%; table-layout:fixed` to fit the viewport.
- `test_dashboard_decision_evidence_rc6.py` fails on literal wording (`Lectura solamente`) although the current panel still declares SHADOW/read-only policy semantically.
- RC3/RC4/RC5/HF tests contain assertions over old menu labels, old deploy wording, old scheduler behavior and older provenance conventions.

The remaining 35 cases are in these files:

- `tests/test_rc6_byma_calendar_failclosed.py` — 2 tests for the retired deferred-calendar architecture. The current runtime deliberately embeds the audited BYMA calendar and no longer calls `ak_byma_calendar`; the AST-only test harness also omits the module constants, which is why the observed symptom is a `NameError`.
- `tests/test_candle_archive_v17.py` — 1 textual/dashboard expectation
- `tests/test_container_rootfs.py` — 2 legacy compose/Chroma layout expectations
- `tests/test_dashboard_daily_responsive_hf6.py` — excluding the one harness bug above
- `tests/test_dashboard_decision_evidence_rc6.py`
- `tests/test_dashboard_live_policy_hf2.py`
- `tests/test_dashboard_paper_v1634.py` — 6
- `tests/test_dashboard_session.py` — 2
- `tests/test_dashboard_v17_rc3.py`
- `tests/test_data912_history_fallback.py`
- `tests/test_exit_supervision_v17.py`
- `tests/test_historical_candle_shadow_rc6.py` — 3
- `tests/test_historical_freshness_v1633.py`
- `tests/test_history_freshness_metrics_rc5.py`
- `tests/test_hotfix_rc3_hf2.py` — 2
- `tests/test_hotfix_rc3_hf5.py`
- `tests/test_hotfix_rc3_hf6.py`
- `tests/test_maintenance_scheduler.py`
- `tests/test_rc4_acceptance.py` — 3
- `tests/test_rc4_hf2_consolidation.py`
- `tests/test_rc6_table_headers_visible_sticky.py`
- `tests/test_rc6_validation_dynamic.py` — 2

The exact node IDs remain preserved in the Actions evidence and must be carried into the machine-readable classification before suite gating changes.

---

## Required suite architecture

The solution is NOT to return to manual test lists.

Every candidate should continue to discover all tests automatically. The suite should then classify them explicitly:

1. `CURRENT_CONTRACT` — blocking.
2. `LEGACY_REGRESSION` — still executed and reported; reconciled before deletion/update.
3. `ENVIRONMENT_HARNESS` — still executed; harness must be repaired, not hidden.
4. `NETWORK_RED` — explicit marker for tests requiring external Internet, already represented by pytest marker `red`.

A test may be excluded from a blocking gate only through an explicit versioned classification with rationale. Silent omission is forbidden.

## Next safe actions

1. Repair the six proven harness/fixture problems in the isolated governance branch.
2. Produce a machine-readable exact-node classification for the 51 failures.
3. Keep the 12 current-contract failures blocking until product scope is reconciled.
4. Reconcile legacy tests against current behavior one cluster at a time.
5. Do not alter production runtime or the canonical deploy workflow yet.
6. Do not touch PPI Watch.
7. Keep FIX-FORWARD and no-rollback policy unchanged.

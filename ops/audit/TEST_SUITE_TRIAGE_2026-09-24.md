# Full Test Suite Triage — 2026-09-24

## Scope

This is a read-only / CI-only triage of the first automatic full-suite execution inside the exact Predeploy V2 image.

Production runtime changed: **NO**  
Droplet touched: **NO**  
PPI Watch changed: **NO**  
Tests deleted or silently excluded: **NO**

## What the run proved

Before full test discovery, the exact artifact passed:

- policy gate
- Python compile gate
- validator regression tests
- build-once candidate image
- exact image filesystem extraction
- 371 runtime files verified
- 0 missing runtime files
- 0 missing local imports
- critical runtime import smoke

Automatic discovery then exposed **51 failing test cases across 24 test files** that the existing manually enumerated candidate CI did not surface.

The failures inspected so far do **not** establish a production-runtime regression. They predominantly show test-contract drift: older expectations remained in the repository after deliberate RC6 architecture/policy/UI changes.

## File-level classification

| Test file | Failing cases | Classification | Evidence / current contract |
|---|---:|---|---|
| test_candle_archive_v17.py | 3 | UPDATE_REGRESSION_FOR_NEW_HISTORY_GUARD | History download is now gated by the completed cutoff repair; two tests bypass neither the new guard nor V2 history store. UI wording assertion is also stale. Valuable regression intent should be preserved and updated. |
| test_container_rootfs.py | 2 | STALE_INFRASTRUCTURE_CONTRACT | Assertions expect the previous Chroma/init compose layout; current compose no longer contains the expected service block/log initialization. |
| test_dashboard_daily_responsive_hf6.py | 1 | TEST_IMPLEMENTATION_BUG | Test calls `Path` without importing it. Product code is not reached by the failing assertion. |
| test_dashboard_decision_evidence_rc6.py | 1 | BRITTLE_TEXT_ASSERTION | Current panel still declares SHADOW/read-only semantics but wording changed from literal `Lectura solamente`. |
| test_dashboard_live_policy_hf2.py | 1 | SUPERSEDED_PAGINATION_CONTRACT | Test expects 20 rows; current RC6 dashboard contract uses 10-row compact pagination. |
| test_dashboard_paper_v1634.py | 6 | SUPERSEDED_UI_SURFACE | Underlying caución/history assertions pass before failures; failures are old expectations that those details/labels remain on `motor_page` / old consolidated live surface. |
| test_dashboard_session.py | 2 | SUPERSEDED_LIVE_LAYOUT | Authentication/session checks pass; failures expect old numbered Scalping/Workers sections in `/vivo`. |
| test_dashboard_v17_rc3.py | 1 | LEGACY_DEPLOY_TEXT_ASSERTION | RC3 test requires an exact explanatory string in docker-compose rather than executable behavior. |
| test_data912_history_fallback.py | 1 | SUPERSEDED_HISTORY_SCHEDULER | Internal historical catch-up job was intentionally retired; current scheduler comments reserve that circuit away from internal maintenance/PPI Watch conflict. |
| test_exit_supervision_v17.py | 1 | SUPERSEDED_CHILD_STARTUP_CONTRACT | Runtime now intentionally staggers child startup; test still requires four child processes immediately. |
| test_historical_candle_shadow_rc6.py | 3 | SUPERSEDED_SHADOW_COST_API | Personal-tax diagnostic was intentionally removed/out-of-scope; known-cost output keys changed while effect remains SHADOW_ONLY. |
| test_historical_freshness_v1633.py | 1 | SUPERSEDED_HISTORY_SCHEDULER | Expects retired startup catch-up job. |
| test_history_freshness_metrics_rc5.py | 1 | LEGACY_UI_TEXT_ASSERTION | Requires an RC5 dashboard phrase, not a semantic data contract. |
| test_hotfix_rc3_hf2.py | 2 | SUPERSEDED_INGEST_POLICY | Old TTL/force expectations conflict with current once-per-session/cutoff-gated historical ingestion. |
| test_hotfix_rc3_hf5.py | 1 | SUPERSEDED_SCALPING_POLICY | Test expects ACTIVE_PAPER; current safe setting is ACTIVE_OBSERVE. |
| test_hotfix_rc3_hf6.py | 1 | SUPERSEDED_SYSTEM_MENU | Requires the former `scraping` system section. |
| test_maintenance_scheduler.py | 1 | SUPERSEDED_JOB_INVENTORY | Expects removed historical jobs and omits newer audit/validation/preopen/GDELT jobs. |
| test_production_paper_v1634.py | 10 | SUPERSEDED_INSTRUMENT_SCOPE | Failing assertions require BONOS/LETRAS/ON/FUTUROS and the old enlarged universe. Current observer explicitly limits eligible operational symbols to ACCIONES/CEDEARS. |
| test_rc4_acceptance.py | 3 | LEGACY_RC4_CONTRACT | Requires retired scheduler/UI/deploy text contracts. |
| test_rc4_hf2_consolidation.py | 1 | LEGACY_UI_TEXT_ASSERTION | Exact RC4-era live-page wording no longer exists. |
| test_rc6_byma_calendar_failclosed.py | 2 | TEST_HARNESS_OBSOLETE | Test AST-extracts `_business_day` without its required constants and injects an old `ak_byma_calendar` module no longer used by the embedded audited calendar. |
| test_rc6_table_headers_visible_sticky.py | 1 | SUPERSEDED_RESPONSIVE_LAYOUT | Test requires `width:max-content`; current tablet policy deliberately uses fixed/100% width to prevent horizontal overflow. |
| test_rc6_validation_dynamic.py | 2 | SUPERSEDED_POLICY_SEMANTICS | M11 is now POLICY_BLOCKED/GRAY rather than a runtime RED failure; summary intentionally excludes policy-blocked rows from critical runtime reds. |
| test_single_close_ledger_v17.py | 3 | TEST_IMPLEMENTATION_BUG | Current motor already rejects families outside ACCIONES/CEDEARS. The updated regression test called `_open()` correctly in intent but unpacked 2 values while the current contract returns 3 `(opened, reason, paper_id)`. Fix preserves the safety assertion and adds `paper_id is None`. |

Total failing cases represented above: **51**.

## Required remediation rule

Do not make CI green by deleting tests, hiding files, or adding a giant exclusion list.

For each file:
1. preserve still-valid safety/regression intent;
2. update it to the current executable contract where the architecture changed deliberately;
3. move truly historical expectations into an explicit legacy-regression classification if they remain useful;
4. delete only assertions that no longer represent any supported contract and only after their replacement/current contract is covered;
5. current-contract tests must remain blocking.

## Important architectural conclusion

Manual focused test lists created two problems:
- new regression tests could exist without running;
- old tests could remain stale indefinitely because nothing forced reconciliation.

Deploy V2 therefore keeps automatic discovery as a permanent principle. Test classification must be explicit and auditable; discovery itself must never depend on someone remembering a filename.


## Progress update — exact-image full suite

Latest complete Predeploy V2 run on `e376160c5ae24b9d8c09d9b806d899501433b3fa`:
- discovered/executed: **1,984 tests**
- failures: **37**
- errors: **0**
- skipped: **0**
- artifact integrity before tests: GREEN
- runtime files verified: 371
- missing runtime files: 0
- missing local imports: 0
- droplet touched: NO
- local runner cleanup: GREEN

The previous full-suite run exposed 51 failures. Current triage/test-contract corrections reduced this to 37, a reduction of 14 failures without deleting or silently excluding tests.

# RC6 #685 — postinstall contract v1 expiry, 2026-10-22

## Observed incident

- Deploy V2 run [39696980751](https://github.com/mbalbo2023/Porota-trading/actions/runs/39696980751) promoted merge `c16a093df2161fbb6ef17edd44d17adf64578f85` / candidate `9b138e8b759ce5046419f5827375a1ff530ca6b9` into observer and dashboard. The contract reconciliation then raised `RECENT_COMPLETED_V1_UNVERIFIED` before v2 materialization.
- The canonical failure cleanup left `FIX_FORWARD_VALIDATION_REQUIRED`, with `real_orders_sent=0` and `ppi_watch_accessed=false`. The installed image remains pending validation; the failed run is not GREEN.
- Bounded read-only host inventory [39697722110](https://github.com/mbalbo2023/Porota-trading/actions/runs/39697722110) showed the newest completed v1 sweep ran 2026-10-20 06:56:13–07:55:23 UTC, with `total=10328`, `verified=1438`, `blocked_porota=4804`, `blocked_provider=4086`. The three counts sum exactly to the total. At deployment it was over 40 hours old, beyond the 24-hour reuse limit. The timer `porota-host-general-backup-rc6.timer` was `masked|inactive`; the observer was `PRODUCTION_PAPER`, `WAITING_MARKET`, `MARKET_CLOSED`, with zero open PAPER positions, cauciones and futures.

## Cause and correction

The prior guard collapsed a structurally valid but expired complementary provider sweep into the same `UNVERIFIED` error as malformed data. The deploy treated that expired sweep as an installation prerequisite, preventing the local full-catalogue v2 materialization. Re-running the provider v1 sweep during deploy would duplicate the scheduled collector's costly requests.

The runner now distinguishes `RECENT_COMPLETED_V1_EXPIRED` from malformed or absent evidence. In the expired-complete case it emits `SKIPPED_EXPIRED_COMPLETED` and `STALE`, makes no broker call, and continues through the mandatory v2 collection and catalogue reconciliation. The workflow accepts this exact classified outcome only with `STATUS=OK`, `QUICK_CHECK=ok`, and `LATEST_EVIDENCE_V2_RUN` in `OK`. It never reports v1 as recent or reused. A malformed, incomplete, absent, future-dated or unparseable latest row still fails closed.

The local v2 collector carries the catalogue's original `last_seen_at` into `observed_at`; PAPER admission and complementary reconciliation must continue to treat missing or expired identity and contract evidence as unavailable. Market Watch and OPEN/ARS are first-wheel runtime acceptance, never synthetic closed-session GREEN.

## Regression and continuation

The test fixture uses the six scalar fields observed on the host and proves an expired complete v1 does not call the broker, remains labelled `STALE`, and still produces v2 `OK` with SQLite integrity. Existing tests retain the negative cases for malformed and absent runs. The postinstall quiescence fixture uses the exact old candidate, merge SHA and installed image ID to replay `FIX_FORWARD_VALIDATION_REQUIRED` through the next exact stopped-image adoption without restarting the prior image.

The next candidate requires a fresh Predeploy V2, exact ownership authorization and canonical Deploy V2. A successful deploy still requires direct host and authenticated browser validation. Until then, status remains NO_GO.

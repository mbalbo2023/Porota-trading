# POROTA TRADING — CHECKPOINT PPI ARGENTINA API — 2026-09-12

## Binding scope

This checkpoint supersedes the broader foreign-instrument discovery scope for the current history work.

Current scope is **Argentine-market financial instruments exposed by the productive PPI API**. Foreign-market families and markets are excluded from ingestion work: `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ`.

Source order remains:

1. PPI productive API — exhaust first.
2. PPI authenticated web/scraping — only for residual gaps that remain after API-side RCA/retry and only if needed.
3. IOL — tertiary residual fallback only if both PPI layers are insufficient.

Historical ingestion is independent from `READY_PAPER`. This work does not call order routes.

## Safety baseline

- Host runtime SHA: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`.
- Runtime mode during all work: `PRODUCTION_PAPER`.
- `real_orders_sent=0`.
- Market phase during ingestion jobs: `CLOSED`.
- History lock: `/run/lock/porota-ppi-fullfamily-history.lock`.

## Argentine PPI API universe currently evidenced

A local-only productive API discovery restricted to `BYMA`, `ROFEX`, `A3`, `OTC` produced **1,960 unique identities**:

| Family | API identities |
|---|---:|
| ACCIONES | 55 |
| BONOS | 42 |
| CAUCIONES | 10 |
| CEDEARS | 191 |
| FCI | 1,040 |
| FUTUROS | 52 |
| LEBACS (provider-returned label) | 1 |
| LETRAS | 23 |
| LICITACIONES | 18 |
| ON | 91 |
| OPCIONES | 437 |
| **TOTAL** | **1,960** |

No local `ETF` identity was returned by the tested BYMA API searches (13/13 HTTP OK but empty). This is evidence of current API discovery, not a claim about every possible regulatory/security classification outside PPI.

## Original normalized catalog first pass — COMPLETE

The original live normalized catalog contained 901 local identities. PPI API historical first-pass is now **901/901 attempted**.

Final usable/hard results:

| Family | Total | Usable (FULL+PARTIAL) | Hard outcome |
|---|---:|---:|---:|
| ACCIONES | 55 | 55 | 0 |
| BONOS | 42 | 42 | 0 |
| CAUCIONES | 10 | 10 | 0 |
| CEDEARS | 191 | 183 | 8 |
| FUTUROS | 52 | 36 | 16 |
| LETRAS | 23 | 16 | 7 |
| ON | 91 | 33 | 58 |
| OPCIONES | 437 | 180 | 257 |

The 901 pass completed with `FIRSTPASS_REMAINING=0`. Hard outcomes are **not automatically scraping requirements**: they must first be classified as no history/no trades, stale/expired, wrong identity/settlement, validation mismatch, or transient/provider error.

## Quick 7-day productive API proof

A 7-day read-only test sampled local families and proved historical rows from the PPI API for ACCIONES, BONOS, CAUCIONES, CEDEARS, FCI, FUTUROS, LETRAS and OPCIONES. ON and LICITACIONES samples returned valid empty payloads in that narrow 7-day window; prior 365-day ON proof returned historical data, so a 7-day empty response does not imply lack of API history.

FCI was additionally persisted end-to-end for five identities. All 5/5 returned 4 provider rows and 4 valid rows; **20 valid FCI rows** were stored through raw evidence + History Store v2. This proves that local FCI history is available through the productive PPI API and the 1,040 FCI identities are API-ingestion work, not scraping work.

`CEDI` was discovered with provider-returned type `LEBACS`, but a history request with `instrument_type=LEBACS` returned `Instrument Type not found: LEBACS`. A targeted RCA is testing the PPI-declared `LEBAC`/`NOBAC` type variants before any fallback is considered.

## Current API versus scraping delta

Latest audit before the FCI seed and LICITACIONES follow-up:

- Local API identities: **1,960**.
- Already usable from 365-day API attempts: **555**.
- API ingestion pending at that audit: **1,059** (mainly 1,040 FCI + 18 LICITACIONES + 1 LEBACS). This is **API work, not scraping**.
- Hard API outcomes requiring RCA: **346**.
- Scraping objectively proven necessary: **0 identities at this stage**.

Hard API outcomes by family requiring RCA before PPI web fallback:

- CEDEARS: 8 (all AVAILABLE).
- FUTUROS: 16 = 7 AVAILABLE + 9 STALE.
- LETRAS: 7 = 4 AVAILABLE + 3 STALE.
- ON: 58 (all AVAILABLE).
- OPCIONES: 257 (all AVAILABLE).

The complement delta will therefore be computed as:

`PPI API universe -> API historical attempt -> API RCA/retry -> residual gap -> PPI Web only for residual`.

An unattempted identity is never classified as a scraping gap.

## Nightly ingestion installed

Host timer installed and verified:

- `porota-ppi-argentina-nightly-rc6.timer`: **enabled + active**.
- Schedule: **02:30 America/Argentina/Buenos_Aires**, randomized delay 0–10 minutes.
- Current batch limit: 100 identities per run.
- Scope: local PPI API only (`BYMA/ROFEX/A3/OTC`), foreign families excluded.
- Uses the shared history lock and skips if conflicting history services are active.
- Asserts `PRODUCTION_PAPER`, market CLOSED and `real_orders_sent=0` before processing.
- Persists raw evidence, History Store v2, attempt state and complete legacy payload only when fully valid.

## Remaining work

1. Complete all newly discovered local API identities, especially FCI and LICITACIONES, through the nightly API job.
2. Finish `LEBAC`/`NOBAC`/provider `LEBACS` contract reconciliation.
3. Run API-side RCA/retry on the 346 hard outcomes.
4. Recompute the per-family and per-identity residual after API exhaustion.
5. Only then invoke PPI Web for the residual identities/fields that the API demonstrably cannot supply.
6. Keep IOL outside the workflow unless a residual remains after both PPI layers.
7. Permanent code fix remains: decouple production `_historical_targets()` from `can_simulate`, with tests, before deploy.

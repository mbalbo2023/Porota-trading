# POROTA TRADING — CHECKPOINT PPI ARGENTINA API — 2026-09-12

## Binding scope

This checkpoint supersedes the broader foreign-instrument discovery scope for the current history work.

Current scope is **Argentine-market financial instruments exposed by the productive PPI API**. Foreign-market families and markets are excluded from ingestion work: `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ`.

Historical source policy for this work:

1. Exhaust the productive PPI API first.
2. Do not start PPI Web ingestion now. Web/scraping is only a residual complement if, after API-side RCA/retry, required history or reference fields are demonstrably unavailable from the API.
3. IOL remains tertiary and outside the current ingestion.

Historical ingestion is independent from `READY_PAPER`. No order route is called by this work.

## Safety baseline

- Host runtime SHA: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`.
- Runtime mode during all work: `PRODUCTION_PAPER`.
- `real_orders_sent=0`.
- Market phase during ingestion jobs: `CLOSED`.
- Shared history lock: `/run/lock/porota-ppi-fullfamily-history.lock`.

## Argentine PPI API universe currently evidenced

A productive API discovery restricted to local PPI markets (`BYMA`, `ROFEX`, `A3`, `OTC`) produced **1,960 unique identities**:

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

No local `ETF` identity was returned by the tested BYMA API searches: 13/13 requests completed successfully and returned no normalized ETF identity. This is a statement about current PPI API discovery only, not a regulatory universe claim outside PPI.

## Original 901-identity catalog — first API history pass COMPLETE

PPI historical API first-pass is **901/901 attempted**.

| Family | Total | Usable (FULL+PARTIAL) | Hard API outcome |
|---|---:|---:|---:|
| ACCIONES | 55 | 55 | 0 |
| BONOS | 42 | 42 | 0 |
| CAUCIONES | 10 | 10 | 0 |
| CEDEARS | 191 | 183 | 8 |
| FUTUROS | 52 | 36 | 16 |
| LETRAS | 23 | 16 | 7 |
| ON | 91 | 33 | 58 |
| OPCIONES | 437 | 180 | 257 |

The pass completed with `FIRSTPASS_REMAINING=0`. A hard outcome is not automatically a scraping requirement: it must first be classified as no trades/no history, stale/expired, wrong identity/settlement, validation mismatch, or transient/provider error.

## Quick 7-day API proof and actual seed ingestion

The 7-day productive API proof returned historical rows for sampled ACCIONES, BONOS, CAUCIONES, CEDEARS, FCI, FUTUROS, LETRAS and OPCIONES. ON samples were empty in that narrow week, while previous 365-day ON probes returned data, proving that a short-window empty response does not mean the historical API is unsupported.

Five FCI identities were then actually persisted end-to-end through raw evidence + History Store v2:

- `A.AHPLUS.B`: 4/4 valid rows.
- `AD.AH.DI.A`: 4/4.
- `AD.AH.DI.B`: 4/4.
- `AD.AUSD.D`: 4/4.
- `AD.AUSD.E`: 4/4.

Result: **5/5 FCI usable, 20 valid rows stored**. Therefore the 1,040 local FCI identities are API-ingestion work, not web-scraping work.

## LICITACIONES and LEBAC/NOBAC API RCA

All **18/18 LICITACIONES** discovered through the PPI API were queried over a trailing **365-day** history window. Every request completed without an HTTP/read error, but every payload contained **0 historical rows**. Result: `tested=18`, `usable=0`, `empty=18`, `errors=0`, `valid_rows=0`.

This is now a confirmed **PPI historical-API data gap for LICITACIONES for the tested 365-day endpoint/window**. It does not block API ingestion of the rest of the local universe. If historical auction/result information is later required, LICITACIONES is a valid residual candidate for a PPI Web/reference-data complement rather than another blind API retry.

For the single provider-discovered `CEDI` identity:

- history as `LEBAC` -> `Instrument not found`;
- history as `NOBAC` -> HTTP OK, 0 rows;
- history as `LEBACS` -> `Instrument Type not found: LEBACS`.

Therefore `CEDI` is not currently a usable historical series through the tested PPI API contract. Keep it as an explicit API gap; do not reinterpret the provider label silently.

## Current API-versus-complement delta

Immediately before the seed, the local universe had 1,960 API identities, 555 usable 365-day identities, 1,059 API-pending identities and 346 hard API outcomes. After the actual seed and full LICITACIONES pass, the deterministic current accounting is:

- API identities: **1,960**.
- API attempts/evidence: **925** identities.
- Usable API historical identities: **560** = previous 555 + 5 FCI.
- Hard/empty API outcomes: **365** = previous 346 + 18 LICITACIONES + 1 provider `LEBACS` identity.
- API ingestion still pending: **1,035**, overwhelmingly the remaining FCI identities.

Residual families that require API-side RCA before any scraping classification remain:

- CEDEARS: 8 hard API outcomes.
- FUTUROS: 16 = 7 AVAILABLE + 9 STALE.
- LETRAS: 7 = 4 AVAILABLE + 3 STALE.
- ON: 58 AVAILABLE hard outcomes.
- OPCIONES: 257 AVAILABLE hard outcomes.
- LICITACIONES: 18 confirmed 365-day API-empty histories.
- provider `LEBACS` identity: 1 unresolved/empty across tested PPI type variants.

**Scraping is not being executed now.** The complement rule is:

`PPI API universe -> API history ingestion -> API RCA/classification -> only then residual PPI Web complement`.

The 1,035 unattempted identities are API ingestion pending, never scraping gaps.

## Nightly PPI API ingestion installed and widened

Host timer is installed and verified:

- `porota-ppi-argentina-nightly-rc6.timer`: **enabled + active**.
- Schedule: **02:30 America/Argentina/Buenos_Aires**, randomized delay 0–10 minutes.
- Host batch limit was initially 100 and was safely widened after successful bounded proofs to **600 identities per nightly run**.
- Script timeout remains 2,400 seconds; the job is intended to consume the overnight window without continuously hammering PPI.
- Scope is local PPI API only: `BYMA/ROFEX/A3/OTC`; foreign families are excluded.
- Uses the shared history lock and skips if conflicting history services are active.
- Asserts `PRODUCTION_PAPER`, market CLOSED and `real_orders_sent=0` before processing.
- Persists raw evidence, History Store v2, attempt state, and the legacy complete payload only when fully valid.

At 600 identities per run, the current ~1,035 pending API identities can be covered in roughly two nightly passes, subject to provider latency/errors and the fail-closed runtime window.

## Remaining work

1. Let the nightly API job complete the remaining ~1,035 local API identities, primarily FCI.
2. Recompute the exact per-family/per-identity residual after API exhaustion.
3. Run targeted API RCA/retry on the existing hard outcomes, prioritizing AVAILABLE identities over STALE/expired identities.
4. Classify true residuals by cause before deciding whether any PPI Web complement is needed.
5. Keep IOL outside the current workflow.
6. Permanent code fix remains: decouple production `_historical_targets()` from `can_simulate`, with CI tests, before deploy.

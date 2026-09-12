# POROTA TRADING — CHECKPOINT PPI ARGENTINA API + CONTRATOS WEB — 2026-09-12

## Binding scope

Current scope is **Argentine-market financial instruments exposed by PPI**, with the productive API as the authoritative first layer for universe discovery and historical data. Foreign-market families and markets remain excluded from the current ingestion scope: `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ`.

Source policy:

1. Productive PPI API — exhaust first for identities and history.
2. Authenticated PPI Web — active for **contract/reference evidence** and later usable as a residual complement for fields/history that the API demonstrably does not provide.
3. IOL — tertiary residual fallback only after both PPI layers are exhausted.

Historical ingestion is independent from `READY_PAPER`. Contract/reference enrichment does not auto-promote an identity to PAPER/tradable status.

## Safety baseline

- Current host runtime SHA: `d719deeb379de285b07fa61a40f5d48d19d5bffd`.
- The host advanced from the previous history baseline because of the EOD UI PAPER deployment; the history/API data persisted.
- Runtime mode during current checks: `PRODUCTION_PAPER`.
- `real_orders_sent=0`.
- History lock: `/run/lock/porota-ppi-fullfamily-history.lock`.
- Contract Web collector is read-only and recent runs report `real_orders_sent=0`.

## Argentine PPI API universe currently evidenced

Productive API discovery restricted to local PPI markets (`BYMA`, `ROFEX`, `A3`, `OTC`) produced **1,960 unique identities**:

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

No local `ETF` identity was returned by the tested BYMA API searches: 13/13 requests completed successfully and returned no normalized ETF identity. ETF Web rows, if found, are discovery evidence only until reconciled to a valid local API identity/market.

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

A hard outcome is not automatically a scraping requirement: it must first be classified as no trades/no history, stale/expired, wrong identity/settlement, validation mismatch, or transient/provider error.

## Quick 7-day API proof and actual seed ingestion

The 7-day productive API proof returned historical rows for sampled ACCIONES, BONOS, CAUCIONES, CEDEARS, FCI, FUTUROS, LETRAS and OPCIONES. ON samples were empty in that narrow week, while previous 365-day ON probes returned data.

Five FCI identities were persisted end-to-end through raw evidence + History Store v2:

- `A.AHPLUS.B`: 4/4 valid rows.
- `AD.AH.DI.A`: 4/4.
- `AD.AH.DI.B`: 4/4.
- `AD.AUSD.D`: 4/4.
- `AD.AUSD.E`: 4/4.

Result: **5/5 FCI usable, 20 valid rows stored**. Therefore the 1,040 local FCI identities are primarily API-history ingestion work, not Web-history work.

## LICITACIONES and LEBAC/NOBAC API RCA

All **18/18 LICITACIONES** discovered through the PPI API were queried over a trailing **365-day** history window. Every request completed without an HTTP/read error, but every payload contained **0 historical rows**: `tested=18`, `usable=0`, `empty=18`, `errors=0`, `valid_rows=0`.

This is a confirmed historical-API gap for the tested LICITACIONES endpoint/window. PPI Web is therefore a legitimate complement for auction/reference/result fields if those are needed; that does not imply candle history exists on the Web.

For provider-discovered `CEDI`:

- `LEBAC` -> `Instrument not found`;
- `NOBAC` -> HTTP OK, 0 rows;
- `LEBACS` -> `Instrument Type not found: LEBACS`.

Keep this as an explicit API contract/taxonomy gap.

## Current API-history delta

Current deterministic accounting remains:

- API identities: **1,960**.
- API attempts/evidence: **925** identities.
- Usable API historical identities: **560**.
- Hard/empty API outcomes: **365**.
- API ingestion still pending: **1,035**, overwhelmingly remaining FCI identities.

Hard outcomes requiring classification/RCA include CEDEARS 8, FUTUROS 16, LETRAS 7, ON 58, OPCIONES 257, LICITACIONES 18, and provider `LEBACS` 1.

The **1,035 pending are API work, never a Web gap**.

## Nightly PPI API ingestion

`porota-ppi-argentina-nightly-rc6.timer` is **enabled + active**.

- Schedule: 02:30 America/Argentina/Buenos_Aires, randomized 0–10 minutes.
- Batch limit: **600 identities per run**.
- Local PPI API only.
- Shared lock and conflict checks protect concurrent history services.
- It asserts `PRODUCTION_PAPER`, market CLOSED and `real_orders_sent=0` before processing.
- It persists raw evidence + History Store v2 + attempt state.

At the current delta, roughly two nightly passes can cover the 1,035 API-pending identities, subject to provider latency/errors.

## PPI Web contract/reference evidence — ACTIVE

The W1–W18 contract-evidence architecture is not merely repository-only: a current runtime service is active on the host.

`porota-contract-evidence-rc6.timer` is **enabled + active** and polls every **5 minutes**. The service uses the trusted PPI browser profile and imports sanitized authenticated Web evidence into the observer database. Recent service output is `AUTHENTICATED_TRUSTED_DEVICE`, with `blocked_nonread=0` and `real_orders_sent=0`.

Current DOM route policy refreshes:

| Family | PPI Web route | max age |
|---|---|---:|
| CAUCIONES | `/Cotizaciones/Cauciones` | 300 s |
| LICITACIONES | `/Cotizaciones/Licitaciones` | 300 s |
| FUTUROS | `/Cotizaciones/Futuros` | 900 s |
| BONOS | `/Cotizaciones/Bonos` | 900 s |
| OPCIONES | `/Cotizaciones/Opciones` | 900 s |

Current evidence objects in the observer DB:

- `contract_evidence`: 855 rows.
- `contract_evidence_v2_current`: **520 rows**.
- `contract_evidence_v2_changes`: 1,585.
- `contract_evidence_v2_runs`: 159.
- `contract_evidence_v2_snapshots`: 1,073.
- `ppi_intraday_contract_state`: 834.

Relevant local-family rows currently present in `contract_evidence_v2_current`:

| Family | Web evidence rows | latest observed |
|---|---:|---|
| ACCIONES | 77 | 2026-09-09 |
| BONOS | 59 | 2026-09-12 |
| CAUCIONES | 2 | 2026-09-12 |
| CEDEARS | 204 | 2026-09-09 |
| FCI | 1 | 2026-09-09 |
| FUTUROS | 44 | 2026-09-12 |
| LETRAS | 32 | 2026-09-09 |
| LICITACIONES | 3 | 2026-09-12 |
| ON | 85 | 2026-09-09 |
| OPCIONES | 1 | 2026-09-12 |

These counts are evidence rows and are **not expected to equal API identity counts one-for-one**; reconciliation must use identity/market/currency/settlement and field-level provenance.

Older W12 deep-collector code already defines safe authenticated GET-only routes for all PPI families. For this weekend only the Argentine/local routes should be used: `FCIs`, `Acciones`, `Bonos`, `Cauciones`, `Cedears`, `Futuros`, `Letras`, `Licitaciones`, `Ons`, `Opciones`, with `ETFs` allowed as discovery-only evidence. Foreign routes `FCIsExterior` and `AccionesUSA` remain excluded.

## Weekend convergence plan

Goal for Saturday/Sunday: maximize local PPI data before Monday without weakening execution safety.

1. Leave the 600-identity nightly API-history ingestion running until the 1,035 API-pending identities are exhausted.
2. Canary the missing/stale local Web contract routes using the existing trusted browser and GET/HEAD/OPTIONS-only policy.
3. If canary is green, extend the existing contract-evidence route policy through the weekend to refresh/enrich ACCIONES, CEDEARS, FCI, LETRAS and ON in addition to the five families already refreshed continuously; optionally probe ETF as discovery-only.
4. Reconcile API identities against Web evidence field-by-field. Store MATCH / API_ONLY / WEB_ONLY / CONFLICT and provenance; do not silently overwrite API facts.
5. Use fresh contract evidence to RCA the 365 hard API-history outcomes: expired/stale, no traded history, settlement/type mismatch, or genuine provider gap.
6. Only for genuine API residuals, run targeted PPI Web history/reference probes. LICITACIONES and `CEDI` are already explicit candidates for reference/contract complement.
7. Before Monday, produce a read-only certification: API universe, historical coverage, hard residuals, Web contract coverage, conflicts, readiness candidates, and zero automatic operational promotions.

## Readiness rule

Web evidence may complete contract/reference fields, but **no identity becomes tradable merely because scraping found fields**. Candidate readiness requires reconciled identity, family contract fields, historical sufficiency where applicable, status/liquidity checks, and existing Porota readiness gates. Real orders remain disabled.

## Permanent code work still pending

- Decouple production `_historical_targets()` from `can_simulate` with tests/CI before code deploy.
- Separate information universe from operational/PAPER selectors permanently so data discovery cannot grant trading eligibility.

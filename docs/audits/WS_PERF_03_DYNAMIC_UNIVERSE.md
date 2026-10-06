# WS-PERF-03 — source audit and dynamic universe

Issue: https://github.com/mbalbo2023/Porota-trading/issues/458

Audit base: `da697c6e6c2274579f9e4a112fabc4327475dd35`, productive branch
`deploy/rc6-pr69-isolated-20260915`. Source audit began READ_ONLY. No provider
requests, browser sessions, orders, host changes, merges or deployment were
performed for this audit. Source adapters are offline and OBSERVE_ONLY.

## Existing source architecture

The catalogue is independent of sampling capacity. An existing instrument can
remain READY while its quote source, strategy suitability, discovery age or
economics is unverified. Source observations below do not grant BUY authority.

| Existing source | Factual coverage and fields | Time and freshness | Discovery/cost limits |
| --- | --- | --- | --- |
| PPI read-only API | Existing `ProductionMarketReader` queries exact identities: current price/date; book prices/quantities/date; intraday price/volume/date; instrument search; history; configuration; bond valuation. Endpoint capability may differ by instrument. | `current.date`, `book.date` and intraday row date are source events; receipt is separate. Intraday normalization checks ordering, duplicates, session date and future events. | Current/book/intraday are per instrument, not a demonstrated transversal snapshot. Capacity and endpoint latency remain NO_VERIFICADO during a session. Existing handling of HTTP 429 is a generic defense, not evidence of a 20-instrument broker limit. |
| PPI authenticated web | Current trusted collectors allow only quote pages and GET/HEAD/OPTIONS. Structured target reads retain instrument metadata, estimated commissions/fees, option underlyings and bond technical fields. DOM supports 13 allowed family pages; defaults are caucion/auctions/futures/bonds/options. | `generated_at` dates the capture. It does not establish quote freshness. Per-row explicit provider time is necessary for dynamic discovery. | Visible DOM maximum is 8 tables × 100 rows × 40 columns per route, without pagination. `rc6_dom_coverage_reconciler` requires an independent expected universe before COMPLETE_PROVEN. Importer stores family aggregate ticker `*`, market/settlement UNKNOWN, and does not infer column semantics. No all-equity fresh radar demonstrated. |
| IOL integrated runtime/cache | Quote cache rotates auditable catalogue tickers. Normalized fields: last, bid/ask, sizes, spread, unit/lot prices, variation, cash volume, provider time, family/currency/lot metadata. Family references separately support one fixed-income identity, up to four option-underlying chains, FCI inventory and caucion rates. | Row `captured_at` differs from quote `provider_observed_at`. Quote metadata TTL is 24 h; cache fallback TTL is 5 min; published provider-fresh counter uses 120 s. Saved LIVE_FRESH labels must be recomputed against time before use. | Quote batch 12 per scheduled call; 40 calls/minute is a local governor, not factual provider capacity. Worst nominal first-run cost is 24 quote/metadata calls plus 15 family reads, with retries still governed. No transversal equity quote feed is proven. |
| Existing public BYMA scraper | Six paginated panels: leading-equity ACCIONES, CEDEARS, BONOS, OBLIGACIONES, CAUCIONES and OPCIONES. Possible fields include symbol, currency, bid/ask, last, variation, volume/cash volume, VWAP, row timestamp/time-only, sizes, settlement code and maturity. | An aware per-row source datetime can establish age. Time-only or snapshot capture time cannot. A multi-page capture is not an atomic simultaneous exchange snapshot. | Reuse `rc6_public_sources_latest.json`; no new scraper or BYMA API dependency. `excludeZeroPxAndQty=True` and T1 filtering mean capture is not the complete READY catalogue. No direct ETF/LETRAS/FUTUROS/FCI panel is proven by this scraper. Always OBSERVE_ONLY. |

Relevant modules were read in full: `bd_ppi_readonly_guard.py`,
`cn_ppi_authenticated_family_scraper_hf6.py`,
`rc6_trusted_browser_contract_collector.py`, `rc6_contract_dom_collector.py`,
`rc6_contract_dom_importer.py`, `rc6_ppi_contract_normalizer.py`,
`rc6_dom_coverage_reconciler.py`, `rc6_source_consolidation.py`,
`rc6_multisource_discovery.py`, `cy_market_source_arbitration_hf6.py`,
`iol_mcp_readonly_adapter_rc6.py`, `iol_shadow_collector_rc6.py`,
`iol_shadow_observation_rc6.py`, `iol_shadow_decision_input_rc6.py`,
`rc6_iol_family_reference.py`, `scripts/rc6_iol_shadow_collect.py`,
`scripts/rc6_public_source_capture.py`, `scripts/rc6_byma_morning_pipeline.py`,
`scripts/rc6_byma_morning_watch.py`, and contract scheduling/policy modules.

The old credential-login scraper
`cn_ppi_authenticated_family_scraper_hf6.py` is explicitly classified
OPS_CLI_MANUAL_LEGACY in `rc4_module_inventory.py`. It is not reused as an
additional login or as a new hot-path source. Current trusted-browser state
and profile are not opened or changed by WS-PERF-03.

## Configured cadence versus demonstrated source freshness

The current code has three distinct existing BYMA paths using one structured
scraper: the IOL host service invokes public capture after each scheduled
batch, the observer captures every 900 s while OPEN and at closing transition,
and the 08:30 Argentina morning pipeline captures once before the authority
watch. The IOL timer is scheduled each minute during 10:30–16:59 Argentina
weekdays. The service can overrun, block or fail; this schedule does not prove
one-minute complete or fresh market visibility. The observer's separate
21600 s public health check is not its structured capture cadence.

Contract/browser due checks are scheduled every 300 s. Policy TTLs are
operability/derivative series 900 s, caucion/auctions 300 s, static contract
86400 s and full-browser audit 604800 s. Contract evidence is deliberately
outside the strategy hot path. Neither configuration nor HTTP success proves
quote freshness or completeness.

Read-only Actions log evidence:

- Run `37125515526`, job `111209919420`, reported
  `BYMA_MORNING_PIPELINE` at `2026-10-03T14:04:59+00:00`,
  SCRAPED_PUBLIC_DATA, one record, no reported errors, OBSERVE_ONLY,
  CHANGED_REVIEW_REQUIRED. This Saturday observation establishes an existing
  working scraper path; it does not establish full-market coverage or
  session-time freshness/capacity.
- Run `37131712187`, job `111228035345`, reported IOL reference cache
  `refreshed_at` and `last_known_good_at` of
  `2026-10-02T19:58:57.187272+00:00`. Stored sections caucion ARS/USD and FCI
  were LIVE_FRESH at capture; fixed income and option underlyings
  DOME/ECOG/ECOGC/ECOGD were SOURCE_UNAVAILABLE_NO_LKG. A stored LIVE_FRESH
  label on the following day does not establish a fresh quote.
- Historical sanitized IOL evidence already in
  `docs/audit/ws14_iol_mcp_sanitized_evidence.json` proves selected Sep 29
  fixed-income, options, FCI and caucion reference fields, not contemporary
  broad equity coverage. Capability gaps remain scoped to their endpoints
  and identities.

No request/minute, p50/p95/p99 latency, exchange delay, usable fraction or
simultaneous broad coverage has been demonstrated by these source logs. The
source manifest exposes these as NO_VERIFICADO. Closed/preopen evidence is not
a market-session capacity benchmark.

## Offline adapter contract

`rc6_dynamic_universe.sources.source_observations(snapshot, source=..., as_of=...,
max_age_seconds=120)` consumes existing raw snapshots. It returns an auditable
report with observations, seen/useful/rejected counters and partial errors.
Each observation has the exact five-part identity, source and receive times,
age, useful flag, rejection reason, explicitly supported numeric fields/units,
and OBSERVE_ONLY authority. It performs no HTTP, login, browser, database,
broker or execution operation.

Unknown family/market/currency/settlement is rejected for dynamic promotion,
without modifying or trimming the catalogue. Explicit BCBA/BYMA, T1/A-24HS and
T0/INMEDIATA spellings are normalized; an unknown numeric settlement code is
not guessed. DOM route family may be used because the allowlisted route is
explicit evidence; missing market/currency/settlement columns remain missing.

Source age never falls back to capture time. Naive timestamps, time-only
timestamps, future source events, observations received after the evaluation
cutoff, and stale quotes remain rejected evidence. Existing source labels and
partial failures remain visible rather than becoming a numerical claim that
the provider has zero instruments.

Price, volume, cumulative volume and depth require explicit units. No nominal
unit, cash conversion or cumulative/session meaning is invented from a raw
`volume` field. Relative volume inputs additionally require
`volume_semantics=CUMULATIVE_SESSION`. A within-row bid/ask ratio may produce
dimensionless spread bps. Source provenance remains separate from strategy
signal, economics, risk and PAPER authority.

The existing IOL consolidation script can put BYMA capture time into a missing
`provider_observed_at`; its consolidated FRESH label is therefore not accepted
as new dynamic-source proof. The WS-PERF-03 adapter reads original raw source
rows and keeps provider and receipt times separate. The existing discovery
module already describes the capture-versus-quote distinction explicitly.

No sufficiently fresh transversal discovery feed is yet proven. Any planner
using these reports must retain adaptive per-instrument discovery and report
coverage/age; it cannot promise that all market moves are detected in real time.

## Runtime and baseline gate revalidation

The exact existing Predeploy workflow `.github/workflows/porota-predeploy-v2.yml`
was read in full and its baseline run `37125173991`, job `111208898481`, checked
directly via Actions logs/API. It checked candidate
`9ca8cb13bfc6664597e313e5d1874710e3263ec4`, tree
`96a112a55ed779df3f7056b1e30d81aac8d0b791`, and passed 2493 discovered/executed
tests, zero failures/errors/skips/xfails. Artifact
`11275685221` has API digest
`sha256:405cf74f6a21f06d32fa66993b21536786d851b6a49c83e8fc965be35115f9cc`.
This is baseline evidence, not validation of the WS-PERF-03 candidate.

The original promote run remains FAILED. The validation-only resume
`37131712187` final log at `2026-10-03T15:16:36.701864+00:00` confirms productive
deploy SHA `da697c6e6c2274579f9e4a112fabc4327475dd35`, VALIDATED_RUNTIME,
PRODUCTION_PAPER, `real_orders_sent=0`, real routes NOT_CALLED and PPI Watch
untouched. No deploy is repeated for this issue. Concise runtime revalidation
is retained privately outside the repository; no private runtime snapshot,
account data, profile, token or cookie is committed.

## Implemented architecture and Issue #458 completeness

All new allocation, ranking, events and economic variants are SHADOW hypotheses,
not automatic promotion of a trading policy. The two existing-code changes are
the `cf_intraday_scalping.shadow_sampling_plan` adapter and the generic
`PaperBroker.decide` family guard. The actual collector, scanner, broker
admission, limits, cost rules, factual score, TP/stop/MaxHold/EOD and specialized
lifecycles retain their existing owners. The old eight-symbol focus remains an
operational baseline; it is not used by the new attention scheduler.

| Requirement | Concrete implementation and evidence boundary |
| --- | --- |
| Objectives 1/6; adenda A/H/K | Bounded real-SDK benchmark over the existing `ProductionMarketReader`: batches 20/40/60/80/100, endpoint and full serial-cycle accounting, requests/minute, p50/p95/p99, freshness, useful/distinct observations and scoped failures. `safe_capacity` requires OPEN calendar evidence at measurement and use, valid digest/config, completed same-batch cycles, recent distinct samples, endpoint and combined latency budgets, and a safety factor. Unmeasured or incompatible evidence produces safe limit zero; no productive limit changes. |
| Objective 2; adenda D/H/M | Explicit CATALOG_READY → STRATEGY_ELIGIBLE → TRADEABLE → OBSERVABLE → SIGNAL_READY → ECONOMICS → RISK → PAPER telemetry. Fixed income routes to yield/duration/cashflow; options to underlying/expiry/strike; futures to #453; cauciones to cash/rate/term events; FCI to NAV/subscription/redemption. Observation dispatch never falls back to equity. Specific native option contract gaps remain before the generic guard. |
| Objective 3; adenda C/D/F | Available-at-cutoff 5/20 audited-session activity, unit-qualified volume/turnover, within-family/market/currency liquidity percentile, spread p50/p95, depth/PAPER size, slippage, quote age and usable fraction. Interarrival/range are exposed only when supplied. Missing sessions remain unknown. Concentration is a measured cumulative curve with a real denominator, without an imposed 80/20. Attention score is separate from opportunity and cost/risk. |
| Objective 4; adenda A/B/K/L | Separate HOT/WARM/DISCOVERY schedules. Defaults SCALPING 30/120/300 s, 15 distinct samples/2700 s; normal equity 120/300/600 s, 6 samples/5400 s. These are versioned requested hypotheses, not demonstrated runtime cadence. HOT is a capacity-derived subset and requires a previous WARM/HOT state, valid strategy samples, current book and freshness. DISCOVERY retains the remaining eligible universe and never grants immediate entry. Fair least-served rotation and a reserved discovery slot avoid silent deletion. |
| Objective 5; adenda A/L | Existing snapshot adapters preserve explicit identity, provider/receipt clocks, supported units, stale/rejected rows, partial failures and provenance. No transport or login. No sufficiently fresh broad radar has been demonstrated; age/coverage and theoretical rotation lower bounds expose this limitation. The bound assumes completed serial work without errors and is not a discovery guarantee. |
| Objective 7; adenda B/C/E | Immutable preopen JSON and SHA256 freeze available information from the previous session before opening. Intraday RVOL and acceleration compare cumulative/interval units in the same minute/window against prior-session profiles; new-trade transitions, same-horizon normalized price shock, spread compression and depth improvement create only prospective promotion events. Hypotheses are versioned SHADOW/OOS and never selected by retrospective P&L. The historical approximately 0.389 ARS AUC does not invert or recalibrate the factual score; no threshold is a success probability. |
| Adenda F/G | `economics.py` calls #456's common `economics_gate`, `ExitPolicy`, `ExitReplay`, `forward_labels` and cost functions. Preregistered volatility/time-to-EOD, MaxHold, trailing and break-even variants share exact factual entries and path hashes; baseline is preserved. Entry-hour/family/currency cohorts report net modeled results, sampled MFE/MAE and level-touch fractions only with complete EOD labels. Missing coverage is censored, and continuous hit probability and future OOS edge remain NO_VERIFICADO. No copied cost model or parameter search. |
| Adenda I/J | CEDEAR ratio, underlying US session, CCL and local divergence have their own available/freshness provenance; absent inputs remain NO_VERIFICADO without blanket exclusion. Option observation selection requires a liquid observable exact underlying, currency/price-basis agreement, active bounded expiry, moneyness, acceptable spread and positive depth. Volume/OI/IV/Greeks are unknown unless individually fresh. This selection is OBSERVE_ONLY. |
| Adenda M/N | Native reason codes include LOW_LIQUIDITY, NO_RECENT_TRADES, LOW_ACTIVITY, SPREAD_TOO_WIDE, INSUFFICIENT_DEPTH, STALE_QUOTES, INSUFFICIENT_USEFUL_OBSERVATIONS, SCANNER_CAPACITY, DISCOVERY_ONLY, WARMUP_INCOMPLETE, SPECIALIZED_LIFECYCLE, STRATEGY_NOT_VALIDATED, SOURCE_UNAVAILABLE and PPI_INSTRUMENT_NOT_FOUND. Instrument failures remain scoped, with original native codes retained alongside the source reason. No provider failure becomes a zero-price success. |

Capacity is allocated per engine and reconciled once against the same measured
serial account budget. Overlapping identity/endpoint reads share a reservation;
both engines cannot independently spend all of a benchmark. Rejected shared
tasks do not advance their selection checkpoint. Open equity positions retain
first priority even when capacity overflows, which is explicitly reported.
Specialized open positions remain delegated to their lifecycle, outside the
Scalping cycle. Configuration/catalog/capacity changes invalidate incompatible
checkpoints; merely validating the same evidence at a later time does not.

Telemetry includes requested and achieved revisit separately, useful fractions
including rejected observations, warmup, provenance, selection/promotion/demotion
times, discovery p50/p95/max ages, touched-by-window and fresh useful coverage,
family/source counts, event-to-promotion and time-to-warmup. Never-observed ages
are lower bounds from opening. Missed/late discovery remains NO_VERIFICADO unless
future causal comparisons supply the counterfactual evidence; the report always
sets `all_movements_detected=false`.

## Running the reusable SHADOW tooling

`scripts/rc6_dynamic_universe_shadow.py` accepts a private JSON evidence bundle
and optional existing runtime database. Required keys are `as_of`, `session_open`,
`catalog` (full exact READY identities), `safety`, `sessions` (audited calendar),
`preopen_cutoff` and `frozen_at`. Safety requires PAPER/SIMULATION,
`real_orders_sent=0` and `real_routes=NOT_CALLED`. Optional `history`,
`preopen_observations`, `intraday_history`, `observations`, `sources` and
`capacity_report` carry actual evidence with both availability clocks and units.
Policy/hypothesis overrides and capacity configuration are explicit inputs and
are fingerprinted, never inferred from later returns.

Invoke `python scripts/rc6_dynamic_universe_shadow.py --input BUNDLE.json
--out REPORT.json --checkpoint CHECKPOINT.json [--db EXISTING_PAPER.db]`.
Create the frozen preopen before opening and reuse the checkpoint in-wheel;
missing in-wheel preopen is rejected rather than reconstructed retrospectively.
The DB bridge opens SQLite `mode=ro`/`query_only`, with bounded queries, no schema
initialization, provider or broker. It returns every READY identity, fails closed
instead of truncating that catalogue, and leaves non-ready financial inventory
unchanged. Mutable intraday revisions must have `last_verified_at` available by
the cutoff and are not backdated. No unverified interval volume is manufactured.
Reports/checkpoints are locked, atomically replaced private files distinct from
inputs; the CLI does not install a host service or join a productive writer.

`.github/workflows/rc6-ppi-capacity-shadow.yml` is reusable manual tooling because
existing host audit workflows do not provide a bounded endpoint/batch/cadence
PPI benchmark. It has no schedule, SSH, host mount or production secret retrieval.
It checks the exact manually selected SHA before optional dedicated read-only
runner credentials, validates inputs and transport guards, then emits a sanitized
artifact. Its default 30 s cadence is a hypothesis passed explicitly to the CLI.
The real SDK is constrained to a single login and audited market-data routes;
HTTP 429/session failure stop immediately, repeated 408/5xx stop via circuit,
with serial/global request and time caps and no SDK retry/refresh loop. Missing
credentials or non-OPEN calendar produce NO_VERIFICADO without provider calls.

## Validation and concurrent ownership

Regression coverage includes causal source/publication clocks, immutable freeze,
full READY retention, cold/event warmup, prospective outside-basket promotion,
dense HOT and discovery coverage, open priority/overflow, shared serial budgets,
checkpoint invalidation/restart, exact identities/units, specialized routing,
native failure scoping, read-only mutable runtime history, actual SDK/wire error
guards, calendar honesty, real-route blocking and absence of BYMA API/PPI Watch
changes. A minimal pinned measurement environment also passes the SDK guard suite.
The authoritative final SHA/tree/test totals, Predeploy V2 run, artifact ID/digest
and reconciled-workstream results are recorded in the PR handoff after freezing
the candidate; baseline CI is never reused as validation of this branch.

Reconciliation inputs are #456 `b8c95c10459cb8ede2925da491730f5988b29561`
(includes #454), #457 `77c4e13768b252a8e7a614a0653b0b0dabb1ed02`
(includes #450), #453 `d738db79a452c699b50aed00ac539e57e1cc3b04`, and #455
`84528216cd85060075b63be25e71876e1b742b0c`. None is merged by this workstream.
#453's explicit FUTUROS signal allowlist and dedicated `_on_future_quote` binding
take precedence when reconciled; WS-PERF-03 neither adds FUTUROS authority nor
changes its `_future_cost`, `_future_exposure`, `_future_locked_admission`,
`_open_future`, `_close_future`, `_on_future_quote`, family lifecycle, contract
policy or `bw_daily_risk.DailyRisk`.
#456's economic laboratory is an explicit dependency: standalone absence reports
NO_VERIFICADO, while reconciliation exercises the actual APIs and costs.

ARTEFACTO_VALIDADO means the exact candidate and these regression/SHADOW contracts
passed the recorded gates. It does not prove factual profit, complete real-time
radar, a new productive limit, fresh multi-provider coverage, broker account
terms, continuous target-hit probability or OOS calibration/edge. Today is a
Saturday CLOSED: the actual-clock benchmark emits MARKET_NOT_OPEN and
NO_VERIFICADO, with zero provider requests and real orders. In-session PPI
capacity, prospective discovery delay/coverage and future OOS outcomes remain
pending. No merge, deployment or PPI Watch operation is part of this mission.

Offline reconciliation is conflict-free across all four exact heads. The
combined governed suite executed 2735 tests: 2731 passed, four FUTUROS failures.
Those same four fail on untouched #453 alone (12 targeted, 8 passed/4 failed),
including `no such table: paper_future_positions`; they are an existing external
owner blocker, not new failures introduced by WS-PERF-03. All six FUTUROS broker
methods are AST-identical to #453; DailyRisk, contract policy and family lifecycle
are byte-identical. No external-owner implementation is corrected or copied in
this PR. Future consolidated integration must revalidate a corrected #453 head.
The new SHADOW exit adapter passed against the actual #456 APIs in that combined
suite, including MFE/MAE, identical entry/path hashes and censored EOD coverage.

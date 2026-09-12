# POROTA TRADING — CHECKPOINT PPI FULL INGEST — 2026-09-12

## Scope / precedence

Binding source precedence for RC6 historical completion:

1. PPI productive API.
2. PPI authenticated web/read-only scraping only for gaps left by the API.
3. InvertirOnline only as tertiary residual fallback.

Historical ingestion is independent from READY_PAPER. No real-order route is enabled by this work.

## Runtime safety baseline

- Host runtime SHA: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`.
- Runtime mode during proofs and ingestion: `PRODUCTION_PAPER`.
- `real_orders_sent=0` before and after completed stages.
- `ORDER_ROUTES=NOT_CALLED`.
- Market phase during this work: `CLOSED`.

## Productive PPI configuration proved live

PPI currently declares these 15 instrument types through its productive configuration API:

`BONOS`, `LETRAS`, `NOBAC`, `LEBAC`, `ON`, `FCI`, `CAUCIONES`, `ACCIONES`, `ETF`, `CEDEARS`, `OPCIONES`, `FUTUROS`, `LICITACIONES`, `ACCIONES-USA`, `FCI-EXTERIOR`.

Markets declared: `ROFEX`, `OTC`, `NYSE`, `NASDAQ`, `BYMA`.

The normalized live operational catalog contains 901 identities: 889 AVAILABLE + 12 STALE. The 12 STALE are 9 FUTUROS and 3 LETRAS. Current catalog families/counts: ACCIONES 55; BONOS 42; CAUCIONES 10; CEDEARS 191; FUTUROS 52 total (43 AVAILABLE + 9 STALE); LETRAS 23 total (20 AVAILABLE + 3 STALE); ON 91; OPCIONES 437.

Seven PPI-declared types were absent from that normalized operational catalog: `NOBAC`, `LEBAC`, `FCI`, `ETF`, `LICITACIONES`, `ACCIONES-USA`, `FCI-EXTERIOR`.

## Read-only discovery of previously absent PPI families — GREEN

Workflow: `.github/workflows/rc6-ppi-declared-missing-family-probe-20260912.yml`
Run: `34670448946`
Job: `103490697568`
Conclusion: SUCCESS.

The probe executed 192 authenticated read-only PPI requests, with zero blocked calls, one login, no host mutation and no order routes. It found **4,239 unique normalized identities** in the successful responses, proving that the 901-identity operational catalog is not the full information universe exposed by PPI.

Normalized unique discoveries by returned PPI family:

- `ACCIONES-USA`: **3,075** unique identities across NASDAQ/NYSE. The requested-family probe had 32 queries: 24 HTTP OK and 8 read timeouts/exceptions, so this count is a lower bound, not certified exhaustive yet.
- `ETF`: **105** unique identities across NASDAQ/NYSE.
- `FCI`: **1,040** unique identities on BYMA.
- `LICITACIONES`: **18** unique identities on BYMA.
- `LEBACS`: **1** unique returned identity (`CEDI`) discovered while probing the PPI-declared legacy families. This returned family label differs from the configuration enum `LEBAC`/`NOBAC` and must be reconciled explicitly rather than silently renamed.
- `FCI-EXTERIOR`: 39 HTTP-OK searches, all empty in this probe.
- `LEBAC`: 10 HTTP-OK searches, all empty in this probe.
- `NOBAC`: the requested NOBAC searches returned records, but normalization exposed the returned family as `LEBACS`; this is a provider taxonomy mismatch requiring evidence-preserving reconciliation.

This discovery does **not** promote these thousands of identities to the operational/PAPER catalog. They are data-universe evidence only until the execution universe is separated safely from the information universe.

## Five-family historical API proof — GREEN

Workflow: `.github/workflows/rc6-ppi-five-family-history-proof-20260912.yml`
Run: `34670203665`
Job: `103490019507`
Conclusion: SUCCESS.

Three live identities per family were queried read-only for the trailing 365-day window:

- ON: 3/3 HTTP OK; 507 provider rows; 470 rows passed current OHLCV validation; all 3 had data.
- LETRAS: 3/3 HTTP OK; 118 provider rows; 115 valid; 1/3 had data and 2/3 returned a valid empty response.
- OPCIONES: 3/3 HTTP OK; 38 provider rows; 35 valid; 2/3 had data and 1/3 returned a valid empty response.
- FUTUROS: 3/3 HTTP OK; 160 provider rows; 57 valid; all 3 had data.
- CAUCIONES: 3/3 HTTP OK; 429 provider rows; 395 valid; all 3 had data.

This proves that PPI productive historical API support is not limited to ACCIONES/CEDEARS/BONOS: the endpoint also returns historical payloads for ON, LETRAS, OPCIONES, FUTUROS and CAUCIONES. Empty payloads and validation rejections must still be classified per identity.

## Stage-1 actual ingestion — GREEN

Workflow: `.github/workflows/rc6-ppi-five-family-ingest-stage1-20260912.yml`
Run: `34670365124`
Job: `103490463245`
Conclusion: SUCCESS.

90 identities were actually ingested from PPI API into Porota history evidence/store:

- CAUCIONES: 10 tested; 1 VALID_PAYLOAD; 9 PARTIAL; 0 hard empty/error; 1,451 valid rows.
- FUTUROS: 20 tested; 2 VALID_PAYLOAD; 13 PARTIAL; 5 EMPTY_OR_INVALID; 0 ERROR; 487 valid rows.
- LETRAS: 20 tested; 1 VALID_PAYLOAD; 15 PARTIAL; 4 EMPTY_OR_INVALID; 0 ERROR; 1,162 valid rows.
- ON: 20 tested; 0 VALID_PAYLOAD; 7 PARTIAL; 13 EMPTY_OR_INVALID; 0 ERROR; 617 valid rows.
- OPCIONES: 20 tested; 2 VALID_PAYLOAD; 7 PARTIAL; 11 EMPTY_OR_INVALID; 0 ERROR; 155 valid rows.

Total stage-1: **3,872 valid historical rows** from 90 PPI identities. Reader metrics: authenticated=true; 91 allowed HTTP calls; 0 blocked; 1 login.

PPI history source after stage-1: AMARILLO because current semantics reserve legacy COMPLETE status for payloads with zero rejected rows. Stage-1 had 6 complete identities, 51 partial-but-usable identities, and 33 hard empty/invalid identities. Partial valid rows are still passed to History Store v2 and raw evidence is retained; they are not silently discarded.

Legacy complete-only table after stage-1: ACCIONES 30/7,136 rows; BONOS 1/245; CAUCIONES 1/184; CEDEARS 37/8,518; FUTUROS 2/18; LETRAS 1/11; OPCIONES 2/27. This legacy table is NOT the correct denominator for usable canonical-v2 coverage.

## Root cause confirmed

The production `_historical_targets()` currently selects from `candidate_universe` with `status='AVAILABLE' AND (can_simulate=1 OR instrument_type='INDICES')`. This incorrectly couples data ingestion to paper-operability. Operational workflows in this branch bypass that selector safely and read from the normalized PPI catalog so history can be ingested even when a family is not READY_PAPER.

A permanent forward code fix is still required and must pass CI before deploy; the current runtime engine has not been modified by these ingestion workflows.

A second architectural issue is now explicit: the **information universe** exposed by PPI is much larger than the **operational/PAPER universe**. Persisting 3,075 ACCIONES-USA + 1,040 FCI + 105 ETF directly into the current operational catalog would alter runtime universe rotation. Therefore the new discoveries are being persisted into a separate shadow evidence catalog first, with no PAPER promotion.

## Work launched after stage-1

1. `RC6 PPI catalog history ingest stage2 2026-09-12`, run `34670495541`: next bounded 100-identity PPI history batch, serialized on the common PPI lock.
2. `RC6 PPI catalog history first-pass complete 2026-09-12`, repaired commit `b9953d85f2dcc67666b94f47833c189a58d6eb61`, run `34670952207`: valid workflow now running/waiting on the same serialization lock. It completes all still-unattempted identities in the current 901-row normalized catalog in bounded batches.
3. `RC6 PPI full discovery shadow persist 2026-09-12`: replays the absent-family discovery and persists results only into `ppi_full_discovery_shadow` + query-evidence tables. It does not mutate `financial_instrument_catalog` or the operational universe.
4. `RC6 PPI shadow-family history proof 2026-09-12`: waits for the shadow catalog and then tests the historical endpoint on representative newly discovered PPI families before any mass history run on thousands of identities.
5. `RC6 PPI history depth proof 2026-09-12`: serialized read-only probe of older PPI windows (1-2y, 2-3y, 3-5y, 5-10y) to determine whether the existing 365-day downloader is a Porota limit rather than a PPI limit.

## Remaining definition of DONE

The first 365-day pass is only the first layer. Full PPI closure additionally requires:

- finish the 901-identity current normalized-catalog first pass;
- persist the full discovered information universe in shadow without changing PAPER behavior;
- reconcile provider taxonomy (`LEBAC`/`NOBAC` configuration vs returned `LEBACS`) and retry the 8 ACCIONES-USA timeouts so the discovery denominator is certified rather than a lower bound;
- prove historical support semantics for the newly discovered ACCIONES-USA, ETF, FCI, LICITACIONES/LEBACS families;
- determine maximum historical depth available from PPI by walking older date windows, rather than assuming the current 365-day downloader window is the provider limit;
- classify empty/invalid and partial histories; preserve raw evidence and distinguish no trades/no history from validation-model mismatches;
- fill API residual gaps from authenticated PPI web/scraping read-only;
- only after both PPI layers are exhausted, send the final residual gap list to IOL;
- implement and CI-test permanent separation of historical/data universe from `can_simulate`/PAPER execution universe before deployment.

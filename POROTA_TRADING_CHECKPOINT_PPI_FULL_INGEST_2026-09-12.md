# POROTA TRADING — CHECKPOINT PPI FULL INGEST — 2026-09-12

## Scope / precedence

Binding source precedence for RC6 historical completion:

1. PPI productive API.
2. PPI authenticated web/read-only scraping only for gaps left by the API.
3. InvertirOnline only as tertiary residual fallback.

Historical ingestion is independent from READY_PAPER. No real-order route is enabled by this work.

## Runtime safety baseline

- Host runtime SHA: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`.
- Runtime mode during proofs and stage-1 ingestion: `PRODUCTION_PAPER`.
- `real_orders_sent=0` before and after.
- `ORDER_ROUTES=NOT_CALLED`.
- Market phase during work: `CLOSED`.

## Productive PPI configuration proved live

PPI currently declares these 15 instrument types through its productive configuration API:

`BONOS`, `LETRAS`, `NOBAC`, `LEBAC`, `ON`, `FCI`, `CAUCIONES`, `ACCIONES`, `ETF`, `CEDEARS`, `OPCIONES`, `FUTUROS`, `LICITACIONES`, `ACCIONES-USA`, `FCI-EXTERIOR`.

Markets declared: `ROFEX`, `OTC`, `NYSE`, `NASDAQ`, `BYMA`.

The normalized live catalog currently contains 901 identities: 889 AVAILABLE + 12 STALE. The 12 STALE are 9 FUTUROS and 3 LETRAS. Current catalog families/counts: ACCIONES 55; BONOS 42; CAUCIONES 10; CEDEARS 191; FUTUROS 52 total (43 AVAILABLE + 9 STALE); LETRAS 23 total (20 AVAILABLE + 3 STALE); ON 91; OPCIONES 437.

Therefore seven PPI-declared types are not yet represented in the normalized live catalog and require explicit discovery/reconciliation: `NOBAC`, `LEBAC`, `FCI`, `ETF`, `LICITACIONES`, `ACCIONES-USA`, `FCI-EXTERIOR`.

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

## Work already launched after stage-1

1. `RC6 PPI declared missing family probe 2026-09-12`, run `34670448946`: read-only discovery of the seven PPI-declared family types absent from the normalized catalog. It serializes on the PPI history lock and does not persist probe results or call order routes.
2. `RC6 PPI catalog history ingest stage2 2026-09-12`, run `34670495541`: next bounded 100-identity PPI history batch, serialized on the same lock.
3. `RC6 PPI catalog history first-pass complete 2026-09-12`: waits for stage-2 coverage, then ingests every still-unattempted identity in the current 901-identity catalog in bounded batches of at most 100, with pauses between batches. It asserts `PRODUCTION_PAPER`, market CLOSED and `real_orders_sent=0`, and fails if any current catalog identity remains unattempted.

## Remaining definition of DONE

The first 365-day pass is only the first layer. Full PPI closure additionally requires:

- reconcile and persist identities discovered for the seven PPI-declared but absent family types;
- first-pass history for every reconciled PPI identity;
- determine the maximum historical depth available from PPI by walking older date windows rather than assuming the current 365-day downloader window is the provider limit;
- classify empty/invalid and partial histories; preserve raw evidence and distinguish no trades/no history from validation-model mismatches;
- fill API residual gaps from authenticated PPI web/scraping read-only;
- only after both PPI layers are exhausted, send the final residual gap list to IOL;
- implement and CI-test the permanent `_historical_targets()` decoupling from `can_simulate` before deployment.

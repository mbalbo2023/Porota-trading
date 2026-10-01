# CHECKPOINT 01 — WS-HISTORY-09 RUNTIME PROGRESS — 2026-10-01

## Evidence
- base integration SHA: 220501ce3929897ed59264cd70c2ae216a98b450
- PR: #396
- run: 36804275137 — GREEN
- artifact: ws-history-09-progress / 11137011708
- runtime: PRODUCTION_PAPER / WAITING_MARKET / MARKET_CLOSED
- real_orders_sent=0; real routes NOT_CALLED; PPI Watch untouched.

## Canonical history
- history_canonical_v2: 439,833 rows
- latest canonical date: 2026-09-28
- READY total: 6,955
- READY with exact canonical history: 1,913
- READY without exact canonical history: 5,042

## Coverage by family
- ACCIONES: 105/126
- BONOS: 27/1,823
- CEDEARS: 808/968
- FCI: 962/1,013
- LETRAS: 10/31
- OBLIGACIONES: 0/2,993
- OPCIONES: 1/1

## Cutoff repair
- cutoff: 2026-09-21
- targets: 1,094
- complete: 899
- failed: 195
- ppi_queries: 726
- state: PARTIAL
- state rows: 183 ALREADY_COVERED; 727 COMPLETE; 193 NO_NEW_VALID_ROWS; 2 PPI_QUERY_FAILED
- active repair process at audit time: none.

## State
- progress is real but historical closure remains BLOQUEADO/PARTIAL.
- no second repair was started by WS-HISTORY-09.
- evidence supports a single resumable repair run under the global mutex.

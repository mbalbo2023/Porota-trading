# RC6 Dynamic SHADOW systemic read repair — 2026-10-15

Issue #511, workstream `WS-RC6-SHADOW-SYSTEMIC-20261015`. This checkpoint describes the candidate, not a runtime validation. Product base `64583571846addb85038554dc56230bafbc7e6b4`; deployed OCI `sha256:07dba5eb33a3149783e3d0be767c22eb05afdd37f95075841a40bfa253634d64` remained unchanged during analysis. PPI Watch was not accessed or modified. PAPER/SHADOW only; real routes `NOT_CALLED`, `real_orders_sent=0`.

## ERROR → RCA → FIX → GUARD → TEST → EVIDENCE

| Stage | Finding |
| --- | --- |
| ERROR | Run 38694565268 attempts 2–4: `FAIL_CLOSED / SQLITE_INTERRUPT(9)` at runtime.py lines 75, 30, 56. The 2026-10-15 16:55 UTC read-only cut still saw `FAIL_CLOSED`, status line 46. |
| RCA | One 2-second progress deadline covered all SQLite statements **and** Python row conversion between them. Thus a later statement could be interrupted after successful preceding reads and conversions. `SQLITE_INTERRUPT(9)` is distinct from `SQLITE_BUSY`, physical I/O errors and WAL contention; none of those was demonstrated. The exact instantaneous source of load is still unverified. |
| MEASUREMENT | Manual-only Action 38772952791, artifact 11991149714, ZIP SHA256 `b6b050507c9cd47008b13c4b69504c0da5e832125daddce49becc6b059f3cbf7`: catalog 14,592 rows/121 ms, positions 2/1 ms, contracts 2,404/138 ms, intraday 11,896/311 ms, snapshots 2,244/189 ms. All five SQL stages completed with 1,238 ms remaining in this cut. Catalogue used `idx_shadow_catalog_order`, intraday `idx_shadow_intraday_event_jd`, snapshot used rowid lower bound and `idx_shadow_snapshot_observed_jd` subquery. WAL 31,987,712 bytes. This is one successful diagnostic sample, not proof of sustained margin under load. Observer cycles were 135–311 seconds. No OOM/restarts; ~5.11 GB disk free, ~290 MB available memory. |
| FIX | Read safety, catalogue, positions, contracts, points and snapshots from one SQLite `BEGIN` read snapshot. Finish all SQL extraction under the unchanged 2-second SQLite progress deadline, then release the WAL snapshot before converting rows in memory. Parse contract timestamps once each. Preserve every SQL predicate, ordering, row limit, identity and temporal cutoff. No table or PPI writer change. |
| GUARD | Explicit 2-second transformation cap after the SQL phase; any exhaustion remains fail-closed. Status records allowlisted stage, row counts, time, remaining budget, deadline flag and query plan on both success and failure. Original SQLite exception class and code remain intact; no SQL values, parameters, DB paths or exception text are persisted. No retry, stale fallback, coverage reduction or extra provider/broker call. |
| TEST | Real SQLite contract tests include deterministic deadline interruption, slow timestamp conversion, concurrent WAL mutation with a consistent observation generation, and a 235,634-row snapshot fixture with 14,592 catalog rows, 11,896 intraday rows, 2,404 contracts and a concurrent WAL writer. Late historical arrival is excluded; counts and plans are asserted. Targeted 55 tests passed locally. CI/Predeploy/runtime gates remain pending. |

The data equivalence argument is a relocation of the unchanged SQL statements and unchanged observation projection under a single snapshot. The WAL concurrency regression checks that an in-flight writer cannot mix later observations into an earlier safety/catalog generation. The 2-second SQLite budget is unchanged; total CPU conversion has its own finite cap. If live load still exhausts either cap, the worker must remain fail-closed and the stage metrics must guide a further fix-forward candidate. This checkpoint does not authorize a deploy.

## Acceptance state

- Monitor confirmation on `main`: run 38772145395, artifact 11991412859, verified digest, `previous_comparable=true`, P1 `DYNAMIC_SHADOW_FAIL_CLOSED`, Telegram `SENT` message ID 928. Manual cut 38772952791 confirmed continued P1 and message ID 929. This does not establish SHADOW recovery.
- Candidate product CI, frozen Predeploy V2, manifest/digest/SHA and final Deploy V2: **NO_VERIFICADO**.
- Three independent MARKET_OPEN observations with fresh SHADOW, data equivalence, PAPER/0 and stable resources: **NO_VERIFICADO**.
- DEPLOY_OWNER: **NOT_ACQUIRED**. No productive mutation during the market session.

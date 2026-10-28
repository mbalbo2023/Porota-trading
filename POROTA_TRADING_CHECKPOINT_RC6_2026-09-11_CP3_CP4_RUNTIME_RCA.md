# POROTA TRADING RC6 — CHECKPOINT CP3/CP4 RUNTIME RCA — 2026-09-11

Evidence cut: 2026-09-11T15:26:00Z
Canonical branch: `fix/rc6-w10-sector-map-binding-20260910`
Evidence candidate HEAD before this checkpoint commit: `89f2768cd07982f2e347ded95fb787246086de55`
Audited deployed Droplet HEAD: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Latest fully validated code/test baseline before diagnostic-only commits: `73024733c22b12fa302bf98baf5fe4991b048b9b`
Recovery policy: STRICT NO-ROLLBACK. Failure -> exact signature -> RCA -> smallest evidence-based forward fix -> affected validation + required regression.

## Safety invariants
- `VERSION=17.0.0-rc6`
- `MODE=PRODUCTION_PAPER`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `real_orders_sent=0`
- no real order routes; no `/Operar`
- Contract Evidence authenticated browser remains read-only/fail-closed
- GET/HEAD/OPTIONS only where applicable; non-read requests remain physically blocked
- no invented contract/economic values
- `GDELT=SHADOW_ONLY`

## CP1–CP8 matrix
| CP | Status | Current evidence / residual |
|---|---|---|
| CP1 | 🟢/🟡 | Fully validated baseline `73024733...`: integrated `1801 passed`, PAPER safety GREEN, no real-order routes. Diagnostic-only current lineage re-triggers W10/Post-W10 regression; final candidate/runtime certification still mandatory. |
| CP2 | 🟢/🟡 | Source/regression baseline GREEN; final PRODUCTION_PAPER ledger/dashboard live-wiring proof remains mandatory. |
| CP3 | 🟡 BLOCKING | Authentication blocker CLOSED: trusted-device reauthentication recovered and repeated scheduled collections authenticate. Current blocker moved downstream: routes are reached but target endpoint capture remains `endpoints=0`, importer writes `records=0`, DB freshness remains stale. Current blocked POSTs are overwhelmingly telemetry/support; safety remains GREEN. |
| CP4 | 🟡 BLOCKING | Deterministic simple-DLR mapper and current source wiring exist in repository, but deployed host/container execute the older `ew_a3_history_rc6.py` and do not contain/wire the mapper. All 40 FUTUROS remain `ALIGNMENT_UNVERIFIED`, rows=0, COMPLETE=0. This is now proven deployment/wiring debt, not an unknown algorithm defect. |
| CP5 | 🟢/🟡 | Source/regression baseline GREEN; `GDELT=SHADOW_ONLY`; final runtime/UX proof mandatory. |
| CP6 | 🟡 OPEN | Only evidence-based residuals: CP3 target endpoint/materialization/freshness and CP4 candidate code deployment/wiring + persistence. |
| CP7 | ⚪ PENDING | Requires CP1–CP6 closed; then clean-tree Waves 1–18 18/18 barrier across CODE+TESTS+INTEGRATION+DEPLOY+LIVE_WIRING+RENDER/ROUTES+DATA/FRESHNESS+CROSS-WAVE_REGRESSION+SAFETY. |
| CP8 | ⚪ PENDING | Final audited deploy/runtime/postflight + overnight/preopen + scheduler/timer/next-trigger + approved data acquisition verification only after CP7. |

## CP3 — material RCA change: authentication recovered
Workflow `RC6 CP3 Post-Backoff + CP4 Runtime Probe 2026-09-11`, run `34615602115`, CP3 job `103316626835`: SUCCESS, read-only.

Exact post-backoff chain:
- deployed host exact: `e47eeff...`; mutations NONE.
- CE timer active/enabled and firing.
- after the previous one-hour safe backoff, the browser first saw the expired session, then isolated reauth succeeded.
- reauth diagnostic settled on authenticated trading page; subsequent collector state repeatedly became `AUTHENTICATED_TRUSTED_DEVICE`.
- scheduled cycles at approximately 14:36, 14:45, 14:57, 15:07 and 15:18 UTC reached 2–5 quote routes and always kept `real_orders_sent=0`.
- every current cycle still reported `endpoints=0`.
- importer remained `records=0`, `state=AMARILLO`, while wrapper reported `GREEN_COLLECTION` because the invocation and safety checks succeeded.
- `contract_evidence_v2_current` remains 519 rows with max observed `2026-09-09T20:06:20.770262+00:00`; freshness age was ~43.25 h at the probe.

Therefore the old blocking chain `expired session -> failed reauth -> backoff` is no longer current. New exact chain:
`trusted auth GREEN -> route navigation GREEN -> target response capture EMPTY -> importer records=0 -> DB freshness stale`.

### CP3 blocked-path proof
Workflow `RC6 CP3 Blocked Paths + CP4 Deployed Wiring 2026-09-11`, run `34616066396`, CP3 job `103318185467`: SUCCESS, read-only.

Latest persisted successful capture:
- `contract_20260911T151656Z.json`
- auth `AUTHENTICATED_TRUSTED_DEVICE`
- routes=5
- endpoints=0
- blocked_nonread=21
- orders=0

The current blocked requests are telemetry/support endpoints such as PPI `/api/logger`, Zendesk session/support, Refiner, Clarity, Amplitude/Hotjar. They remain blocked. No current evidence shows a required Contract Evidence business endpoint being blocked by the read-only guard. One older historical capture showed `/api/Cotify/Ranking` POST and it also remained blocked; it is not evidence that this POST is needed for the current CE target pipeline.

Repository collector evidence at current branch:
- `rc6_trusted_browser_contract_collector.py` allows only GET/HEAD/OPTIONS.
- target response names are hardcoded to `InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `SubyacenteOpciones`, `DatosTecnicos`.
- `on_response` captures only GET responses whose URL contains one of those names.
- route dwell after DOMContentLoaded is 900 ms.

The next CP3 diagnostic must therefore inventory sanitized first-party GET response paths/status/content-type during authenticated `/Cotizaciones/...` route visits, with no query strings, bodies, headers or account data. This will distinguish endpoint-name drift from response-timing/trigger drift before any collector hotfix. Do not weaken POST blocking.

## CP4 — material RCA change: deterministic mapper not deployed/wired
Workflow `RC6 CP3 Endpoint + CP4 Futures RCA 2026-09-11`, run `34615763269`, CP4 job `103317164709`: SUCCESS, read-only.

Runtime database facts:
- `a3_history_ingest_state_rc6`: 40 FUTUROS rows.
- all 40 status `ALIGNMENT_UNVERIFIED`.
- all `rows_last=0`; `last_success_at=NULL`.
- each inspected row reports `CEM_SYMBOL_FAMILY_NOT_EXACT`.
- this affects both simple contracts such as `DLR/SEP26`, `DLR/DIC26`, `DLR/ENE27`, etc., and intentionally unsupported variants/spreads.
- `catalog_family_coverage` sees FUTUROS instruments (`observed_count=43`) but `ready_paper_count=0`.
- no FUTUROS rows are present in production history evidence queried by the probe.
- A3 RECONCILE/DAILY runs select 40 but report `matched=0`, `unmatched=40`, `complete=0`, `versions_appended=0`, `canonical_updates=0`, `execution_allowed=false`.

Scheduler/runtime infrastructure itself is present:
- daily timer next 21:30 UTC;
- reconcile timer next 02:30 UTC;
- weekend-deep timer next 06:00 UTC;
- history-postclose timer active as well.

### Exact deployed wiring proof
Workflow `RC6 CP3 Blocked Paths + CP4 Deployed Wiring 2026-09-11`, run `34616066396`, CP4 job `103318185186`: SUCCESS, read-only.

Systemd executes:
`docker exec -e A3_HISTORY_MODE=%i porota_production_observer python /app/ex_a3_history_job_rc6.py %i`.

Deployed host and container both have the same legacy `ew_a3_history_rc6.py` hash:
`825baa65e8beaae61dda2176e0665236d3f40b822f1917f40376b2f3cf9247f8`.

That deployed source shows the old unmatched path but no import/use of `fd_a3_identity_mapper_rc6`. The mapper file hash was absent from the deployed host/container probe. By contrast, the current repository `ew_a3_history_rc6.py` imports `fd_a3_identity_mapper_rc6`, uses `porota_to_a3_dlr`, `_resolve_source_identity`, and preserves variants/spreads fail-closed. The repository mapper deterministically maps simple examples such as `DLR/SEP26 -> DLR092026` and rejects unsupported forms.

CP4 RCA is therefore exact:
`correct deterministic mapper/source exists in candidate -> deployed image/host predates it -> systemd invokes old container code -> all FUTUROS remain unmatched -> no persistence`.

The smallest forward fix is a targeted candidate deployment/wiring of the already-tested A3 source+mapper into the PRODUCTION_PAPER image/runtime, followed by a controlled read-only A3 run proving:
1. simple DLR contracts map deterministically;
2. variants/spreads stay fail-closed;
3. historical rows persist/version correctly;
4. real orders remain 0;
5. affected A3 tests + required W10/Post-W10 regressions remain GREEN.
No rollback.

## Supporting CP4 source evidence
Current repository `fd_a3_identity_mapper_rc6.py`:
- explicit simple grammar only: `DLR/<MON><YY|YYYY>` <-> `DLRMMYYYY`;
- unsupported/fuzzy forms raise `A3IdentityError`;
- canonical_write remains `DENY`.

Current repository `ew_a3_history_rc6.py`:
- imports mapper functions;
- `_resolve_source_identity` first permits exact direct source identity, then deterministic simple-DLR mapping only for FUTUROS;
- source lookup must exist and round-trip alignment must be exact;
- variants/options/spreads remain `ALIGNMENT_UNVERIFIED`;
- execution path remains historical/read-only and `execution_allowed=false`.

## Active validation state at evidence cut
Current diagnostic HEAD: `89f2768cd07982f2e347ded95fb787246086de55`.
Push-triggered workflows include:
- CP3/CP4 diagnostic run `34616066396`: both jobs SUCCESS.
- required W10/Post-W10 regressions for this current diagnostic lineage were queued/running at evidence cut; do not inherit final GREEN until their conclusions are observed.

Prior fully validated code baseline remains `73024733...` with integrated run `34601120408`, job `103268380923`: SUCCESS, `1801 passed`, PAPER safety GREEN, no real-order routes.

## Exact dependency graph / next actions
Parallel safe tracks:
1. **CP3 endpoint discovery:** sanitized authenticated first-party GET-path inventory -> prove endpoint drift vs timing -> smallest collector target/timing forward fix -> targeted capture -> importer -> DB freshness proof.
2. **CP4 runtime wiring:** package/deploy tested mapper + current A3 source into candidate PRODUCTION_PAPER runtime -> controlled A3 historical run -> deterministic simple-DLR persistence proof, variants/spreads fail-closed -> targeted regression.
3. **CP1/CP2/CP5:** observe current W10/Post-W10 regression conclusions and revalidate only if material code changes enter.
4. **CP6:** retain only surviving evidence-based residuals.

Then:
`CP3 + CP4 + CP1/CP2/CP5 -> CP6 closure -> CP7 clean-tree W1-W18 18/18 -> final deploy -> CP8 audited postflight/readiness`.

GLOBAL_RC6=YELLOW
GO_18_OF_18=NO
READY_FOR_FINAL_DEPLOY=NO

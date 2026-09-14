# POROTA TRADING RC6 — CONTEXT HANDOFF

Updated evidence cut: 2026-09-11T15:26:00Z
Canonical branch: `fix/rc6-w10-sector-map-binding-20260910`
Latest evidence candidate before checkpoint/handoff docs: `89f2768cd07982f2e347ded95fb787246086de55`
Checkpoint commit: `134da03fcf5a1b816abeccab57a0901148cddfeb`
Audited deployed Droplet HEAD: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Latest fully validated code/test baseline before diagnostic-only commits: `73024733c22b12fa302bf98baf5fe4991b048b9b`
Latest milestone checkpoint: `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP3_CP4_RUNTIME_RCA.md`

## Compact CP1–CP8 matrix
- CP1: 🟢/🟡 — baseline integrated GREEN (`1801 passed`); current diagnostic-lineage regressions must complete; final runtime certification pending.
- CP2: 🟢/🟡 — source/regression baseline GREEN; final PRODUCTION_PAPER ledger/dashboard live-wiring proof pending.
- CP3: 🟡 BLOCKING — authentication RECOVERED. Timer and trusted-device login work repeatedly, routes are reached, orders=0. New blocker: target endpoint capture remains empty -> importer records=0 -> CE DB freshness stale.
- CP4: 🟡 BLOCKING — repository deterministic simple-DLR mapper/source is correct/tested, but deployed host/container still run legacy `ew_a3_history_rc6.py` and mapper is not deployed/wired. All 40 FUTUROS remain ALIGNMENT_UNVERIFIED, rows=0, COMPLETE=0.
- CP5: 🟢/🟡 — source/regression baseline GREEN; final runtime/UX pending; `GDELT=SHADOW_ONLY`.
- CP6: 🟡 OPEN — residuals only: CP3 endpoint/materialization/freshness + CP4 targeted deploy/wiring/persistence.
- CP7: ⚪ PENDING clean-tree Waves1–18 18/18 canonical barrier.
- CP8: ⚪ PENDING final audited deploy/postflight + overnight/preopen + timers/schedulers/data jobs/freshness.

## Safety flags
`VERSION=17.0.0-rc6`
`MODE=PRODUCTION_PAPER`
`REAL_ORDER_CAPABILITY=BLOCKED`
`real_orders_sent=0`
no real order routes; no `/Operar`
PPI Contract Evidence read-only/fail-closed; non-read methods remain blocked
no invented contract/economic values
`GDELT=SHADOW_ONLY`
STRICT NO-ROLLBACK; RCA -> smallest forward fix -> targeted validation + required regression.

## Latest CP3 evidence
### Post-backoff auth recovery
Run `34615602115`, CP3 job `103316626835`: SUCCESS, read-only.
- host `e47eeff...`, mutations NONE.
- safe backoff expired; isolated reauth succeeded and trusted-device session recovered.
- repeated scheduled collections thereafter return `AUTHENTICATED_TRUSTED_DEVICE`.
- routes=2–5, endpoints=0, orders=0.
- importer repeatedly `records=0`, `state=AMARILLO`.
- CE current table=519 rows; max observed `2026-09-09T20:06:20.770262+00:00`, ~43.25h stale at probe.

Current CP3 causal chain:
`AUTH GREEN -> quote routes reached -> target responses not captured -> endpoints=0 -> records=0 -> DB stale`.

### Blocked-path proof
Run `34616066396`, CP3 job `103318185467`: SUCCESS.
Latest successful persisted capture `contract_20260911T151656Z.json`:
- auth trusted
- routes=5
- endpoints=0
- blocked_nonread=21
- orders=0

Current blocked requests are telemetry/support (PPI logger, Zendesk, Refiner, Clarity/Amplitude/Hotjar) and remain blocked. There is no current evidence that a required Contract Evidence business endpoint is being blocked. The branch collector only recognizes five legacy GET target-name patterns (`InstrumentosOperables`, `CaucionesOperables`, `ConfiguracionOperatoriaSimplificada`, `SubyacenteOpciones`, `DatosTecnicos`) and waits 900 ms after route DOM load. Next safe RCA is sanitized first-party GET path/status/content-type inventory with no query/body/header/account data to prove endpoint-name drift vs response timing.

## Latest CP4 evidence
### Runtime state
Run `34615763269`, CP4 job `103317164709`: SUCCESS, read-only.
- A3 state has 40 FUTUROS, all `ALIGNMENT_UNVERIFIED`, all rows_last=0, last_success=NULL.
- simple DLR contracts are failing together with intentionally unsupported variants/spreads under `CEM_SYMBOL_FAMILY_NOT_EXACT`.
- daily/reconcile runs select 40, matched=0, unmatched=40, complete=0, canonical_updates=0, execution_allowed=false.
- FUTUROS observed_count=43 but ready_paper_count=0.
- daily/reconcile/weekend A3 timers are installed and scheduled; scheduler absence is NOT the blocker.

### Exact deployed wiring proof
Run `34616066396`, CP4 job `103318185186`: SUCCESS, read-only.
Systemd invokes `/app/ex_a3_history_job_rc6.py` inside `porota_production_observer`.
Host and container legacy `ew_a3_history_rc6.py` hash:
`825baa65e8beaae61dda2176e0665236d3f40b822f1917f40376b2f3cf9247f8`.
The deployed source does not import/use `fd_a3_identity_mapper_rc6`; mapper file was absent in the deployed probe.

Current repository source DOES import the deterministic mapper and uses `_resolve_source_identity` / `porota_to_a3_dlr`, mapping only proven simple DLR identities while keeping variants/spreads/options fail-closed. Therefore CP4 blocker is a proven candidate-vs-runtime deployment/wiring mismatch, not missing scheduler and not an unresolved mapper algorithm.

## Canonical supporting baseline
- fully validated baseline candidate `73024733c22b12fa302bf98baf5fe4991b048b9b`
- integrated run `34601120408`, job `103268380923`: SUCCESS, `1801 passed`, PAPER safety GREEN, no real-order routes.
- companion Post-W10 run `34601120465`: SUCCESS.
- current diagnostic HEAD `89f2768...` generated run `34616066396` SUCCESS; its push-triggered W10/Post-W10 regressions were queued/running at this evidence cut and must be observed before inheriting GREEN.

## Exact next dependency path
Parallel safe tracks:
1. CP3: sanitized authenticated first-party GET response-path inventory -> endpoint/timing RCA -> smallest collector fix -> fresh capture -> importer -> CE freshness proof.
2. CP4: targeted forward deployment/wiring of already-tested mapper + current A3 source into PRODUCTION_PAPER runtime -> controlled historical read-only run -> prove simple DLR persistence while variants/spreads remain fail-closed -> affected + W10/Post-W10 regression.
3. CP1/CP2/CP5: finish/observe current required regressions; revalidate on material code/runtime change.
4. CP6: only surviving evidence-based gaps.

Then:
`CP3 + CP4 + CP1/CP2/CP5 -> CP6 closure -> CP7 clean-tree W1-W18 18/18 -> final deploy -> CP8 audited postflight/readiness`.

GLOBAL_RC6=YELLOW
GO_18_OF_18=NO
READY_FOR_FINAL_DEPLOY=NO


## REGLAS OBLIGATORIAS DE ACCESIBILIDAD DEL OPERADOR — persistentes

El operador Tincho tiene cuadriplejia, usa Android/Termius y no puede escribir manualmente. Todo chat debe tratarlo como restricción permanente antes de dar instrucciones, sin volver a pedirle que lo repita.

- No solicitar tipeo manual, selección manual de salida, ni correcciones de comandos línea por línea. Preferir voz y un único bloque íntegro para pegar.
- Entregar scripts operativos como .sh. Evitar prompts interactivos alimentados por stdin del bloque pegado; si son inevitables, leer desde /dev/tty y aceptar pegado en el prompt. Nunca encadenar un heredoc de instalación con una ejecución que consuma stdin interactivo sin separar ambas entradas o usar /dev/tty.
- Toda salida de diagnóstico/script debe ser breve, saneada y preparada para copiar de vuelta. En Termius emitir OSC 52 hacia el portapapeles con terminadores BEL y ST y reportar exactamente PORTAPAPELES_OSC52=EMITIDO cuando se emita; usar termux-clipboard-set si se ejecuta localmente en Termux. Si el canal no admite portapapeles, decirlo claramente y no simular éxito.
- Nunca copiar secretos, tokens, cookies ni contenido de .env al portapapeles o a la salida. Mostrar estado/nombres de variables únicamente.
- Dar pasos adaptados a voz/pegado y detenerse para recibir el resultado cuando un paso requiera confirmación operativa del usuario.

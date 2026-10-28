# POROTA TRADING RC6 — CHECKPOINT CP2/CP4/CP5 SOURCE PREP

Fecha: 2026-09-11
Branch: `fix/rc6-w10-sector-map-binding-20260910`
Commit de preparación de fuentes: `9e58e33adf042fe4af3e2cba6196cd7f75086e94`
Runtime productivo PAPER: aún `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Runtime mutado por este paso: NO
Modo/seguridad: `PRODUCTION_PAPER`, real orders observadas 0, generic news feed intencionalmente OFF.

## Fuentes preparadas, todavía no cableadas/deployadas

### CP2 — historial operacional diario real
Se agregó `rc6_validation_operational_daily.py` y test focalizado.
- abre `observer_v17.db` sólo `mode=ro` + `PRAGMA query_only`;
- exige `PRODUCTION_PAPER` y `real_orders_sent=0`;
- agrupa en zona `America/Argentina/Buenos_Aires` evidencia real de `paper_events`, posiciones, decisiones y fills;
- calcula aperturas/cierres/PnL realizado por día;
- NO escribe en DB;
- NO modifica ni auto-promueve M0-M11;
- está separado del `validation_campaign_rc6.jsonl`, que seguirá siendo ledger de campaña/auditoría.

Pendiente CP2: cablearlo en `/validacion`, reemplazar la confusa sección vacía por actividad PAPER real + ledger de campaña separado, y aplicar `<thead>`/sticky headers a tablas afectadas. Requiere CI focalizado antes de deploy.

### CP4 — mapper A3 exacto
Se materializó en current branch `fd_a3_identity_mapper_rc6.py` y test focalizado, tomado de la rama histórica ya auditada pero con nomenclatura RC6.
- sólo equivalencia determinística DLR (`DLR/SEP26` ↔ `DLR092026`);
- fail-closed para otros productos/formatos;
- no network, no DB, no broker, no órdenes;
- `canonical_write=DENY`.

Pendiente CP4: demostrar que los 40 FUTURES actuales corresponden a formas cubiertas por ese mapper antes de conectarlo a `ew_a3_history_rc6.py`. Si no se prueba, A3 queda `ALIGNMENT_UNVERIFIED`; no habrá fuzzy matching.

### CP5 — GDELT event-risk estructurado
Se agregó `rc6_gdelt_event_risk_job.py` y test focalizado.
- usa exclusivamente el feed RC6 GDELT estructurado existente;
- persiste en store dedicado `event_risk/gdelt_shadow_rc6.db`;
- dedup por `event_id`, provenance y timestamps;
- exige safety PAPER antes de correr;
- autoridad fija `SHADOW_ONLY`;
- no importa broker/órdenes ni `g_news_feed`;
- generic news feed queda `INTENTIONALLY_OFF_UNTOUCHED`.

Pendiente CP5: tests, systemd service/timer bounded y panel Riesgo mostrando status/freshness. No usar `financial_news`.

## CP3
No se cambió Contract Evidence aún. RCA previo demostró que el importer sí existe y el gap está en extracción/mapeo de evidencia útil. Antes de modificar collector/importer se requiere leer el importer host y estructura A3/capture exacta.

## Incidente CI observado
El workflow read-only `RC6 CP3 CP4 Final Readonly Proof 2026-09-11`, run `34547522462`, falló en dos intentos antes de ejecutar cualquier step (`steps=[]`). En el mismo momento el workflow W10 conocido/baseline (`run 34547522434`) también falló casi instantáneamente. Esto apunta a incidente de ejecución de GitHub Actions/runner, no a una regresión funcional demostrada de POROTA.

No se declara ningún source component GREEN hasta que CI vuelva a ejecutar tests. No se muta runtime a ciegas.

## Estado

`SOURCE_PREP_CP2=READY_FOR_TEST`
`SOURCE_PREP_CP4=READY_FOR_IDENTITY_PROOF`
`SOURCE_PREP_CP5=READY_FOR_TEST_AND_WIRING`
`CP3=RCA_PENDING_EXACT_HOST_READ`
`RUNTIME_MUTATED=NO`
`GLOBAL_RC6=YELLOW`
`GO_18_OF_18=NO`
`NEXT=WAIT_FOR_RUNNER_EXECUTION -> TEST_CP2_CP4_CP5 + READONLY_CP3_A3_PROOF -> FORWARD_FIX/DEPLOY`

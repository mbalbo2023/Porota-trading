# POROTA TRADING RC6 — CHECKPOINT RUNNER PROBE + CONTINUIDAD PARALELA

Fecha: 2026-09-11
Branch: `fix/rc6-w10-sector-map-binding-20260910`
Head al iniciar este checkpoint: `55a49bde78e22ee129e56102d045495d3c424e20`
Runtime desplegado y NO mutado: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Modo de runtime: `PRODUCTION_PAPER`
Órdenes reales observadas: `0`
Generic news feed: `INTENTIONALLY_OFF`
GDELT: sólo event-risk estructurado, `SHADOW_ONLY`, separado del news feed.

## Incidente GitHub Actions confirmado

Se reintentaron workflows existentes y todos fallaron antes de ejecutar steps:

- `RC6 CP3 CP4 Final Readonly Proof 2026-09-11`, run `34547522462`, reintentado hasta attempt 3.
  - jobs `ce_importer` y `a3_identity`: `failure`
  - `steps=null` / lista vacía
  - descarga de logs: `404 BlobNotFound`
- `RC6 W10 Forward Fix Validation`, run `34547940657`, reintentado: falla inmediata antes de steps.
- `RC6 Post-W10 Next Failure RCA`, run `34547940628`, reintentado: falla inmediata antes de steps.

Para aislar POROTA se creó un workflow mínimo sin checkout, secretos ni Python:
`.github/workflows/rc6-runner-probe-20260911.yml`

Run probe: `34548319691`
- `ubuntu_22` sobre `ubuntu-22.04`: failure antes del primer step
- `ubuntu_24` sobre `ubuntu-24.04`: failure antes del primer step
- ambos con `steps=null`

Conclusión: el incidente está antes de la ejecución del job/step y no demuestra una regresión de POROTA. La hipótesis queda en capa de provisión/política/cuenta de GitHub Actions; no se atribuye todavía a billing/cuota sin evidencia directa.

## Estado del runtime

No se hizo deploy, restart, rollback ni mutación de datos por este incidente.
El runtime certificado anterior sigue siendo la referencia segura:
`e47eeffcb94a9468ac7e7f610beee0869353a77e`.

## Trabajo que continúa sin esperar al runner

### CP2 — Validación/UX
- Fuente read-only `rc6_validation_operational_daily.py` preparada.
- Debe cablearse al dashboard `/validacion` como **Actividad PAPER por jornada**, separada del ledger M0–M11.
- El ledger de campaña/auditoría NO se inventa ni auto-rellena.
- Corregir visibilidad/sticky de cabeceras de tablas para tablet/Voice Access.

### CP5 — Riesgo/GDELT
- `rc6_gdelt_event_risk_job.py` preparado.
- Mantener generic news feed OFF.
- Cablear sólo lectura de `latest_status()` al panel Riesgo.
- Preparar unit/timer bounded separado; NO activar hasta que tests/CI puedan ejecutarse.

### CP3 — Scraping / Contract Evidence
- Infraestructura W12 continúa activa en runtime anterior.
- Gap funcional conocido: captura raw más reciente no se refleja completamente en canonical/materialización/dashboard.
- Workflow read-only para leer importer host está listo, pero no puede correr mientras Actions no asigne runner.
- No reabrir auth/trusted device.

### CP4 — Históricos / A3
- Mapper exacto DLR preparado y fail-closed.
- NO cablearlo hasta demostrar formas reales PPI/A3 de Futures en host.
- Históricos siguen operativos pero con cobertura incompleta; no bajar controles de calidad para aumentar cobertura artificialmente.

## Próximo orden

1. Materializar source-only CP2 + CP5 dashboard changes y tests focalizados.
2. Guardar checkpoint de source wiring.
3. Reprobar Actions con el runner probe.
4. Si runner vuelve: ejecutar en paralelo focused tests + CP3 host proof + CP4 identity proof.
5. Sólo con pruebas GREEN: preparar units/timers CP5, fixes CP3/CP4 necesarios, suite integrada y deploy transaccional.
6. Mantener `PRODUCTION_PAPER`, real orders 0 y sin rutas reales en cada gate.

`RUNNER_PROBE=RED_INFRASTRUCTURE`
`RUNTIME_MUTATED=NO`
`CP2=SOURCE_WIRING_IN_PROGRESS`
`CP3=HOST_PROOF_BLOCKED_BY_ACTIONS`
`CP4=IDENTITY_PROOF_BLOCKED_BY_ACTIONS`
`CP5=SOURCE_WIRING_IN_PROGRESS`
`GLOBAL_RC6=YELLOW`
`GO_18_OF_18=NO`

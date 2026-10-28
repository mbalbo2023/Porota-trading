# POROTA TRADING RC6 — CP1B RCA EXACTO PRE-FIX

Fecha: 2026-09-11
Branch: `fix/rc6-w10-sector-map-binding-20260910`
Branch HEAD auditado: `4ea32cdc263765959a41c3b6aa3e42ae25a5808c`
Runtime auditado: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Workflow: `RC6 CP2-CP5 Exact RCA 2026-09-11`
Run: `34547117227`
Jobs: 4/4 SUCCESS
Mutaciones runtime: NINGUNA
Modo: `PRODUCTION_PAPER`
Ordenes reales: 0
Feed general de noticias: `INTENTIONALLY_OFF` — prohibido reactivarlo.

## CP2 — RCA exacto UX + Historial por día

El ledger `validation_campaign_rc6.jsonl` no está roto: simplemente no tiene productor operacional.

Búsqueda runtime de `append_record(` encontró únicamente tests y la definición en `em_validation_campaign_rc6.py`; el dashboard `en_validation_project_dashboard_rc6.py` sólo lee. No existe service/timer de validation/campaign y `/app/data/validation` no aportó ledger operativo.

Por lo tanto, `Todavía no hay jornadas registradas en el ledger RC6` es verdad respecto de ese archivo, pero incompleta como UX porque sí existe actividad PAPER en otras tablas. No se deben inventar jornadas ni auto-promover M7-M11.

Forward-fix CP2:
1. normalizar tablas sin `<thead>` y mantener header visible/sticky durante scroll horizontal en tablet;
2. crear una fuente diaria separada, append-only y explícitamente operacional, que tome sólo evidencia real de DB/observer al cierre y NO cambie milestones M0-M11;
3. hacer que `Historial por día` muestre esa evidencia diaria y mantenga el campaign ledger independiente.

## CP3 — RCA exacto Contract Evidence

El pipeline host actual YA tiene importer correcto después de la captura:
- collector GET-only;
- captura en staging;
- instalación en `rc6_trusted` como root:GID observer 0640;
- ejecución de `rc6_contract_capture_importer.py` dentro del observer;
- postflight quick_check + PRODUCTION_PAPER + real_orders=0.

La última captura `contract_20260910T213256Z.json` está correctamente 0640 y es importable. Varias capturas anteriores siguen 0600 root:root por ser anteriores al fix; se preservan como evidencia histórica y no justifican reabrir autenticación.

El gap actual NO es ausencia de importer. El último due job/capture útil puede importar un run con `records=0` porque la captura no trae endpoints/material contractual aprovechable. El problema se desplaza al collector/selector/normalización y al mapeo FULL_BROWSER/DOM_SEMANTIC, no a auth ni al boundary de permisos ya reparado.

Forward-fix CP3:
- auditar y reparar extracción de rutas `/Cotizaciones/...` y material contractual GET-only;
- conservar `blocked_nonread` y cero `/Operar`;
- importador debe seguir fail-closed;
- demostrar que una captura fresh produce records/mapped canonical útiles o, si una familia realmente no expone datos, dejar razón explícita y no READY automático.

## CP4 — RCA exacto históricos / A3

Timers confirmados:
- daily incremental: Lun-Vie 18:30 ART;
- reconcile: Lun-Vie 23:30 ART;
- weekend deep: Sáb/Dom 03:00 ART;
- PPI postclose poll: cada 15 min;
- candle integrity: cada 10 min.

El A3 runner es read-only y sólo trabaja con FUTUROS/OPCIONES. Exige match exacto `(symbol,family)` contra catálogo A3. Cuando no existe, graba `ALIGNMENT_UNVERIFIED` con `CEM_SYMBOL_FAMILY_NOT_EXACT` y NO consulta precios. Ésta es la causa de los 40 Futures sin éxito.

No se relajará exactness. Existe branch histórica `feature/rc6-a3-identity-mapper-20260907`; debe auditarse y reutilizarse únicamente si demuestra mapping exacto y trazable.

El backfill planner mantiene PPI/IOL elegibles por defecto y A3 bloqueado salvo `a3_identity_aligned=True`; Data912 bloqueado. Esto es correcto y se conserva.

Forward-fix CP4:
- materializar mapper A3 sólo con evidencia exacta;
- mantener PPI postclose + reconcile + weekend activos;
- ampliar cobertura de noche bounded, sin duplicar timers ni aflojar validación OHLC;
- medir deltas de cobertura y causas de rechazo.

## CP5 — RCA exacto GDELT / event-risk

Wave7 integró las librerías `fi_event_risk_shadow_rc6.py` y `fk_gdelt_shadow_feed_rc6.py` y validó SHADOW_ONLY. W11 posterior fue esencialmente readiness/audit; no materializó un productor/scheduler persistente adicional.

En current tree:
- GDELT module existe;
- tests existen;
- dashboard importa el contrato event-risk;
- no hay service/timer productor GDELT/event-risk;
- no hay persistencia live específica demostrada.

Por eso W11 está `CODE_READY` pero no operacional.

Forward-fix CP5:
- crear job/timer dedicado de GDELT event-risk SHADOW, GET-only, bounded;
- persistir evidencia estructurada separada del feed general de noticias;
- aplicar TTL/freshness/dedup/provenance;
- dashboard Riesgo debe mostrar estado de event-risk/GDELT;
- autoridad de trading sigue NONE/SHADOW_ONLY;
- `financial_news`/generic-news permanece OFF y no se toca.

## Reglas para la mutación que sigue

- CP2, CP3, CP4 y CP5 se desarrollan en paralelo sobre archivos no conflictivos cuando sea posible.
- Cada CP debe tener tests focalizados antes de deploy.
- No desplegar tips históricos completos ni ramas enteras por divergencia de SHA.
- Host dirty no se limpia.
- Deploy final desde candidato exacto/clean source; no arrastrar untracked del host.
- No rollback automático.
- Cada CP cerrado genera su checkpoint propio con SHA + run IDs + runtime proof.

## Estado

`CP1B=GREEN_RCA_COMPLETE`
`RUNTIME_MUTATED=NO`
`GLOBAL_RC6=YELLOW`
`GO_18_OF_18=NO`
`GENERAL_NEWS_FEED=INTENTIONALLY_OFF`
`NEXT=CP2_CP3_CP4_CP5_SOURCE_FORWARD_FIX_PARALLEL`

# POROTA TRADING RC6 — CONTRACT IDENTITY WIRING + COMPLETION STRATEGY

Fecha: 2026-09-11
Wave: post-RC6 operacional, forward-only
Runtime host preservado: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`

## Invariantes
- `17.0.0-rc6`
- `PRODUCTION_PAPER`
- ejecución `SIMULATED`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `real_orders_sent=0`
- sin rutas reales de órdenes
- CP1–CP14 permanecen FROZEN

## Corrección de wiring Contract
Se corrigió el falso missing por identidad normalizada en Contract Evidence v2.

Contract v2 ya persistía `ticker`, `market` y `settlement` como columnas de identidad separadas de `evidence_json`. Los evaluadores de readiness fusionaban sólo `evidence_json`, por lo que reportaban esos tres campos como faltantes aun cuando estaban explícitamente probados.

Cambios:
- `cp_contract_evidence_v2_hf6.py`: bridge explícito de `ticker/market/settlement` sólo cuando no son `*`/`UNKNOWN`/placeholders. No infiere ni fabrica campos económicos.
- `cq_family_contract_rules_hf6.py`: utiliza el mismo bridge al construir contrato/dinámica y conserva provenance/source precedence.
- valores explícitos ya presentes en payload proveedor no son sobrescritos.
- `source_conflict`, fail-closed y `auto_activation_allowed=False` se preservan.

Commits de código:
- `5328e3a453b18a8009c887cb355eb064585466e2` — Contract readiness identity bridge.
- `b4e131cf842783fbc46922cd103d0403ce90cca3` — family rules identity bridge.

Proofs:
- Workflow `34658957708`, job `103457266017`: SUCCESS.
- Workflow `34659105237`, semantic step: SUCCESS.
- Unit: wildcard/UNKNOWN permanece missing; campos económicos genuinos permanecen missing; auto activation FALSE.

Impacto medido sobre datos reales, falsos missing eliminados:
- ACCIONES: ticker/market/settlement en 54 identidades explícitas.
- BONOS: ticker/market/settlement en 42.
- CEDEARS: ticker/market/settlement en 191.
- FUTUROS: ticker/market en 42 (settlement no estaba explícitamente probado de forma apta para bridge en esa medición).
- LETRAS: ticker/market/settlement en 17.
- ON: ticker/market/settlement en 84.

Aun después del bridge, `execution_complete=0` para todas las familias: quedan faltantes económicos/contractuales reales. Esto es correcto y no debe ocultarse.

## Importante: Contract readiness vs hot path PAPER
Auditoría de call sites del runtime f8adec no encontró `family_readiness_state()` ni `cq_contract_readiness_hf6` en `bv_paper_runtime.py`/`bf_production_paper_observer.py`. Los usos encontrados de Contract v2 son importadores/bridges, tests y dashboard. Por lo tanto este wiring corrige la evaluación contractual y evita futuros falsos bloqueos, pero NO es el gate que hoy esté rechazando aperturas del observer PAPER.

El runtime PAPER actual define `SAFE_PAPER_TYPES={ACCIONES, CEDEARS, BONOS, ETF/ETFS}` y exige además catálogo, identidad monetaria, book/quotes y demás gates. FUTUROS/OPCIONES y otras familias todavía requieren adaptador/sizing/simulador/exit e integración específica antes de READY_PAPER.

No se recreó el observer sólo para esta corrección porque el módulo de readiness no está en su hot path. Evitar ese restart preserva el runtime congelado y reduce riesgo sin perder funcionalidad actual.

## Scraping / XHR completion campaign
Sí se puede y se debe aprovechar el fin de semana para completar Contract Evidence, pero no mediante DOM ciego ni fabricando datos.

Orden de autoridad recomendado:
1. PPI structured/read-only API y XHR autenticado: identidad por instrumento, mercado, moneda, settlement, mínimos/steps y campos contractuales que exponga.
2. PPI DOM autenticado: tablas/celdas explícitas HTML/ARIA y páginas de detalle, sin inferir semántica ausente.
3. Documentación oficial PPI / especificaciones oficiales A3-BYMA-Matba Rofex según familia: ratios, nominales, láminas, ticks, multiplicadores, settlement/exercise, fee schedules, etc.
4. Fuentes históricas ya definidas: PPI > BYMA/A3 > IOL > auxiliares.
5. Derivados matemáticos sólo después de tener primitivas verificadas y con lineage (por ejemplo Greeks/IV no deben confundirse con un término contractual scrapeado).

Una campaña única coordinada puede recorrer todas las familias, pero debe ejecutarse secuencialmente/con locks en el Droplet 1vCPU/1GB para no solapar Chrome con history/A3. Cada familia se persiste, reconcilia y checkpointa; un fallo no habilita defaults inseguros.

## Binding / fail-closed
No relajar binding para aumentar aceptación. Si faltan términos verdaderos, el rechazo es correcto. La estrategia es:
- eliminar falsos missing (hecho en source/control),
- recolectar evidencia faltante,
- normalizar por identidad,
- reconciliar fuentes/conflictos,
- certificar simulador/cost/sizing/exit por familia,
- sólo entonces promover READY_PAPER_CANDIDATE / integración explícita.

El binding sectorial (`PAPER_SECTOR_CONCENTRATION_POLICY=BINDING`) es un gate distinto de Contract readiness y no debe confundirse con incompletitud contractual.

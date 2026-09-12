# POROTA TRADING RC6 — API UNIVERSE CONSOLIDATION

Fecha: 2026-09-12
Wave: post-RC6 operacional, forward-only

## Invariantes
- VERSION `17.0.0-rc6`
- MODE `PRODUCTION_PAPER`
- EXECUTION `SIMULATED`
- REAL_ORDER_CAPABILITY `BLOCKED`
- real_orders_sent `0`
- REAL_ORDER_ROUTES `NOT_CALLED`
- CP1–CP14 permanecen GREEN/FROZEN

## Runtime observado durante esta wave
La base RC6 certificada fue `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`.
Durante la auditoría read-only se observó que el host ya había avanzado a `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`, hijo directo de `f8adec8`, commit `fix(rc6): correct PAPER scalping economics and prove learning`.

No se ejecutó rollback ni modificación de runtime desde esta wave. Recheck read-only workflow `34660920043`, job `103463059740`: SUCCESS.
- `/health`: `status=ok`, `version=17.0.0-rc6`
- observer: running, restart=0, image `porota-trading-bot:17.0.0-rc6`
- dashboard: healthy
- DB host presente y activa
- `docker exec` smoke: OK, Python 3.11.16

Los fallos iniciales de auditoría fueron del diagnóstico, no de producción:
- `34660447904`: esperaba SHA exacto `f8adec8` cuando el host ya estaba en su descendiente `eddcc29`.
- `34660877133`: mismo cambio de SHA.
- primer pending-family audit: leyó `evidence_json` desde `contract_evidence_v2_current`; el payload real está en `contract_evidence_v2_snapshots`, join por `snapshot_id`.
- primeros proofs del módulo de autoridad fallaron por entorno CI (`pytest` ausente y luego `PYTHONPATH`); se corrigió el harness sin cambiar semántica. Proof final GREEN registrado abajo.

## Autoridad de universo: PPI API productiva
Auditoría live autenticada `34659647038` / `103459289644`: SUCCESS.

InstrumentTypes declarados por PPI producción (15):
`ACCIONES`, `ACCIONES-USA`, `BONOS`, `CAUCIONES`, `CEDEARS`, `ETF`, `FCI`, `FCI-EXTERIOR`, `FUTUROS`, `LEBAC`, `LETRAS`, `LICITACIONES`, `NOBAC`, `ON`, `OPCIONES`.

Markets: `BYMA`, `NASDAQ`, `NYSE`, `OTC`, `ROFEX`.
Settlements: `INMEDIATA`, `A-24HS`, `A-48HS`, `A-72HS`.

Regla canónica:
`API_DECLARED -> API_DISCOVERED -> CONTRACT_ENRICHED -> HISTORY_READY -> SIMULATOR/SIZING/EXIT_READY -> PAPER_CANDIDATE`.

Web/XHR no crea universo operativo por sí solo. `INDICES`, `MONEDAS`, `TASAS` quedan `CONTEXT_ONLY` mientras no sean InstrumentTypes de la API productiva.

## Catálogo API ya observado
Workflow `34659762851` / `103459630927`: SUCCESS.
- ACCIONES: 55
- BONOS: 42
- CAUCIONES: 10
- CEDEARS: 191
- FUTUROS: 52
- LETRAS: 23
- ON: 91
- OPCIONES: 382
Total materializado en esas familias: 846 identidades.

## Evidencia existente para familias pendientes
Workflow `34661020272` / `103463358860`: SUCCESS, read-only, safety `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`.
- ACCIONES-USA: wildcard web/XHR.
- ETF: wildcard web/XHR.
- FCI: wildcard web.
- FCI-EXTERIOR: wildcard web/XHR.
- LICITACIONES: `TECPE10`, XHR `instrument_id=928820`, moneda USD/CCL, más wildcard web/XHR.
- LEBAC: sin evidencia current.
- NOBAC: sin evidencia current.

## Directed discovery inicial contra API productiva
Workflow `34661074786`, job `103463521230`: SUCCESS.
Safety pre/final `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`; HTTP blocked=0; REAL_ORDER_ROUTES=NOT_CALLED; MUTATIONS=NONE.

Identidades confirmadas:
- ACCIONES-USA AAPL / NYSE
- ACCIONES-USA MSFT / NASDAQ
- ACCIONES-USA NVDA / NASDAQ
- ETF SPY / NYSE
- ETF IWM / NYSE

Resultados 0 sólo invalidan el filtro exacto; no la familia completa.
`TECPE10` web/XHR no fue descubierto por `SearchInstrument` con `LICITACIONES/TECPE10/BYMA`; su evidencia además marca `operable_auction=False`, por lo que puede ser una licitación cerrada o requerir discovery específico de evento.

## Sweep multi-mercado de candidatos con resultado inicial 0
Workflow `34661275674`, job `103464119990`: SUCCESS.
Safety pre/final GREEN, PPI producción, HTTP blocked=0, sin mutaciones ni rutas de órdenes.

Hallazgos:
- `TSLA` ACCIONES-USA: NASDAQ 0, **NYSE 1** -> Tesla Motors, USD/CCL. OTC/BYMA 0.
- `QQQ` ETF: NASDAQ 0; búsqueda NYSE devolvió **CQQQ, SQQQ, TQQQ**. Esto NO demuestra que QQQ exista: demuestra que SearchInstrument puede devolver coincidencias parciales/fuzzy y que las identidades retornadas son especies distintas concretas.
- `DIA` ETF: 0 en NASDAQ/NYSE/OTC/BYMA con ese filtro.

Nueva regla obligatoria: nunca promover la semilla consultada. Se valida la identidad DEVUELTA por PPI (`ticker/type/market`) y se registra por separado si hubo exact match.

## FCI / FCI Exterior: evidencia web como generador de candidatos
Workflow `34661292189` / `103464165603`: SUCCESS.
Workflow de shape específico `34661437882` / `103464587412`: SUCCESS.

FCI local web:
- tabla observada con 50 identidades de muestra;
- headers explícitos: `NOMBRE`, `CATEGORÍA`, `MON.`, `PLAZO RESC.`, `HORARIO LÍMITE`, rendimientos, `RIESGO`, `Patrimonio`;
- ejemplos: `Adcap Acciones Clase A`, `Adcap Ahorro Dinamico Clase A`, `Adcap Ahorro Pesos Clase A`, múltiples Allaria.

FCI Exterior web:
- 50 identidades de muestra;
- headers: `NOMBRE`, `CATEGORÍA`, rendimientos, `RIESGO`;
- ejemplos: All Metrics, FEX Barings/Federated, Franklin, JH, JPM.

La web demuestra nombres visibles pero no ticker API. Por diseño siguen siendo `WEB_CANDIDATE_ONLY` hasta validación PPI API.

## Validación API de nombres FCI observados en web
Workflow `34661582090`, job `103465013360`: SUCCESS; seguridad pre/final GREEN, HTTP blocked=0, no órdenes/mutaciones.

- Pasar el nombre completo como Ticker+Name devolvió 0 para los casos probados.
- Usar `Adcap` como ticker query produjo 4 identidades PPI API reales en BYMA:
  - `ADCAP.AP.A` — Adcap Ahorro Pesos Clase A
  - `ADCAP.AP.B` — Adcap Ahorro Pesos Clase B
  - `ADCAP.AP.LEY` — Adcap Ahorro Pesos Clase Ley 27743
  - `ADCAP.WG.LEY` — Adcap Wise Capital Growth Clase Ley 27743
- Las búsquedas FCI-EXTERIOR de All Metrics / Franklin por nombre/prefijo en OTC devolvieron 0.

Importante: la respuesta de `Adcap` no coincidió necesariamente con el nombre web usado como seed. Nuevamente, la autoridad es cada fila retornada por la API, no el texto de entrada.

## Sweep de prefijos derivados de la web
Workflow `34661703151`, job `103465371550`: SUCCESS.
Prefijos derivados de evidencia almacenada:
- FCI: `Adcap`, `Allaria`.
- FCI-EXTERIOR: `All`, `FEX`, `Cullen`, `Franklin`, `JPM`.

Resultado:
- FCI/BYMA: 4 identidades concretas, las cuatro `ADCAP.*` listadas arriba.
- Allaria/BYMA: 0 con ese filtro.
- FCI-EXTERIOR/OTC: 0 identidades con esos prefijos en este sweep.

FCI queda entonces `API_DECLARED` y ya posee al menos 4 identidades `API_DISCOVERED` verificadas aunque todavía no estén materializadas por el `instrument_catalog` productivo actual.
FCI-EXTERIOR permanece `API_DECLARED_NOT_DISCOVERED` con los filtros probados.

## Arquitectura source-only creada y probada
Se agregó `rc6_api_universe_authority.py`:
- normaliza aliases de familia;
- distingue `API_DECLARED`, `API_DISCOVERED`, `CONTEXT_ONLY`;
- Binding contractual sólo puede evaluar identidad concreta API;
- `paper_candidate=False` siempre en esta capa;
- `validate_search_results()` valida la fila devuelta y separa `exact_ticker_match`, evitando inventar QQQ cuando la API devolvió CQQQ/SQQQ/TQQQ.

Se agregó `rc6_web_api_candidate_bridge.py`:
- convierte tablas web con headers explícitos en candidatos de validación;
- reconoce `NOMBRE` y headers explícitos de ticker cuando existan;
- jamás marca `api_discovered`, `contract_ready` o `paper_candidate`.

Proof conjunto workflow `34661657554`, job `103465236345`: SUCCESS.
`API_UNIVERSE_AUTHORITY=GREEN`, `WEB_CANDIDATE_BRIDGE=GREEN`, `AUTO_PAPER_PROMOTION=FALSE`, `ORDER_CAPABILITY=NONE`.

Los módulos son source/control solamente; no fueron desplegados al runtime en esta wave.

## Limitación confirmada del DOM importer actual
`rc6_contract_dom_collector.py` ya extrae hasta 100 filas de tablas HTML/ARIA con headers explícitos. Sin embargo `rc6_contract_dom_importer.py` deliberadamente persiste un snapshot agregado por ruta con `ticker='*'`, `market='UNKNOWN'`, `settlement='UNKNOWN'` y no infiere semántica de columnas.

Esto es seguro pero insuficiente para consolidación por instrumento. La siguiente evolución debe ser: tabla web -> candidato explícito -> validación API -> identidad `API_DISCOVERED` -> enriquecimiento Contract por esa identidad. Nunca convertir una fila web directamente en instrumento operativo.

## Hallazgo arquitectónico del runtime
`bf_production_paper_observer.py::_download_catalog()` sólo consulta lo producido por `_candidate_universe()`.
El runtime actual tiene seeds para ETF y FCI, pero no para ACCIONES-USA, FCI-EXTERIOR, LICITACIONES, LEBAC, NOBAC. Los seeds estándar además salen con market BYMA, insuficiente para familias USA.

## Próxima wave priorizada
1. Crear discovery dirigido persistente por `familia+ticker/name+market` que acepte únicamente filas reales devueltas por PPI.
2. Materializar en una tabla `api_universe` las identidades confirmadas de ACCIONES-USA, ETF y FCI sin habilitar PAPER.
3. Enriquecer Contract sólo para `api_discovered=1` y preservar web-only como candidates/context.
4. Ampliar candidatos FCI desde tablas web actuales y validar progresivamente contra API sin brute force.
5. Investigar FCI-EXTERIOR y LICITACIONES con semántica/ruta específica; no asumir que resultado 0 equivale a familia vacía.
6. LEBAC/NOBAC siguen `API_DECLARED_NOT_DISCOVERED` hasta evidencia concreta.
7. Después, cruzar `api_universe` con History Store y readiness por familia para definir cobertura operativa real.

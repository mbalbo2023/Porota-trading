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

No se ejecutó rollback ni modificación del checkout/runtime desde esta wave. El único cambio productivo realizado por esta wave fue metadata aditiva `api_universe_v1` dentro de observer DB; no hubo restart de contenedor, no se habilitó PAPER para nuevas especies y no se tocaron rutas reales de órdenes.

Recheck read-only workflow `34660920043`, job `103463059740`: SUCCESS.
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

## Catálogo API observado inicialmente
Workflow `34659762851` / `103459630927`: SUCCESS.
- ACCIONES: 55
- BONOS: 42
- CAUCIONES: 10
- CEDEARS: 191
- FUTUROS: 52
- LETRAS: 23
- ON: 91
- OPCIONES: 382
Total inicial: 846 identidades.

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

Regla obligatoria: nunca promover la semilla consultada. Se valida la identidad DEVUELTA por PPI (`ticker/type/market`) y se registra por separado si hubo exact match.

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

Importante: la respuesta de `Adcap` no coincidió necesariamente con el nombre web usado como seed. La autoridad es cada fila retornada por la API, no el texto de entrada.

## Sweep de prefijos derivados de la web
Workflow `34661703151`, job `103465371550`: SUCCESS.
Prefijos derivados de evidencia almacenada:
- FCI: `Adcap`, `Allaria`.
- FCI-EXTERIOR: `All`, `FEX`, `Cullen`, `Franklin`, `JPM`.

Resultado:
- FCI/BYMA: 4 identidades concretas, las cuatro `ADCAP.*` listadas arriba.
- Allaria/BYMA: 0 con ese filtro.
- FCI-EXTERIOR/OTC: 0 identidades con esos prefijos en este sweep.

FCI queda `API_DECLARED` y ya posee al menos 4 identidades `API_DISCOVERED` verificadas.
FCI-EXTERIOR permanece `API_DECLARED_NOT_DISCOVERED` con los filtros probados.

## Arquitectura source-only creada y probada
Se agregaron y probaron:
- `rc6_api_universe_authority.py`: normalización de aliases, estados API, validación de filas reales devueltas, fuzzy-safe y `paper_candidate=False`.
- `rc6_web_api_candidate_bridge.py`: convierte tablas web con headers explícitos en candidatos; nunca autoriza identidad operativa.
- `rc6_api_universe_store.py`: modelo/persistencia de universo API con flags readiness fail-closed.

Proofs finales GREEN:
- workflow `34661657554`, job `103465236345`
- workflow `34661851857`, job `103465817099`
- workflow `34662087687`, job `103466510922`

Invariantes de los proofs: `AUTO_PAPER_PROMOTION=FALSE`, `ORDER_CAPABILITY=NONE`.

## Materialización productiva aditiva `api_universe_v1`
Forward-fix workflow `34662014329`, job `103466286784`: SUCCESS.

Pre-safety:
- quick_check=ok
- mode=PRODUCTION_PAPER
- session=MARKET_CLOSED
- real_orders_sent=0
- tabla previa inexistente / 0 filas.

Bootstrap aditivo:
- catalog_seen=901
- directed_seen=13
- total persistido=914
- invalid_rows=0
- `contract_ready=0`, `history_ready=0`, `simulator_ready=0`, `paper_candidate=0` para todas las filas.

Cobertura materializada:
- ACCIONES 55
- ACCIONES-USA 4
- BONOS 42
- CAUCIONES 10
- CEDEARS 191
- ETF 5
- FCI 4
- FUTUROS 52
- LETRAS 23
- ON 91
- OPCIONES 437
- TOTAL 914

Muestras nuevas confirmadas:
- ACCIONES-USA: AAPL, MSFT, NVDA, TSLA
- ETF: CQQQ, IWM, SPY, SQQQ, TQQQ
- FCI: ADCAP.AP.A, ADCAP.AP.B, ADCAP.AP.LEY, ADCAP.WG.LEY

Post-proof:
- quick_check=ok
- mode=PRODUCTION_PAPER
- real_orders_sent=0
- invalid_rows=0
- ready_sums=[0,0,0,0]
- host SHA permaneció `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`
- observer running, restart=0
- RESTART_PERFORMED=NO
- ROLLBACK=NOT_USED
- REAL_ORDER_ROUTES=NOT_CALLED

La mutación fue exclusivamente metadata aditiva de universo API. No alteró motor, imagen, checkout, scheduler ni capacidad de ejecución.

## Cruce `api_universe_v1` ↔ Contract Evidence
Read-only workflow `34662153584`, job `103466703294`: SUCCESS.
Safety `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`; MUTATIONS=NONE; REAL_ORDER_ROUTES=NOT_CALLED.

Cobertura exacta por ticker:
- ACCIONES: 55 API / 54 Contract exacto / falta VALOX
- ACCIONES-USA: 4 / 0 / faltan AAPL, MSFT, NVDA, TSLA
- BONOS: 42 / 42 / completo por identidad
- CAUCIONES: 10 / 0 / faltan DOLAR1,DOLAR2,DOLAR7,DOLAR30,DOLAR120,PESOS1,PESOS2,PESOS7,PESOS30,PESOS120
- CEDEARS: 191 / 191 / completo por identidad
- ETF: 5 / 0 / faltan CQQQ,IWM,SPY,SQQQ,TQQQ
- FCI: 4 / 0 / faltan cuatro ADCAP.*
- FUTUROS: 52 / 42 / faltan 10 variantes observadas
- LETRAS: 23 / 17 / faltan 6
- ON: 91 / 84 / faltan 7
- OPCIONES: 437 / 0 / faltan 437 identidades exactas

TOTAL:
- API identities: 914
- exact Contract identities: 430
- market-compatible exact Contract: 430

Los wildcards web existentes no satisfacen Binding individual.
Prioridad Contract por volumen/beneficio: OPCIONES, CAUCIONES, FUTUROS, LETRAS, ON, luego ACCIONES-USA/ETF/FCI y VALOX.

## History Store canónico identificado
Read-only workflow `34662200612`, job `103466839351`: SUCCESS.
`/opt/porota-trading/data/market_history.db`, quick_check=ok.

Tablas relevantes:
- `history_canonical_v2`: 49,413 filas; columnas symbol,instrument_type,market,settlement,date,OHLCV,source,adjusted,source_rank,observed_at,version_id.
- `history_versions_v2`: 50,162 filas.
- `history_close_canonical_v1`: 8,026 filas.
- `history_close_versions_v1`: 8,040 filas.
- `market_historical_ohlcv`: 107,538 filas.

Este DB, y no sólo `production_history` del observer, es la autoridad para el siguiente cruce `api_universe_v1 -> HISTORY_READY`.

## Limitación confirmada del DOM importer actual
`rc6_contract_dom_collector.py` extrae hasta 100 filas de tablas HTML/ARIA con headers explícitos. `rc6_contract_dom_importer.py` persiste un snapshot agregado por ruta con `ticker='*'`, `market='UNKNOWN'`, `settlement='UNKNOWN'` y no infiere semántica de columnas.

Esto es seguro pero insuficiente para consolidación por instrumento. Evolución obligatoria:
`tabla web -> candidato explícito -> validación API -> identidad API_DISCOVERED -> enriquecimiento Contract exacto`.
Nunca convertir una fila web directamente en instrumento operativo.

## Hallazgo arquitectónico del runtime
`bf_production_paper_observer.py::_download_catalog()` sólo consulta lo producido por `_candidate_universe()`.
El runtime actual no cubría de forma suficiente ACCIONES-USA, FCI-EXTERIOR, LICITACIONES, LEBAC, NOBAC y usaba BYMA como supuesto de mercado en seeds normales. La tabla `api_universe_v1` desacopla discovery/autoridad del universo sin habilitar PAPER.

## Estado actual de wave
- API authority/model: GREEN
- API discovery materializado: GREEN, 914 identidades
- API universe storage: GREEN, metadata only
- Contract identity coverage: YELLOW, 430/914 exactas
- History canonical DB: GREEN/integrity, cruce 914 pendiente
- Simulator/sizing/exit readiness: fail-closed, no promoción automática
- PAPER promotion: 0
- real orders: 0

## Próxima wave priorizada
1. Cruzar las 914 identidades contra `history_canonical_v2` y medir cobertura/días/frescura por familia e identidad.
2. Persistir `history_ready` sólo cuando exista criterio explícito suficiente; antes de escribir, realizar proof read-only de la regla.
3. En paralelo, cerrar Contract exacto empezando por CAUCIONES y OPCIONES, sin inferir ticker desde wildcard.
4. Resolver los 10 FUTUROS, 6 LETRAS, 7 ON y VALOX mediante evidencia proveedor/API/web vinculable de forma determinística.
5. Enriquecer ACCIONES-USA/ETF/FCI sólo sobre las identidades PPI API ya confirmadas.
6. FCI-EXTERIOR, LICITACIONES, LEBAC y NOBAC permanecen API_DECLARED_NOT_DISCOVERED hasta discovery concreto.
7. Auditar y reparar en wave separada cualquier drift de controles/scheduler, sin reabrir CP1–CP14.

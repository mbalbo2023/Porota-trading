# Delta cross-chat: eficiencia, paridad PAPER y plan Wave A — 2026-09-14

**ID:** POROTA-DELTA-AI-EFFORT-PAPER-PARITY-20260914-01  
**Estado:** PENDING_INTEGRATION  
**Rama aislada:** `docs/ai-effort-paper-parity-main-20260914`  
**Base:** `fc9e8f9872c3669f934debbd36a41982ec40e7cf`  
**Baseline de código RC6:** `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`  
**Alcance:** política cross-chat y hallazgos de solo lectura. No modifica runtime, DB, importación, servicios ni órdenes.

## Regla solicitada: esfuerzo y contexto

Ajustar el esfuerzo del modelo a la complejidad, ambigüedad y riesgo: usar la menor cantidad de razonamiento, llamadas y tokens que complete la tarea con evidencia suficiente; aumentar profundidad y verificación cuando el cambio sea operativo, financiero, irreversible o haya discrepancias. La eficiencia nunca omite las lecturas obligatorias, pruebas proporcionales, evidencia ni restricciones del usuario. Agrupar lecturas independientes, ejecutar en paralelo sólo tareas independientes y aisladas, conservar un ledger/checkpoint compacto con decisiones, evidencias, incertidumbres y siguiente paso exacto, y no repetir trabajo ya cerrado. Si los últimos chats no son accesibles, decirlo y usar el repositorio como fuente verificable; no inventar memoria conversacional.

## Hallazgo: elegibilidad spot versus apertura PAPER

Auditoría estática del baseline RC6:

- `READY_PAPER_SPOT` es una puerta legacy de elegibilidad para el motor spot, no el readiness contractual integral de PPI.
- `bu_instrument_catalog.py` asigna a ciertas familias BYMA una convención de unidad con multiplicador/paso 1 aunque dichos términos no provengan explícitamente de la respuesta de búsqueda PPI.
- El observer entrega cotizaciones al broker PAPER; `be_paper_engine.py` puede aceptar `OPENED_SIMULATED` si se cumplen sus otros controles de identidad, precio/profundidad, frescura, sesión, caja, costos y riesgo. Los módulos nuevos de Contract Evidence/readiness no están conectados a esa ruta de apertura; terminan en `READY_PAPER_CANDIDATE`.
- `PAPER_ECONOMIC_GATE_MODE=SHADOW` permite registrar evaluaciones aunque el gate económico marque would-block. Esto explica evaluaciones y que el camino legacy pueda abrir PAPER; no prueba que haya ocurrido una apertura concreta.
- Los 246 del dashboard son pool elegible rotativo, no 246 posiciones ni prueba de fills: 55 ACCIONES + 191 CEDEARS. El máximo activo configurado se documentó como 20.
- La última matriz estricta registrada era 0/444 antes de fixes y no se recalculó. Resultado estricto actual: `NOT_VERIFIED`. Históricos: 239/246 con cobertura; siete CEDEARs tienen `HISTORY_GAP`.

### Regla de producto para paridad

En familias con órdenes PPI simulables, `READY_PAPER_SPOT` sólo permite evaluación de mercado/diagnóstico; no autoriza una apertura ni fill PAPER. Para iniciar una operación simulada se requiere `READY_PAPER_PPI` con identidad, datos/analytics requeridos, contrato explícito PPI, riesgo, frescura, costos, settlement/calendario y reglas de unidad/tick/step validadas. `SHADOW` puede comparar e informar would-block, pero no puede habilitar la apertura. Los flujos sin operación intradía conservan su state machine propia.

Esta es una política/documentación propuesta a partir de la aclaración expresa del usuario; su integración al runtime sigue pendiente. No se afirma que haya habido una violación de fill histórico hasta inspeccionar evidencia runtime autorizada. Sin inspeccionar DB/runtime, fills históricos: `NOT_VERIFIED`.

## 444 instrumentos: evidencia y alternativas

- PPI API de histórico: 1.960/1.960 tareas terminales, pero 679 resultados residuales de calidad. PPI Web residual: 642/642 terminales, 0 válidos. Corridas cerradas: no repetir scraping/ingesta masiva, no segundo writer ni retry ciego.
- Capturas contractuales recientes confirmaron llamadas a `InstrumentosOperables`, `CaucionesOperables` y `ConfiguracionOperatoriaSimplificada`; una fila AL30 y 120 Cauciones. No se observó `DatosTecnicos`; faltan términos explícitos en AL30 y semántica contractual completa de Cauciones. La captura de inventario expuso una ruta sensible en logs; corregir redacción antes de nuevas capturas.
- La API estructurada IOL se probó en instrumentos representativos y campos como cotizaciones, historia, analítica de renta fija, opciones y tasas de caución. No se probó cobertura completa ni identidad canónica de 444.
- IOL Web scraping quedó bloqueado por diseño hasta demostrar que un campo crítico falta en API pero aparece establemente en Web. No hay evidencia de scrape masivo de IOL. La cobertura completa de 444 vía IOL API sigue sin probarse.
- Las fuentes disponibles no documentan pruebas de proveedores de mercado terceros o alternativas de bulk feed. Quedan `NOT_RESEARCHED`, no son opciones validadas.
- El usuario solicitó revisar los últimos diez chats. En esta sesión no hay herramienta para leer conversaciones anteriores; GitHub es la fuente comprobable. No se pueden certificar intentos que sólo estén en transcripciones inaccesibles.

## Wave A — fundamentos encontrados, avance pendiente

Código reusable en RC6: `bs_instrument_contracts.py`, `cp_contract_evidence_v2_hf6.py`, `cq_family_contract_rules_hf6.py`, `cq_contract_readiness_hf6.py`, `rc6_ppi_contract_normalizer.py` y cliente IOL legacy `ak_iol_client.py`. No duplicar contratos/evidence store/readiness. Riesgos de diseño: aliases divergentes de familias, campos distintos `price_precision`/ `price_tick`, fuente prioritaria dependiente del campo/familia, y superficie account/portfolio amplia del cliente IOL.

Slices de Wave A sobre la rama de código RC6 aislada, con pruebas offline: WA-01 resolver puro está en PR borrador #63 con CI verde; siguen vistas/adaptadores de provenance sobre Evidence v2; allowlist IOL read-only; PPI contract DTO desde normalizador actual; evaluator unificado de gates; comparador de divergencias. Diseño de DB primero, sin migraciones.

La rama reciente `ops/rc6-contract-open-session-immediate-20260914` sólo contiene documentación y diagnósticos; no es base de código para Wave A. `main` también es docs/ops-only. El SHA RC6 `f8adec…` es el baseline de código. No se tocaron la rama del pipeline PPI, runtime ni DB. El 2026-09-14 se abrió una rama propia de integración de código, `integration/rc6-wave-a-core-20260914`, desde el baseline RC6 exacto `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`; la feature `feature/wave-a-canonical-identity-20260914` aporta WA-01 en PR borrador #63. El resolver permanece aislado y no se conectó al runtime.

## Estado de ejecución

- Código WA-01: `canonical_identity_resolver.py` en PR borrador #63. Requiere familia/subfamilia, ticker, mercado, venue, moneda, settlement y provider ID; faltantes/conflictos => sin canonical ID. CEDEAR exige underlying; opciones/futuros, sus dimensiones contractuales de identidad.
- CI de WA-01: run final de Actions `34886541982` SUCCESS; compileall y 18 pruebas unitarias offline pasaron. El primer diseño se corrigió después de review. Esto valida sólo el slice aislado.
- Pendiente de Wave A: ratificar taxonomía con módulos existentes; WA-02 provenance, WA-03 IOL read-only, WA-04 DTO PPI, WA-05 readiness, WA-06 divergencias y conexión del gate al punto de apertura.
- Runtime/DB/importación/readiness recompute: no inspeccionados ni ejecutados.
- Capturas/ingesta masiva: no se repitieron.
- Órdenes reales: no habilitadas; invariantes PAPER/fail-closed se preservan.
- Checkpoint canónico: este archivo es delta aislado, no reemplaza ni sobrescribe el checkpoint activo.


## Entrega documental y verificación

La regla se agregó a `AGENTS.md`, `POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md` y `POROTA_TRADING_CHAT_START_HERE.md` en esta rama aislada. Cada archivo se volvió a leer desde GitHub después del commit:

- `AGENTS.md`: blob `d06d6de0b634526c84444dc04914ef40b1d609ff`, commit `549d55caac81c5820091daebf363fc6b2c5fdb1e`.
- `POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md`: blob `b473a7792a42dcf472c300275e6499b819535349`, commit `22343149e7e08d5c001705db582c1f581e48c23c`.
- `POROTA_TRADING_CHAT_START_HERE.md`: blob `b7e6713e02494e8873efd1140abef511ec8e9aae`, commit `8188102c23cc53037adea2719c500889abef20db`.

La entrada de chat nuevo apunta explícitamente a este delta. No se fusionó ni se desplegó; queda pendiente de integración por el chat integrador. No hay CI de aplicación ejecutada para estos cambios documentales.


## Alternativa oficial de datos investigada en esta sesión

La búsqueda actual de documentación oficial encontró una vía nueva respecto de las corridas PPI ya cerradas:

- BYMA publica una API de Market Data y una API Market Data Instruments. BYMA describe la segunda como fuente de datos de Acciones, CEDEARs, Bonos y otros instrumentos, con características y parámetros de negociación; la API de precios publica información negociada durante el día. La página de APIs enumera renta variable, renta fija, futuros, opciones, cauciones e intradiarios. La implementación depende de requisitos/acceso comercial y técnicos. La documentación indica que las APIs Market Data son la excepción a la regla de acceso reservado a agentes miembros.
- Es una alternativa oficial para medir precios y ciertos términos estructurados de BYMA. La página publica para Market Data Instruments 1.000 solicitudes/mes: sin costo para miembros y USD 200/mes para no miembros. Para Market Data, EOD publica 1.000 solicitudes/mes (sin costo para miembros, USD 50/mes no miembros); los planes Snapshot/Delay tienen otras cuotas y precios. La suscripción requiere solicitar acceso/documentación y firmar el contrato de Market Data y/o disclaimer aplicable. Aún no se probó conexión, método bulk/paginación, alcance por campo/universo, profundidad histórica ni cobertura de los 444. No prueba ejecución PPI y no cubre por sí sola activos fuera de BYMA (por ejemplo, acciones USA).
- IOL ya tiene cliente REST read-only en el código y su documentación oficial ofrece cotizaciones actuales e históricas para instrumentos del mercado argentino; ya se probaron ejemplos, pero no un barrido API completo de 444. La siguiente prueba de cobertura debe ser API-first, por muestras/campos faltantes y con rate/call budget; no scraping web masivo.
- PPI documenta API para buscar instrumentos, consultar históricos/cotizaciones en tiempo real y operar. Los pases históricos masivos PPI del checkpoint ya se agotaron y no se repiten; datos de cotización/contrato actual deben tratarse en capturas read-only y gates separados, sin importar ni operar.

Fuentes primarias consultadas: [BYMA Market Data APIs](https://www.byma.com.ar/productos/productos-de-datos/market-data/apis), [BYMA APIs y requisitos de acceso](https://www.byma.com.ar/byma-apis), [API oficial de IOL](https://www.invertironline.com/api), [PPI Docs](https://itatppi.github.io/ppi-official-api-docs/).

Conclusión: para los activos BYMA, el siguiente camino nuevo y concreto es solicitar/evaluar BYMA Market Data + Market Data Instruments; en paralelo, medir cobertura y calidad de IOL API sobre los 444 identificados. Ninguna de las dos fuentes reemplaza el contrato de ejecución PPI. Para proveedores terceros adicionales: `NOT_RESEARCHED` y no recomendados todavía.

## Semáforo de ejecución después de Wave A

**Regla común:** 🟢 sólo cuando hay código integrado en la rama acordada, tests/CI del patrón verdes, evidencia trazable y vigente, cinco gates verificados y comportamiento runtime PAPER de apertura/HOLD probado. 🟡 significa investigación o evidencia parcial: HOLD para aperturas. 🔴 indica ambigüedad, conflicto, stale, falta contractual/riesgo o prueba fallida: bloquear. Éxito de workflow no basta. PAPER nunca autoriza órdenes reales.

### Estado actual para comenzar

- 🔴 Paridad de apertura PAPER: el código legacy permite potencial OPENED_SIMULATED bajo READY_PAPER_SPOT; READY_PAPER_PPI no está conectado. Política documentada en PR #62, pendiente de integración y wiring de código. Fill runtime concreto: NOT_VERIFIED.
- 🟡 246 READY_PAPER_SPOT: 55 ACCIONES + 191 CEDEARS, pool de elegibilidad, no readiness integral.
- 🟡 Universo estricto 444: último 0/444 es anterior a fixes; valor actual NOT_VERIFIED.
- 🟡 Wave A completa: rama de integración propia desde el SHA RC6 correcto; PR #63 sigue en borrador y CI WA-01 está verde (18 pruebas). Resto de WA pendiente; el slice aún no está integrado al branch de integración ni conectado al runtime.
- 🟢 Corridas masivas históricas PPI cerradas: no repetirlas. 🟡 API IOL y nueva opción BYMA por medir; BYMA requiere documentación/acceso y su cobertura sigue sin probarse.

### Wave A — core reusable y regla de apertura

1. 🟢 Base de integración aislada creada desde RC6 `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`: `integration/rc6-wave-a-core-20260914`. No se usa rama deploy/docs/ops ni pipeline PPI.
2. 🟡 Ratificar vocabulario canónico con evidencia y módulos existentes: identity/provider IDs, venue, family/subfamily, moneda/plaza ARS/D/C, underlying/emisor, settlement; resolver aliases y `price_precision`/`price_tick`/`quantity_step` sin equivalencias inferidas.
3. 🟡 WA-01/02: PR #63 implementa WA-01; CI final verde con 18 tests. Se exige subfamily/provider ID y dimensiones de CEDEAR/opciones/futuros; aliases desconocidos de moneda/settlement no se adivinan. Pendiente integrar/revisar el slice, ratificar el vocabulario contra Evidence v2 y después implementar provenance adapters sin tablas duplicadas ni migración.
4. 🟡 WA-03/04: IOL provider estrictamente allowlisted read-only y DTO PPI sobre normalizador existente; tests con DTO/mocks, sin red ni credenciales.
5. 🟡 WA-05/06: evaluator de los cinco gates identity/market data/analytics/contract PPI/risk más divergence gate; conflicto o campo crítico ausente => HOLD.
6. 🔴 Conectar gate estricto al punto de apertura PAPER: evaluación diagnóstica puede seguir en SHADOW, OPENED_SIMULATED no puede saltar READY_PAPER_PPI.
7. 🟡 WA-07: diseño de schema y estrategia de migración; no migrar runtime en Wave A.
8. 🟢 Wave A sólo al integrar en rama de código válida, pasar tests/CI, demostrar que missing/stale/conflict bloquea y demostrar HOLD versus apertura simulada controlada en PAPER sin DB productiva ni órdenes reales.

### Después de Wave A

| Orden | Ola y patrón | Qué hacer | Verde sólo si… |
|---|---|---|---|
| 1 | **B — Spot**: GGAL; AAPL CEDEAR; IVV; AAPL NASDAQ | Validar identidad/venue por separado; para CEDEAR, ratio, ARS/D/C, doble calendario y eventos; para ETF, clasificación y leverage/inverse; USA separado con FX/costos/calendario. | Cada patrón tiene contrato PPI, datos/analytics, riesgo y prueba PAPER individual. No extrapolar un ticker a toda su familia. |
| 2 | **C — Renta fija**: AL30, S30O6, D30O6, YMCJO | AL30 stopper: nominal/mínimo/step/tick/settlement/disponibilidad/horario explícitos. S30O6 con IOL + términos oficiales. D30O6 con fallback analítico Porota porque IOL devolvió not_found. YMCJO con términos CNV/emisor y crédito/liquidez. | Analytics trazable y contrato PPI completo por patrón; no inferir steps/ticks de decimales. |
| 3 | **D — Cauciones**: colocadora y tomadora | Mantener motores/lados separados; mapear 120 filas por moneda/lado/plazo y validar semántica. Verificar productores canónicos, ObligationSnapshot, datos frescos, aforo/collateral y gates. Reutilizar sweeper/adapter/bridge/controller existentes. | Semántica contractual y dinámica fresca completas; productor y obligaciones runtime comprobados; PAPER E2E permite o retiene correctamente. |
| 4 | **E — Derivados**: opciones y futuros | Opciones: contrato/multiplicador/tick/step/ejercicio/asignación y OptionsRiskEngine. Futuros: spec BYMA + contrato PPI, margin/fees/MTM/rollover. | Contrato y motor de riesgo propios pasan pruebas de estrés, vencimiento/rollover y HOLD. Hasta entonces, research/HOLD. |
| 5 | **F — Flujos especiales**: FCI, exterior, primario, índices, legacy | FCI con state machine de suscripción/rescate; exterior sólo con ejemplo PPI vigente; licitaciones como flujo primario aparte; índices analytics-only; LEBAC/NOBAC inactivos sin evidencia actual. | Cada flujo usa criterios propios; ninguna métrica analytics-only se convierte en READY PPI. |

Los patrones B/C/D son pruebas de escalamiento, mientras AL30 y Cauciones siguen como blockers paralelos prioritarios. Waves E/F no desaparecen: se difieren hasta pasar sus gates específicos.
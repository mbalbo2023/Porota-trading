# POROTA TRADING — CHECKPOINT CANÓNICO RC6 — 2026-09-09 11:42 ART

## 0. Regla de continuidad
Este archivo es contexto canónico de continuidad para el trabajo P0 del 2026-09-09. Antes de proponer, desplegar o diagnosticar desde otra conversación, leer este archivo completo y continuar desde el último estado probado. No reconstruir contexto desde ramas viejas ni repetir pruebas ya cerradas.

## 1. Objetivo operativo de hoy
Dejar POROTA TRADING apto para jornada en `PRODUCTION_PAPER`, con datos suficientes y gates explícitos por familia, priorizando: preopen/runtime, cauciones, opciones, Contract Evidence/scraping, históricos y observabilidad. Producción en este checkpoint significa PAPER/simulación con datos productivos read-only; NO significa órdenes reales.

## 2. Invariantes absolutas
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0`.
- `PRODUCTION_PAPER`.
- No ejecutar pruebas de rutas de órdenes reales.
- No llamar `/Operar`, Budget/Order ni equivalentes de ejecución.
- PPI API y PPI web: lectura solamente salvo POST de autenticación explícitamente aprobado.
- No revelar credenciales, OTP, cookies, tokens ni cuerpos sensibles.
- No bypass de 2FA.
- Rollback nunca automático.
- CI success no equivale a GREEN: se exige proof live, DB, modo, safety y wiring.

## 3. HEAD live canónico al abrir este checkpoint
`2c9f3df2d03974ab6fe464d119bdbb2d90158706`

Estado probado al deploy de ese HEAD:
- observer running, restarts=0.
- dashboard running, restarts=0.
- SQLite quick_check=ok.
- mode=PRODUCTION_PAPER.
- real_orders_sent=0.
- historical_raw_archive=0.
- `PPI_HISTORY_RAW_STORAGE_MODE=EXTERNAL_EXACT_V1`.
- no order routes called.

## 4. GREEN ya cerrados
### 4.1 Wave8 / UX dashboard
GREEN live. Persisten sobre el HEAD actual:
- tablas clásicas reales, scroll horizontal interno y nowrap para tablet;
- captions/títulos visibles para tablas;
- navegación de Sistema movida desde columna izquierda a barra superior sticky/horizontal;
- índice sticky `En esta página` con links a secciones internas;
- `Estrategias` ya no duplica Scalping; Scalping queda en su destino propio;
- Risk y aprendizaje SHADOW siguen read-only.

### 4.2 Calendario BYMA fail-closed
GREEN live. Si el calendario BYMA no puede verificarse, `_business_day()` devuelve False y la sesión queda CLOSED; no existe fallback lunes-viernes.

### 4.3 Control plane legacy
GREEN live desde `2c9f3df...`:
- `ACTIVE_LEGACY_RUNTIME=0`.
- `ENABLED_LEGACY_TIMERS=0`.
- retirados del load path systemd RC4/RC5/HF5 activos.
- productor de introspección RC6 y publisher RC6 materializados y probados.
- publicación sanitizada a `runtime-observability` probada.
- timer `porota-contract-evidence-rc6.timer` queda como owner RC6.

## 5. Preopen — P0
RCA cerrado: el gate canónico `rc6_preopen.py` espera correctamente `porota-trading-bot:17.0.0-rc6`, pero el wrapper host instalado tenía una premisa vieja y exigía `porota-trading-dashboard:17.0.0-rc6`.

Branch de fix: `fix/rc6-preopen-dashboard-contract-20260909`.
Commit de workflow/fix preparado: `707a6b6881992161c37d7b7c92de72cd3941b3c6`.
Estado al abrir este checkpoint: **PENDIENTE PROOF LIVE**. No marcar GREEN hasta leer el run y comprobar status GREEN + invariantes.

## 6. Cauciones — P0
### Hallazgo
El universo contiene 10 cauciones AVAILABLE:
- PESOS1, PESOS2, PESOS7, PESOS30, PESOS120.
- DOLAR1, DOLAR2, DOLAR7, DOLAR30, DOLAR120.

Antes del fix: 0 decisiones, 0 allocations y 0 posiciones PAPER de cauciones. `bv_paper_runtime.py` no tenía worker de cauciones.

### Modelo existente
Existe allocator/cash-sweep PAPER, pero exige offers verificadas, fees exactos y snapshot completo de obligaciones. No puede usarse como atajo si falta evidencia económica.

### Evaluador nuevo preparado
Branch: `fix/rc6-cauciones-live-evaluator-20260909` (ATENCIÓN: creada originalmente sobre el HEAD UX anterior `0906d7...`; NO desplegar directamente. Portar/rebasar sólo los cambios necesarios sobre el HEAD live actual).
Archivos preparados:
- `rc6_caucion_contract.py`
- `rc6_caucion_live_evaluator.py`

Política:
- MarketData PPI read-only (`Current` + `Book`).
- `ORDER_ROUTING_ALLOWED=False`.
- registra TNA, mejor Bid, cantidad, plazo, mínimo, interés bruto, evidencia de comisión y blocker.
- `final_state=HOLD` mientras no esté certificada la evidencia completa de costos/derechos.
- para PESOS existe evidencia de comisión PPI 0,027%; faltan derechos/costo total certificado.
- para DOLAR la evidencia de comisión completa sigue faltando.

Estado: **PENDIENTE PORT A HEAD ACTUAL + WIRING RUNTIME + DASHBOARD + LIVE PROOF**.

## 7. Opciones — P0 nuevo
### Hallazgo confirmado
No falta universo. PPI ya tiene **382 OPCIONES AVAILABLE** observadas.
Todas están actualmente con capability `NEEDS_OPTION_CONTRACT` y `ready_paper_count=0`.

### Causa exacta
`SearchInstrument` prueba existencia/identidad, pero no prueba contrato derivado completo. `bu_instrument_catalog.contract_for()` bloquea OPCIONES sin `financial_contract_v17` verificable.

El contrato RC6 de una opción exige, como mínimo:
- moneda/mercado/settlement verificables;
- `cash_multiplier`;
- `quantity_step` entero;
- `expires_at` con timezone;
- `underlying`;
- `strike` positivo;
- `option_right` = CALL o PUT;
- `metadata_source` trazable.

No inferir estos campos sólo desde el ticker salvo que una fuente autoritativa documente la codificación y el normalizador preserve provenance.

Estado: **YELLOW / NO READY_PAPER** hasta cerrar evidencia contractual y wiring.

## 8. Históricos — P0 datos suficientes para hoy
RCA del 2026-09-09:
- ACCIONES: 24 AVAILABLE, 24 con history.
- CEDEARS: 35 AVAILABLE, 35 con history.
- BONOS, LETRAS, OPCIONES, FUTUROS, CAUCIONES y FCI: 0 history rows en el inventario observado en ese RCA.
- OPCIONES: 382 observed, 0 ready_paper.
- CAUCIONES: 10 observed, 0 history.
- A3 permanece `ALIGNMENT_UNVERIFIED` y fail-closed; no habilitar background A3 hasta cerrar alignment/payload contract.
- `PPI_HISTORY_UNIVERSE_READINESS=YELLOW`.

Política de trabajo de hoy:
- no saturar PPI intentando descargar indiscriminadamente todo el universo antes de la jornada;
- priorizar dataset mínimo operativo por familia y contratos líquidos/candidatos;
- distinguir contrato/readiness de histórico: una opción no queda operable sólo por tener velas, ni debe exigirse un histórico irrelevante si la estrategia usa subyacente + chain/book actual; cualquier excepción debe quedar documentada y probada;
- conservar provenance y freshness por fuente.

## 9. Scraping / Contract Evidence — P0
Último estado conocido antes de este checkpoint:
- sesión trusted PPI web genuinamente expirada;
- Account y Trading redirigían a `cuenta.portfoliopersonal.com/login`;
- formulario user/password presente;
- no OTP visible en el diagnóstico GET-only;
- Clarity/Hotjar/telemetría de terceros ya no son la causa raíz;
- no reutilizar el atajo incorrecto `/cuentas == authenticated`;
- RC4 collector/timer ya fue retirado, por lo que no debe existir competencia legacy por el perfil browser.

Objetivo inmediato:
- auditar owner RC6 actual, último snapshot/evidence y estado de sesión;
- recuperar sesión sólo mediante login legítimo permitido;
- ejecutar collector read-only sólo si trading session queda realmente autenticada;
- mapear evidencia a cauciones/opciones/otras familias y recalcular readiness.

## 10. Históricos + scraping: criterio de suficiencia para operar hoy
Se considera suficiente sólo si, para la familia que el motor vaya a evaluar:
1. identidad y contrato verificables;
2. market data live fresco y con semántica conocida;
3. costos/riesgo aplicables y no inferidos;
4. histórico/señal requerido por la estrategia disponible y fresco;
5. gate de familia registra APPROVE/HOLD/BLOCKED explícito;
6. todo permanece PAPER-only y real_orders_sent=0.

No se fuerza READY por deadline. Las familias incompletas deben quedar evaluadas y visibles como HOLD con blocker exacto.

## 11. Ramas de trabajo actuales
- `checkpoint/rc6-20260909-1142-parallel-go-live` — este checkpoint.
- `fix/rc6-preopen-dashboard-contract-20260909` — proof preopen pendiente.
- `fix/rc6-cauciones-live-evaluator-20260909` — portar cambios, no desplegar la rama stale directamente.
- cadena de `fix/rc6-trusted-reauth-*` — usar sólo como historial RCA; no rerun ciego.

## 12. Próxima secuencia obligatoria
1. Leer resultado del fix preopen y cerrar GREEN/RED con evidencia.
2. Auditar Contract Evidence RC6 live y sesión browser actual.
3. Auditar reglas/scraper de OPCIONES y localizar qué campos contractuales ya existen en DOM/API/evidence.
4. Cerrar/normalizar contrato de opciones con provenance autoritativa por instrumento.
5. Portar caucion evaluator al HEAD actual, cablearlo al runtime y dashboard y probar live.
6. Ejecutar ingest/historical targeted y recalcular readiness por familia.
7. Actualizar ESTE checkpoint después de cada cierre relevante.

## 13. Semáforo al crear el checkpoint
- UX/dashboard: GREEN.
- BYMA fail-closed: GREEN.
- legacy control plane: GREEN.
- safety real orders: GREEN.
- preopen wrapper: YELLOW, fix preparado / proof pendiente.
- cauciones: YELLOW, universo + modelo presentes / live evaluator pendiente.
- opciones: YELLOW, 382 instrumentos / contratos faltantes.
- históricos: YELLOW, cobertura parcial.
- scraping Contract Evidence: RED/YELLOW operativo por sesión web expirada; no implica caída de PPI API read-only.

Última actualización de este archivo: 2026-09-09 11:42 ART.

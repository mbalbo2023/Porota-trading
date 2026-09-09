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

## 3. HEAD live canónico
`2c9f3df2d03974ab6fe464d119bdbb2d90158706`

Estado probado:
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

### 4.4 Preopen RC6
**GREEN LIVE probado 2026-09-09 11:47 ART.**

RCA:
- `rc6_preopen.py` canónico esperaba correctamente `porota-trading-bot:17.0.0-rc6`.
- el wrapper host anterior esperaba erróneamente una familia `porota-trading-dashboard:17.0.0-rc6`.
- primer intento de despliegue del wrapper corregido falló sólo por `ModuleNotFoundError: rc6_preopen`, porque el wrapper vive en `/usr/local/lib/porota-sre-rc6` y el módulo canónico en `/opt/porota-trading`.
- segundo fix agregó resolución explícita del módulo desde `/opt/porota-trading`, sin copiar ni bifurcar la política canónica.

Branch: `fix/rc6-preopen-dashboard-contract-20260909`.
Commit final: `62b5029de073e496fbb952d652b810e806c2ac4f`.
Run GREEN: `34365904742`.

Proof live:
- schema `POROTA_RC6_PREOPEN_V2`.
- `status=GREEN`, `red_checks=[]`.
- release identity `17.0.0-rc6`, mode `PRODUCTION_PAPER`, execution `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED`.
- observer container GREEN, read-only rootfs=true, restarts=0.
- dashboard container GREEN, restarts=0.
- observer DB GREEN, quick_check=ok, session `MARKET_OPEN`, `ppi_auth=OK`, `real_orders_sent=0`.
- disco GREEN, libre > 8 GiB.
- timers requeridos RC6 GREEN/active/enabled.
- postflight `ok|PRODUCTION_PAPER|0|0|EXTERNAL_EXACT_V1`.
- runtime Git HEAD no cambió.
- no network order test, no order routes, no rollback automático.

## 5. Cauciones — P0
### Hallazgo
El universo contiene 10 cauciones AVAILABLE:
- PESOS1, PESOS2, PESOS7, PESOS30, PESOS120.
- DOLAR1, DOLAR2, DOLAR7, DOLAR30, DOLAR120.

Antes del fix: 0 decisiones, 0 allocations y 0 posiciones PAPER de cauciones. `bv_paper_runtime.py` no tenía worker de cauciones.

### Modelo existente
Existe allocator/cash-sweep PAPER, pero exige offers verificadas, fees exactos y snapshot completo de obligaciones. No puede usarse como atajo si falta evidencia económica.

### Evaluador nuevo preparado
Branch: `fix/rc6-cauciones-live-evaluator-20260909` (creada originalmente sobre `0906d7...`; NO desplegar directamente. Portar sólo los cambios necesarios sobre HEAD live actual).
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

## 6. Opciones — P0
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

El collector RC6 ya navega `/Cotizaciones/Opciones` y captura `SubyacenteOpciones`, pero el normalizador actual sólo materializa la lista de subyacentes; no materializa series individuales con strike/vencimiento/call-put/multiplicador/lote.

Evidencia pública autoritativa BYMA vigente consultada el 2026-09-09:
- opciones BYMA son americanas;
- desde 2026-04-24 la prima liquida T+0;
- lotes: 100 nominales para acciones, 10 para CEDEARs y 1000 para títulos públicos;
- cada serie posee strike y vencimiento predeterminados;
- el día de vencimiento la negociación llega hasta 15:30 y ejercicio/no-ejercicio hasta 15:59.
Estos términos generales NO sustituyen la identidad específica de cada una de las 382 series.

Estado: **YELLOW / NO READY_PAPER** hasta cerrar evidencia contractual individual y wiring.

## 7. Históricos — P0 datos suficientes para hoy
RCA del 2026-09-09:
- ACCIONES: 24 AVAILABLE, 24 con history.
- CEDEARS: 35 AVAILABLE, 35 con history.
- BONOS, LETRAS, OPCIONES, FUTUROS, CAUCIONES y FCI: 0 history rows en el inventario observado en ese RCA.
- OPCIONES: 382 observed, 0 ready_paper.
- CAUCIONES: 10 observed, 0 history.
- A3 permanece `ALIGNMENT_UNVERIFIED` y fail-closed; no habilitar background A3 hasta cerrar alignment/payload contract.
- `PPI_HISTORY_UNIVERSE_READINESS=YELLOW`.

Política de hoy:
- no saturar PPI intentando descargar indiscriminadamente todo el universo;
- priorizar dataset mínimo operativo por familia y contratos líquidos/candidatos;
- distinguir contrato/readiness de histórico;
- conservar provenance y freshness por fuente.

## 8. Scraping / Contract Evidence — P0
Último estado conocido:
- sesión trusted PPI web genuinamente expirada;
- Account y Trading redirigían a `cuenta.portfoliopersonal.com/login`;
- formulario user/password presente;
- no OTP visible en diagnóstico GET-only;
- Clarity/Hotjar/telemetría de terceros ya no son la causa raíz;
- no reutilizar el atajo incorrecto `/cuentas == authenticated`;
- RC4 collector/timer ya fue retirado, sin competencia legacy por el perfil browser.

Objetivo inmediato:
- auditar owner RC6, último snapshot/evidence y estado de sesión;
- recuperar sesión sólo mediante login legítimo permitido;
- ejecutar collector read-only sólo si trading session queda realmente autenticada;
- mapear evidencia a cauciones/opciones/otras familias y recalcular readiness.

Diagnóstico read-only en curso:
`diag/rc6-options-scraping-history-live-20260909`, workflow `rc6-options-scraping-history-live-diagnostic-20260909.yml`.
No abre nuevas sesiones PPI ni hace llamadas de red; lee DB, schedulers y snapshots sanitizados ya persistidos.

## 9. Criterio de suficiencia para operar hoy
Se considera suficiente sólo si, para la familia que el motor vaya a evaluar:
1. identidad y contrato verificables;
2. market data live fresco y con semántica conocida;
3. costos/riesgo aplicables y no inferidos;
4. histórico/señal requerido por la estrategia disponible y fresco;
5. gate de familia registra APPROVE/HOLD/BLOCKED explícito;
6. todo permanece PAPER-only y real_orders_sent=0.

No se fuerza READY por deadline. Familias incompletas deben quedar evaluadas y visibles como HOLD con blocker exacto.

## 10. Ramas de trabajo actuales
- `checkpoint/rc6-20260909-1142-parallel-go-live` — este checkpoint.
- `fix/rc6-preopen-dashboard-contract-20260909` — GREEN live; no requiere más cambio salvo consolidación posterior.
- `diag/rc6-options-scraping-history-live-20260909` — diagnóstico read-only en curso.
- `fix/rc6-cauciones-live-evaluator-20260909` — portar cambios, no desplegar rama stale directamente.
- cadena `fix/rc6-trusted-reauth-*` — historial RCA; no rerun ciego.

## 11. Próxima secuencia obligatoria
1. Leer diagnóstico Opciones/Contract Evidence/históricos live.
2. Auditar reglas/scraper de OPCIONES y localizar campos contractuales ya existentes en DOM/API/evidence.
3. Cerrar/normalizar contrato de opciones con provenance autoritativa por instrumento.
4. Portar caucion evaluator al HEAD actual, cablearlo al runtime/dashboard y probar live.
5. Ejecutar ingest/historical targeted y recalcular readiness por familia.
6. Corregir reauth/collector RC6 sólo con flujo legítimo y read-only.
7. Actualizar ESTE checkpoint después de cada cierre relevante.

## 12. Semáforo actual
- UX/dashboard: GREEN.
- BYMA fail-closed: GREEN.
- legacy control plane: GREEN.
- safety real orders: GREEN.
- preopen: **GREEN LIVE**.
- cauciones: YELLOW, universo + modelo presentes / live evaluator pendiente.
- opciones: YELLOW, 382 instrumentos / contratos individuales faltantes.
- históricos: YELLOW, cobertura parcial.
- scraping Contract Evidence: RED/YELLOW operativo por sesión web expirada; PPI API read-only está OK.

Última actualización: 2026-09-09 11:48 ART.

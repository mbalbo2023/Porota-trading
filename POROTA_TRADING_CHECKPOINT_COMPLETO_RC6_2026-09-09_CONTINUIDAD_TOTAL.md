# POROTA TRADING — CHECKPOINT COMPLETO RC6
## Continuidad total — 2026-09-09 16:00 ART
### CONTEXTO CANÓNICO PARA NUEVA CONVERSACIÓN

> LEER COMPLETO ANTES DE ACTUAR.
> Este archivo consolida el estado técnico, decisiones, restricciones, pruebas, SHA, olas, scraping, históricos, UX, seguridad y pendientes de la conversación.
> No reconstruir contexto desde memoria parcial. No repetir pruebas ya cerradas. No tratar CI verde como runtime verde.

---

## 1. REGLAS DE TRABAJO CON EL USUARIO

El usuario tiene cuadriplejia y opera principalmente por voz desde tablet Android / Voice Access.

Reglas:
- Preferir GitHub Actions → Droplet por SSH estricto.
- Intervención manual en Termius sólo si es excepcionalmente necesaria.
- Si hay comandos SSH, entregar preferentemente UN bloque completo.
- Scripts `.sh` salvo pedido contrario.
- `sudo -n` cuando corresponda.
- Evitar prompts interactivos.
- Salidas compactas aptas para portapapeles.
- No ZIP salvo pedido expreso.
- Trabajar todo lo posible en paralelo.
- Informar hallazgos parciales apenas aparezcan.
- RCA antes de reintentar un fallo.
- No pedir de nuevo información que ya está en este checkpoint.

---

## 2. SEGURIDAD ABSOLUTA

Modo actual:
- `PRODUCTION_PAPER`
- datos reales;
- decisiones y operaciones simuladas;
- órdenes reales bloqueadas.

Invariantes:
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `real_orders_sent=0`
- `ORDER_ROUTES=NOT_CALLED`
- `REAL_ORDER_TEST=NOT_PERFORMED`
- no `/Operar` en scraping;
- no `api/Orders`, `send_order`, `place_order`, `new_order`;
- no permission probes de órdenes;
- no bypass de OTP/2FA;
- no imprimir credenciales, cookies, tokens u OTP;
- `ROLLBACK_AUTOMATICO=NO`.

PPI Web Contract Evidence:
- navegación de scraping sólo `/Cotizaciones/*`;
- GET/HEAD/OPTIONS para collector;
- POST de auth sólo a endpoints explícitamente aprobados;
- telemetría LinkedIn/Hotjar/Clarity abortada y no confundida con auth.

---

## 3. REPOSITORIO Y BASE LIVE

Repo privado:
`mbalbo2023/Porota-trading`

Último baseline live comprobado en esta conversación:
`9197aedf35fe1739b594c742902e80df069c47c9`

Últimos invariantes live conocidos:
- observer running;
- dashboard running;
- restarts 0;
- DB `quick_check=ok`;
- `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- PPI API read-only OK;
- market data OK;
- BYMA fail-closed GREEN;
- provenance `EXTERNAL_EXACT_V1`;
- raw legacy count 0.

No confundir este live SHA con ramas deploy-prep.

Baselines históricos importantes:
- `bf25769749a203463b993d0923b116101f4ae032`: BYMA fail-closed GREEN intermedio.
- `0906d7d...`: UX intermedio.
- `2c9f3df...`: legacy cleanup intermedio.
- `9197aed...`: live actual conocido al cierre.

---

## 4. LEGACY RC4 / RC5 / HF

Se detectaron y retiraron ownerships activos heredados:
- `porota-contract-evidence-rc4.timer`
- `porota-introspeccion-hf5.timer`

Se migró primero la funcionalidad a RC6 y recién después se retiró legacy.

Resultado probado:
- `ACTIVE_LEGACY_RUNTIME=0`
- `ENABLED_LEGACY_TIMERS=0`

Regla:
RC4/RC5/HF pueden permanecer como evidencia histórica en Git, pero ningún runtime, timer, service, deploy path o readiness activo de RC6 debe depender de ellos.

---

## 5. BYMA CALENDAR FAIL-CLOSED

Se corrigió un P0: antes, si fallaba el calendario BYMA, el código podía usar `weekday()<5`.

Se cambió a fail-closed:
```python
except Exception:
    return False
```

Run V2:
`34353684905` SUCCESS.

Issue asociado: #37.

Este fix está incorporado en la línea que llevó al baseline live actual.

---

## 6. DASHBOARD / UX / ACCESIBILIDAD

Defectos reportados y corregidos:
- tablas sin encabezados/títulos;
- navegación lateral de Sistema desfasada al hacer scroll;
- falta de referencias a secciones inferiores;
- doble submenú;
- captions duplicados;
- Trading → Estrategias duplicando Scalping;
- Scalping/Workers en En Vivo;
- mala legibilidad tablet.

RCA:
- patch RC5 ocultaba `tr` de headers en tablet;
- Wave8 ocultaba labels por celda;
- normalizador agregaba caption duplicado;
- detector inicial de subnav no reconocía `<div class='subnav'>`.

Cambios:
- navegación de Sistema arriba;
- sticky/horizontal;
- índice “En esta página”;
- submenús claros;
- headers `<th>` restaurados;
- captions duplicados eliminados;
- scroll horizontal interno;
- Estrategias ya no embebe Scalping;
- Scalping separado;
- actividad real por familia visible en Trading;
- Scalping/Workers fuera de En Vivo;
- Riesgo ampliado.

W17 current-base:
- run `34388407505`
- SUCCESS
- estado: LIVE GREEN / READY.

---

## 7. PREOPEN RC6

Se detectó `porota-preopen-rc6.service=FAILED`.

RCA: el wrapper esperaba imagen de dashboard distinta de la real.

Se corrigió la identidad esperada y el path/import.

Resultado:
- preopen GREEN;
- PPI API OK;
- market open;
- timers RC6 GREEN;
- observer read-only;
- dashboard sano;
- DB ok;
- orders 0.

---

## 8. MOTOR — ACCIONES Y BONOS

El usuario no veía actividad en dashboard.

Proof live mostró que sí había análisis:
- ~1.425 decisiones del día;
- Acciones: ~540 decisiones, 539 HOLD, 1 BUY;
- Bonos: 41 decisiones, 41 HOLD;
- posición PAPER abierta observada: CEPU, `OPENED_SIMULATED`.

Bonos:
- 42 AVAILABLE;
- 0 `can_simulate=1`;
- blocker dominante: `NEEDS_NOMINAL_UNITS`.

Conclusión: el motor sí analizaba; faltaba visibilidad y readiness contractual.

---

## 9. REGLA P0 DE FAMILIAS

Pedido explícito del usuario: scraping + Contract Evidence deben completar los campos faltantes de TODAS las familias PPI para volverlas operativas/PAPER cuando haya evidencia suficiente.

Pipeline:
`discovery → API/scraping → evidencia contractual → normalización → provenance/freshness → readiness → can_simulate=1 → históricos → evaluación PAPER`

No inferir silenciosamente contratos.

Bloqueadores conocidos:
- Bonos/Letras/ON → `NEEDS_NOMINAL_UNITS`
- Opciones → `NEEDS_OPTION_CONTRACT`
- Futuros → `NEEDS_FUTURES_MARGIN_AND_CONTRACT`
- Cauciones → términos/costos incompletos
- ETF/Índices → mapping/readiness según caso

---

## 10. OPCIONES

Snapshot observado:
- ~382 Opciones AVAILABLE;
- 0 READY_PAPER;
- blocker general `NEEDS_OPTION_CONTRACT`.

Campos faltantes por serie:
- subyacente;
- strike;
- vencimiento;
- CALL/PUT;
- multiplier/lote;
- provenance.

El collector llegaba a `/Cotizaciones/Opciones` y capturaba subyacentes, pero el normalizador no materializaba todavía el contrato individual por serie.

Información BYMA útil pero no suficiente para inventar series:
- prima T+0;
- opciones americanas;
- lotes generales según familia.

No habilitar por inferencia de ticker.

---

## 11. CAUCIONES

10 cauciones AVAILABLE:
- PESOS1, PESOS2, PESOS7, PESOS30, PESOS120
- DOLAR1, DOLAR2, DOLAR7, DOLAR30, DOLAR120

Al inicio:
- 0 decisiones;
- 0 allocations;
- 0 posiciones PAPER.

RCA: no había worker live de cauciones en el loop principal.

Se diseñó/creó evaluador RC6 read-only/PAPER:
- Current/Book;
- TNA;
- mejor Bid;
- cantidad;
- plazo;
- mínimo;
- interés bruto;
- costo conocido;
- blocker exacto;
- `ORDER_ROUTING_ALLOWED=False`.

Comisiones corregidas:
- pesos: 2% + IVA anual;
- dólares: hasta 1% + IVA anual;
- sumar derechos de mercado.

Mientras falte costo completo:
- evaluar;
- mostrar HOLD;
- no promover a PAPER.

Branches:
- `feature/rc6-cauciones-contract-model-20260908`
- `fix/rc6-cauciones-live-currentbase-20260909`
- `fix/rc6-cauciones-live-evaluator-20260909`
- `fix/rc6-cauciones-paper-evaluation-20260909`

---

## 12. HISTÓRICOS PPI

Snapshot inicial:
- 246 targets;
- 67 instrumentos persistidos;
- 17 fallidos;
- 66 `VALID_PAYLOAD`
- 170 `PARTIAL`
- 10 `EMPTY_OR_INVALID`

Más tarde:
- Acciones: 30 instrumentos / ~7.111 filas;
- CEDEARs: 37 instrumentos / ~8.479 filas.

El selector descarga targets `AVAILABLE` cuando `can_simulate=1` más índices.

Por eso no entran todavía Bonos/Letras/ON/Opciones/Cauciones si sus contratos no cierran.

Batch:
- incremental;
- hasta 40;
- evita saturar PPI.

Orden correcto:
`contrato/readiness → can_simulate → histórico`

W13:
- backfill planner;
- reconciler multi-fuente;
- A3 fail-closed;
- `canonical_write=DENY`.

A3: `ALIGNMENT_UNVERIFIED`.

---

## 13. IOL / INVERTIRONLINE HISTÓRICO

Se comprobó que IOL sí sirve como fuente histórica complementaria.

Se verificó histórico real para:
- GGAL;
- AL30;
- AAPL/CEDEAR;
- intradiario reciente para GGAL.

Rol objetivo:
- read-only;
- segunda fuente;
- backfill;
- reconciliación;
- provenance `IOL`;
- cross-validation.

NO reemplaza PPI Contract Evidence.

Problema detectado: la tabla histórica actual usa PK `(symbol,date)`, por lo que una fuente puede pisar a otra.

Antes de usar IOL como reconciliador real:
- persistir por fuente, conceptualmente `(symbol,date,source)`;
- equivalente para intradiario;
- luego construir vela canónica reconciliada.

Branches:
- `feature/rc6-iol-history-readonly-20260907`
- `feature/rc6-iol-history-shadow-20260907`
- `feature/rc6-iol-history-shadow-deploy-20260907`
- `feature/rc6-iol-history-shadow-verified-20260907`
- `wave19/next-iol-readonly-crossvalidation-20260909`

---

## 14. CONTRACT EVIDENCE / SCRAPING PPI — RCA COMPLETO

### 14.1 Por qué funcionaba antes
El 4 de septiembre podía traer Data porque el Chrome trusted profile todavía tenía sesión PPI válida.

Con sesión válida:
- collector rc=0;
- captura;
- normalización;
- importación.

### 14.2 Qué pasó al expirar
Collector detectó `BLOCKED_AUTH_SESSION_EXPIRED` y devolvió `rc=4`.

Systemd lo etiquetaba como `NOPERMISSION`, pero el 4 era semántica interna del collector, no un permiso Linux demostrado.

### 14.3 Bug `set -e`
El wrapper reactivaba `set -e` antes de devolver `rc=4`.
Bash terminaba el servicio antes de la rama de reauth.

Se corrigió.

### 14.4 Ownership Chrome
Se detectó `BROWSER_PROFILE_KEYFILE_OWNER_MISMATCH`.

Tres archivos internos estaban como root:
- Preferences
- Secure Preferences
- Local State

Se corrigieron sólo esos nodos, sin `chown -R`.

### 14.5 Legacy competing
Contract Evidence RC4 estaba activo y podía competir por el perfil Chrome.
Fue retirado.
Legacy activo final = 0.

### 14.6 Telemetría
Se interceptaron POSTs de LinkedIn, Hotjar y Clarity.
Se abortan sin ampliar allowlist y sin tratarlos como login.

### 14.7 No `/Operar`
Antes de live proof se detectaron rutas GET `/Operar/*` en un collector candidato.
Se eliminaron.
Gate actual exige `/Cotizaciones/*` solamente.

### 14.8 Formulario PPI actual
Diagnóstico GET-only:
- `#username`
- `#password`
- botón `Ingresar`
- campos visibles, enabled y editable.

No se imprimieron valores.

### 14.9 Fill resiliente
Se agregó lógica:
- relocalizar;
- click;
- fill;
- verificar;
- reintentar;
- no imprimir valores.

Run de materialización:
`34388873990` SUCCESS.

Rama W12 luego avanzó a:
`d7cb02145f4a545fd67f374a42f4c73bc458ad90`

### 14.10 Último live proof W12

Run:
`34389232945`

Candidate usado en ese run:
`2665f10da909cc03e03ce05b39ab3f67cc517d79`

Static:
- 14 tests PASS
- `W12_STATIC_GATE=GREEN`

Pre:
- `PRE_DB=ok|PRODUCTION_PAPER|0`
- `CE_RUNS_PRE=103`

Install:
- `W12_ISOLATED_INSTALL=GREEN`

Service:
- `SERVICE_START_RC=0`

Secuencia:
- `BLOCKED_AUTH_SESSION_EXPIRED`
- `AUTH_BROWSER_STARTED=YES`
- `REAUTH_ATTEMPTED=YES`
- `BLOCKED_AUTH_USERNAME_FILL_FAILED`
- backoff
- `REAUTH_DIAG_STAGE=AUTH_LOOP_2`
- `REAUTH_DIAG_PAGE=https://cuenta.portfoliopersonal.com/cuentas`
- `REAUTH_DIAG_BLOCKED=NONE`
- finalmente `BLOCKED_AUTH_PASSWORD_FILL_FAILED`

Resultado:
- `FRESH_CAPTURE=NO`
- `CE_RUNS_POST=103`
- `POST_DB=ok|PRODUCTION_PAPER|0`
- `ORDER_ROUTES=NOT_CALLED`
- `REAL_ORDER_TEST=NOT_PERFORMED`
- `ROLLBACK_AUTOMATICO=NO`
- `W12_LIVE_PROOF=YELLOW_NO_FRESH_CAPTURE`

Interpretación:
- no es fallo de seguridad;
- no es fallo de PPI API;
- reauth ya entra;
- el bloqueo está dentro del flujo del formulario/login;
- aún no hay evidencia fresca.

Próximo paso:
probar el HEAD W12 actual `d7cb021...` o sucesor verificado en una única corrida controlada.

No repetir V3 con candidate viejo.

---

## 15. ÚLTIMA DATA CONTRACT EVIDENCE

En diagnósticos previos:
- scheduler RC6 activo/enabled;
- última captura válida conocida antes de los fixes: `contract_20260908T025621Z.json`;
- session status entonces: authenticated trusted device.

DB ya contenía aproximadamente:
- 855 evidencias;
- 389 contratos v2;
- 765 snapshots;
- 834 contract states intradía.

No se perdió data vieja.
Problema actual = frescura.

---

## 16. ESTADO DE OLAS W9–W18

### W9
Rama: `deploy-prep/rc6-wave9-currentbase-20260909`
HEAD observado: `08112ca2ec44c7a856619ffc1cab9c13752e6822`
Estado: GREEN / CODE READY

Incluye contrato nominal Bonos/Letras/ON fail-closed, modelo cauciones y tests.

### W10
Rama: `deploy-prep/rc6-wave10-currentbase-20260909`

Wiring real confirmado en `be_paper_engine.py`.
Live previo: container policy `OBSERVATION_ONLY`.

RCA: `porota_mode_manager.py` congelaba policy en `PAPER_DEFAULTS` y `HF3_FROZEN_PAPER_SETTINGS`.

Se materializó `PAPER_SECTOR_CONCENTRATION_POLICY=BINDING`.

HEAD observado: `617244b23b54e4c227b1d76bc217460be4d78cd5`
Estado: GREEN / CODE READY

Antes de cierre deploy:
- live proof BINDING;
- bloquear apertura al límite;
- no bloquear cierres.

### W11
Rama: `deploy-prep/rc6-wave11-currentbase-20260909`
Estado: GREEN / CODE READY

### W12
Rama: `deploy-prep/rc6-wave12-currentbase-20260909`
HEAD más nuevo observado: `d7cb02145f4a545fd67f374a42f4c73bc458ad90`
Estado: YELLOW / P0
Bloquea fresh Contract Evidence.

### W13
Rama: `deploy-prep/rc6-wave13-currentbase-20260909`
Estado: GREEN / CODE READY

### W14
Rama: `deploy-prep/rc6-wave14-currentbase-20260909`
Estado: GREEN / CODE READY

### W15
Rama: `deploy-prep/rc6-wave15-currentbase-20260909`
Estado: GREEN / CODE READY

### W16
Rama: `deploy-prep/rc6-wave16-currentbase-20260909`
Run final: `34388315928` SUCCESS
Estado: GREEN / READY

Housekeeping: builder prune; no images, containers, volumes ni datasets.

### W17
Rama: `deploy-prep/rc6-wave17-currentbase-20260909`
Run: `34388407505` SUCCESS
Estado: GREEN / LIVE GREEN / READY

### W18
Rama: `deploy-prep/rc6-wave18-currentbase-20260909`
V4 run: `34389298145` SUCCESS
Se eliminó rollback automático del package.
Estado: GREEN / CODE READY

---

## 17. ISSUES / CHECKPOINTS RELEVANTES

- #37 BYMA fail-open → corregido.
- #44 legacy runtime cleanup.
- #45 preopen RC6 → corregido.
- #40 Telegram E2E test-only.
- #38 IOL next version; ahora IOL histórico es útil.
- #36 feriados USA deben cubrir todos los CEDEARs.
- #28 A3 sólo tras alignment.
- #27 no RC4 runtime active.
- #25 old rollback tracker → no usar.
- #20 old RC5 tracker superseded.
- #6 gaps contractuales por familia siguen conceptualmente abiertos hasta W12/familias.

Checkpoint previo:
`checkpoint/rc6-20260909-1142-parallel-go-live`

Archivo previo:
`POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-09_1142.md`

Este archivo supersede ese resumen, sin borrarlo.

---

## 18. ORDEN DE CONTINUIDAD INMEDIATA

Paralelizar:

1. Refrescar GitHub + runtime live.
2. W12:
   - probar HEAD actual `d7cb021...` o sucesor;
   - una sola corrida;
   - diagnostics sanitizados;
   - fresh capture;
   - auth trusted;
   - `/Cotizaciones/*` only;
   - import;
   - CE runs >103;
   - DB/PAPER/orders0.
3. W10:
   - integrar;
   - live proof BINDING.
4. W18:
   - integrar package V4 sin auto rollback.
5. Consolidar W9–W18 sobre current live baseline.
6. Recalcular family readiness tras fresh evidence.
7. Ampliar históricos PPI sólo para familias READY.
8. Integrar IOL read-only con persistence multi-source.
9. Checkpointar cada cierre.

---

## 19. GO / NO-GO

No declarar FULL GO por CI solo.

FULL GO requiere:
- candidate exact SHA;
- tests;
- observer/dashboard;
- DB;
- PAPER;
- orders0;
- no order routes;
- BYMA fail-closed;
- PPI API;
- W12 fresh evidence o decisión explícita de GO restringido;
- W10 BINDING live;
- W18 no auto rollback;
- historical health;
- family readiness;
- no legacy;
- UX;
- disk;
- postflight.

Veredictos:
- GO
- GO CON RESTRICCIONES
- NO-GO

---

## 20. FRASE FINAL DE CONTINUIDAD

RC6 live `9197aed...` está sano y seguro en PRODUCTION_PAPER. W9/W10/W11/W13/W14/W15/W16/W17/W18 están mayormente CODE READY/LIVE GREEN. W12 sigue YELLOW porque la reautenticación PPI Web todavía no produjo fresh Contract Evidence. El siguiente intento debe usar W12 `d7cb021...` o un sucesor verificado, manteniendo fail-closed, órdenes reales en cero y `/Cotizaciones/*` only.

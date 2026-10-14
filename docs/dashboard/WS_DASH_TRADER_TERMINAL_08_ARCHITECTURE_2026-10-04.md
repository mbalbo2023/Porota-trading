# WS-DASH-TRADER-TERMINAL-08 — Arquitectura integral del Dashboard RC6 — 2026-10-04

## 0. Estado, ownership y límites

- WORKSTREAM_ID: `WS-DASH-TRADER-TERMINAL-08`
- MODE: `WRITE_OWNER` exclusivo del scope dashboard definido en esta orden.
- Branch reservada: `work/ws-dash-trader-terminal-08-20261004`
- Base productiva revalidada: `deploy/rc6-pr69-isolated-20260915@da697c6e6c2274579f9e4a112fabc4327475dd35`.
- Comparación base vs productiva: identical / ahead=0 / behind=0 al iniciar.
- DEPLOY_OWNER: `NOT_ACQUIRED`.
- NO MERGE.
- NO DEPLOY.
- NO runtime mutation.
- NO DB write/migration.
- NO systemd/Docker mutation.
- NO PPI Watch.
- NO broker/order route.
- PAPER/SHADOW ONLY.
- `real_orders_sent=0`.
- FIX-FORWARD ONLY.

### Concurrencia obligatoria

El candidato de motor vigente para reauditoría es PR #466, branch `integration/ws-fix-audit-08-reaudit-20261004`, HEAD observado al crear esta orden `c27dfd963c4fe83465c0f2105347e974fbbe6356`. Es READ_ONLY para este workstream. No modificar ningún path de #466.

PRs dashboard antiguos abiertos que deben tratarse como inputs/scope ajeno, no como ramas a modificar:
- #423: `rc6_annual_instrument_analysis.py` + test.
- #422: `bh_universe_dashboard_hf6.py` + test.
- #344: suite UX antigua.
- #107: private Decision Cockpit 8766 y systemd asociado.

No cerrar, reescribir ni force-push ninguna de esas ramas. Si su lógica es útil, reutilizarla por adapter/wrapper o documentar reconciliación futura. Todo el trabajo nuevo vive en la branch de este WS y debe confluir después en un único candidato reconciliado con #466.

---

# 1. Objetivo de producto

Reconstruir integralmente el dashboard visible de POROTA TRADING RC6 con el look & feel del mockup aprobado: terminal oscura profesional, compacta, de alta densidad informativa, orientada a trader experto y accesible por voz/tablet.

El objetivo NO es “maquillar” las páginas actuales. Debe rediseñarse:
1. la arquitectura de información;
2. la navegación;
3. la jerarquía visual;
4. la selección de métricas;
5. la semántica de estados;
6. el detalle por estrategia/familia;
7. la proyección read-only de datos;
8. la accesibilidad/responsive;
9. la coherencia cross-route.

El dashboard debe permitir operar y auditar el PAPER día a día sin obligar al operador a pensar en nombres internos de módulos, workers o tablas.

---

# 2. Principios UX / trader obligatorios

1. **Una pantalla = una pregunta operacional principal.**
2. **No infinite scroll.** Cada destino usa submenús/tabs; máximo una tabla principal por subvista.
3. **10 filas visibles por defecto**; “Mostrar 10 más”, “Mostrar menos” y X de Y.
4. **Sin scroll horizontal como experiencia primaria.** En tablet, columnas secundarias pasan al detalle expandible.
5. **No color-only.** Siempre texto + color + iconografía.
6. **HOT ≠ BUY.** HOT es alta prioridad de observación/análisis.
7. **DISCOVERY/WARM son observación**, nunca permiso de entrada.
8. **READY ≠ STRATEGY_ELIGIBLE ≠ TRADEABLE ≠ HOT ≠ SIGNAL ≠ PAPER**.
9. **Green se reserva para PASS/healthy/resultado positivo real de la métrica.** HOT usa ámbar/naranja; WARM amarillo/dorado; DISCOVERY violeta; bloqueos rojo; UNKNOWN/NO_VERIFICADO gris.
10. **No mezclar monedas.** ARS, USD, USD_MEP, USD_CCL se presentan separados salvo una conversión explícita con fuente/timestamp, que hoy no debe inventarse.
11. **Score no es probabilidad** salvo calibración OOS demostrada.
12. **Win rate no es KPI suficiente**: acompañar muestra, net P&L, costos, profit factor/expectancy cuando sea matemáticamente válido y drawdown.
13. **Freshness visible.** Todo dato vivo debe tener fuente y reloj/as_of; stale nunca se vuelve verde por generated_at.
14. **Exact identity**: ticker + family/asset_class + market + currency + settlement. Nunca join por ticker solo cuando pueda existir ambigüedad.
15. **Dashboard read-only.** Nunca recalcula autoridad financiera ni crea decisiones.
16. **El detalle técnico no debe dominar la vista de trader.** Reason codes, hashes, generation IDs y provenance viven en drawer/details, salvo alerta crítica.
17. **Refresh no disruptivo**: conservar foco, expansión, filtros, tab y scroll; no reemplazar DOM mientras Voice Access está interactuando.
18. **ART visible** para reloj operativo. Relojes provider/receipt se mantienen separados en detalle.
19. **Responsive real** a 1440/1280/1024/800/600/360 px.
20. **No hardcodear datos ficticios en producción.** Fixtures de UI sólo en tests.

---

# 3. Nueva arquitectura de navegación

Reducir primer nivel a ocho destinos:

1. **Inicio**
2. **En Vivo**
3. **Trading**
4. **Universo**
5. **Instrumentos**
6. **Riesgo**
7. **Analítica**
8. **Sistema**

Las URLs legacy permanecen compatibles/redirect/wrapper cuando corresponda, pero desaparecen del menú principal como destinos competidores.

### Reubicación obligatoria

- `/scalping` → Trading > Scalping.
- `/validacion` → Analítica > Experimentos/Validación.
- `/historicos` → Analítica > Histórico.
- `/analisis` → Analítica > Análisis/Performance o Instrumentos cuando sea análisis de especie.
- `/aprendizaje` → Analítica > Aprendizaje/Experimentos.
- `/reportes` → Analítica > Reportes.
- `/postcierre`/Cierre → Analítica > Cierre diario.
- `/salud`, `/sre`, `/infra`, scheduler, logs, config → Sistema.
- `/motor-trading` → Trading > Resumen/Detalle del motor.
- `/informacion-financiera` → Instrumentos/Analítica según contenido.
- `/testing`, `/observacion`, `/telegram` dejan de ser navegación primaria y quedan en Sistema/compatibilidad.

---

# 4. Shell visual global

## 4.1 Sidebar izquierda

Persistente en desktop, colapsable en tablet/móvil. Debe seguir el mockup:
- marca POROTA TRADING RC6;
- icono + label completo;
- destino activo claramente marcado;
- sin frases decorativas obligatorias si quitan espacio;
- controles Voice Access identificables por texto.

## 4.2 Header superior fijo

En TODAS las pantallas:
- POROTA TRADING RC6 + destino actual.
- chip `PAPER / SHADOW ONLY`.
- Modo: autoridad runtime.
- Sesión: PREOPEN / MARKET_OPEN / POSTCLOSE / CLOSED / NOT_DUE.
- Selector/capacity state: OFF / SHADOW / APPROVED_DYNAMIC / BASELINE_FAIL_CLOSED.
- PPI auth/primary status.
- `real_orders_sent=0`.
- Heartbeat + age.
- Último dato de mercado + age.
- reloj ART.

No repetir luego estas mismas métricas en todas las páginas salvo contexto específico.

## 4.3 Subnav sticky

Cada destino principal posee submenú horizontal sticky. La subvista se conserva en URL/path/query y debe ser deep-linkable.

## 4.4 Design system

Crear tokens CSS centralizados:
- background navy/charcoal;
- panels dark-blue;
- borders sutiles;
- typographic scale compacta;
- status pills;
- metric cards;
- dense tables;
- progress bars;
- funnel chevrons;
- event timeline;
- drawers/details;
- mini-panels;
- alert banners.

No copiar una librería pesada si no es necesaria. Preferir HTML/CSS/JS mínimo sobre FastAPI existente.

---

# 5. INICIO — “¿Cómo estoy y qué exige atención ahora?”

## Mantener / mostrar

Tarjetas superiores, sólo las que cambian decisiones:
- Patrimonio PAPER por moneda.
- Caja disponible / reservada por moneda.
- PnL neto hoy: realizado y no realizado, separado por moneda.
- Exposición actual / límite.
- Riesgo diario usado / presupuesto.
- Operaciones abiertas.
- HOT / señales candidatas.
- Sesión + tiempo a cierre/EOD.
- Alertas críticas activas.

Zona central:
- **Atención ahora**: máximo 5 alertas accionables priorizadas: posición sin book fresco, risk limit cerca, source crítico stale, exit supervisor degradado, DB/worker relevante, etc.
- preview de posiciones abiertas (máx 5).
- preview de oportunidades HOT/WARM (máx 5).
- performance intradía por moneda/estrategia.
- riesgo resumido.

## Quitar/reubicar

- Win rate aislado como tarjeta ejecutiva.
- detalles de systemd/workers/API.
- tablas de readiness completas.
- logs.
- información contractual.
- reportes históricos largos.

Inicio debe ser un dashboard de “estado + atención”, no un inventario técnico.

---

# 6. EN VIVO — “¿Qué está haciendo POROTA en esta rueda y por qué?”

Subtabs:
1. **Resumen**
2. **Oportunidades**
3. **Decisiones**
4. **Posiciones**
5. **Cierres**
6. **Capacidad & Coverage**
7. **Riesgo & Workers**
8. **Fuentes**

## 6.1 Resumen

- Operaciones abiertas.
- Cerradas hoy.
- HOT ahora.
- Señales candidatas.
- PnL neto hoy por moneda.
- workers críticos OK/stale.
- embudo operativo:
  `READY → ELIGIBLE → TRADEABLE → DISCOVERY → WARM → HOT → SIGNAL → ECONOMICS → RISK → PAPER`.
- preview mesa oportunidades.
- preview promociones/demotions.
- preview decisiones/posiciones/cierres/capacidad.

## 6.2 Oportunidades

Mesa principal, máximo 10 filas:
- Prioridad.
- Ticker.
- Familia.
- Estrategia.
- Estado HOT/WARM/DISCOVERY/BLOCKED.
- Tradeability.
- Warmup.
- Freshness.
- Señal.
- Economics.
- Risk.
- Motivo humano.

Filtros:
- Todos / HOT / WARM / DISCOVERY / BLOCKED.
- estrategia.
- familia.
- moneda.
- mercado.
- búsqueda.

Panel lateral:
**Promociones / demociones / descartes en vivo** con reloj y reason code:
- DISCOVERY→WARM.
- WARM→HOT.
- HOT→BLOCKED.
- HOT→WARM/DISCOVERY.
- SCANNER_CAPACITY.
- STALE.
- warmup incomplete.
- economics/risk failure.

Detalle expandible del instrumento:
- identidad exacta;
- rank + componentes;
- selected/promoted/demoted timestamps;
- last useful observation;
- usable observation fraction;
- planned vs achieved revisit;
- discovery age;
- source/provenance;
- current spread/depth cuando exista;
- signal/economics/risk chain;
- reason codes técnicos;
- preopen digest/config fingerprint sólo en detalle.

## 6.3 Decisiones

No mostrar un HOLD genérico como si fuera toda la explicación.

Tabla:
- Hora.
- Identidad.
- Estrategia.
- Estado previo.
- Signal result.
- Economics PASS/FAIL/NO_EVAL.
- Risk PASS/FAIL/NOT_CALLED.
- Final: PAPER_OPENED / HOLD / BLOCKED / OBSERVE_ONLY.
- Reason principal.

Drawer:
- score con label “NO PROBABILIDAD”;
- variables causales usadas;
- source clocks;
- costo esperado;
- liquidity/depth;
- risk usage;
- evidence IDs.

## 6.4 Posiciones

Prioridad máxima de seguridad.

Por posición:
- símbolo + identidad;
- estrategia/familia;
- lado;
- cantidad/unidad;
- entrada;
- mark executable o label no ejecutable;
- PnL no realizado por moneda;
- PnL realizado parcial;
- costos;
- tiempo en posición;
- stop/target;
- distancia a stop/target;
- MaxHold restante;
- EOD restante;
- mark freshness;
- bid depth / exit liquidity;
- supervisor state;
- exit reader state;
- risk contribution;
- lifecycle especializado cuando sea FUTURO.

Nunca mostrar MFE/MAE=0 como medido. Usar NO_MEDIDO si no existe trayectoria válida.

## 6.5 Cierres

- hora entrada/salida;
- duración;
- exit reason;
- gross PnL;
- costos;
- net PnL;
- moneda;
- estrategia/familia;
- MFE/MAE sólo si provenance válido;
- stop/target/EOD/maxhold;
- resultado vs economics esperado.

Resumen por exit reason y estrategia, sin mezclar monedas.

## 6.6 Capacidad & Coverage

Trader/quant necesita saber si el motor no opera porque “no hay oportunidad” o porque “no mira suficiente”.

Mostrar:
- capacity policy status;
- safe capacity por engine cuando esté demostrada;
- baseline cuando dynamic no esté approved;
- discovery age p50/p95/max;
- touched fraction;
- useful/distinct observation fraction;
- planned revisit vs achieved;
- HOT/WARM/DISCOVERY counts;
- scanner capacity rejects;
- endpoint budgets;
- opened/exit priority;
- latency p50/p95 si evidencia existe;
- status OPEN evidence / NO_VERIFICADO.

No presentar benchmark fuera de rueda como capacidad real.

## 6.7 Riesgo & Workers

Sólo los workers que cambian seguridad de la rueda:
- exit supervisor;
- exit reader;
- factual paper runtime;
- risk worker;
- dynamic shadow worker;
- scalping worker.

Mostrar:
- state;
- heartbeat;
- age;
- last success/error;
- open positions supervised;
- stale/coverage exception.

El detalle técnico completo queda en Sistema.

## 6.8 Fuentes

Sólo fuentes relevantes a las decisiones del momento:
- PPI primary.
- IOL complement.
- BYMA public scraper observe-only.
- A3/ROFEX/otras cuando apliquen.

Por fuente:
- state;
- role;
- provider/event clock;
- received/capture clock;
- freshness;
- LKG;
- affected fields/families;
- conflicts.

`SOURCE_UNAVAILABLE` debe ser scoped; nunca IOL=0.

---

# 7. TRADING — “¿Qué estrategias están activas y cómo opera cada familia?”

Subtabs:
1. Resumen
2. Equity Spot
3. Scalping
4. Renta Fija
5. Derivados
6. Tesorería / FCI

## Resumen

Por estrategia:
- strategy/lifecycle owner;
- status: ACTIVE_PAPER / SHADOW / OBSERVE_ONLY / NO_VERIFICADO;
- entry_authority;
- familias;
- universe/eligible/tradeable/hot;
- cadence;
- última evaluación;
- posiciones abiertas;
- operaciones hoy;
- net PnL por moneda;
- principal blocker.

No repetir catálogo/contract matrix completa.

## Equity Spot

ACCIONES/CEDEARS/ETF:
- tradeability;
- spread/depth;
- freshness;
- samples;
- momentum/RVOL/microstructure cuando existan;
- signal;
- economics;
- risk;
- exits;
- CEDEAR clocks/ratio/underlying sólo si evidencia.

## Scalping

- mode;
- HOT/WARM/DISCOVERY;
- intraday contract;
- warmup progress;
- cadence achieved;
- spread/depth;
- economics;
- MaxHold/EOD;
- current candidates;
- paper positions;
- blockers.

## Renta fija

BONOS/LETRAS/ON:
- nominal/unit convention;
- price convention;
- settlement;
- maturity;
- coupon/cashflows si existe;
- TIR/duration/parity/accrued si existe y provenance válido;
- liquidity/spread/depth;
- carry;
- strategy status.

Nunca fallback a momentum equity.

## Derivados

OPCIONES/FUTUROS:
- exact contract;
- underlying;
- call/put/strike/expiry para opciones;
- multiplier/tick/settlement;
- IV/Greeks/OI sólo si fresco y validado;
- margin/reserve semantics;
- futures variation;
- expiry/EOD lifecycle;
- specialized entry authority.

## Tesorería / FCI

Cauciones:
- settled free cash;
- rate;
- tenor;
- minimum/step si verificado;
- costs;
- maturity;
- opportunity cost;
- allocation/sweep state.

FCI:
- NAV clock;
- subscription/redemption status;
- liquidity horizon;
- strategy status.

No momentum scanner.

---

# 8. UNIVERSO — “¿Qué parte del mercado ve, prioriza o excluye POROTA?”

Subtabs:
1. Resumen
2. Discovery
3. Tradeability
4. Coverage
5. Excluidos
6. Familias

## Resumen

- catalog READY;
- strategy eligible;
- tradeable;
- discovery/warm/hot counts;
- distribución por familia/mercado/moneda;
- coverage touched;
- discovery age.

## Discovery

- current tier;
- last touched;
- discovery age;
- anomaly/reason;
- source;
- useful observation;
- promotion/demotion clock.

## Tradeability

- rank;
- components;
- liquidity;
- activity;
- spread;
- depth;
- freshness;
- movement potential.

Label obligatorio: tradeability NO es señal direccional ni probabilidad.

## Coverage

- planned vs achieved cadence;
- p50/p95/max age;
- untouched/late when causally demonstrable;
- coverage by family/source;
- scanner capacity exclusions.

## Excluidos

Top reason codes + listado:
- no strategy;
- identity ambiguous;
- contract gap;
- stale;
- insufficient depth/spread;
- source unavailable scoped;
- scanner capacity;
- no recent trades.

## Familias

Policy/routing por familia:
- strategy/lifecycle;
- status;
- entry authority;
- cadence class;
- source authority;
- deep-analysis eligibility;
- reason when OBSERVE_ONLY.

---

# 9. INSTRUMENTOS — “¿Qué sé exactamente de esta especie?”

Subtabs:
1. Buscador
2. Ficha
3. Contrato
4. Mercado & Liquidez
5. Evidencia

Buscador con identidad completa y filtros.

## Ficha
- canonical identity;
- description;
- family;
- market/currency/settlement;
- RUNTIME_READY;
- strategy route;
- current universe state;
- tradeability;
- active position/candidate if any.

## Contrato
Campos family-specific, nunca columnas vacías irrelevantes:
- units/lot/nominal;
- settlement;
- maturity;
- coupon/cashflows;
- strike/expiry/right;
- multiplier/tick;
- ratio CEDEAR;
- minimum/step;
- provenance/state.

## Mercado & Liquidez
- last/bid/ask;
- depth;
- spread;
- last trade;
- timestamps;
- historical activity summary;
- freshness.

## Evidencia
- PPI primary fields;
- IOL complementary fields;
- public sources;
- conflicts;
- LKG;
- timestamps;
- contract evidence.

No convertir complementos en autoridad de identidad PPI.

---

# 10. RIESGO — “¿Cuánto puedo perder y qué puede quedar sin salida?”

Subtabs:
1. Resumen
2. Exposición
3. Límites
4. Posiciones
5. Liquidez & Salidas
6. P&L / Drawdown
7. Event Risk

## Resumen
- equity/cash/reserved/exposure por moneda;
- daily realized/unrealized/costs;
- daily risk consumed;
- open positions;
- concentration;
- stale marks;
- unsupervised exits;
- risk alerts.

## Exposición
- family;
- strategy;
- symbol;
- currency;
- notional/normalized exposure only when units compatible;
- concentration;
- top positions.

## Límites
Config vs current:
- risk/trade;
- position cap;
- total exposure;
- daily soft/hard stop;
- emergency cap;
- family-specific guards.

## Posiciones
- risk contribution;
- stop distance;
- time/MaxHold;
- stale mark;
- exit coverage;
- liquidity.

## Liquidez & Salidas
- bid depth;
- spread;
- executable mark;
- exit-reader reserve;
- stale/current;
- positions without executable book;
- EOD/expiry urgency.

## P&L / Drawdown
- realized;
- unrealized;
- costs;
- net;
- drawdown;
- separated currencies;
- strategy/family attribution.

## Event Risk
Mostrar SHADOW/BINDING explícito; una noticia no puede parecer permiso/veto si no lo es.

---

# 11. ANALÍTICA — “¿El sistema tiene edge y qué está aprendiendo?”

Subtabs:
1. Performance
2. Señales
3. Salidas
4. Histórico
5. Experimentos
6. Cierre diario
7. Reportes

## Performance
Por moneda/strategy/family:
- trades;
- gross;
- costs;
- net;
- win rate;
- profit factor;
- expectancy;
- avg win/loss;
- drawdown;
- holding time;
- sample size.

No declarar edge con muestra insuficiente.

## Señales
- score buckets;
- hit rate;
- forward returns;
- MFE/MAE;
- AUC/calibration sólo con dataset correcto;
- cohorts family/symbol/hour/regime;
- explicit `score_is_probability=false` mientras corresponda.

## Salidas
- exit reasons;
- TP/SL/EOD/MaxHold;
- factual vs SHADOW alternatives;
- same-entry counterfactuals;
- trailing/break-even experiments;
- MFE/MAE provenance.

## Histórico
Calidad de datos:
- coverage;
- depth;
- freshness;
- gaps;
- candles;
- source;
- no readiness.

## Experimentos
- preregistered entry evaluators;
- economic/exit lab;
- OOS status;
- registry/version;
- observations;
- NO automatic promotion.

## Cierre diario
Trader journal de la rueda:
- pnl/costs by currency;
- opened/closed;
- exit reasons;
- funnel;
- why no trade;
- late/missed discovery sólo si demostrado causalmente;
- no-trade baseline;
- cash/caucion opportunity cost si existe evidencia;
- alerts/incidents.

## Reportes
Semanal/mensual/versionado/downloads. No duplicar toda la analítica en pantalla.

---

# 12. SISTEMA — “¿La plataforma técnica está sana?”

Subtabs:
1. Resumen
2. Salud
3. Workers
4. Integraciones
5. Scheduler
6. Evidencia
7. Backups
8. Logs
9. Configuración

## Resumen
- runtime process;
- DB;
- dashboard;
- critical worker health;
- disk;
- latest preflight/deploy identity if available;
- real_orders_sent.

## Salud
component health con state/freshness/last success/next check.

## Workers
heartbeat, age, state, failures, lag.

## Integraciones
PPI/IOL/BYMA/A3/etc como integración técnica. No usar esta página para decidir BUY.

## Scheduler
internal jobs + systemd snapshot, stale semantics, next run.

## Evidencia
Después de #466, consumir únicamente generación SHADOW comprometida/coherente:
- `CURRENT.json`;
- generation_id;
- sequence;
- manifest digest;
- source watermark;
- configuration fingerprint;
- report/checkpoint/status cross hashes;
- retention status;
- preopen freeze digest.

**Nunca mezclar latest/checkpoint/status independientes.**
Usar el lector canónico de generación de #466 después de la reconciliación. Si el contrato no está disponible en esta rama base: mostrar `NO_VERIFICADO_AWAITING_RECONCILIATION`, no reconstruir por mtimes.

## Backups
coverage, last backup/checkpoint, protected stores.

## Logs
sanitized, bounded; últimas líneas + downloads.

## Config
effective non-secret values + origin. Nunca secretos.

---

# 13. Autoridades de datos

Mantener/fortalecer:

| Concepto | Autoridad |
|---|---|
| runtime/mode/safety | observer_state / canonical runtime truth |
| catálogo | financial_instrument_catalog |
| RUNTIME_READY | candidate_identity_v2 |
| contrato | contract_evidence_v2_current |
| selección dinámica | generación SHADOW comprometida de #466 |
| funnel | operational_funnel dentro del mismo cut |
| decisiones factuales | paper_decisions + decision_evidence + trade_gate_evaluations |
| posiciones/fills spot | paper_positions / paper_fills / ledger canónico |
| futures | specialized lifecycle |
| risk | DailyRisk + ledgers/guards canónicos |
| históricos | history_canonical_v2 / candle store |
| sources | source authority + api/source health con clocks |
| scheduler | systemd snapshot / operational jobs |
| reports | reports versionados |

Regla absoluta: el frontend no deriva un estado más fuerte que la fuente.

---

# 14. Qué está de más hoy y debe desaparecer de la navegación principal

- Panel vs Testing vs Observación como conceptos duplicados.
- Scalping como top-level separado de Trading.
- Validación top-level.
- Históricos, Análisis, Aprendizaje y Reportes como cuatro silos.
- Salud, SRE, Infra, Scheduler, Logs y Config fragmentados.
- readiness matrix repetida en múltiples destinos.
- PPI/real_orders_sent repetidos como cards en todas las páginas.
- grandes párrafos explicativos repetidos.
- códigos internos ocupando columnas principales.
- win rate aislado sin economics/sample.
- tablas técnicas enormes donde el trader necesita excepción/summary.

Toda la evidencia sigue accesible en la pantalla primaria correcta o en detalle.

---

# 15. Qué falta hoy y debe incorporarse

- funnel operacional vivo;
- promociones/demotions;
- tradeability vs signal claramente separados;
- discovery age y coverage real;
- planned vs achieved revisit;
- capacity status;
- why-no-trade;
- economics/risk chain;
- position exit liveness;
- time-to-EOD/MaxHold;
- exit liquidity;
- PnL/costs/drawdown separados por moneda;
- performance por strategy/family;
- causal late/missed discovery cuando sea demostrable;
- source clocks y conflicts;
- committed SHADOW generation integrity;
- alert center de trader;
- exact identity drill-down.

---

# 16. Arquitectura de código preferida

Evitar seguir creciendo el monolito `bg_paper_dashboard.py`.

Crear un paquete nuevo, por ejemplo:

```
rc6_trader_dashboard/
  __init__.py
  design_system.py
  projection.py
  shell.py
  views_home.py
  views_live.py
  views_trading.py
  views_universe.py
  views_instruments.py
  views_risk.py
  views_analytics.py
  views_system.py
  routes.py
```

Principios:
- queries/readers bounded y read-only;
- una proyección por request, reutilizada por componentes;
- componentes puros de render;
- SQL parametrizado;
- exact identity;
- no ORM/migración salvo decisión explícita posterior;
- no broker imports;
- no network calls al abrir páginas;
- no materializar catálogos completos si sólo se muestran 10 filas;
- server-side pagination/filter;
- no N+1;
- caché únicamente si no miente sobre freshness y con invalidación explícita.

La instalación en FastAPI debe requerir el mínimo cambio posible en código legacy. Legacy routes pueden wrapper/redirect a nuevas vistas para conservar bookmarks y tests.

No modificar las ramas #422/#423. Si una de sus optimizaciones es necesaria, documentar que se espera su reconciliación o implementar equivalente en el nuevo adapter sin copiar autoridad divergente.

---

# 17. Accesibilidad y Voice Access

Obligatorio:
- labels textuales completos;
- botones con nombres únicos;
- ARIA para tabs/pagers/details;
- foco visible;
- orden DOM lógico;
- no hover-only;
- no icon-only para acciones;
- detalles expandibles anunciables;
- preservar foco en refresh;
- ningún auto-refresh mientras un control tiene foco;
- `prefers-reduced-motion`;
- contrast WCAG razonable;
- status no sólo por color;
- 10 filas;
- tablet sin swipe horizontal obligatorio;
- a <=1024 px mover columnas de baja prioridad a `details`;
- a <=600 px convertir row principal en card semántica sin perder labels.

---

# 18. Performance de UI

Gates:
- ninguna ruta principal debe cargar el universo completo para mostrar 10 filas;
- LIMIT/OFFSET server-side;
- consultas agregadas por SQL;
- bounded JSON/evidence reads;
- no loops de miles de queries;
- no read/write SQLite;
- no `PRAGMA quick_check` al render;
- no network provider calls;
- tiempo de render medible en tests con fixtures grandes;
- 12k instrumentos / 60k observaciones sintéticas no deben volver la UI no acotada.

---

# 19. Tests obligatorios

Crear tests nuevos; no editar los archivos de test propiedad de PR #344.

## Semántica financiera
- HOT nunca renderiza verde como PASS.
- DISCOVERY/WARM no muestran BUY authority.
- score nunca se rotula probabilidad.
- currencies no se suman.
- readiness sólo candidate_identity_v2.
- history no gobierna readiness.
- complementos no sobrescriben PPI identity.
- exact identity joins.
- stale no se presenta LIVE.
- SOURCE_UNAVAILABLE scoped.
- NO_VERIFICADO visible.

## #466 generation contract
- reader sólo acepta committed coherent generation.
- mismatch digest/generation => fail closed.
- missing #466 contract in isolated branch => NO_VERIFICADO, no fake data.
- after integration uses `read_committed_generation` / canonical equivalent.

## UX
- exactamente 8 items top-level.
- legacy URLs siguen funcionando.
- subnav por sección.
- max 10 rows opening.
- Mostrar más/menos.
- no duplicate nav.
- no page horizontal overflow at 360/600/800/1024.
- focus/scroll/details preserved across refresh.
- all actionable controls have textual accessible name.
- no auto refresh during interaction.
- status text plus color.

## Routes
Smoke authenticated 200 for all canonical views and legacy routes.
Inventory/truth matrix updated and zero unclassified routes.

## Security
- dashboard package has no order broker callable/import.
- no POST mutation introduced by new canonical pages.
- real_orders_sent invariant remains display/read only.
- PPI Watch untouched.

## Performance
- bounded queries and evidence file size.
- large synthetic catalog rendering bounded.
- no full materialization for first page.

## Global
- py_compile.
- diff --check.
- dashboard focused suite.
- governed repo-root suite.
- Predeploy V2 exact branch HEAD if compatible with concurrent integration state.

Nunca excluir/skip/xfail un test para poner GREEN salvo exclusión preexistente gobernada y demostrada.

---

# 20. Criterios de aceptación visual

El resultado debe parecer el mismo producto que el mockup aprobado:
- terminal dark premium;
- sidebar compacta;
- top status strip;
- subnav clara;
- tarjetas densas pero legibles;
- tables low-noise;
- semantic badges;
- funnel;
- timeline;
- mini-preview panels;
- no páginas blancas legacy en rutas canónicas;
- no scroll interminable;
- no duplicación de navegación;
- trader encuentra cualquier dato operativo importante en <=2 niveles de navegación.

No usar texto decorativo/marketing para llenar espacio. Priorizar datos y decisión.

---

# 21. Cierre exigido a Codex

Trabajar autónomamente hasta:
1. inventariar y revalidar rutas/datasets actuales;
2. implementar design system + shell;
3. migrar 8 destinos y subtabs;
4. mantener legacy compatibility;
5. crear adapters a verdad actual y futura #466;
6. ejecutar tests focales y full suite;
7. corregir fallos fix-forward;
8. actualizar route inventory/truth matrix;
9. publicar PR DRAFT GREEN;
10. ejecutar Predeploy V2 exacto si el estado concurrente lo permite sin tocar runtime;
11. entregar handoff con branch, SHA, tree, PR, tests, run/artifact si aplica, gaps externos, paths, ownership release.

Estado final permitido:
`READY_FOR_INTEGRATION_AND_DEPLOY_CANDIDATE`

No decir DESPLEGADO/VALIDADO_RUNTIME.
No mergear.
No desplegar.
No adquirir DEPLOY_OWNER.

La reconciliación final deberá combinar este dashboard con el candidato de motor vigente (#466 o su sucesor auditado) en un único candidato y un único Deploy V2 cuando el usuario lo autorice.

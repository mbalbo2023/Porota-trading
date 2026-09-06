# POROTA TRADING — CHECKPOINT CANÓNICO RC6 — ESTADO CORREGIDO POSTDEPLOY

Fecha: 2026-09-06

Este documento corrige el checkpoint previo cuando información posterior al deploy dejó algunos ítems desactualizados. Para continuidad técnica, este archivo debe prevalecer sobre cualquier lista anterior que marque como pendientes de implementación componentes ya desplegados o ya materializados en RC6.

## 1. Identidad live que NO debe cambiarse por este checkpoint

- Branch live: `release-candidate/v17.0.0-rc6-deploy3-20260906`
- SHA live: `5bdad270c2a23bb2456a320d41e18940ce70ec6f`
- Imagen live: `porota-trading-bot:17.0.0-rc6`
- Runtime: `PRODUCTION_PAPER`
- Observer/dashboard: RC6
- Capacidad de órdenes reales: bloqueada
- `real_orders_sent=0`

Este checkpoint es documental. No reemplaza ni modifica la identidad live.

## 2. Corrección crítica — Contract Evidence RC6

El estado anterior `BLOCKED_PENDING_RC6_NATIVE_WIRING` quedó desactualizado.

Posteriormente se materializó y desplegó Contract Evidence RC6 nativo mediante:

- Branch: `candidate/v17.0.0-rc6-contract-evidence-native-20260906`
- Workflow: `RC6 native Contract Evidence deploy`
- Run: `34055591313`
- Head SHA del workflow exitoso: `2368fe90d28e1edeb855afbacfd848e378c3b246`
- Conclusión del workflow: `success`

El deploy:

1. verificó que `/opt/porota-trading` continuaba exactamente en el SHA live RC6;
2. verificó observer RC6 read-only, sin restarts y `real_orders_sent=0`;
3. instaló los módulos Contract Evidence RC6 nativos fuera del árbol live, bajo `/usr/local/lib/porota-contract-evidence-rc6`;
4. instaló `/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh`;
5. instaló `porota-contract-evidence-rc6.service` y `porota-contract-evidence-rc6.timer`;
6. deshabilitó timers Contract Evidence RC4/RC5;
7. probó el domingo `STATUS=GREEN_NOT_DUE`;
8. verificó `AUTH_BROWSER_STARTED=NO` fuera de ventana;
9. verificó nuevamente `REAL_ORDERS_SENT=0`;
10. habilitó y dejó activo el timer RC6.

Estado canónico:

`CONTRACT_EVIDENCE_RC6_NATIVE=GREEN_ACTIVE_NOT_DUE_SUNDAY`

Por lo tanto Contract Evidence NO es backlog de implementación. Lo pendiente es únicamente observar y validar su primer run realmente DUE durante una jornada/ventana operativa.

### Cadencias Contract Evidence vigentes

- Operability/dynamic: 15 minutos durante mercado.
- Cauciones: 5 minutos durante mercado.
- Auctions/licitaciones: 5 minutos durante mercado.
- Derivative series: 15 minutos durante mercado.
- Static contract: diario, post-close.
- Full browser audit: semanal, viernes post-close.
- Browser autenticado: nunca fuera de ventana y nunca en el hot path de trading.

## 3. Corrección crítica — A3 Historical

A3 Historical NO debe figurar como pendiente de construcción.

RC6 ya contiene:

- `ew_a3_history_rc6.py`
- `ex_a3_history_job_rc6.py`
- modos `BOOTSTRAP`, `DAILY_INCREMENTAL`, `RECONCILE`, `WEEKEND_DEEP`;
- checkpoints persistentes;
- estados `COMPLETE`, `EMPTY_OBSERVED`, `EMPTY_CONFIRMED`, `FAILED`, `ALIGNMENT_UNVERIFIED`;
- identificación estricta por `symbol + family + market + settlement`;
- integración con history store v2;
- provenance/source `A3_CEM_CLOSING`;
- ejecución sólo con mercado cerrado y `real_orders_sent=0`;
- aislamiento del hot path y ausencia de métodos de órdenes.

Los timers A3 RC6 ya estaban instalados/activos postdeploy.

Estado canónico: A3 Historical está RESUELTO como ingeniería. Lo que resta es aumentar y medir cobertura mediante los schedulers existentes.

## 4. Backlog corregido

### 4.1 RESUELTO / NO backlog de implementación

- Contract Evidence RC6 nativo: RESUELTO y desplegado; falta sólo primera evidencia DUE.
- A3 Historical engine: RESUELTO.
- A3 daily/reconcile/weekend schedulers: RESUELTOS.
- History freshness/depth metrics: RESUELTAS.
- Close-only salvage con separación de FULL_OHLC: IMPLEMENTADO.
- SHADOW learning counterfactual: IMPLEMENTADO.
- Tablet/Voice Access compact table layer: IMPLEMENTADA; falta field acceptance física.
- Telegram centralized suppression policy con P0/CRITICAL unsuppressible: IMPLEMENTADA.
- Sector observation framework: IMPLEMENTADO en modo observacional.
- Forward Lab expectancy dimensionalmente coherente: IMPLEMENTADA.
- MFE/MAE con precios ejecutables simulados (ask entrada / exit ejecutable): IMPLEMENTADO como cálculo.

### 4.2 PENDIENTE DE EVIDENCIA / ACUMULACIÓN TEMPORAL

Estos puntos no son deuda de construcción y no deben tratarse como defectos del Go Live PAPER:

- primer run Contract Evidence realmente DUE;
- crecimiento de coverage histórico A3 y resto de fuentes;
- acumulación de sesiones SHADOW (~20 ruedas objetivo observacional, sin autopromoción);
- candle integrity longitudinal;
- medición de efectividad del close-only salvage;
- aceptación real del dashboard en tablet Samsung + Voice Access;
- acumulación de resultados PAPER para análisis estadístico.

### 4.3 PENDIENTE REAL DE INGENIERÍA P2

1. Forward Lab v2 / robustez estadística:
   - bootstrap por `trading_day_ar` / stationary or block bootstrap;
   - leave-day-out;
   - leave-symbol-out;
   - cohorts/subperiods;
   - tratamiento de dependencia/panel cuando corresponda;
   - multiple testing / DSR o equivalente;
   - evidencia suficiente antes de cualquier promoción BINDING.

2. MFE/MAE v2 persistence/provenance:
   - persistencia explícita;
   - `excursion_state`/coverage;
   - trazabilidad de quote/executable source;
   - control de ventanas faltantes.

3. Retention/dbstat/storage governance:
   - política explícita de retención;
   - métricas dbstat/storage longitudinales;
   - housekeeping sin borrar evidencia útil.

4. Mejoras posteriores no críticas:
   - mapa sectorial autoritativo completo;
   - correlaciones/concentración avanzada;
   - ampliar fuentes históricas para familias que continúan `PROBE_REQUIRED`.

## 5. Gobernanza especial — Contract Evidence como host overlay

Contract Evidence RC6 nativo quedó instalado como overlay del host bajo `/usr/local/...` mientras `/opt/porota-trading` permaneció deliberadamente en el SHA live `5bdad270...`.

Esto es aceptable y seguro para el Go Live PAPER, pero debe mantenerse como deuda de reproducibilidad/recovery:

- una reconstrucción completa del host debe reinstalar explícitamente el overlay Contract Evidence RC6;
- a futuro conviene canonicalizar ese overlay dentro del release/recovery mechanism sin alterar innecesariamente el runtime live antes del Go Live.

IMPORTANTE PARA AUDITORÍAS:

El ZIP exportado como "exact deployed RC6 source" desde la rama `export/rc6-deployed-20260906` deriva directamente del SHA live y puede no incluir los archivos Contract Evidence instalados posteriormente bajo `/usr/local/...`. Por ello una auditoría basada sólo en ese ZIP puede producir un falso positivo diciendo que Contract Evidence RC6 no existe o no está wired. Cualquier hallazgo de ese tipo debe contrastarse con el workflow/run `34055591313` y con el estado efectivo del host.

## 6. Gate real para el lunes / Go Live PAPER

No se conocen P0 técnicos abiertos que por sí solos justifiquen cancelar el Go Live PAPER.

El gate real permanece el preopen operativo:

- SHA/branch/image correctos;
- observer/dashboard running y sin restarts;
- read-only rootfs;
- DB quick_check OK;
- disco saludable;
- timers esperados enabled/active;
- PPI auth/read path esperado;
- `real_orders_sent=0`;
- real-order capability bloqueada;
- calendar/session correcta;
- reglas especiales de mercado/underlying cuando correspondan;
- Contract Evidence y A3 sin afectar el hot path.

Si el preopen da GREEN, el objetivo es operar PAPER, observar y producir evidencia, no bloquear por ítems que ya fueron implementados o que requieren necesariamente varias ruedas.

## 7. Regla de interpretación para futuras auditorías

Cada hallazgo debe clasificarse en una de estas cuatro categorías:

1. P0/P1 realmente abierto y reproducible contra el estado efectivo del host.
2. Ya resuelto en código/deploy y auditoría basada en snapshot anterior o incompleto.
3. Implementado pero pendiente de evidencia temporal/longitudinal.
4. P2/mejora que no bloquea PAPER y debe resolverse antes de BINDING/real-money si corresponde.

Nunca elevar a blocker del Go Live PAPER un hallazgo sólo porque el checkpoint viejo lo llamaba backlog.

## 8. Regla operativa persistente

El usuario sigue siendo la última instancia para cualquier comando manual en terminal/Droplet. Preferencia operativa: cambios vía GitHub Actions -> SSH estricto -> preflight -> backup cuando corresponda -> activación transaccional -> postflight -> rollback. La intervención manual por Termius debe ser excepcional.

---

Este archivo es el checkpoint canónico corregido para continuar el análisis de auditorías y el Go Live PAPER del 2026-09-07.
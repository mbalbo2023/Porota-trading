# CHECKPOINT 02 — runtime probes y limpieza — 2026-09-30

## Evidencia GitHub Actions

- Run inicial: `36790388149`.
- Run corregido de preopen: `36790466663`.
- Base de trabajo: `fc27321c2f460639471f298a88fa717ebad37780`.
- PAPER invariant observado: `PRODUCTION_PAPER / real_orders_sent=0`.

## Disco — VALIDADO_RUNTIME

Primer cleanup:
- antes: `2.117.816.320 B` libres;
- después: `4.716.548.096 B` libres;
- recuperado: `2.598.731.776 B`;
- temporales Deploy V2 restantes: `0`;
- PPI Watch: `UNTOUCHED=GREEN`;
- contenedores observer/dashboard: running antes y después;
- volúmenes/SQLite/datos persistentes: no eliminados.

Segundo cleanup de confirmación:
- antes/después: `4.716.531.712 B`;
- recuperado adicional: `0 B`.
- Conclusión: cleanup idempotente y disco por encima del piso bloqueante de 2 GiB.

## Preopen 2026-10-01 — VALIDADO_RUNTIME

Run `36790466663`, después del cleanup:
- `T_MINUS_45 RC=0`;
- `T_MINUS_10 RC=0`;
- ambos devuelven `status=GREEN`, `red_checks=[]`, `read_only=true`, sin prueba de ruta de órdenes;
- observer: `WAITING_MARKET / MARKET_CLOSED / PPI auth OK / real_orders_sent=0`;
- timers RC6 requeridos: GREEN;
- scalping worker: GREEN;
- caucion cash sweep worker: GREEN a nivel de heartbeat/runtime;
- BYMA morning watch: AMBER `BYMA_CHANGE_REVIEW_REQUIRED` para `OPEN_DATA`;
- IOL reference fallback: AMBER `SOURCE_UNAVAILABLE`, caución ARS `SOURCE_UNAVAILABLE_NO_LKG`;
- history quarantine sigue reportada según política existente.

## Caución — RCA runtime

Worker:
- `PAPER_CAUCION_SWEEP_MODE=ACTIVE_PAPER`;
- estado runtime: `HOLD`;
- heartbeat vivo;
- `real_orders_sent=0`, `routes_json=[]`.

Bloqueo persistido:
- código: `EXACT_FEE_OR_VERSIONED_PAPER_TARIFF_MISSING`;
- `offers=0`;
- 10 cauciones en catálogo, todas `STALE`;
- candidate_identity_v2: 10 `STALE / can_simulate=0`;
- Evidence v2 actual: 2 filas;
- allocations: 0;
- paper_cauciones: 0.

Conclusión RCA parcial:
- el worker existe y corre;
- el blocker primario a investigar es la ausencia de identidad PPI CURRENT/AVAILABLE + evidencia dinámica utilizable; el mensaje de tarifa aparece después de quedar el set de ofertas vacío y no debe asumirse como única causa.

## Históricos — P0 confirmado

Canonical history store:
- `history_canonical_v2`: 324.905 filas.
- READY runtime: 6.955.
- READY con historia por identidad exacta: 1.228.
- READY sin historia canónica: **5.727**.

Cobertura exacta READY:
- ACCIONES: 51/126;
- BONOS: 27/1.823;
- CEDEARS: 177/968;
- FCI: 962/1.013;
- LETRAS: 10/31;
- OBLIGACIONES: 0/2.993;
- OPCIONES: 1/1.

La base histórica sí contiene además familias como CAUCIONES/FUTUROS/ON, pero no alinea con la identidad READY vigente en la cobertura requerida.

Runtime/config:
- `porota-history-postclose-rc6.timer`: masked/inactive;
- `rc6_postclose_history_job.py`: `BLOCKED_BY_POLICY / RC6_HISTORY_QUARANTINE`;
- policy operacional histórica: sólo ACCIONES/CEDEARS;
- A3 history timers: activos para derivados, con 104 estados de ingesta.

Conclusión: la ingesta histórica de los nuevos instrumentos **NO está completa**. Es un P0 real; READY no implica histórico.

## Dashboard/Riesgo

El probe del HTML falló por 401 porque la instrumentación host no aportó el Bearer token. Es un fallo del probe, no evidencia de que la página esté caída.
La siguiente Action usará el token sólo internamente, sin imprimirlo, para localizar:
- los tres `data-porota-record='1'` residuales;
- el layout efectivo de `/riesgo`.

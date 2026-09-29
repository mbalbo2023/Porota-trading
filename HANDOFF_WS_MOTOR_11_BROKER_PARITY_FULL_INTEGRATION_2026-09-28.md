# HANDOFF — WS-MOTOR-11 broker parity full integration

## Identidad del candidato

- Repositorio: `mbalbo2023/Porota-trading`
- Base productiva revalidada: `a03142bc440bbb871714e4ae931d73513d073a56`
- Branch: `work/ws-motor-11-broker-parity-full-integration-20260928`
- Commit de código/evidencia: `c7ab1362cf9a2bab0d8078ee0f0fa3857f4e2e35`
- PR: `#359`, DRAFT, base `deploy/rc6-pr69-isolated-20260915`
- Predeploy del commit de código: run `36509017467` / run number `90`
- Merge: no. Deploy: no.

Este handoff se agrega en un commit documental posterior. El SHA final de la
rama y su Predeploy exacto quedan consignados en el cuerpo de PR #359 y en la
copia final persistente de este handoff, evitando afirmar que un archivo puede
contener el SHA del commit que lo crea.

## FASE 0 y reconciliación

- La sesión comenzó READ_ONLY.
- HEAD real fue consultado en GitHub; no se tomó el checkout local como verdad.
- PR #357: open/draft, head `98ecf90859fe33dd66986b70f1c0010b0321fdb9`.
- PR #358: open/draft, head publicado `17aa11e...`.
- El SHA WS10 `3eddbd0d...` nunca existió en GitHub y no se usa ni se cita como
  base remota.
- No había Actions queued/in-progress al adquirir ownership.
- Mutex de predeploy: `rc6-unified-paper-deploy`, `cancel-in-progress=false`.
- #357/#358 no fueron modificados ni mergeados. La reconstrucción es
  fix-forward selectiva sobre el HEAD real.

De #357 sólo se conservó el fix estrecho que evita falsa ambigüedad en la
identidad primaria. Se descartaron TTL de identidad de 14 días, capture time
como quote time y supresión de complementos por ticker/familia. De WS09/WS10 se
reconstruyeron clave con moneda, autoridad única de readiness, TTL por campo,
perfiles OPEN/CLOSE/EVENT/FULL y exclusión de requisitos de cuenta real.

## Cómo operan realmente los brokers

PPI e IOL no toman la decisión desde un único endpoint. Componen maestro interno
de especies, formulario/ticket, validadores pre-trade, reglas del mercado,
clearing, backoffice y lifecycle. Sus APIs/MCP exponen proyecciones parciales de
esa unión. POROTA tampoco necesita replicar todo: para PAPER sólo exige los
campos que un consumidor concreto usa para identidad, sizing, cashflow, riesgo,
settlement, evento atravesado o cierre.

Jerarquía implementada, complementaria y sin pisado:

1. PPI structured/catalog/XHR: identidad primaria y término PPI explícito.
2. IOL MCP/API: complemento estructurado por campo.
3. BYMA/A3/ROFEX/Clearing/gestora: contrato, margen y lifecycle oficiales.
4. XHR/DOM PPI/IOL: último recurso para un campo PAPER indispensable.
5. Derivación: sólo aritmética/regla oficial trazable.

El merge usa `family+ticker+market+currency+settlement`. Un complemento nunca
crea ni sobrescribe identidad PPI. Valores ausentes se completan; conflictos de
negocio se conservan y bloquean; diferencias de provenance no son conflictos.

## Requisitos corregidos por consumidor PAPER

| Familia | OPEN mínimo real | Fuera de OPEN |
|---|---|---|
| Acciones/CEDEAR/ETF | identidad PPI exacta + quote vigente; contrato unitario BYMA | ISIN, ratio descriptivo, tick y fee duplicado |
| Bonos/Letras/ON | identidad + multiplier/base + mínimo + step | maturity/cupón/amortización son EVENT; analytics enrichment |
| Opción long | identidad + underlying/right/strike/expiry + multiplier + mínimo/step | ejercicio es EVENT; griegas/OI enrichment |
| Caución colocadora | identidad/lado/fechas/mínimo/step/base + tasa/depth/timestamp frescos | metadata descriptiva; fee ARS duplicado usa tarifario central |
| FCI | clase/moneda/mínimo/step/estado de suscripción | NAV/cutoff/rescate son EVENT; manager/custodian enrichment |
| Futuro | identity/underlying/expiry/multiplier/mínimo/step + margen vigente | settlement method es EVENT; tick value derivable |

`fee_schedule`, `trading_session`, `price_tick` e `isin` dejaron de bloquear
OPEN porque sus funciones ya tienen otra autoridad o no participan de la
matemática PAPER. Hay un guard que exige consumidor nombrado para todo campo
blocking y validación positiva para términos numéricos críticos.

## Fuente → captura → cache → Evidence → contrato → readiness

- `rc6_iol_family_reference.py`: conserva parciales de renta fija y opciones;
  incluye fondos y cauciones.
- `rc6_broker_parity_evidence.py`: ingesta offline, identidad PPI obligatoria,
  prioridad estructurada y DOM sólo residual.
- `cp_contract_evidence_v2_hf6.py`: clave con moneda, migración forward-only,
  historial append-only y TTL semántico.
- `rc6_contract_bridge.py`: normaliza únicamente la identidad completa y
  rechaza conflicto/moneda incorrecta.
- `cq_family_contract_rules_hf6.py`: autoridad única, perfiles y consumidores.
- `bu_instrument_catalog.py`: capability final del executor PAPER existente.
- `rc6_paper_family_lifecycle.py`: diseño aislado/idempotente FCI y futuros,
  sin cliente ni ruta de broker; todavía no se finge integrado.

## Replay controlado before/after

- Snapshot sanitizado generado en directorio temporal.
- El replay opera sobre una copia; SHA-256 del original antes/después idéntico.
- DB productiva no abierta ni modificada.
- 20 testigos, 222 filas instrumento/campo, cero extrapolación.
- Before: 0 READY; 222/222 filas sin evidencia integrada.
- After: 73/222 filas evidenciadas; 149 `UNRESOLVED`.
- Resultado final: `GD30=READY_PAPER_SPOT` y
  `GFGC6000OC=READY_PAPER_OPTION_LONG`.
- Otros 18 testigos: `BLOCKED_DATA`; FCI y futuros además exhiben
  `BLOCKED_EXECUTOR` en eje independiente.
- `READY_PAPER_CANDIDATE` queda sólo en la frontera interna de Evidence. El
  replay atraviesa bridge+catalog y reporta el capability final.

Matriz machine-readable:
`docs/evidence/ws_motor_11_before_after.json`. Informe humano:
`docs/WS_MOTOR_11_FIELD_SOURCE_MATRIX.md`.

## Qué agrega el MCP/API

En la matriz de 222 filas:

- IOL MCP observado: 24/222 (10,8%).
- IOL MCP usable tras binding PPI exacto: 20/222 (9,0%).
- PPI structured + IOL structured usables: 50/222 (22,5%).
- Toda la evidencia integrada: 73/222 (32,9%).

El MCP agrega chain de opciones, vencimiento/analytics/simulación de renta fija,
inventario FCI y términos publicados de caución. No aporta identidad PPI,
mínimo/step del ticket, profundidad caución ejecutable, margen de clearing ni
todos los eventos. Las cuatro filas MCP sin binding quedan informativas, no
promueven READY. Detalle: `docs/WS_MOTOR_11_QUE_AGREGA_EL_MCP.md`.

## Qué agrega el scraping

No se ejecutó scraping nuevo. Se reutilizó evidencia read-only sanitizada de
WS10 únicamente para campos del ticket PPI no resueltos en structured/oficial:
mínimo/step/base GD30, términos exactos GFGC6000OC, clase FCI observada, binding
PPI DLR/DIC26 y estado after-hours de caución. Scraping no crea timestamp de
mercado, profundidad, identidad ni autoridad superior. Si no agrega un campo
indispensable consumido por PAPER, queda fuera del camino crítico. Detalle:
`docs/WS_MOTOR_11_QUE_AGREGA_EL_SCRAPING.md`.

## Estado por familia e instrumento

| Familia | Resultado comprobado |
|---|---|
| Acciones/CEDEAR/ETF BYMA | El executor/catálogo ya produce `READY_PAPER_SPOT` por identidad PPI exacta + quote; no se agregó un gate descriptivo |
| Bonos/Letras/ON | GD30 READY; AL30, D30N6, YMCJO, YMCIO y S31O6 conservan sólo sus faltantes propios de mínimo/step/base |
| Opciones | GFGC6000OC READY long; GFGC7000OC, AAPC1000O y GFGC50000A siguen bloqueadas por contrato de esa serie |
| Cauciones | Executor existente; faltan oferta vigente, depth/principal, step y provider timestamp según testigo |
| FCI | Binding/término OPEN parcial y lifecycle PAPER todavía no conectado |
| Futuros | Contrato/margen parcial y lifecycle PAPER todavía no conectado |

No se declara READY por familia. Cada fila se decide por identidad exacta.

## Blockers residuales reales

### Datos

- Renta fija vecina: mínimo, step o base/multiplier explícitos de cada especie.
- Opciones vecinas/CEDEAR/ajustada: contrato exacto de cada serie.
- Caución: oferta/depth y timestamp proveedor frescos; USD además presupuesto
  de fee aplicable.
- FCI: clase PPI exacta y términos OPEN por clase; NAV sólo al atravesar EVENT.
- Futuro: contrato exacto por serie y margen vigente de clearing.

### Executor/lifecycle

- FCI: falta conectar el lifecycle idempotente PAPER al ledger/catálogo.
- Futuros: falta conectar reserva de margen, variación diaria, déficit y cierre.
- Caución no tiene blocker de executor general: el residuo demostrado es dato
  dinámico ejecutable por oferta.

## RCA, guards y tests

RCA/fixes principales: clave sin currency; autoridades duplicadas; TTL global;
EVENT mezclado con OPEN; cuenta real mezclada con PAPER; executor mezclado con
dato; requisitos sin consumidor; DatosTecnicos PPI desaprovechado; parciales
IOL descartados; secciones FCI/caución ignoradas.

Guards: identidad exacta, no overwrite PPI, conflicto fail-closed, timestamp
proveedor para dinámica, scraping last-resort, numeric positive, consumer map,
profile isolation, idempotencia de lifecycle y rutas reales vacías.

Validación local del commit de código:

- 2224 tests collected.
- 2220 passed / 4 skipped / 0 failed.
- 99 pruebas focales WS09/10/11: todas verdes.
- `git diff --check`: verde.
- PPI Watch diff contra base: 0 bytes.

## Incidentes y decisiones

- El clone fue sólo un workspace transitorio de lectura/edición; no creó otro
  repo en GitHub. La publicación se hizo con objetos GitHub (blob/tree/commit)
  en el repositorio canónico.
- El push git directo no tenía autenticación; se usó el conector GitHub
  autorizado, sin pedir terminal/SSH al usuario.
- Se detectó que exponer `READY_PAPER_CANDIDATE` como resultado del replay era
  ambiguo; se corrigió para atravesar el gate final y publicar capabilities.

## Ownership y seguridad

- Ownership final: `RELEASED` en el commit documental.
- PR permanece DRAFT.
- No se modificaron #357/#358, rama productiva, DB productiva, runtime, Docker,
  systemd, secretos ni PPI Watch.

```text
PAPER_SHADOW_ONLY=YES
REAL_ORDERS_SENT=0
REAL_ORDER_ROUTES_USED=NONE
PPI_WATCH=UNTOUCHED
PRODUCTIVE_DB_MUTATIONS=0
MERGE_TO_PRODUCTIVE=NO
DEPLOY=NO
```

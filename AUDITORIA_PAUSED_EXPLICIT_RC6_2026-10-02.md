# AUDITORÍA PAUSED_EXPLICIT RC6 — 2026-10-02

## Estado

**VALIDADO_RUNTIME — READ_ONLY**

- WORKSTREAM_ID: `WS-READINESS-PAUSED-EXPLICIT-AUDIT-20261002`
- branch: `audit/rc6-paused-explicit-20261002`
- base productiva auditada: `136453dc5b6d4146057142bcdd76b2f6b8d474e0`
- runtime DB: `/opt/porota-trading/data/paper_v17/observer_v17.db`
- acceso SQLite: `mode=ro` + `PRAGMA query_only=ON`
- deploy: NO
- mutación runtime: NO
- promoción readiness: NO
- PPI Watch: NO TOCADO
- órdenes reales: no se llamó ninguna ruta de orden

## Pregunta

Explicar por qué existen **4.066 `PAUSED_EXPLICIT`** en `candidate_identity_v2`, separar las causas y determinar qué requiere una corrección posterior versus qué debe seguir fail-closed.

## Verdad de runtime

`candidate_identity_v2` contiene 13.991 identidades:

| Estado | Cantidad |
|---|---:|
| AVAILABLE | 8.079 |
| PAUSED_EXPLICIT | **4.066** |
| OBSERVED_SHADOW | 1.838 |
| STALE | 8 |

`PAUSED_EXPLICIT` representa **29,06%** de `candidate_identity_v2`.

Los **4.066/4.066** tienen `can_simulate=0`.

## Semántica real del estado

En `bu_instrument_catalog.py`, `sync_candidate_universe()` calcula una lista de `identity_reasons()` por identidad completa.

Los motivos contemplados son:

1. `PPI_PRIMARY_IDENTITY_NOT_VERIFIED`;
2. identidad incompleta;
3. PPI identity last-known-good fuera del límite;
4. catálogo distinto de `AVAILABLE`;
5. capability que no comienza con `READY_PAPER_`;
6. retry de identidad ambigua.

Luego `explicit_projection()` hace una proyección especial para las familias residuales:

- OPCIONES -> `OPTION_CONTRACT_UNRESOLVED`;
- FUTUROS -> `FUTURES_CONTRACT_OR_PAPER_MARGIN_UNRESOLVED`;
- ON / OBLIGACIONES -> `ON_CONTRACT_UNRESOLVED` o `ON_IDENTITY_AMBIGUOUS`.

**Conclusión:** `PAUSED_EXPLICIT` es un estado fail-closed válido, pero semánticamente funciona como un **contenedor de causas heterogéneas**. No significa una única falla ni una pausa manual única.

## Distribución por familia

| Familia | PAUSED_EXPLICIT | % de los pausados |
|---|---:|---:|
| OPCIONES | 2.376 | 58,44% |
| OBLIGACIONES | 1.401 | 34,46% |
| ON | 91 | 2,24% |
| FUTUROS | 198 | 4,87% |

No se observaron `PAUSED_EXPLICIT` en Acciones, CEDEARs, Bonos, Letras, FCI, etc. en este corte.

## Distribución por capability exacta del catálogo

El cruce se revalidó por **full key exacta**:

`ticker + instrument_type + market + currency + settlement`

Resultado: **4.066/4.066 tienen match exacto en `financial_instrument_catalog`**. No se usó un cruce ambiguo por ticker para las conclusiones.

| Capability | Cantidad | % |
|---|---:|---:|
| MISSING_CURRENCY_OR_MARKET | 1.186 | 29,17% |
| CONTRACT_EVIDENCE_REVIEW_REQUIRED | 1.114 | 27,40% |
| NEEDS_NOMINAL_UNITS | 811 | 19,95% |
| NEEDS_OPTION_CONTRACT | 757 | 18,62% |
| NEEDS_FUTURES_MARGIN_AND_CONTRACT | 198 | 4,87% |

### Estado de catálogo de esos 4.066

| Estado catálogo | Cantidad | % |
|---|---:|---:|
| AVAILABLE | 1.578 | 38,81% |
| OBSERVED_SHADOW | 2.243 | 55,16% |
| STALE | 245 | 6,03% |

**Importante:** `catalog.status=AVAILABLE` no equivale a `RUNTIME_READY`. Es posible tener identidad de catálogo disponible y seguir fail-closed por contrato/capability/autoridad de identidad.

## Causas cruzadas de identidad

Los tokens del campo `detail` muestran:

- `PPI_PRIMARY_IDENTITY_NOT_VERIFIED`: **2.261**
- `IDENTITY_INCOMPLETE`: **1.186**
- `PPI_FRESHNESS_STALE`: **245**
- `RETRY_IDENTITY_AMBIGUOUS`: **182**

La policy de freshness de identidad PPI usada por este código es **14 días** (`PAPER_PPI_IDENTITY_LKG_SECONDS = 14 * 86400`), no un freshness de cotización intradiaria.

Los 245 stale tienen antigüedad:

- mínimo: 16,52 días;
- mediana: 16,52 días;
- máximo: 31,52 días.

Por familia: 52 Futuros, 91 ON, 102 Opciones.

## Hallazgo 1 — MISSING_CURRENCY_OR_MARKET = en realidad moneda faltante

**VALIDADO_RUNTIME**

Los **1.186** casos de `MISSING_CURRENCY_OR_MARKET` tienen:

- currency faltante/UNKNOWN: **1.186**
- market faltante: **0**
- settlement faltante: **0**

Distribución:

- OBLIGACIONES: 679
- OPCIONES: 507

Además, en **243** de esos 1.186 el `metadata_json` contiene algún valor no vacío bajo una clave `currency`, aunque la moneda canónica persistida quedó `UNKNOWN`. Ejemplo observado: valor complementario `EXT`.

**Interpretación:** hay un frente de normalización/complementación de moneda que debe auditarse. Esto NO autoriza a mapear `EXT` o cualquier valor automáticamente sin una regla/versionado de autoridad.

## Hallazgo 2 — CONTRACT_EVIDENCE_REVIEW_REQUIRED está dominado por Opciones

**VALIDADO_RUNTIME**

De los 1.114:

- OPCIONES: **1.112**
- OBLIGACIONES: **2**

Los 1.114 tienen `_contract_bridge.status=BLOCKED` con gap:

`CHANGE_REVIEW_REQUIRED`

En **1.112/1.114** existe además `financial_contract_v17` en metadata. Es decir: para la población masiva de Opciones no estamos ante “no hay contrato”; existe un contrato estructurado, pero el bridge lo invalida mientras el cambio no sea revisado.

### Correlación con `contract_evidence_v2_changes`

- paused review-required identities: 1.114
- identidades con `CHANGED_REVIEW_REQUIRED` correlacionado: **1.112**
- identidades sin change-row correlacionado: **2** -> **NO_VERIFICADO**
- change rows correlacionadas: **5.865**
- familia de las 5.865: **OPCIONES**
- source_class: **IOL_STRUCTURED_API** en 5.865/5.865
- detalle: `El hash contractual cambió; no promover ni ejecutar hasta revisión.`

Cantidad de change rows por identidad:

- 3 cambios: 2 identidades
- 5 cambios: 802
- 6 cambios: 307
- 7 cambios: 1

Edad del último cambio para las 1.112 Opciones:

- mínimo: 0,12 h
- mediana: **0,28 h**
- máximo: 20,04 h

### Estado causal

**NO_VERIFICADO**

La evidencia prueba que el hash contractual cambia repetidamente. Todavía NO prueba si:

1. IOL realmente cambia términos contractuales;
2. el objeto que entra al hash contiene un campo variable/no contractual;
3. el normalizador produce representaciones distintas de un mismo término;
4. existe otra causa.

El hash de Evidence v2 se calcula sobre `canonical_json(evidence)`. `observed_at` se almacena aparte y no entra automáticamente al hash, por lo que no se puede culpar al timestamp sin comparar `previous_hash/current_hash` y los `evidence_json`.

**Guard:** no auto-aprobar estas 1.112 opciones hasta hacer diff de evidencia contractual por campo.

## Hallazgo 3 — Opciones NEEDS_OPTION_CONTRACT

**VALIDADO_RUNTIME**

Total: **757**

- 209 AVAILABLE con bridge que declara faltantes contractuales.
- 102 STALE.
- 446 tienen metadata `units_per_lot` proveniente de IOL, pero eso por sí solo no completa el contrato de opción.

El gap-set estructurado observado en 209 identidades es:

- `cash_multiplier`
- `expires_at`
- `minimum_quantity`
- `option_right`
- `quantity_step`
- `strike`
- `underlying`

Estos campos —en especial expiry, strike, right, underlying y multiplier— son materialmente necesarios para simular una opción de forma financiera coherente. No corresponde promover por ausencia de ellos.

## Hallazgo 4 — Futuros

**VALIDADO_RUNTIME**

Total: **198**

- 146 catálogo AVAILABLE;
- 52 STALE.

En los 146 frescos/AVAILABLE:

- 134 faltan `cash_multiplier`, `expires_at`, `underlying`;
- 12 faltan además `minimum_quantity`, `quantity_step` y `paper_margin_policy`.

El sistema ya tiene evidencia de política PAPER conservadora para margin/quantity en parte de los registros, pero no alcanza para completar los campos contractuales faltantes.

No corresponde habilitar Futuros sin multiplier/expiry/underlying y una política de margen PAPER consistente.

## Hallazgo 5 — OBLIGACIONES / ON y nominal units

**VALIDADO_RUNTIME**

`NEEDS_NOMINAL_UNITS`: **811**

- OBLIGACIONES: 720
- ON: 91

Dentro de esa población, **25** registros ya contienen `units_per_lot` y `_iol_units_per_lot` explícitos en metadata.

Esto demuestra una oportunidad de RCA específica: para esos 25, verificar si el dato complementario está correctamente probado dimensionalmente y por qué no llegó a `financial_contract_v17` / `READY_PAPER_SPOT`.

No implica que los otros 786 puedan inventarse o inferirse.

### Ambigüedad

Hay **182** identidades con `RETRY_IDENTITY_AMBIGUOUS`:

- 91 OBLIGACIONES;
- 91 ON stale.

La regla de proyecto exige no elegir arbitrariamente entre identidades ambiguas. Deben permanecer fail-closed hasta resolver full identity.

## Fuentes de descubrimiento de los 4.066

- BYMA_PUBLIC_COMPLEMENTARY: 1.790
- PPI_PRIMARY: 1.558
- IOL_COMPLEMENTARY: 473
- NONE: 245

Settlement source:

- REQUEST_CANDIDATE: 1.803
- BYMA_PUBLIC_COMPLEMENTARY: 1.790
- IOL_COMPLEMENTARY: 473

Esto explica por qué gran parte de los PAUSED tiene datos complementarios pero no autoridad PPI primaria confirmada. Bajo la política actual, una fuente complementaria no debe sobrescribir identidad PPI.

## ¿Es injustificado que haya 4.066 PAUSED_EXPLICIT?

**Respuesta auditada: no se puede responder con un sí/no global.**

La población mezcla bloqueos de distinta calidad:

### Justificados fail-closed con evidencia actual

- moneda canónica UNKNOWN;
- identidad ambigua;
- identidad PPI stale >14 días bajo la policy vigente;
- opciones sin strike/right/expiry/underlying/multiplier;
- futuros sin multiplier/expiry/underlying;
- cambios contractuales pendientes de revisión mientras no se conozca el campo que cambió.

### Requieren RCA/fix-forward porque puede existir información suficiente que no está llegando al contrato

- 1.112 Opciones con `financial_contract_v17` presente pero bloqueadas por change-review repetitivo;
- 25 OBLIGACIONES con `units_per_lot` IOL explícito pero capability aún `NEEDS_NOMINAL_UNITS`;
- 243 identidades con alguna moneda complementaria en metadata pero canonical currency `UNKNOWN`.

Estos son candidatos de **investigación**, no de promoción automática.

## Defecto semántico confirmado

`PAUSED_EXPLICIT` está siendo utilizado como estado agregado de salida para múltiples reasons en OPCIONES/FUTUROS/ON/OBLIGACIONES.

Esto es funcionalmente fail-closed, pero dificulta observabilidad. Un dashboard que muestre sólo `PAUSED_EXPLICIT` sin `detail` puede inducir a pensar que todos los casos tienen la misma causa.

**PROPUESTO:** mantener el estado fail-closed, pero exponer `pause_code` / `pause_reasons` estructurados o una proyección equivalente sin parsear strings.

## Siguientes workstreams propuestos

### WS-PAUSED-01 — CHANGE_REVIEW hash RCA
READ_ONLY primero.

Para las 1.112 Opciones:
- comparar previous/current `evidence_json`;
- identificar campo exacto que cambia;
- separar cambio contractual real de drift representacional/volátil;
- definir review/acceptance idempotente y guard de regresión.

### WS-PAUSED-02 — Currency normalization
Revisar los 1.186 currency UNKNOWN:
- PPI primero;
- IOL/BYMA sólo complementan;
- inventariar valores complementarios como `EXT`;
- crear mapping sólo con evidencia y tests;
- no zero-fill ni asumir ARS/USD.

### WS-PAUSED-03 — ON nominal-unit bridge
Priorizar los 25 con `units_per_lot` explícito de IOL:
- validar dimensionalidad;
- comprobar price basis;
- generar `financial_contract_v17` sólo si economics quedan demostrados.

### WS-PAUSED-04 — Option contract completion
Para los 757 `NEEDS_OPTION_CONTRACT`:
- completar/validar expiry, strike, right, underlying, multiplier y quantity semantics;
- mantener opciones short bloqueadas si así lo exige la policy PAPER.

### WS-PAUSED-05 — Futures contract completion
Para los 198:
- refresh de los 52 stale;
- contract terms de los 146 disponibles;
- margin policy PAPER versionada;
- no real routes.

### WS-PAUSED-06 — identity ambiguity/staleness
- resolver 182 retry ambiguities por full key;
- revalidar 245 stale con PPI primario;
- nunca elegir “primera coincidencia”.

## Evidencia

### Matriz full-key
- Run: `37027360445` — SUCCESS
- Artifact: `11235234620`
- Digest: `sha256:c378c521a7d7b14f758c7a411d478f6efbc2d065943a36966c389bf92065d79c`
- Contiene matriz por instrumento de los 4.066 casos.

### Deep metadata/bridge
- Run: `37027851630` — SUCCESS
- Artifact: `11236375466`
- Digest: `sha256:78cb58d9154dc8a53ed4d4e4f5a53dfe1a9ea17136993eaadad656d9ba0e3934`

### Change review
- Run: `37028217039` — SUCCESS
- Artifact: `11236685991`
- Digest: `sha256:aeebafe587df6dd5ae43a28e86ced30610648bdf536d95d5ab675423be132003`

## Cierre

La auditoría queda **VALIDADO_RUNTIME** para conteos, full-key, reasons y metadata observada.

Permanece **NO_VERIFICADO**:
- si los 5.865 cambios de hash IOL son cambios contractuales reales o churn de representación/inputs;
- por qué las 2 OBLIGACIONES con `CONTRACT_EVIDENCE_REVIEW_REQUIRED` no tienen change-row correlacionado;
- si los 25 nominal-unit IOL cumplen todas las condiciones dimensionales para promoción PAPER.

No se realizó ninguna promoción, deploy, restart, escritura DB ni mutación de PPI Watch.

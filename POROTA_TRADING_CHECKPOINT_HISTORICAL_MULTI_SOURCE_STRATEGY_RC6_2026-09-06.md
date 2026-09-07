# POROTA TRADING — ESTRATEGIA CANÓNICA MULTI-SOURCE DE HISTÓRICOS RC6

**Fecha:** 2026-09-06  
**Release:** `17.0.0-rc6`  
**Estado:** P1 / DISEÑO CANÓNICO ANTES DE HABILITAR A3+IOL COMO WRITERS CANONICAL  
**Impacto sobre PAPER del 2026-09-07:** NO BLOCKER del hot path mientras A3/IOL sigan fuera de canonical multi-source  

## 1. Objetivo

Usar PPI, A3/BYMA e IOL para aumentar cobertura y calidad histórica **sin superponer, mezclar ni pisar datos de forma destructiva**.

Principio central:

> Las fuentes pueden coexistir para una misma identidad/fecha como evidencia versionada, pero sólo una observación correctamente reconciliada puede representar la serie canónica para un propósito determinado.

Nunca se deben promediar precios de distintas fuentes a ciegas ni reemplazar una barra sólo por orden de llegada.

## 2. Identidad financiera obligatoria

Toda barra se identifica por:

`symbol + instrument_type + market + settlement + date`

Antes de aceptar una barra:
- identidad completa;
- `market` verificado;
- `settlement` verificado;
- tipo de instrumento verificado;
- fecha de sesión válida;
- fuente/provenance explícitos.

Si el mapping es ambiguo: `HISTORY_IDENTITY_UNVERIFIED` / fail-closed.

## 3. Capas de almacenamiento

### Capa A — evidencia por fuente / append-only

Cada observación de PPI, A3/BYMA, IOL o cualquier otra fuente entra primero como versión independiente en `history_versions_v2`.

Nunca se borra una observación válida previa por recibir otra fuente. El `payload_hash`, `source`, `observed_at`, `adjusted` y metadata permiten auditar qué llegó y cuándo.

### Capa B — normalización y quality classification

Antes de canonical, clasificar como mínimo:
- `FULL_OHLCV`;
- `FULL_OHLC`;
- `CLOSE_ONLY`;
- `INVALID`;
- `IDENTITY_UNVERIFIED`;
- `SOURCE_DISCREPANCY` cuando corresponda.

`CLOSE_ONLY` no debe competir como si fuera una vela FULL_OHLCV.

### Capa C — reconciliación

Para cada identidad+fecha comparar candidatos válidos por:
1. misma identidad exacta;
2. misma base de precio/semántica (`RAW` vs `ADJUSTED`);
3. validez OHLC/volumen;
4. completitud/calidad;
5. autoridad/confianza de la fuente para ese campo/familia;
6. freshness/observed_at cuando aplique;
7. discrepancias contra otras fuentes.

### Capa D — canonical

Sólo después de las capas anteriores se actualiza la observación canonical correspondiente.

La selección debe registrar `winner_version_id`, fuente, motivo de selección, policy/version y discrepancias detectadas.

## 4. Rol de cada fuente

### PPI

**Rol:** fuente primaria de market data de POROTA y primera opción histórica cuando devuelve una barra válida para la identidad exacta.

Uso:
- primera fuente de histórico diario;
- backfill bounded;
- no relajar validadores para subir coverage;
- si PPI devuelve una barra inválida, conservar rechazo/provenance y buscar alternativa sin contaminar canonical.

### IOL

**Rol objetivo:** fuente secundaria de alta utilidad para FULL_OHLCV diario, backfill y cross-validation.

Evidencia read-only ya obtenida:
- GGAL;
- GD30;
- AAPL;
- prueba de GGAL con histórico desde enero de 2020.

Antes de canonical:
- demostrar mapping `term`/settlement;
- validar identidad por familia;
- medir coverage y gaps;
- comparar OHLCV contra PPI;
- detectar adjusted/unadjusted;
- budget de API calls;
- primero diag/shadow.

Cuando quede validado, IOL será fallback preferido para FULL_OHLCV faltante/invalidado por PPI.

### A3/BYMA

**Rol:** autoridad/contraste oficial de mercado cuando el contrato y la identidad estén determinísticamente verificados.

Estado actual:
- transporte disponible;
- alignment todavía no cerrado para todas las identidades.

Regla crítica:
- un dato A3 `CLOSE_ONLY` no debe desplazar una vela FULL_OHLCV válida de PPI o IOL;
- mientras el alignment sea ambiguo, no promover a canonical;
- conservar símbolo A3 original y mapping/provenance.

Si en el futuro A3/BYMA entrega una vela FULL_OHLCV con identidad/semántica verificadas, su autoridad puede evaluarse según el contrato específico de esa familia.

### Data912

**Rol:** `LOW_TRUST_SOURCE / LEGACY_FALLBACK / DIAGNOSTIC`.

Objetivo:
- no usarla como fuente preferente cuando PPI/IOL/A3 válidos existan;
- no permitir que gane discrepancias por simple precedencia;
- conservar provenance histórico;
- objetivo de policy: `DATA912_CANONICAL_ELIGIBLE=false` hasta nueva validación específica, salvo decisión explícita posterior.

## 5. Hallazgo P1 en el selector actual

El History Store v2 actual ya protege varias cosas correctamente:
- identidad completa;
- append-only versions;
- canonical separado;
- source rank;
- validadores fail-closed;
- duplicados por hash controlados.

Pero antes de multi-source canonical deben corregirse dos riesgos:

### 5.1 Completeness no participa explícitamente en `_prefer()`

Actualmente la selección se basa principalmente en `adjusted`, source rank y `observed_at`.

Riesgo concreto:
- A3 tiene prioridad/rank mayor que IOL;
- una fila A3 `CLOSE_ONLY` podría ocupar canonical;
- una vela IOL posterior `FULL_OHLCV` podría quedar protegida por precedencia y no reemplazarla.

Eso sería incorrecto para una serie canonical FULL_OHLCV.

### 5.2 `adjusted` no forma parte de la primary identity

Actualmente `adjusted` es atributo de la vela pero no parte de la primary key canonical. `_prefer()` da preferencia a `adjusted=True` sobre `False`.

Riesgo:
- RAW y ADJUSTED son semánticas distintas;
- no deben superponerse como si fueran el mismo producto de datos sin una policy explícita.

Corrección objetivo:
- separar `price_basis=RAW|ADJUSTED` en identidad lógica o en series canonical distintas;
- nunca reemplazar RAW por ADJUSTED sólo por llegada/rank.

## 6. Regla de selección propuesta

Para una serie `FULL_OHLCV RAW`:

1. descartar identidad no verificada;
2. descartar filas inválidas;
3. excluir `CLOSE_ONLY` de la competencia FULL_OHLCV;
4. comparar sólo `RAW` con `RAW`;
5. preferir PPI FULL_OHLCV válido;
6. si PPI falta/falla, preferir IOL FULL_OHLCV validado;
7. A3/BYMA FULL_OHLCV puede competir según contrato/autoridad cuando alignment y campos estén probados;
8. A3 close-only se conserva como referencia de cierre/cross-check, no como sustituto de una vela completa;
9. Data912 no promueve canonical mientras permanezca low-trust;
10. si PPI/IOL/A3 discrepan por encima de tolerancia y no hay una causa resoluble, registrar `SOURCE_DISCREPANCY` y no inventar/promediar.

La política exacta por familia puede diferir si el contrato de la fuente demuestra que un campo específico tiene distinta autoridad.

## 7. Scheduler / ingesta para evitar duplicación innecesaria

### PPI
- corre primero para targets faltantes/stale;
- bounded retry/backoff;
- no martillar series problemáticas.

### IOL
- inicialmente sólo canarios y matriz de coverage;
- luego backfill dirigido a gaps/filas PPI inválidas o periodos faltantes;
- no descargar indiscriminadamente todo en cada ciclo;
- controlar `api_calls_total` y budget.

### A3/BYMA
- usar postclose/catalog/alignment y validación de cierres;
- canonical sólo cuando identidad y calidad estén demostradas.

### Data912
- sacar del camino preferente automático;
- mantener como diagnóstico/provenance legacy hasta nueva evaluación.

## 8. Reconciliación de discrepancias

Ante dos o más candidatos para la misma identidad/fecha:

1. verificar que settlement sea idéntico;
2. verificar RAW/ADJUSTED;
3. verificar market/currency/family;
4. comparar timestamps/session;
5. comparar OHLCV campo por campo;
6. determinar si una fuente es stale o incompleta;
7. no promediar;
8. guardar todas las versiones;
9. elegir winner sólo si la policy lo permite;
10. si no se puede resolver, mantener discrepancia visible y evitar conclusiones dependientes del dato.

## 9. Pruebas obligatorias antes de habilitar multi-source canonical

- unit tests de quality/completeness precedence;
- test: A3 CLOSE_ONLY nunca desplaza IOL/PPI FULL_OHLCV;
- test: IOL FULL_OHLCV puede rellenar gap PPI válido como ausencia, no como overwrite destructivo;
- test: PPI válido conserva autoridad frente a IOL discrepante salvo policy explícita;
- test RAW vs ADJUSTED separados;
- test settlement mismatch fail-closed;
- test identity ambiguous fail-closed;
- test duplicate payload no crece versions;
- test discrepancy queda auditada;
- coverage antes/después por fuente y canonical;
- zero impact en observer/PAPER hot path.

## 10. Veredicto

La estrategia no será “sumar tres proveedores a la misma tabla y dejar que el último gane”.

Será:

`SOURCE RAW/VERSIONED → IDENTITY VALIDATION → QUALITY CLASSIFICATION → RECONCILIATION → CANONICAL WINNER`

PPI sigue primary. IOL se convierte en el candidato prioritario para completar FULL_OHLCV y reemplazar a Data912 como fallback preferente una vez validado. A3/BYMA se usa como autoridad/contraste de mercado con identidad determinística y sin permitir que datos close-only degraden una vela completa.

**No habilitar A3/IOL como writers canonical simultáneos hasta corregir completeness precedence y separar la semántica RAW/ADJUSTED.**
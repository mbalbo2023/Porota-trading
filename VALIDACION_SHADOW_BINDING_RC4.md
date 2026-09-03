# RC4 — VALIDACIÓN SHADOW → BINDING

Estado: **WIP / diseño de gobierno, sin mutación de runtime**.

## Objetivo

Crear una vista `/validacion` que responda en forma auditable:

1. qué control se está validando;
2. modo actual (`SHADOW`, `BINDING`, `NOT_READY`);
3. qué puede medir hoy y qué no puede observar en PRODUCTION_PAPER;
4. hito/semana actual;
5. fecha real de inicio de la ventana válida;
6. ruedas y días transcurridos / restantes;
7. criterios cumplidos, pendientes, fallidos o no observables;
8. counterfactual: qué habría bloqueado BINDING;
9. cambios de configuración que invalidan o parten la serie;
10. lección aprendida diaria, semanal y final;
11. decisión permitida hoy y por qué.

## Regla de inicio

La ventana NO comienza por calendario solamente. Las fechas 07/09→02/10 del documento original son una propuesta. El contador válido comienza en la **primera rueda completa posterior al deploy de la instrumentación requerida y con el gate en SHADOW**.

Si se modifica un parámetro que afecta las métricas de la campaña, el dashboard debe mostrar el corte de serie y, cuando corresponda, reiniciar el criterio afectado.

## Separación obligatoria de preguntas

- **Q1 PAPER — fidelidad contractual/matemática del modelo de costos:** verificable contra tarifario oficial versionado y tests.
- **Q1 REAL — fidelidad del costo efectivamente cobrado:** NO observable en PRODUCTION_PAPER sin fills reales/resumen de cuenta externo. No se inventa.
- **Q2 — fidelidad del modelo de ejecución PAPER:** slippage, profundidad, puntas y fills contrafácticos sobre evidencia registrada.
- **Q3 — ventaja estadística de la estrategia:** meses/out-of-sample; no se confunde con elegibilidad del portón.

El panel debe distinguir `PAPER_BINDING_ELIGIBLE` de cualquier futura elegibilidad para dinero real. Un BINDING PAPER no demuestra bonificación real ni edge.

## Economía — campaña principal

### Hito 0 — Instrumentación

Debe existir antes de contar la ventana:

- evaluación económica persistida en cada candidato;
- `would_block` persistido aunque el modo sea SHADOW;
- `authority` separado del veredicto;
- tarifario oficial con source/hash/effective_at/freshness;
- versión de parámetros económicos del candidato;
- book/mark utilizados y timestamps;
- razón estable de PASS/FAIL;
- reporte diario y semanal reproducible.

### Hito 1 — SHADOW observable

Criterios mínimos:

- 100% de evaluaciones económicas con veredicto persistido;
- cero aperturas bloqueadas por el gate económico mientras la campaña está SHADOW;
- counterfactual diario disponible;
- no hay claims de `reported_broker_cost` en PAPER;
- tarifario contractual vigente y no stale.

### Hito 2 — Fidelidad del modelo contractual

En PAPER se valida implementación, no cobro real:

- casos ACCIONES/CEDEAR/familias habilitadas contra tarifario oficial;
- bonificación modelada sólo bajo las condiciones contractuales que estén explícitamente documentadas;
- mínimos, IVA/derechos y vigencia versionados;
- conflictos de fuente => HOLD/SHADOW, nunca inferencia.

La evidencia real de si PPI aplicó efectivamente la bonificación se marca `NOT_OBSERVABLE_IN_PAPER` hasta disponer de fuente externa autorizada.

### Hito 3 — Fidelidad de ejecución

Medir sobre los mismos candidatos/fills PAPER:

- slippage modelado vs implícito;
- entrada ask / salida bid y book freshness;
- tamaño vs profundidad disponible;
- causa de salida;
- close-vs-entry;
- EOD share;
- counterfactual de parámetros alternativos sin cambiar el runtime.

No aceptar por default 8/10/15 pb: calibrar a partir de esta evidencia.

### Hito 4 — Consistencia y decisión PAPER

La campaña debe mostrar estabilidad entre semanas y ausencia de anomalías de instrumentación. Antes de habilitar BINDING PAPER debe generarse un informe de decisión firmado por release/config, nunca transición automática desde el dashboard.

## Breakeven correcto

No usar `stop/(stop+target)` cuando existen costos.

`p_be = net_loss / (net_gain + net_loss)`

Donde `net_gain` y `net_loss` incluyen aranceles modelados, spread/slippage y demás fricción aplicable. El panel debe mostrar la geometría bruta y la neta por separado.

## Contrafáctico SHADOW

Cada evaluación debe persistir como mínimo:

- `gate_key`;
- `evaluated_at`;
- `symbol/family/currency`;
- `mode`;
- `passed`;
- `would_block`;
- `execute_block`;
- `reason`;
- `parameter_version`;
- `evidence_version`.

En SHADOW: `execute_block=False` siempre, pero `would_block` conserva el veredicto.

El dashboard muestra:

- evaluadas;
- PASS;
- WOULD_BLOCK;
- porcentaje discriminado;
- operaciones abiertas que BINDING habría rechazado;
- P&L de ese subconjunto, por moneda sin mezclar ARS/USD;
- lección aprendida.

El rango 20–70% de rechazos se conserva como **heurística visual**, no como prueba universal de validez del gate.

## Expectancy, régimen y sector — campañas separadas

No heredan automáticamente la campaña económica.

### Expectancy

Prerequisitos:

- proveedor común de evidencia basado en posiciones cerradas;
- muestra mínima explícita;
- fuera de muestra / estabilidad;
- evaluación siempre persistida en SHADOW.

`empirical_expectancy()` pertenece a `ch_empirical_learning.py` y requiere posiciones. No se inventan métodos `store.empirical_samples()`.

### Régimen

`breadth_observation(rows)` requiere filas de mercado. Candidate1 ya construye esas filas en introspección. RC4 debe refactorizar la extracción a código compartido, con freshness y coverage.

### Sector

`sector_observation(positions)` sólo es confiable si las posiciones tienen sector explícito. RC4 no inferirá sector por ticker.

Readiness para BINDING exige:

- source/provenance;
- effective_at;
- cobertura suficiente del universo READY_PAPER;
- candidate sector conocido;
- política fail-closed ante mapping ambiguo/stale.

## UI `/validacion`

Orden propuesto:

1. **Decisión de hoy**: `NO ELEGIBLE`, `ELEGIBLE PARA BINDING PAPER`, `BINDING ACTIVO`.
2. **Campaña activa**: gate, modo, inicio válido, hito, progreso, ruedas faltantes.
3. **Criterios binarios**: objetivo, valor actual, target, estado, última evidencia, fuente.
4. **Counterfactual SHADOW**.
5. **Fidelidad de costos**.
6. **Fidelidad de ejecución**.
7. **Parámetros congelados y change log**.
8. **Lecciones aprendidas**: diaria, semanal, cierre de hito.
9. **Otras políticas**: Expectancy / Régimen / Sector, cada una con readiness independiente.
10. **Bloqueos para dinero real** claramente separados.

## Seguridad de gobierno

- El dashboard jamás cambia SHADOW/BINDING por sí mismo.
- La elegibilidad es evidencia, no una acción.
- Todo cambio de autoridad requiere commit/config versionada, tests, auditoría y autorización explícita.
- Falta de evidencia no se transforma en PASS.
- Datos stale/conflictivos se muestran como tales.
- PRODUCTION_PAPER y `real_orders=0` permanecen invariantes durante la campaña.

# POROTA TRADING — CHECKPOINT ACTIVE / READY 2026-09-14

**Estado:** ACTIVO / CANÓNICO PARA CONTINUIDAD
**Fecha de fijación:** 2026-09-13
**Objetivo operativo:** maximizar la cantidad de instrumentos seguros en `READY_PAPER` para la rueda del lunes 2026-09-14, sin relajar contratos, identidad, settlement, seguridad ni procedencia de datos.

## 0. REGLA DE CONTINUIDAD OBLIGATORIA

Este archivo es el punto de reanudación vigente. Si un chat/proceso se interrumpe, el siguiente DEBE leer este checkpoint antes de ejecutar nada y continuar desde `ÚLTIMO PASO CONFIRMADO` / `SIGUIENTE ACCIÓN EXACTA`.

NO reconstruir el trabajo desde checkpoints anteriores. NO repetir pruebas ya cerradas salvo nueva evidencia que lo justifique. Cada hito material debe actualizar este checkpoint con evidencia, semáforo, último paso confirmado y siguiente acción.

## 1. REPOSITORIO / RAMA

- Repo: `mbalbo2023/Porota-trading`
- Rama de trabajo canónica: `ops/rc6-ppi-web-residual-ready-20260913`
- HEAD observado antes de crear este checkpoint: `92cb2aa3d2f07d897734ae1420e2605df6e3c8b3`
- Universo canónico PPI: `1960` identidades.
- Modo obligatorio: `PRODUCTION_PAPER`.
- Órdenes reales permitidas durante este trabajo: `0`.

## 2. CAMBIO DE ALCANCE DECIDIDO POR EL USUARIO

### EXCLUIDOS DEL OBJETIVO READY PARA 2026-09-14

1. `FCI`
2. `OPCIONES`

La exclusión es **reversible**. No borrar código, contratos, histórico, catálogo ni datos de estas familias. No contabilizarlas como resueltas ni como `DONE_EMPTY`; simplemente quedan fuera del gate de preparación para la rueda del 2026-09-14.

### PRIORIDAD ACTUAL

1. Conseguir/completar contratos, settlements y demás metadatos obligatorios faltantes para TODAS las familias restantes.
2. Consolidar `PPI API + PPI Web` sobre una única identidad canónica y una política explícita de precedencia/procedencia.
3. Clasificar solamente los errores de la corrida Web que correspondan a familias NO excluidas y que puedan bloquear instrumentos útiles.
4. Medir `READY_PAPER` real por familia e instrumento.
5. Mantener los 18 producers/timers pausados hasta validación integral y reactivación controlada.

## 3. RESULTADO FINAL DE LA CORRIDA PPI WEB RESIDUAL

Run: `PPI-WEB-RESIDUAL-20260913-001`
Servicio: `porota-ppi-web-residual-rc6.service`
Root durable: `/opt/porota-ingest/ppi-web-residual`

Resultado final aceptado para este checkpoint:

- TOTAL: `642/642`
- ERROR: `494`
- DONE_PARTIAL: `117`
- DONE_EMPTY: `31`
- DONE_VALID: `0`
- PENDING: `0`
- RUNNING: `0`

Último snapshot intermedio previamente verificado en runtime: cuando quedaban `418` pendientes, `410` eran `OPCIONES` y `8` eran `ON`; había `78` ERROR. Luego el total final de ERROR pasó a `494` (+416). Por lo tanto, **por inferencia y no por clasificación individual todavía**, al menos `404/410` OPCIONES terminaron en ERROR. No usar esa inferencia para clasificar los 494 errores de manera automática.

### Semáforo del diagnóstico Web

- 🟢 corrida Web: finalizada 642/642
- 🟢 proceso durable / state preservado
- 🟢 single-writer/browser serialization observada durante la ejecución
- 🟢 API historical writer: debía permanecer inactivo durante Web
- 🟢 seguridad última verificada: `ok|PRODUCTION_PAPER|0|0|1960`
- 🔴 OPCIONES: falla masiva; fuera del objetivo 2026-09-14
- 🟡 errores NO-OPCIONES: falta clasificación exacta
- 🟡 DONE_PARTIAL/DONE_EMPTY: requieren reconciliación con API antes de decidir cobertura final

## 4. FCI

- FCI previamente diferidos: `37`.
- Decisión actual: excluidos del objetivo READY 2026-09-14.
- No tratarlos como resueltos, `DONE_EMPTY` ni error reparado.

## 5. REGLA DE CONSOLIDACIÓN PPI API + WEB

La consolidación debe ser por identidad canónica, no por concatenación de datasets.

Precedencia inicial fijada:

1. `PPI API` es fuente primaria para catálogo, contratos/metadatos e histórico cuando el dato está presente, válido y consistente.
2. `PPI Web autenticada` es complemento/fallback para campos o cobertura faltante de API.
3. Un dato Web de menor calidad NO puede sobrescribir silenciosamente un dato API válido.
4. Cada valor consolidado debe conservar `source/provenance` suficiente para auditar de dónde salió.
5. No fuzzy matching silencioso. Alias explícito existente: `MRCTO -> MRCAC`.
6. No datos sintéticos.
7. No segundo historical writer concurrente.

## 6. SETTLEMENT / PLAZOS — REGLA VIGENTE

- BYMA estándar desde 2024-05-27: `T+1 / 24HS`.
- `CI`: `T+0`.
- `48HS`: legado; no asumirlo como settlement operativo por defecto.
- Usar `PlazosOperables` reales por instrumento.

## 7. DEFINICIÓN DE READY_PAPER — NO CONFUNDIR CON HISTÓRICO DESCARGADO

Un instrumento NO queda READY sólo porque tenga histórico.

Para declararlo `READY_PAPER` se debe verificar, como mínimo, según aplique a la familia:

- identidad canónica inequívoca;
- familia/mercado correcto;
- símbolo/ticker operativo correcto;
- moneda;
- settlement/plazo operativo real;
- tamaño mínimo / step / lote cuando aplique;
- price tick / price step cuando aplique;
- multiplicador / valor nominal cuando aplique;
- reglas contractuales necesarias para sizing y órdenes;
- estado/instrumento operable;
- histórico suficiente para el motor cuando sea requerido;
- procedencia de los campos críticos;
- ningún gate crítico desconocido inventado por default.

Los campos exactos y gates existentes en código deben auditarse antes de agregar una lógica paralela.

## 8. REGLAS DE SEGURIDAD / NO HACER

- NO enviar órdenes reales.
- NO salir de `PRODUCTION_PAPER`.
- NO reintentar ciegamente los 494 errores Web.
- NO reiniciar la corrida completa PPI API.
- NO regenerar manifiestos históricos congelados como atajo.
- NO borrar `state.sqlite3`.
- NO resetear tasks a ciegas.
- NO iniciar IOL antes de reconciliar API+Web y calcular el residual verdadero, salvo decisión explícita posterior.
- NO borrar ni deshabilitar permanentemente FCI/OPCIONES; sólo excluirlas del target actual.
- NO reactivar los 18 producers/timers todavía.

## 9. SEMÁFORO GLOBAL ACTUALIZADO

- 🟢 Alcance para mañana: definido
- 🟢 FCI: decisión tomada, excluido reversiblemente
- 🟢 OPCIONES: decisión tomada, excluido reversiblemente
- 🟢 PPI Web residual: corrida finalizada
- 🟢 Safety PAPER / real orders=0: última comprobación verde
- 🟢 Clasificador visual `/instrumentos`: trazado hasta su fuente real
- 🟢 Acciones: `55/55` observadas aparecen `READY_PAPER` en el catálogo/runtime mostrado
- 🟢 CEDEAR: `191/191` observadas aparecen `READY_PAPER` en el catálogo/runtime mostrado
- 🟡 Consolidación PPI API + Web: la infraestructura v2 ya existe; falta poblarla/reconciliarla con la evidencia actual y usarla como gate estricto
- 🟡 Contratos/metadatos de familias restantes: inventario de campos faltantes localizado en código; falta medición runtime exacta por instrumento
- 🟡 Errores Web no-OPCIONES: clasificación pendiente
- 🟡 READY_PAPER mostrado en dashboard: es evidencia contractual/capability de catálogo, NO autorización end-to-end por sí sola
- ⛔ IOL residual: no iniciar todavía
- ⛔ 18 producers/timers: mantener pausados

## 10. ÚLTIMO PASO CONFIRMADO

Se trazó la pantalla `/instrumentos` hasta la lógica exacta que genera el estado mostrado. El flujo es `bg_paper_dashboard.instruments_page -> _family_ux_table -> _family_ux_snapshot -> catalog_family_coverage.ready_paper_count`. El badge de familia se muestra `READY_PAPER` cuando `ready_paper_count > 0`; la columna `READY PAPER` contiene el conteo exacto.

`catalog_family_coverage.ready_paper_count` se calcula en `bu_instrument_catalog.persist_family_coverage` contando registros cuya `capability == READY_PAPER_SPOT`. En la evidencia visual aportada por el usuario: Acciones `55/55` y CEDEAR `191/191` aparecen READY en ese catálogo/runtime.

También se confirmó que existe un gate contractual v2 más estricto (`cp_contract_evidence_v2_hf6.py` + `cq_contract_readiness_hf6.py`) que evalúa identidad, campos obligatorios por familia, conflictos entre fuentes, freshness, costo y simulador. Su máximo automático es `READY_PAPER_CANDIDATE`; `auto_activation_allowed=False`. Por lo tanto, NO crear un tercer clasificador: consolidar el catálogo existente con este gate v2 y el gate final de integración.

El propio dashboard advierte que la cobertura/compatibilidad PAPER del catálogo no acredita permisos ni implica ejecución real; faltan los demás portones. Esta diferencia queda fijada como regla para no confundir `READY_PAPER_SPOT` de catálogo con READY end-to-end.

## 11. SIGUIENTE ACCIÓN EXACTA

1. Auditar las fuentes contractuales ya implementadas para `BONOS`, `LETRAS`, `ON`, `CAUCIONES`, `FUTUROS`, `ETF` y `ACCIONES_USA` (FCI/OPCIONES fuera del target inmediato).
2. Medir en runtime, read-only, la matriz exacta por familia e instrumento: observados, `READY_PAPER_SPOT`, evidencia v1/v2, campos obligatorios faltantes, conflictos, stale, costo/simulador y `READY_PAPER_CANDIDATE`.
3. Reutilizar `Contract Evidence v2` como capa canónica de reconciliación API + Web, respetando precedencia/procedencia y fail-closed ante contradicciones.
4. Implementar sólo los adaptadores/capturas mínimas faltantes que destraben instrumentos seguros para 2026-09-14.
5. Mantener `PRODUCTION_PAPER`, órdenes reales `0`, histórico API/Web sin nuevo writer y producers/timers pausados hasta el gate integral.

## 12. HALLAZGO CANÓNICO — SEMÁNTICA DEL DASHBOARD `/instrumentos`

### Capa visual/catalogal existente

- `READY_PAPER` visible en la tabla NO es inventado ni decorativo.
- La pantalla lee `catalog_family_coverage` y expone `ready_paper_count`.
- Ese conteo proviene de `READY_PAPER_SPOT` en `financial_instrument_catalog`.
- La etiqueta de familia pasa a `READY_PAPER` con al menos un registro compatible; por eso para evaluar cobertura completa se debe usar el conteo `READY PAPER`, no sólo el badge.
- Acciones/CEDEAR BYMA pueden obtener contrato spot reconocido mediante la política explícita del catálogo; no extrapolar esa convención a renta fija, derivados, cauciones o FCI.

### Capa contractual v2 existente

`cq_contract_readiness_hf6.py` define campos obligatorios por familia y falla cerrado ante faltantes, stale o conflicto. `cp_contract_evidence_v2_hf6.py` persiste evidencia versionada por identidad `(familia,ticker,market,settlement)`, conserva procedencia por fuente, detecta contradicciones y nunca auto-activa una familia.

La estrategia canónica desde este punto es **reconciliar y completar estas capas existentes**, no crear una clasificación paralela.

---

**Regla para cualquier chat siguiente:** si este archivo existe, NO volver al handoff PPI History anterior como punto de ejecución; usarlo sólo como antecedente. El punto de trabajo vigente es ESTE checkpoint y su sección `SIGUIENTE ACCIÓN EXACTA`.
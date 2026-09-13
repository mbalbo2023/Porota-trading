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

## 9. SEMÁFORO GLOBAL AL CREAR ESTE CHECKPOINT

- 🟢 Alcance para mañana: definido
- 🟢 FCI: decisión tomada, excluido reversiblemente
- 🟢 OPCIONES: decisión tomada, excluido reversiblemente
- 🟢 PPI Web residual: corrida finalizada
- 🟢 Safety PAPER / real orders=0: última comprobación verde
- 🟡 Consolidación PPI API + Web: por implementar/auditar
- 🟡 Contratos/metadatos de familias restantes: inventario pendiente inmediato
- 🟡 Errores Web no-OPCIONES: clasificación pendiente
- 🔴 READY_PAPER por familia: todavía NO demostrado
- ⛔ IOL residual: no iniciar todavía
- ⛔ 18 producers/timers: mantener pausados

## 10. ÚLTIMO PASO CONFIRMADO

Se fijó el nuevo alcance (FCI + OPCIONES fuera de objetivo) y se comenzó la inspección de la rama canónica para localizar la lógica existente de catálogo, contratos, settlement y `READY_PAPER`. No se ha modificado todavía esa lógica ni se han hecho retries históricos.

## 11. SIGUIENTE ACCIÓN EXACTA

**Auditoría read-only del código actual de la rama canónica para localizar y mapear:**

1. modelo/catálogo de instrumentos;
2. fuente PPI API de contratos/metadatos;
3. fuente PPI Web equivalente/complementaria;
4. reglas de settlement/plazos;
5. gates/validadores que determinan operabilidad o `READY_PAPER`;
6. familias NO-FCI/NO-OPCIONES presentes y campos críticos faltantes.

Después de ese inventario, producir una matriz por familia `READY / BLOQUEADO / FALTA CAMPO`, y recién entonces implementar cambios mínimos sobre la lógica existente.

---

**Regla para cualquier chat siguiente:** si este archivo existe, NO volver al handoff PPI History anterior como punto de ejecución; usarlo sólo como antecedente. El punto de trabajo vigente es ESTE checkpoint y su sección `SIGUIENTE ACCIÓN EXACTA`.
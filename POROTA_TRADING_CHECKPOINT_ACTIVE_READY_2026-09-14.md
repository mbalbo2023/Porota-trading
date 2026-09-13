# POROTA TRADING — CHECKPOINT ACTIVE / READY 2026-09-14

**Estado:** ACTIVO / CANÓNICO PARA CONTINUIDAD
**Fecha de fijación:** 2026-09-13
**Última actualización material:** 2026-09-13 14:46 UTC aprox.
**Objetivo operativo:** maximizar la cantidad de instrumentos seguros en `READY_PAPER` para la rueda del lunes 2026-09-14, sin relajar contratos, identidad, settlement, calendarios, seguridad ni procedencia de datos.

## 0. REGLA DE CONTINUIDAD OBLIGATORIA

Este archivo es el punto de reanudación vigente. Si un chat/proceso se interrumpe, el siguiente DEBE leer este checkpoint antes de ejecutar nada y continuar desde `ÚLTIMO PASO CONFIRMADO` / `SIGUIENTE ACCIÓN EXACTA`.

NO reconstruir el trabajo desde checkpoints anteriores. NO repetir pruebas ya cerradas salvo nueva evidencia que lo justifique. Cada hito material debe actualizar este checkpoint con evidencia, semáforo, último paso confirmado y siguiente acción.

## 1. REPOSITORIO / RAMA / HEAD

- Repo: `mbalbo2023/Porota-trading`
- Rama de trabajo canónica: `ops/rc6-ppi-web-residual-ready-20260913`
- HEAD canónico observado tras las últimas regresiones: `a71d6f57ffabbd548bdb9a9833d0026667690ec2`
- Commit: `test: fail closed on contract identity-key disagreement`
- Universo canónico PPI: `1960` identidades históricas del run masivo.
- `candidate_universe` actual observado: `911` filas.
- Modo obligatorio: `PRODUCTION_PAPER`.
- Órdenes reales permitidas durante este trabajo: `0`.

## 2. CAMBIO DE ALCANCE DECIDIDO POR EL USUARIO

### EXCLUIDOS DEL OBJETIVO READY PARA 2026-09-14

1. `FCI`
2. `OPCIONES`

La exclusión es **reversible**. No borrar código, contratos, histórico, catálogo ni datos de estas familias. No contabilizarlas como resueltas ni como `DONE_EMPTY`; simplemente quedan fuera del gate de preparación para la rueda del 2026-09-14.

### PRIORIDAD ACTUAL

1. Conservar y validar end-to-end los instrumentos hoy simulables.
2. Completar contratos, settlements y metadatos obligatorios de las familias restantes, priorizando `BONOS`, `LETRAS` y `ON`, luego `CAUCIONES` y `FUTUROS`.
3. Consolidar `PPI API + PPI Web/XHR` sobre una única identidad canónica y una política explícita de precedencia/procedencia.
4. Tratar histórico y contratos como dos ingestas distintas: NO repetir histórico masivo para resolver faltantes contractuales.
5. Mantener los 18 producers/timers pausados hasta validación integral y reactivación controlada.

## 3. LÓGICA CANÓNICA DE INGESTA — ACLARACIÓN IMPORTANTE

Hay dos flujos distintos y no deben confundirse:

### A. HISTÓRICO DE PRECIOS / VELAS

Objetivo: alimentar al motor de decisión con series históricas.

Estado:
- corrida masiva PPI API: cerrada; NO repetir completa;
- corrida PPI Web residual: cerrada; NO repetir completa;
- reconciliar lo ya descargado entre stores;
- cualquier nueva adquisición histórica debe ser residual, dirigida y justificada.

### B. CONTRATOS / METADATOS OPERATIVOS

Objetivo: saber cómo se puede operar correctamente un instrumento.

Incluye, según familia: ticker/identidad, mercado, settlement real, moneda, mínimo/step/lote, precisión/tick, nominal, lámina mínima, ISIN, vencimiento, cupón, amortización, relación CEDEAR, multiplicador de futuros, márgenes, reglas de caución, costos y demás términos necesarios para sizing/órdenes.

Precedencia:
1. `PPI API` estructurada cuando el dato está presente y validado.
2. `PPI Web/XHR autenticada` como complemento/fallback por campo.
3. `IOL` sólo residual final después de reconciliar PPI, salvo decisión explícita.

Un histórico completo NO vuelve READY a un instrumento si su contrato está incompleto.

## 4. RESULTADO FINAL DE LA CORRIDA PPI WEB RESIDUAL

Run: `PPI-WEB-RESIDUAL-20260913-001`
Servicio: `porota-ppi-web-residual-rc6.service`
Root durable: `/opt/porota-ingest/ppi-web-residual`

Resultado final:
- TOTAL: `642/642`
- ERROR: `494`
- DONE_PARTIAL: `117`
- DONE_EMPTY: `31`
- DONE_VALID: `0`
- PENDING: `0`
- RUNNING: `0`

Clasificación exacta de errores Web NO-OPCIONES ya obtenida:
- `ON`: 42
- `LICITACIONES`: 18
- `FUTUROS`: 9
- `CAUCIONES`: 8
- `LETRAS`: 6
- `LEBACS`: 1
- Total NO-OPCIONES exacto: `84`

`BONOS` no tuvo ERROR Web en ese residual; sus 5 residuales quedaron `DONE_PARTIAL` y aportaron 1015 filas válidas de 1220 recibidas.

### Semáforo Web

- 🟢 corrida finalizada 642/642
- 🟢 state durable preservado
- 🟢 single-writer histórico respetado
- 🟢 API historical writer inactivo
- 🔴 OPCIONES: fuera del objetivo inmediato
- 🟡 errores NO-OPCIONES: ya clasificados; tratar sólo los útiles para READY
- 🟡 PARTIAL/EMPTY: reconciliar con API antes de declarar faltante real

## 5. SAFETY / WRITERS — ÚLTIMO SNAPSHOT VERIFICADO

Workflow paralelo: `RC6 READY parallel RCA 2026-09-13`
Run exitoso: `34763546320`
Jobs: `6/6 success`.

Estado runtime leído en modo read-only:
- `porota-ppi-web-residual-rc6.service`: `inactive/dead`, `Result=success`, `MainPID=0`
- `porota-ppi-fullfamily-history-rc6.service`: `inactive/dead`, `Result=success`, `MainPID=0`
- procesos writer históricos detectados: `0`
- observer DB `quick_check=ok`
- `candidate_universe_rows=911`
- `can_simulate=1 AND status=AVAILABLE`: `246`
  - `ACCIONES`: `55`
  - `CEDEARS`: `191`

No cambiar `PRODUCTION_PAPER`. No habilitar órdenes reales.

## 6. HISTÓRICO DE LOS 246 HOY SIMULABLES

Reconciliación read-only sobre todos los stores históricos:
- total simulables: `246`
- con evidencia histórica en al menos un store: `239/246` = `97,15%`
- faltantes reales en todos los stores examinados: `7`

Por familia:
- `ACCIONES`: `55/55` cubiertas
- `CEDEARS`: `184/191` cubiertos

Residual exacto de 7 CEDEAR:
- `CRWVC`
- `CVSC`
- `MRVLC`
- `VIVTC`
- `VIVTD`
- `VRTXC`
- `VXXC`

Regla: NO rerun masivo. Primero RCA de estos 7 usando tasks/attempt ledgers/catálogo; sólo después, si son faltantes verdaderos y vigentes, adquisición histórica dirigida.

## 7. RCA CERRADO DE 4 ON CON HTTP 200 PERO 0 FILAS VÁLIDAS

Instrumentos:
- `MRCGC`: provider 180, dropped_old 180, rows_365d 0
- `MRCGD`: provider 339, dropped_old 339, rows_365d 0
- `MRCLC`: provider 279, dropped_old 279, rows_365d 0
- `MRCPO`: provider 480, dropped_old 480, rows_365d 0

Conclusión: PPI sí devolvió histórico, pero todas las filas estaban fuera de la ventana de 365 días. No es un error de transporte ni justifica retry ciego.

## 8. CONTRACT EVIDENCE V2 — ESTADO

Tablas runtime presentes:
- `contract_evidence_v2_current`
- `contract_evidence_v2_snapshots`
- `contract_evidence_v2_changes`

Identidades con evidencia v2 observadas: `447`.

Fuentes existentes incluyen `PPI_AUTHENTICATED_XHR` y `PPI_AUTHENTICATED_WEB`.

La matriz de diagnóstico raw produjo `complete_execution=0/447`, PERO **NO interpretar ese 0 como resultado contractual final**, porque ese job inspeccionó `evidence_json` crudo y siguió marcando `ticker`, `market` y `settlement` como ausentes aunque forman parte de la identidad canónica del registro. El evaluador fue corregido después para reutilizar identidad unánime y fallar cerrado si existe discrepancia.

### Corrección ya aplicada

Se corrigió Contract Evidence v2 para:
- no tratar metadatos de recolección como conflictos financieros;
- reutilizar `ticker/market/settlement` de la identidad canónica cuando son unánimes;
- marcar `CONFLICT` si fuentes/registro discrepan en cualquiera de esos campos;
- no promover automáticamente ningún instrumento.

Regresiones: `7/7 PASS`.

Tests confirmados:
- diferencias sólo de metadata no son conflicto financiero;
- desacuerdo de settlement en record key falla cerrado;
- payload vs record key discrepante falla cerrado;
- identidad unánime se inyecta correctamente;
- metadata no puede ocultar un conflicto contractual real;
- diferencias financieras reales siguen siendo conflicto;
- mismo valor financiero desde múltiples fuentes no es conflicto.

## 9. DATOS TÉCNICOS / COLECTOR CONTRACTUAL

Se inspeccionaron `2695` archivos bajo `/opt/porota-ingest` y `/opt/porota-trading` buscando referencias operativas a `DatosTecnicos`.

Resultado:
- `match_count=1`
- única referencia: `/opt/porota-trading/PATCH_HF6_CONTRACT_EVIDENCE_V2_WIP.md`
- path mencionado: `/DatosTecnicos`

Conclusión: **no hay hoy un colector operativo vivo de `DatosTecnicos` demostrado**. No asumir que existe. Hay que leer la especificación WIP y construir/adaptar un colector mínimo, autenticado y read-only si la ruta/semántica queda confirmada.

No iniciar navegador masivo ni scraping general para esto.

## 10. REGLA DE CONSOLIDACIÓN PPI API + WEB

La consolidación debe ser por identidad canónica y por campo, no por concatenación de datasets.

1. `PPI API` prima cuando hay dato válido y consistente.
2. `PPI Web/XHR` complementa campos faltantes.
3. Una fuente de menor calidad no sobrescribe silenciosamente una de mayor calidad.
4. Conservar `source/provenance`, observación y conflictos.
5. No fuzzy matching silencioso.
6. Alias explícito vigente: `MRCTO -> MRCAC`.
7. No datos sintéticos.
8. No segundo historical writer concurrente.
9. Conflictos críticos => fail-closed.

## 11. SETTLEMENT / PLAZOS

- BYMA estándar desde 2024-05-27: `T+1 / 24HS`.
- `CI`: `T+0`.
- `48HS`: legado; no asumirlo por default.
- Usar `PlazosOperables` reales por instrumento.
- No fuzzy matching de plazo.

## 12. READY_PAPER — DEFINICIÓN Y CAPAS

### Dashboard / catálogo

Flujo localizado:
`bg_paper_dashboard.instruments_page -> _family_ux_table -> _family_ux_snapshot -> catalog_family_coverage.ready_paper_count`

`ready_paper_count` cuenta instrumentos cuyo catálogo tiene `capability == READY_PAPER_SPOT`.

Observado:
- Acciones: `55/55`
- CEDEAR: `191/191`

### Gate contractual v2

`cp_contract_evidence_v2_hf6.py` + `cq_contract_readiness_hf6.py` evalúan evidencia, identidad, requisitos de familia, conflictos, freshness y otras condiciones. Máximo automático: `READY_PAPER_CANDIDATE`; `auto_activation_allowed=False`.

### Gate end-to-end

`READY_PAPER_SPOT` visual NO equivale a READY integral. Para READY end-to-end se requiere, según familia:
- identidad canónica;
- contrato obligatorio completo;
- settlement real;
- históricos suficientes cuando el motor los requiere;
- calendario/sesión correspondiente;
- costos/riesgo/sizing;
- estado operable;
- procedencia de campos críticos;
- ningún conflicto crítico ni default inventado.

## 13. FCI / OPCIONES

- FCI diferidos conocidos: `37`.
- FCI y OPCIONES permanecen excluidos reversiblemente del target 2026-09-14.
- No borrar ni marcar como resueltos.

## 14. REGLAS DE SEGURIDAD / NO HACER

- NO enviar órdenes reales.
- NO salir de `PRODUCTION_PAPER`.
- NO reintentar a ciegas los 494 errores Web.
- NO reiniciar la corrida completa PPI API.
- NO reiniciar la corrida Web completa.
- NO regenerar manifiestos históricos congelados como atajo.
- NO borrar `state.sqlite3`.
- NO resetear tasks a ciegas.
- NO iniciar IOL antes de reconciliar PPI API + Web y calcular residual verdadero, salvo decisión explícita.
- NO deshabilitar permanentemente FCI/OPCIONES.
- NO reactivar los 18 producers/timers todavía.
- NO promover instrumentos READY por simple presencia de histórico.

## 15. SEMÁFORO GLOBAL ACTUALIZADO

- 🟢 checkpoint de continuidad: activo/canónico
- 🟢 scope 2026-09-14: definido
- 🟢 FCI/OPCIONES: exclusión reversible decidida
- 🟢 PPI API histórico masivo: cerrado
- 🟢 PPI Web residual: cerrado 642/642
- 🟢 writers históricos: inactivos
- 🟢 observer DB: quick_check ok
- 🟢 simulables actuales: 246 = 55 Acciones + 191 CEDEAR
- 🟢 histórico Acciones: 55/55
- 🟢 histórico CEDEAR: 184/191
- 🟡 7 CEDEAR: RCA residual pendiente
- 🟢 Contract Evidence v2: 447 identidades con evidencia
- 🟢 fix de conflictos/identidad: 7/7 tests PASS
- 🟡 matriz contractual post-fix: pendiente de recalcular con el evaluador corregido
- 🟡 Bonos/Letras/ON: prioridad contractual alta
- 🟡 Cauciones/Futuros: contratos especializados pendientes
- 🔴 colector `DatosTecnicos`: no existe operativo; adaptar/implementar mínimo read-only
- ⛔ IOL: no iniciar todavía
- ⛔ 18 producers/timers: mantener pausados
- 🔴 READY end-to-end integral: todavía NO demostrado

## 16. ÚLTIMO PASO CONFIRMADO

El último lote paralelo `RC6 READY parallel RCA 2026-09-13` terminó `6/6 success` sobre HEAD `a71d6f57ffabbd548bdb9a9833d0026667690ec2`.

Se confirmó:
1. safety/read-only verde y writers históricos detenidos;
2. 246 instrumentos actualmente simulables;
3. 239/246 tienen histórico en al menos un store;
4. sólo 7 CEDEAR carecen de histórico localizado;
5. los 4 ON investigados no requieren retry ciego: sus filas son antiguas y quedan fuera de 365 días;
6. Contract Evidence v2 contiene 447 identidades;
7. la matriz raw 0/447 estaba afectada por la forma de diagnóstico de identidad y debe recalcularse usando el evaluador corregido;
8. el fix de identidad/conflictos tiene 7/7 regresiones verdes;
9. no existe un colector operativo demostrado de `DatosTecnicos`.

No se iniciaron writers históricos, no se reactivaron producers/timers y no se enviaron órdenes reales.

## 17. SIGUIENTE ACCIÓN EXACTA

Ejecutar en paralelo, sin compartir writers:

### CARRIL A — MATRIZ CONTRACTUAL POST-FIX
1. Evaluar las `447` identidades con el evaluador real corregido, no con merge raw ad-hoc.
2. Obtener por familia/instrumento: `READY_PAPER_CANDIDATE`, `HOLD/BLOCKED`, campos faltantes, conflictos, stale, costos/simulador.
3. Separar faltantes reales de falsos faltantes ya resueltos por identidad canónica.

### CARRIL B — 7 CEDEAR SIN HISTÓRICO LOCALIZADO
1. Inspeccionar `history_attempt_ledger_v2`, `production_history_attempts`, tasks PPI y aliases/identidad para esos 7.
2. Determinar si son símbolos activos/válidos, aliases, provider empty, inválidos, o verdaderamente no ingeridos.
3. Sólo si el faltante es real: adquisición dirigida de esos 7, nunca rerun masivo.

### CARRIL C — COLECTOR CONTRACTUAL MÍNIMO
1. Leer `PATCH_HF6_CONTRACT_EVIDENCE_V2_WIP.md` y código de importadores existentes.
2. Confirmar la ruta/forma de `DatosTecnicos` sin inventar endpoint ni parámetros.
3. Implementar/adaptar sólo el colector read-only mínimo necesario para completar campos críticos de `BONOS`, `LETRAS` y `ON`.
4. Preservar provenance y fail-closed; no auto-activar.

### CARRIL D — QUICK WINS DE READY
1. Con la matriz post-fix, priorizar instrumentos que sólo tengan 1-2 campos críticos faltantes.
2. No bloquear por metadata puramente accesoria.
3. No relajar contrato, settlement, calendarios, costos o riesgo.

### CARRIL E — CHECKPOINT
Después de cada hito material, actualizar este archivo con:
- evidencia;
- semáforo;
- `ÚLTIMO PASO CONFIRMADO`;
- `SIGUIENTE ACCIÓN EXACTA`.

---

**Regla para cualquier chat siguiente:** este archivo es el único punto canónico de reanudación. Los checkpoints anteriores sirven sólo como antecedente. No recomenzar la ingesta histórica masiva ni volver a diagnosticar temas ya cerrados sin nueva evidencia.
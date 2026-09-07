# POROTA TRADING — CHECKPOINT DASHBOARD CLASSIC TABLES RC6 — 2026-09-07

## Estado

**P1 DASHBOARD CLASSIC TABLES: CANDIDATE_GREEN / NOT_DEPLOYED**

Este checkpoint documenta exclusivamente una corrección de presentación del dashboard RC6. No modifica observer, estrategia, gates, órdenes, históricos, DB writers, calendarios ni broker connectivity.

## Base exacta

Dashboard live de referencia al iniciar el parche:

- branch: `ux/rc6-dashboard-final-20260906`
- SHA: `da2c87936d90cda17512de3bc529d13f4693c1c1`
- live branch verificada sin cambios luego de preparar el candidato.

Observer frozen continúa fuera de alcance:

- SHA: `db26c76723bb988c956589c572b87cbcb4191731`
- `PRODUCTION_PAPER`
- real orders capability: `BLOCKED`
- `real_orders_sent=0` continúa como invariante absoluto.

## Rama candidata

`ux/rc6-dashboard-classic-tables-20260907`

Creada desde el SHA live exacto del dashboard.

Head validado:

`3f0591e4d6a39c88207d042e83bc21b590f6ed51`

## Defecto corregido

La implementación anterior podía transformar tablas a cards/stacked records automáticamente por presión de ancho, cantidad de columnas u overflow.

La política RC6 objetivo queda fijada como:

`CLASSIC_ROWS_COLUMNS`

Regla dura:

**Una tabla permanece una tabla en todos los viewports.**

No existe conversión automática a cards por:

- cantidad de columnas;
- ancho disponible;
- ancho por columna;
- overflow.

## Archivos de presentación modificados

### `ev_dashboard_table_accessibility_rc6.py`
Nueva capa RC6 de accesibilidad/presentación:

- preserva `<table>/<thead>/<tbody>/<tr>/<th>/<td>`;
- agrega `scope="col"` a encabezados cuando falta;
- agrega nombre accesible a la tabla;
- tablas anchas usan un contenedor local con `overflow-x:auto`;
- el overflow queda contenido por tabla, no global al dashboard;
- `role="region"` y `tabindex="0"` en el contenedor local;
- densidad visual menor en tablet/móvil sin cambiar semántica;
- conserva paginación progresiva de 20 registros con `Mostrar más` / `Mostrar menos`;
- no usa `data-porota-compact`;
- no usa labels por celda para simular cards.

### `eq_dashboard_table_layout_rc6.py`
Reemplaza en runtime la capa responsive/card previa por la nueva capa classic-table, antes del render operator-facing.

Mantiene sin cambios los instaladores RC6 existentes de:

- go-live UX;
- blocking semantics;
- runtime truth fixes.

### `er_dashboard_table_semantics_rc6.py`
La función de compatibilidad `should_force_compact(...)` ahora devuelve siempre `False`.

Agrega política explícita:

`CLASSIC_TABLE_POLICY = "CLASSIC_ROWS_COLUMNS"`

Las tablas de 6+ columnas reciben un ancho mínimo legible dentro del scroll container local, acotado para evitar expansión global.

### `tests/test_dashboard_table_layout_rc6.py`
Actualizado para bloquear regresiones hacia cards/stacked layout.

## CI validation-only

Workflow aislado:

`.github/workflows/rc6-dashboard-classic-tables-validate-20260907.yml`

El workflow:

- no contiene SSH;
- no contiene `DO_HOST` ni `DO_SSH_KEY`;
- no tiene capacidad de deploy;
- build de candidato dentro de GitHub Actions;
- tests dentro de contenedor `--network none --read-only`;
- compila los módulos cambiados en memoria para mantener filesystem read-only.

### Primera ejecución

Run `34120241391`:

- Docker build: GREEN;
- presentation tests: GREEN, 24 tests;
- compile step: falló únicamente porque `py_compile` intentó crear `__pycache__` sobre filesystem read-only;
- no fue un fallo del código ni de tests.

El workflow fue corregido para compilar en memoria sin escritura.

### Ejecución final

Run `34120451462`:

- Build dashboard candidate: GREEN;
- Classic table presentation tests: GREEN;
- 24 tests: GREEN;
- Compile changed presentation modules in memory: GREEN;
- Assert no deployment capability: GREEN;
- Job final: SUCCESS.

## Diff vs dashboard live

El candidato parte exactamente de `da2c879...` y contiene únicamente cambios de presentación/tests/validation workflow.

No hay cambios deliberados en:

- observer;
- `bv_paper_runtime.py`;
- scanner/decision engine;
- PPI guard;
- execution/order path;
- DB schemas/writers;
- historical ingestion;
- risk gates;
- market calendars;
- CEDEAR Labor Day policy;
- Telegram;
- Contract Evidence.

## Criterio de aceptación visual pendiente

La validación automatizada demuestra contrato estructural y ausencia de regresión de código, pero la aceptación final en dispositivo debe verificar:

1. tablas reales de filas/columnas en Samsung Android;
2. encabezados legibles;
3. Voice Access puede navegar/identificar controles;
4. tablas anchas desplazan sólo su propio contenedor;
5. no existe scroll horizontal global de página;
6. `Mostrar más` / `Mostrar menos` sigue operativo;
7. no aparecen cards/stacked records;
8. `/vivo`, `/validacion` y demás páginas mantienen verdad operacional.

Este field test es P1 de UX/accesibilidad, no hard gate P0 por sí solo, salvo pérdida real de observabilidad crítica.

## Deployment state

**NOT_DEPLOYED.**

La rama live del dashboard continúa en:

`da2c87936d90cda17512de3bc529d13f4693c1c1`

No se movió el ref live y no se ejecutó workflow de deployment.

## Semáforo

- Código del parche: 🟢 GREEN
- Build candidato: 🟢 GREEN
- Tests automatizados: 🟢 GREEN
- Compile/read-only validation: 🟢 GREEN
- Scope isolation dashboard-only: 🟢 GREEN
- Live deployment: ⚪ NOT_EXECUTED
- Samsung/Voice Access field acceptance: 🟡 PENDING
- Observer/runtime impact: 🟢 NONE
- Real money: ⚪ BLOCKED / NO-GO

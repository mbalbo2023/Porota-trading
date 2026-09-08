# POROTA TRADING — CHECKPOINT RC6
## Dashboard UX / navegación / aprendizaje / SHADOW→BINDING

**Fecha:** 2026-09-08  
**Rama:** `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`  
**Tipo de trabajo:** auditoría documental/código solamente.  
**Implementación realizada:** NO.  
**Deploy/runtime modificado:** NO.  
**DB modificada:** NO.  
**PPI/browser/order routes:** NO TOCADOS.  

---

## 1. Pedido funcional agregado por el operador

Se incorpora como pendiente formal de RC6 un frente específico de experiencia de uso del dashboard:

1. En las páginas con muchas tablas/secciones no existe una referencia clara de cuánto contenido resta hacia abajo ni una forma cómoda de saltar entre bloques.
2. Se propone un **submenú horizontal por página**, visible y accesible, que enumere las secciones/tablas principales y permita saltar directamente a cada una mediante anchors.
3. Debe evitarse depender de scroll largo para descubrir contenido.
4. Deben eliminarse inconsistencias visuales heredadas: páginas viejas, shells legacy y controles duplicados como dos acciones de “Actualizar”.
5. La página **Aprendizaje** debe organizar la información cronológicamente:
   - detalle por día;
   - consolidado semanal;
   - consolidado mensual;
   - cada nivel debe permitir entender qué lecciones se aprendieron, sobre qué operaciones/instrumentos/familias y con qué resultado.
6. Deben revisarse los hitos de **vinculación / SHADOW→BINDING** para identificar qué componentes ya existen estructuralmente y cuáles todavía no tienen evidencia runtime suficiente.
7. Todo cambio futuro debe conservar accesibilidad para Samsung/Android + Voice Access.

---

## 2. Hallazgos verificados en el repositorio

### UX-01 — Ya existe un patrón reutilizable de submenú horizontal, pero sólo está generalizado parcialmente

`da_dashboard_ux_hf6.py` define:

- navegación superior canónica;
- `TRADING_NAV`;
- `trading_nav_html()` con clase CSS `subnav`;
- grupos de familias para Trading.

Por lo tanto, el patrón técnico de submenú horizontal **ya existe** y no hace falta inventar una arquitectura nueva.

**Estado estructural:** `GREEN_CODE_PRESENT`  
**Estado funcional global:** `YELLOW_NOT_GENERALIZED`

Pendiente: convertir este patrón en un componente reutilizable por página, con anchors de sección para `En vivo`, `Trading`, `Scalping`, `Validación`, `Instrumentos`, `Históricos`, `Aprendizaje`, `Reportes` y `Sistema` cuando exista contenido suficientemente largo.

---

### UX-02 — La duplicación de controles “Actualizar” tiene explicación concreta en código

El documento canónico de `bg_paper_dashboard.py` agrega siempre un control global:

- `🔄 Actualizar página`

A su vez, páginas internas como la vista En Vivo agregan su propia acción:

- `Actualizar ahora`

La función `_dedupe_refresh()` elimina meta-refresh y textos de próxima actualización, pero **no elimina enlaces/botones adicionales de actualización**.

Resultado: la duplicación observada por el operador es coherente con la arquitectura actual y no debe tratarse como un problema subjetivo de CSS.

**Estado:** `YELLOW_CONFIRMED_CODE_PATH`

Pendiente futuro:

- una única política de refresh por documento;
- ownership único del control visible;
- preservar auto-refresh parcial donde corresponda sin duplicar acciones manuales;
- validar foco/Voice Access después de refresh.

---

### UX-03 — Coexisten renderizadores legacy y canónicos dentro de la misma aplicación

`o_dashboard.py` conserva varias rutas HTML históricas (`/vivo`, `/salud`, `/historicos`, `/aprendizaje`, `/testing`, etc.).

Después instala `bg_paper_dashboard`, que mediante middleware:

- reemplaza rutas conocidas por páginas nuevas;
- canonicaliza HTML de otras rutas;
- elimina parte de navegación/shell legacy.

Más tarde se instalan además:

- `ep_dashboard_truth_layer_rc6`;
- `eq_dashboard_table_layout_rc6`;
- `en_validation_project_dashboard_rc6`.

Esto significa que existe **doble ownership visual**: rutas antiguas continúan físicamente definidas y una capa posterior intenta sustituir/canonicalizar la respuesta.

No se afirma todavía que cada página vieja observada en runtime sea causada por este mecanismo; eso requiere HTTP/runtime validation posterior. Pero la fragilidad arquitectónica sí queda probada por código.

**Estado:** `YELLOW_DUAL_RENDERER_ARCHITECTURE`

Pendiente futuro:

1. inventario route-by-route de owner real;
2. marcar cada ruta como `CANONICAL`, `LEGACY_WRAPPED`, `REDIRECT_ONLY` o `DEAD`;
3. consolidar ownership visual;
4. mantener redirects/bookmarks sólo donde sea necesario;
5. no eliminar rutas antiguas hasta probar equivalencia funcional.

---

### UX-04 — La adaptación de tablas para Samsung/Voice Access ya está implementada estructuralmente

`eq_dashboard_table_layout_rc6.py` implementa compactación basada en **ancho real del contenedor**, no sólo viewport:

- fuerza formato compacto con muchas columnas;
- fuerza formato compacto si cada columna queda demasiado angosta;
- detecta overflow real;
- reevalúa ante resize/mutaciones del DOM.

`o_dashboard.py` instala explícitamente `eq_dashboard_table_layout_rc6.install()` después de la capa PAPER.

Existe test dedicado `tests/test_dashboard_table_layout_rc6.py` que cubre:

- muchas columnas;
- columna/contenedor angosto;
- tabla pequeña legible;
- overflow real.

**Hito estructural:** `GREEN_CODE_WIRED_AND_TESTED_AT_UNIT_LEVEL`  
**Validación runtime/tablet:** `NOT_REVALIDATED_IN_THIS_CHECKPOINT`

No convertir este hallazgo en “GREEN operativo” hasta volver a comprobar el dashboard vivo en tablet/Voice Access.

---

## 3. Aprendizaje — estado actual vs pedido

### LEARN-01 — La página canónica actual no implementa todavía día → semana → mes

La página actual de aprendizaje en `bg_paper_dashboard.py` muestra principalmente:

- estado event-driven;
- cantidad de muestras cerradas;
- último cierre PAPER;
- última etiqueta;
- win rate;
- resultado por moneda;
- expectativa empírica por moneda sobre últimas operaciones cerradas;
- una tabla plana de cierres/etiquetas.

No se observó en esta página una jerarquía de navegación/consolidación:

- diaria;
- semanal;
- mensual.

**Estado:** `RED_REQUIREMENT_NOT_IMPLEMENTED`

### LEARN-02 — Ya existe en `/validacion` un patrón de historial por día reutilizable

`en_validation_project_dashboard_rc6.py` contiene `_daily_history()`:

- agrupa registros por `date_ar`;
- ordena días;
- usa `<details>` por jornada;
- permite filtros de últimos 10/30 días o todo.

Esto proporciona una base visual ya probada a nivel de código para reutilizar en Aprendizaje.

**Estado:** `GREEN_REUSABLE_PATTERN_EXISTS`

### Diseño objetivo registrado para Aprendizaje

Futuro diseño propuesto, sin implementar en este checkpoint:

**Nivel 1 — Día**
- fecha;
- operaciones cerradas;
- familia/instrumento;
- estrategia;
- PnL neto;
- motivo de entrada/salida;
- gate técnico/económico/patrimonial;
- lección de cada operación;
- lecciones nuevas del día;
- errores/false positives/false negatives relevantes.

**Nivel 2 — Semana**
- consolidación de jornadas;
- cantidad de operaciones;
- win rate sólo si muestralmente interpretable;
- PnL por moneda/familia/estrategia;
- lecciones repetidas;
- lecciones contradictorias;
- cambios de régimen/cohorte;
- qué hipótesis ganaron o perdieron evidencia.

**Nivel 3 — Mes**
- resumen acumulado;
- estabilidad de lecciones;
- comparación con semanas previas;
- lecciones persistentes vs transitorias;
- cambios candidatos a investigación;
- explícitamente NO auto-promover parámetros por una agregación mensual.

Cada nivel debe tener submenú/anchors y ser usable sin scroll exploratorio largo.

---

## 4. SHADOW → BINDING / “vinculación” — auditoría estructural

### BIND-01 — Contrato de etapas existe

`es_shadow_binding_contract_rc6.py` define explícitamente el camino:

`COLLECTING_EVIDENCE → SHADOW → SHADOW_VALIDATION → EVIDENCE_SUFFICIENT → ELIGIBLE_FOR_BINDING_DECISION → BINDING_PAPER → BINDING_VALIDATION → BINDING_PAPER_PROVEN → REAL_MONEY_GOVERNANCE → CANARY_REAL_MONEY`

También separa:

- **learning policies** experimentales;
- **hard safety blocks** obligatorios.

Las políticas declaradas son:

- `ECONOMIC_GATE`;
- `EXPECTANCY`;
- `MARKET_REGIME`;
- `SECTOR_CONCENTRATION`.

**Estado estructural:** `GREEN_CONTRACT_DEFINED`

---

### BIND-02 — Auto-promoción está explícitamente prohibida

El contrato fija:

- `automatic_promotion=False`;
- `real_money_authorized=False`;
- promoción a BINDING PAPER sólo elegible si existen simultáneamente:
  - autorización explícita del operador;
  - release versionado;
  - evidencia suficiente;
  - tests GREEN.

`tests/test_shadow_binding_contract_rc6.py` valida estos invariantes.

**Estado estructural:** `GREEN_GOVERNANCE_GUARD_PRESENT`

Esto NO autoriza todavía ninguna promoción.

---

### BIND-03 — Collector contrafactual read-only existe

`et_shadow_learning_rc6.py`:

- abre SQLite en `mode=ro`;
- activa `PRAGMA query_only=ON`;
- lee `trade_gate_evaluations` y `paper_positions`;
- calcula por política:
  - would-block;
  - would-allow;
  - pérdidas evitables;
  - ganancias que un bloqueo habría eliminado;
  - pérdidas permitidas;
  - outcome pendiente;
  - PnL PAPER observado;
  - PnL contrafactual si el gate hubiese sido BINDING;
  - sesiones con evidencia.

No escribe DB ni habilita real-money.

`tests/test_shadow_learning_rc6.py` verifica incluso que los bytes de la DB queden idénticos antes/después del collector en el fixture.

**Estado estructural:** `GREEN_READONLY_COLLECTOR_PRESENT`

---

### BIND-04 — Vista de validación SHADOW→BINDING existe

`ev_shadow_validation_view_rc6.py` muestra en `/validacion`, para cada política:

- etapa;
- autoridad actual;
- ruedas con evidencia;
- métricas contrafactuales;
- precisión de bloqueo;
- PnL observado vs contrafactual;
- siguiente hito.

`en_validation_project_dashboard_rc6.py` incorpora esa vista dentro del tablero Camino a Producción.

**Estado estructural:** `GREEN_VISUALIZATION_PRESENT`

---

### BIND-05 — No corresponde marcar todavía “BINDING alcanzado” sólo por estructura

Aunque el contrato, collector, métricas y vista existen, `et_shadow_learning_rc6.py` devuelve actualmente por diseño:

- `eligible_for_binding_decision=False`;
- objetivo observacional aproximado `~20 ruedas / 4 semanas`;
- nota explícita de que tiempo transcurrido NO es promoción automática.

Por lo tanto, los siguientes estados **NO quedan aprobados** por este checkpoint:

- `EVIDENCE_SUFFICIENT`;
- `ELIGIBLE_FOR_BINDING_DECISION`;
- `BINDING_PAPER`;
- `BINDING_VALIDATION`;
- `BINDING_PAPER_PROVEN`.

Para avanzar cualquiera de ellos hace falta evidencia real de campaña, no sólo código.

**Estado:** `YELLOW_STRUCTURE_READY_EVIDENCE_PENDING`

---

## 5. Relación con hitos M0–M11 del Camino a Producción

`em_validation_campaign_rc6.py` define M0–M11.

Este checkpoint permite reconocer **progreso estructural**, pero no cambiar semáforos runtime sin evidencia:

### M7 — Operabilidad y accesibilidad

Criterios: tablet, Voice Access, Telegram, dashboard, scheduler host truth.

Hallazgos de este checkpoint:

- compactación de tablas por ancho real: PRESENTE y cableada;
- tests unitarios de layout: PRESENTES;
- navegación superior canónica: PRESENTE;
- submenú horizontal reutilizable: PRESENTE sólo en Trading;
- navegación de página completa: PENDIENTE;
- limpieza de páginas legacy/refresh duplicado: PENDIENTE;
- validación final tablet/Voice Access: PENDIENTE.

**Conclusión M7 en este checkpoint:** no cambiar a GREEN; sí registrar que varios prerrequisitos estructurales ya existen.

### M8 — Campaña PAPER sostenida

La arquitectura de validación ya conserva historial append-only por día y lecciones por registro. La página Aprendizaje, sin embargo, todavía no ofrece el agregado diario/semanal/mensual pedido.

**Conclusión M8:** estructura parcial disponible; evidencia de campaña y UX de consolidación aún pendientes.

### M9 — Auditoría independiente y consenso

La arquitectura de checkpoints y ledger existe, pero siguen P0/P1 abiertos y no corresponde marcar consenso.

### M10/M11

Sin cambios: governance real-money y real-money continúan BLOCKED.

---

## 6. Backlog UX añadido

### Prioridad P1 — Dashboard UX / navegación

- `UX-P1-001`: componente genérico de submenú horizontal por página.
- `UX-P1-002`: anchors estables por tabla/sección.
- `UX-P1-003`: indicador claro de contenido/secciones disponibles sin necesidad de scroll exploratorio.
- `UX-P1-004`: ownership único de refresh; eliminar duplicados “Actualizar”.
- `UX-P1-005`: inventario route-by-route de renderer owner.
- `UX-P1-006`: retirar visual legacy sólo después de equivalencia funcional.
- `UX-P1-007`: revalidar tablet Samsung + Voice Access después de cambios.

### Prioridad P1 — Aprendizaje

- `LEARN-P1-001`: vista diaria.
- `LEARN-P1-002`: consolidado semanal.
- `LEARN-P1-003`: consolidado mensual.
- `LEARN-P1-004`: vínculo explícito lección ↔ operación ↔ instrumento/familia ↔ resultado.
- `LEARN-P1-005`: diferenciar observación descriptiva de recomendación y de cambio autorizado.
- `LEARN-P1-006`: reutilizar patrón `<details>`/historial de `/validacion` cuando sea conveniente.

### Prioridad P1/P2 — SHADOW→BINDING

- `BIND-P1-001`: verificar runtime que collector/vista consumen evidencia actual correcta.
- `BIND-P1-002`: medir cobertura por política, familia, instrumento y régimen.
- `BIND-P1-003`: validar false positives/false negatives y outcomes pendientes.
- `BIND-P1-004`: definir evidencia suficiente por política sin usar sólo “N ruedas”.
- `BIND-P1-005`: registrar formalmente la transición `EVIDENCE_SUFFICIENT` únicamente con evidencia.
- `BIND-P1-006`: cualquier promoción a `BINDING_PAPER` requiere autorización explícita + tests + release versionado.

---

## 7. Semáforo de este frente

| Frente | Estado | Motivo |
|---|---|---|
| Navegación superior canónica | 🟢 Código | Existe |
| Submenú horizontal genérico por página | 🔴 Pendiente | Sólo patrón parcial en Trading |
| Anchors/secciones para evitar scroll exploratorio | 🔴 Pendiente | No generalizado |
| Tabla adaptable Samsung/Voice Access | 🟢 Estructural | Cableada + test unitario |
| Validación runtime tablet posterior | 🟡 Pendiente | No realizada en este checkpoint |
| Ownership único de rutas/render | 🟡 | Conviven legacy + canonical middleware |
| Control refresh único | 🟡 | Duplicación posible confirmada por código |
| Aprendizaje por día | 🔴 | No implementado en página actual |
| Aprendizaje semanal | 🔴 | No implementado |
| Aprendizaje mensual | 🔴 | No implementado |
| Patrón de historial diario reutilizable | 🟢 Código | Existe en `/validacion` |
| Contrato SHADOW→BINDING | 🟢 Código | Definido y testeado |
| Collector contrafactual read-only | 🟢 Código | Existe y tiene test de no-mutación |
| Vista SHADOW→BINDING | 🟢 Código | Existe en Validación |
| EVIDENCE_SUFFICIENT | 🟡/NO PROBADO | Requiere campaña real |
| BINDING_PAPER | 🔴 NO AUTORIZADO | No promover por estructura |
| Real-money | 🔴 BLOCKED | Sin cambios |

---

## 8. Reglas de continuidad

1. Este checkpoint **no autoriza implementación inmediata**.
2. No desplegar cambios UX hasta retomarlo explícitamente.
3. No modificar runtime ni DB para “marcar cumplido” un hito.
4. Distinguir siempre:
   - código presente;
   - wiring presente;
   - test unitario presente;
   - runtime verificado;
   - evidencia de campaña suficiente.
5. Nunca usar existencia de UI como prueba de comportamiento operativo.
6. Nunca promover SHADOW→BINDING automáticamente.
7. `real_orders_sent=0` y bloqueo de real-money siguen siendo invariantes absolutos.

---

## 9. Próximo paso cuando se retome

Sin ejecutar cambios todavía:

1. auditar mapa completo de rutas del dashboard y su owner real;
2. inventariar secciones/tablas de cada página;
3. diseñar subnav horizontal común;
4. diseñar agregado Aprendizaje día/semana/mes;
5. validar qué partes de M7/M8 y SHADOW→BINDING pueden pasar de “estructura presente” a “evidencia runtime demostrada”;
6. recién después preparar implementación versionada y pruebas.

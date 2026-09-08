# POROTA TRADING — CHECKPOINT PPI DOM / API OPERABILITY / ALERTING

Fecha: 2026-09-08
Estado: PENDIENTE CANÓNICO DE RC6
Rama: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

## 1. Contexto confirmado

Durante la validación autenticada de PPI Web se confirmó:

- autenticación PPI Cuenta: GREEN;
- SSO hacia Trading: GREEN;
- sesión autenticada trusted-device: GREEN;
- `real_orders_sent=0` en todas las pruebas;
- observer: `ok|PRODUCTION_PAPER|0` antes y después;
- política browser read-only estabilizada:
  - GET/HEAD/OPTIONS permitidos;
  - mutaciones de terceros abortadas silenciosamente;
  - `/api/logger` y `zendesk-session` de PPI abortados;
  - cualquier otra mutación first-party PPI => registrada + FAIL-CLOSED;
- Contract Evidence runtime: `GREEN_NOT_DUE` cuando la ventana/cadencia no corresponde;
- el mecanismo GET/XHR histórico quedó obsoleto para las páginas STATIC: las páginas cargan autenticadas pero los endpoints contractuales esperados ya no aparecen;
- el DOM autenticado sí contiene tablas útiles y estructuradas para familias como Acciones, CEDEARs, Bonos, Letras, ON y FCI;
- WebSocket `realtime-hw.portfoliopersonal.com/socket.io/` existe, pero en la prueba STATIC observada aportó poco volumen relativo frente al contenido ya renderizado en DOM;
- se inició Full DOM Sweep de 16 familias de cotizaciones con cobertura/paginación en modo read-only.

## 2. PENDIENTE CRÍTICO #1 — Matriz de operabilidad real por API

### Pregunta que debe quedar respondida antes de considerar READY una familia

**Que una familia exista en PPI Web y pueda relevarse por DOM NO implica que sea operable a través de la API que usa POROTA.**

Esto es crítico porque POROTA ejecutará órdenes mediante API, no mediante browser scraping.

### Regla canónica

Para cada familia/instrumento visible en PPI Web se debe construir una matriz explícita de capacidad API:

| Campo | Requerido |
|---|---|
| Familia PPI Web | Sí |
| Disponible para Market Data API | Sí/No/Parcial |
| Disponible para búsqueda de instrumento API | Sí/No/Parcial |
| Disponible para histórico API | Sí/No/Parcial |
| Disponible para crear orden API | Sí/No/Parcial |
| Disponible para cancelar/modificar orden API | Sí/No/Parcial |
| Settlement/plazo soportado | Lista explícita |
| Moneda/mercado soportado | Lista explícita |
| Campos contractuales suficientes | Sí/No |
| Evidencia empírica | endpoint/método/respuesta sanitizada |
| Estado | GREEN/YELLOW/RED |

### Familias mínimas a evaluar

- Acciones
- CEDEARs
- Bonos
- Letras
- ON
- Cauciones
- Opciones
- Futuros
- ETF
- FCI
- FCI Exterior
- Acciones USA / Exterior
- Licitaciones
- Índices
- Monedas
- Tasas
- cualquier otra familia descubierta por PPI Web

### Regla de gating

Una familia NO debe marcarse `READY_PAPER` ni mucho menos `READY_PROD` por el solo hecho de que:

- aparezca en PPI Web;
- tenga datos en DOM;
- tenga cotización;
- pueda ser scrapeada.

Debe existir evidencia separada de que la API de broker utilizada por POROTA soporta de forma correcta y completa la operatoria requerida.

Si la API no soporta una familia:

- puede mantenerse como evidencia/mercado/observación;
- puede alimentar análisis o contexto si la semántica es válida;
- NO debe generar órdenes;
- debe mostrarse explícitamente como `NON_EXECUTABLE_VIA_API` o equivalente.

## 3. PENDIENTE CRÍTICO #2 — Semáforo visible en Dashboard > Scraping

Si el DOM/Contract Evidence autenticado queda integrado al sistema, debe existir visibilidad operativa directa en el dashboard.

### Ubicación objetivo

Dashboard -> submenú `Scraping` / `Contract Evidence`.

### Semáforo mínimo por fuente/familia

#### GREEN

- autenticación vigente;
- captura reciente dentro del SLA;
- cobertura suficiente;
- campos requeridos presentes;
- sin cambios de esquema incompatibles;
- no hay gaps críticos;
- fuente reconciliada con API cuando corresponda.

#### YELLOW

- fuente usable pero degradada;
- parcialidad conocida;
- cobertura incompleta no crítica;
- refresh atrasado pero aún dentro de tolerancia operativa;
- cambio menor de DOM/schema;
- discrepancia no material con otra fuente;
- fallback disponible.

#### RED

- autenticación perdida;
- fuente inaccesible;
- captura demasiado vieja para la operatoria;
- campos contractuales críticos ausentes;
- cambio de DOM/schema que rompe extracción;
- discrepancia material sin resolver;
- no existe fallback válido;
- datos potencialmente peligrosos para toma de decisión;
- API operativa de la familia no soportada cuando el motor pretende ejecutarla.

### Datos mínimos que debe mostrar cada tarjeta/fila

- fuente;
- familia;
- estado GREEN/YELLOW/RED;
- última captura exitosa;
- edad del dato;
- SLA/TTL esperado;
- cobertura observada / esperada;
- cantidad de instrumentos;
- campos críticos faltantes;
- último error;
- fallback activo;
- estado de reconciliación;
- `API_EXECUTABLE = YES/NO/PARTIAL`;
- impacto operativo;
- enlace/drill-down al detalle.

## 4. PENDIENTE CRÍTICO #3 — Alerting por staleness / pérdida de información con impacto operativo

No alcanza con mostrar el semáforo en dashboard. El sistema debe alertar automáticamente cuando la pérdida o antigüedad de la información pueda afectar la operatoria o la toma de decisiones.

### Principio

La severidad no depende solo de que una fuente falle, sino de:

1. qué dato se perdió;
2. cuánto tiempo lleva stale;
3. si existe fallback;
4. si el dato participa en una decisión actual;
5. si la familia está habilitada para operar por API;
6. si el sistema puede demostrar que la decisión sigue siendo segura con evidencia alternativa.

### Ejemplos de severidad

#### Informativo

- una captura falla una vez;
- existe API principal sana;
- el dato no participa en una decisión activa;
- siguiente refresh esperado pronto.

#### YELLOW accionable

- varias capturas consecutivas fallan;
- dato más viejo que su TTL normal;
- cobertura parcial creciente;
- DOM/schema cambió;
- reconciliación con API pendiente;
- todavía existe evidencia suficiente para no bloquear.

#### RED / bloqueo de familia

- dato contractual crítico excede el `max_staleness` permitido;
- no existe fuente alternativa válida;
- el dato es necesario para sizing, precio, settlement, fee, vencimiento, ratio, tick, step, elegibilidad o riesgo;
- API y scraping discrepan materialmente y no se puede resolver;
- el motor no puede demostrar que la decisión es segura.

### Comportamiento requerido ante RED material

- FAIL-CLOSED solo sobre la familia/instrumento afectado, no detener innecesariamente toda POROTA;
- bloquear nuevas órdenes de esa familia;
- mantener market data, históricos, backfill, observer, dashboard e introspección activos;
- Telegram/alerta técnica con causa, fuente, último dato válido, edad, fallback y acción tomada;
- registrar evento en introspección/SRE;
- evitar spam mediante deduplicación, escalamiento y recuperación explícita.

### Regla adicional

El sistema debe distinguir entre:

- `STALE_BUT_USABLE`
- `STALE_NOT_SAFE_FOR_DECISION`
- `SOURCE_DOWN_FALLBACK_OK`
- `SOURCE_DOWN_NO_FALLBACK`
- `SCHEMA_DRIFT`
- `COVERAGE_GAP`
- `API_EXECUTION_UNSUPPORTED`

## 5. Integración con el motor de decisión

Antes de que una señal pase a candidato ejecutable, el motor debe poder consultar un estado consolidado de evidencia por familia/instrumento.

Ejemplo conceptual:

`DATA_READINESS = GREEN | YELLOW | RED`

con razones trazables como:

- `API_EXECUTABLE`
- `CONTRACT_FRESH`
- `MARKET_DATA_FRESH`
- `HISTORY_FRESH`
- `SETTLEMENT_KNOWN`
- `FEES_KNOWN`
- `RATIO_KNOWN`
- `MATURITY_KNOWN`
- `TICK_STEP_KNOWN`
- `FALLBACK_AVAILABLE`

Una señal puede seguir existiendo en SHADOW aunque la familia quede RED, pero no debe llegar a orden ejecutable.

## 6. Pendiente de implementación UI / introspección

Agregar a `/vivo` o submenú `Scraping`:

- semáforo global de Scraping/Contract Evidence;
- semáforo por fuente;
- semáforo por familia;
- último refresh;
- staleness;
- cobertura;
- API executable status;
- último schema/hash;
- discrepancias;
- último recovery;
- motivo exacto del estado;
- botón/drill-down solo de lectura para evidencia.

Integrar esta información al motor de introspección/early-warning y a Telegram para incidentes relevantes.

## 7. No-Trade 2026-09-08

Decisión operativa vigente:

- trading/órdenes: HARD BLOCK / NO TRADE;
- `real_orders_sent=0` sigue siendo invariante absoluto;
- market data: ON;
- históricos/backfill: ON;
- PPI Web / Contract Evidence: ON en read-only;
- PPI Web History SHADOW: ON cuando corresponda;
- observer/dashboard/introspection: ON;
- cambios funcionales de evidencia/scraping se pueden investigar sin habilitar operatoria.

## 8. Orden de trabajo recomendado

1. terminar Full DOM Sweep de 16 familias y cobertura/paginación;
2. construir matriz `PPI Web family -> PPI API executable capability`;
3. marcar familias `EXECUTABLE`, `PARTIAL`, `NON_EXECUTABLE`;
4. definir SLA/TTL/max_staleness por tipo de evidencia;
5. integrar DOM Contract Evidence V1 en SHADOW / `NO_AUTO_ACTIVATION`;
6. reconciliar DOM vs API vs History Store;
7. implementar semáforo Dashboard > Scraping;
8. integrar alerting/introspección/Telegram;
9. recién después evaluar promoción de familias a `READY_PAPER`.

## 9. Criterio de aceptación

Este checkpoint no se considera cerrado hasta que exista evidencia de que:

- cada familia visible tenga clasificación de operabilidad API;
- el sistema no confunda “visible/scrapeable” con “ejecutable”;
- la frescura de datos tenga SLA explícito;
- la pérdida de información pueda bloquear selectivamente una familia;
- dashboard muestre el estado de scraping con semáforo;
- introspección y Telegram alerten degradaciones materiales;
- `real_orders_sent=0` permanezca preservado durante toda la implementación y pruebas RC6.

## 10. Corrección transversal — Disk / Empirical Evidence Architecture A→E

Este checkpoint forma parte del backlog general RC6 y debe conservar la corrección canónica del frente de almacenamiento/evidencia.

NO resumir ese frente como `storage containment Phase A` solamente.

El programa completo es:

1. FASE A — Contención + nueva arquitectura de escritura.
2. FASE B — Migración SHADOW / reconciliación del legado.
3. FASE C — Retirada de redundancia física demostrada.
4. FASE D — Learning Evidence completa (`decision -> evidence -> outcome`).
5. FASE E — Retención / COLD storage.

Archivos obligatorios de continuidad agregados a esta rama:

- `POROTA_TRADING_CHECKPOINT_DISK_EVIDENCE_ARCHITECTURE_PHASES_A_E_2026-09-08.md`
- `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-08_ADDENDUM_DISK_PHASES_A_E.md`

El addendum corrige cualquier representación incompleta del checkpoint canónico actual y debe leerse junto con él en futuras conversaciones.

Las cinco fases permanecen PENDIENTES hasta evidencia explícita de ejecución/validación. Ningún registro de este plan autoriza cleanup destructivo.

Invariantes:

- preservar `historical_raw_archive` hasta equivalencia requerida;
- no `VACUUM` prematuro;
- no `docker system prune`;
- preservar evidencia/versiones/manifests/PARTIAL/conflicts;
- Gate 0 contra SHA live real y workflows antes de iniciar A;
- `real_orders_sent=0` absoluto.

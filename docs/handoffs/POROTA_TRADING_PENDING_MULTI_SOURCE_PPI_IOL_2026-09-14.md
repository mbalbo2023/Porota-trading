# POROTA TRADING — PENDIENTES OBLIGATORIOS MULTI-FUENTE PPI + IOL — 2026-09-14

## Carácter de este documento

Este archivo es un **backlog obligatorio** para continuar la ampliación del universo operativo de Porota Trading.

No puede darse por cerrado ni borrarse silenciosamente. Cada ítem debe terminar en uno de estos estados:

- `DONE` con evidencia/commit/run asociado;
- `DEFERRED` con motivo explícito;
- `WONT_DO` solo con decisión documentada del usuario;
- nunca desaparecer por simple reemplazo de checkpoint.

Branch de continuidad:

`ops/rc6-contract-open-session-immediate-20260914`

Documentos técnicos asociados:

- `docs/research/POROTA_MULTI_SOURCE_MARKET_DATA_AND_CONTRACT_ARCHITECTURE_2026-09-14.md`
- `docs/research/POROTA_PROVIDER_COVERAGE_MATRIX_INITIAL_2026-09-14.md`

## Objetivo

Evitar dos extremos incorrectos:

1. depender exclusivamente de PPI para toda la información analítica;
2. abandonar familias de instrumentos solo porque PPI no expone cómodamente todos los campos.

Objetivo final:

> Market data y analytics multi-source + contrato de ejecución específico del broker + fail-closed.

## P0 — Stopper para declarar nuevas familias READY_PPI

### MS-P0-01 — Completar AL30 como caso patrón de renta fija

Estado: `IN_PROGRESS`

Ya obtenido:

- AL30 explícito en PPI `InstrumentosOperables`;
- item/instrument identity;
- moneda;
- comisiones;
- precisión decimal;
- evidencia IOL de lote, T1, símbolos ARS/D/C;
- precio/caja de puntas IOL;
- histórico diario IOL 2026-09-01..2026-09-14 con OHLCV;
- TIR, TEM, duration, valor técnico, paridad y cashflow IOL.

Falta:

- capturar PPI `DatosTecnicos` o endpoint actual equivalente, o demostrar formalmente que PPI ya no lo entrega;
- determinar mínimos reales y semántica de cantidad/nominal para orden PPI;
- determinar `quantity_step` explícito si existe;
- determinar `price_tick` explícito si existe;
- validar settlement/plazo PPI exacto;
- confirmar restricciones de operatoria/subasta relevantes;
- comparar histórico IOL vs PPI para mismas fechas;
- producir veredicto `READY_PPI` / `HOLD` con causas enumeradas.

Criterio de salida:

No usar precisión decimal como sustituto de step/tick.

### MS-P0-02 — Completar Cauciones PPI

Estado: `IN_PROGRESS`

Ya obtenido:

- endpoint PPI `CaucionesOperables` confirmado;
- 120 filas observadas;
- campos `dias`, `descripcion`, `pildoraDescripcion`, `cantidadDecimales`, `cantidadDecimalesPrecio`;
- IOL API entrega tasa, vencimiento y mínimo para cauciones.

Falta:

- mapear filas PPI a moneda y plazo exactos;
- identificar tasa/precio que usa el flujo PPI;
- mínimo/máximo;
- step de monto/cantidad;
- liquidación;
- garantía/aforo para tomadora cuando aplique;
- disponibilidad actual;
- reglas de horarios;
- comparación PPI vs IOL y política ante divergencias.

Criterio de salida:

No habilitar Cauciones READY solo porque IOL provea tasas. El contrato de ejecución PPI debe estar completo.

### MS-P0-03 — Separar formalmente `ANALYTICS_READY` de `BROKER_READY`

Estado: `TODO`

Definir estados canónicos, por ejemplo:

- `MARKET_DATA_READY`
- `ANALYTICS_READY`
- `CONTRACT_READY_<BROKER>`
- `READY_PAPER_<BROKER>`
- `HOLD_CONTRACT`
- `HOLD_DATA`
- `PROVIDER_DIVERGENCE`

Evitar que una evaluación/simulación sea interpretada como autorización contractual.

## P1 — Integración IOL read-only

### MS-P1-01 — Inventario completo de endpoints/capacidades IOL útiles

Estado: `IN_PROGRESS`

Capacidades ya verificadas:

- asset info;
- current quote/order book;
- daily history;
- intraday prices;
- fixed income analytics;
- options chain + Greeks;
- caucion rates;
- corporate events;
- FCI listing.

Documentación oficial ya verificada:

- HTTPS + JSON;
- bearer + refresh token;
- bearer TTL documentado de 15 minutos;
- `/token` para autenticación/refresh;
- existencia de sandbox separado;
- producción puede impactar REAL;
- bonificación pública informada hasta 25.000 API calls/mes, sujeta a condiciones vigentes.

Pendiente:

- confirmar cobertura por familia PPI objetivo;
- documentar parámetros y respuestas;
- confirmar qué endpoints son públicos/market-data y cuáles requieren contexto de cuenta;
- prohibir en el adapter de investigación las operaciones mutativas.

### MS-P1-02 — Diseñar y codificar `IOLReadOnlyProvider`

Estado: `TODO`

Debe exponer inicialmente solo métodos de lectura:

- `get_instrument_info`
- `get_quote`
- `get_order_book`
- `get_daily_history`
- `get_intraday_history`
- `get_fixed_income_analytics`
- `get_caucion_rates`
- `get_options_chain`
- `get_corporate_events`

No incluir en esta fase:

- place order;
- cancel order;
- place caucion;
- FCI subscribe/redeem;
- movimientos de cuenta.

### MS-P1-03 — Seguridad de autenticación IOL

Estado: `TODO`

Documentar e implementar:

- bearer token;
- refresh token;
- bearer TTL documentado de 15 minutos;
- refresh seguro;
- secrets fuera de Git;
- tokens nunca en logs/artifacts;
- HTTPS obligatorio;
- timeouts;
- retries acotados;
- default deny ante fallo de auth.

### MS-P1-04 — Presupuesto de llamadas / rate budget

Estado: `TODO`

La documentación pública de IOL indica bonificación hasta 25.000 API calls/mes, sujeto a condiciones vigentes.

Pendiente:

- estimar llamadas por ciclo;
- estimar llamadas por rueda;
- definir cache TTL por tipo de dato;
- evitar polling redundante;
- definir backoff;
- definir métrica `iol_calls_today/month`.

## P1 — Matriz PPI vs IOL

### MS-P1-05 — Crear matriz de cobertura por campo y familia

Estado: `IN_PROGRESS`

Archivo creado:

`docs/research/POROTA_PROVIDER_COVERAGE_MATRIX_INITIAL_2026-09-14.md`

Commit inicial:

`ced8a71bc2f7e6ddbe70c712c1f8d6e8c944180f`

Actualización con pruebas de históricos/intradiario:

`454548fce663986d4474ed4baead6fca63cdeaa0`

Familias mínimas incluidas:

- ACCIONES
- CEDEAR
- ETF
- BONOS
- LETRAS
- ON
- CAUCIONES
- OPCIONES
- FUTUROS cuando corresponda
- FCI como familia diferida, sin borrado.

Pendiente completar todos los UNKNOWN/PARTIAL con evidencia.

### MS-P1-06 — Reconciliación de identidad

Estado: `TODO`

Resolver explícitamente:

- ticker base;
- ARS/D/C;
- item/provider ID;
- market;
- currency;
- settlement;
- family;
- underlying/issuer;
- alias y símbolos relacionados.

No unir instrumentos solo por ticker textual.

### MS-P1-07 — Reconciliación de cotizaciones

Estado: `TODO`

Definir:

- tolerancia temporal;
- tolerancia de precio;
- spread;
- stale detection;
- `PROVIDER_DIVERGENCE`;
- reglas para no mezclar snapshots de distintos plazos/monedas.

## P1 — Históricos y analítica

### MS-P1-08 — Validar históricos IOL por muestra representativa

Estado: `IN_PROGRESS`

Completado hasta ahora:

- Acción líquida `GGAL`: histórico diario 2026-09-01..2026-09-14, OHLCV disponible.
- CEDEAR `AAPL`: histórico diario 2026-09-01..2026-09-14, OHLCV disponible.
- Bono `AL30`: histórico diario 2026-09-01..2026-09-14, OHLCV disponible.
- `GGAL` intradiario 2026-09-14: secuencia de trades timestamp/precio/volumen disponible; `dates_without_intraday=[]` para la consulta probada.

Falta:

- 1 Letra;
- 1 ON;
- comparar GGAL/AAPL/AL30 contra PPI para mismas fechas;
- medir profundidad temporal real;
- medir retención intradiaria consultando fechas previas;
- huecos;
- ajuste/no ajuste;
- fechas/feriados;
- consistencia de volumen.

### MS-P1-09 — Renta fija multi-source

Estado: `IN_PROGRESS`

Usar AL30 como patrón.

Validar qué campos de IOL pueden alimentar analytics sin contaminar el contrato PPI:

- TIR;
- TEM;
- duration;
- clean/dirty price;
- accrued interest;
- technical value;
- parity;
- issue/maturity;
- cashflows;
- residual balance.

Crear provenance por campo.

### MS-P1-10 — Opciones: investigación analítica, NO READY

Estado: `IN_PROGRESS_RESEARCH_ONLY`

IOL ya demuestra capacidad de cadena + Greeks con GGAL.

Antes de cualquier habilitación PAPER operativa:

- contrato PPI;
- multiplicador;
- strike/vencimiento/tipo;
- exercise style/reglas;
- price tick;
- quantity step;
- liquidez;
- spread máximo;
- IV freshness;
- límites delta/gamma/vega;
- expiración y cierre;
- assignment/exercise risk.

## P2 — Scraping IOL solo por excepción

### MS-P2-01 — No construir scraper masivo todavía

Estado: `BLOCKED_BY_DESIGN`

Solo avanzar si la matriz demuestra campos críticos con:

- `PPI_API/WEB` insuficiente;
- `IOL_API` insuficiente;
- `IOL_WEB` sí contiene el dato;
- valor operacional alto.

### MS-P2-02 — Si se habilita scraping IOL

Estado: `DEFERRED`

Requisitos:

- read-only;
- perfil autenticado separado;
- locks;
- no credenciales en logs;
- schema validation;
- freshness;
- retries limitados;
- health/progress observable;
- fuente etiquetada `IOL_WEB`;
- nunca sustituir silenciosamente una fuente API más autoritativa.

## P2 — Motor de decisión

### MS-P2-03 — Provenance por decisión

Estado: `TODO`

Cada decisión debe poder explicar:

- qué precio utilizó;
- qué proveedor;
- timestamp/freshness;
- qué históricos;
- qué analytics;
- qué contrato de broker;
- qué restricciones se aplicaron.

### MS-P2-04 — Fail-closed por divergencia o staleness

Estado: `TODO`

Bloquear evaluación operable cuando:

- contrato del broker incompleto;
- cotización stale;
- PPI/IOL divergen por encima de tolerancia;
- settlement no coincide;
- moneda no coincide;
- provider ID ambiguo;
- step/tick inferido y no explícito cuando es obligatorio.

## Política temporal de universo

### Núcleo prioritario

`ACCIONES + CEDEAR` continúan como universo PAPER prioritario por madurez actual.

Esto **NO** equivale a descartar Bonos/Letras/ON/Cauciones/Opciones.

### Expansión

Orden recomendado:

1. Acciones/CEDEAR con cross-check IOL.
2. Bonos usando AL30 patrón.
3. Letras/ON.
4. Cauciones.
5. Opciones después de módulo de riesgo específico.

## Definición de DONE del programa multi-source

El programa no se considera terminado hasta que:

- existe adapter IOL read-only probado;
- existe modelo canónico multi-provider;
- existe reconciliación de identidad;
- existe provenance;
- existe provider divergence gate;
- PPI Contract Gate sigue separado de analytics;
- AL30 pasa su caso end-to-end en PAPER o queda HOLD con faltantes concretos;
- Cauciones pasan caso end-to-end PAPER o quedan HOLD con faltantes concretos;
- la matriz de familias está documentada;
- scraping IOL se acepta o rechaza por evidencia, no por intuición.

## Invariantes

- `REAL_ORDERS_SENT=0`.
- no mutaciones de broker durante investigación.
- no borrar familias ni históricos para simplificar el problema.
- no declarar READY por aproximación.
- no inferir step/tick desde decimales.
- no usar IOL para autorizar ejecución PPI sin contrato PPI.

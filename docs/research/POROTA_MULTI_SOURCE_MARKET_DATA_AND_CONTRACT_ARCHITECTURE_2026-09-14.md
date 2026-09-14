# POROTA TRADING — INVESTIGACIÓN ARQUITECTURA MULTI-FUENTE PPI + IOL — 2026-09-14

## Estado

**INVESTIGACIÓN INICIADA — OBLIGATORIA ANTES DE AMPLIAR READY MÁS ALLÁ DEL NÚCLEO MADURO.**

Este documento define la línea de trabajo para desacoplar el análisis de mercado de Porota de un único broker, sin perder el principio de que la ejecución debe respetar el contrato operativo del broker por el que efectivamente se cursaría la orden.

No habilita órdenes reales, no cambia el motor productivo y no autoriza importaciones canónicas automáticas.

## Decisión de arquitectura adoptada

Porota debe evolucionar hacia una arquitectura de cuatro capas claramente separadas:

1. **MARKET DATA**: cotizaciones, puntas, volumen, históricos, intradiario, eventos.
2. **ANALYTICS**: indicadores, renta fija, TIR, duration, cashflows, opciones/Greeks, scoring, señales.
3. **BROKER CONTRACT / EXECUTION CONTRACT**: reglas específicas del broker: especie exacta, moneda, mercado, plazo, unidad/lote, mínimos, incrementos, restricciones, disponibilidad y validaciones.
4. **EXECUTION ADAPTER**: construcción/validación/envío de la orden al broker correspondiente. En el estado actual de Porota esto permanece en PAPER y toda orden real sigue prohibida.

Regla principal:

> Una fuente puede ser excelente para analizar un instrumento sin ser suficiente para autorizar su ejecución en otro broker.

Por lo tanto, datos de IOL pueden alimentar el análisis de instrumentos que luego se operarían por PPI, pero la condición `READY_PPI` debe seguir exigiendo contrato operativo PPI suficiente.

## Rol de cada proveedor

### PPI

Rol primario propuesto:

- autoridad para disponibilidad real en PPI;
- identidad/especie operable en PPI;
- mercado y moneda;
- plazo/liquidación;
- parámetros de operatoria que PPI exponga;
- restricciones y configuraciones del flujo de órdenes PPI;
- contrato de ejecución final cuando el broker de ejecución sea PPI.

PPI **no debe ser la única fuente obligatoria de analítica** si otro proveedor estructurado y auditable ofrece mejores datos.

### IOL API

Rol primario propuesto:

- segunda fuente estructurada de market data;
- cotización y caja de puntas;
- históricos diarios e intradiarios;
- metadatos de instrumentos;
- analítica de renta fija;
- opciones y Greeks;
- tasas y mínimos de cauciones;
- eventos corporativos;
- fuente de contraste para detectar anomalías entre proveedores.

IOL API debe ser preferida frente a scraping IOL cuando exista un endpoint estructurado equivalente.

### Scraping IOL

Rol propuesto: **último recurso**.

Solo se justifica si un dato importante:

1. no existe en la API IOL;
2. no existe o no es accesible de forma confiable en PPI;
3. es visible en la web de IOL y puede obtenerse de forma estable, read-only y legalmente compatible;
4. tiene valor suficiente para justificar el costo de mantenimiento del scraper.

No iniciar scraping masivo IOL hasta terminar la matriz de cobertura de API.

## Evidencia obtenida el 2026-09-14

### PPI — evidencia contractual actual

Se confirmó en sesión autenticada que el frontend actual de PPI sigue emitiendo:

- `/api/Ordenes/InstrumentosOperables`
- `/api/Ordenes/CaucionesOperables`
- `/api/Ordenes/ConfiguracionOperatoriaSimplificada`

Captura dirigida posterior:

- AL30 apareció explícitamente en `InstrumentosOperables`.
- `CaucionesOperables` devolvió **120 filas**.
- Las filas de cauciones observadas incluyen al menos: `cantidadDecimales`, `cantidadDecimalesPrecio`, `descripcion`, `dias`, `pildoraDescripcion`.
- AL30 normalizado expuso identidad, moneda, comisiones, decimales y otros campos de operabilidad.
- El normalizador mantiene `quantity_step=None` y `price_tick=None` cuando PPI solo entrega precisión decimal. **No inferir step/tick desde decimales.**

Aún falta capturar de manera concluyente `DatosTecnicos` o equivalente actual para AL30 y validar qué datos de contrato no están en los endpoints ya recuperados.

### IOL — capacidades estructuradas verificadas

La investigación read-only confirmó que el conector/API disponible expone, entre otras capacidades:

- `get_asset_info`
- `get_asset_quote`
- `get_price_history`
- `get_intraday_prices`
- `get_fixed_income_analytics`
- `get_options_chain`
- `get_caucion_rates`
- `get_caucion_rate`
- `get_next_corporate_events`
- `get_fci_funds`

Pruebas representativas realizadas sin órdenes:

#### AL30

`get_asset_info` devolvió:

- tipo: `TIT. PUBLICOS`;
- moneda ARS;
- `units_per_lot=100`;
- plazo `T1`;
- especies relacionadas `AL30`, `AL30D`, `AL30C`.

`get_asset_quote` devolvió precio, apertura, cierre previo, máximo, mínimo, variación, volumen nominal, volumen efectivo y caja de puntas bid/ask.

`get_fixed_income_analytics` devolvió, entre otros:

- dirty price;
- clean price;
- accrued interest;
- technical value;
- parity;
- TIR;
- TEM;
- Macaulay duration;
- modified duration;
- fecha de emisión;
- vencimiento;
- settlement date;
- nominal usado en el cálculo;
- cronograma de cashflows con interés, amortización y saldo residual.

Esto prueba que IOL puede cubrir una parte sustancial de la **analítica profesional de renta fija** aunque el contrato de ejecución continúe siendo PPI.

#### Acciones y CEDEAR

`GGAL` fue identificado como `ACCIONES`, moneda ARS, T1, lote 1 y especie dólar relacionada.

`AAPL` en BCBA fue identificado como `CEDEARS`, moneda ARS, T1, lote 1 y especies `AAPLD`/`AAPLC` relacionadas.

Esto confirma que IOL puede servir como segunda fuente para metadatos de Acciones/CEDEAR y, mediante quote/history, como fuente de contraste de precios e históricos.

#### Cauciones

`get_caucion_rates` entregó tasas colocadoras vigentes para 1, 2 y 3 días, fecha de vencimiento y monto mínimo. En la consulta observada el mínimo ARS fue `100000`.

Este dato es valioso para análisis y validación económica, pero no sustituye las reglas de ejecución PPI si la orden se cursa por PPI.

#### Opciones

`get_options_chain(GGAL)` devolvió cadena activa con:

- call/put;
- strike;
- vencimiento;
- bid/ask cuando existe mercado;
- implied volatility;
- theoretical price;
- delta/gamma/theta/vega/rho;
- volumen;
- bandera de stale cuando aplica.

Conclusión preliminar: IOL ofrece una cobertura analítica de opciones muy superior a lo que hoy está integrado en Porota. Esto justifica investigar una capa `IOL_ANALYTICS`, pero **NO habilitar opciones para operar hasta tener contrato de ejecución y lógica/riesgo específicos**.

## Investigación de documentación oficial IOL

Documentación oficial consultada el 2026-09-14:

- `https://www.invertironline.com/api`
- `https://www.invertironline.com/documentacion-api`
- `https://api.invertironline.com/`
- `https://api.invertironline.com/Help/Autenticacion`

Hallazgos:

- IOL declara acceso a cotizaciones actuales e históricas para mercado argentino.
- La API utiliza HTTPS y JSON.
- La documentación de autenticación describe bearer token + refresh token.
- El bearer token documentado tiene vigencia de 15 minutos.
- El primer login documentado usa `POST /token`; la renovación también usa `/token` con refresh token.
- La documentación oficial menciona un entorno sandbox separado del usuario/servicio real.
- La página principal de la API advierte que las acciones sobre el entorno real impactan en REAL; por lo tanto Porota debe usar default-deny y nunca asumir que un endpoint operativo es simulado.
- La página de tarifas informa servicio API bonificado hasta 25.000 calls/mes, sujeto a condiciones/tarifas vigentes de IOL. Debe medirse consumo antes de una ingesta masiva recurrente.

## Arquitectura objetivo propuesta

```text
                       +-------------------+
PPI API / PPI Web --->|                   |
                      |  Provider Adapters |----+
IOL API ------------->|                   |    |
                       +-------------------+    |
                                                v
                                       +-----------------+
                                       | Canonical Model |
                                       | instruments     |
                                       | quotes/history  |
                                       | analytics       |
                                       | contracts       |
                                       +-----------------+
                                                |
                         +----------------------+----------------------+
                         |                                             |
                         v                                             v
                +-----------------+                           +------------------+
                | Decision Engine |                           | Contract Gate    |
                | multi-source    |                           | broker-specific  |
                +-----------------+                           +------------------+
                         |                                             |
                         +----------------------+----------------------+
                                                v
                                      +---------------------+
                                      | PAPER Execution     |
                                      | PPI adapter guarded |
                                      +---------------------+
```

## Modelo canónico mínimo propuesto

### `instrument_identity`

- canonical_id
- ticker
- family
- issuer/underlying
- market
- currency
- provider_ids: `{ppi, iol, ...}`
- related_symbols ARS/D/C cuando corresponda

### `market_snapshot`

- provider
- canonical_id
- timestamp
- settlement_term
- last/open/high/low/previous_close
- bid levels / ask levels
- volume nominal / cash volume
- freshness

### `historical_bar`

- provider
- canonical_id
- timeframe
- timestamp/date
- OHLCV
- adjusted flag si aplica

### `fixed_income_analytics`

- provider
- canonical_id
- calculation_timestamp
- clean/dirty price
- accrued interest
- technical value
- parity
- TIR/TEM
- duration
- issue/maturity dates
- cashflow schedule

### `broker_contract`

- broker
- canonical_id
- provider_instrument_id
- availability
- market
- currency
- settlement terms
- units/lot
- minimum_quantity_or_nominal
- quantity_step
- price_tick
- price/quantity decimal places
- commissions/fees
- auction/restriction flags
- evidence timestamp
- evidence provenance
- semantic confidence

**Regla:** ningún campo semántico ausente puede derivarse silenciosamente de otro campo de diferente significado.

## Política de precedencia de datos propuesta

### Para decidir/analizar

1. dato más fresco y estructurado de un proveedor autorizado;
2. contraste entre PPI e IOL cuando ambos entregan el mismo concepto;
3. si difieren, marcar `PROVIDER_DIVERGENCE` y no fusionar sin regla explícita;
4. usar fuentes regulatorias/oficiales para términos estables cuando corresponda, sin reemplazar la disponibilidad operativa del broker.

### Para ejecutar por PPI

PPI debe prevalecer para:

- identidad operable exacta;
- disponibilidad;
- mercado/moneda/plazo aceptados;
- restricciones;
- unidades/mínimos/step/tick cuando el broker los exija;
- validaciones del flujo de orden.

IOL **no puede autorizar por sí solo** una orden PPI.

## Universo operativo recomendado durante la transición

### Acciones y CEDEAR

Mantener como núcleo PAPER prioritario por ser hoy el universo más maduro del sistema.

No congelar el desarrollo del resto de familias.

### Bonos / Letras / ON

No abandonar.

Usar IOL para market data y analítica cuando sea suficiente, mientras PPI Contract Gate completa la evidencia necesaria para ejecución PPI.

AL30 será caso patrón de validación de la arquitectura.

### Cauciones

No abandonar.

Combinar:

- PPI `CaucionesOperables` para contrato/disponibilidad PPI;
- IOL rates/minimums para análisis y cross-check;
- fail-closed ante divergencias semánticas no resueltas.

### Opciones

Mantener fuera de READY operativo inmediato.

Investigar IOL como fuente de cadena/Greeks. Antes de habilitar PAPER operativo se requieren:

- contrato PPI específico;
- multiplicador/tamaño de contrato;
- vencimiento/strike/tipo;
- reglas de ejercicio;
- liquidez/spread filters;
- Greeks/riesgo de cartera;
- límites de exposición;
- estrategia de cierre y expiración.

### FCI

Fuera del objetivo inmediato de trading intradía. Mantener datos/código, sin borrarlos.

## Fases de implementación propuestas

### Fase MS-0 — investigación y contrato de interfaces

Estado: **IN PROGRESS**.

- inventariar todos los endpoints read-only de IOL útiles;
- mapear cobertura por familia;
- mapear campos PPI vs IOL;
- definir modelo canónico y reglas de precedencia;
- definir freshness/quality score;
- definir presupuesto de llamadas IOL;
- definir seguridad/secret handling.

### Fase MS-1 — adapter IOL read-only

Crear un cliente exclusivamente read-only para:

- asset info;
- quote/order book;
- daily history;
- intraday history;
- fixed income analytics;
- caucion rates;
- options chain;
- corporate events.

**No implementar métodos de place_order/cancel/place_caucion/suscripciones FCI en esta fase.**

### Fase MS-2 — cross-provider reconciliation

- normalizar ticker/provider IDs;
- comparar precio/freshness;
- detectar outliers;
- generar `provider_divergence`;
- no sobreescribir PPI contract evidence con IOL.

### Fase MS-3 — integración con Decision Engine

- permitir market data/analytics multi-source;
- mantener Contract Gate separado;
- registrar en cada decisión qué fuente aportó cada dato relevante;
- evitar que la indisponibilidad de una fuente degrade silenciosamente a datos stale.

### Fase MS-4 — habilitación por familias

Orden recomendado:

1. Acciones/CEDEAR: cross-check IOL sin cambiar ejecución.
2. Bonos: AL30 patrón y luego expansión.
3. Letras/ON: una vez estabilizado el mapping de renta fija.
4. Cauciones.
5. Opciones solamente tras módulo de riesgo específico.

## Criterio para decidir si hace falta scraping IOL

No iniciar un scraper por intuición.

Para cada campo faltante se debe registrar:

- campo;
- familia;
- existe PPI API/web: sí/no/parcial;
- existe IOL API: sí/no/parcial;
- existe IOL web: sí/no;
- criticidad para análisis;
- criticidad para ejecución;
- estabilidad esperada;
- decisión final.

Solo los campos con `IOL_API=NO` y `IOL_WEB=SI` pasan a evaluación de scraping.

## Guardrails de seguridad obligatorios

- `PRODUCTION_PAPER` mientras esta investigación esté activa.
- `REAL_ORDERS_SENT=0` como invariante.
- investigación IOL inicialmente **GET/read-only**.
- no llamar endpoints de `place_order`, `cancel_order`, `place_caucion`, `subscribe_fci`, `redeem_fci` ni equivalentes desde runners de investigación.
- credenciales/tokens IOL nunca en Git ni en logs.
- bearer/refresh token en memoria o secret store; nunca en artifacts.
- timeouts, retries acotados y rate limiting.
- cache donde tenga sentido para no consumir API calls innecesariamente.
- cada provider response debe registrar provenance/freshness sin almacenar secretos.

## Veredicto preliminar

**NO abandonar PPI. NO limitar Porota definitivamente a Acciones+CEDEAR. NO iniciar scraping IOL masivo todavía.**

La arquitectura recomendada es:

> **PPI = autoridad de contrato/ejecución PPI. IOL = segunda fuente estructurada de market data y analítica. Porota = normalización, reconciliación, decisión y riesgo.**

Esto permite operar profesionalmente con información más rica sin asumir que un broker define por sí solo toda la verdad de mercado.

## Próxima investigación obligatoria

1. cerrar AL30 `DatosTecnicos` o demostrar formalmente qué campos no entrega PPI;
2. construir matriz PPI-vs-IOL por familia y campo;
3. validar cobertura IOL de históricos para muestra representativa: Acción, CEDEAR, Bono, Letra, ON;
4. validar freshness y caja de puntas IOL durante rueda;
5. medir límites/costo de API y diseñar cache/rate budget;
6. diseñar adapter IOL read-only en código sin tocar el motor productivo;
7. recién después decidir si algún campo requiere scraping IOL.

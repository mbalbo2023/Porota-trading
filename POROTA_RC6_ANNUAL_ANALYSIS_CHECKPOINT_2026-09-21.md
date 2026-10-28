# POROTA RC6 — checkpoint de análisis anual e históricos

**Fecha:** 2026-09-21 (UTC)  
**Workstream:** menú de análisis anual, independiente  
**Repositorio:** `mbalbo2023/Porota-trading`  
**Rama aislada:** `feat/rc6-annual-instrument-analysis-20260921`  
**Base operativa usada:** `deploy/rc6-pr69-isolated-20260915@3de5171281612f06f97a118cca2d01a016382cb9`  
**Guardrails:** no merge ni deploy; `PRODUCTION_PAPER`, `SIMULATED`, órdenes reales bloqueadas; no tocar PPI Watch, Cockpit/Site 8766 ni el workstream paralelo de caché IOL.

## Objetivo y alcance

1. Determinar qué puede aportar IOL a una futura ampliación de familias publicadas por PPI y qué riesgos existen con evidencia contractual incompleta.
2. Documentar cómo el motor usa hoy los históricos y qué estado verificable tienen.
3. Añadir en una rama independiente un menú autenticado **Análisis**, con selector de tipo y papel e informe de performance anual basado en el histórico canónico v2.

La medición/rotación/frescura del caché IOL queda excluida por instrucción del usuario y no es insumo de este análisis ni de la página.

## Hallazgos sobre IOL y nuevas familias

- El catálogo reconoce Acciones, CEDEARs, ETF, Bonos, Letras, ON, Opciones, Futuros, Cauciones y FCI; reconocer una familia no significa que tenga contrato, colector, motor o permisos de operatoria listos.
- La política RC6 limita la operatoria y la nueva ingesta de históricos a `ACCIONES` y `CEDEARS`. En el colector IOL observado los únicos métodos permitidos son lectura de cotización e información del activo; el DTO normalizado disponible contempla último, bid/ask, spread, variación, volumen en dinero, tipo, moneda y unidades por lote. Esa información puede servir como contraste SHADOW de precio/liquidez, pero no sustituye los términos contractuales especializados.
- El repositorio ordena las fuentes históricas PPI primero (rank 10), BYMA/A3 después (20), IOL (30), Data912 (50) y Yahoo (90). IOL es respaldo/contraste; no sobrescribe una fuente de mayor autoridad.
- Para habilitar una familia se necesita evidencia completa y específica por identidad, además de calidad/frescura de mercado. Ejemplos mínimos que faltan según familia: nominal mínimo/step, vencimiento, cupón/amortización y convención de precio en renta fija; subyacente, vencimiento, strike, call/put y multiplicador en opciones; contrato, vencimiento y margen en futuros; plazo, lado, tasa y garantía en cauciones; valuación, cutoff, rescate/liquidación y cargos en FCI. El repositorio prohíbe derivar multiplicadores, márgenes, vencimientos o tick desde ticker/decimales.
- Con campos incompletos, el riesgo es una identidad equivocada, precio con escala/unidad errónea, P&L y tamaño mal calculados, vencimiento/settlement incorrectos, costos omitidos, señal sobre cotización vieja/no comparable y backtest inválido. La regla vigente es fail-closed: mostrar/observar con etiqueta explícita, abstenerse de habilitar `READY_PAPER` y no enviar órdenes.

## Históricos: arquitectura y uso por el motor

- El almacén v2 separa `history_versions_v2` append-only de `history_canonical_v2`; la identidad de una barra es símbolo + familia + mercado + liquidación + fecha. La elección canónica conserva procedencia, prioriza fuente con mayor autoridad y el flag adjusted cuando la identidad coincide. Histórico no equivale a `READY_PAPER` ni es precio de ejecución.
- La política de recolección vigente procesa sólo acciones/CEDEARs; las otras familias son legado/auditoría y no reciben nueva ingesta. No se permite interpolar, hacer forward-fill ni inventar OHLC. Los niveles de contexto definidos son 30 barras mínimas, 90 preferidas y 180 fuertes.
- El motor no está leyendo el canónico v2 para su cálculo histórico de decisión: `historical_candle_shadow_rc6.py` consulta la última fila de `production_history` para símbolo/familia/liquidación; valida OHLC y limita cada fila a `date <= as_of`. Para velas 5m consulta `candle_versions/candle_series` y exige `bar_end` y `known_at <= as_of`, excluye sintéticas y valida calidad.
- Ambas señales se evalúan en SHADOW. El histórico calcula tendencia 20/50, rango promedio y retorno absoluto promedio; las velas calculan momentum 3 contra 15 barras y rango. Con al menos 20 cierres y 15 velas de calidad produce un delta acotado de score y un `decision_shadow`; no modifica la acción factual, gates, tamaño ni órdenes. Si faltan datos devuelve `HISTORY_UNAVAILABLE`, `HISTORY_INSUFFICIENT` o `INSUFFICIENT_DATA`.
- El dashboard existente compara métricas legacy con el conteo de identidades/filas v2. El checkpoint del 21-Sep verifica integridad SQL de `market_history.db`, no su cobertura ni la fecha/frescura de las barras. Último conteo de cobertura rastreable en el checkpoint heredado del 14-Sep: 239/246 identidades simulables (97,15%; Acciones 55/55, CEDEARs 184/191). Es un dato histórico, no debe presentarse como estado actual. **Cobertura actual por familia, ventanas y filas válidas: NOT_VERIFIED**; hace falta leer métricas runtime actuales de modo read-only.

## Menú Análisis agregado en esta rama

Archivos:
- Nuevo `rc6_annual_instrument_analysis.py`: lee sólo `history_canonical_v2` con SQLite `mode=ro` y `query_only`; no crea, migra ni modifica la base.
- `da_dashboard_ux_hf6.py`: agrega `/analisis` a la navegación estándar.
- `bg_paper_dashboard.py`: agrega ruta GET autenticada `/analisis`.

El selector muestra únicamente identidades con barras canónicas y luego permite elegir el papel. El informe del año calendario presenta retorno YTD con base de cierre previo cuando existe, drawdown, volatilidad anualizada, RSI-14, ATR-14, SMA-20/50/200, máximo/mínimo de hasta 252 barras, cantidad de barras, OHLC completo, fuente y estado de ajuste. Si no hay datos/base suficiente, lo identifica y no sintetiza valores. La página es informativa y no cambia el universo ni la operatoria.

## Estado de entrega

- Código añadido en rama separada: sí.
- Revisión estática de source e integración/diff: realizada; enlace de comparación incluido en el handoff.
- Pruebas funcionales automatizadas y runtime: pendientes; no ejecuté tests.
- PR: no creado, para no iniciar CI/verificaciones automatizadas sin pedido del usuario.
- Integración en rama canónica / deploy / validación runtime: no realizados.
- SHA inicial de cambio del módulo: `d0b2d4d641c858c9d1ba41132bb17fc7afa8d896`.
- SHA del ajuste de identidad y cierre de conexiones read-only: `e217f0bb12922f62e92652a35d00a5b0fffbd743`.
- Evidencia de render/datos del runtime: NOT_VERIFIED.

## Próximos pasos

1. Revisar el diff de esta rama en GitHub.
2. Cuando se autorice verificación, ejecutar pruebas focalizadas de página/selector, queries de sólo lectura, identidad con mercado/liquidación, cobertura nula y cálculos de indicadores; documentar el resultado.
3. Después de revisión/CI, abrir PR en borrador hacia la rama canónica si no hay conflicto de concurrencia. Merge y deploy requieren autorización explícita.
4. En un pase runtime separado, medir cobertura histórica actual por familia, rango de fechas, fuente, adjusted/raw y barras válidas; no repetir ingestas masivas.
5. Mantener cualquier propuesta de ampliar familias en Shadow/read-only hasta demostrar contrato completo, cobertura histórica y gates por familia. No cambiar la operatoria dentro de este workstream.


## Aclaración de alcance del usuario — señales y validación ON (21-Sep-2026)

- El usuario pide un dashboard más amplio (momentum y sugerencias compra/venta desde análisis propio) y una validación para Obligaciones Negociables comparando PPI e IOL. La tarea de caché de IOL queda explícitamente fuera de este workstream.
- Ampliación en `rc6_annual_instrument_analysis.py` (commit `4dc2aa32bf2831b2933270bb268e3f0416bb4a31`): retornos de momentum a 20/60/120/252 ruedas, MACD 12/26, lectura descriptiva de tendencia y estado de sugerencia.
- El informe no emite compra/venta para datos diarios porque la regla de momentum del motor RC6 usa muestras intradiarias y spread; no hay validación walk-forward demostrada para trasladar sus umbrales a frecuencia anual. Muestra explícitamente abstención y por qué. La lectura de tendencia es contextual y no modifica el motor.
- Para familias ON, el informe explica que cotejar cotizaciones PPI/IOL no es suficiente y enumera los términos contractuales requeridos para rendimiento/riesgo; sin contrato íntegro o ejecutor/gates especializados no se puede concluir aptitud operativa. La página no valida un ticker individual ni activa trading.
- No se ejecutaron pruebas ni consultas runtime; se debe revisar diff y, con autorización del usuario, hacer verificación focalizada. No PR, merge ni deploy.


## Piloto de ON elegido por el usuario — YMCID (YPF Clase XVII)

Validación de datos de consulta realizada el 21-Sep-2026; no se enviaron órdenes.

- IOL identifica YMCID como ON de YPF Clase XVII, BCBA, moneda USD, lote de 100 unidades; related symbols: YMCIO (ARS), YMCID (USD), YMCIC (cable). Fuente del emisor: YPF lista la Clase XVII emitida el 12-Feb-2021 y con vencimiento 30-Jun-2029; el prospecto oficial describe tasa fija 8,5% anual. Consultas independientes de IOL describen vencimiento 30-Jun-2029.
- Quote IOL observado 21-Sep-2026 16:49:11 -03: lot_price USD 94.37; mejor bid 94.00 (361), mejor ask 94.37 (2,313); volumen nominal 115,035; monto efectivo USD 108,330.34. Datos de referencia de una consulta, no prueba de liquidez sostenida ni de ejecutabilidad por PPI.
- IOL fixed-income analytics observado 16:56 -03: dirty price 95.90, clean 94.14274, accrued 1.75726, residual nominal 85.72, TIR 0.0236557 (campo decimal según la herramienta), duración modificada 1.41596 y cronograma de seis flujos. Estos cálculos deben tomarse como output del proveedor pendiente de validación: calculation_inputs.trade_price=95.90 no coincide con la cotización lot_price=94.37; no comparar sin confirmar convención de precio, fecha de liquidación, residual/unidades y origen temporal. La diferencia no se interpreta aquí como arbitraje ni como señal.
- IOL history devuelve 245 barras completas entre 22-Sep-2025 y 21-Sep-2026. Cálculos descriptivos de cierres: cambio de precio del período -9.08% (no retorno total), momentum 20 ruedas +0.94%, 60 ruedas -15.17%, 120 ruedas -13.06%, volatilidad realizada anualizada aproximada 20.55%, drawdown máximo de cierres -18.50%, SMA20 93.749, SMA50 93.544, SMA200 104.736. En una ON el cambio de precio excluye cupones/amortizaciones y no equivale a performance total.
- Código de Porota PPI admite solicitar get_market_data y libro con tipo genérico ON, y enumera ON como tipo aceptado; eso demuestra una vía de consulta prevista, no cobertura efectiva de YMCID. El catálogo contractual no acepta una ON sin financial_contract_v17, en particular señala NEEDS_NOMINAL_UNITS. La política de histórico RC6 mantiene ON en familias legacy/audit y no las refresca; por lo tanto, el selector de análisis no puede prometer que YMCID aparezca con serie canónica.
- Resultado: **VALIDACIÓN PARCIAL / NO HABILITAR OPERATORIA**. Falta cotejo PPI en la misma plaza, especie, liquidación y unidad; confirmar contrato/tamaño nominal por unidad, base clean/dirty y fecha de liquidación; validar analytic cashflows contra términos oficiales vigentes; capturar histórico total-return con cupones/amortizaciones; y crear/revisar ejecutor/riesgo especializado para renta fija. Un quote IOL e identidad de ticker no alcanzan.
- El informe anual del feature branch fue ajustado para llamar la cifra ON “Variación de precio (sin flujos)” y advertir que no incluye cupón, amortización, interés corrido ni reinversión. Commit: 24520060a43c5948d3ab3f1dbca9e8ddd18350ac.
- Evidencia de consulta live PPI: NOT AVAILABLE IN THIS SESSION. No se usó una orden, simulación de orden, endpoint de cuenta ni caché.


## Complemento del análisis: el endpoint de bonos PPI sí documenta analytics

La primera conclusión sobre PPI debe precisarse. La documentación oficial de la librería Python incluye `marketdata.estimate_bonds(EstimateBonds(...))`. Su respuesta documenta flujos con residual/renta/amortización/total, sensibilidad con TIR/precio/paridad/variación, TIR, duración modificada, interés corrido, valor residual, total de renta y amortización, moneda, cantidad/títulos, cupón actual, emisor, moneda de emisión/pago, amortización, intereses, fecha de emisión/vencimiento, ley, lámina mínima e ISIN. La configuración oficial enumera ON como tipo y BYMA/settlements como catálogos disponibles. Referencias: documentación pública oficial PPI, secciones Instrument Types, Market Data y Bonds Calculator: https://itatppi.github.io/ppi-official-api-docs/api/documentacionPython/

Eso hace **plausible cubrir en combinación** el gap contractual/analítico que PPI quotes alone no cubrían: PPI puede ser fuente de precio/libro/estimación y IOL puede aportar precio/libro, histórico y fixed-income analytics/cashflows independientes. Es complementariedad potencial, todavía no conformidad confirmada para YMCID.

Estado real del gap:
- En Porota, `c_ppi_client.py` implementa current/book/historical search, pero no contiene una llamada `estimate_bonds`; hay que integrar el endpoint con schema validado y solo lectura.
- La documentación muestra ejemplo del calculator con CUAP, no evidencia de respuesta exitosa para el ticker corporativo YMCID ni de soporte exacto por settlement; falta consulta PPI read-only real.
- La ruta IOL collector de Porota hoy permite `get_asset_info` y `get_asset_quote` solamente; analytics/history de fixed income retornados por el conector actual todavía deben integrarse al pipeline y persistirse como evidencia con source/timestamp.
- El piloto YMCID necesita cotejar por misma fecha de cálculo y liquidación: ticker/ISIN, moneda, lote y nominal, precio clean vs dirty, accrued, residual balance, flows, TIR/convención, duration, book, costos y monto. Un mismatch provoca abstención.
- Una serie de OHLC histórica de cualquiera de los brokers no es retorno total de la ON; debe incorporarse cashflow ledger de cupones y amortizaciones.
- La operación de RC6 no se activa por añadir un ticker: la allowlist del motor continúa ACCIONES/CEDEARS; falta ledger/event handling de renta fija y gates de crédito, liquidez, concentración y cash settlement. La condición `NEEDS_NOMINAL_UNITS` permanece hasta formalizar el contrato del símbolo.

Conclusión de piloto actualizada: **GAP CUBRIBLE EN PRINCIPIO, PENDIENTE DE DEMOSTRAR PARA YMCID**. No habilitar ON productiva aún. La evidencia que falta es una llamada real PPI de lectura/estimate para YMCID y su comparación con IOL. No hay herramienta PPI autenticada accesible en esta sesión y no se hizo cambio operativo ni orden. Si esa corrida coincide y los gates de renta fija pasan, el paso siguiente es integrar primero Shadow, medir y luego habilitar explícitamente la familia en RC6; el presente feature branch no ha sido desplegado.


### Detalle de compatibilidad del Bond Calculator PPI

La documentación Python de PPI muestra `marketdata.estimate_bonds(EstimateBonds(ticker, date, quantityType, quantity, price))` y su respuesta rica en flujos/analítica. Sin embargo, la documentación REST para `GET /MarketData/Bonds/Estimate` lista además `AmountOfMoney`, `ExchangeRate`, `EquityRate`, `ExchangeRateAmortization` y `RateAdjustmentAmortization` como parámetros requeridos. Antes de añadir el wrapper en Porota debe verificarse la firma instalada de ppi-client 1.3.0 y una respuesta sandbox/read-only para YMCID, sin adivinar esos campos. Esto es otra brecha de integración, no evidencia de imposibilidad del proveedor.


## Implementación del piloto PPI + IOL

- `c_ppi_client.py` ahora incluye `get_bond_estimate` (commit `14f94e64f784d906ed985d47a2c69d026e3cd60d`): wrapper de solo lectura para el calculador de bonos PPI. Si el SDK instalado no acepta la firma o faltan campos, devuelve `None` y no habilita nada.
- `rc6_on_validation.py` (commit `5161eeaa04a87de56052d4e4682341bc9a7e6729`) compara payloads ya capturados de PPI e IOL: identidad, plaza, moneda, liquidación y unidades. Cualquier ausencia o contradicción produce `BLOCKED`; solo la evidencia alineada produce `READY_SHADOW`. El módulo no hace llamadas ni órdenes.
- Falta una corrida autenticada en el entorno del bot para YMCID que obtenga PPI SearchInstrument/current/book/history/estimate y los cruce con IOL. No se ejecutó desde aquí porque no hay cliente PPI autenticado expuesto en esta sesión y se mantiene el alcance read-only.
- La producción no debe activarse solo porque el wrapper compile: se requiere resultado `READY_SHADOW`, concordancia de flujos y convención clean/dirty, histórico total-return y gates/executor de renta fija.


## Comparación concreta PPI vs IOL para YMCID (21-Sep-2026)

### Qué ofrece cada fuente

| Campo | PPI documentado | IOL observado | Resultado |
|---|---|---|---|
| Tipo de instrumento | Configuración incluye ON | Obligaciones Negociables | Compatible |
| Identidad | SearchInstrument: ticker, descripción, moneda, tipo, mercado | YMCID, YPF Clase XVII, BCBA, USD | Falta cotejo live PPI |
| Unidad | Calculator recibe quantity/price; la respuesta incluye quantityTitles/minimalSheet | units_per_lot=100 | Falta confirmar misma convención |
| Current/Book | Current y Book documentados | Último, bid/ask, volumen | Cubre el dato |
| Histórico | Search histórico OHLCV | 245 barras diarias IOL | Cubre el dato; falta comparar series |
| Flujos | Bonds Estimate: flows, residual, rent, amortization, total | Seis flujos IOL | Cubre el dato; falta cotejo |
| TIR/duración | Bonds Estimate: tir y md | TIR 2,3656%, MD 1,41596 | Cubre el dato; falta tolerancia |
| Identidad contractual | Response incluye issuer, currencies, issue/expiration, law, ISIN | IOL analytics expone fechas, residual y flujos | PPI debe confirmar ISIN/contrato |
| Total return | Requiere sumar flujos a precio/fecha | No viene listo en OHLC | Debe calcularlo Porota |

PPI documenta ON como instrument type y el endpoint `GET /MarketData/Bonds/Estimate` con Ticker, Date, QuantityType, Quantity, AmountOfMoney, Price, ExchangeRate, EquityRate, ExchangeRateAmortization y RateAdjustmentAmortization; su respuesta incluye flujos, sensibilidad, TIR, MD, interés corrido, residual, amortización, emisor, moneda, fechas, ley, lámina mínima e ISIN. No se debe asumir que los parámetros opcionales tienen valores neutros: hay que probar la firma real del SDK en sandbox/read-only.

### Valores IOL del piloto

- Cotización: lot price USD 94,37; mejor bid 94,00 x 361; mejor ask 94,37 x 2.313; volumen nominal 115.035; volumen efectivo USD 108.330,34.
- Analytics de renta fija: dirty 95,90; clean 94,14274; accrued 1,75726; residual 85,72; current yield 8,0446%; TIR 2,3656%; MD 1,41596; seis flujos hasta 2029.
- La cotización y el trade_price analítico difieren USD 1,53, equivalente a 1,62% sobre la cotización. Clean vs lot price difiere aproximadamente 0,24%. Es una discrepancia de convención/instante que debe explicarse y registrarse, no una señal.
- Los flujos IOL suman aproximadamente 99,2254 por el residual informado. El informe anual sigue rotulando el cambio de precio como variación sin flujos; no lo trata como retorno total.

### Veredicto de comparación

**Datos: AMARILLO — gap potencialmente cubrible.** PPI e IOL ofrecen, según sus contratos/documentación, los campos necesarios para construir una ficha de ON. **Evidencia de esta especie: AMARILLO — no cerrada**, porque falta la respuesta autenticada PPI de YMCID y la conciliación de precios/fechas/unidades. **Operatoria: ROJO — no habilitar**, hasta obtener PPI live, igualar la convención de precio, validar flujos/ISIN y pasar los gates específicos.


### Simulación IOL con precio de mercado — 100 nominales YMCID

Consulta read-only realizada el 21-Sep-2026 a las 19:16 UTC aproximadamente mediante la herramienta de simulación de renta fija. No crea orden ni mueve fondos.

- Precio de mercado utilizado: dirty USD 94,37 por 100 nominales; clean USD 92,61274; interés corrido USD 1,75726.
- Fecha de liquidación: 22-Sep-2026; vencimiento: 2-Jul-2029.
- Nominales: 100; residual: 85,72; total a cobrar: USD 99,2254; interés total: USD 13,5054; amortización total: USD 85,72; ganancia estimada: USD 4,8554 (5,145% sobre inversión).
- TIR: 3,5109%; TEM: 0,2840%; tasa nominal anual: 3,4806%; current yield: 8,1751%; duración modificada: 1,39247.
- Los seis cashflows coinciden con los del analytics IOL anterior. La diferencia de TIR frente al analytics anterior (2,3656%) se explica por el precio de entrada de cálculo anterior (dirty 95,90) frente a dirty 94,37 en la simulación; debe registrarse el precio y timestamp junto al cálculo.
- Este resultado demuestra que IOL puede transformar cotización + contrato + flujos en un análisis de rendimiento total para YMCID. Falta repetir el mismo cálculo desde PPI Bond Estimate a igual precio, cantidad, fecha y parámetros, y verificar tolerancias.
- **Estado actualizado:** IOL para YMCID = VERDE en disponibilidad de datos; conciliación PPI/IOL = AMARILLO hasta evidencia PPI live; operabilidad RC6 = ROJO hasta integrar contrato, ledger de flujos, gates y ejecutor de renta fija.

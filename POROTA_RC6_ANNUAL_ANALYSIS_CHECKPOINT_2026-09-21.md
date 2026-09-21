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

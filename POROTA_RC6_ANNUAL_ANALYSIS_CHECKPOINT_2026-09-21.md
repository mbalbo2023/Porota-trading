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
- Inspección de source, diff y prueba funcional automatizada: pendiente.
- Tests ejecutados: no.
- PR: no creado.
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

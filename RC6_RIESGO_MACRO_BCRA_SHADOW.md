# RC6 — Riesgo macro BCRA en Shadow

## Propósito

El motor PAPER de Porota incorpora contexto macroeconómico argentino para
explicar y posteriormente evaluar las decisiones sobre **acciones y CEDEARs**.
No tiene autoridad para abrir, bloquear, dimensionar ni cerrar posiciones.

La integración se denomina **Shadow**: se calcula y persiste junto a un
candidato técnico que ya superó el umbral base, pero su resultado no modifica
la decisión Python ni llama al broker.

## Fuentes y actualización

El módulo `ad_macro_history.py` actualiza la caché local fuera de la ruta de
decisión. La tarea `maintenance_macro_refresh` corre diariamente a las 02:30
(Argentina), con TTL de 12 horas y timeouts de red cortos.

Fuentes actuales:

- **BCRA oficial**: reservas internacionales, base monetaria, TAMAR privada y
  dólar oficial.
- **Datos Argentina / INDEC**: IPC y actividad económica.
- **ArgentinaDatos**: series históricas de MEP, CCL y blue, como fuente
  complementaria de menor jerarquía.

La decisión nunca espera una API externa. Si una fuente falla, se conserva el
último dato válido o el estado explícito de falta de datos.

## Qué se calcula

Para cada serie disponible de los últimos 180 días se conserva:

- último valor y fecha;
- mínimo, máximo y promedio del período;
- tendencia simple: al alza, a la baja o estable;
- percentil del último valor dentro de su propia historia;
- fuente del dato.

También se calcula la brecha cambiaria a partir de series alineadas por fecha,
no dividiendo dos valores de días distintos.

## Cómo entra al motor

1. El motor admite sólo acciones y CEDEARs.
2. Evalúa señal técnica, precios, profundidad y economía.
3. Si la señal técnica supera el umbral candidato BUY, llama a
   `rc6_macro_risk_shadow.collect()`.
4. El adaptador lee exclusivamente la caché y persiste una versión compacta
   dentro de `features_json` de la operación PAPER.
5. El dashboard muestra el estado, fuente y política Shadow dentro del detalle
   de la operación.

No se consulta la red en el paso 3. Si la caché no está disponible, se registra
`UNAVAILABLE` o `INSUFFICIENT_DATA`, sin inventar un valor y sin bloquear.

## Límites explícitos

- **No es GDELT**. El repositorio no contiene todavía un adaptador GDELT
  verificable; por eso no se lo declara activo.
- No hace predicción de precio ni reemplaza el análisis técnico.
- No modifica stop, take-profit, Max Hold, End of Day, riesgo por operación ni
  exposición.
- No envía órdenes reales: todo permanece en `PRODUCTION_PAPER` y
  `SIMULATED`.

## Evolución prevista

GDELT sólo se podrá sumar después de definir una fuente, esquema, retención,
límites de tasa, mapeo de instrumentos y pruebas de point-in-time para impedir
look-ahead. Inicialmente también deberá ingresar como Shadow y compararse con
las decisiones y resultados PAPER antes de cualquier posible autoridad.

# RC6: datos de decisión, costos, liquidación y reportes

## Decisión en modo SHADOW

En cada evaluación el motor conserva la decisión baseline y calcula en paralelo una
decisión shadow con histórico PPI y velas 5m cerradas. La shadow usa solamente
datos disponibles hasta el instante de evaluación. No puede abrir, cerrar ni
modificar posiciones.

Se guardan:

- identidad completa del instrumento;
- fecha de corte;
- cobertura y cantidad de observaciones;
- calidad de las velas;
- tendencia histórica y momentum intradía;
- score baseline;
- score shadow;
- decisión baseline y decisión shadow;
- razón y versión del cálculo.

La promoción de shadow a binding requiere forward testing y backtesting sin
look-ahead bias, comparando falsos positivos, falsos negativos, costos, drawdown y
PnL contra el baseline.

## Costos e impuestos

El tarifario existente calcula por familia:

- comisión;
- derecho de mercado;
- IVA cuando corresponde;
- costos específicos de opciones;
- costo anualizado de cauciones;
- bonificación intradiaria cuando el contrato y la sesión la habilitan.

Los impuestos personales o fiscales no pueden inferirse del precio de una operación.
Ganancias, IIBB, Bienes Personales y otros conceptos dependen del sujeto, residencia,
instrumento, período fiscal y normativa vigente. Si se define una tasa explícita,
el motor la calcula solamente como diagnóstico shadow. Si no existe, informa
UNKNOWN y no inventa una tasa.

## Liquidaciones pendientes

Una venta permanece pendiente hasta que la disponibilidad esté respaldada por
available_at o por la política PAPER conservadora habilitada para T+1. El dashboard
debe diferenciar:

- DISPONIBLE;
- PENDIENTE_CONFIRMACION;
- T+1 EN ESPERA;
- INCONSISTENTE o REQUIERE_CONCILIACION.

Una liquidación pendiente no implica necesariamente un error: puede ser el
comportamiento normal del settlement. Sí es un blocker si falta identidad,
plazo, fecha disponible o conciliación con el fill.

## Take Profit

El disparador actual observa el bid, no el last:

1. valida que el libro sea fresco y compatible;
2. comprueba bid >= target_price;
3. exige profundidad suficiente para la participación simulada;
4. registra la intención;
5. ejecuta el cierre simulado;
6. verifica que el ledger quede CLOSED o EXIT_PARTIAL.

La capa shadow adicional calcula el resultado neto esperado después de costos
conocidos y, si existe, la tasa fiscal configurada. No cambia todavía el
disparador. Esto permite comprobar si el target bruto realmente produce un
resultado neto satisfactorio.

## Retención de reportes

La política propuesta es:

- reportes diarios: sólo jornadas lunes a viernes;
- al terminar la semana: consolidar diarios en un reporte semanal;
- al completar cuatro semanas: consolidar en un reporte mensual;
- conservar el consolidado y comprimir los reportes fuente;
- nunca borrar evidencia sin verificar hash, tamaño, período y éxito del
  consolidado;
- conservar un índice de reportes y la relación entre fuente y consolidado.

La compresión y el borrado deben ser ejecutados por un job idempotente, fuera del
motor de trading, con estado visible en el dashboard. Hasta que ese job exista y
pruebe restauración, no se deben eliminar los diarios.

## Seguridad

Todo el circuito continúa en PRODUCTION_PAPER/SIMULATED, con
real_orders_sent=0. El workflow de cambios y despliegue es el RC6 canónico; no se
usa deploy.yml ni se reinicia el bot genérico.

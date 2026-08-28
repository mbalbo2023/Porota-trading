# Resultado del preflight del ledger anterior — 28/08/2026

## Evidencia y alcance

JSON aportado por el operador, no una inspección SSH desde desarrollo.
Diagnóstico `v17-ledger-preflight-3`, generado a las 20:50:29.831603 UTC,
completado a las 20:50:30.306641 UTC. Estado `OBSERVED_REVIEW_REQUIRED`,
etapa `DONE`, modo `TEMPORARY_COPY`, Python 3.11.16 / SQLite 3.46.1.

Origen legible de 9.187.328 bytes con encabezado WAL; auxiliares ausentes.
Copia con hash concordante y dos relecturas del origen. Copia y contenedor
temporal eliminados; motores detenidos y reinicio deshabilitado antes/después.
Sin red ni credenciales montadas. El informe declara original no modificado,
sin migración y `promotion_allowed=false`.

## Resultado observado

| Control | Resultado |
| --- | --- |
| Posiciones / fills | 10 / 16 |
| Estados agrupados | 6 CLOSED, 3 OPEN, 1 UNKNOWN |
| Concordancia aritmética bajo hipótesis legacy | 9 |
| Inconsistencias detectadas | 1, SPOT_LEDGER_INCONSISTENT |
| Ejemplo | Ordinal 4, no ID de posición |
| Tablas paper_spot_sales / paper_sale_receivables | Ausentes, no vacías |
| Cierres sin recibo | 6 |
| Proyección sólo en memoria | ARS / BYMA / LEGACY_ASSUMED_ARS |

## Interpretación que permite el código

- La lectura se completó; no corresponde repetir la revisión 2 ni diagnosticar
  de nuevo el bloqueo SQLite anterior. El ZIP operativo se entregó con nombre
  distintivo `porota_ledger_preflight_v17_r3.zip`; su código no cambió.
- `UNKNOWN` es la agrupación de cualquier estado diferente de OPEN/CLOSED.
  El informe no revela el valor original ni su motivo. No se puede llamarlo
  cancelación, cuarentena, cierre o ausencia de exposición.
- `cd_spot_ledger.partition` rechaza todo estado no admitido antes de validar
  sus términos económicos. Con una única posición UNKNOWN y una única
  inconsistencia, el código permite inferir que corresponden al mismo registro.
  Puede haber otros problemas en esa fila ocultos por el primer rechazo.
- El código base 16.3.5 aportado sólo inserta OPEN y actualiza a CLOSED. No hay
  evidencia suficiente para atribuir el otro estado a una intervención manual
  o a un bug concreto. No se reemplaza por OPEN/CLOSED por conjetura.
- Las dos tablas ausentes fueron añadidas en v17: no se demuestra pérdida de
  seis recibos que antes hubiesen existido. Su ausencia tampoco acredita fondos.
- Nueve filas concordantes no significan nueve operaciones reales validadas:
  son registros PAPER, con moneda/plaza asumidas y controles de salida contra
  entradas almacenadas. No se certifican compras, tarifas ni saldo de PPI.

## Ensayo de regresión, sólo con datos ficticios

`tests/test_legacy_observed_shape_v17.py` reproduce los conteos/estructura,
no las filas del servidor. Usa identidades e importes inventados expresamente
para pruebas. Verifica lectura por copia sin alterar origen, descarte del
estado crudo y mantenimiento de los bloqueos.

El ensayo del constructor actual `PaperStore` demuestra que su migración
aditiva puede crear seis recibos con calendario PAPER y etiquetar moneda
LEGACY_ASSUMED_ARS. Eso NO es una conciliación ni una autorización para correr
ese constructor sobre la base del servidor. El estado no reconocido se
conserva; caja continúa bloqueada y supervisión separa tres abiertas de una
inconsistente. Corregir hipotéticamente sólo el estado tampoco certifica
el historial ni elimina las hipótesis de moneda o los recibos faltantes.

## Decisión del operador, sin cambios en el servidor

Recomendación: conservar la base anterior intacta y preparar para v17 una
base PAPER independiente, sin trasladar caja, posiciones ni aprendizaje
no verificados. El historial anterior sigue disponible para conciliación;
no se borra, repara ni convierte un estado desconocido en un cierre.

El operador respondió «Continúa Según tu criterio» y autorizó preparar esta
separación. Implementación: [PAPER v17 independiente](V17_PAPER_SEPARACION.md).
No supone arrancar v17, validar el resto del sistema ni desplegar. No se
autoriza por ello reescribir registros del original ni cambiar la base activa
del servidor. La base nueva no se creó en el Droplet durante el desarrollo.

Alternativa si se exige continuidad de las diez posiciones: obtener evidencia
del estado original y su procedencia, contrastar términos/entrada de esa fila
y definir explícitamente las hipótesis contables y recibos de la migración.
No basta con corregir un texto ni descartar la fila. No se solicita repetir
el mismo diagnóstico agregado, que ya cumplió su objetivo.

Cauciones siguen sólo colocadoras, con caja liquidada libre y misma moneda;
la confirmación de términos de PPI sigue pendiente por el canal del operador.
PR #3 permanece en borrador, sin merge, despliegue ni arranque del servidor.

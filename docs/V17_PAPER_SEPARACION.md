# PAPER v17 independiente — preparación autorizada

El operador autorizó continuar con esta separación después de recibir el
[resultado del ledger anterior](V17_LEDGER_RESULTADO_20260828.md).
Se prepara código/configuración; no se ejecutó en el Droplet, no se migraron
datos ni se habilitó el arranque. PR #3 sigue en borrador.

## Identidad y rutas

| Componente | Ruta por defecto en el servidor |
| --- | --- |
| Historial anterior, sin importar | `/opt/porota-trading/data/observer/observer_production.db` |
| Base nueva | `/opt/porota-trading/data/paper_v17/observer_v17.db` |
| Informes y lecciones exportadas | `/opt/porota-trading/data/paper_v17/artifacts/observer_v17.db/reports` |
| Backups de la nueva base | `/opt/porota-trading/data/paper_v17/artifacts/observer_v17.db/backups/paper` |

`cg_paper_workspace` centraliza la selección. La variable nueva es
`PAPER_V17_DB_PATH`; el default usa `DATA_DIR/paper_v17/observer_v17.db`.
La variable anterior `PAPER_DB_PATH` se ignora: no es una alternativa cuando
falta el archivo nuevo. El selector transmite una misma ruta absoluta al
dashboard y al runtime; los hijos la heredan aunque cambien de directorio.

Los artefactos se separan también por nombre de base para que dos datasets
en la misma carpeta no sobreescriban sus informes/backups. No se buscan
reportes en carpetas viejas ni se copian históricos, aprendizaje, catálogo,
velas, decisiones, posiciones, cauciones, outbox, saldos o recibos anteriores.
El catálogo/mercado nuevo requerirá sus propias lecturas cuando se autorice
iniciar el sistema. Este cambio no realiza esas lecturas.

## Inicio nuevo, no reinicio contable

- Un `PaperStore` creado sobre un archivo inexistente registra identidad
  `POROTA_PAPER_V17_FRESH`, UUID, fecha y origen `FRESH_EMPTY`, sin importación.
  El constructor de bajo nivel conserva soporte de pruebas/migraciones
  antiguas, pero **no marca un archivo preexistente como recién nacido**.
- Las entradas de producción —reloj, escáner, workers y prueba de salud— usan
  `runtime_store`. Antes de migrar/abrir en escritura un existente exigen su
  identidad y capital inicial registrados. Rechazan bases sin marca, corruptas,
  identidades distintas o archivos sólo preparados mediante el API de pruebas.
  No vacían, reemplazan ni reparan automáticamente un archivo rechazado.
- Las rutas conocidas del libro anterior y alias de archivo se rechazan antes
  de abrir SQLite. No se modifica el archivo anterior ni se necesita montar
  su contenido para construir el nuevo. El lock de inicialización sólo coordina
  inicializadores v17, no modificaciones externas/manuales.
- Un inicio incompleto que dejó archivo sin identidad/capital no se presenta
  como éxito. Requiere revisión; no se recrea sobre él para sortear el problema.
- Reabrir un dataset válido conserva UUID, posiciones y aprendizaje propios.
  Dos inicializadores concurrentes comparten el mismo dataset, no crean dos
  libros. La exclusión del reloj ya existente sigue aplicándose por archivo.

La marca de origen es evidencia de inicialización, no una certificación
financiera o detección de cualquier manipulación/copia posterior. Los controles
de ledger, riesgo, precios y disponibilidad siguen siendo necesarios.

## Capital exclusivamente ficticio

El primer inicio usa la configuración PAPER, no consulta cuentas PPI. Se
conservan los defaults del modelo: ARS 1.000.000; USD, USD_MEP y USD_CCL cero,
o los valores PAPER explícitos del operador. Son presupuestos de simulación,
no una declaración de su patrimonio o dinero disponible real.

Los cuatro importes se registran juntos al crear el dataset. En un reinicio,
un cambio de capital se rechaza antes de iniciar procesos/llamadas externas;
las representaciones decimales equivalentes no se consideran cambios.
Esto impide recalcular retroactivamente la caja cambiando una variable.
Otra base/capital requiere una decisión explícita de continuidad; no hay reset
automático ante un error. Riesgo y presupuesto diario siguen sus propios controles.

## Lectura y presentación

El dashboard verifica identidad antes de mostrar el libro, incluyendo el
endpoint de asignaciones de caución. No crea archivos ni vuelve a leer la base
vieja si falta la nueva. La falta de datos sigue siendo `s/d`/no disponible,
no un resultado cero certificado. La banda de modo distingue el historial
PAPER v17 independiente. Los avisos de modo preparados incluyen esa separación;
no se enviaron mensajes reales al desarrollar/probar.

## Imagen candidata y límite de despliegue

Selector y Compose apuntan a `porota-trading-bot:17.0.0-rc1`; no reutilizan el
tag de la imagen 16.3.5 instalada. El selector exige la imagen local y no hace
pull ni fallback a la anterior. La etiqueta RC no declara v17 lista.

No ejecutar el selector ni Compose en el servidor como parte de este cambio.
Todavía falta un plan de promoción con validación integral y preparación del
directorio nuevo con el usuario correcto. En particular, el init de permisos
del Compose legado recorre `data`: no es un procedimiento de despliegue aislado
aprobado para preservar el historial. Debe resolverse antes de dar un comando
de instalación. No se cambian ahora permisos del Droplet.

Pruebas: variables antiguas ignoradas, rechazo de rutas/alias/identidades,
capital inmutable, dos procesos concurrentes, dashboard sin fallback,
directorios de artefactos distintos y un worker de velas realmente iniciado y
detenido localmente con la base nueva, sin tocar el archivo legado ficticio.
Las fixtures no son registros reales. El smoke general del CI no certifica
fuentes PPI, cauciones reales ni todo el flujo PRODUCTION_PAPER.

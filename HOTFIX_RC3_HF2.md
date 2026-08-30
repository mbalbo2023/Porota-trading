# Porota Trading 17.0 RC3-HF2

## Dictamen ejecutivo

RC3-HF2 es un hotfix defensivo para simulación productiva. No cambia la señal,
el score, el stop, el objetivo, el tamaño de posición, la política económica ni
la política de IA. No incorpora capacidad de órdenes reales.

El hotfix resuelve tres silencios operativos:

1. Verifica que las ocho identidades prioritarias existan realmente en el
   catálogo PPI con tipo, liquidación y capacidad `READY_PAPER_SPOT`.
2. Separa la factibilidad de muestreo del foco y del universo rotativo.
3. Cuenta las aperturas simuladas realizadas pese a un rechazo económico en
   modo `SHADOW` y las etiqueta como validación de pipeline, no rentabilidad.

Además, activa ingesta histórica PPI incremental fuera de rueda con TTL de seis
horas por intento. No consulta `current` ni `book` fuera de rueda y no presupone
una garantía contractual 7x24 de PPI: trabaja mientras el login y History
respondan; ante error espera el TTL y no genera una tormenta de llamadas.

## Decisiones sobre la reauditoría

| Hallazgo | Decisión | Fundamento categórico |
|---|---|---|
| HF-01 Backup retirado del deploy | Aceptado con alcance corregido | No se hará backup previo al deploy. Se conserva el backup SQLite diario persistente y se agrega una herramienta general del host independiente de Docker y del despliegue. |
| HF-02 Economía falla pero SHADOW abre | Aceptado | Es intencional para validar el circuito, pero era insuficientemente visible. Se agregan contadores de evaluadas, aprobadas, fallidas y abiertas pese al fallo. No se activa `BINDING`. |
| HF-03 Solo ocho instrumentos reúnen muestras | Aceptado parcialmente | Los ocho son prioritarios, no el único universo. La rotación existe, pero puede carecer de cadencia. Se calcula por separado y deja de mostrarse un verde global engañoso. |
| HF-04 Foco hardcodeado puede quedar vacío | Aceptado | Una coincidencia exacta ausente puede vaciar el foco silenciosamente. Menos de cuatro identidades bloquea nuevas aperturas simuladas; ingesta y cierres continúan. |
| HF-05 Redondeo económico con una unidad | Aceptado, diferido | Es real para precios bajos, pero no es bloqueo del pipeline del lunes. Se corregirá con cantidad candidata o cálculo porcentual sin redondeos intermedios. |
| HF-06 Versionado/documentación | Aceptado, parcialmente resuelto | HF2 tiene identidad única. El saneamiento genérico de documentación histórica y CI se hará luego de estabilizar la rueda. |
| HF-07 Pulso Porota fue agregado en hotfix | Observación válida, sin cambio | No hay evidencia de defecto. Si afecta el dashboard será el primer componente accesorio en desactivarse. |
| HF-08 IA apagada sin evidencia de aporte | Aceptado | La IA continúa completamente fuera de la rueda. Una evaluación batch posterior al cierre podrá sugerir hipótesis sin cambiar parámetros ni autorizar operaciones. |

## Correcciones a la propia reauditoría

- `candidate_universe` no tiene la columna `capability`; pertenece a
  `financial_instrument_catalog`.
- `trade_gate_evaluations` almacena `detail_json`, no `detail`.
- Los comandos con `sudo` interactivo no son aceptables para este despliegue;
  se usará siempre `sudo -n`.
- OSC52 sí devuelve la salida al portapapeles en el entorno Termius validado.
- La suite final sí es reproducible dentro de la imagen: HF1 aprobó 1.417
  pruebas y HF2 debe aprobar su suite ampliada antes de desplegarse.
- La primera ejecución Docker de HF2 expuso que OverlayFS puede no reflejar a
  tiempo una reescritura del mismo tamaño en sus metadatos. Se rechazó omitir la
  prueba: el preflight ahora relee el origen y compara SHA-256, además de inode,
  tamaño y tiempos. HF1 permaneció activo y HF2 nunca llegó a activarse.
- La primera ejecución del backup general posterior encontró una carrera de
  archivos SQLite `-shm` efímeros durante el recorrido recursivo de staging.
  HF2 permaneció operativo: se eliminó la recursión y el tar ahora incorpora
  exclusivamente la lista exacta de archivos copiados y verificados; WAL/SHM
  quedan categóricamente excluidos.
- El diagnóstico posterior detectó que `data/backups` conservaba copias antiguas
  con archivos `.env`. El backup general excluye ahora copias previas,
  `restore_test` y cualquier `.env` anidado. Solo `--include-secrets` puede
  incorporar el `.env` actual y nunca arrastra credenciales históricas.

## Operación del foco

Estados de cobertura:

- 8/8: verde, configuración declarada completa.
- 4-7/8: amarillo, funcionamiento degradado permitido únicamente en PAPER.
- 0-3/8: rojo; se bloquean nuevas aperturas. Las posiciones abiertas pueden
  cerrar normalmente y la ingesta continúa.

La identidad requiere simultáneamente ticker, tipo, liquidación, estado
`AVAILABLE` y capacidad `READY_PAPER_SPOT`. Un ticker con otra liquidación no
se acepta como equivalente.

## Ingesta PPI en background

### Qué se incorpora

- Catálogo completo una vez por fecha local.
- Lotes históricos rotativos de hasta 40 instrumentos cada seis horas fuera de
  rueda.
- TTL por intento, no por éxito: un fallo también inicia el período de espera.
- Persistencia de filas válidas, intentos, cobertura y causa de error.
- Current/book y estrategia continúan estrictamente limitados a rueda abierta.

No se encontró una publicación oficial de PPI que garantice SLA 7x24 o cuota
ilimitada para esta API. Por eso el diseño no afirma disponibilidad: aprovecha
la ventana cuando responde y degrada de forma segura cuando no responde.

## Economía bursátil

PPI publica una comisión online de 0,6% más IVA para acciones y CEDEAR. Si se
compra y vende el mismo activo en el día, bonifica los aranceles de la operación
de menor valor, solo en mercado local, con misma moneda y liquidación; excluye
derechos de mercado y su IVA, y aplica el beneficio al cierre.

Fuente: https://www.portfoliopersonal.com/Contenido/comisiones

BYMA informa cinco puntos básicos para renta variable: tres de negociación y
dos de post-trade.

Fuente: https://www.byma.com.ar/newsroom/aranceles-de-rv

Consecuencia: la bonificación no debe descontarse anticipadamente del riesgo o
la caja. En un hotfix posterior se registrarán costo conservador completo y
bonificación estimada por separado, seguida de conciliación contra PPI.

No se cambia ahora stop 2% ni objetivo 3,5%. La propuesta 1,5%/4,5% mejora una
relación algebraica, pero no demuestra tasa de acierto, frecuencia del objetivo
ni rentabilidad. Requiere replay, backtest fuera de muestra y PAPER.

## Backup general fuera de Docker

`./data:/app/data` es un bind mount: los datos escritos por el contenedor
persisten en el filesystem del host. Docker documenta ese comportamiento en:

https://docs.docker.com/engine/storage/bind-mounts/

El backup diario de la base PAPER ya usa la API online de SQLite, compresión,
SHA-256, restauración temporal y `PRAGMA quick_check`. SQLite documenta que su
Online Backup API produce una instantánea consistente de una base activa:

https://www.sqlite.org/backup.html

HF2 agrega `scripts/v17_host_general_backup.py` para generar, en un evento
separado, un archivo general bajo `/opt/porota-backups`. No usa Docker y no es
un paso previo al deploy. Incluye datos persistentes, índice SRE reconstruible,
versión, Compose y manifiesto. Los secretos quedan excluidos por defecto; solo
se incluyen con `--include-secrets` y el archivo final queda en modo 600.

Un backup en el mismo Droplet protege contra errores de contenedor o aplicación,
pero no contra pérdida total del servidor. La copia fuera del Droplet requerirá
definir después un destino remoto cifrado.

## Plan del domingo 30 de agosto de 2026

### D1. Suite local y contrato del artefacto

- Se espera: todas las pruebas aprobadas; solo skips ya conocidos.
- Se analiza: cobertura, cierres con bloqueo activo, rotación, SHADOW, ingesta,
  identidad de versión y ausencia de órdenes.
- Si falla: no se construye ni despliega; se corrige el código y se reinicia la
  suite completa.

### D2. Construcción de imagen HF2

- Se espera: imagen `porota-trading-bot:17.0.0-rc3-hf2` construida una sola vez.
- Se analiza: versión interna, usuario no root, filesystem read-only y suite
  ejecutada dentro de esa misma imagen.
- Si falla: la imagen se descarta; HF1 continúa activo sin cambios.

### D3. Activación sin backup previo

- Se espera: reemplazo de observer/dashboard por HF2, modo
  `PRODUCTION_PAPER`, IA OFF y cero órdenes reales.
- La activación se ejecuta con `scripts/v17_rc3_hf2_deploy.py`: el proceso es
  compatible con `nohup`, no pide contraseña (`sudo -n`), conserva el log y
  genera `v17_rc3_hf2_deploy_result.json`, aunque se cierre Termius.
- HF1 continúa operativo durante la construcción y las pruebas de HF2. Si una
  validación crítica falla después de activar, el script intenta volver al
  commit y a la imagen exactos de HF1.
- Se analiza: health, versión, contenedores, manifiesto de modo y logs de
  arranque.
- Si falla: se detiene la activación y se conserva o restaura HF1 usando la
  imagen ya existente. No se toca la persistencia.

### D4. Validación fuera de rueda

- Se espera: PPI auth y catálogo responden si el servicio está disponible;
  market data vivo figura NO_APLICA; foco y muestreo quedan calculados.
- Se analiza: foco 8/8, identidades faltantes, histórico cubierto, último
  intento y próximo lote.
- Si foco queda 0-3: HF2 bloquea aperturas automáticamente. Se corrige la
  correspondencia de catálogo; nunca se inventa una identidad.
- Si PPI no responde: se conserva el estado degradado y el cooldown; no se
  aumenta la frecuencia.

### D5. Backup general separado

- Se ejecuta después de validar HF2, nunca antes del deploy.
- Se espera: archivo externo a Docker, SHA-256, manifiesto y quick_check OK.
- Si falla: se corrige únicamente el proceso de backup. No se reinician ni se
  alteran los contenedores operativos.

## Plan del lunes 31 de agosto de 2026

### L1. Preapertura

- Se espera: `READY_PREOPEN`, auth PPI verde, catálogo disponible, foco al menos
  4/8, órdenes reales cero e IA OFF.
- Se analiza: hora local, calendario BYMA, antigüedad de datos y capacidad
  teórica de seis muestras.
- Si foco <4, auth falla o existe cualquier capacidad de órdenes: no se habilita
  la simulación de aperturas. Se diagnostica sin cambiar la estrategia.

### L2. Primeros 30 minutos de rueda

- Se espera: current/book únicamente para el lote seleccionado, cotizaciones
  frescas, sin puntas cruzadas y sin errores superiores al 10%.
- Se analiza: latencia por símbolo, rechazos de datos, rotación y estabilidad de
  memoria/SQLite.
- Si hay 401/403: se respeta cooldown y se investiga autenticación. Si hay datos
  viejos o ambiguos: se rechaza el instrumento; no se relajan los controles.

### L3. Minuto 90

- Se espera: los instrumentos de foco observados deben haber alcanzado al menos
  seis muestras; la rotación se mide por separado.
- Se analiza: muestras reales por identidad versus estimación.
- Si el foco no llega: no se modifica durante la rueda. Para la sesión siguiente
  se evalúa reducir lote, ampliar ventana o cambiar intervalo con una sola
  variable por vez.

### L4. Primeras señales BUY

- Se espera: cada señal persiste técnica, economía, patrimonio, liquidez y
  resultado. SHADOW puede abrir de manera simulada aunque economía falle.
- Se analiza: relación neta, breakeven, causa patrimonial, spread, profundidad y
  cantidad.
- Si 100% falla economía: no se cambia stop/target ese día. Se conserva como
  evidencia para el modelo de bonificación y el backtest.

### L5. Cierres y fin de rueda

- Se espera: cierres por objetivo, stop o tiempo exclusivamente PAPER; fills de
  compra/venta simulados; órdenes reales cero.
- Se analiza: costos completos, slippage, PnL por moneda, duración y consistencia
  del ledger.
- Si hay inconsistencia contable: se congela toda nueva apertura en la sesión
  siguiente hasta reconciliar. No se reescriben resultados históricos.

### L6. Ingesta posterior al cierre

- Se espera: la estrategia se detiene; históricos, noticias, macro, reportes y
  backup diario continúan según sus TTL.
- Se analiza: cobertura incremental, errores PPI y ausencia de current/book.
- Si el histórico falla: se espera el TTL; el fallo no invalida por sí solo las
  cotizaciones en vivo observadas durante la rueda.

## Evolución tras las primeras ruedas

1. Ruedas 1-3: validar pipeline, datos, contabilidad y guardas. El PnL no se usa
   todavía para declarar rentabilidad.
2. Con evidencia: implementar bonificación PPI en SHADOW, con emparejamiento y
   conciliación al cierre.
3. Replay/backtest: comparar parámetros con costos conservadores y bonificados,
   sin optimizar y evaluar sobre el mismo período.
4. PAPER adicional: promover un cambio solo si mejora expectativa, drawdown y
   estabilidad fuera de muestra.
5. IA postcierre opcional: una evaluación agregada de lecciones; nunca por
   instrumento ni con autoridad operativa.

## Criterio de salida a dinero real

HF2 no habilita ni aproxima una salida a dinero real. Esa decisión exige, como
mínimo, datos suficientes, conciliación PPI, economía neta positiva, backtest
fuera de muestra, PAPER estable y una autorización separada. Ninguna cantidad de
pruebas técnicas permite prometer cero errores; este plan sí cierra los errores
lógicos conocidos y hace visibles los supuestos restantes.

## Evidencia de verificación local

- Suite completa: 1.431 pruebas aprobadas.
- Pruebas omitidas: 4, ya marcadas por depender de condiciones externas.
- Compilación Python de los módulos modificados: correcta.
- Construcción Docker: debe ejecutarse y repetirse dentro de la imagen final en
  el Droplet, porque el entorno de auditoría local no dispone del daemon Docker.

# Porota RC6 · Matriz de migración a IOL para la rueda PAPER

La [revisión de release PAPER](MIGRACION_IOL_OFFHOST.md#revisión-de-release-paper-del-26112026) usa la rama `fix/rc6-iol-paper-release-20261126`. Las tablas de abajo preservan la línea base del comparador. El recorrido sintético y el estado de mercado tendrán recibos independientes del SHA, árbol e imagen instalados.

Corte del 26/11/2026 ART. Rama aislada del [PR #1287](https://github.com/mbalbo2023/Porota-trading/pull/1287), base de código productiva `a6cc6e3ddcb18cde1e4aeb87752212c5bf925d36`. Actions #5 acreditó 9.263 regresiones, 347 focales, 16 disparos de verificación de timers y `real_orders_sent=0` en la imagen del comparador `1669b5c3f693eeb800290ea20bf7107a4be8faac`. El corte físico heredado es el release #212, fuente `414771d203cf1d0bff4e00c803ef722309dca064`. La última sonda #537 citada es de las 16:49 ART y observó el sistema PPI. Deploy V2 está bloqueado. [Artefacto del comparador](https://github.com/mbalbo2023/Porota-trading/actions/runs/45894680236/artifacts/15153208814) · [Evidencia física y RCA de PPI](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7202160360) · [Candidato auditado y recibos](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7203257656)

**Verde** significa que se puede conservar el componente y que su función indicada tiene prueba aplicable. **Amarillo** indica integración parcial o una comprobación pendiente con el proveedor, el runtime o la tarea completa. **Rojo** identifica una precondición para rueda incumplida o un bloqueo efectivo. El ámbito con cobertura física pendiente queda **NO_VERIFICADO**. La calificación integral de rueda permanece **INVIABLE**.

## Plano de control, runtime y capturas

| Artefacto | Migración | Evidencia del estado | Solución y aceptación |
| --- | --- | --- | --- |
| `porota-policy.yaml`, perfil PAPER y bloqueo de órdenes | Verde | El guard y el smoke de la imagen bloquean rutas reales y leen el contador desde SQLite. La interfaz IOL de mercado admite ficha, cotización y profundidad. | Reusar el perfil. Medir `real_orders_sent=0` en cada entorno y rechazar los métodos de órdenes previo al transporte. |
| Marcador `rc6-iol-testing-only.json` y workflow aislado | Verde | El veto se prueba en clasificación, paquete y promoción. El runner hospedado usa la imagen sin red. | Mantener el candidato de pruebas con su marcador y publicar evidencia por SHA, árbol e imagen. |
| CI gobernada, Dockerfile, lockfiles, manifest y verificador de bundle | Verde | El comparador ejecutó toda la suite dentro de la imagen exacta. El job de cobertura de rueda quedó rojo. | Reusar build once y recibos inmutables. Atar los ensayos del perfil IOL a SHA, árbol y digest. |
| Predeploy V2, Promote V2 y rearmado PAPER | Rojo | Veto de release vigente en la rama aislada. Promoción pendiente de autorización. | Integrar un solo candidato después de la calificación. Ejecutar preflight y promover una vez el mismo paquete sólo con autorización. Confirmar postflight y seguridad PAPER. |
| `compose.rootless.yml`, host, volúmenes, contenedores observer/dashboard | Amarillo | Perfil rootless RC6 y validación de imagen disponibles. Faltan montaje, custodia y salud del perfil IOL integral. | Arrancar instancia desechable con volúmenes sintéticos y dependencias simuladas. Acreditar identidad y salud de cada contenedor. |
| Supervisor `bv_paper_runtime.py`, arranque/esquema de los hijos | Amarillo | El supervisor actual define ocho roles, dos opcionales. Los puntos de lectura de scanner y EXIT aceptan el adaptador IOL aislado. El arranque integrado sigue configurado para PPI. | Inyectar backend y registro desde perfil offhost explícito. Probar alta, revocación y recreación de roles y una sola instancia escritora. |
| OAuth IOL y `iol_rest_readonly_rc6.py` | Amarillo | Cliente REST probado con respuestas de prueba y custodia privada. #212 acreditó una consulta REST de referencia. | En la cuenta de prueba verificar renovación, 401/403, recuperación y permisos de mercado. Mantener el token dentro del owner. |
| Gateway AF_UNIX, roles, generación y recibos IOL | Amarillo | #5 acreditó `AF_UNIX_PEERCRED` para PPI e IOL y un cliente EXIT separado. El candidato agrega un recibo IOL por endpoint y consumidor. | Cablear fábrica y registro desde el supervisor de pruebas. Acreditar arranque, muerte y recreación del dueño y recuperación sin sesiones PPI del producto. |
| Cuota, 429 y prioridades | Rojo | El gobernador de prueba cuenta OAuth y GET y frena lecturas tras 429. Falta acreditar cuota y reserva EXIT de la cuenta y arbitraje entre procesos. | Unificar recolector, scanner, EXIT, salud y scalping en un dueño de cuota. Medir tasa, latencia de recepción, espera en cola y capacidad de salida en sesión. |
| WebSocket IOL y volumen intradiario | Rojo | Contrato público de suscripción disponible. Falta el adaptador que produzca recibos y recupere huecos. | Implementar suscripciones desde el owner. Probar continuidad de trades, volumen, recuperación, deduplicación y equivalencia con observaciones REST. |
| `financial_instrument_catalog` y `candidate_identity_v2` | Amarillo | PPI conserva la autoridad primaria de identidad. La ficha y el par IOL de prueba cruzan los cinco campos. La autoridad PPI guardada vence a los 14 días por defecto. | Versionar y renovar equivalencias por clave exacta antes de que caduquen. Cotejar moneda, plaza y plazo explícito. Revocar cuando el origen no acredite la variante. Publicar el censo por familia. |
| Preapertura, calendario BYMA y recibo PAPER | Amarillo | Se ejercita el recibo efectivo con tiempo y release de prueba. La autoridad operativa sigue ligada al flujo PPI. | Validar el proveedor efectivo en el perfil aislado manteniendo pausa, rearmado y día de rueda. Probar HOLD ante fuente caída y GO con el candidato exacto. |
| Worker scanner `bf_production_paper_observer.py` | Amarillo | GGAL usa selector real, misma clave, entrada IOL y calentamiento de prueba. Otras ramas de descubrimiento y los recibos nativos aún son PPI. | Cablear el backend desde el supervisor. Comparar COLD, WARM, HOT, READY, BUY y fill por familia en la misma ventana y clave. |
| Worker EXIT y `bm_exit_supervisor.py` | Amarillo | El piloto spot usa profundidad IOL con identidad y reloj propios. El cierre auditado de Bonos/ON conserva el testigo PPI. | Reusar vigilancia y cadencia prioritaria, incluso sin trade reciente. Acreditar el recibo causal nuevo antes de activar renta fija con IOL. |
| Worker `dynamic_shadow` y `rc6_shadow_runtime` | Amarillo | El replay prospectivo pasa DISCOVERY, WARM y HOT. El lector identifica IOL y las muestras sin origen quedan sin verificar. IOL no puede completar el calentamiento del plan PPI. | Calificar un plan propio con recibos IOL tipados. Mantener SHADOW, huella de capacidad, calificación de negociabilidad, causalidad y deduplicación. |
| Worker Scalping `cf_intraday_scalping.py` | Rojo | Motor y guardas de rango/costo probados. La ruta IOL depende de trades y volumen intradiario acreditados. | Inyectar trades del dueño IOL. Medir ventana, rango neto, volumen y cierre en SHADOW y PAPER aislado. |
| Worker Cauciones `di_caucion_cash_sweep_runtime_hf6.py` | Rojo | Colocación y liquidación pasan en replay. La rueda requiere libro colocador cualificado. | Obtener TNA, monto, reloj, lado y vencimiento por variante. Probar reserva, riesgo y liquidación idempotente por la ruta normal. |
| Worker velas `bl_candle_engine.py`, cola, `candle_series`, `candle_versions` | Amarillo | El materializador genera 1m/5m `SAMPLED` con origen IOL y volumen ausente. El verificador IOL seleccionado comprueba recibos en ambas resoluciones. | Integrar su recibo en el perfil IOL de pruebas y en la condición de preopen correspondiente. Acreditar completitud, reloj de trade y unidad de volumen antes de promover una serie completa. |
| Worker notificaciones `bn_telegram_bus.py` y outbox | Amarillo | #537 acreditó ACK y recuperaciones con cobertura escasa. #1289 tiene un fix validado en su propia rama. | Reusar las transiciones compatibles y conectar recuperación a estado IOL. Reproducir 429, caída y recuperación en outbox aislado. [RCA de #1289](https://github.com/mbalbo2023/Porota-trading/pull/1289) |
| SQLite operativo, libro PAPER, caja, riesgo e integridad | Verde | El artefacto compara WAL, integridad, persistencia y política real bloqueada en imagen aislada. | Preservar libro y esquema. Verificar reinicio, riesgo concurrente y liquidaciones con la nueva fuente, con contadores leídos de DB. |
| Recibos PIT, ACK, Book1/Book2 y firma de EXIT | Rojo | Bonos/ON y revocación pasan con política y firma de prueba. El testigo nativo es PPI y el pin vive fuera del proceso scanner. | Transferir la autoridad pública y su generación por el canal autenticado del supervisor. Adaptar el origen del recibo a IOL y probar entre procesos bajo lock y con el primer Book posterior a ACK. |
| Históricos `history_versions_v2` y `history_canonical_v2` | Verde | El replay conserva consulta `as_of`, hashes y datos previos al anexar referencia IOL. | Fijar corte y reconciliar checksum, recuentos por partición y consultas `as_of`. Mantener identidad de base, fuente y versión. |
| Importador IOL, cierre EOD y nueva serie canónica | Amarillo | `bars` entra como referencia separada e idempotente. El cierre EOD archivó dos velas IOL y la repetición importó cero en SQLite temporal. | Acreditar variante, plazo, base de precio y huecos posteriores al corte. Anexar sólo particiones calificadas e informar diferencias. |
| Certificación de fuente postcierre y publicación del reporte | Rojo | El archivo EOD IOL queda `LOCAL_EOD_READY`. Snapshot, revisión y reporte de decisiones siguen `PROVISIONAL_POSTCLOSE_SOURCE_PENDING` con PAPER seguro. El verificador exige un recibo diario RAW PPI. | Incorporar un recibo diario IOL por identidad exacta, sesión, versión, cohorte y dataset después de calificar las barras diarias. El snapshot, la revisión y el reporte deben coincidir en el mismo recibo. |
| Dashboard, API, reportes, M0/M1 | Amarillo | 106 rutas registradas. La sonda ASGI temporal obtuvo 88 HTTP 200 en 101 GET, sin 5xx. El comparador verificó cuatro rutas HTTP dentro de la imagen. | Probar acceso, contenido, redirecciones M0/M1 y las cinco mutaciones. Contrastar primeras filas y estado de proveedor con la DB. Cortar aliases legacy antes de ejecutar el handler. |
| Riesgo, aprendizaje, validación, analítica y cierre | Amarillo | Lectores del embudo, ledger y costos tienen regresiones. La nueva cohorte y las fuentes requieren paridad de evidencia. | Reconciliar reloj de disponibilidad, fuente y costo por cohorte. Mantener aprendizaje en SHADOW y comprobar actualizaciones postcierre y M0/M1. |
| Introspección, scheduler export, auditoría y publicación GitHub | Amarillo | Hay pruebas de lógica, vistas de scheduler, sanitización y disparos. El circuito íntegro usa rutas y permisos de host. | Ejecutar recolección RO con fixtures de procesos, cotejar `success → unknown` y publicar en destino de prueba, ligado al candidato. |
| Fuentes complementarias BYMA, A3 y CAFCI | Amarillo | Lectores y validaciones específicos con casos sintéticos. La cobertura ejecutable de cuenta sigue pendiente. | Calificar BYMA para libro colocado y reloj, A3 para contrato y tick, y CAFCI como referencia de clase/NAV cruzada con el canal. [BYMA](https://www.byma.com.ar/productos/productos-de-datos/market-data/apis) · [A3](https://a3mercados.com.ar/productos/futuros-y-opciones/monedas/dolar) · [CAFCI](https://estadisticas.cafci.org.ar/consulta-avanzada) |
| PPI actual, fix #1289 y PPI Watch | Fuera del perfil IOL | #212 es el último release físico acreditado en el checkpoint. #1289 es un candidato distinto con su propio recibo. PPI Watch tiene ownership propio. | Usar la evidencia como comparador e integrar sólo cambios compatibles en el candidato aislado. |

## Matriz por familia

| Familia | Migración | Prueba aplicable | Qué tiene que pasar para certificar rueda |
| --- | --- | --- | --- |
| Acciones | Amarillo | GGAL con datos IOL de prueba, selección, preapertura, compra y venta PAPER. | Cotización y profundidad con reloj de origen, T0/T1 y cupo EXIT en la cuenta de prueba. Medir READY y recorrido PAPER en la misma rueda. |
| CEDEARs | Amarillo | Par IOL sintético y vuelta completa con Gateway nativo de prueba. | Repetir extremo a extremo con IOL, moneda y plazo exactos y sesión del subyacente. |
| ETFS | Rojo | MERV completa replay con costos de prueba. La política desconocida conserva HOLD. | Acreditar comisión, derechos, mínimo y unidad de la variante. Reproducir BUY, sizing y EXIT con esa política. |
| Bonos | Rojo | Compra y venta Book2 en la misma base temporal para un lote revisado de prueba. | Cerrar testigo y recibo IOL, escala de VN, términos del emisor y costos. Validar salida PIT y liquidación entre procesos. |
| Letras | Amarillo | Compra y redención por pago revisado en prueba. | Unir observación de rueda, plazo y condiciones efectivas con la evidencia exacta de vencimiento y acreditar sus flujos. |
| Obligaciones negociables | Rojo | Compra del lote y venta causal PASS en replay de prueba. | Completar autoridad de emisor, condiciones de VN, evidencia independiente, costos y testigo IOL por variante. |
| Opciones | Rojo | El ciclo nativo acredita contrato y subyacente en prueba. | Capturar cadena, serie, vencimiento, derecho, strike, multiplicador y precios de la serie y del subyacente. Adaptar su verificador específico. |
| Futuros | Rojo | DLR pasa apertura, margen, variación y cierre con el contrato A3 de prueba. | Acreditar proveedor habilitado de A3 con identidad, tick, Book y reloj. Reusar margen y variación. |
| Cauciones | Rojo | Colocación, liquidación e idempotencia de prueba en el worker. | Verificar lado colocador, TNA, monto, plazo, moneda, comisión, vencimiento y reloj. Una tasa de referencia sirve para observación. |
| FCI | Rojo | Parser CAFCI, asignador, suscripción, rescate y pago con datos de prueba. | Acreditar clase exacta, NAV y calendario/corte del canal, mínimos y tarifas. Resolver `FCI_RUNTIME_SOURCE_CALENDAR_NOT_AUTHENTICATED`. |

Los índices se tratan como referencia. La consulta de instrumentos de EE.UU. queda en el alcance de fuentes y calendario correspondiente. El cuadro cubre las diez familias PAPER del inventario RC6.

El corte #530 de las 14:57 ART registró 17.483 filas de catálogo y un elegible en Bonos. `readiness.ready` suma `catalog_eligible`. El READY de apertura por familia y el diferencial IOL quedan **NO_VERIFICADO** para ese corte. La comparación requiere el mismo denominador, clave exacta y fase de rueda, con categorías explícitas para elegibilidad de catálogo, fuente vigente, admisión y ciclo PAPER completado. [Censo #530](https://github.com/mbalbo2023/Porota-trading/actions/runs/45872015091/artifacts/15142279321)

| Familia | Filas de catálogo #530 | Elegibles de catálogo #530 |
| --- | ---: | ---: |
| Acciones | 206 | 0 |
| CEDEARs | 2.504 | 0 |
| ETFS | 0 | 0 |
| Bonos | 3.108 | 1 |
| Letras | 59 | 0 |
| Obligaciones negociables | 5.175 | 0 |
| Opciones | 5.003 | 0 |
| Futuros | 239 | 0 |
| Cauciones | 10 | 0 |
| FCI | 1.087 | 0 |

## Procesos del supervisor

El [arranque original](../../bv_paper_runtime.py) prepara la DB una vez, hereda la ruta absoluta y usa un lock por libro. El CLOCK padre y los seis roles ordinarios requieren una ejecución conjunta sobre la misma DB temporal. Los dos roles opcionales se prueban también con su configuración habilitada.

| Rol exacto | Migración | Criterio de aceptación encima de la prueba funcional |
| --- | --- | --- |
| `ppi_gateway` | Amarillo | Arrancar la variante IOL con OAuth simulado, cupo único y nuevo registro de clientes al cambiar la generación. |
| `scanner` | Amarillo | Registrar el PID hijo y obtener GO, Current, Book, READY y BUY PAPER con recibos IOL. |
| `dynamic_shadow` | Amarillo | Congelar plan, calificar las muestras IOL y emitir transiciones reproducibles con cero fills del worker. |
| `exit_reader` | Amarillo | Servir profundidad al proceso independiente bajo cuota, reinicio y atraso de trade. Probar cierre de los lotes que requieren Book2. |
| `notifications` | Amarillo | Consumo del outbox, reintento y ACK con Telegram simulado. Verificar retorno y reinicio sin duplicados. |
| `candles` | Amarillo | Mantener heartbeat y versiones 1m/5m con recibos. Probar backpressure y recuperación de una cola con atraso. |
| `intraday_scalping` | Rojo | Habilitar el rol opcional una vez acreditados los trades con volumen y el costo neto por instrumento. |
| `caucion_cash_sweep` | Rojo | Habilitar el rol opcional con Book colocador calificado, presupuesto de caja y liquidación reproducible. |

## Inventario exacto de systemd

El [manifiesto de host](../../scripts/porota_host_manifest_v2.py) concilia las 44 unidades de la [política de reconciliación](../../ops/policy/host-control-plane-reconciliation-v2.json). Hay 16 timers activos y sus 16 servicios, 5 timers y 2 servicios en cuarentena, 2 pares retirados y una plantilla A3 retenida. El recibo #4 validó 31 expresiones y un disparo real por timer activo con un verificador de los dos archivos. Las filas activas necesitan además probar el comando íntegro del servicio en el perfil aislado.

| Par timer/service del mismo nombre | Migración | Ejecución pendiente y criterio |
| --- | --- | --- |
| `porota-byma-morning-watch-rc6` | Amarillo | Reproducir parte BYMA, horarios ART y feriado con fuente de prueba. Confirmar recibo de la jornada. |
| `porota-candle-integrity-rc6` | Amarillo | CLI original verde sobre dos versiones IOL. Falta ejecutar la unidad, simular un hueco y comprobar el recibo en preopen. |
| `porota-fast-functional-health-rc6` | Amarillo | CLI original verde en SQLite temporal. Falta arrancar la unidad y probar su salida con worker sano y caído. |
| `porota-full-db-integrity-rc6` | Amarillo | CLI original devolvió `quick_check=ok` con hash de DB intacto. Falta ejecutar la unidad y medir contención con el escritor aislado. |
| `porota-iol-oauth-health-rc6` | Amarillo | Simular expiración, renovación y 401 con el dueño nuevo. Confirmar que salud observa la misma generación. |
| `porota-iol-shadow-collector-rc6` | Rojo | Integrar una sola cuota con el scanner. Verificar cero duplicación cuando coincidan ambos ciclos. |
| `porota-introspection-rc6` | Amarillo | Producir introspección con procesos simulados e identificar IOL, SHA y generación exactos. |
| `porota-introspection-publish-rc6` | Amarillo | Publicar desde introspección temporal a destino de prueba. Verificar sanitización y retención. |
| `porota-local-eod-history-rc6` | Amarillo | Consolidación original e idempotencia verdes en local. Falta acoplar la unidad, calendarios y volúmenes aislados y medir su espacio libre real. |
| `porota-market-watch-silence-rc6` | Amarillo | Reproducir silencio y retorno en outbox aislado. Verificar motivo y deduplicación. |
| `porota-preopen-rc6` | Rojo | Hacer que preopen compruebe el proveedor efectivo. Probar NO_GO y GO con recibo de rueda, ventana y SHA correctos. |
| `porota-rc6-candle-inmarket-verify` | Amarillo | El CLI acredita ambas resoluciones con recibos IOL válidos. Falta conectar el comando, su condición de rueda y el consumidor de evidencia en el perfil aislado. |
| `porota-runtime-evidence-rc6` | Amarillo | Correr recolección RO con identidad de DB, digest, conteo por familia y salida sanitizada. |
| `porota-scheduler-export-rc6` | Amarillo | Ejecutar exportador desde systemd aislado. Contrastar disparo y resultado del servicio. |
| `porota-snapshot-postclose-rc6` | Rojo | Los tres productores se probaron sobre un mismo dataset IOL de prueba. El contrato de fuente diario conserva dependencia PPI. Falta publicar y consumir el recibo IOL común antes de habilitar el cierre certificado. |
| `porota-snapshot-preopen-rc6` | Amarillo | Generar y leer snapshot IOL sin adelantar datos. Verificar lectura por preopen y dashboard. |

| Unidad fuera de ejecución ordinaria | Disposición | Condición aplicable |
| --- | --- | --- |
| `porota-a3-history-daily-rc6.timer` | Cuarentena | Demostrar presupuesto de bytes y migración de copia. |
| `porota-a3-history-reconcile-rc6.timer` | Cuarentena | Acreditar esquema de copia y reconciliación de particiones. |
| `porota-a3-history-weekend-rc6.timer` | Cuarentena | Probar rebalance y recuperación sobre copia sin repetir ingestas. |
| `porota-a3-history-rc6@.service` | Plantilla retenida | Sólo reparación gobernada sobre copia. |
| `porota-history-postclose-rc6.{timer,service}` | Cuarentena | Recuperación expresa sobre copia y con control de origen. |
| `porota-host-general-backup-rc6.{timer,service}` | Cuarentena | Requiere decisión de política y ensayo de restauración. |
| `porota-functional-health-rc6.{timer,service}` | Retirado | Sustituido por health rápido e integridad completa. |
| `porota-gdelt-event-risk-rc6.{timer,service}` | Retirado | Evidencia histórica preservada según política. |

## Orden de implementación

1. **Gateway, OAuth, cuota y relojes.** Cablear el perfil aislado del supervisor. Completar selección y salud por proveedor. Unir los consumidores a una cuota y medir cobertura en ventana autenticada. El adaptador probado usa `quote/depth` y conserva un canal EXIT independiente.
2. **En paralelo, motores y contratos.** Calificar Spot y su economía. Adaptar recibos para Bonos/ON y Opciones. Mantener pruebas especializadas de Letras, A3, Cauciones y FCI con sus propios cierres y fuentes.
3. **En paralelo, servicios, datos y observabilidad.** Ejecutar cada servicio original con dependencias aisladas. Medir continuidad histórica, velas y consistencia del dashboard contra la DB. Llevar la evidencia al semáforo de cada componente.
4. **En serie, aceptación.** Integrar un candidato, probar fase de mercado, selección, READY, compra y salida por familia. Comparar contra el mismo denominador y verificar PAPER/0. La autorización separada aplica al único Deploy V2 posterior.

## RCA del transporte y la profundidad IOL

El replay anterior llamaba al cliente IOL directamente desde la prueba. El Gateway tenía únicamente un contrato nativo PPI. Se agregaron recibos IOL propios sobre framing, registro de PID y cola priorizada existentes. El dueño emite ficha, cotización o profundidad por demanda. El presupuesto cuenta OAuth y GET. EXIT sólo admite ficha y profundidad. Se conservan identidad exacta, generación, reloj y digest. La rueda requiere acreditar cuota, cobertura y relación de `last_updated` con el último trade. [Contrato REST](https://developers.iolinversiones.com/api/llms) · [Gateway IOL](../../rc6_iol_market_gateway.py)

El lector de profundidad ahora selecciona mejores puntas por precio. Si varios niveles comparten el mejor precio, el tamaño queda indeterminado y produce HOLD. La regresión cubre listas desordenadas, 429 sin Book de apertura, pausa compartida, generación vencida, plazo distinto y un proceso con el mismo UID sin el rol autorizado.

### Presupuesto y cobertura IOL

El presupuesto REST local distribuye capacidad entre scanner, EXIT, colector y OAuth. El recibo conserva la capacidad, las reservas, el uso observado y las esperas. La cuota efectiva de la cuenta requiere evidencia del proveedor y de la rueda.

El scanner destina los espacios restantes de su conexión de trades a un recorrido persistente de las identidades elegibles. Antes de sustituir un grupo, el Gateway devuelve qué claves de ese grupo tenían una operación fresca, estaban suscritas sin operación o seguían pendientes. El recibo conserva el denominador por familia, las claves programadas, los resultados por clave y las consultas profundas despachadas. Cuando hay un negocio fresco de descubrimiento, puede usar el turno REST frío existente para confirmar ficha, cotización y libro. Ese turno sigue siendo observacional. EXIT y las claves con evaluación profunda conservan prioridad.

IOL documenta hasta 50 títulos por conexión para trades y puntas, con un título por conexión para profundidad. El Gateway distribuye hasta 550 claves exactas seleccionadas entre 11 conexiones estables. Conserva una conexión rotativa para claves adicionales o rechazadas por el proveedor. El planificador acorta el despacho de evaluaciones a 30 segundos cuando hay suscripciones estables confirmadas y conserva el mismo presupuesto por minuto. La profundidad de entrada o salida conserva su validación específica. Futuros, Cauciones y FCI conservan su contrato de fuente especializado. [WebSocket IOL](https://developers.iolinversiones.com/docs/websocket) · [Planificador](../../rc6_dynamic_universe/iol_coverage.py)

Con 540 claves, 12 lecturas REST completas por minuto permiten como máximo dos evaluaciones de libro por clave en 90 minutos si se reparten uniformemente. La señal requiere seis muestras. El barrido rotativo de referencia de 20 ciclos de 60 segundos supera también los 120 segundos de frescura. Los recibos muestran por separado cobertura continua confirmada, actividad recibida y capacidad de evaluación profunda. El umbral de capacidad de la cuenta debe comprobarse en rueda.

## RCA de procedencia en velas y embudo

- **Prueba de velas intradiarias.** El CLI original devolvía RED sobre dos velas IOL válidas. Estas muestras alimentan la cola de velas por fuera del cursor de snapshots. El otro camino de progreso sólo buscaba `PPI_GUARDED_CURRENT`. La opción explícita `--sample-source IOL_REST_TRADE_SAMPLE` publica una prueba propia para 1m y 5m de la misma identidad. Verifica hashes, tiempos, calidad y vínculo de cada muestra con su recibo archivado. La regresión rompe el vínculo de 5m y exige RED aun con el cursor avanzado. El CLI publica JSON y abre SQLite de sólo lectura. [Verificador](../../scripts/rc6_candle_inmarket_evidence.py) · [Regresión](../../tests/test_rc6_iol_migration_offhost.py)
- **Promoción de COLD a HOT.** El lector de snapshots publicaba cada observación como `PPI_MARKETDATA_CURRENT` y el planificador contaba cualquier Book útil para WARM. El lector reconoce IOL y comprueba el origen PPI explícito. La prueba conserva WARM ante un Book IOL en el plan PPI, y sólo lo completa al llegar el Book PPI. El camino SHADOW del plan IOL necesita su propia autoridad. El replay GGAL con IOL obtiene una señal de compra por el selector fallback y el broker. [Planificador](../../rc6_dynamic_universe/orchestrator.py) · [Regresión](../../tests/test_rc6_dynamic_orchestrator.py)
- **Certificación del cierre.** El consolidado local archiva muestras IOL en frío. La certificación diaria recibe `PPI_EOD_SOURCE_NOT_GREEN` porque observa `PPI_PRODUCTION_HISTORY`. El ensayo de la cadena mantiene PAPER seguro y los tres reportes provisionales. La solución requiere una prueba diaria propia de IOL con barras certificadas y la misma fuente, cohorte, reloj y dataset en cada consumidor. Las velas muestreadas conservan su alcance intradiario. [Contrato de fuente](../../rc6_postclose_source_contract.py) · [Regresión](../../tests/test_rc6_iol_migration_offhost.py)

## Inventario de rutas del dashboard

Registro obtenido al construir la aplicación con el perfil de DB temporal. Las rutas de documentación incluyen GET/HEAD en el mismo registro. La ejecución HTTP dentro de la imagen cubrió el grupo de smoke. La sonda local hizo 101 GET con red y subprocess bloqueados. Obtuvo 88 HTTP 200, siete 409 de aliases legacy, cinco 404 y un 422 con parámetros o recursos de prueba ausentes. En el árbol auditado, el middleware produce los siete 409 después de llamar al handler legacy. La solución debe autenticar y devolver el 409 antes de ejecutar ese handler. Quedan pendientes contenido, navegación, escrituras y accesos en el perfil IOL.

| Grupo | Estado | Rutas registradas | Solución |
| --- | --- | --- | --- |
| Smoke HTTP acreditado (4) | Verde | `GET /api/dashboard/truth`, `GET /health`, `GET /salud`, `GET /vivo` | Conservar el smoke de la imagen exacta y cotejar la proyección IOL con la DB. |
| Mutaciones y permisos (5) | Amarillo | `POST /api/reports/action4`, `POST /api/sre/encolar/{propuesta_id}`, `POST /api/testing/autorizar`, `POST /config`, `POST /restart-request` | Verificar autorización, rechazo del contexto de prueba y efectos sobre recursos temporales. |
| API, contratos HTTP y descargas (22) | Amarillo | `GET /api/dashboard`, `GET /api/diagnostics/download`, `GET /api/failed-notifications`, `GET /api/learning-logs`, `GET /api/logs/current`, `GET /api/logs/download/{log_type}`, `GET /api/observation-instruments`, `GET /api/observer/state`, `GET /api/paper/caucion-allocations`, `GET /api/rc6/snapshots/latest`, `GET /api/reports/action4`, `GET /api/reports/weekly/download`, `GET /api/reports/{report_id}/{kind}`, `GET /api/sre/propuestas`, `GET /api/testing/estado`, `GET /api/trader/logs/download`, `GET /api/v15/estado`, `GET /api/v16/estado`, `GET,HEAD /docs`, `GET,HEAD /docs/oauth2-redirect`, `GET,HEAD /openapi.json`, `GET,HEAD /redoc` | Probar contrato, acceso y contenido sanitizado con SQLite de prueba. |
| Rueda, instrumentos y embudos (32) | Amarillo | `GET /en-vivo`, `GET /en-vivo/capacidad`, `GET /en-vivo/cierres`, `GET /en-vivo/decisiones`, `GET /en-vivo/fuentes`, `GET /en-vivo/oportunidades`, `GET /en-vivo/posiciones`, `GET /en-vivo/workers`, `GET /inicio`, `GET /instrumentos`, `GET /instrumentos/contrato`, `GET /instrumentos/evidencia`, `GET /instrumentos/ficha`, `GET /instrumentos/mercado`, `GET /motor-trading`, `GET /observacion`, `GET /scalping`, `GET /trading`, `GET /trading/cauciones`, `GET /trading/derivados`, `GET /trading/equity-spot`, `GET /trading/renta-fija`, `GET /trading/scalping`, `GET /trading/tesoreria`, `GET /trading/{section}`, `GET /universo`, `GET /universo-operativo`, `GET /universo/coverage`, `GET /universo/discovery`, `GET /universo/excluidos`, `GET /universo/familias`, `GET /universo/tradeability` | Comparar primeras filas, estado por familia, etiquetas de origen y transición COLD→BUY. |
| Riesgo, histórico y aprendizaje (25) | Amarillo | `GET /ai-decisions`, `GET /analisis`, `GET /analitica`, `GET /analitica/anual`, `GET /analitica/cierre-diario`, `GET /analitica/contrafactuales`, `GET /analitica/experimentos`, `GET /analitica/historico`, `GET /analitica/reportes`, `GET /analitica/salidas`, `GET /analitica/senales`, `GET /aprendizaje`, `GET /historicos`, `GET /informacion-financiera`, `GET /postcierre`, `GET /reportes`, `GET /reports/monthly`, `GET /riesgo`, `GET /riesgo/event-risk`, `GET /riesgo/exposicion`, `GET /riesgo/limites`, `GET /riesgo/liquidez`, `GET /riesgo/pnl`, `GET /riesgo/posiciones`, `GET /validacion` | Contrastar reloj, costos, saldo, resultados y M0/M1 contra el ledger y los recibos. |
| Sistema, credenciales y salud (18) | Amarillo | `GET /`, `GET /config`, `GET /dashboard/logs`, `GET /infra`, `GET /scheduler`, `GET /sistema`, `GET /sistema/backups`, `GET /sistema/configuracion`, `GET /sistema/evidencia`, `GET /sistema/integraciones`, `GET /sistema/logs`, `GET /sistema/salud`, `GET /sistema/scheduler`, `GET /sistema/validacion`, `GET /sistema/workers`, `GET /sre`, `GET /telegram`, `GET /testing` | Validar permisos, redirecciones, timers y estado de procesos con el perfil IOL. |

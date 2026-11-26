# Auditoría y backtesting PAPER

**Porota Trading RC6 · Continuidad para la rueda del 25/11/2026**

El corte inicial de esta auditoría se tomó el 24/11 a las 23:27 ART (`2026-11-25T02:27:20Z`). Market Watch #459 acreditó en el Droplet el producto `167ae9bc00de6f3de9d18183a10a8c2364c2e387`, candidato `d5a656f9529bbc1749cd6ada1c098e218a8b6398`, en PAPER y con cero órdenes reales. El mercado estaba cerrado. El preopen del 25/11 está programado para las **10:20 ART (`13:20 UTC`)**. [Recibo de runtime](https://github.com/mbalbo2023/Porota-trading/actions/runs/45533003189/artifacts/14985443767) · [Recibo del Promote #206](https://github.com/mbalbo2023/Porota-trading/actions/runs/45523776489/artifacts/14980838451)

## Universo congelado y primera causa

El último censo por clave disponible al iniciar fue el de #205, generado el **24/11 a las 21:50:08 ART** (`2026-11-25T00:50:08Z`). Contiene **17.473 claves únicas**. **17.472** corresponden a la auditoría de diez familias y una es un índice de referencia. Cada fila preserva las cinco coordenadas, la primera barrera y su causa. Los 91 rótulos `ON` se sumaron a Obligaciones en la tabla y conservaron su identidad original en el manifiesto. [Censo original fijado](https://github.com/mbalbo2023/Porota-trading/commit/dd06564d7630d6c819fd10fb119b5c6883fbac21) · [Casos por clave](CENSO_PAPER_2026-11-25.jsonl.gz)

| Familia | Claves | Plazo sin acreditar | Identidad primaria faltante | Evidencia vencida |
| --- | ---: | ---: | ---: | ---: |
| Acciones | 206 | 165 | 35 | 6 |
| CEDEARs | 2.504 | 1.198 | 1.166 | 140 |
| ETFs | 0 | 0 | 0 | 0 |
| Bonos | 3.105 | 2.495 | 610 | 0 |
| Letras | 59 | 42 | 16 | 1 |
| Obligaciones negociables | 5.263 | 3.866 | 1.397 | 0 |
| Opciones | 5.002 | 2.147 | 2.855 | 0 |
| Futuros | 236 | 236 | 0 | 0 |
| Cauciones | 10 | 10 | 0 | 0 |
| FCI | 1.087 | 1.065 | 22 | 0 |
| **Total** | **17.472** | **11.224** | **6.101** | **147** |

El primer gate fue `PPI_IDENTITY` en 17.434 de las claves del alcance y `CANDIDATE_LEDGER` en 38. En estas últimas el ledger también atribuyó el rechazo al plazo. Las claves quedaron detenidas antes de señal o ejecución. El total READY del censo fue **0**. La sonda posterior de #206 volvió a leer **0/17.473** en la proyección agregada. Esa igualdad de conteos conserva dos cortes con identidades de release distintas. El lote por clave es reproducible a partir del censo fijado y su SHA-256 en el manifiesto de recibos. [Manifiesto de recibos](RECIBOS_PAPER_2026-11-25.json)

## Casos de replay y asientos

El ensayo de captura nativa usó el Gateway, catálogo, recibo de plazo y guard de admisión reales del código, con transporte y relojes de prueba. Ocho casos con libro pasaron el guard. El `Current` de PESOS1 y QA_FCI produjo una referencia y conservó el HOLD. Los ciclos contables de abajo usaron una SQLite temporal, entradas de prueba y motores del proyecto. Cada caso indica su última etapa observada.

| Familia | Clave de prueba y resultado observado |
| --- | --- |
| Acciones | GGAL, BYMA, ARS, A-24HS. Ocho cotizaciones de prueba, señal de momentum, compra y venta PAPER conciliadas en SQLite. |
| CEDEARs | AAPL, BYMA, ARS, A-24HS. Señal, compra y cierre PAPER del motor spot con el calendario de prueba de la sesión. |
| ETFs | MERV, BYMA, ARS, A-24HS. Señal, compra y venta con tarifa sintética explícita en el motor. SPY ejercitó admisión del Gateway en otro caso. |
| Bonos | AL30 cruzó el guard en fixture. El simulador comprobó la compra por 100 nominales en USD MEP. La compra por estrategia nativa quedó HOLD ante el contrato de salida de eventos. |
| Letras | `SYNTHETIC_LETRA`. Compra de 100 VN, costo de entrada, obligación oficial de fixture, pago de ARS 105 y cierre idempotente. |
| Obligaciones | `SYNTHETIC_ON` alcanzó la estrategia de renta fija y devolvió `FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND`. El replay anterior de YMCJO/YMCJD/YMCJC ejercitó venta de 100 VN por variante con testigo de prueba. |
| Opciones | GFGC7000OC. Compra simulada de hasta dos contratos, multiplicador 100, presupuesto sobre prima total. La serie vencida fue rechazada. |
| Futuros | DLR/OCT26. Apertura, reserva de margen, variación y cierre PAPER en el ledger especializado. DLR/NOV26 ejercitó la admisión del Gateway en otro caso. |
| Cauciones | PESOS3. Libro y costo de fixture, colocación PAPER por ARS 100.000. Repetición idempotente y rechazos de libro vencido, autoridad inválida y candidato revocado. |
| FCI | `FCI.FIXTURE.B` recorrió un mismo expediente desde el asignador hasta el rescate. Débito de ARS 1.001,01, 100 cuotas, rescate por ARS 1.029 y caja final ARS 2.027,99. Los reintentos de suscripción y pago preservaron los seis eventos. |

La regresión de scalping ejercitó la entrada y la salida PAPER con un libro y una ventana intradía de prueba. Las regresiones de velas comprobaron la lectura del cursor, barras cerradas, revisiones y rechazo de corrupción o barras pendientes fuera de plazo. En históricos se comprobó rechazo de precio ajustado o no acreditado, reanudación parcial y conservación de barras admitidas. El reporte JSON guarda los archivos de test y los hashes de los recibos locales.

Las tres suites focalizadas aprobaron 341 pruebas de admisión, firma, EXIT y host, 551 de ejecución y contabilidad y 215 de ingesta, scalping e históricos. El manifiesto enlaza cada suite con los archivos de prueba del candidato.

El replay anterior de reloj observó seis pares reales de AL30 y GGAL. Los seis resultaron frescos en su recepción, futuros justo antes y vencidos al corte del ensayo. El recibo acredita ese filtro temporal. Los dos instrumentos conservaron su bloqueo de identidad en el censo anterior. [Prueba de reloj](https://github.com/mbalbo2023/Porota-trading/actions/runs/45508220641/artifacts/14975086071)

## RCA y correcciones del candidato

| Hallazgo | Causa reproducida y corrección |
| --- | --- |
| El descubrimiento deja claves sin turno | El selector de diagnóstico excluía una familia al primer candidato READY. La consulta devolvía reiteradamente la primera clave. #1272 recorre las diez familias, avanza la clave aun si falla la lectura y vuelve al inicio del orden estable. La prueba cubre rechazo, avance y vuelta al inicio. La solicitud pasa por el mismo Gateway y presupuesto. |
| El censo rechaza un plazo firmado de su propia base | La lectura usaba el SQLite explícito y la verificación buscaba el pin de firma de la base por defecto. #1272 enlaza recolector, pin y ciclo de verificación a la base elegida. La regresión usa dos rutas y un recibo firmado que falla en la versión anterior. |
| El publicador depende de módulos ausentes en una instalación host mínima | #206 propagó el colector en el checkout del host. El verificador nuevo se encontraba en el source privado del release. Se reprodujo `ModuleNotFoundError` con los archivos conservados. #1272 ejecuta el publicador bajo el launcher rootless, con el source validado y el lock compartido. Si falta el verificador, el censo conserva el motivo y la clave queda bloqueada. |

Las barreras del Gateway, preopen, EXIT, riesgo y fuente se conservan. Un resultado READY de un caso aislado prueba su lógica con esos insumos. El READY de rueda se obtiene con la clave exacta, evidencia vigente y los gates de la política instalada. [PR #1272](https://github.com/mbalbo2023/Porota-trading/pull/1272)

La última publicación verificable del censo antes de #207 ocurrió a las 21:50 ART del 24/11, antes de instalar #206. La introspección continuó publicando a las 22:42 y 23:42 ART. La separación acota el fallo al censo. El error de importación se reprodujo en una réplica del source host. [Último censo](https://github.com/mbalbo2023/Porota-trading/commit/dd06564d7630d6c819fd10fb119b5c6883fbac21) · [Introspección](https://github.com/mbalbo2023/Porota-trading/commit/d748733156f2c10852b599c15772336039a456b4)

## Evidencia del candidato y la promoción

Predeploy V2 #1321 ejecutó **9.178 de 9.178 pruebas activas** con cero fallos, errores y saltos. El inventario tiene 496 casos retirados. La prueba HTTP aislada verificó las rutas de salud y dashboard, el bloqueo de rutas de órdenes y el contador cero sobre la misma imagen. El candidato `2b719cc86252c51dac516a48b519ab422dcb8e50` ingresó por #1272 a producto `c6b990e20fb7b943dfda478202452b5c550816f6`, con el mismo árbol `1401d8390e01dbf3be3b0e115a5bfd97acd0ae36`. Promote V2 #207 verificó y descargó ese paquete. El manifiesto de recibos conserva los digests del ZIP, la imagen y sus miembros. [Predeploy #1321](https://github.com/mbalbo2023/Porota-trading/actions/runs/45535126618/artifacts/14986798842) · [Promote #207](https://github.com/mbalbo2023/Porota-trading/actions/runs/45537154370) · [Recibos](RECIBOS_PAPER_2026-11-25.json)

**VALIDADO_RUNTIME** a las **00:08:01 ART** del 25/11 (`03:08:01 UTC`). Una sola promoción instaló la imagen probada. El recibo físico acreditó ocho roles y seis variantes de venta de renta fija `ACTIVE_PAPER`, dashboard saludable, observer activo, guard REAL `BLOCKED` y `real_orders_sent=0` en la misma base PAPER. El recibo declara `ppi_watch_touched=false`. La instalación verificó por hash el colector y el wrapper de publicación. El paso restante del publicador es observar su próxima entrega por el timer de `:50`. IOL REST verificó la ficha de referencia GD30 fuera de la ventana de colecta. [Recibo físico #207](https://github.com/mbalbo2023/Porota-trading/actions/runs/45537154370/artifacts/14987771207)

La lectura independiente de las **00:12:02 ART** del 25/11 (`03:12:02 UTC`) corroboró el árbol y el candidato activos en el Droplet, PPI Auth OK, un dueño local con un login registrado y tres consumidores autorizados, incluido EXIT. El lector estaba en `WAITING_MARKET`. La proyección indicó **0/17.473 READY**, **0 aperturas y cierres PAPER**, último dato del 24/11 a las 16:55:50 ART y cursor 289.243. La sonda registró 10.290 claves de consulta y `provider_attested_keys=0` en el catálogo primario. La sonda anterior de las 00:09 devolvió `CONTAINER_PROBE_UNAVAILABLE` en ese diagnóstico y la repetición estabilizada devolvió `OBSERVED`. Ambas conservaron PAPER/0 y cero accesos de la sonda a rutas de orden o PPI Watch. [Postflight #460](https://github.com/mbalbo2023/Porota-trading/actions/runs/45537926257/artifacts/14987483063) · [Postflight #461](https://github.com/mbalbo2023/Porota-trading/actions/runs/45538260228/artifacts/14988041697)

## Hallazgos operativos y criterio de cierre

| Frente | Evidencia, causa y resolución |
| --- | --- |
| Preopen y EXIT | La sonda nocturna registró `MARKET_CLOSED` y EXIT `WAITING_MARKET`. El T-10 del 24/11 pertenece a su release y fecha. El cierre de esta verificación requiere el T-10 de las 10:20 ART del 25/11, la ronda EXIT con generación, cuota y plazo, y la primera evidencia fresca de cada familia en su ventana. |
| Cauciones | A655 observó 10 activas con `NEEDS_CAUCION_TERMS`, cinco sin campos completos en el puente. El worker nocturno estaba en `WAITING_WINDOW`. El último intento del día registrado estuvo en HOLD por `NO_GO_PAPER_SCALPING`. La prueba de cierre exige términos y libro colocador vigente por plazo y moneda, TNA, costo, caja y colocación idempotente. |
| FCI | A655 observó 1.063 capacidades de catálogo y 23 brechas activas de liquidación. El executor de runtime devuelve `FCI_RUNTIME_SOURCE_CALENDAR_NOT_AUTHENTICATED` sin un productor autorizado de calendario, corte y evidencia de clase. El NAV de referencia tiene su propia procedencia. El cierre exige esos recibos y la conciliación de suscripción, cuotas y rescate. |
| Opciones | 568 revisiones materiales coinciden con el agregado de campos de nominal y unidad de precio. Todas las 568 conservan plazo en `REQUEST_CANDIDATE`; A655 no certifica identidad nativa. El cierre requiere el recibo Current/Book exacto y la revisión material por contrato bajo la política vigente. |
| Bonos y ON | El benchmark de compra reportó `FI_NATIVE_EVENT_EXIT_ENGINE_NOT_BOUND` para los casos de renta fija que exigen salida por evento. Es una barrera distinta de la política PAPER de venta por seis variantes ya instalada. La comprobación de venta usa posición elegible, decisión comprometida, ACK firmado, primer Book posterior de la misma generación y asiento. |
| ETFs | El manifiesto publicado contiene cero filas `ETFS`. El caso sintético valida el motor con su tarifa. Para la rueda falta verificar qué devuelve el catálogo primario y cómo se proyectan la identidad y los costos, incluidos los antecedentes de #1216. |
| Scalping | La sonda nocturna observó `WAITING_MARKET`, 0 seleccionados y 0 candidatos. La tarde del 24/11 había mostrado `SECURITY_BLOCK` y ningún punto SHADOW en la ventana. El cierre requiere un ciclo con presupuesto, ticks, velas, HOT, señal, riesgo y ejecución al mismo corte. |
| Velas 1m/5m | El worker estaba `RUNNING` con cursor 289.243, sin un nuevo trade nocturno en el corte. La ingesta de rueda se acredita enlazando eventos TRADE, cursor, barras OHLCV, `known_at` y delta EOD. |
| Históricos | `history_cutoff_repair=INCOMPLETE` en la sonda. El inventario previo contó 195/1.094 series pendientes y las cargas de `SOURCE_REPORTED` tienen recibos propios. El cierre requiere cobertura RAW acreditada de las brechas reales y una restauración válida con el owner incremental. |
| Scheduler y última actividad | La sonda registró cero runs genuinos recientes, ACK histórico y ciclos del observer con cero claves seleccionadas. El T-10, el censo de cada hora y los demás jobs conservan sus propios recibos de invocación. La auditoría tiene un inventario de 32 timers del corte anterior. [Inventario](TIMERS.md) |
| SQLite y copia | La salud ligera del 24/11 a las 23:24 ART quedó verde. La proyección marcó `DAILY_BACKUP=DEFERRED_DISK` con registro del 21/10 y último `HISTORY_DAILY` del 21/10. Es evidencia de la antigüedad de esa proyección. La custodia rootless A729 tiene sus propios respaldos y restauraciones. Cerrar con una corrida efectiva y restauración de PAPER, HISTORY y OBSERVER, ligadas a su timer en el release. |
| IOL, Telegram y performance | El IOL complementario requiere su ventana de colecta. La lectura conservó `PERFORMANCE_REPORT_STALE` y `DYNAMIC_SHADOW_NOT_PROGRESSING`. El último ACK independiente correspondía a las 16:23 ART del 24/11. Correlacionar alertas nuevas con clasificación, outbox y ACK. Las lecturas iniciadas para esta auditoría operaron en `READ_ONLY`. |

A655 leyó la base PAPER bajo un montaje privado de solo lectura. Observó **954 brechas activas** en seis familias y cero fills en `paper_fills` para el 24/11. El propio A655 declara su límite de correlación con el SHA. Market Watch aporta el recibo ligado al release. [A655 run 45534276846](https://github.com/mbalbo2023/Porota-trading/actions/runs/45534276846/artifacts/14985743479) · [Market Watch run 45533003189](https://github.com/mbalbo2023/Porota-trading/actions/runs/45533003189/artifacts/14985443767)

La introspección de las 23:35 ART observó cero ofertas completas en 10 cauciones, cero posiciones abiertas y cero evaluaciones de scalping en la última hora. La proyección histórica mostraba 242.636 filas, 1.151 instrumentos, RAW 0, último día de mercado histórico el 25/09 y última revisión de vela conocida el 24/11 a las 17:00 ART. La lectura anterior repite esos valores. La disponibilidad de velas locales y el corte histórico tienen procedencias distintas. [Introspección de las 23:35](https://github.com/mbalbo2023/Porota-trading/blob/d748733156f2c10852b599c15772336039a456b4/runtime/introspection/latest.json) · [Comparación de las 22:35](https://github.com/mbalbo2023/Porota-trading/blob/316a1f404a8d606fd4228f8db40da21b224e01b2/runtime/introspection/latest.json)

## Reproducción y continuidad

1. Fijar SHA, árbol, imagen, recibo físico y fecha efectiva. Confirmar modo, guard REAL y contador `real_orders_sent=0` desde runtime.
2. Descomprimir el manifiesto de claves, contar por familia y comparar primera barrera con la proyección del mismo corte. Reutilizar `RECIBOS_PAPER_2026-11-25.json` para verificar hashes y relojes.
3. Ejecutar los archivos de prueba declarados con la imagen o checkout exacto, proveedor aislado y almacén temporal. Ejercer descubrimiento, identidad, datos, reloj y selección. Registrar la primera barrera si una clave no progresa.
4. Para el pase completo, registrar señal, economía, riesgo, capacidad EXIT, decisión, orden o evento especializado, cantidad, costos y asientos conciliados. El replay causal sólo puede usar datos disponibles en `known_at`.
5. Publicar las evidencias contemporáneas de preopen, EXIT, primera cotización, progreso y ledger por clave al corte de rueda. Actualizar el handoff y el registro de causas con las deltas verificadas.

## Addenda de datos y preopen

El [censo posterior](https://github.com/mbalbo2023/Porota-trading/commit/1d34e94ae9005194cb1ea2718195fbae12ecdf5d), del 25/11 a las 07:50:29 ART, registró 17.482 claves, cero READY y 11.195 plazos registrados como hipótesis de consulta. El primer bloqueo de otras 6.102 claves fue la falta de identidad primaria PPI. El [Promote #208](https://github.com/mbalbo2023/Porota-trading/actions/runs/45599380125/artifacts/15014269186) de las 08:01:42 ART acreditó PAPER/0 en el release siguiente. Estos dos recibos tienen sus propios cortes.

| Frente | Corrección incorporada al candidato #1275 | Evidencia que cierra la rueda |
| --- | --- | --- |
| Históricos | Completar huecos con los archivos PPI y de referencia mediante el importador existente. Conservar cada fecha ya cubierta y su procedencia. | Conciliar altas, huecos y versiones por serie. El lote anterior dio 0/40 en la certificación RAW. Hace falta el contrato de procedencia del proveedor. |
| Velas 1m/5m | Conservar un Current genuino y vigente de clave acreditada si Book falla. El worker publica barras muestreadas con sus propios relojes. | Relacionar evento, recepción, muestra y versión. Acreditar la continuidad de trades y su unidad para OHLCV completo. |
| Preopen y EXIT | Conservar el plazo firmado vigente al rotar el Gateway, preparar la rueda por grupo, computar cobertura contractual por familia y reservar un lugar para EXIT. | T−10, primer dato de rueda, motivos por clave y medición de la ronda EXIT del SHA instalado. |
| Gateway y scalping | Observar límites informados en respuestas existentes. Al llegar 429, pausar la rotación y reintentar cuando vence la espera observada. | Cuota de cuenta acreditada por PPI, reanudación física, volumen intradiario y primer gate de scalping. |

La revisión focalizada aprobó 311 pruebas y registró dos omisiones del entorno. La comprobación final recorrió el fallo de Book desde el scanner hasta las dos resoluciones y verificó que la colección permanezca observacional. Los casos de instrumentos y asientos conservan sus recibos originales. [RCA, solución y criterio de cierre](RCA_DATOS_PREOPEN_2026-11-25.md) · [Candidato #1275](https://github.com/mbalbo2023/Porota-trading/pull/1275)

## Cierre técnico del Deploy V2 #209

**VALIDADO_RUNTIME** a las **10:05:44 ART** del 25/11/2026. [Deploy V2 #209](https://github.com/mbalbo2023/Porota-trading/actions/runs/45618943128/artifacts/15023674360) acredita producto `44ee181dcc452215ab05fae7f3919e344e7601d7`, candidato `55bb80daa2834df6b8c7ae6f90b7db9d74ed0eae` y árbol `95493d68d9dd02014d364c0ec24c971a75923cc8`. El [Predeploy #1330](https://github.com/mbalbo2023/Porota-trading/actions/runs/45616489476/artifacts/15022852754) aprobó 9.190 pruebas y validó la misma imagen.

El recibo, el guard y la consulta de SQLite acreditan `PRODUCTION_PAPER / SIMULATION`, `orders_guard=BLOCKED` y `real_orders_sent=0`. El dashboard estaba saludable, el observer activo y el controlador reportó ocho roles. PPI Watch está fuera de cambios. [Checkpoint #1093](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7161442099)

La siguiente observación de rueda debe acreditar T−10, EXIT efectivo, primera cotización fresca y habilitación por clave. La cuota de cuenta PPI y la cobertura RAW siguen pendientes de sus recibos. [Registro de recibos](RECIBOS_PAPER_2026-11-25.json)

PAPER/SHADOW. Órdenes reales bloqueadas. PPI Watch fuera de cambios. [Handoff](HANDOFF.md) · [Ownership](https://github.com/mbalbo2023/Porota-trading/issues/980)

## RCA del acceso PPI y de GitHub Actions desde septiembre

**Corte operativo del 25/11/2026.** Las horas se expresan en ART. El alcance es lectura de evidencia y contabilidad PAPER.

| Fecha | Qué cambió o qué quedó acreditado |
| --- | --- |
| 5/9 y 15/9 | El scanner ya pedía **Current y Book** para una clave seleccionada. Scalping consultaba **Intraday** y EXIT usaba **Book**. Los procesos instanciaban sus lectores y se autenticaban por separado. El deploy del 15/9 acredita PAPER/0 para el código inspeccionado, con `ppi-client==1.3.0`. [Base 5/9](https://github.com/mbalbo2023/Porota-trading/blob/852b812d610860b57b579212978443f7450e8855/bf_production_paper_observer.py#L1178) · [Scanner 15/9](https://github.com/mbalbo2023/Porota-trading/blob/b4f19b70107859fd2dd6c32c84da5328e13d9908/bf_production_paper_observer.py#L1225) · [Deploy 15/9](https://github.com/mbalbo2023/Porota-trading/actions/runs/35025657772) |
| 10–17/11 | El guard de identidad empezó a exigir una constancia de plazo para la clave exacta. El 17/11 ya había **0/17.266 READY** con datos antiguos. La pérdida de READY antecede al Gateway compartido. [Guard 10/11](https://github.com/mbalbo2023/Porota-trading/commit/4ac3e6343e86a936dd9eb2f97edabebf2534dcde) · [Recibo 17/11](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6962390612) |
| 21/11 | El Gateway reemplazó los lectores por proceso con un propietario local de sesión para scanner, EXIT y scalping. Las lecturas comparten transporte serializado y el guard de pausa por 429. El descubrimiento actual corta antes de Book si Current incumple su límite de cinco segundos entre reloj nativo y recepción. [Instalación](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7057286587) · [Guard activo](https://github.com/mbalbo2023/Porota-trading/blob/44ee181dcc452215ab05fae7f3919e344e7601d7/rc6_ppi_shared_feed.py#L598) |
| 24–25/11 | Hubo pares Current/Book exitosos el 24/11, respuesta 429 a las **15:01:32** y una cotización GGAL persistida a las **16:55:50**. El primer ciclo conservado del 25/11 intentó **una clave**, con 20 lugares de rotación local, y devolvió 429. La ampliación de rotación del 25/11 es posterior al primer 429 acreditado. [429 del 24/11](https://github.com/mbalbo2023/Porota-trading/actions/runs/45457257511/artifacts/14951650853) · [Cierre 24/11](https://github.com/mbalbo2023/Porota-trading/actions/runs/45480836551/artifacts/14961946946) · [Lectura #475](https://github.com/mbalbo2023/Porota-trading/actions/runs/45648387278/artifacts/15037104201) |

El ledger activo conservaba las siguientes compras y ventas simuladas. El recuento agrupa `paper_fills` por `filled_at` en ART, con `source=PRODUCTION_PAPER`. El informe cuenta asientos de compra y venta en esa tabla. La custodia histórica y los eventos especializados tienen sus propios recibos. [SQLite READ_ONLY #477](https://github.com/mbalbo2023/Porota-trading/actions/runs/45658375444/artifacts/15042124814)

| Mes de 2026 | Compras PAPER | Ventas PAPER |
| --- | ---: | ---: |
| Septiembre | 80 | 96 |
| Octubre | 52 | 62 |

**Cuota y causa pendiente.** El #475 registró 103 autorizaciones locales de Current en su muestra, 20 eventos 429 de PPI y 58 activaciones del bloqueo local. El último 429 allí expuso una espera de un segundo y ningún límite numérico observado. Las tres cifras describen etapas distintas. La concurrencia, la cuota y las respuestas exitosas requieren testigos de transporte. En el #477 se observó un 429 a las **13:29:08**, un 401 a las **13:44:46** y, al corte de **13:51:31**, `ppi_auth=ERROR`, **0/17.482 READY** y el dato del 24/11 a las **16:55:50**. La documentación REST de PPI consultada describe los endpoints, sin publicar una cuota por cuenta. Queda por acreditar alcance y ventana de limitación. [REST PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/) · [Recibo #477](https://github.com/mbalbo2023/Porota-trading/actions/runs/45658375444/artifacts/15042124814)

### Estado del Gateway y recuperación

El #478 aisló el estado del socket y observó el propietario local disponible con los tres consumidores registrados. La última respuesta nativa registrada era **401**. A las **13:55:12** el scanner seguía en `COOLDOWN` y la causa persistida era `PPI_GATEWAY_SESSION_RECOVERY_PENDING`. El presupuesto del propietario reportó **266 segundos** de espera restante. El código fija 900 segundos para el 401 y permite una renovación anticipada de la misma sesión si los guardas lo permiten. La interrupción se produjo en la acreditación de sesión, antes de recibir un dato nuevo. El propietario local acredita IPC. Para volver a consultar, el scanner exige una sesión recuperada. [Recibo #478](https://github.com/mbalbo2023/Porota-trading/actions/runs/45659056310/artifacts/15044745271) · [Guard de recuperación](https://github.com/mbalbo2023/Porota-trading/blob/44ee181dcc452215ab05fae7f3919e344e7601d7/rc6_ppi_shared_session_owner.py#L593-L638)

El primer intento de renovar la sesión, a las **13:44:48**, quedó `PPI_OWNER_REFRESH_UNVERIFIED` y el código del intento quedó sin acreditar. El propietario inició la recuperación a las **13:59:48** y el scanner volvió a Auth OK a las **14:00:00**. A las **14:00:03**, una lectura normal de Current recibió otro **429**. El reporte posterior seguía en **0/17.482 READY** con el dato del 24/11. [Recibo #479](https://github.com/mbalbo2023/Porota-trading/actions/runs/45659857026/artifacts/15043768933) · [Recibo #480](https://github.com/mbalbo2023/Porota-trading/actions/runs/45660264049/artifacts/15043529247)

**Amplificación local del 429.** PPI informó `Retry-After: 1` segundo y el guard de Porota registró **781 segundos restantes**. El contador persistente aplica 60, 120, 240, 480 y hasta 900 segundos con cada 429 sin un 200 que reinicie la serie. Toma el mayor valor entre la espera local y la anunciada por el proveedor. El código y el recibo explican la pausa que también afecta a los otros lectores del Gateway. El disparador inicial del 429 y la cuota de la cuenta siguen pendientes de evidencia del proveedor o de ventanas medidas. [Guard de transporte](https://github.com/mbalbo2023/Porota-trading/blob/44ee181dcc452215ab05fae7f3919e344e7601d7/rc6_ppi_global_budget.py#L2883-L2897) · [Medición #480](https://github.com/mbalbo2023/Porota-trading/actions/runs/45660264049/artifacts/15043529247)

**GitHub Actions.** El 25/11 GitHub inició la investigación a las **12:55**, confirmó la degradación de Actions a las **13:07** y señaló alta carga de su base de datos a las **13:14**. Los intentos rechazados durante esa ventana quedaron sin run nuevo. Tras la recuperación, el #476 llegó al Droplet en READ_ONLY. El #477 identificó un segundo fallo, distinto del despacho, al vencer el límite de nueve segundos del `docker exec` que combinaba IPC, catálogo y construcción. SQLite seguía accesible. [Incidente GitHub](https://www.githubstatus.com/incidents/qn8mfv6q5hlx) · [Recibo #476](https://github.com/mbalbo2023/Porota-trading/actions/runs/45657215786/artifacts/15044036249)

**Criterio de validación.** Con una sesión existente acreditada y fuera de la pausa del proveedor, medir una consulta Current y registrar endpoint, hora, estado y encabezados permitidos. Hacer un segundo intento con una clave distinta sólo tras un 200 y con capacidad observada. Ante el primer 429, conservar el intento y respetar la espera. Un 200 acredita esa consulta. Para inferir cuota y ámbito se necesita evidencia de varias ventanas o confirmación de PPI. Todas las lecturas de esta adenda acreditan cero consultas propias a PPI y `real_orders_sent=0`. [Registro #1093](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7168398470)

## 26/11/2026 · Deploy V2 #211 y censo de READY del Droplet

**Estado:** `DESPLEGADO` localmente en PAPER/SIMULATION. `BLOQUEADO` en la verificación final de Promote V2 y sin aumento de READY en la rueda. El producto quedó en `d8850851b9ce3ebd5190cb98696f44191eaf2449` desde [#1284](https://github.com/mbalbo2023/Porota-trading/pull/1284). La fuente activa es `574c1ca1379ee7592b05b3f4401097b39d1e5f76` y el árbol `04aa6b05d4e788a702090f6af0c19a48154fe086`. [Recibo #211](https://github.com/mbalbo2023/Porota-trading/actions/runs/45857533968/artifacts/15137086626)

- El candidato conservó el reloj de Current al entregar observaciones tardías o repetidas y mantuvo el control de frescura del caché nativo. La rotación prioriza plazos acreditables y excluye claves con identidad económica ambigua. Si el par confirma el plazo, el Current alimenta el muestreo existente de velas de 1m/5m con su hora de fuente y volumen desconocido. Los índices con clave PPI verificada entran por observación. [Diff exacto](https://github.com/mbalbo2023/Porota-trading/commit/574c1ca1379ee7592b05b3f4401097b39d1e5f76)
- Predeploy V2 ejecutó **9.217 de 9.217** con cero fallos u omisiones sobre el artefacto `15135838119`. El SHA-256 del ZIP fue `8fbffa646b4798ad6e4664ebe54f0fe74b9548514713ec33a7228ed14f0da6d6`. La imagen en el Droplet coincide con la certificada, `sha256:041358d3cf56ff623a27f55a7720a9603f5ec8610929912eb38adee7e0c51f7c`. [Certificación](https://github.com/mbalbo2023/Porota-trading/actions/runs/45854551655/artifacts/15135838119)
- Se ejecutó una sola promoción. El host completó la instalación a las 13:26:53 ART, con dashboard saludable, observer en ejecución, ocho procesos listos, guard real bloqueado y `real_orders_sent=0`. El firmante de política de venta PAPER estaba activo para seis variantes. Limpieza Docker acreditada por 832.163.840 bytes. `ppi_watch_touched=false`. [Recibo de runtime](https://github.com/mbalbo2023/Porota-trading/actions/runs/45857533968/artifacts/15137086626)

### Censo comparable por familia

Cortes READ_ONLY a las 13:01:46 y 13:28:54 ART. La columna de catálogo cuenta filas elegibles antes del plan. READY corresponde a las identidades en el plan de apertura del último ciclo del escáner. Se contó sobre el mismo esquema de observación en ambos cortes, con cero peticiones PPI de la sonda y contador real cero. [Antes](https://github.com/mbalbo2023/Porota-trading/actions/runs/45854770610/artifacts/15136630476) · [Después](https://github.com/mbalbo2023/Porota-trading/actions/runs/45859113586/artifacts/15138010485)

| Familia | Universo observado | Elegibles de catálogo, antes → después | READY, antes → después | Primer bloqueo observado |
| --- | ---: | ---: | ---: | --- |
| Acciones | 206 | 0 → 0 | 0 → 0 | 171 plazos y 35 identidades PPI por acreditar |
| CEDEARs | 2.504 | 0 → 0 | 0 → 0 | 1.338 plazos y 1.166 identidades PPI por acreditar |
| ETFs | 0 | 0 → 0 | 0 → 0 | Sin filas en esta familia |
| Bonos | 3.108 | 1 → 1 | 0 → 0 | 2.497 plazos y 610 identidades PPI por acreditar. El elegible quedó fuera del plan |
| Letras | 59 | 0 → 0 | 0 → 0 | 43 plazos y 16 identidades PPI por acreditar |
| Obligaciones negociables | 5.175 | 0 → 0 | 0 → 0 | 5.127 contratos y 48 identidades ambiguas |
| Opciones | 5.003 | 0 → 0 | 0 → 0 | Contratos sin resolver |
| Futuros | 239 | 0 → 0 | 0 → 0 | Contrato o margen PAPER sin resolver |
| Cauciones | 10 | 0 → 0 | 0 → 0 | Plazo por acreditar. Worker en `WAITING_WINDOW` |
| FCI | 1.087 | 0 → 0 | 0 → 0 | 1.065 plazos y 22 identidades PPI por acreditar. Sigue pendiente la evidencia económica por clase |

### RCA y criterios de cierre

1. **PPI Book.** El 26/11 a las 13:28:55 ART se registró Current HTTP 200 con fecha de mercado 13:28:48. En los cuatro intentos registrados hasta el censo #526, el Current registrado fue seguido de `GET Book` permitido y de `PPI_NATIVE_RECEIPT_CLOCK_INVALID`, antes de que el par llegara al escáner. En el código exacto, este motivo sale de `publish_native_rest_receipt`, detrás de la validación del HTTP 200 por el productor. Para Book se aplican 5 segundos de antigüedad entre fecha nativa y recepción, más la validación de orden de los relojes. El recibo público no incluye el desfase de Book. La siguiente corrección debe exponer endpoint y relojes sanitizados antes del rechazo, distinguir Book vencido de un reloj adelantado y probar las dos ramas con captura y caché nativo. La secuencia volvió a ocurrir a las 13:39:54. Se observaron además HTTP 400 y nuevos 429 de Current con pausa del proveedor. La cuota queda por verificar. [Secuencia #526](https://github.com/mbalbo2023/Porota-trading/actions/runs/45860433150/artifacts/15137971794) · [Guard exacto](https://github.com/mbalbo2023/Porota-trading/blob/574c1ca1379ee7592b05b3f4401097b39d1e5f76/rc6_ppi_shared_feed.py#L585-L621) · [Productor](https://github.com/mbalbo2023/Porota-trading/blob/574c1ca1379ee7592b05b3f4401097b39d1e5f76/rc6_ppi_shared_rest_producer.py#L214-L240)
2. **IOL REST.** La fase física de #211 quedó en `COMPLETE`. El primer subchequeo REST fue el recolector y dio `IOL_REST_RELEASE_PROBE_UNVERIFIED`. El wrapper exige estado, código, identidad de release y comando de IOL compatibles, y publicó una firma agregada. No quedó recibo interno del subchequeo de promoción. La lectura posterior sí acreditó `AUTHENTICATED` a las 13:30:18 y un ciclo del recolector `COMPLETE` a las 13:37:20, ambos bajo el SHA activo. #527 corroboró el ciclo `COMPLETE` a las 13:39:49 y la autenticación a las 13:40:19. También registró que el timer omitió un turno a las 13:26:04 en `DEPLOY_IN_PROGRESS`, antes de terminar la instalación física y de ejecutar el subchequeo de promoción. El subchequeo sigue sin causa interna acreditada. [Secuencia #527](https://github.com/mbalbo2023/Porota-trading/actions/runs/45860966618/artifacts/15138816514) Para evitar otra firma genérica, el chequeo de promoción debe conservar el rol, estado, código y causa tipada sanitizados. [Wrapper exacto](https://github.com/mbalbo2023/Porota-trading/blob/574c1ca1379ee7592b05b3f4401097b39d1e5f76/scripts/porota_actions_host.py#L164-L173) · [Evidencia posterior](https://github.com/mbalbo2023/Porota-trading/actions/runs/45860433150/artifacts/15137971794)
3. **Apertura y ejecución.** El corte posterior registra `T10_RECEIPT_RELEASE_MISMATCH`. El T−10 de las 10:20:13 ART pertenece a `f04b6a9`. La próxima ventana ordinaria para emitir el recibo del activo es el 27/11 de 10:20 a 10:29 ART, sujeta al rearm y a preopen GREEN del SHA exacto. El tercer corte de las 13:41:01 confirmó 0 READY, 0 aperturas del día y `real_orders_sent=0`. Sigue pendiente ver un par Current/Book válido, publicación de plazo y economía por cada variante, y su admisión. En ambos cortes el máximo cursor de fills PAPER fue 304, con cero aperturas/cierres del día en el dashboard y cero rutas reales. No se registró una venta nueva con testigo posterior, ni una colocación de caución en este corte. Scalping estaba en ejecución con 0 seleccionados. [Admisión](https://github.com/mbalbo2023/Porota-trading/blob/574c1ca1379ee7592b05b3f4401097b39d1e5f76/rc6_paper_admission_gate.py#L200-L254) · [Medición](https://github.com/mbalbo2023/Porota-trading/actions/runs/45859113586/artifacts/15138010485)
4. **Histórico, velas y supervisión.** La nueva ruta de velas pasó las pruebas de candidato y quedó pendiente la primera confirmación nativa del flujo en rueda. La recuperación histórica del dashboard sigue `INCOMPLETE` y su escritura tiene ownership independiente. Las alertas de frescura, reporte de performance y cadencia del Market Watch siguen abiertas. El censo posterior preserva el detalle de los primeros bloqueos de las diez familias. [Censo posterior](https://github.com/mbalbo2023/Porota-trading/actions/runs/45859113586/artifacts/15138010485) · [Ownership de históricos](https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-7135331826)

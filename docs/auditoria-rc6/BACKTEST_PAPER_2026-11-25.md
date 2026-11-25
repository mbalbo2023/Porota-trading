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

PAPER/SHADOW. Órdenes reales bloqueadas. PPI Watch fuera de cambios. [Handoff](HANDOFF.md) · [Ownership](https://github.com/mbalbo2023/Porota-trading/issues/980)

# Backtesting y auditoría integral PAPER

Porota Trading RC6 · Corte documental del 24/11/2026 · Traspaso para la rueda del 25/11

Este dossier reúne los casos ejercitados, su alcance probatorio y el procedimiento para completar la auditoría integral. Las horas locales corresponden a `America/Argentina/Buenos_Aires` (ART). La bitácora de ejecución conserva también UTC.

## Estado y trazabilidad

El despliegue del plazo PPI quedó **VALIDADO_RUNTIME** con el mercado cerrado. El recibo del controlador está fechado el 24/11 a las 22:18:26 ART (25/11 a las 01:18:26 UTC). El **GO operativo del 25/11 permanece NO_VERIFICADO**. El próximo corte de rueda debe acreditar el preopen de las 10:20 ART para el release activo, la capacidad de EXIT y el primer dato fresco por familia.

| Capa | Evidencia del corte |
| --- | --- |
| Código | [PR #1270](https://github.com/mbalbo2023/Porota-trading/pull/1270). Producto `167ae9bc00de6f3de9d18183a10a8c2364c2e387`. Candidato `d5a656f9529bbc1749cd6ada1c098e218a8b6398`. Árbol `8f89464d79accab63733140cb07362564d4f9994`. |
| CI y paquete | [Predeploy V2 #1320](https://github.com/mbalbo2023/Porota-trading/actions/runs/45521938932/artifacts/14980649015). 9.174 pruebas, 0 skips, prueba funcional HTTP PAPER. Imagen `sha256:d35953f8993bd0fc6660c490df6678ce9f460b24b51277bde4aa7b4b2816c58b`. |
| Droplet | [Promote V2 #206](https://github.com/mbalbo2023/Porota-trading/actions/runs/45523776489/artifacts/14980838451). SHA-256 del ZIP `1c9b18d3deae441aa8b61dce3208cf9152fecb3133e26e7e8eff446b66a9929a`. Dashboard saludable, observer activo, SQLite en `PRODUCTION_PAPER / SIMULATION`, guard REAL `BLOCKED`, `real_orders_sent=0`. |
| Roles del controlador | `ppi_gateway`, `scanner`, `dynamic_shadow`, `exit_reader`, `intraday_scalping`, `caucion_cash_sweep`, `candles` y `notifications`. El recibo acredita el arranque. Queda por medir el último trabajo útil de cada rol en rueda. |
| Otras comprobaciones | Política de venta BONOS/ON `ACTIVE_PAPER` para seis variantes. IOL REST verificó GD30 y publicó el recolector fuera de ventana. La ejecución del recolector en ventana queda pendiente. |

El censo anterior a la corrección observó **0/17.384 READY**. Sus primeros bloqueos fueron 11.172 plazos, 6.027 identidades primarias y 185 evidencias vencidas. El corte de las 19:55:27 ART luego de #204 observó **0/17.473 READY**. Las cifras son recuentos de sus respectivos releases y ventanas. La lectura spot A655 del 24/11 observó cero fills en `paper_fills` a las 17:12:36 ART. [Censo intradiario](https://github.com/mbalbo2023/Porota-trading/commit/e9e35204b02b2e0da8092d31726aa4ce511e671e) · [Postflight #204](https://github.com/mbalbo2023/Porota-trading/actions/runs/45505318106/artifacts/14972172388) · [Ledger A655](https://github.com/mbalbo2023/Porota-trading/actions/runs/45478284088/artifacts/14961151613).

## RCA del plazo y de la recepción

`SearchInstrument` aporta ticker, tipo, mercado y moneda. El plazo se envía en la solicitud a `Current` y `Book`. La sonda registró siete respuestas Current y cinco Book HTTP 200 sin literal de plazo en el cuerpo. El Gateway retenía los recibos, el observador los archivaba como diagnóstico y el catálogo dejaba la fila `REQUEST_CANDIDATE` fuera del selector por falta de evidencia de plazo. [REST oficial PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/) · [Sonda física](https://github.com/mbalbo2023/Porota-trading/actions/runs/45457257511/artifacts/14951650853).

1. El Gateway del #1270 enlaza el par nativo Current/Book con la solicitud exacta, la generación, el catálogo, las huellas y sus relojes. El firmante local se ancla al proceso y al release. La firma acredita el recibo interno de captura.
2. El observador publica el recibo, actualiza el catálogo y recalcula ambas proyecciones PAPER en una transacción. Preserva `REQUEST_CANDIDATE` como procedencia del plazo pedido y conserva por separado el literal realmente observado. La cotización diagnóstica mantiene su propio resultado y el scanner toma otro par para evaluar una entrada.
3. En los instrumentos de libro de la prueba, la identidad única y el par completo permitieron pasar el selector y `paper_admission_guard` con reloj, datos, GO y condiciones de prueba. La ejecución termina con cero posiciones abiertas. La orden simulada, sus filtros de señal, riesgo y economía, el fill y el asiento requieren una prueba posterior por instrumento.
4. Cauciones y FCI tienen rutas diferenciadas. La sonda genérica pide sólo `Current` para esas familias y registra una referencia. Falta el libro de colocación de cauciones o el NAV y corte de la clase FCI para ejercer su admisión. Las pruebas de esas rutas se hicieron en módulos separados.

El dashboard de #1269 corregía otro origen del síntoma. `market_snapshots.observed_at` ya contenía la recepción persistida, pero las vistas usaban un alias distinto o buscaban una columna ausente. Se corrigió la proyección en #1269 y se validó por Promote #205 a las 20:55:26 ART. La frescura del dato, la vigencia de identidad y la visualización del reloj se comprueban por separado. [PR #1269](https://github.com/mbalbo2023/Porota-trading/pull/1269) · [Recibo #205](https://github.com/mbalbo2023/Porota-trading/actions/runs/45513092734/artifacts/14976144081).

La primera CI de #1270 comparó el SHA sintético del fixture con el SHA de la imagen y lo rechazó. El fixture pasó a fijar sus propios SHAs y prueba el rechazo con un SHA diferente. La defensa del release quedó ejercitada en Predeploy. [CI inicial](https://github.com/mbalbo2023/Porota-trading/actions/runs/45520005231) · [RCA y verificación](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7148830673).

## Los diez casos de admisión por familia

La prueba parametrizada del [Gateway de diez familias](https://github.com/mbalbo2023/Porota-trading/blob/d5a656f9529bbc1749cd6ada1c098e218a8b6398/tests/test_rc6_ppi_gateway_family_ready.py) se ejecutó fuera del Droplet. Usa SQLite temporal, SDK y guard del proyecto, transporte HTTP inyectado, préstamo de presupuesto ficticio y reloj de recepción repetible. El GO global del fixture está fijado para aislar la barrera por instrumento. Las diez claves tienen moneda ARS.

| Familia | Instrumento y coordenadas restantes | Última etapa comprobada en la simulación |
| --- | --- | --- |
| ACCIONES | GGAL · BYMA · A-24HS | Recibo firmado, selector y guard de admisión PAPER. |
| CEDEARS | AAPL · BYMA · A-24HS | Recibo firmado, selector y guard de admisión PAPER. |
| ETFS | SPY · BYMA · A-24HS | Recibo firmado, selector y guard de admisión PAPER sobre la identidad de prueba. |
| BONOS | AL30 · BYMA · A-24HS | Admisión con términos económicos de renta fija de prueba. |
| LETRAS | QA_LETRA · BYMA · A-24HS | Admisión con términos económicos de renta fija de prueba. |
| OBLIGACIONES | QA_ON · BYMA · A-24HS | Admisión con términos económicos de renta fija de prueba. |
| OPCIONES | YPFC40500D · BYMA · INMEDIATA | Admisión con YPFD como subyacente de prueba y evidencia económica V2. |
| FUTUROS | DLR/NOV26 · A3 · INMEDIATA | Admisión con contrato complementado por evidencia económica V2. |
| CAUCIONES | PESOS1 · BYMA · INMEDIATA | Referencia `CAUCION_CURRENT_REFERENCE_OBSERVED`. HOLD de la clave con `Current` genérico. |
| FCI | QA_FCI · FCI · INMEDIATA | Referencia `FCI_CURRENT_REFERENCE_OBSERVED`. HOLD de la clave con `Current` genérico. |

En los ocho casos de libro se observó primero el bloqueo de liquidación y luego el par nativo de prueba. Un segundo par, con reloj avanzado, pasó la verificación de vigencia del scanner. La aserción final prueba admisión y cero posiciones abiertas. La clasificación y operativa de cada familia en el catálogo físico requieren sus propios recibos de rueda.

## Casos especializados y rechazos de contrato

| Caso ya ejercitado | Paso probado y resultado |
| --- | --- |
| PESOS3 · Colocación A232 | Fixture de catálogo, libro vigente y recibo histórico PPI explícitos. Colocación PAPER de ARS 100.000 con costo positivo y un débito de capital. Repetir la petición conserva el mismo `paper_id`. Fuente simulada sin autoridad física. |
| PESOS3 · Defensas | Rechazo sin asiento nuevo ante candidato revocado, fuente de libro sustituida, libro vencido, autoridad de costos alterada y recibo futuro, vencido o ausente. Una etiqueta de metadatos inventada carece de autoridad. |
| SYNTHETIC_FCI · Admisión A232 | La clave de cinco campos del fixture pasó catálogo. El plazo sólo propuesto frenó el caso. Aun con GO y catálogo, faltó calendario autenticado de la clase. Sábado, feriado y fuera de ventana bloquearon FCI y cauciones. |
| SYNTHETIC.FCI.C1 · Suscripción | El asignador ejercitó `SUBSCRIBE_REQUESTED`, débito de ARS 1.000 y un evento durable. El reconciliador dejó las cuotas indeterminadas al faltar los recibos de asignación. Reintento idempotente, política de costos y piso de caja cubiertos. |
| FCI.FIXTURE.B · Liquidación | Con una suscripción PAPER previa de fixture, se aplicaron recibos de prueba firmados para NAV, asignación de 100 cuotas y el ciclo de rescate. Pasaron idempotencia, concurrencia, rechazo de pruebas inválidas y reversión atómica de la transacción ante escritura fallida. |
| AL30 · Recibo de liquidación | Se probó la firma esperada, ausencia de anclaje, PID distinto, firma ajena, otro release, plazo o digest incompatibles, corte futuro o vencido y procedencia complementaria. Se rechazaron Current perdido, otro instrumento, literal contradictorio, cuerpo alterado, otra generación, lease repetido, par vencido y segunda moneda o mercado ambiguos. |

Rutas de referencia. [A232 cauciones y FCI](https://github.com/mbalbo2023/Porota-trading/blob/d5a656f9529bbc1749cd6ada1c098e218a8b6398/tests/test_rc6_a232_fund_caucion_market_entry.py) · [Asignador FCI](https://github.com/mbalbo2023/Porota-trading/blob/d5a656f9529bbc1749cd6ada1c098e218a8b6398/tests/test_rc6_fund_paper_allocator.py) · [Liquidación atestiguada](https://github.com/mbalbo2023/Porota-trading/blob/d5a656f9529bbc1749cd6ada1c098e218a8b6398/tests/test_rc6_fund_attested_settlement.py) · [Recibo nativo](https://github.com/mbalbo2023/Porota-trading/blob/d5a656f9529bbc1749cd6ada1c098e218a8b6398/tests/test_rc6_ppi_native_request_receipt.py).

## Replay de rueda y antecedente BONOS/ON

El replay READ_ONLY consultó seis filas reales persistidas de `market_snapshots` del 24/11, sin pedidos al proveedor ni uso de rutas de órdenes. Recalculó la vigencia inmediatamente antes de la recepción, con `as_of=observed_at` y al corte de 19:55:27 ART. Usó las ventanas del ensayo de 120 s para libro y recepción y 900 s para trade. Conservó los relojes nativos.

| Clave local | Pares registrados | Antes de recibir | En recepción | Al corte |
| --- | --- | --- | --- | --- |
| AL30 · BONOS · BYMA · ARS · A-24HS | 4 | 4 rechazados | 4 frescos, libro a dos puntas | 4 vencidos |
| GGAL · ACCIONES · BYMA · ARS · A-24HS | 2 | 2 rechazados | 2 frescos, libro a dos puntas | 2 vencidos |

El recibo fija `authority=CLOCK_REPLAY_ONLY`, `provider_requests=0`, `order_routes=0` y `real_orders_sent=0`. El replay acredita el filtro temporal sobre registros reales del corte #204. [JSON y recibo del replay](https://github.com/mbalbo2023/Porota-trading/actions/runs/45508220641/artifacts/14975086071).

El estudio anterior de **salida** de renta fija reutilizó puntas históricas con relojes y firmas de prueba. Acreditó escenarios del supervisor y asiento PAPER simulado. [Reporte de seis variantes](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7112589354) · [Implementación #1255](https://github.com/mbalbo2023/Porota-trading/pull/1255).

| Variante | Resultado observado en la simulación anterior |
| --- | --- |
| GD30 | 61 VN. |
| GD30D | 0 VN por profundidad insuficiente. |
| GD30C | 19 VN. |
| YMCJO, YMCJD, YMCJC | 100 VN por variante. |

El certificado actual de `ACTIVE_PAPER` corresponde a la política y al firmante del release. Para acreditar una venta de rueda por variante se necesita la decisión comprometida, ACK firmado del lector EXIT, primer libro nativo elegible posterior en la misma generación, aplicación contable e idempotencia. Esa secuencia quedó `NO_VERIFICADO` en el 24/11.

## Paso a paso del próximo backtesting

1. **Fijar el corte.** Leer AGENTS.md, #1093 y #980. Reconciliar SHA de producto, imagen, artefacto y recibo físico. Iniciar READ_ONLY mediante Actions. Verificar modo, guard REAL y contador. Obtener el preopen del 25/11 y la capacidad EXIT con recibos vigentes para la fuente desplegada.
2. **Congelar un universo por familia.** Exportar el catálogo acreditable y su denominador por ticker, familia, mercado, moneda y plazo. Conservar clases, alias, vencimientos y particiones de moneda. Registrar toda clave ausente, ambigua, inelegible o pendiente con primer motivo. Si se corre por lotes, conservar el total y el saldo sin ejecutar. Usar diez familias operativas y registrar explícitamente las identidades de sólo referencia.
3. **Preparar el replay.** Usar datos sanitizados con origen y hashes, calendario, términos vigentes, cortes ART/UTC y versiones de política. Mantener copia SQLite privada y reproducible. Trabajar por evento y `known_at`; conservar la diferencia entre dato real reproducido y fixture. Reservar las pruebas sintéticas para ramas positivas y adversariales.
4. **Seguir fuente e identidad.** Para cada clave, registrar evidencia de catálogo, literal o recibo exacto, `Current`/`Book` o fuente especializada, cuota, lease, generación, hora nativa y recepción. Probar la degradación por dato viejo, futuro, identidad distinta, libro sin punta, autorización vencida o capacidad insuficiente.
5. **Ejecutar el camino normal.** Pasar la clave por los filtros aplicables de screening, DISCOVERY, WARM, HOT, señal, decisión, contrato económico, comisiones, caja, exposición, margen y cobertura EXIT. Registrar etapa aplicable, entrada, salida, reloj y razón. Un segmento o etapa ausente necesita una clasificación con evidencia. La prueba de punta a punta debe ejercer los guards vigentes con evidencia coherente al corte.
6. **Simular la entrada.** Llevar cada caso elegible hasta la orden y fill PAPER en el almacén aislado usando el motor de su familia. Caución usa colocación y FCI usa suscripción y asignación. Verificar precio, lotes, profundidad, nominal/multiplicador, liquidación, redondeos, costos y caja. Ejercer fill total, parcial, rechazo y reintento cuando el contrato los contempla.
7. **Conciliar posición y salida.** Reconciliar ID de decisión con orden, fill, posición y movimientos. Probar liquidación y salida aplicables, incluidos Book2 causal en BONOS/ON, vencimiento de caución y NAV/rescate en FCI. Verificar que cada fallo preserve el ledger y que los reintentos no dupliquen débitos ni eventos.
8. **Publicar evidencia por capa.** Entregar manifiesto del universo, resultados por clave y motor, primer gate y asientos, hashes, faltantes y controles adversariales. Reportar cobertura por familia, filtros aplicados, admitidos, órdenes simuladas, fills, pendientes y motivos. Concluir cada caso sólo al nivel de evidencia alcanzado. Adjuntar el PDF de resultados y fijar en GitHub el run y el manifiesto que permiten reproducirlo.

## Universo y controles por familia

| Familia | Ruta a ejercer y ramas que se deben discriminar |
| --- | --- |
| ACCIONES | Puntas y trades de la acción por plazo y moneda. Señal y compra PAPER. Profundidad, costos, tenencia y salida del motor spot. |
| CEDEARS | Clave del certificado, ratio si corresponde al contrato vigente y moneda. Control de alias, precio escalado, lotes y cotización nativa. |
| ETFS | Clasificación, mercado y alias desde catálogo hasta dashboard y selector. Probar el recorrido efectivo del ETF y límite por identidad. La admisión del fixture SPY ya está cubierta. |
| BONOS | Base de cotización, VN, moneda, denominación y cupón/amortización aplicables. Conciliar fill y profundidad. En venta, verificar identidad, decisión, ACK y Book2. |
| LETRAS | Unidad nominal, vencimiento y flujo económico al corte. Ejercer compra PAPER, invalidación por vencimiento y registro de liquidación. |
| OBLIGACIONES | Identidad de ON por moneda y variante de negociación. Precio por VN, términos y salida auditada sobre YMCJO/YMCJD/YMCJC si hay posición elegible o fixture explícito. |
| OPCIONES | Serie, subyacente, call/put, strike, vencimiento y multiplicador. Política efectiva, orden sobre prima y rechazo de serie incongruente o vencida. |
| FUTUROS | Serie A3 exacta, lote, tick, multiplicador, margen y vencimiento. Apertura, variación, caja y cierre del contrato PAPER. |
| CAUCIONES | `caucion_cash_sweep`, `dj_caucion_live_ppi_rc6.py` y `bt_caucion_paper.py`. Tenor, TNA, punta colocadora, capital disponible, costos y fecha de madurez. Ejercer ventana, colocación, reserva de caja y liquidación. Registrar variantes ARS/USD habilitadas, ofertas vacías, libro vencido y contrato incompleto. |
| FCI | `rc6_fund_paper_allocator.py` y `rc6_fund_paper_reconciler.py`. Acreditar clase, moneda, NAV con reloj de publicación/recepción, calendario, corte, costos, caja y fuente independiente. Ejercer suscripción, asignación de cuotas, rescate y conciliación. Registrar separadamente PPI vigente y referencia CAFCI o IOL si aplica. |

Para las rutas comunes, seguir `bf_production_paper_observer.py`, `rc6_shadow_runtime/funnel.py`, `rc6_paper_admission_gate.py`, `be_paper_engine.py` y los ejecutores por familia. El alcance del recorrido se obtiene de la política efectiva del release. Un motivo del scanner puede preceder a señales, economía y riesgo. Conservar el rechazo de una identidad inválida como resultado esperado del caso de control.

## Auditoría operativa, velas e históricos

| Frente | Evidencia de partida y próximo control |
| --- | --- |
| Preopen y EXIT | Comparar el recibo de las 10:20 ART con el SHA activo, horario de rueda, permisos del Gateway, sesión del proveedor, cuota y capacidad de cubrir salidas. Capturar primer Current/Book fresco por familia y seguir el primer gate. El último censo de rueda precede a #1270. |
| Scalping | `intraday_scalping` está en el controlador. El corte del 24/11 observó `SECURITY_BLOCK`, cero seleccionados y una ventana SHADOW de cero puntos. Revisar `cf_intraday_scalping.py`, último ciclo útil, presupuesto, ticks/velas intradía, HOT, señal, órdenes PAPER y salida. Probar los motivos de falta de datos, presupuesto, sesión, costos y riesgo con sus relojes. |
| Cauciones y FCI en rueda | Certificar el sweep con libro vigente, TNA, cuenta de costos, caja y evento de colocación. Certificar por clase FCI su calendario, NAV y recibos de suscripción/asignación. El corte físico previo registró cauciones `WAITING_WINDOW`. |
| Velas 1m y 5m | Reconciliar `TRADE` en `market_snapshots`, cursor de `bl_candle_engine.py`, ventanas ART, OHLCV, volumen, revisión y `known_at`. Cotejar 1m/5m con los eventos, minutos sin trades, duplicados y desorden. Registrar ticks/filas consumidos, edad del último evento, barras nuevas, lag y delta EOD a COLD. |
| Históricos | Verificar el scheduler postclose, `history_source_policy_rc6.py`, `fb_history_reconciler_rc6.py` y escritores nativos/externos. El cutoff 21/09 quedó `INCOMPLETE` con 195/1.094 series pendientes. Las referencias `SOURCE_REPORTED` tienen sus recibos. A3 acreditó 1.192 referencias en 13 series y 0 RAW exactos en su corte. Inventariar por clave, fecha y resolución, distinguir RAW/ajustado y procedencia, y comprobar huecos, duplicados, revisiones y reanudación con copia restaurable. |
| Salud, SQLite y alertas | Reconciliar los 32 timers del inventario con `Unit=`, `InvocationID`, horarios, resultados y siguiente disparo. Medir los ocho roles por trabajo útil y último éxito y conciliar dashboard con la base. Verificar acceso SQLite, WAL, backups y guard REAL. Para Telegram ligar clasificación, outbox y ACK. Revisar los casos de desacuerdo de sesión, 401/429 y falta de frescura. |

Cortes de referencia. [RCA, cronología, embudo y alertas](RCA_RUEDA_2026-11-24.md) · [Inventario histórico de timers](TIMERS.md) · [A3 y procedencia](https://github.com/mbalbo2023/Porota-trading/actions/runs/45395647061/artifacts/14920414408). Performance #1166 sigue como frente independiente. Reportar las métricas que corroboren el recorrido ensayado y sus condiciones de captura.

## Reproducción y entregables de evidencia

El CI de la imagen exacta validó 9.174 pruebas. El ensayo focal local del candidato corrigió coordenadas de release y registró **362 PASS, 1 caso AF_UNIX omitido por el entorno local y 21 subtests PASS**. Su núcleo puede reproducirse con el candidato fijado y con los fixtures y stores temporales existentes.

```sh
python -m pytest -q -o addopts= \
  tests/test_rc6_ppi_native_request_receipt.py \
  tests/test_rc6_ppi_gateway_family_ready.py \
  tests/test_rc6_a232_fund_caucion_market_entry.py \
  tests/test_rc6_fund_paper_allocator.py \
  tests/test_rc6_fund_attested_settlement.py
```

| Cobertura complementaria del ensayo focal | Suites en `tests/` con prefijo `test_rc6_` |
| --- | --- |
| Origen y transporte PPI | `ppi_spot_catalog_wire.py`, `ppi_direct_rest_recovery.py`, `ppi_rest_recovery_probe.py`, `a678_gateway_reader_adapter.py`, `ppi_global_gateway_runtime.py`. |
| Integridad de contrato | `complementary_catalog_merge.py`, `r136_option_current_admission.py`, `a762_book1_fivekey_guard.py`. |
| Fuentes y reconciliación FCI | `fund_source_quarantine.py`, `fund_paper_reconciler.py`, `fund_ppi_catalog_market.py`. |
| Contrato cauciones | `cauciones_contract.py`, `p0a2_mass_caucion_fivekey.py`. |

Para cada estudio futuro, guardar `caso_id`, identidad y familia, `as_of`, procedencia del dato, SHA/árbol/imagen, entrada y salida de cada gate, política, motivo, primer bloqueo, decisión, orden PAPER, fill, asiento y saldos conciliados. Cada run conserva manifiesto y cobertura, un catálogo de motivos, los hashes de la evidencia sanitizada y un informe actualizado. Guardar separados los asientos de prueba, los observados en producción PAPER y el conteo físico de rutas reales.

**Continuidad.** Iniciar en [AGENTS.md](../../AGENTS.md) y [HANDOFF.md](HANDOFF.md). Registrar el nuevo corte en [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y verificar ownership en [#980](https://github.com/mbalbo2023/Porota-trading/issues/980). El alcance de este dossier es evidencia y preparación de la auditoría. Los casos integrales de la nueva rueda, el GO, la reconstrucción RAW y el backtest completo por universo permanecen pendientes de sus recibos.

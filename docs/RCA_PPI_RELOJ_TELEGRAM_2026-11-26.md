# Porota RC6 · Reloj PPI, solicitudes y avisos de Telegram

Auditoría del 26/11/2026 sobre Deploy V2 #212, fuente `414771d203cf1d0bff4e00c803ef722309dca064` y producto base `a6cc6e3ddcb18cde1e4aeb87752212c5bf925d36`. Al corte `2026-11-26T19:32:30.689669Z` (16:32:30 ART), el observador estaba vivo en `PRODUCTION_PAPER`, en `COOLDOWN` y `DEGRADED`. El recibo registra `real_orders_sent=0`, cero rutas reales llamadas, cero reinicios de contenedor y cero OOM. [Promoción #212](https://github.com/mbalbo2023/Porota-trading/actions/runs/45871190344) · [Recibo READ_ONLY #534](https://github.com/mbalbo2023/Porota-trading/actions/runs/45884363181/artifacts/15148643605)

## Ventana de 120 segundos

El firmante de la evidencia de liquidación exige que Current y Book tengan como máximo 120 s de edad en sus respectivos recibos HTTP y que ambos recibos estén separados por como máximo 120 s. Este es un criterio local de prueba para la identidad exacta del instrumento. PPI devuelve el reloj propio de cada dato en sus endpoints Current y Book. [Contrato del firmante](../rc6_ppi_native_request_receipt.py) · [Documentación PPI REST](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/)

| Etapa | Criterio |
| --- | --- |
| Caché nativa y prueba del primer libro posterior a una decisión | 5 s en la configuración observada. La observación spot más antigua conserva el reloj original fuera de esa caché. |
| Observación de plazo spot | Current y Book con identidad exacta, recibos HTTP originales, precios válidos y causalidad comprobada. La edad del Current se mide en el recibo. |
| Entrada PAPER | `PaperBroker` aplica la ventana configurada del último negocio y del libro al momento de decidir. Los valores por defecto son 900 s y 120 s, respectivamente. También exige las demás autorizaciones y controles propios del instrumento. |

El caso reproducido llevaba 119,9 s de edad en recepción. Después de 200 ms de IPC, el escáner lo comparaba con 120 s en su propio reloj y omitía Book. La prueba de extremo a extremo detectó otra comparación tardía después de Book y una tercera en el consumidor de velas. El candidato usa el reloj del recibo para acreditar Current, valida Book al observar la muestra y conserva el control del broker al decidir. El materializador conserva `fuente ≤ recepción ≤ verificación`, el origen `PPI_GUARDED_CURRENT` y la calidad `SAMPLED` con `known_at` del cierre publicado. La prueba preserva el corte para un Current con 121 s en recepción y la prioridad de EXIT.

La cadencia del escáner tampoco fija la edad de un precio. Un Current solicitado en ese turno puede devolver 200 con la fecha de una operación anterior. La evidencia de Book para ese par se detiene cuando Current ya llega vencido o PPI lo rechaza con 429. El registro confirma Books 200 en otros turnos y planes de consulta. [Documentación de Current y Book](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/)

## Solicitudes al Gateway

La sonda #534 leyó el registro de PAPER entre `2026-11-26T13:30:00Z` (10:30 ART) y `2026-11-26T19:32:37.238973Z` (16:32:37 ART). Encontró 418 admisiones de solicitudes de mercado. El máximo observado fue de tres admisiones en el segundo `2026-11-26T19:26:53Z` (16:26:53 ART). El registro `ALLOWED` se escribe antes de aplicar la autorización final del adaptador y puede incluir solicitudes que encuentran contrapresión. El contador físico `guard_http_allowed` se incrementa justo antes de invocar `HTTPAdapter.send`. [Guarda en el SHA físico](https://github.com/mbalbo2023/Porota-trading/blob/414771d203cf1d0bff4e00c803ef722309dca064/bd_ppi_readonly_guard.py#L517-L644) · [Sonda #534](https://github.com/mbalbo2023/Porota-trading/actions/runs/45884363181/artifacts/15148643605)

| Endpoint | Admisiones en la ventana |
| --- | ---: |
| Current | 329 |
| Book | 59 |
| Intraday | 30 |

La sonda #535 midió el contador físico desde `2026-11-26T19:38:45.765975Z` (16:38:45 ART). En 8,002 s y ocho intervalos próximos a un segundo hubo **cero invocaciones al adaptador**, promedio y pico de 0 en esa ventana. El Gateway informó último rechazo HTTP 401 y 388 s de pausa. La misma lectura confirmó las 418 admisiones y el pico previo de tres. Estos registros identifican dos condiciones operativas distintas y permiten verificar la cadencia de salida durante la pausa. [Contador físico #535](https://github.com/mbalbo2023/Porota-trading/actions/runs/45885128428/artifacts/15149380401)

La segunda muestra física #537 arrancó a las 16:49:08 ART (`2026-11-26T19:49:08.490250Z`). Otro 429 de PPI a las 16:49:02.981 inició una nueva pausa. El proveedor informó `Retry-After=1` y el Gateway aún tenía 55 s de resguardo local. Se midieron **0 invocaciones en 8,002 s**. El log de la rueda al segundo corte sumó 440 admisiones (332 Current, 75 Book y 33 Intraday), con el mismo pico de tres en un segundo. En los 90 s anteriores al corte hubo 13 admisiones (3 Current y 10 Book), promedio 0,1444 admisiones/s. Son registros de solicitudes antes de la salida. Los timestamps de la muestra física verifican la tasa durante ambas pausas. [Recibo #537](https://github.com/mbalbo2023/Porota-trading/actions/runs/45886394733/artifacts/15149526785)

Los 17.483 registros corresponden al censo de catálogo. El escáner rota una selección acotada con límite por defecto de 20 instrumentos por turno y una cadencia configurable. El Gateway es el dueño del envío y serializa la salida. En el último ciclo de #537 el escáner eligió dos instrumentos y recibió dos cotizaciones útiles. El primer intento de descubrimiento terminó en `PPI_NATIVE_HTTP_429`, con cero pares frescos. El último corte contiene 17 timestamps de 429 nativos en la muestra acotada de motivos. [Sonda #537](https://github.com/mbalbo2023/Porota-trading/actions/runs/45886394733/artifacts/15149526785)

## Telegram y cronología

El outbox confirma 30 avisos operativos entregados en la misma ventana. El componente de datos originó 13 degradaciones y 12 recuperaciones. Las 12 recuperaciones de datos informaban un solo instrumento con cotización útil. Los otros cinco avisos correspondían a autenticación. El outbox muestra ACK al primer intento y cero errores finales 429 de Telegram en esta muestra. [Sonda #534](https://github.com/mbalbo2023/Porota-trading/actions/runs/45884363181/artifacts/15148643605)

| HTTP 429 de PPI | Aviso encolado | ACK de Telegram | Muestra del aviso |
| --- | --- | --- | --- |
| 16:09:44.712 ART, `2026-11-26T19:09:44.712145Z` | 16:09:45.780 ART, `2026-11-26T19:09:45.780240Z` | 16:09:46.304 ART, `2026-11-26T19:09:46.304712Z` | 1/1 |
| 16:17:03.816 ART, `2026-11-26T19:17:03.816265Z` | 16:17:04.888 ART, `2026-11-26T19:17:04.888909Z` | 16:17:05.370 ART, `2026-11-26T19:17:05.370008Z` | 1/1 |
| 16:49:02.981 ART, `2026-11-26T19:49:02.981176Z` | 16:49:04.256 ART, `2026-11-26T19:49:04.256594Z` | 16:49:04.781 ART, `2026-11-26T19:49:04.781907Z` | 2/2 con 429 en descubrimiento |

El outbox al segundo corte tenía 32 entregas confirmadas al primer intento y cero códigos 429 de Telegram. De las 13 recuperaciones de datos, 12 venían de una sola cotización útil y la última de dos. Otras seis entregas (tres degradaciones y tres recuperaciones) correspondían a autenticación. [Recibo #537](https://github.com/mbalbo2023/Porota-trading/actions/runs/45886394733/artifacts/15149526785)

El escáner alternaba `VERDE` con cualquier cotización útil y `ROJO` al quedar sin cotizaciones en un ciclo que había solicitado datos. La deduplicación anterior dependía del último OK, que avanzaba con cada éxito. El remitente de Telegram vive en un proceso propio, llama `api.telegram.org` y libera la transacción de SQLite antes de enviar. En los ejemplos de la tabla, el 429 se registró antes de encolar y entregar el aviso. El recibo distinguió degradación observada y proceso Docker vivo. [Productor de estado en #212](https://github.com/mbalbo2023/Porota-trading/blob/414771d203cf1d0bff4e00c803ef722309dca064/bf_production_paper_observer.py#L3789-L3809) · [Transporte Telegram en #212](https://github.com/mbalbo2023/Porota-trading/blob/414771d203cf1d0bff4e00c803ef722309dca064/bn_telegram_bus.py#L68-L96)

El candidato conserva un incidente de datos por episodio mediante estado transaccional en SQLite. Publica `DATOS PPI DEGRADADOS` una vez y `DATOS PPI ESTABLES EN LA MUESTRA` tras dos ciclos distintos, válidos y consecutivos del mismo día de rueda. Si el Gateway declara pausa o sesión inválida, el ciclo corta la racha aunque tenga cotizaciones útiles. La regresión reproduce el 2/2 con 429 de las 16:49 y verifica que tampoco se recupere tras un verde aislado. La migración conserva un aviso pendiente o ya confirmado de la versión anterior. Las pruebas verifican reinicio, deduplicación y recuperación de una falla de escritura. La cobertura del último ciclo viaja en el aviso y el broker conserva sus controles PAPER por instrumento.

## Cierre y evidencia pendiente de promoción

- Suite focal del producto con dependencias del lock y Python 3.12.14. Resultado 222 pruebas aprobadas y dos omitidas por falta de `AF_UNIX` en el ejecutor local. La prueba de frontera recorrió SDK, Gateway, candidato, validador PAPER y velas 1m/5m con HTTP simulado. Las sondas #535 y #537 observaron el socket y el dueño físico del release #212.
- Suite de la sonda de control con 125 pruebas y 57 subpruebas aprobadas. La medición #535 acreditó ambos registros de forma independiente desde el workflow de lectura existente.
- La cuota efectiva de PPI requiere atribución por endpoint y ventana en registros del proveedor. El recibo disponible contiene 429 y `Retry-After`; la cadencia local, el pico de admisiones y los ACK del outbox no acreditan una cuota contractual. Solicitar a PPI la política aplicada con los timestamps UTC y conservar el arbitraje fail-closed mientras se documenta la respuesta.
- El control de rueda tras una promoción debe acreditar en el SHA instalado un par Current/Book fresco, la liquidación por identidad, los dos cierres de vela y las transiciones de la muestra en el outbox, con `real_orders_sent=0`. La sonda de este RCA se ejecutó en `read_only` y dejó la entrega en `NOT_REQUESTED`.
- La auditoría de control conserva dos salidas independientes para contadores IPC y SQLite. El primer intento excedió el límite de 30 s del ejecutor rootless. El siguiente preservó la evidencia del outbox aun con IPC sin recibo. La muestra breve mantiene el límite del ejecutor. [Intento #533](https://github.com/mbalbo2023/Porota-trading/actions/runs/45883432652) · [Recibo de outbox #534](https://github.com/mbalbo2023/Porota-trading/actions/runs/45884363181/artifacts/15148643605) · [Muestra breve #535](https://github.com/mbalbo2023/Porota-trading/actions/runs/45885128428)

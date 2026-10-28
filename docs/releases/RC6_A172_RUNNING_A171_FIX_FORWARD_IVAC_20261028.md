# RC6 A172 — continuación exacta del runtime A171 (28 Oct 2026)

## Incidente y causa

El Deploy V2 #40771934853 instaló y arrancó el candidato A171
`4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6`, pero terminó RED en
`T_MINUS_45`: `required_rc6_timers` observó fallido el servicio del guardian
OAuth IOL. El timer estaba activo. La frescura IOL fue AMBER y no bloqueó.
El cierre conservó la pareja nueva RUNNING, con recibo
`FIX_FORWARD_VALIDATION_REQUIRED`; no hay autorización de PAPER GO ni rollback.

Las sondas de sólo lectura #40773641026 y #40775048585 probaron en el
Droplet 594077619/nyc1 el SHA256 del recibo
`7a5e73359e85ef99796607228876443680f56bfe26ce201f4664474f700eabb5`,
el source instalado `rc6_deploy_quiescence.py` SHA256
`3b7317deaffbd08d556c35584953e416d8aa02a5dccd21c059347bd121e5b373`,
el marker del merge `d9e2a4d7e55b27e9799ae6db44dfda9a013912c6`
en `DEPLOYED_VALIDATION_PENDING`, la imagen
`sha256:e61052af13c01d9ce76e92f38b50c794dac8a146427e132d43a8a80554a879aa`,
y los IDs del observer/dashboard registrados por la ruta A172. El libro está
plano en `WAITING_MARKET/MARKET_CLOSED`, `PRODUCTION_PAPER`, cero órdenes
reales, 16 timers retenidos y barrera ausente. #40775048585 también comprobó
que el guardian recuperó `AUTHENTICATED` en el ciclo 21:45 UTC.

## IVAC de la corrección

La ruta A172 fija la procedencia A171 exacta: recibo, cadena A143/A169/A170,
manifest congelado, marker de merge, imagen, IDs y estados de ambos
contenedores, source instalado, libro plano y hold A151 fechado. Sólo acepta
un candidato nuevo y el modo `CLOSED_A171_PENDING_FIX_FORWARD`. Escribe el
intento durable antes de detener únicamente la pareja A171, comprueba salida
limpia y conserva la prueba de procedencia hasta instalar y validar la nueva
pareja. Scheduler, COLD2 y restatement comprueban el mismo modo y recibo;
ningún camino infiere GREEN del resultado parcial A171. PPI Watch se observa
solamente por estado; no se inicia el mercado ni se envía una orden real.

La corrección OAuth complementaria está documentada en
`docs/audit/RC6_A172_IOL_GUARDIAN_RETRY_IVAC_20261028.md`. Las pruebas
focales de continuidad (297 casos en diez archivos), guardian (106) y las
pruebas cruzadas de integración (69) pasaron localmente. La admisión final
exige Predeploy V2 completo en el SHA/tree congelados, preflight de espacio
recalculado, aprobaciones literales #624 y Deploy V2 canónico. El resultado
fuera de horario sólo puede certificar CLOSED; OPEN requiere observación real
de otra rueda. NO_GO operativo continúa hasta una reactivación explícita.

## Replay del primer Predeploy A172

El primer SHA A172 `3be1e9cbed86ee8ddecd1b33370615e1b84e7f6f` pasó
política, sintaxis, fixtures negativos y replay de transición, pero el
Predeploy #40776096632 terminó RED **antes de construir o transferir un
artefacto**: 5.384 pruebas ejecutadas, tres fallos y cero skips/errores.
Las tres aserciones inspeccionaban texto literal anterior del workflow:
nombre de la variable de auditoría de caución, lista de modos del gate de
aprobación intradiaria y cantidad fija de llamadas a `gzip`. El código nuevo
usa la auditoría acotada a `CLOSED_CAUCION`, exige también la aprobación
literal para el modo A171 exacto y comprime por stdin con `-n` en todas las
rutas. Se corrigieron las pruebas para exigir esas propiedades, incluso la
nueva ruta, sin reducir el gate; 46 casos dirigidos pasaron localmente.
El SHA anterior queda RED y no es promovible: el sucesor requiere Predeploy
V2 completo y un artefacto propio.

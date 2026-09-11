# POROTA TRADING RC6 — CHECKPOINT FINAL CP11–CP14 — DEPLOY GREEN

Fecha de cierre: 2026-09-11
Repositorio: `mbalbo2023/Porota-trading`
Rama de continuidad: `fix/rc6-w10-sector-map-binding-20260910`

## 1. Estado global

**GLOBAL_RC6=GREEN**

- CP1–CP10: GREEN / FROZEN. No reabrir ni repetir pruebas certificadas.
- CP11: GREEN / FROZEN. Workflow final, candidato exacto y guards de seguridad identificados.
- CP12: GREEN / FROZEN. Candidato RC6 activado en runtime.
- CP13: GREEN / FROZEN. Postdeploy/postflight certificado.
- CP14: GREEN / FROZEN. Soak final completado y seguridad final certificada.

Regla de continuidad: no rollback y no reapertura de checkpoints cerrados. Ante una falla nueva: RCA -> forward fix -> revalidar exclusivamente el tramo afectado -> continuar.

## 2. Identidad desplegada

- Runtime/source SHA: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- Commit funcional: `fix(rc6): validate and promote CP4 zero-candle forward fix`
- Imagen canónica: `porota-trading-bot:17.0.0-rc6`
- Image ID certificado: `sha256:77a3e6e5f8528e70d502f6f98efe8262aa5ee3adae475b30a7cc644b1630a8bd`
- Observer y dashboard ejecutan exactamente esa imagen.
- Host runtime HEAD certificado: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`

La rama GitHub contiene commits de control/documentación posteriores al runtime SHA. Esto es intencional: no cambia el código funcional desplegado.

## 3. Seguridad inmutable

Estado final certificado:

- VERSION=`17.0.0-rc6`
- MODE=`PRODUCTION_PAPER`
- EXECUTION=`SIMULATED`
- REAL_ORDER_CAPABILITY=`BLOCKED`
- `real_orders_sent=0`
- `REAL_ORDER_ROUTES=NOT_CALLED`
- PPI production secret montado read-only.
- DB observer quick_check=ok.
- DB market history quick_check=ok.
- Observer restart count=0.
- Dashboard restart count=0.
- Health=`{"status":"ok","version":"17.0.0-rc6"}`.

No se habilitaron órdenes reales.

## 4. CP12 — Deploy y activación

Workflow: `RC6 Final Current Candidate Deploy 2026-09-10`
Run: `34651178762`
Job: `103433452669`
Controller commit: `50505e320022cb89ac6692977936cf871e54e987`

Evidencia alcanzada antes del fallo de postflight del workflow integrado:

- PRE_SAFETY=`ok|PRODUCTION_PAPER|0`
- source identity GREEN
- candidate image commit=`f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- offline policy proof GREEN
- focused candidate tests GREEN
- ACTIVATION_STARTED=YES
- HEALTH_OK=YES
- observer/dashboard sobre imagen canónica, restart count 0
- DEPLOYED_IMAGE_COMMIT=`f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- POST_SAFETY=`ok|ok|PRODUCTION_PAPER|0`
- PRE_SCHEMA_OBJECTS=81
- POST_SCHEMA_OBJECTS=81
- DESTRUCTIVE_SCHEMA_CHANGES=0
- 9 timers RC6 activos y enabled
- FUNCTIONAL_HEALTH_SERVICE=GREEN

Conclusión: la activación/deploy quedó GREEN. La falla posterior no revirtió ni invalidó la activación.

## 5. RCA del postflight integrado

El run `34651178762` terminó FAILURE únicamente después de la activación y de los principales post-checks, al calcular la ruta relativa del capture W12.

Error exacto:

`bash: line 264: unexpected EOF while looking for matching '}'`

Causa: expansión Bash defectuosa en la variable `REL` del tramo W12 del workflow integrado.

Acciones forward-only:

1. No rollback.
2. No redeploy repetido.
3. Se utilizó el certificador postdeploy dedicado, read-only/mutations none, con resolución segura de ruta.
4. Luego se persistió el hardening del parser W12 en el workflow de deploy para ejecuciones futuras.

Hardening commit: `3d57a9c426fba9468df1dffa78545d4c8e202ab6`
Mensaje: `fix(rc6): harden W12 path parser [skip ci]`

Ese commit fue intencionalmente `skip ci` para no repetir un deploy ya certificado.

## 6. CP13 + CP14 — Postdeploy, W12 y Green Run

Workflow: `RC6 Final Postdeploy Certify 2026-09-10`
Run: `34651662099`
Job: `103434978730`
Controller commit: `0eb15ee6899cd0abb18456ee714525bd9ec2fa5d`
Resultado: `completed / success`

Prueba final:

- TARGET_SHA=`f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- MUTATIONS=NONE
- AUTO_ROLLBACK=DISABLED
- REAL_ORDER_ROUTES=NOT_CALLED
- HOST_SHA=`f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- HOST_TRACKED_DIRTY=0
- HOST_STAGED_DIRTY=0
- observer=`true|porota-trading-bot:17.0.0-rc6|0|true`
- dashboard=`true|porota-trading-bot:17.0.0-rc6|0`
- deployed image commit exacto=`f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- HEALTH ok / version 17.0.0-rc6
- PPI_SECRET_READONLY=YES
- DB_AND_SAFETY=`ok|ok|PRODUCTION_PAPER|0`
- 9 timers RC6 active/enabled
- FUNCTIONAL_HEALTH_SERVICE_RESULT=success
- W12 capture mode/group correctos
- W12_RELATIVE_CAPTURE resuelto correctamente
- W12_CAPTURE_READABLE=YES
- W12_SERVICE_RESULT=success
- SOAK 60S completado
- FINAL_HEALTH ok / version 17.0.0-rc6
- FINAL_SAFETY=`ok|PRODUCTION_PAPER|0`
- REAL_ORDERS_SENT=0
- RC6_FINAL_POSTDEPLOY=GREEN

UTC final de certificación: `2026-09-11T21:57:43Z`.

## 7. Validaciones paralelas

El commit controlador del deploy también disparó validaciones paralelas W10/RCA. Ambas finalizaron SUCCESS. Se tomaron como evidencia suplementaria solamente; no se reabrieron CP1–CP10.

## 8. Estado de continuidad

`RUNTIME_SHA=f8adec8a02b2f9f0ef2dffbee75458c958bf711e`

`CP1-CP14=GREEN/FROZEN`

`MODE=PRODUCTION_PAPER`

`REAL_ORDER_CAPABILITY=BLOCKED`

`REAL_ORDERS_SENT=0`

`NO_ROLLBACK / NO_REOPEN`

`DEPLOY=COMPLETE`

`POSTFLIGHT=COMPLETE`

`GREEN_RUN=COMPLETE`

A partir de este checkpoint, cualquier trabajo nuevo debe tratarse como un nuevo cambio/onda posterior a RC6 final desplegado, sin reconstruir ni repetir esta cadena de certificación.

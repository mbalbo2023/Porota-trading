# POROTA TRADING — CHECKPOINT CANÓNICO RC6 POSTDEPLOY FINAL

Fecha local de referencia: 2026-09-10 20:50 ART
Repositorio: `mbalbo2023/Porota-trading`
Rama de trabajo: `fix/rc6-w10-sector-map-binding-20260910`
Versión: `17.0.0-rc6`

## 1. VEREDICTO DE DEPLOY

**DEPLOY RC6: GREEN / COMPLETADO Y CERTIFICADO**

Candidato funcional desplegado y fijado:
`e47eeffcb94a9468ac7e7f610beee0869353a77e`

Imagen canónica activa:
`porota-trading-bot:17.0.0-rc6`

Image ID certificado en observer y dashboard:
`sha256:6a10e702b85faeeb8eb11f10f6fd6db79765f6fdca92fd5843204e26025dc7fe`

## 2. EVIDENCIA FINAL POSTDEPLOY

Workflow de certificación final:
- Nombre: `RC6 Final Postdeploy Certify 2026-09-10`
- Run ID: `34543785475`
- Job: `certify`
- Resultado: **SUCCESS**
- Fin de certificación: `2026-09-10T23:50:25Z`
- Marcador final: `RC6_FINAL_POSTDEPLOY=GREEN`

Pruebas/evidencias del postflight:
- `HOST_SHA=e47eeffcb94a9468ac7e7f610beee0869353a77e`
- observer: running, imagen `17.0.0-rc6`, restart count 0, rootfs readonly
- dashboard: running, imagen `17.0.0-rc6`, restart count 0
- `/health`: `status=ok`, `version=17.0.0-rc6`
- `PPI_SECRET_READONLY=YES`
- DB observer quick_check: `ok`
- DB market_history quick_check: `ok`
- modo: `PRODUCTION_PAPER`
- `real_orders_sent=0`
- `REAL_ORDER_ROUTES=NOT_CALLED`
- Functional Health service result: `success`
- W12 Contract Evidence service result: `success`
- W12 latest capture readable por observer, modo 0640, grupo 1000
- soak final 60 s: GREEN

Timers certificados ACTIVE + ENABLED:
- `porota-contract-evidence-rc6.timer`
- `porota-functional-health-rc6.timer`
- `porota-history-postclose-rc6.timer`
- `porota-host-general-backup-rc6.timer`
- `porota-preopen-rc6.timer`
- `porota-candle-integrity-rc6.timer`
- `porota-a3-history-daily-rc6.timer`
- `porota-a3-history-reconcile-rc6.timer`
- `porota-a3-history-weekend-rc6.timer`

## 3. ACLARACIÓN SOBRE EL WORKFLOW DE DEPLOY

El workflow inicial de activación terminó marcado FAILURE por un error de sintaxis Bash en una comprobación posterior a la activación (`unexpected EOF while looking for matching '}'`).

Ese error ocurrió **después** de:
- construir la imagen exacta del candidato;
- validar identidad y políticas offline;
- activar la imagen canónica;
- reiniciar/levantar observer y dashboard;
- verificar `/health`;
- verificar DBs;
- verificar `PRODUCTION_PAPER|0`;
- verificar ausencia de cambios destructivos de esquema;
- verificar timers y Functional Health.

No hubo rollback automático. El postflight independiente posterior certificó el runtime activo y cerró el deploy en GREEN.

## 4. INVARIANTES QUE NO DEBEN REGRESAR

- `VERSION=17.0.0-rc6`
- `MODE=PRODUCTION_PAPER`
- `EXECUTION=SIMULATED`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `real_orders_sent=0`
- economía: `SHADOW`
- expectancy: `OBSERVATION_ONLY`
- market regime: `ALERT_ONLY`
- sector concentration: `BINDING`
- max posiciones por sector: `2`
- W10: GREEN/BINDING
- W12: GREEN/read-only/fail-closed
- IA intradía: OFF

## 5. PENDIENTE POSTERIOR NO BLOQUEANTE — REFERENCIAS HISTÓRICAS

**PENDIENTE REGISTRADO:** limpiar/normalizar referencias históricas heredadas en tests, workflows, nombres de artefactos y documentación que todavía mencionen RC3/HF, RC4/HF, RC5 u otras denominaciones antiguas cuando ya no correspondan al estado canónico RC6.

Objetivo:
- renombrar o aclarar referencias históricas ambiguas;
- mantener trazabilidad donde realmente sea histórica;
- evitar que nombres viejos induzcan a interpretar que son políticas/runtime vigentes;
- no alterar lógica de negocio ni parámetros operativos;
- no volver a abrir pruebas ya cerradas solamente por nomenclatura;
- realizar esta limpieza **después del deploy**, en una ola separada y sin bloquear producción-paper.

Clasificación: **PENDIENTE / NO BLOQUEANTE PARA RC6 ACTUAL**.

## 6. OTRO PENDIENTE OPERATIVO CONOCIDO

El host conserva 58 archivos untracked históricos/diagnósticos. En el deploy se verificó `UNTRACKED_TARGET_COLLISIONS=0`, por lo que no bloquearon ni contaminaron el candidato. No limpiar automáticamente; revisar en una tarea separada y conservar lo que tenga valor forense/auditable.

## 7. ESTADO CANÓNICO AL CIERRE

**RC6 FINAL DEPLOY: GREEN**

El runtime productivo-paper está desplegado, certificado y estable bajo el candidato exacto `e47eeffcb94a9468ac7e7f610beee0869353a77e`, con órdenes reales bloqueadas y cero órdenes reales enviadas.

Próximos trabajos deben partir desde este checkpoint y no reconstruir ni repetir W10/W12/deploy salvo evidencia concreta de regresión.

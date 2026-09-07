# POROTA TRADING — CHECKPOINT RC6 DASHBOARD FINAL — 2026-09-06

## Estado

**GREEN — dashboard RC6 desplegado transaccionalmente.**

Este checkpoint documenta únicamente el dashboard. No cambia estrategia, observer, modo operativo ni capacidad de órdenes.

## Identidad runtime preservada

- Observer branch: `hotfix/rc6-cedear-us-labor-day-20260906`
- Observer SHA: `db26c76723bb988c956589c572b87cbcb4191731`
- Observer image: `porota-trading-bot:17.0.0-rc6`
- Observer restart: `0`
- Observer rootfs: read-only
- Mode: `PRODUCTION_PAPER`
- `real_orders_sent=0`
- Observer DB `quick_check=ok`
- History DB `quick_check=ok`
- Strategy changed: NO
- Real order capability: BLOCKED

## Dashboard RC6 desplegado

- Branch de construcción: `ux/rc6-dashboard-final-20260906`
- Dashboard SHA: `da2c87936d90cda17512de3bc529d13f4693c1c1`
- Imagen activa: `porota-trading-dashboard:17.0.0-rc6-go-live-final`
- Workflow: `RC6 dashboard final deploy 2026-09-06`
- Run: `34071773312`
- Resultado validate: SUCCESS
- Resultado deploy: SUCCESS

Rollback retenido:

- `porota-trading-dashboard:17.0.0-rc6-go-live-ux2`
- commit de rollback: `318043805a9736eda77874ba72ee7abc93fcb0f1`

## Corrección de introspección

Se confirmó en el Droplet que el productor real de introspección corre **cada hora al minuto 15**:

- `porota-introspeccion-hf5.timer`
- `OnCalendar=*-*-* *:15:00`
- enabled + active
- snapshot RC6 generado correctamente en cada ciclo observado.

El dashboard heredaba un umbral de `600 s` (10 minutos), incompatible con una fuente horaria. Se corrigió exclusivamente la semántica de presentación con una ventana máxima de `4500 s` (75 minutos):

- snapshots dentro de la cadencia normal ya no aparecen como vencidos;
- si se supera la ventana de 75 minutos, el aviso de snapshot no vigente permanece;
- no se modificó el productor ni el contenido persistido del snapshot.

Prueba directa antes de activar:

- `INTROSPECTION_AGE_SECONDS=3110.1`
- dentro de ventana esperada
- `RC6_DIRECT_PROOF=GREEN`

## Telegram

Prueba runtime previa a activación:

- estado: `VERDE`
- notification worker: `RUNNING`
- heartbeat vigente
- outbox `SENT=83`
- `FAILED=0`
- `paper_blocking=False`

## SRE

Prueba runtime previa a activación:

- estado persistido: `AMARILLO`
- `quick_check=ok`
- disco libre: `34.7%`
- medición SRE: `29426.6 ms`
- amarillo explicado por latencia de medición, no por corrupción DB ni falta de disco
- `paper_blocking=False`

La latencia SRE queda como hallazgo operativo a revisar, pero no bloquea PAPER mientras DB y capacidad de disco permanezcan sanas.

## Matriz HTTP posterior

Las siguientes superficies respondieron HTTP 200 y superaron validaciones de contenido:

- `/`
- `/vivo`
- `/historicos`
- `/reportes`
- `/config`
- `/sistema?section=introspeccion`
- `/salud`
- `/universo-operativo`
- `/scalping`
- `/instrumentos`
- `/aprendizaje`
- `/dashboard/logs`

Resultado: `POST_HTTP_MATRIX=GREEN`.

En `/sistema?section=introspeccion` se verificó:

- navegación `system-nav` presente;
- ausencia de falso aviso `Snapshot de introspección no vigente` durante una ventana horaria normal;
- presencia de evidencia explícita de snapshot vigente.

## Veredicto

`POROTA_TRADING_RC6_DASHBOARD_DEPLOY=GREEN`

El sistema continúa siendo **POROTA TRADING 17.0.0-RC6**. Los nombres usados durante intentos técnicos anteriores como `v4`/`v5` fueron únicamente iteraciones internas de workflows de despliegue y no versiones del producto. A partir de este checkpoint se evita esa nomenclatura en la comunicación operativa para no confundirla con RC6.

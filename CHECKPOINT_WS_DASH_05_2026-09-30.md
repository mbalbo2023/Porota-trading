# CHECKPOINT — WS-DASH-05 dashboard performance + repo/droplet cleanup

Actualizado: 2026-09-30

## Ownership
- WORKSTREAM_ID: WS-DASH-05
- mode: DEPLOY_OWNER
- branch: `fix/ws-dash-05-dashboard-performance-cleanup-20260930`
- base product SHA: `b7f3210bb9d9b3c0664f1086994e861b2d81ec56`
- scope: dashboard presentation/runtime validation + safe repo cleanup + safe Droplet disk cleanup.
- PAPER/SHADOW ONLY.
- PPI Watch: NO TOCAR.
- FIX-FORWARD ONLY.

## Checkpoint 1 — entrada
- Product branch verificada: `deploy/rc6-pr69-isolated-20260915`.
- Product SHA verificado: `b7f3210bb9d9b3c0664f1086994e861b2d81ec56`.
- Último Deploy V2: run `36773889907` GREEN y VALIDADO_RUNTIME.
- No hay deploy activo al tomar ownership.

## RCA confirmado — /instrumentos
ERROR:
- `/instrumentos` puede congelar el navegador/tablet con el catálogo productivo.

RCA:
- `_instrument_readiness_matrix()` llama `instrument_rows(..., limit=50000)`.
- El servidor renderiza todas las filas y sólo agrega `hidden` a las posteriores a la décima.
- La paginación visual existente no reduce SQL, HTML ni DOM.

FIX objetivo:
- paginación real server-side de 10 filas;
- botón “Mostrar más”/navegación sin precargar el resto;
- límite fijo por request;
- guard de bytes/filas en auditoría runtime.

## Limpieza
- Repo: sólo cerrar PRs demostrablemente integrados/superseded; no borrar ramas por antigüedad/nombre.
- Droplet: medir antes/después; sólo temporales Deploy V2, dangling/unreferenced images y build caches seguros.
- Nunca borrar volúmenes, SQLite, datos persistentes, logs/evidencia requerida, imagen activa ni PPI Watch.

## Estado
- DESARROLLADO: NO
- COMMITTEADO: checkpoint inicial
- EN_GITHUB: SÍ
- DESPLEGADO: NO
- VALIDADO_RUNTIME: NO


## Checkpoint 2 — censo runtime ANTES del fix
Action read-only: `36783391687` — GREEN.

Superficies pesadas comprobadas:
- `/instrumentos`: 5.135.274 bytes; 13.047 registros DOM; 4,917 s.
- `/universo-operativo`: 2.063.919 bytes; 8.476 registros DOM; 18,784 s.
- `/vivo`: 160.428 bytes; 503 registros DOM; 9,950 s.
- Resto de superficies core: HTTP 200.
- Badges literales PENDING/PENDIENTE: 0 en las 15 superficies core auditadas.
- Runtime durante censo: PRODUCTION_PAPER / real_orders_sent=0.
- Disco libre antes de este ciclo: 4.718.469.120 bytes.

RCA común:
- paginación visual ocultaba filas después de la décima, pero servidor seguía serializando el dataset completo.

## Checkpoint 3 — fixes EN_GITHUB
- `/instrumentos`: SQL LIMIT/OFFSET real, 10 filas por request, Anterior/Mostrar más.
- `/universo-operativo`: DOM del catálogo limitado a 10 filas por request; mantiene agregados canónicos; Anterior/Mostrar más.
- `/vivo`: cierres y decisiones pasan a páginas server-side de 10; no precarga 500 decisiones.
- `OBSERVED_BLOCKED`: deja de presentarse como el genérico PENDIENTE y conserva el estado exacto.
- Los estados PENDING_EXPECTED/PENDING_CONFIRMATION de liquidación no se eliminan: son estados financieros semánticos con explicación visible, no placeholders legacy.
- Runtime guard ampliado: presupuestos máximos de bytes y registros para /instrumentos, /universo-operativo y /vivo; falla si reaparece el full-DOM.
- Auditoría visual ampliada: mide filas, cards y contextos pending por ruta.

Estado:
- DESARROLLADO: SÍ.
- COMMITTEADO: SÍ.
- EN_GITHUB: SÍ.
- ARTEFACTO_VALIDADO: NO_VERIFICADO hasta Predeploy final.
- DESPLEGADO: NO.
- VALIDADO_RUNTIME: NO.

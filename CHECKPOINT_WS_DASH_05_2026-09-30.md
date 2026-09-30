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

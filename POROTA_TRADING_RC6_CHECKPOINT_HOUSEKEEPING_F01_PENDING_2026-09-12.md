# POROTA TRADING RC6 — CHECKPOINT HOUSEKEEPING + F01 PENDING — 2026-09-12

## Baseline de referencia

- Runtime observado antes del deploy EOD UI: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`.
- Modo: `PRODUCTION_PAPER`.
- Ejecución: `SIMULATED`.
- Órdenes reales: `0`.
- Política: NO ROLLBACK; forward-fix solamente.

## Pendientes de housekeeping

Estado observado en el host `/opt/porota-trading` durante el preflight EOD UI:

- `TRACKED_DIRTY_COUNT=0`: no hay código versionado modificado fuera de Git.
- `HOST_DIRTY_COUNT=58`: hay 58 artefactos no trackeados, principalmente scripts/resultados históricos de diagnósticos y despliegues (`v17_*.py`, `*_result.json`, `.pid`, `.sh`, probes).
- Espacio libre observado: ~14 GiB.

Pendientes obligatorios:

1. Inventariar y clasificar los 58 artefactos en: evidencia útil / activo / obsoleto.
2. Preservar la evidencia útil fuera del checkout operativo (por ejemplo bajo `data/diagnosticos` o almacenamiento de artifacts) antes de eliminar nada.
3. Retirar sólo residuos confirmados como obsoletos; prohibido `git clean` indiscriminado.
4. Evitar que diagnósticos/deploys escriban nuevos `.json`, `.pid`, logs o scripts temporales en la raíz del repo.
5. Endurecer `.gitignore` para artefactos inequívocos, sin patrones amplios que puedan ocultar código legítimo.
6. Auditar y reducir la proliferación de ramas GitHub ya cerradas/integradas, preservando referencias canónicas, release, checkpoint y ramas realmente activas.
7. Definir política de lifecycle de ramas: creación, convergencia, certificación y retiro luego de integrar.
8. Converger a un workflow estable de deploy por SHA/dispatch, evitando workflows auxiliares temporales por cada intervención.
9. Separar el deploy en pasos observables (preflight/build/activation/health/postflight) para evitar bloques largos sin visibilidad.
10. Mantener siempre guardas: `PRODUCTION_PAPER`, `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED`, `real_orders_sent=0`.

## F01 — localización y causa del deploy pendiente

Candidato autorizado original:

- Rama: `deploy/rc6-f01-paper-20260912`.
- Commit autorizado: `55826f9b0a0ee7998f5498cc44d887f76ffc730f`.
- Workflow: `RC6 F01 PAPER exact-base deployment`.
- Run de activación: `34665171825`.
- La validación offline completa terminó GREEN: exact scope, compileall, build, tests RC6 e invariantes F01.
- La fase `Activate and postflight PAPER F01` falló inmediatamente al comenzar el bloque remoto, antes de modificar el runtime.

Causa operativa identificada:

El workflow exigía simultáneamente:

- host exactamente en `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`, y
- `git status --porcelain` completamente vacío.

Preflights posteriores comprobaron que el host sí estaba en ese SHA y que los archivos trackeados estaban limpios, pero existían 58 archivos **no trackeados**. Por lo tanto el guard de árbol limpio bloqueó el deploy F01 antes de build/activación en host. No fue un fallo de la lógica F01 ni de sus tests.

## Plan F01 pendiente

- NO reejecutar a ciegas el candidato viejo si el deploy EOD UI cambia el SHA productivo.
- Esperar el cierre/postflight del deploy EOD UI en curso.
- Tomar el SHA productivo resultante como nueva base exacta.
- Reaplicar únicamente el delta funcional F01 (motor/modelo de salida/backtests/tests), sin arrastrar cambios ajenos.
- Adaptar el guard del deploy para aceptar artefactos no trackeados preexistentes sólo si:
  - `TRACKED_DIRTY_COUNT=0`;
  - no colisionan con paths del target;
  - la lista de no trackeados se preserva exactamente antes/después.
- Ejecutar CI completa y postflight PAPER.
- No marcar F01 como desplegado hasta verificar SHA del host, health, observer/dashboard sin reinicios, `PRODUCTION_PAPER`, `SIMULATED` y `real_orders_sent=0`.

## Incidente EOD UI durante este checkpoint

- Run EOD UI: `34671405159`.
- Validación offline: `SUCCESS` completa.
- Fase remota: `FAILURE` por transporte SSH, no por código ni por los 58 archivos.
- El guard nuevo aceptó correctamente `TRACKED_DIRTY_COUNT=0`, contabilizó `UNTRACKED_COUNT=58`, confirmó espacio suficiente y verificó `PRE_SAFETY=ok|PRODUCTION_PAPER|0`.
- El fallo ocurrió durante `docker build`, en instalación de dependencias (`pip install`), con `client_loop: send disconnect: Broken pipe` / exit code `255`.
- Según el orden del workflow, todavía no se había ejecutado `git checkout` al target, `docker tag` del candidato ni `porota_mode_manager.py simulation`; por lo tanto no se considera activado EOD UI.
- Forward-fix pendiente: robustecer la sesión SSH/ejecución remota para builds largos y reintentar desde la misma base certificada, previa revalidación read-only del host.

## Estado actualizado

- Housekeeping: `PENDIENTE`.
- Limpieza destructiva: `NO AUTORIZADA` sin clasificación previa.
- F01 lógica/CI offline: `GREEN` en el candidato original.
- F01 producción PAPER: `PENDIENTE DE REBASE/REVALIDACIÓN` después de cerrar EOD UI.
- EOD UI código/CI: `GREEN`.
- EOD UI activación: `NO ACTIVADA`; fallo de transporte SSH durante build remoto.
- Runtime seguro esperado: baseline anterior `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`, sujeto a verificación read-only fresca antes del siguiente intento.

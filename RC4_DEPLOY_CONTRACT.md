# POROTA TRADING RC4 — contrato canónico de testing/deploy

**Estado:** RC4 HF2 candidato. Este documento no autoriza deploy por sí solo. La ejecución de HF2 requiere autorización explícita del operador; esa autorización fue otorgada para esta ejecución. Producción real permanece bloqueada.

## Base reproducible

- Baseline operativo: `17.0.0-rc3-hf6-v2-candidate1`.
- Source congelado del builder baseline: `a03cf47d5db01d8990a99f3f7836879c0606771b`.
- SHA256 ZIP operative-audit baseline: `8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499`.
- RC4 hotfix version: `17.0.0-rc4-hf2`.
- Imagen esperada: `porota-trading-bot:17.0.0-rc4-hf2`.

Antes del deploy final, el source RC4 debe quedar committeado/taggeado en Git y la imagen debe construirse **directamente desde ese commit**, sin patchers externos. El commit/digest finales se incorporarán al manifest antes de autorizar deploy.

## Runtime canónico PRODUCTION_PAPER

`porota_mode_manager.py simulation` es el selector versionado de modo y crea dos contenedores separados:

1. `porota_production_observer`
   - entrypoint `python bv_paper_runtime.py`;
   - root filesystem read-only;
   - sin healthcheck HTTP del dashboard;
   - PPI Production sólo para market-data/read-only;
   - secret PPI montado read-only;
   - `PPI_ORDERS=BLOCKED`;
   - ejecución `SIMULATED`.

2. `porota_production_dashboard`
   - entrypoint `python o_dashboard.py`;
   - sin credenciales PPI;
   - publicación local `127.0.0.1:8000:8000`;
   - lectura de la DB compartida y artefactos sanitizados.

`docker-compose.yml` permanece como stack legacy/SANDBOX/tooling y **no debe ejecutarse en paralelo** con PRODUCTION_PAPER.

## Invariantes de aceptación

- `MODE=PRODUCTION_PAPER`.
- `EXECUTION=SIMULATED`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0` antes y después de testing.
- observer read-only.
- dashboard separado del observer.
- PPI order routing bloqueado.
- IA intradía OFF.
- ninguna familia HOLD se promueve automáticamente.
- Contract Evidence/browser sólo lectura.
- no `docker system prune`.
- no eliminación de rollback hasta checkpoint EOD GREEN y autorización.

## Gates antes del deploy

1. `python rc4_release_preflight.py` GREEN.
2. `python -m compileall -q .` GREEN.
3. tests RC4/settlement/dashboard/risk/history ejecutables GREEN.
4. tests no ejecutables por dependencia del sandbox deben correr en el host/CI con `requirements.txt` completo.
5. `AUDIT_RESPONSE_RC4.md` completo.
6. source RC4 committeado en Git; working tree limpio.
7. imagen construida desde ese commit y digest registrado.
8. preflight read-only del Droplet.
9. autorización explícita del operador.


## Consolidación HF2

- Contract Evidence same-context runner formalizado en Git.
- Materialización autenticada conservadora; campos contractuales faltantes no se infieren.
- Dynamic Contract Evidence: lunes a viernes 10:40–17:00 AR.
- Static/full-browser: fuera de la ventana dinámica, sólo días hábiles; fin de semana sin browser autenticado.
- `/en-vivo`: `paper_decisions` es la fuente primaria; gates son secundarios/event-driven para BUY.
- `/en-vivo`: operaciones cerradas limita el drill-down al día local actual; las abiertas permanecen visibles hasta cierre.
- Liquidaciones que ya pasaron la frontera conservadora T+1 dejan de listarse como pendientes.
- Históricos parciales y aprendizaje event-driven se presentan como estados informativos, no como fallas.
- Ningún cambio habilita órdenes reales ni autoactivación por Contract Evidence.

# RC6 A620 — Atto de la policy inicial de PID1 sin GET PPI

Fecha de evidencia: 2026-11-03 (ART).  
Issue canónico: [#732](https://github.com/mbalbo2023/Porota-trading/issues/732).  
Owner WRITE_OWNER: [#732/6615160049](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6615160049).  
PR control-plane: [#898](https://github.com/mbalbo2023/Porota-trading/pull/898) (DRAFT; no merge).

## Alcance y SHAs

- Base **main** reconciliada: `cd0c823fe1c61b0b18246b40f7b22c9e1d031281`.
- Producto efectivamente fijado por A516: `7e1e744d6ca9ab30c51b5747dd4467af5ece0145`; imagen esperada `sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b`.
- Cambios de código iniciales en branch: `7a491587eb7bee02a13efb7729fac510ca630724`; al publicar este checkpoint cambia el HEAD, que debe reconciliarse en PR.
- Branch: `codex/rc6-a620-observer-pid1-policy-ro`. Únicamente tres paths de owner: `scripts/rc6_a516_host_budget_metadata_ro.py`, `tests/test_rc6_a516_host_budget_metadata_ro.py`, este documento.
- Workflow existente: `.github/workflows/rc6-history-fresh-offhost-evidence.yml`, modo manual `DROPLET_BUDGET_METADATA` en `main`, exclusión `rc6-unified-paper-deploy`. No se modifica ni despacha en este PR.

## ERROR → RCA (observado vs no probado)

1. **A597:** [Action #41699488780](https://github.com/mbalbo2023/Porota-trading/actions/runs/41699488780) produjo `SIDECAR_ABSENT`, con `provider_calls=0` por contrato del diagnóstico auxiliar. No prueba rechazo de PPI, ni que el proceso observador tenga modo OFF; el probe corría como **docker exec auxiliar**.
2. **A519:** la rutina `auxiliary_policy_intent` del A516 anterior ya discrimina archivo/variable en *ese* docker exec, con `runtime_effective_policy=NO_VERIFICADO`. Falta atribución verificable a PID1 del observer.
3. **RCA estático:** producto `7e1e744d`, `porota_mode_manager.observer_runtime_env()` sólo exporta `paper_settings`, Telegram, Contract Evidence, T+1, DB. Las claves `POROTA_DYNAMIC_CAPACITY_MODE` y `POROTA_CAPACITY_POLICY_PATH` no figuran en `PAPER_DEFAULTS`/frozen settings. `ops/policy/rc6-dynamic-capacity-v1.json` define `mode: OFF`. Esto hace probable un arranque OFF con sidecar no inicializado, pero **no constituye evidencia directa del entorno efectivo de PID1 ni del estado de EXIT**.
4. **A620 antecedente CI:** apertura inicial de #898 no incluyó metadatos obligatorios del workflow `checkpoint-attestation.yml`; [Action #41714549122](https://github.com/mbalbo2023/Porota-trading/actions/runs/41714549122) FAIL en dicho gate. Causa: PR body incompleto, no fallo de código A516 ni de provider. Fix: este checkpoint real + campos exactos en body + nuevo CI.

## FIX → GUARD

- Extender **solamente** A516 para leer metadatos fijos de `/proc/1/cmdline` (máx. 2 KiB, comando Python/bv_paper_runtime.py exacto) y sólo en ese caso leer `/proc/1/environ` (máx. 16 KiB, O_NOFOLLOW). Filtrar y proyectar exclusivamente los enums de `POROTA_DYNAMIC_CAPACITY_MODE` y la **presencia** del selector `POROTA_CAPACITY_POLICY_PATH`; no imprimir jamás rutas ni contenidos de credenciales.
- Un override de policy por ruta no se abre, ni siquiera si está presente; queda `UNAVAILABLE`. Sin override, resolver archivo **predeterminado** a través del validador existente (`read_json(limit=16384)` + `validate_policy`). PID1 inesperado, permisos, truncado o duplicados permanecen sin inspeccionar.
- El recibo versión `rc6.a516.ppi.budget.metadata-only.v3` lleva `observer_launch_scope=PID1_INITIAL_ENV_NOT_EFFECTIVE_PROCESS_STATE`; **la lectura de /proc es una foto de arranque**, no el `os.environ` mutable de Python ni la autoridad de presupuesto. `runtime_effective_policy`, shared ledger/EXIT reserves y retención siguen `NO_VERIFICADO`; incluso `APPROVED` inicial da `paper_ready=false`, `book_replay_verified=false`, `provider_calls=0`, `real_orders_sent=0`.
- Sin PPI GET ni login; sin abrir trading SQLite, adquirir lease, crear sidecar, escribir archivos en host, mutar runtime, PPI Watch, workers, scanners o fuentes. A516 conserva su snapshot sidecar READ_ONLY ya existente; este PR no añade ningún SQLite.

## TEST / CI / EVIDENCIA

- Unittest local (fuera de host): **23/23 PASS** con `python3 -B -m unittest discover -s tests -p test_rc6_a516_host_budget_metadata_ro.py -v`: A516 previos + casos A620 de OFF/APPROVED inicial, cmdline adversarial, acceso denegado, env duplicado/truncado, override secreto sin dereferenciar, falsificación de receipt. No pruebas de runtime en esta rama.
- Branch comparada contra base: 2 commits de código, 2 paths iniciales, sin cambios productivos. Al añadir este checkpoint, el diff final debe ser 3 paths.
- [CI general #41714549114](https://github.com/mbalbo2023/Porota-trading/actions/runs/41714549114): SUCCESS sobre HEAD de código original. [CI evidencia offhost #41714549291](https://github.com/mbalbo2023/Porota-trading/actions/runs/41714549291): SUCCESS de contratos offline; **todos los jobs de host/proveedor SKIPPED**. [Checkpoint attestation #41714549122](https://github.com/mbalbo2023/Porota-trading/actions/runs/41714549122): FAIL por metadatos de PR ausentes; debe revalidarse después de este FIX. Estas ejecuciones **no certifican** el nuevo HEAD del checkpoint hasta correr la validación nueva.
- A597 `SIDECAR_ABSENT` y ausencia de ledger/lease/EXIT real: **NO_VERIFICADO para política efectiva**; no afirmar que PPI no cotiza ni que observer OFF sólo por esa clasificación.
- Estado actual al crear este checkpoint: **DESARROLLADO / EN_GITHUB / CI por recomprobar**; no ARTEFACTO_VALIDADO, DESPLEGADO ni VALIDADO_RUNTIME. #778 queda el **único candidato producto** y este cambio control-plane no se integra ahí.

## Criterio de cierre / siguiente paso seguro

1. Reconciliar HEAD final de PR #898 con SHA de este checkpoint y verificar 3 paths scope; cuerpo de PR con campos canónicos y owner #732.
2. Obtener CI de PR completo GREEN; mantener #898 DRAFT y sin merge hasta decisión del owner global.
3. **Después de merge independiente autorizado a main**, el Action manual A516 existente puede generar recibo PID1 de imagen idéntica, con 0 GET PPI y 0 cambios host. Mientras tanto `NO_VERIFICADO`.
4. Contrastar el recibo PID1 con estado runtime de presupuesto/EXIT y política de aprobación real, con guardas existentes. Sin evidencia real de ownership, reserva EXIT y lease, **ningún nuevo Book GET por este código**.
5. Mantener abiertos A613/A614/A617: PPI/Book PIT causal, filtro COLD, 819 contratos económicos, FUND_TERMS/TARIFF, salida FI, COLD2, retención #478 y demás gates. No Predeploy ni Deploy V2.

## Invariantes

`PRODUCTION_PAPER` / `SIMULATION`; `REAL_ORDERS_SENT=0`; `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`; `fail-closed`; PPI Watch intocable; Fix-Forward; ningún rollback ni stress.

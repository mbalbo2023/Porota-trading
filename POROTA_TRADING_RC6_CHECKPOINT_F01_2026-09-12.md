# POROTA TRADING RC6 — CHECKPOINT F-01 Y PENDIENTES
Fecha: 2026-09-12
Rama: fix/rc6-f01-only-pending-rest-20260912
Base verificada: 2bee4ca94a6f0bb71b02572ccb6aa0141e7cfa7d

## Estado ejecutivo
- F-01 (paridad del modelo de salida PAPER/backtest): **CORREGIDO EN ESTA RAMA**.
- F-02 a F-10: **PENDIENTES DE INTEGRACIÓN, REVISIÓN O RESOLUCIÓN**, detallados abajo.
- Runtime productivo: **SIN CAMBIOS / SIN DEPLOY**.
- Modo certificado preservado: PRODUCTION_PAPER / EXECUTION=SIMULATED.
- Órdenes reales: bloqueadas; no se ejecutaron operaciones.

## F-01 corregido
- Se agregó bk_exit_model.py con el modelo canónico versionado PAPER_FIXED_PERCENT_V1.
- PaperBroker aplica la función común en diagnóstico económico y al crear stop/target.
- El default PAPER se alinea con parámetros RC6: stop 2%, objetivo 5%.
- El backtest legado retirado, si se invoca internamente, usa la misma geometría fija; no se presenta como validador/promotor.
- Los candidatos ATR quedan declarados no comparables con PAPER y no promovibles.
- Se persisten modelo y porcentajes efectivos en features de la posición.
- Hay pruebas de fórmula, defaults, reporte de modelo y aislamiento del candidato ATR.
- Regresiones focalizadas y compilación reportadas GREEN en la rama F-01 original. No se ejecutó deploy.

## Pendientes del repositorio
Los siguientes hallazgos quedan expresamente fuera del alcance de esta rama y no deben considerarse cerrados ni desplegados:

- F-02 — identidad completa en latest_quote: pendiente de integración/revisión.
- F-03 — estado fail-closed persistido ante errores SQLite en riesgo diario: pendiente de integración/revisión.
- F-04 — control del POST de estimación IOL mediante opt-in: pendiente de integración/revisión; IOL sigue desactivado por defecto.
- F-05 — lock reproducible de dependencias: pendiente de resolución en Python 3.11/Docker; no generar pins sin validar el entorno objetivo.
- F-06 — claves duplicadas en configuración de entorno/dashboard: pendiente de integración/revisión.
- F-07 — observabilidad de fallbacks relevantes: pendiente.
- F-08 — accesibilidad semántica del dashboard: pendiente.
- F-09 — analítica de causas secundarias bajo precedencia EOD: pendiente de decisión/producto; la precedencia actual se conserva.
- F-10 — reducción/gobierno de workflows: pendiente de revisión.

Nota de trazabilidad: existe otra rama de trabajo con propuestas/cambios para algunos de F-02/F-03/F-04/F-06. No se incorpora a esta rama F-01 y debe revisarse por separado. No se ha eliminado ninguna rama ni commit.

## Validación y seguridad
- F-01: pruebas focalizadas de paridad, take-profit, supervisor, backtest e integración PAPER reportadas GREEN.
- Compilación de los módulos F-01: GREEN.
- Este checkpoint no certifica ni sustituye una nueva prueba del contenedor Docker Python 3.11 ni de producción.
- No hubo cambios en host, credenciales, datos financieros ni políticas de ejecución.
- No hay autorización de trading real; conservar PRODUCTION_PAPER / SIMULATED y REAL_ORDER_CAPABILITY=BLOCKED.

## Próximo paso
Revisión independiente del diff F-01 en esta rama y CI completa sobre el entorno objetivo. Resolver los pendientes F-02 a F-10 de forma separada, actualizar este checkpoint tras cada aceptación y no desplegar sin aprobación y post-deploy certification.


## Seguimiento del despliegue F-01 — 2026-09-12

- Estado al preparar esta candidata: **CI pendiente; deploy NO iniciado; runtime sin cambios**.
- Último runtime RC6 PAPER certificado read-only: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`; PAPER/`real_orders_sent=0`. Postdeploy [34660018614](https://github.com/mbalbo2023/Porota-trading/actions/runs/34660018614) y auditoría de timers [34662807538](https://github.com/mbalbo2023/Porota-trading/actions/runs/34662807538) terminaron GREEN sobre ese SHA.
- El delta de código/pruebas entre ese runtime y la rama F-01 se limita a `be_paper_engine.py`, `bk_exit_model.py`, `by_strategy_backtest.py`, `q_backtest.py`, `tests/test_backtest_data_v17.py` y `tests/test_exit_model_parity_rc6.py`; el otro delta era este checkpoint.
- No se usa `deploy.yml`: corresponde al Compose monolítico v16.2 y no a la ruta certificada del runtime RC6 dividido (observer/dashboard).
- La candidata se basa directamente en el SHA certificado, limita paths, compila y ejecuta la suite RC6 offline antes de SSH. Un segundo commit con marcador explícito `[DEPLOY_F01_RC6]` habilita el job; éste conserva el guard de Environment `production`, safety pre/postflight y no llama rutas de órdenes reales.
- F-02 a F-10 siguen pendientes. No se considera desplegado F-01 hasta confirmar el SHA del host en postflight.

- Hallazgo del primer guard de scope (commit `327c0054246affc4d9fb6e7f1ceeb11818563a8a`): la ejecución [34664820872](https://github.com/mbalbo2023/Porota-trading/actions/runs/34664820872) se detuvo antes del build y SSH porque el árbol heredado alteraba cuatro paths de scalping del runtime actual. No hubo cambios en el host. Se reconstruye el árbol desde el runtime base certificado y se superponen únicamente los seis paths F-01 autorizados.

- Validación exacta del árbol F-01: commit `06cefbf5977517e5456d67e400d2270cd5bdf124`, Actions run [34664977262](https://github.com/mbalbo2023/Porota-trading/actions/runs/34664977262) **SUCCESS** (identidad, scope, compileall, build de imagen, suite activa RC6 offline y invariantes F-01).
- El runtime no se modifica hasta completar la etapa posterior. Se registró la autorización explícita del usuario para desplegar F-01 PAPER; el commit de activación vuelve a pasar el mismo gate y usa el guard del environment `production`.


## Resultado del intento de despliegue F-01 — 2026-09-12 01:43 UTC

- Validación de candidata desde la base RC6 certificada: [34664977262](https://github.com/mbalbo2023/Porota-trading/actions/runs/34664977262) **GREEN**; scope exacto, compilación, build Docker, suite RC6 offline y pruebas/invariantes F-01.
- El intento de activación [34665171825](https://github.com/mbalbo2023/Porota-trading/actions/runs/34665171825) **FALLÓ ANTES DE LA ACTIVACIÓN** en el preflight de árbol limpio del host. No alcanzó la etapa de seguridad/deploy; no se hizo fetch/checkout/build/retag ni activación, y no se intentó rollback. La regla no se omitió.
- Auditoría posterior estrictamente read-only [34665567955](https://github.com/mbalbo2023/Porota-trading/actions/runs/34665567955): host continúa en `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`; árbol dirty; salud `/health=ok`; observer/dashboard activos, misma imagen RC6, reinicios 0; SQLite `quick_check=ok`; `PRODUCTION_PAPER`, `real_orders_sent=0`; los 9 timers RC6 activos y habilitados; disco libre 14,135,320,576 bytes. `HOST_MUTATION=NONE`; rutas de órdenes reales no llamadas.
- **Estado F-01: BLOQUEADO / NO DESPLEGADO.** El host permanece operando sobre el SHA RC6 previamente certificado; no atribuirle el código F-01 nuevo.
- El diagnóstico del guard informó estos nombres de ruta, sin leer sus contenidos ni eliminar/modificar nada:

```text
scripts/ppi_web_authenticated_probe_hf6.py
v17_build_cache_cleanup_1.py
v17_build_cache_cleanup_1_result.json
v17_git_object_dirs_group_fix_r1.py
v17_git_permissions_audit_r1.py
v17_git_remote_diagnostic_r1.py
v17_hf1_resume_deploy.pid
v17_hf1_resume_deploy.sh
v17_legacy_history_source_audit_1.py
v17_legacy_history_source_audit_1_result.json
v17_mode_manager_readonly_audit_1.py
v17_mode_manager_readonly_audit_1_result.json
v17_postcommission_inventory_1.py
v17_postcommission_inventory_1_result.json
v17_rc2_build_preflight_r1.py
v17_rc2_canonical_prestart_backup_1.py
v17_rc2_canonical_prestart_backup_1_result.json
v17_rc2_consolidated_commissioning_1.py
v17_rc2_consolidated_commissioning_1_result.json
v17_rc2_consolidated_commissioning_2.py
v17_rc2_consolidated_commissioning_2_result.json
v17_rc2_consolidated_commissioning_3.py
v17_rc2_consolidated_commissioning_3_result.json
v17_rc2_disposable_paper_gate_1.py
v17_rc2_disposable_paper_gate_1_result.json
v17_rc2_isolated_smoke_r1.py
v17_rc2_live_quote_canary_1.py
v17_rc2_live_quote_canary_1_result.json
v17_rc2_network_build_r2.py
v17_rc2_offline_build_r1.py
v17_rc2_ppi_readonly_canary_1.py
v17_rc2_ppi_readonly_canary_2_result.json
v17_rc2_source_update_r1.py
v17_rc2_source_update_r2.py
v17_rc2_source_update_r3.py
v17_rc3_automated_deploy_1.py
v17_rc3_automated_deploy_1_result.json
v17_rc3_automated_deploy_2.py
v17_rc3_automated_deploy_2_result.json
v17_rc3_automated_deploy_3.py
v17_rc3_automated_deploy_3_result.json
v17_rc3_automated_deploy_4.py
v17_rc3_automated_deploy_4_result.json
v17_rc3_final_readonly_verification_2.py
v17_rc3_final_readonly_verification_2_result.json
v17_rc3_final_readonly_verification_3.py
v17_rc3_final_readonly_verification_3_result.json
v17_rc3_hf2_deploy_result.json
v17_rc3_postacceptance_finalize_1.py
v17_rc3_postacceptance_finalize_1_result_20260829T212931Z.json
v17_rc3_resume_deploy_5.py
v17_rc3_resume_deploy_5_result.json
v17_rc3_root_mode_resume_6.py
v17_rc3_root_mode_resume_6_result.json
v17_trusted_history_catalog_import_1.py
v17_trusted_history_catalog_import_1_result.json
v17_trusted_history_catalog_import_2.py
v17_trusted_history_catalog_import_2_result.json
```

- No se hizo limpieza, reset ni modificación en el servidor. Hay que revisar la propiedad/estado de esas rutas en el host y resolver el árbol dirty por el procedimiento operativo aprobado antes de considerar un nuevo deploy. **No reintentar ni relajar el preflight hasta entonces.**
- F-02 a F-10 continúan pendientes; su estado no cambia.

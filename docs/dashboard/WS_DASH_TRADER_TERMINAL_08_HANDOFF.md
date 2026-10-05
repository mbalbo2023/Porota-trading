# WS-DASH-TRADER-TERMINAL-08 — implementación y handoff

Nota de convergencia 2026-10-05: este documento describe el candidato aislado #470. Los contratos de root, capacidad, labs, funnel, futuros y gates combinados están sustituidos por [RC6_CONVERGENCE_UX470_AUD14_15_16.md](RC6_CONVERGENCE_UX470_AUD14_15_16.md). Las evidencias antiguas conservan su corte histórico.

Issue contractual: [#467](https://github.com/mbalbo2023/Porota-trading/issues/467).
Especificación íntegra: `WS_DASH_TRADER_TERMINAL_08_ARCHITECTURE_2026-10-04.md`, preservada sin cambios.
Branch exclusiva: `work/ws-dash-trader-terminal-08-20261004`.
Base: `deploy/rc6-pr69-isolated-20260915@da697c6e6c2274579f9e4a112fabc4327475dd35`.
Commit de entrada: `88edd36aaab653c249644162ffdab1457c946de4`.

## Entrega

La navegación visible usa ocho destinos, 49 subvistas canónicas y 22 aliases legacy. Cada página usa el shell oscuro compartido, strip operacional con autoridad `observer_state`, ART, sidebar, subnav con URL, controles por voz y detalles progresivos. Ninguna página canónica ejecuta un renderer legacy antes de descartarlo. El único enganche al monolito es la instalación final en `o_dashboard.py`.

El paquete `rc6_trader_dashboard/` separa tokens/interacción, componentes puros, navegación, proyección, adapter de generación y las ocho vistas. El inventario executable y `DASHBOARD_TRUTH_MATRIX_RC6.json` clasifican las 100 rutas registradas / 102 entradas por método. Los endpoints de mutación legacy no se amplían ni se exponen mediante controles nuevos. El único API nuevo es un GET autenticado de logs sanitizados.

Las mesas se limitan a diez filas mediante LIMIT/OFFSET en SQL. Filtros, paginación e identidad se mantienen en la URL; la navegación de ficha conserva los cinco componentes de identidad. Una transacción SQLite `mode=ro` / `query_only` por solicitud conserva un snapshot; lecturas históricas independientes declaran su propia fuente. No hay queries por cada instrumento, catálogos completos materializados, `quick_check`, proveedor de red ni cálculo de permisos financieros.

Readiness viene exclusivamente de `candidate_identity_v2`; las fuentes de catálogo, contrato, historia y estrategia permanecen distintas. Importes agregados usan Decimal y mantienen ARS/USD/USD_MEP/USD_CCL separados. Datos inválidos/ausentes no se convierten en cero. Score mantiene `score_is_probability=false`. HOT/WARM/DISCOVERY no usan verde PASS ni conceden entrada. MFE/MAE requieren trayectoria validada. IV/Greeks/OI/TIR/duration necesitan provenance válido y fresco. Book/event clocks y captura no se intercambian.

## Adapter #466 y límites externos de evidencia

Este candidato no integra ni copia código del PR #466. `generation.py` carga exclusivamente `rc6_shadow_runtime.persistence.read_committed_generation` cuando esté disponible después de reconciliación. Default de evidencia: `<DB_PATH>.shadow`, igual al writer canónico; `POROTA_SHADOW_RUNTIME_ROOT` permite seleccionar un root explícito. El consumidor no crea archivos ni adquiere ownership del writer.

El adapter exige CURRENT/manifest/generation/sequence/watermark/config coherentes y digests de los tres miembros. Mantiene un límite de 4 MiB por payload. Contrato ausente → `NO_VERIFICADO_AWAITING_RECONCILIATION`; cut rechazado/degradado/oversize → `NO_VERIFICADO`, sin fallback por mtime ni latest/checkpoint/status independientes. El embudo selecciona un único cohort moneda/canal de `operational_funnel` del mismo cut; no incorpora el READY actual de otra consulta ni suma canales factuales y SHADOW.

La UI declara NO_VERIFICADO para capacidad OPEN, cobertura causal, edge/OOS, calibration, costos de cuenta, Greeks/OI/NAV, profundidad ejecutable o estado host sin evidencia disponible. Esto representa un dato externo ausente, no un permiso ni una simulación ficticia. La reconciliación con #466 o su sucesor auditado es trabajo del integration owner; el WS no adquiere ese ownership. No se afirma estado desplegado ni validación runtime.

Configuración no secreta: allowlist explícita. Scheduler usa `POROTA_SCHEDULER_STATE_PATH` o `data/scheduler/systemd_timers.json`; historia usa `HIST_DB_PATH` o `data/market_history.db`; logs usan `POROTA_SHARED_LOG_DIR`/`LOG_DIR` con nombres fijos y tail de 64 KiB / diez líneas. JSON adicional se limita a 1 MiB; JSON por fila a 32 KiB. Symlinks/FIFOs no se abren como evidencia válida. Detalles JSON y líneas sensibles se redactan.

## Gates y recuperación de errores

Regresiones nuevas: `tests/test_ws_dash_trader_terminal_08.py`; se preservan íntegramente los doce archivos propiedad de #344. El gate de navegador reproducible está en `tests/ci_trader_terminal_browser.py`: Chromium offline, seis anchos (1440/1280/1024/800/600/360), 294 combinaciones canónicas, 22 aliases, nombres de controles, foco/scroll/details/filtros/deep link, inputs pendientes, pause automática y menú/Escape. Capturas y reporte JSON se publican como evidencia del PR.

Evidencia versionada: [`WS_DASH_TRADER_TERMINAL_08_ACCEPTANCE.json`](WS_DASH_TRADER_TERMINAL_08_ACCEPTANCE.json) traza las 22 secciones íntegramente preservadas de la especificación a implementación y regresiones. [`evidence/ws08-local-gates.json`](evidence/ws08-local-gates.json) registra la suite local (2.595 tests), focal (171), siete fixtures negativos y completitud del bundle. [`evidence/ws08-browser-gate.json`](evidence/ws08-browser-gate.json) conserva los renders y nueve guards de interacción; capturas [1440 px](evidence/ws08-instrumentos-1440.png) y [360 px](evidence/ws08-instrumentos-360.png) usan exclusivamente fixtures sintéticos.

La prueba grande usa 12.000 instrumentos y 60.000 observaciones sintéticas; primera página y HTML se mantienen acotados, la DB conserva su hash, se rechaza DELETE y la consulta agregada no materializa el catálogo. Gates locales adicionales: compile, diff check, negative fixtures, completitud de bundle por descubrimiento y suite automática desde repository-root.

Errores iniciales conservados en la evidencia del PR:

| Error | RCA | Fix / guard |
|---|---|---|
| Banner legacy omitido | Shell nuevo no preservaba IDs/textos de compatibilidad | Un strip único conserva IDs y semántica sólo si el dato observado lo demuestra; regresión legacy preservada y nueva comprobación cross-route |
| Ruta nueva sin clasificar en primera corrida | Inventario todavía anterior al nuevo dispatcher al importar el proceso | Clasificación explícita de 49 subvistas/22 aliases; inventario automático sin rutas sin clasificar |
| `TARGET_MODE_INVALID` en fixture legacy | Umask del executor 0077 versus modo 0750 esperado por fixture | Ejecución de tests con umask 0022; no se modifica producto ni tests ajenos |
| Fixture de logs sin su root explícito | La suite global también crea snapshots en el default compartido | Fixture nueva fija `POROTA_SHARED_LOG_DIR` y comprueba redacción/descarga contra ese root |
| Métrica derivada dentro de contrato con digest válido | Integridad del contrato no valida el provenance ni el reloj de una métrica | Guard compartido catálogo/contrato; cinco regresiones cubren provenance ausente, stale, futuro, sin fuente y válido |
| Harness de fetch diferido bloqueado | Playwright interpretaba el valor función de la asignación como callable y esperaba el Promise sin liberar | Expresiones de instalación/liberación devuelven `void 0`; el guard de navegador verifica texto y foco preservados durante el fetch pendiente |

No se agregan exclusions, skips ni xfails. Se mantiene únicamente la exclusión gobernada existente `test_a3_primary_readonly_hf6.py`, con successor `tests/test_a3_primary_readonly_hf6.py`. El PR permanece DRAFT. Predeploy V2 construye y congela su artefacto exacto como evidencia de CI, sin promoción ni deploy; ese artefacto aislado no reemplaza el futuro candidato reconciliado del integration owner. SHA/tree/run/artifact y conteos finales se registran en el PR y el cierre de ownership del issue, después del gate exacto GREEN.

## Ownership y seguridad

Scope nuevo: `rc6_trader_dashboard/**`, dos archivos nuevos de pruebas/gate, este handoff y matriz de aceptación. Cambios mínimos sobre `o_dashboard.py`, `scripts/rc6_dashboard_route_inventory.py` y `DASHBOARD_TRUTH_MATRIX_RC6.json`. Revalidación programable: intersección cero con paths de #466 y #344; `rc6_annual_instrument_analysis.py` (#423), `bh_universe_dashboard_hf6.py` (#422) y #107 intactos. Ninguna escritura en sus branches.

WORKSTREAM_ID=`WS-DASH-TRADER-TERMINAL-08`. DEPLOY_OWNER=`NOT_ACQUIRED`. WRITE_OWNER se libera en #467 una vez que la programación y CI exacta estén GREEN. Estado final autorizado: `READY_FOR_INTEGRATION_AND_DEPLOY_CANDIDATE`, registrado con evidencia exacta en ese cierre.

PAPER/SHADOW ONLY; real routes NOT_CALLED; provider requests=0 al render; `real_orders_sent=0` sigue como invariante de política y dato observado read-only. No merge, deploy, runtime/DB productiva, systemd, Docker operativo ni PPI Watch. FIX-FORWARD ONLY. La integración posterior debe producir un único candidato reconciliado y un único Deploy V2 bajo autorización del usuario.

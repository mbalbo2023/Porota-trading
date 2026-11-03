# RC4 TEST REPORT — 17.0.0-rc4-test-ready1

## Estado

**Resultado local:** TEST-READY PARA STAGE EN HOST.  
**NO autorizado para deploy todavía.**

## Identidad

- Version: `17.0.0-rc4-test-ready1`
- Mode: `PRODUCTION_PAPER`
- Execution: `SIMULATED`
- Real order capability: `BLOCKED`
- Frozen candidate1 source: `a03cf47d5db01d8990a99f3f7836879c0606771b`
- Operative baseline ZIP SHA256: `8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499`

## Static preflight

`RC4_PREFLIGHT=GREEN` — **15/15 checks**.

Incluye split observer/dashboard, observer read-only, PPI secret `:ro`, orders blocked, compose legacy rotulado, ausencia de `:memory:.ses` y garantía de que Contract Evidence RC4 usa trusted-device collector y no el scraper legacy de credenciales.

## Compilación

`python -m compileall -q .` → **GREEN**.

## Batería crítica

GREEN para:

- RC4 acceptance / Shadow→Binding;
- Dashboard `/vivo` y sesión;
- Dashboard responsive/UX/logs;
- Scheduler dashboard;
- Contract Evidence;
- History fallback (excepto el test que importa APScheduler real);
- A3 CEM history / A3 Primary read-only;
- host general backup;
- caucion ledger;
- partial receipts / settlement;
- legacy observed-shape actualizado a `PENDING_CONFIRMATION`;
- Dynamic Concurrent Risk + caucion sweep.

## Matriz diferencial candidate1 ↔ RC4

Clasificación actual:

- **BASELINE_EXISTING_OR_SHARED_FAIL: 33**
- **ENVIRONMENT_MISSING_DEP: 9**
- **GREEN_BOTH: 18**
- **RC4_IMPROVEMENT_OR_UPDATED_TEST: 8**

Después de modernizar la expectativa RC4 de settlement y `/vivo`, no queda ningún `RC4_REGRESSION_REVIEW` pendiente.

`BASELINE_EXISTING_OR_SHARED_FAIL` significa que el archivo de test no fue un GREEN diferencial contra candidate1 en este sandbox; no se convierte artificialmente en GREEN. El detalle se conserva en `RC4_TEST_FILE_MATRIX.tsv`.

## Dependencias ausentes en este sandbox

Estas dependencias están declaradas por el proyecto pero no pudieron instalarse aquí porque el entorno de análisis no tiene acceso a PyPI. Deben ejecutarse en el Droplet/CI antes de cualquier deploy:

- **apscheduler**: `test_historical_freshness_v1633.py`, `test_maintenance_scheduler.py`, `one test in test_data912_history_fallback.py`
- **ppi-client**: `test_order_trace_units_v17.py`, `test_ppi_documented_contract_v17.py`, `test_ppi_observed_configuration_v17.py`, `test_ppi_retry_policy_v1633.py`, `test_ppi_sandbox_adapter_v1632.py`
- **hypothesis**: `test_propiedades_costos.py`
- **yfinance**: `test_backtest_data_v17.py`


No se eliminaron ni skippearon del repositorio para obtener un verde cosmético.

## Inventario

- Python modules: `REVIEW_REQUIRED=0`.
- Scripts: `SCRIPT_REVIEW_REQUIRED=0`.
- Prefix collisions: documentadas; no se hace renombre masivo en RC4.
- Legacy credential scraper: manual/legacy; no scheduler RC4.

## Seguridad de testing

- candidate1 no fue modificado durante esta preparación;
- no hubo deploy/restart/prune;
- no se enviaron órdenes;
- el package no debe incluir `.env`, DB/WAL/SHM, logs, cookies, browser profile, tokens u OTP;
- browser contractual RC4 es trusted-device + GET/HEAD/OPTIONS solamente.

## Gates obligatorios en host

1. verificar SHA256 del ZIP;
2. extraer en staging aislado;
3. confirmar runtime candidate1 intacto antes de probar;
4. build de imagen RC4 sin activarla;
5. ejecutar preflight y compileall dentro del entorno completo;
6. ejecutar tests que aquí quedaron `ENVIRONMENT_MISSING_DEP`;
7. ejecutar batería crítica completa;
8. ejecutar replay read-only contra copia de la DB/evidencia runtime;
9. confirmar `real_orders_sent=0` antes y después;
10. revisar disco y no ejecutar prune;
11. sólo después versionar source final en Git, construir desde commit y registrar digest;
12. recién entonces evaluar GO/NO-GO de deploy.

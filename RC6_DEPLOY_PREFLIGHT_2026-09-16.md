# RC6 deploy preflight — 2026-09-16

Preflight local previo al deploy canónico:

- Los 20 módulos definidos en `.github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml`
  fueron analizados con `python -m py_compile`.
- Resultado: sintaxis válida.
- No se modificó el YAML, la política PAPER ni el código de negocio.
- La validación posterior continúa siendo el workflow transaccional canónico.

Invariantes: `PRODUCTION_PAPER`, `SIMULATED`,
`REAL_ORDER_CAPABILITY=BLOCKED`, `real_orders_sent=0`.

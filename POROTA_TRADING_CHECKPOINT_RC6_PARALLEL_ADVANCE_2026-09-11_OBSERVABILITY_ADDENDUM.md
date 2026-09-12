# POROTA TRADING RC6 — CHECKPOINT ADDENDUM OBSERVABILIDAD 2026-09-11

Este addendum complementa `POROTA_TRADING_CHECKPOINT_RC6_PARALLEL_ADVANCE_2026-09-11.md`. No reemplaza ni modifica la verdad de runtime registrada allí.

## Runtime

- Runtime desplegado vigente: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`.
- Último postdeploy certificado de ese runtime: run `34660018614`, job `103460392927`, SUCCESS.
- Ningún cambio documentado en este addendum está desplegado.
- La evidencia `REAL_ORDERS_SENT=0` corresponde a ese último postdeploy certificado; este addendum no afirma una nueva introspección host.

## Scalping revision telemetry — observabilidad read-only

### Rama

- `feature/rc6-scalping-telemetry-observability-20260911`
- HEAD certificado: `d1d4c6c8c990ac5d47d4068eaed3d908ccd2e721`
- Base: telemetry certificada `3793dbb9cab1eb2b25bcd8e96a4e3a5df336970b`.

### Archivos nuevos

- `fk_scalping_revision_metrics_rc6.py`
- `tests/test_rc6_scalping_revision_metrics.py`
- `.github/workflows/rc6-scalping-telemetry-observability-20260911.yml`

### Función

Consume únicamente eventos ya persistidos `SCALPING_INTRADAY_REVISION_TELEMETRY` y resume, sin reinterpretar el contrato de trading:

- cantidad válida e inválida de evidencias;
- `REFRESH_MUTABLE` vs `REJECT_CLOSED_REVISION`;
- umbral vigente informado, actualmente 120 s;
- edad de revisión p50/p95/max;
- última evidencia;
- distribución por acción;
- distribución por símbolo.

La capa identifica explícitamente su salida como `OBSERVABILITY_ONLY`.

### Seguridad

- lectura `SELECT` sobre `paper_events`;
- no `INSERT`, `UPDATE` ni `DELETE`;
- no `/Operar`;
- no `send_order`/`place_order`;
- no cambia `DEFAULT_MUTABLE_SECONDS`;
- no cambia score, spread, candidatos ni fills;
- no es motor de decisión;
- no está wired todavía al dashboard runtime.

### Pruebas

Las pruebas cubren:

1. distribución sintética de edades 30/90/180/660 s con p50=90 y p95/max=660;
2. evidencia malformed contabilizada como inválida, nunca convertida a edad cero;
3. lector SQLite únicamente SELECT y filtrado por event type, verificando que `total_changes` no aumente;
4. regresión del productor de telemetría y política temporal.

### Certificación CI

- run: `34662775025`
- job: `103468540971`
- resultado: `SUCCESS`
- HEAD del run: `d1d4c6c8c990ac5d47d4068eaed3d908ccd2e721`

Pasos GREEN:

- exact delta from certified telemetry baseline;
- compile;
- metrics tests;
- telemetry producer regression;
- prove metrics layer is read-only and non-binding.

### Clasificación

- PRESENT IN SOURCE: YES, rama funcional.
- TESTED: YES.
- CERTIFIED CI: YES.
- READ-ONLY: YES por diseño/pruebas.
- DEPLOYED: NO.
- ACTIVE RUNTIME: NO.
- DASHBOARD WIRED: NO.

`STATUS = GREEN EN CI / NO DEPLOY`

## Estado consolidado después de este addendum

- Runtime `eddcc29...`: GREEN según último postdeploy, sin cambios.
- Scalping revision telemetry producer: GREEN CI, NO DEPLOY.
- Scalping telemetry metrics: GREEN CI, NO DEPLOY.
- SWING_PAPER shadow policy: GREEN CI, NO DEPLOY / NO BINDING.
- Caucion cash-sweep shadow policy: GREEN CI, NO DEPLOY / NO BINDING.
- Dashboard wiring: YELLOW, pendiente.
- Evidencia real de latencia de revisiones PPI: YELLOW, requiere rueda abierta con telemetry desplegada.
- Replay contrafactual EOD/overnight: YELLOW, en preparación read-only; no modificar EOD productivo hasta tener cobertura suficiente.
- Schedule/provenance live de caución: YELLOW; no binding.

## Próximos pasos sin reabrir trabajo ya certificado

1. medir cobertura read-only de datos posteriores a cierres `EOD_PAPER` para determinar si el replay overnight es técnicamente válido;
2. construir replay contrafactual con costos no intradía completos solo donde exista evidencia suficiente;
3. preparar wiring de dashboard exclusivamente consumidor de métricas/evidencia;
4. mantener separado cualquier deploy futuro de telemetry del deploy de SWING/caución, para atribución mínima de incidentes;
5. no alterar el umbral temporal de 120 s hasta observar distribución real durante rueda abierta.

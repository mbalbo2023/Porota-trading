# CHECKPOINT — RC6 CONSOLIDATED ALL-PENDING — 2026-10-01

## Entrada canónica

- Product branch: `deploy/rc6-pr69-isolated-20260915`
- Base SHA para todos los workstreams: `7b45c56ca7bc1fd9f5bc4f716be370728c7c1298`
- Runtime observado tras Deploy V2 fallido 36889965850: nueva imagen `sha256:c4a32f8da794600d47626975a93ff8abf62dcb794412a4ca238fbb5051af6f01`
- `CURRENT_STATE_V2` permanece viejo: NO_VERIFICADO hasta release final.
- PAPER/SHADOW ONLY.
- `real_orders_sent=0`.
- Rutas reales `NOT_CALLED`.
- PPI Watch: NO TOCAR.
- FIX-FORWARD ONLY.
- No existe deploy automático programado para las 17:05; fue deshabilitado por instrucción del operador.

## Ownership / paralelización

### WS-CONSOLIDATED-IOL
- Branch: `work/ws-consolidated-iol-20261001`
- PR draft: #413
- Scope exclusivo: OAuth MCP IOL del Droplet, refresh, guardian, source-path IOL, alertas IOL.
- Estado: DESARROLLADO/EN_GITHUB parcial; no deploy.

### WS-CONSOLIDATED-CONTROLPLANE
- Branch: `work/ws-consolidated-controlplane-20261001`
- PR draft: #414
- Scope exclusivo: anti-hang, probes bounded, coordinación/checkpoints.
- Estado: DESARROLLADO/EN_GITHUB parcial; no deploy.

### WS-CONSOLIDATED-DASHBOARD
- Branch: `work/ws-consolidated-dashboard-20261001`
- Scope: performance /analisis, /vivo, /universo-operativo, issue #411.
- Estado de entrada: branch idéntica a base; RCA en progreso.

### WS-CONSOLIDATED-SRE
- Branch: `work/ws-consolidated-sre-20261001`
- Scope: caucion DB lock, candle-integrity, full-db-integrity, introspection, GDELT/systemd.
- Estado de entrada: branch idéntica a base; RCA en progreso.

## IOL — evidencia actual

- Reautorización humana del OAuth del Droplet completada.
- Verify runtime: TOKEN_PRESENT=YES; initialize=200; tools/list=200; MCP_AUTH_VERIFIED=YES.
- Token access observado: expires_in=900 s.
- Refresh token presente.
- Live proactive-refresh smoke: ACCESS_TOKEN_ROTATED=YES; REFRESH_TOKEN_ROTATED=YES; TOKEN_ENDPOINT_PERSISTED=YES; TOKEN_OBTAINED_AT_PERSISTED=YES; store=0600; tools=33; real_orders_sent=0.
- Collector real recuperó `LIVE_FRESH`: cauciones ARS=3, USD=3, FCI=22 y opciones live.
- AL30 direct source-path: asset_info, analytics, simulate_by_nominals y quote T1 = OK.
- Política objetivo revisada:
  - durante rueda, el collector usa IOL como fuente complementaria real detrás de PPI;
  - guardian no debe competir innecesariamente con el collector;
  - fuera de rueda, guardian OAuth mantiene/verifica continuidad;
  - ante REAUTH_REQUIRED, alerta Telegram P0 idempotente;
  - nunca IOL=0 por falla de source-path.

## Pendientes P0/P1 consolidados

1. Deploy/provenance parcial: runtime nuevo + CURRENT_STATE_V2 viejo.
2. Dashboard performance: /analisis timeout 20 s; /vivo ~33 s; /universo-operativo ~40 s.
3. IOL OAuth continuidad + alerta Telegram + source-path residual.
4. Caucion cash-sweep: database is locked; sin oferta dinámica ejecutable persistida.
5. Scalping per-identity read failures; no degradar globalmente por endpoint unsupported.
6. Históricos: 899/1094 completos; 193 NO_NEW_VALID_ROWS + 2 PPI_QUERY_FAILED.
7. candle-integrity timeout.
8. full-db-integrity timeout.
9. introspection failed/timeout.
10. GDELT stale + timer absent + schema reader `no such column: title`.
11. Preopen: revalidar limpio después de RestartCount=0.
12. Legacy systemd failed states: distinguir activos vs supersedidos.
13. Issue #411 dashboard intraday semantics.
14. Issue #72 historical/candle SHADOW validation remains broader backlog.
15. PR/issue hygiene: no mergear ramas viejas a ciegas; cerrar sólo con evidencia funcional.

## Release policy

- No deploy durante rueda.
- No deploy automático.
- Integrar todos los workstreams GREEN en un único candidato.
- Releer HEAD productiva antes de integración.
- Exact Predeploy V2 + artifact/manifest/digest.
- Un único Deploy V2 después del cierre sólo cuando todos los gates requeridos estén GREEN.
- Postdeploy inmediato + soak + auditoría demorada.
- Actualizar estado/error-registry/lessons recién después de VALIDADO_RUNTIME.

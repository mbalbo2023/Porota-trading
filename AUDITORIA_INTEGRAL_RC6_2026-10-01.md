# AUDITORÍA INTEGRAL RC6 — 2026-10-01

## Estado

- WORKSTREAM: `WS-AUDIT-FULL-SYSTEM-20261001`
- Modo: `READ_ONLY` sobre runtime; escritura sólo de evidencia/documentación en branch aislada.
- Base auditada de código: candidato `3b29cb55b68482c8922477f90d05fa392fb40a24` (#424).
- Product base observada: `7815f1b405492440e305313165796b66632cb53b`.
- Runtime observado: imagen `sha256:c4a32f8da794600d47626975a93ff8abf62dcb794412a4ca238fbb5051af6f01`, label `porota.commit=851972a92762fcf7636197b67f3a4631c4a61340`.
- Safety runtime: `PRODUCTION_PAPER`, `real_orders_sent=0`, rutas reales `NOT_CALLED`.
- PPI Watch: no mutado.
- No deploy realizado por esta auditoría.

## Evidencia

- Full system audit: run `36915681293` — GREEN.
  - runtime artifact `11190260473`
  - static artifact `11188289479`
- Detailed audit: run `36917277311` — GREEN.
  - artifact `11189789815`
  - digest `sha256:0ccbeba2889c13bdbad2e0071a78117ec4f2db2aaf7118584684cf00b0eedc29`
- Targeted RCA audit: run `36917901654` — GREEN.
- La primera versión del detailed audit (run `36916747434`) falló por una suposición de columna del propio auditor; se corrigió schema-aware y quedó GREEN en `36917277311`.

## Dictamen ejecutivo

RC6 conserva las invariantes de seguridad PAPER, pero **no está listo para un deploy final de cierre**. Hay defectos P0/P1 que deben corregirse y reconciliarse en un único candidato antes de un único Deploy V2.

La principal conclusión de arquitectura es que varios problemas aparentemente separados —heartbeat intermitente, health RED, timeouts, dashboard lento y servicios de integridad— compiten por un Droplet de 1 vCPU / ~1 GB y por una SQLite de ~1.2 GB. Parte de la carga es innecesaria y puede eliminarse en código antes de evaluar un resize.

## Findings

| ID | Prioridad | Severidad | Área | Hallazgo | Impacto | Estado |
|---|---|---|---|---|---|---|
| AUD-P0-001 | P0 | CRITICAL | SRE/observer | `bi_operational_services.collect_sre()` ejecuta `PRAGMA quick_check` cada ~5 min dentro del loop del observer. | Bloqueos de 149 s promedio / 270 s máx, heartbeat stale, presión CPU/DB. | CONFIRMADO |
| AUD-P0-002 | P0 | CRITICAL | Dashboard | El middleware canónico ejecuta primero `call_next()`: corre handler legacy costoso y luego descarta su HTML. | Doble trabajo, timeouts, rutas de 10–45+ s. | CONFIRMADO |
| AUD-P0-003 | P0 | CRITICAL | Dashboard/API | `snapshot()` / `/api/observer/state` materializan datasets amplios; response observado ~3.6 MB y ~8 s. | Cada página puede reconstruir verdad pesada; /vivo, /historicos, /sistema degradados. | CONFIRMADO |
| AUD-P0-004 | P0 | CRITICAL | Deploy/infra | Disco libre ~5.45 GB < ~7.42 GB exigidos por pre-transfer guard. | Deploy final bloqueado correctamente. | BLOQUEADO |
| AUD-P0-005 | P0 | HIGH | Provenance | Runtime actual es commit `851972...`, pero `CURRENT_STATE_V2.json` y frozen candidate aún describen `055a257...`. | Estado auditado puede mentir después de promoción seguida de fallo. | CONFIRMADO |
| AUD-P1-006 | P1 | HIGH | systemd | Full DB timer sigue `Persistent=true`; intención y nombre de test dicen non-persistent. | Catch-up de quick_check pesado fuera de ventana postclose. | CONFIRMADO |
| AUD-P1-007 | P1 | HIGH | Ingesta BYMA | `/data/market` root:root 755; observer UID 1000 no puede crear `.tmp`. | `BYMA_STRUCTURED_CAPTURE=ROJO PermissionError`. | CONFIRMADO |
| AUD-P1-008 | P1 | HIGH | Reporting financiero | `rc6_snapshot.py` etiqueta todo `net_pnl` cerrado como `net_pnl_ars`, ignorando currency. | Puede sumar USD_MEP como ARS en snapshot/postclose review. | CONFIRMADO |
| AUD-P1-009 | P1 | HIGH | Históricos | Cobertura exacta READY ≈1,989/7,728; ≈5,739 sin histórico canónico exacto. | M3/backtest/análisis incompletos; no implica corrupción de ejecución live. | CONFIRMADO |
| AUD-P1-010 | P1 | MEDIUM-HIGH | Históricos/reporting | Métrica runtime llegó a “cobertura 1151/231”; numerador y denominador usan scopes distintos. | Indicador imposible >100%, engaña auditoría. | CONFIRMADO |
| AUD-P1-011 | P1 | HIGH | Observer/market data | Selecciona 20 aunque métrica recomienda 9–15; rotación ~7,600 instrumentos con ~845 turnos y `rotation_feasible=false`. | Densidad intraday global inalcanzable; ciclos 49–94 s y fallas parciales. | CONFIRMADO |
| AUD-P1-012 | P1 | HIGH | Scalping | Worker RUNNING pero 0 posiciones SCALPING_PAPER; contratos mayormente PENDING/REJECTED. | ACTIVE_PAPER no tiene validación conductual suficiente todavía. | CONFIRMADO |
| AUD-P1-013 | P1 | HIGH | Caución | Cash sweep HOLD: `EXACT_FEE_OR_VERSIONED_PAPER_TARIFF_MISSING`; 0 ofertas/allocations. | Funcionalidad no operativa; fail-closed correcto. | CONFIRMADO |
| AUD-P1-014 | P1 | HIGH | Timers/capacidad | Snapshot 17:15, introspection :15, publish/full-db ~:20 y runtime evidence :25 concentran carga postclose. | Contención evitable en 1 vCPU. | CONFIRMADO |
| AUD-P1-015 | P1 | HIGH | Runtime services | candle-integrity/introspection/full-db muestran timeouts históricos; candidate #418/#419 corrige parte, no runtime. | M0/M4 no cerrables antes del deploy final + soak. | CONFIRMADO |
| AUD-P1-016 | P1 | MEDIUM-HIGH | GDELT | Runtime sigue con 10 eventos, último 2026-09-20, último run parcial. | Riesgo-evento SHADOW stale. Candidate #421 aún no desplegado. | CONFIRMADO |
| AUD-P1-017 | P1 | MEDIUM-HIGH | IOL | Guardian OAuth 24x7 está en candidato, no runtime; preopen observó SOURCE_UNAVAILABLE scoped y sin LKG caución ARS. | Complemento degradable; PPI/BYMA fallback correcto. | CONFIRMADO |
| AUD-P1-018 | P1 | HIGH | Validación | M0–M4 YELLOW; M5–M11 GRAY. | No existe base para declarar campaña totalmente validada. | CONFIRMADO |
| AUD-P2-019 | P2 | MEDIUM | Governance | `main` observado sin protección; product protection NO_VERIFICADO; workflows legacy usan mutex distinto. | Riesgo operativo/gobierno, no bug de trading inmediato. | PARCIAL |
| AUD-P2-020 | P2 | MEDIUM | Repo hygiene | 27 PRs abiertos y 166 workflows en candidato; legacy/retired abundante. | Complejidad y auditabilidad; no borrar a ciegas. | CONFIRMADO |
| AUD-P2-021 | P2 | MEDIUM | Backups | HISTORY_BACKUP GREEN; HOST_GENERAL_BACKUP conserva error histórico de symlink y última salud actual no quedó demostrada. | M0 requiere verificación postclose. | NO_VERIFICADO |

## Infraestructura

- Host: 1 vCPU, ~1 GB RAM.
- Load observado ~2.76/3.18/3.25.
- Swap en uso ~758 MB.
- Disco ~24.88 GB; libre durante auditoría ~5.45 GB.
- Inodos sanos.
- Observer/dashboard: running, OOM=false, restarts=0.
- El dashboard se expone al host por loopback; no se observó binding público del 8000 en el censo.
- Antes de cualquier resize deben cerrarse P0 de carga inútil y re-medirse capacidad.

## Timers y control plane

La política host-control-plane gobierna los timers activos principales. El timer de histórico postclose está intencionalmente quarantined/masked.

Defectos:
1. full-db integrity todavía `Persistent=true`;
2. clúster postclose demasiado concentrado para 1 vCPU;
3. servicios viejos conservan estados failed hasta que el candidato nuevo se despliegue;
4. main no tiene scheduler de housekeeping activo; #410 permanece activation-gated.

## Dashboard

Latencias observadas bajo carga de mercado/auditoría:
- `/historicos` >45 s timeout
- `/informacion-financiera` >25 s timeout
- `/sistema` >25 s timeout
- `/trading` ~31.9 s
- `/motor-trading` ~21.7 s
- `/` ~20.0 s
- `/vivo` ~20.3 s
- `/en-vivo` ~10.4 s
- `/universo-operativo` ~14.0 s
- `/analisis` ~13.3 s
- `/api/observer/state` ~8.3 s / ~3.6 MB.

#422 y #423 son mejoras EN_GITHUB/ARTEFACTO_VALIDADO, no runtime. El cierre debe sumar un fast-path canónico y un snapshot live acotado.

## Ingesta, históricos y candles

- Históricos canónicos: ~439,833 filas, última fecha diaria 2026-09-28.
- Cutoff repair: 1,094 targets; 899 completos; 195 fallidos.
- Cobertura histórica exacta de READY muy incompleta, especialmente BONOS/ON.
- Candle worker RUNNING, `candle_dirty=0` en el detailed audit.
- Rechazos recientes: 1,700 `NO_TRADE_SAMPLE`; 4 `SOURCE_OR_RECEIPT_IN_FUTURE`.
- La guardia de timestamps futuros está funcionando; el volumen de NO_TRADE_SAMPLE requiere lectura de calidad/densidad, no promoción automática a bug.

## Motor financiero y exits

No se encontró una violación de PAPER o una ruta real habilitada.

Estado observado:
- 90 posiciones PAPER acumuladas.
- 1 BBAR abierta alrededor de 16:50 ART durante auditoría.
- 1 exit EOD parcial con remanente 2 esperando profundidad ejecutable.
- Exit reader READY; supervisor RUNNING.
- Riesgo diario ARS READY: baseline 946,852.4565; daily PnL -1,897.9128; presupuesto 2.5%.
- Gates del día: 4 `OPENED_SIMULATED`; 11 `BLOCKED`.
- Close reasons acumulados: 54 EOD, 18 STOP, 9 MAX_HOLD, 8 DAILY_LOSS; no TAKE_PROFIT observado.

El resultado económico agregado es negativo, pero eso es un asunto de calidad/validación de estrategia PAPER. No autoriza cambios automáticos de parámetros sin análisis postclose por cohortes.

## Scalping

- Worker: RUNNING / ACTIVE_PAPER / PAPER only.
- Universo 1,186; batch 24; successful 24; failed 0 en muestra.
- Estados intraday: 421 CONFIRMED_INTERVAL_VOLUME; 1,648 PENDING_LIVE_CONFIRMATION; 50 REJECTED_MUTABLE_CLOSED_POINTS.
- Rechazos/HOLD dominantes: pending confirmation, mutable closed points, insufficient points, stale/no book.
- No posiciones `SCALPING_PAPER` observadas.

Conclusión: wiring vivo, seguridad y guards existen; todavía falta evidencia conductual de entradas/salidas simuladas.

## Caución

- Cash sweep: HOLD.
- Código: `EXACT_FEE_OR_VERSIONED_PAPER_TARIFF_MISSING`.
- Sin allocations ni cauciones abiertas.
- No inventar broker terms. El fix debe conservar separación `BROKER TERM != PAPER POLICY`.

## Reportes y cierre

Bug confirmado: snapshot postclose mezcla monedas bajo nombre ARS.

El postclose review más reciente era 2026-09-30 17:15 ART y VERIFIED. Los reportes formales diarios/semanales aparecen más tarde (~23:03 ART en evidencia previa). Se propone una bitácora post-sesión separada y auditable en backlog.

## Validación M0–M11

- M0–M4: YELLOW.
- M5–M11: GRAY.
- M10/M11 siguen fuera del alcance PAPER y no deben promoverse.

La meta del próximo release no es “todos los hitos GREEN”; es un runtime PAPER coherente, sin P0 conocidos, con evidencia explícita de lo que siga incompleto.

## Release plan: un solo deploy

No desplegar por workstream.

1. Corregir P0/P1 release-scope en branches aisladas.
2. Cada branch con tests/guards y Predeploy V2.
3. Reconciliar sólo los SHAs exactos aprobados sobre un único candidato.
4. Ejecutar un único Predeploy V2 exacto del candidato final.
5. Resolver el gate de disco sin debilitarlo.
6. Un único Deploy V2.
7. Validación inmediata + soak + validación demorada.
8. Actualizar provenance, CURRENT_STATE, pendientes, errores/lecciones.
9. Activar #410 sólo si housekeeping ya está desplegado y su identidad runtime es exacta.

## Criterios bloqueantes antes del deploy

- P0 SRE quick_check del observer eliminado.
- Fast-path dashboard + snapshot/API acotado.
- Reporting multi-moneda corregido.
- BYMA shared market path escribible por runtime UID sin ampliar privilegios.
- Full DB timer non-persistent.
- Provenance post-promotion fail-safe.
- Espacio libre >= pre-transfer guard exacto.
- Candidato final Predeploy V2 GREEN.
- `PRODUCTION_PAPER / real_orders_sent=0 / real routes NOT_CALLED`.
- PPI Watch intacto.

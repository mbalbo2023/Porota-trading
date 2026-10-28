# POROTA TRADING RC6 — CHECKPOINT CANÓNICO PREDEPLOY

Fecha de corte: 2026-09-10 20:33 ART
Estado global: AMARILLO PREDEPLOY — candidato funcional GREEN, falta preflight host fresco + activación/postflight.

## Candidato funcional fijado

- `FUNCTIONAL_CANDIDATE_SHA=e47eeffcb94a9468ac7e7f610beee0869353a77e`
- Versión: `17.0.0-rc6`
- Modo obligatorio: `PRODUCTION_PAPER`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- Política económica: `PAPER_ECONOMIC_GATE_MODE=SHADOW`
- Expectancy: `OBSERVATION_ONLY`
- Régimen: `ALERT_ONLY`
- Concentración sectorial: `BINDING`
- Máximo por sector: `2`

## Suite integrada

Workflow: `RC6 W10 Forward Fix Validation 2026-09-10`
Run: `34542478667`
Resultado: GREEN.

Pruebas:
- W10 causal regression: 5/5 GREEN.
- Suite integrada completa: **1780 passed, 1 warning**.
- `INTEGRATED_TESTS=GREEN`
- `REAL_ORDER_ROUTES=NOT_CALLED`
- `W10_FORWARD_FIX=GREEN`

W10 permanece:
- `W10_POLICY=BINDING`
- cobertura foco `10/10`
- sectores `ENERGIA,FINANCIERO,TECNOLOGIA`
- unmapped fail-closed GREEN
- candidatos válidos evaluables GREEN

Los últimos bloqueantes eran assertions heredadas de tests que contradecían la política RC6 ya congelada (`SHADOW`). Se corrigieron sólo tests; no se cambió lógica de negocio para satisfacerlos.

## Functional Health

Workflow: `RC6 Functional Health Timer Enable 2026-09-10`
Run: `34542267741`
Resultado: GREEN.

Prueba runtime:
- `SAFETY_BEFORE=PRODUCTION_PAPER|0`
- `SAFETY_AFTER=PRODUCTION_PAPER|0`
- `FH_TIMER_ACTIVE=active`
- `FH_TIMER_ENABLED=enabled`
- `FH_SERVICE_RESULT=success`
- `REAL_ORDER_ROUTES=NOT_CALLED`
- `RC6_FUNCTIONAL_HEALTH_TIMER=GREEN`

El amarillo de wiring Functional Health queda cerrado.

## W12 / Contract Evidence

No reabrir autenticación ni trusted-device sin nueva evidencia.
Estado conservado GREEN:
- autenticación/trusted-device ya probados;
- captura readable con modo 0640 root:gid1000;
- Contract Evidence timer RC6 enabled/active;
- importación y wiring previamente validados;
- sólo control de frescura/postflight en el deploy final.

## Runtime de partida conocido

Última evidencia previa al presente predeploy:
- observer/dashboard usando imagen `porota-trading-bot:17.0.0-rc6`;
- `/health` RC6 OK;
- observer `PRODUCTION_PAPER|0`;
- DB quick_check OK;
- observer rootfs readonly;
- secreto PPI productivo montado RO.

El checkout del host conserva archivos históricos/untracked. No ejecutar `git clean`, no borrar evidencia y no arrastrar untracked al candidato. El preflight final debe volver a medir drift y aplicar collision-gate contra los archivos tracked del candidato.

## Decisión de deploy

NO usar el deploy3 histórico tal como está: contiene supuestos RC5, rollback automático y rehearsal RC6→RC5→RC6 incompatibles con la política actual.

Siguiente secuencia exacta:
1. preflight host read-only fresco;
2. exigir invariantes `PRODUCTION_PAPER|0`, DB/health/timers/disk GREEN y preservar untracked;
3. construir desde `FUNCTIONAL_CANDIDATE_SHA` fijado;
4. pruebas offline de imagen antes del corte;
5. activación RC6 transaccional sin rollback automático;
6. postflight completo y soak;
7. checkpoint postdeploy con SHA/image/runtime/timers/W12/DB/órdenes reales.

Política de falla: RCA → forward fix → revalidación. Rollback sólo con autorización explícita del operador.

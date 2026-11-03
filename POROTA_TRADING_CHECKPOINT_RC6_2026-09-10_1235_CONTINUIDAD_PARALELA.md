# POROTA TRADING RC6 — CHECKPOINT CANÓNICO DE CONTINUIDAD PARALELA

Fecha: 2026-09-10
Rama de trabajo: `fix/rc6-w10-sector-map-binding-20260910`
SHA observado al iniciar este checkpoint: `82474ea0de6553e6eb4043b2345fbc937cd01130`

## Invariantes no negociables
- `VERSION=17.0.0-rc6`
- `MODE=PRODUCTION_PAPER`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- Ninguna prueba debe llamar rutas reales de orden.
- W10 concentración sectorial permanece `BINDING`, nunca SHADOW.
- IA intradiaria OFF.
- Deploy final y GO LIVE siguen bloqueados hasta cierre integral.

## Estado CI integrado
- W10 preflight: GREEN.
- W10 sector map: 11 filas, foco 10/10, fail-closed para identidad no mapeada.
- Causal cauciones/W10: GREEN.
- Configuración económica canónica: `PAPER_ECONOMIC_GATE_MODE=BINDING` tanto en defaults como en frozen settings RC6.
- Suite integrada actual: 1.196 tests PASS antes del primer fallo determinístico.
- Primer fallo actual: `tests/test_ppi_public_probe_v17.py::test_un_login_config_todas_familias_y_solo_tickers_devueltos`.
- Resultado observado: `STOPPED` cuando el test histórico esperaba `COMPLETED_OBSERVATION`.
- RCA: el lector `ProductionMarketReader` ya usa el contrato oficial de cauciones (`PESOS{días}` / `DOLAR{días}`, `Name={días}`), mientras `scripts/v17_ppi_public_probe.py` conserva consultas legacy redundantes `PESOS` y `DOLAR` además del alias `CAUCION`, y conserva un límite HTTP calculado para el flujo anterior. No es un fallo de usuario/contraseña ni de reautenticación.
- Política de corrección: alinear el probe/test al contrato oficial vigente sin relajar la barrera read-only, sin refresh automático y sin rutas de cuenta/orden.

## W12 Contract Evidence
- El problema de expiración de sesión ya corregido no se reabre como problema de credenciales.
- El collector RC6 actual permanece fail-closed y read-only; no debe volver a escribir usuario/contraseña/OTP.
- La rama `prep/rc6-w12-minimal-delta-20260910` está en `a9bb4b1144bff708702968b34fa30afb0688a01d` y es ancestro de la rama de trabajo actual; no contiene delta adicional que justifique merge wholesale.
- Pendiente final W12: confirmar/importar wiring mínimo, preservar sesión persistente validada, captura fresca autenticada read-only, evidencia Contract Evidence fresca y scheduler/readiness.

## Host / scheduler
- Runtime observado: RC6 / PRODUCTION_PAPER / real orders 0.
- Contract Evidence activo: `porota-contract-evidence-rc6.timer`.
- Units HF6 encontradas: instaladas pero deshabilitadas/inactivas. Deben retirarse/canonicalizarse antes del cierre final para superficie operacional RC6 inequívoca.

## Identidad legacy
- Auditoría de cierre activo previa: 123 archivos en closure, 99 referencias RC3/RC4/RC5/HF*.
- No todas son equivalentes: tests históricos pueden conservar nombre como evidencia de regresión; runtime/config/dashboard/deploy/systemd no pueden depender de versiones anteriores.
- Persistentes como schema/strategy IDs requieren migración/alias compatible, no reemplazo ciego.
- `scripts/rc6_transactional_host_deploy_v3.sh` NO es apto para deploy final mientras espere rama/imagen RC5 y units antiguas.

## Carriles paralelos activos
1. CI/pytest: corregir coherencia del PPI public probe y continuar `pytest -x` hasta 0 fallos.
2. W12: auditar delta efectivo y wiring/captura fresca sin regresión de sesión.
3. Legacy: clasificar y sanear referencias operativas RC/HF, con compatibilidad explícita sólo donde sea necesario.
4. Deploy: producir ruta transaccional RC6-only y gate de identidad operacional.
5. Checkpoints: actualizar este estado ante cada cierre, nuevo blocker, nuevo SHA o cambio de deploy readiness.

## Semáforo
- Seguridad PAPER/read-only: GREEN.
- W10 BINDING: GREEN.
- Contract Evidence scheduler activo RC6: GREEN.
- Suite integrada: YELLOW — 1196 PASS, 1er fallo en PPI public probe.
- W12 cierre final: YELLOW.
- Identidad legacy operacional: RED.
- Deploy: RED / NO EJECUTAR.
- GO LIVE: RED / NO AUTORIZADO.

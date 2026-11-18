# Porota Trading RC6

Sistema de análisis y simulación para el mercado argentino.
**PAPER/SHADOW ONLY**: modo esperado `PRODUCTION_PAPER / SIMULATION`, rutas de
órdenes reales bloqueadas y requisito `real_orders_sent=0`.

## Documentación vigente

| Fuente | Uso |
|---|---|
| [AGENTS.md](AGENTS.md) | Alcance, seguridad, autonomía, concurrencia y evidencia |
| [Runbook](docs/RUNBOOK.md) | Desarrollo, pruebas, Predeploy, Promote y cierre |
| [Política](ops/policy/porota-policy.yaml) | Contrato estructurado de seguridad y release |
| [Política de pruebas del candidato](https://github.com/mbalbo2023/Porota-trading/blob/integration/rc6-a1115-r87-unified-paper-candidate-20261110/ops/policy/test-policy.yaml) | Selección y clasificación de pruebas |
| [Seguridad](SECURITY.md) | Reporte privado de vulnerabilidades |
| [Evidencia contractual](docs/HF6_CONTRACT_EVIDENCE_POLICY.md) | Identidad y procedencia por instrumento |
| [Canal de evidencia](docs/RUNTIME_EVIDENCE_CHANNEL_RC6.md) | Referencia del productor de evidencia |
| [Historia](docs/history/README.md) | Versiones exactas de documentos retirados |

## Alcance de main

Esta rama contiene el plano de control. Su CI compila los scripts y descubre
sus pruebas; no construye la aplicación ni acredita runtime. La política de
pruebas de release y Predeploy/Promote viven en el candidato de aplicación.

## Trabajo y operación

GitHub y GitHub Actions son el plano de control. Trabajar en ramas aisladas,
revisar el cambio y reunir los frentes en un candidato. Predeploy construye una
imagen y prueba ese mismo paquete; Promote la instala sin reconstruirla.
El bootstrap del entorno rootless es independiente del release.

El repositorio contiene código y evidencia de distintas fechas. Confirmar los
SHA de `main`, producto y candidato antes de una acción; el HEAD de una rama no
certifica qué está instalado. Las ejecuciones y recibos actuales determinan el
estado: [Actions](https://github.com/mbalbo2023/Porota-trading/actions),
[PRs abiertos](https://github.com/mbalbo2023/Porota-trading/pulls),
[continuidad](https://github.com/mbalbo2023/Porota-trading/issues/1093) y
[ownership](https://github.com/mbalbo2023/Porota-trading/issues/980).

**Incidente P0 REST (corte 18-Nov-2026):** #1192 recuperó el circuito REST
de PPI en código y pasó Predeploy, pero [Deploy V2 #44376376095](https://github.com/mbalbo2023/Porota-trading/actions/runs/44376376095)
falló en POSTFLIGHT. [Checkpoint y parche local](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6992170026):
Current/Book, frescura, instrumentos READY y workers siguen sin certificación
física. Login PPI no acredita datos.

Por corrección explícita del operador, **PPI Watch no es «intocable» por política**.
Toda intervención requiere ownership, scope y evidencia real; no deducir cuotas
PPI de dos GET/h ni prohibir la API REST existente. FIX-FORWARD; documentación
sola no instala servicios ni autoriza órdenes reales.

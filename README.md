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
| [Política de pruebas](ops/policy/test-policy.yaml) | Selección y clasificación de pruebas |
| [Seguridad](SECURITY.md) | Reporte privado de vulnerabilidades |
| [Evidencia contractual](docs/HF6_CONTRACT_EVIDENCE_POLICY.md) | Identidad y procedencia por instrumento |
| [Canal de evidencia](docs/RUNTIME_EVIDENCE_CHANNEL_RC6.md) | Referencia del productor de evidencia |
| [Historia](docs/history/README.md) | Versiones exactas de documentos retirados |

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

PPI Watch queda fuera del alcance. Los cambios son fix-forward; la documentación
por sí sola no instala servicios ni autoriza operaciones reales.

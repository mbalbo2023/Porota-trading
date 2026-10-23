# Porota Trading RC6 — entrada al producto PAPER

Esta rama contiene el código del candidato RC6. Su HEAD no demuestra por sí solo qué
SHA está instalado: comprobar `CURRENT_STATE_V2.json`, el Deploy V2 exacto y la
evidencia runtime antes de afirmar un estado operativo.

## Seguridad y alcance

- Sólo `PRODUCTION_PAPER` / SHADOW: decisiones y fills simulados, `real_orders_sent=0`;
  rutas de órdenes reales prohibidas. No operar con dinero real.
- PPI Watch no se modifica. Backups están suspendidos por política vigente.
- FIX-FORWARD únicamente; sin rollback, reinicios o estrés del Droplet durante rueda.
- GitHub y Actions son el plano normal de cambios: rama aislada, PR, Predeploy V2
  del SHA exacto, artefacto congelado, un solo candidato reconciliado y Deploy V2
  sujeto a owner global y aceptación PAPER.
- Ningún README, test, PR o workflow exitoso sustituye la prueba del Droplet.

## Al empezar una tarea

Leer [AGENTS.md](AGENTS.md), la [política RC6](ops/policy/porota-policy.yaml)
y el [contrato de continuidad](docs/codex/RC6_AGENT_CONTINUITY_V1.md). Resolver
HEAD, PRs/issues abiertos, owners y los runs recientes en GitHub. Revalidar
contra la evidencia directa del runtime; reportar `NO_VERIFICADO` cuando falte.

La rama `main` aloja documentación y coordinación, pero no identifica por sí
misma el producto instalado. Las guías de v16, Telegram para operar en real,
`docker compose up` y los ejemplos antiguos son material histórico; no son
instrucciones de arranque ni de promoción de RC6.

## Superficies de consulta

- [Issues](https://github.com/mbalbo2023/Porota-trading/issues),
  [PRs](https://github.com/mbalbo2023/Porota-trading/pulls) y
  [Actions](https://github.com/mbalbo2023/Porota-trading/actions).
- [Handoffs](docs/handoffs/) y [política de pruebas](ops/policy/test-policy.yaml):
  documentos con corte temporal; nunca convertirlos en estado presente sin un recibo nuevo.

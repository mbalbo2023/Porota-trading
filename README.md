# POROTA TRADING — entrada al repositorio

**Atención:** esta rama por defecto (`main`) **no representa por sí sola el código ni el runtime productivo RC6**. Sus referencias históricas deben contrastarse con las fuentes actuales. No tomar el README, el nombre de una rama ni un chat como certificación de despliegue.

## Empezar un trabajo con ChatGPT o Codex

1. Leer [AGENTS.md](AGENTS.md), puerta de entrada de seguridad y gobernanza.
2. Aplicar el [contrato de continuidad RC6 para Codex](docs/codex/RC6_AGENT_CONTINUITY_V1.md): recuperar contexto desde GitHub, no desde memorias supuestamente compartidas.
3. Resolver la rama productiva y su SHA vigentes con GitHub. Consultar el `AGENTS.md` y `ops/policy/porota-policy.yaml` **de esa rama**, sus releases/handoffs actuales y evidencia posterior de Actions y runtime.
4. Consultar PRs/Issues y ownership **antes** de modificar archivos. Trabajar con una rama aislada, scope acotado y PR, nunca escribir directamente en `main` ni productiva.
5. Separar explícitamente código, CI, artifact, despliegue y comportamiento real. Cuando falte evidencia, usar `NO_VERIFICADO`.

Accesos: [rama productiva RC6 (verificar vigencia)](https://github.com/mbalbo2023/Porota-trading/tree/deploy/rc6-pr69-isolated-20260915), [PRs](https://github.com/mbalbo2023/Porota-trading/pulls), [Issues](https://github.com/mbalbo2023/Porota-trading/issues) y [Actions](https://github.com/mbalbo2023/Porota-trading/actions).

## Invariantes operativos

- Sólo **PAPER/SHADOW**, modo esperado `PRODUCTION_PAPER / SIMULATION`. Órdenes reales prohibidas, `real_orders_sent=0`, rutas reales bloqueadas.
- **PPI Watch NO TOCAR** salvo autorización explícita y acotada.
- **FIX-FORWARD ONLY**, sin rollback.
- GitHub/GitHub Actions son el plano de control normal. SSH/manual únicamente break-glass; no operar por Codespaces.
- `READ_ONLY` no bloquea. `WRITE_OWNER` es exclusivo por scope. `DEPLOY_OWNER` es exclusivo global.
- Un solo candidato reconciliado, build único, artifact inmutable y un único deploy final sujeto a gates y evidencia directa de runtime.
- Los cambios de documentación en la raíz de la rama productiva **también pueden activar Deploy V2**. No fusionarlos por separado sin coordinación del integrador y revisión del workflow vigente.
- Sin credenciales, `.env`, tokens ni datos sensibles versionados. No pedir al usuario que ejecute comandos habituales: utilizar Actions/conectores.

Estas reglas son requisitos; **este README no afirma que el estado de runtime de hoy esté verificado**.

## Documentación histórica preservada

Los siguientes archivos representan decisiones y checkpoints de su fecha, **no un baseline vigente sin revalidación**:

- [Protocolo anterior](POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md).
- [Guía anterior de inicio de chat](POROTA_TRADING_CHAT_START_HERE.md).
- [Checkpoint del 14 de septiembre de 2026](POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md).

No usar el antiguo SHA de septiembre, la versión heredada del README ni los workflows de `main` como sustitutos de la verdad del producto RC6. La promoción debe gobernarse por los procedimientos **vigentes en la rama productiva** y sólo dentro del ownership autorizado.

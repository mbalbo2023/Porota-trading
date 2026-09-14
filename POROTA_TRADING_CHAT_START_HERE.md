# POROTA TRADING — empezar una conversación nueva

## Puerta de entrada

No empieces sólo con el último mensaje, una captura o una memoria aislada. Primero:

1. Lee `/AGENTS.md` y `/POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md` completos.
2. Lee `/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md` completo y todo prompt/checkpoint posterior que el usuario identifique.
3. Para el trabajo contractual/multi-fuente del 2026-09-14, lee además obligatoriamente:
   - `/docs/handoffs/POROTA_TRADING_CHECKPOINT_CONTRACT_EVIDENCE_2026-09-14.md`
   - `/docs/handoffs/POROTA_TRADING_PENDING_MULTI_SOURCE_PPI_IOL_2026-09-14.md`
   - `/docs/handoffs/POROTA_TRADING_CHECKPOINT_OPERABILITY_SOURCE_MAP_2026-09-14.md`
   - `/docs/handoffs/POROTA_TRADING_PENDING_OPERABILITY_BY_FAMILY_2026-09-14.md`
   - `/docs/handoffs/POROTA_TRADING_PENDING_INTEGRAL_SOLUTION_WAVES_2026-09-14.md`
   - `/docs/research/POROTA_MULTI_SOURCE_MARKET_DATA_AND_CONTRACT_ARCHITECTURE_2026-09-14.md`
   - `/docs/research/POROTA_PROVIDER_COVERAGE_MATRIX_INITIAL_2026-09-14.md`
   - `/docs/research/POROTA_OPERABILITY_REQUIREMENTS_AND_SOURCE_MAP_BY_FAMILY_2026-09-14.md`
   - `/docs/research/POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md`
4. Comprueba en GitHub el HEAD/SHA actual, cambios posteriores al checkpoint, PRs que toquen el alcance y runs/evidencias recientes. No supongas que el checkpoint sigue siendo el último estado.
5. Resume lo entendido con semáforo, separando código, CI, runtime, evidencia/importación y READY end-to-end. Declara todo dato no verificado.
6. Continúa sólo dentro del alcance y permisos documentados. Actualiza el checkpoint con cada hito material y verifica el commit en GitHub.

Si este chat no tiene acceso al repositorio, informa que no pudo pasar esta puerta y pide el checkpoint completo o el acceso faltante. No digas que recuperaste todo leyendo sólo un título, extracto o mensaje.

## Prompt breve para copiar en chats sin carga automática del repositorio

> Continúa POROTA TRADING desde su checkpoint, sin reconstruir desde cero. Antes de dar estatus o ejecutar nada, lee íntegramente `/AGENTS.md`, `/POROTA_TRADING_CONTINUIDAD_OBLIGATORIA.md`, `/POROTA_TRADING_CHAT_START_HERE.md` y `/POROTA_TRADING_CHECKPOINT_CONTINUIDAD_CROSSCHAT_2026-09-14.md`. Lee también los checkpoints y pendientes contractuales/multi-fuente bajo `/docs/handoffs/` y los documentos de investigación bajo `/docs/research/` fechados 2026-09-14, en especial `POROTA_TRADING_CHECKPOINT_OPERABILITY_SOURCE_MAP_2026-09-14.md`, `POROTA_TRADING_PENDING_INTEGRAL_SOLUTION_WAVES_2026-09-14.md`, `POROTA_OPERABILITY_REQUIREMENTS_AND_SOURCE_MAP_BY_FAMILY_2026-09-14.md` y `POROTA_INTEGRAL_INSTRUMENT_SOLUTION_BLUEPRINT_2026-09-14.md`. Verifica en GitHub la rama, SHA, PRs y runs posteriores. Conserva todas las decisiones, restricciones, pendientes y evidencia; distingue código, CI, runtime, evidencia/importación y READY end-to-end; marca lo no verificado y no inventes ni repitas trabajos cerrados. Informa primero lo que entendiste y continúa sólo dentro del alcance autorizado. Actualiza el checkpoint después de cada hito y verifica su commit SHA.

## Punto operativo actual incluido en el checkpoint

La copia cross-chat conserva íntegro `POROTA_TRADING_CHECKPOINT_ACTIVE_READY_2026-09-14.md` y agrega el delta de la captura contractual reciente. Los checkpoints de operabilidad por familia agregan el diseño multi-source PPI + IOL y deben prevalecer para esa línea de trabajo cuando exista evidencia posterior. El blueprint integral y el tracker obligatorio organizan la implementación por WAVE y por instrumento patrón. Si evidencia nueva cambia el estado, actualiza primero el checkpoint sin borrar el antecedente.

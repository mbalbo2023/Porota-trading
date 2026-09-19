# POROTA TRADING RC6 — CHECKPOINT LIVE DECISION COCKPIT
Fecha: 2026-09-18 / despliegue completado 2026-09-19 00:59 UTC
Workstream aislado: Site privado 8766 + Decision Cockpit LIVE

## Regla de continuidad
Este workstream fue desarrollado y desplegado de forma aislada para no mezclar cambios con otro desarrollo paralelo.
No reconstruir, no repetir y no fusionar automáticamente con otros workstreams.
Antes de integrar, revalidar contra la rama canónica vigente y reconciliar sólo los archivos de este alcance.

## GitHub
- Rama: `feat/rc6-live-decision-cockpit-20260919`
- PR: #107
- Estado PR: DRAFT
- Base al iniciar: `deploy/rc6-pr69-isolated-20260915@c4a4c70ffc35b652022d124151ab990ea8600691`
- SHA exacto desplegado: `50253ab0d5dadae75a01346c48f3504fd84cee01`
- GitHub Actions deploy run: `35411100398`
- Job: `deploy-isolated-cockpit`
- Resultado: SUCCESS / GREEN

## Qué quedó desplegado
1. Site privado localhost-only:
   - bind: `127.0.0.1:8766`
   - acceso: SSH Port Forwarding
   - exposición pública: NO
   - servicio: `porota-private-snapshot.service`
2. Decision Cockpit LIVE:
   - collector: `/opt/porota-private-snapshot/live_collector.py`
   - salida durante rueda: `/opt/porota-trading/data/paper_v17/snapshots/live_latest.json`
   - autoridad: `OBSERVE_ONLY`
   - sin órdenes reales
   - sin cambios automáticos de estrategia
3. Timer:
   - `porota-live-decision-cockpit-rc6.timer`
   - cada 60 s durante la misma ventana usada por IOL SHADOW:
     - 10:30–10:59 ART
     - 11:00–16:59 ART
   - filtro adicional por `ak_byma_calendar`, fail-closed
4. Snapshots:
   - preopen: `preopen_latest.json`
   - live: `live_latest.json`
   - postclose: `postclose_latest.json`
   - archivo histórico por fase en `snapshots/archive/`
   - el preopen futuro incorpora una muestra acotada de decisiones para comparación LIVE
5. Site:
   - Decision Cockpit
   - Top oportunidades, máximo 10
   - Por qué NO operó
   - Trazabilidad de últimas 10 decisiones
   - IOL freshness / SHADOW
   - cambios vs preopen si existe evidencia
   - riesgo visible
   - contrafáctico LIVE declarado INSUFFICIENT_EVIDENCE hasta postcierre
   - Cierre de rueda
   - Evidencia y servicios
   - Semáforo ejecutivo

## Evidencia runtime del deploy
Pre y post deploy:
- SQLite quick_check: `ok`
- mode: `PRODUCTION_PAPER`
- real_orders_sent: `0`
- decision_authority: `OBSERVE_ONLY`
- real_orders_authorized: `false`
- IOL mode: `SHADOW`
- IOL decision_effect: `OBSERVE_ONLY`
- probe del collector encontró 10 oportunidades visibles
- Site health: GREEN
- bind: `127.0.0.1:8766`
- public_exposure: `false`
- `porota-private-snapshot.service`: active/enabled
- `porota-live-decision-cockpit-rc6.timer`: active/enabled
- próximo disparo observado: 2026-09-21 13:30 UTC = 10:30 ART

## Prueba explícita de no interferencia
El workflow verificó:
- runtime git HEAD antes/después sin cambios
- branch/detached state antes/después sin cambios
- observer container ID/running/restart count sin cambios
- dashboard container ID/running/restart count sin cambios
- Docker config: NO cambiado
- dashboard 8000: NO cambiado
- real_orders_sent: 0

Runtime HEAD observado durante deploy:
`a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`
Estado git runtime observado: detached HEAD.
No cambiarlo desde este workstream.

## Estado de snapshots al terminar
- postclose_preserved: true
- preopen_preserved: false para la jornada 18/09 porque el preopen anterior ya había sido sobrescrito antes de instalar la preservación.
- Esto NO se reconstruye ni se inventa.
- A partir del próximo preopen, se preserva automáticamente.

## Contrafácticos — significado de INSUFFICIENT_EVIDENCE
`INSUFFICIENT_EVIDENCE` NO significa que haya fallado la operación, el Site ni el motor.
Significa que Porota no conserva todavía toda la evidencia necesaria para afirmar de forma reproducible qué habría ocurrido bajo una decisión alternativa.

Ejemplo válido de pregunta contrafáctica:
> Si IOL no hubiese estado disponible en el instante de decisión de YPFD, ¿el mismo motor habría mantenido WAIT o habría ejecutado BUY? ¿Cuál habría sido el resultado usando la misma política de ejecución?

No alcanza con mirar el resultado final del activo. Eso introduciría hindsight bias. El contrafáctico debe reconstruirse con la información disponible exactamente en el instante original de decisión.

### Evidencia mínima que debe capturarse por decisión
Para poder marcar un contrafáctico individual como `VERIFIED`, capturar:

1. Identidad de decisión:
   - `decision_id`
   - timestamp exacto
   - símbolo
   - acción real: BUY / SELL / WAIT / HOLD
   - score real del motor
   - motivo final
   - gates/reglas activados
2. Inputs exactos del motor al decidir:
   - precio y market data usados
   - velas / indicadores realmente consumidos
   - PPI
   - IOL
   - régimen
   - volatilidad
   - liquidez
   - riesgo
   - concentración
   - cualquier otra señal efectivamente usada por el motor
3. Freshness de fuentes:
   - timestamp de cada fuente
   - edad del dato al decidir
   - estado READY / STALE / ERROR / N/D
4. Versión de lógica:
   - Git SHA del código
   - versión/configuración del motor
   - parámetros efectivos
5. Escenario contrafáctico explícito:
   - `SIN_IOL`
   - `IOL_IGNORADO`
   - `GATE_SPREAD_RELAXED`
   - `SIN_FUENTE_X`
   - etc.
   Cambiar UNA sola variable por vez.
6. Replay determinístico:
   - volver a evaluar con el mismo motor y mismos inputs
   - cambiar únicamente la variable del escenario
   - guardar la decisión hipotética y su score
7. Ejecución hipotética comparable:
   - misma política de entrada/salida
   - mismo spread/slippage modelado
   - mismas comisiones
   - mismo horizonte temporal
   - mismos SL/TP/time-exit aplicables
8. Camino posterior del mercado:
   - precios posteriores necesarios para saber si habría activado SL, TP o cierre
9. Resultado:
   - PnL real
   - PnL hipotético
   - drawdown
   - tiempo en posición
   - diferencia factual entre real e hipotético
10. Trazabilidad:
   - unir decisión → inputs → fuentes → ejecución → resultado → contrafáctico mediante IDs persistentes.

### Dos niveles de suficiencia
A. `VERIFIED` individual:
- suficiente evidencia para reconstruir una decisión y un escenario contrafáctico puntual.
- permite afirmar algo del tipo:
  - decisión real: WAIT
  - decisión contrafáctica SIN_IOL: BUY
  - resultado hipotético: -X
  - conclusión factual: en ESTE CASO, la presencia de IOL evitó esa operación.

B. Evidencia estadística:
- necesaria antes de concluir que una fuente, gate o regla mejora/empeora sistemáticamente el motor.
- requiere múltiples casos comparables.
- separar por activo, régimen, volatilidad y contexto.
- no inferir mejoras estructurales a partir de una sola operación.

### Arquitectura recomendada pendiente
Crear un ledger append-only de evidencia contrafáctica:
`decision -> input_snapshot -> real_decision -> counterfactual_variants -> subsequent_market_path -> outcome`

Características obligatorias:
- append-only / auditable
- read-only respecto del motor de decisiones
- no ejecutar órdenes
- no cambiar parámetros automáticamente
- no reconsultar una fuente después para rellenar retroactivamente el pasado
- si un dato faltó en tiempo real, conservar `MISSING` / `INSUFFICIENT_EVIDENCE`
- captura EN EL MOMENTO de cada decisión, no reconstrucción retrospectiva al cierre
- máxima trazabilidad por `decision_id`
- integrable al postclose para convertir contrafácticos de amarillo a `VERIFIED` cuando corresponda

### Regla de seguridad
Los contrafácticos sirven para auditoría y aprendizaje.
NO deben promover ni cambiar automáticamente estrategia, gates, pesos o parámetros.
Primero acumular evidencia, luego auditar, y cualquier cambio de motor debe pasar por desarrollo/validación/deploy separado.

### Próxima implementación sugerida
Agregar un collector/ledger específico de evidencia contrafáctica que capture en vivo:
- inputs exactos
- freshness
- código/config
- decision_id
- decisión real
- candidatos contrafácticos definidos
Luego, post-cierre:
- hacer replay determinístico
- evaluar salida hipotética
- publicar resultado en la solapa Cierre de rueda

Mientras esto no exista para un caso, el Site debe mostrar `INSUFFICIENT_EVIDENCE` y nunca inventar la respuesta.

## Archivos del PR
- `rc6_live_decision_cockpit.py`
- `rc6_private_site_8766.py`
- `ops/porota-private-snapshot-archive`
- `systemd/porota-private-snapshot.service`
- `systemd/porota-live-decision-cockpit-rc6.service`
- `systemd/porota-live-decision-cockpit-rc6.timer`
- drop-ins preopen/postclose
- tests focalizados
- CI focalizada
- workflow de deploy aislado

## Estado final
🟢 DESARROLLO: completo
🟢 TESTS: completos
🟢 DEPLOY: completo
🟢 PRIVATE SITE 8766: activo
🟢 TIMER LIVE: activo/enabled
🟢 PAPER / real orders 0: verificado
🟢 NO interferencia con runtime canónico: verificada
🟡 LIVE fresco: no aplica fuera de rueda; se validará automáticamente en la próxima rueda
🟡 preopen 18/09: no recuperable; no inventar
🟡 CONTRAFÁCTICOS: infraestructura visual existente, pero faltan ledger + captura de evidencia en tiempo de decisión + replay determinístico para pasar de INSUFFICIENT_EVIDENCE a VERIFIED

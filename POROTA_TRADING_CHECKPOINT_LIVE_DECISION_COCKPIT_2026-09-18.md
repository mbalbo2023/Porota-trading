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

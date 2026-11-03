# A627 — Causalidad de cron GitHub, EOD/T+1 y cobertura 04-Nov-2026

**Plano:** GitHub Actions `main`, OFFHOST/READ_ONLY. **No es un Deploy** ni
certifica el producto #778, el host, órdenes PAPER o PPI. PPI Watch intacto.

**Governance:** `RC6_A627_SCHEDULE_CAUSALITY_MAIN`, [owner y scope exactos
en #732](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6615939404),
base main reconciliada tras #905 `f33821b01c5342008f7de3aa4bb77058403efbbb`
(base original `d94027f7af81e71d9f6fd8064c05d40fa0193835`). El PR debe cumplir
`checkpoint-attestation` con URL de owner, rama, SHA base y seis paths; el
workflow CI inicial falló por ausencia de esos campos, y el guard debe seguir
siendo bloqueante. No se desprotegieron reglas.

## ERROR / RCA

- A613/A614 reportaron **57/88 slots programados observados** para el monitor.
  Esa cifra histórica debe reconciliarse con runs `event=schedule`, no con
  `workflow_dispatch`, push o PR; no se considera un RCA de plataforma por sí
  sola. El GET directo del índice histórico no estaba disponible desde el
  plano del agente, de modo que A627 calculará su propio recibo desde Actions.
- [Market Watch READ_ONLY](../../.github/workflows/rc6-market-watch-readonly.yml)
  define cron UTC `7-57/10 13-20 * * 1-5` + `7 21 * * 1-5`: **49 slots UTC
  por día hábil**, de los cuales 10:07, 10:17 y 10:27 ART deben activarse como
  run pero omitir sonda por gate de rueda 10:30. Desde COM19031 (02-Nov) el
  gate permite observación T+1 hasta 18:15 ART, sin admisión PAPER adicional.
- El `schedule_health` previo de
  [`scripts/rc6_market_watch_readonly.py`](../../scripts/rc6_market_watch_readonly.py)
  clasifica básicamente frescura por `run_started_at`; no asigna cada slot ni
  distingue falta de trigger, cola, omisión de sonda por gate/dependencia o
  fallo. No se cambia ese workflow ni el watchdog instalado.
- GitHub documenta que schedule es **best effort**, puede demorarse/perderse
  y sólo se evalúa en default branch. Esta característica explica un modo de
  fallo posible, **no acredita la causa concreta** de los 31 slots ausentes:
  <https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>

## FIX / GUARD

`scripts/rc6_a627_schedule_causality_offhost.py` sólo consulta la API oficial
de **GitHub Actions** por HTTPS con permiso `actions:read`; no usa DO credentials,
SSH, host ni proveedores. Informa únicamente IDs de run, tiempos UTC/ART y
estados acotados. El nuevo workflow
`.github/workflows/rc6-a627-schedule-causality-offhost.yml` no modifica
`rc6-market-watch-readonly.yml` ni PPI Watch y agrega `workflow_dispatch` manual
de respaldo. Ningún `actions:write`, POST, deploy, proceso host ni orden.

**A617, control host separado:** en la misma PR se amplía exclusivamente el
trigger del Action A617 ya integrado con cuatro cron del 04/11 (11:05,
14:50, 16:50, 18:20 ART; UTC 14:05, 17:50, 19:50, 21:20). El primer diseño
coincidía con tres slots de **A630 #908 Current+Book** (10:45, 14:30 y
16:30 ART), con riesgo de competencia SSH/RAM/DB y leases de host; esos tres
crons se desplazan 20 minutos, con un mínimo de separación entre ambos
conjuntos de 15 minutos. Adicionalmente, el job `host_provenance` comparte
`concurrency.group=rc6-unified-paper-deploy`,
`cancel-in-progress=false` con el job host A630 y la exclusión canónica:
**serializa** accesos incluso ante jitter de Actions, sin habilitar Deploy.
Este mutex se aplica **a nivel job**, manteniendo la concurrencia propia del
workflow a nivel root. No modifica su script de lectura ni su comando:
`unshare --mount` con bind privado remontado RO antes de abrir SQLite.
`host_provenance` sólo atraviesa SSH si `day_gate.active=1` (04-Nov ART para
schedule; manual explícito de cualquier día). Si el kernel/sudo deniega
`unshare`/mount, Action falla **NO_VERIFICADO**, no reintenta con permisos
menores, no abre DB, no hace PPI GET. Este recibo es independiente de A627,
que jamás consume ni altera el host.

**Riesgo remanente:** el slot A617 18:20 ART puede coincidir con timers EOD
locales; el mutex de Actions no serializa los systemd timers del droplet.
Mantener el RO fail-closed y contrastar InvocationID/reintentos auténticos
antes de afirmar ausencia de contención o EOD GREEN. No se mueve ese slot sin
reconciliar el calendario de EOD y autorización de su dueño.

**Evidencia host posterior:** [A617 manual run #41722452619](https://github.com/mbalbo2023/Porota-trading/actions/runs/41722452619),
03-Nov 21:30Z, SUCCESS con montaje RO efectivo y `db_opened=true`;
`selector`, `snapshots` y `scanner_cycle` observados. `scanner_receipt`
y `native_book_journal` siguen **ABSENT**, y `provider_gets=0`.
Esto prueba el camino de lectura de A617 **en esa ejecución**, pero no
autentica procedencia de PPI, lease/EXIT, operaciones PAPER, EOD o un
InvocationID de systemd; los crons del 04/11 siguen **no ejecutados**.

**Regresión RCA:** A617 tenía una prueba que exigía literalmente el `if`
`workflow_dispatch` exclusivo. Tras añadir `schedule` con gate por fecha,
esa aserción histórica falló aunque el comando privado RO no cambió.
La corrección actualiza únicamente la expectativa del test existente para
exigir ambas rutas y el gate de fecha antes de SSH; mantiene las defensas
contra abrir WAL/-shm en FS escribible. No se omite la regresión.

| Clasificación | Evidencia necesaria | No implica |
|---|---|---|
| `NO_TRIGGER` | Slot vencido 20 min sin `event=schedule` creado en ventana de 10 min, API histórica completa | GitHub nunca disparó después o causa raíz del proveedor |
| `NO_TRIGGER_OR_LATE_AMBIGUOUS` | Hay run posterior que también podría corresponder a slot anterior | Fijar arbitrariamente intención cron |
| `DELAYED` | Run de schedule creado/iniciado más de 5 min tarde, atribución inferida | Que el host haya sido sondeado |
| `SKIPPED` | Job `watch` o paso de sonda SKIPPED | Falla técnica si gate 10:07–10:27 esperaba skip |
| `FAILED` | Run/job/paso FAILED según Actions | Estado causal del EOD/DB |
| `PROBE_STEP_SUCCEEDED` | Paso `Collect bounded read-only host truth` SUCCESS | Runtime GREEN, timer systemd o DBA |
| `NO_VERIFICADO_RERUN` | `run_attempt > 1`; jobs actuales pertenecen al reintento | Primer probe scheduled fresco o su resultado original |
| `NO_VERIFICADO` | API, identidad, jobs o cobertura insuficientes | `0` faltantes ni éxito |

El evento de run REST **no contiene el cron slot causal de origen**; el join
ventana 10 minutos está rotulado `INFERRED_10M_CREATION_WINDOW`. Una creación
demorada puede haber sido asignada al tick vecino: por eso se informa
ambigüedad y no se declara certeza de `NO_TRIGGER` en esas colisiones.
`P1_SCHEDULE_SILENT` exige dos slots maduros de rueda consecutivos omitidos,
fallidos o saltados, sin API incompleta ni late ambiguo. No crea transición
operativa ni suple la alerta de watchdog.

**EOD/T+1:** el código de timers del candidato describe preopen 09:45/10:10/
10:20 y postclose 17:08/18:08/18:45, con ventanas EOD hasta la noche; esto
son **expectativas de calendario de código**, no ejecución. A627 devuelve
`host_eod_t1.state=NO_VERIFICADO` y
`dbus_invocation_id=NO_VERIFICADO` hasta disponer de recibo host auténtico
`InvocationID` + unit result + tiempo + source SHA correlacionado. No se infiere
desde una Action exitosa o de los 57/88 eventos.

## 04-Nov — observación y respaldo manual

La misión A630 de PPI Current+Book #908 dispone de cuatro ventanas
de host 10:45, 11:20, 14:30 y 16:30 ART; A617 queda detrás en
11:05, 14:50, 16:50 y 18:20 ART con mutex común.
A627 **no duplica** capturas PPI y no conecta al host.
A627 observa sólo GitHub metadata después, en estos checkpoints:

| A627 ART 04/11 | UTC | Cobertura / advertencia |
|---|---|---|
| 10:50 | 13:50 | preopen y gate; primer tick 10:37 todavía en gracia |
| 11:25 | 14:25 | confirmación temprana de rueda y faltantes maduros |
| 14:35 | 17:35 | mitad de rueda |
| 16:35 | 19:35 | fuentes antes de T+1 |
| 17:15 | 20:15 | T+1, sin confundir familias |
| 18:35 | 21:35 | último cron 18:07 ya maduro; no equivale a cierre EOD |

Cron `4 11` sigue registrado en GitHub otros años (no hay año en la sintaxis);
el gate de la Action **omite** cualquier fecha distinta de `2026-11-04`.
**Estas ejecuciones futuras no están acreditadas por CI ni garantizadas por
GitHub.** Cualquier tick omitido se puede reemplazar sin terminal/droplet:

1. Abrir la Action A627 en GitHub, ejecutar **Run workflow** en `main`, día
   `2026-11-04`, `lookback_days=1` o `2`. Queda artefacto de clasificación sin
   datos sensibles ni sonda de host.
2. Si hace falta verificar PPI COLD/procedencia sin GET, abrir por separado
   [A617 PPI origin/selector READ_ONLY](https://github.com/mbalbo2023/Porota-trading/actions/workflows/rc6-a617-ppi-origin-selector-readonly.yml).
   El montaje RO ya tuvo un recibo manual válido el 03/11; cualquier futuro
   fallo sigue siendo `NO_VERIFICADO`, sin abandonar el RO fail-closed.
3. Para la sonda operacional normal del Market Watch, usar su
   [workflow_dispatch](https://github.com/mbalbo2023/Porota-trading/actions/workflows/rc6-market-watch-readonly.yml)
   con **`notification_mode=read_only`**, sin interpretar ese run manual como
   `event=schedule` ni modificar configuración de Watch.

Los tres caminos son independientes; A627 **no despacha automáticamente**
A617/Market Watch, no solicita permisos `actions:write` ni evita los guards
existentes. No realizar Predeploy/Deploy hasta todos los stoppers cerrados.

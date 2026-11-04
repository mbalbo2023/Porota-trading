# RC6 A627 — P1 histórico y ventana actual (control-plane READ_ONLY)

## Motivo y evidencia

En la rueda del 04-Nov-2026, el diagnóstico A627 manual de 16:57 ART
encontró 40 slots `NO_TRIGGER` y dos `PENDING_GRACE` para **Market Watch**.
Después llegaron eventos nativos tardíos de GitHub Actions. El recibo A627
`41907742392` de las 17:33 ART contó 42 `NO_TRIGGER`, uno ambiguo por
posible retraso, uno `DELAYED` y uno pendiente de gracia, pero su antiguo
`source_scheduler_finding` decía `NO_CONFIRMED_TWO_SLOT_SILENCE` porque
examinaba únicamente los dos últimos slots maduros.

El que haya llegado un trigger tardío **no rehabilita retroactivamente**
los slots anteriores para los cuales no hubo entrega puntual verificable.
Tampoco permite afirmar que los eventos omitidos fueron definitivamente
perdidos por GitHub: `NO_TRIGGER` conserva el significado «sin coincidencia
en la ventana observada», no «GitHub nunca lo enviará».

## Contrato de recibo

Para cada instantánea con cobertura suficiente del API:

- `historical_mature_two_slot_silence_observed`: `true` si **cualquier** par
  consecutivo de slots maduros `WATCH_EXPECTED` consta como `NO_TRIGGER`,
  `FAILED` o `SKIPPED` en la instantánea. Recuerda un incidente intradía.
- `latest_mature_two_slot_silence_now`: `true` sólo si los **dos últimos**
  slots activos maduros tienen esos estados. Identifica el fallo de la
  ventana más reciente, sin pretender demostrar que se recuperó todo el día.
- `source_scheduler_finding=P1_SCHEDULE_SILENT` si se observó algún par
  histórico, aun cuando la última ventana deje de ser silenciosa. El estado
  `NO_CONFIRMED_TWO_SLOT_SILENCE` requiere ausencia de pares históricos en
  la instantánea y cobertura suficiente.
- Si la cobertura del API es insuficiente, **ambos indicadores son `null`**
  y `source_scheduler_finding=NO_VERIFICADO`; nunca se traduce desconocimiento
  en salud del scheduler.

Los cálculos no clasifican individualmente cuál expresión cron originó cada
run: la asignación usa ventanas de creación inferidas. Las llegadas tardías,
reruns, workflows manuales y `push` no son prueba de ejecución de la cadencia
de host, de `InvocationID`, de fuente PPI fresca ni de readiness PAPER.

## Límites y guard

La defensa es exclusivamente un clasificador off-host; no programa, arranca
ni recupera tareas omitidas. No cambia A358 ni PPI Watch, no conecta al
droplet y no realiza GET a proveedores, escrituras SQLite ni órdenes.
El test `test_late_schedule_burst_does_not_erase_42_prior_mature_gaps`
reproduce las cuentas del 04-Nov con un **fixture sintético**, distinguiendo
evidencia histórica de ventana actual. Los IDs reales del incidente constan
en el issue canónico #732 A652-R10/R11; un test GREEN no certifica runtime.

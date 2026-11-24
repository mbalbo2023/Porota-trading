# Scheduler instalado por Porota RC6

El [inventario de runtime del 24/11 a las 17:04:05 ART](https://github.com/mbalbo2023/Porota-trading/issues/1259#issuecomment-7143249175) detalla los 32 timers del release posterior, incluida la falla `200/CHDIR` del servicio de salud IOL y los dos resultados sin acreditar. El [RCA de la rueda](RCA_RUEDA_2026-11-24.md#motores-timers-sqlite-fuentes-y-performance) cruza el exportador, las condiciones de systemd y la cadencia de GitHub. Los estados y horarios de la tabla siguiente corresponden al corte histórico del 22/11.

Captura 2026-11-22T19:57:50.799506-03:00 (ART) · 2026-11-22T22:57:50.799506+00:00 (UTC) · release `223725e2860b295533d480d6ad24aee46f098b8e`.

32 unidades · 16 activas · 11 deshabilitadas · 5 enmascaradas.

| Timer | Estado | Trabajo y destino | Programación visible | Última ejecución ART | Resultado |
| --- | --- | --- | --- | --- | --- |
| `porota-1016-github-verify.timer` | inactive / disabled | Verificación puntual histórica de GitHub. La finalidad está en el nombre y la fecha; cuerpo del script fuera del árbol auditado. Servicio `porota-1016-github-verify.service`. | OnCalendar=2026-09-14 10:16:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |
| `porota-a3-history-daily-rc6.timer` | inactive / masked | Importación incremental A3/CEM en el perfil DAILY_INCREMENTAL del servicio plantilla. Servicio `porota-a3-history-daily-rc6.service`. | — | — | sin ejecución acreditada |
| `porota-a3-history-reconcile-rc6.timer` | inactive / masked | Conciliación A3/CEM en el perfil RECONCILE del servicio plantilla. Servicio `porota-a3-history-reconcile-rc6.service`. | — | — | sin ejecución acreditada |
| `porota-a3-history-weekend-rc6.timer` | inactive / masked | Historia A3/CEM acotada de fin de semana en el perfil WEEKEND_DEEP. Servicio `porota-a3-history-weekend-rc6.service`. | — | — | sin ejecución acreditada |
| `porota-byma-morning-watch-rc6.timer` | active / enabled | Consulta fuentes públicas BYMA y prepara señal de subastas/autoridad para preopen. Servicio `porota-byma-morning-watch-rc6.service`. | OnCalendar=Mon..Fri *-*-* 08:30:00 America/Argentina/Buenos_Aires | 20/11 08:30:05 | success (0) |
| `porota-candle-integrity-rc6.timer` | active / enabled | Audita cobertura, frescura e integridad de velas en modo de observación. Servicio `porota-candle-integrity-rc6.service`. | OnCalendar=Mon..Fri *-*-* 10:35:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 11..16:05,35:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 17:05:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 23:45:00 America/Argentina/Buenos_Aires | 20/11 23:45:01 | success (0) |
| `porota-contract-evidence-hf6.timer` | inactive / disabled | Publicador HF6 de evidencia de catálogo, mantenido como legado. Servicio `porota-contract-evidence-hf6.service`. | OnCalendar=*-*-* 20:10:00 UTC | — | sin ejecución acreditada |
| `porota-contract-evidence-rc6.timer` | inactive / disabled | Lote legado matinal de evidencia contractual RC6 y selección de rueda. Servicio `porota-contract-evidence-rc6.service`. | OnCalendar=Mon..Fri *-*-* 09:15,20,25,30,35,40,45,50,55:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |
| `porota-contract-evidence-weekend-backfill-rc6.timer` | inactive / disabled | Backfill contractual legado en una ventana puntual. Servicio no acreditado en esta captura. Servicio `porota-contract-evidence-weekend-backfill-rc6.service`. | OnCalendar=2026-09-12 04:15:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |
| `porota-fast-functional-health-rc6.timer` | active / enabled | Verifica Observer, guard PAPER, DB de solo lectura y señales de salud. Servicio `porota-fast-functional-health-rc6.service`. | OnBootSec=3min · OnUnitActiveSec=5min | 22/11 19:54:15 | success (0) |
| `porota-full-db-integrity-rc6.timer` | active / enabled | Ejecuta el control integral programado de SQLite; su periodicidad evita convertirlo en healthcheck ligero. Servicio `porota-full-db-integrity-rc6.service`. | OnCalendar=Mon..Fri *-*-* 23:20:00 America/Argentina/Buenos_Aires | 20/11 23:21:00 | success (0) |
| `porota-history-postclose-rc6.timer` | inactive / masked | Reparación histórica posterior al cierre del proveedor; flujo distinto del EOD local. Servicio `porota-history-postclose-rc6.service`. | — | — | sin ejecución acreditada |
| `porota-host-general-backup-rc6.timer` | inactive / masked | Perfil de copia general del host sujeto a política operativa. Servicio `porota-host-general-backup-rc6.service`. | — | — | sin ejecución acreditada |
| `porota-introspeccion-hf4.timer` | inactive / disabled | Introspección histórica HF4 del estado operacional. Servicio `porota-introspeccion-hf4.service`. | OnCalendar=*-*-* *:15:00 | — | sin ejecución acreditada |
| `porota-introspection-publish-rc6.timer` | active / enabled | Publica por GitHub el reporte de introspección sanitizado. Servicio `porota-introspection-publish-rc6.service`. | OnCalendar=*-*-* *:42:00 | 22/11 19:42:15 | success (0) |
| `porota-introspection-rc6.timer` | active / enabled | Genera introspección de runtime y encola alertas clasificadas. Servicio `porota-introspection-rc6.service`. | OnCalendar=*-*-* *:35:00 | 22/11 19:35:04 | success (0) |
| `porota-iol-oauth-health-rc6.timer` | active / enabled | Comprueba la sesión OAuth de IOL y registra salud, renovación o reautenticación requerida. Servicio `porota-iol-oauth-health-rc6.service`. | OnBootSec=2min · OnUnitActiveSec=10min · OnCalendar=Mon..Fri *-*-* 09:30:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 10:20:00 America/Argentina/Buenos_Aires | 22/11 19:48:23 | exit-code (2) |
| `porota-iol-shadow-collector-rc6.timer` | active / enabled | Recoge referencias IOL complementarias, únicamente para observación SHADOW. Servicio `porota-iol-shadow-collector-rc6.service`. | OnCalendar=Mon..Fri *-*-* 10:30..59:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 11..16:*:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 17:*:00 America/Argentina/Buenos_Aires | 20/11 17:59:04 | success (0) |
| `porota-live-decision-cockpit-rc6.timer` | inactive / disabled | Recolector legado del cockpit de decisiones y observación. Servicio `porota-live-decision-cockpit-rc6.service`. | OnCalendar=Mon..Fri *-*-* 10:30..59:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |
| `porota-local-eod-history-rc6.timer` | active / enabled | Drena el delta de la base operativa hacia EOD local mediante candado e importación idempotente. Servicio `porota-local-eod-history-rc6.service`. | OnCalendar=Mon..Fri *-*-* 17:15/5:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 18..22:00/5:00 America/Argentina/Buenos_Aires | 20/11 22:55:16 | success (0) |
| `porota-log-export-hf6.timer` | inactive / disabled | Exportación de logs de soporte HF6. Conservación histórica. Servicio `porota-log-export-hf6.service`. | OnBootUSec=2min | — | sin ejecución acreditada |
| `porota-market-watch-silence-rc6.timer` | active / enabled | Verifica silencios y frescura de ingesta en ventana de mercado. Servicio `porota-market-watch-silence-rc6.service`. | OnCalendar=Mon..Fri *-*-* 11..16:03,13,23,33,43,53:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 17:03,13:00 America/Argentina/Buenos_Aires | 20/11 17:13:04 | success (0) |
| `porota-ppi-argentina-nightly-rc6.timer` | inactive / disabled | Ingesta nocturna PPI Argentina de la etapa anterior; legado deshabilitado. Servicio `porota-ppi-argentina-nightly-rc6.service`. | OnCalendar=*-*-* 02:30:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |
| `porota-preopen-rc6.timer` | active / enabled | Evalúa T-45 y T-10 y publica el gate de apertura PAPER ligado al release. Servicio `porota-preopen-rc6.service`. | OnCalendar=Mon..Fri *-*-* 09:45:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 10:20:00 America/Argentina/Buenos_Aires | 20/11 10:20:15 | exit-code (2) |
| `porota-preopen.timer` | inactive / disabled | Preopen legado separado de RC6 productivo. Servicio no acreditado. Servicio `porota-preopen.service`. | OnCalendar=Mon..Fri *-*-* 10:40:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |
| `porota-rc6-candle-inmarket-verify.timer` | active / enabled | Verifica progresión intrarrueda de velas con la evidencia del Observer. Servicio `porota-rc6-candle-inmarket-verify.service`. | OnCalendar=Mon..Fri *-*-* 10:40,50:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 11..16:00/10:00 America/Argentina/Buenos_Aires | 20/11 16:50:00 | success (0) |
| `porota-runtime-evidence-rc6.timer` | active / enabled | Publica identidad, estados y observación por instrumento con metadatos sanitizados. Servicio `porota-runtime-evidence-rc6.service`. | OnCalendar=*-*-* *:50:00 | 22/11 19:50:24 | success (0) |
| `porota-scheduler-export-hf6.timer` | inactive / disabled | Exportador histórico del Scheduler HF6, reemplazado por RC6. Servicio `porota-scheduler-export-hf6.service`. | OnBootUSec=2min | — | sin ejecución acreditada |
| `porota-scheduler-export-rc6.timer` | active / enabled | Lee systemd con su candado, publica JSON atómico y alimenta el dashboard. Servicio `porota-scheduler-export-rc6.service`. | OnBootSec=2min · OnUnitActiveSec=5min | 22/11 19:54:15 | success (0) |
| `porota-snapshot-postclose-rc6.timer` | active / enabled | Captura postcierre, Action 4, evidencia de decisiones y publicación de la campaña M0–M11. Servicio `porota-snapshot-postclose-rc6.service`. | OnCalendar=Mon..Fri *-*-* 17:08:00 America/Argentina/Buenos_Aires · OnCalendar=Mon..Fri *-*-* 18:08:00 America/Argentina/Buenos_Aires | 20/11 18:08:05 | success (0) |
| `porota-snapshot-preopen-rc6.timer` | active / enabled | Captura el corte previo a la rueda para auditoría del panel. Servicio `porota-snapshot-preopen-rc6.service`. | OnCalendar=Mon..Fri *-*-* 10:10:00 America/Argentina/Buenos_Aires | 20/11 10:10:24 | success (0) |
| `porota-weekend-ingestion-audit-rc6.timer` | inactive / disabled | Auditoría histórica puntual del fin de semana; ejecución en fecha fija. Servicio `porota-weekend-ingestion-audit-rc6.service`. | OnCalendar=2026-09-12 08:00:00 America/Argentina/Buenos_Aires | — | sin ejecución acreditada |

## Solapamientos

Se pudieron comparar 5 últimos intervalos del día. Estado del journal `UNAVAILABLE`.
- porota-fast-functional-health-rc6.timer y porota-scheduler-export-rc6.timer se cruzaron 2 segundos desde 2026-11-22T19:54:15-03:00.

**Calendario nominal 2026-11-22.** 72 disparos calendarizados y 0 pares con coincidencia exacta.


**Calendario nominal 2026-11-24.** 691 disparos calendarizados y 13 pares con coincidencia exacta.

- `porota-candle-integrity-rc6.timer` y `porota-introspection-rc6.timer` · 7 ocasiones · primera a las 10:35:00 ART.
- `porota-candle-integrity-rc6.timer` y `porota-iol-shadow-collector-rc6.timer` · 14 ocasiones · primera a las 10:35:00 ART.
- `porota-introspection-publish-rc6.timer` y `porota-iol-shadow-collector-rc6.timer` · 8 ocasiones · primera a las 10:42:00 ART.
- `porota-introspection-rc6.timer` y `porota-iol-shadow-collector-rc6.timer` · 8 ocasiones · primera a las 10:35:00 ART.
- `porota-introspection-rc6.timer` y `porota-local-eod-history-rc6.timer` · 6 ocasiones · primera a las 17:35:00 ART.
- `porota-iol-oauth-health-rc6.timer` y `porota-preopen-rc6.timer` · 1 ocasiones · primera a las 10:20:00 ART.
- `porota-iol-shadow-collector-rc6.timer` y `porota-local-eod-history-rc6.timer` · 9 ocasiones · primera a las 17:15:00 ART.
- `porota-iol-shadow-collector-rc6.timer` y `porota-market-watch-silence-rc6.timer` · 38 ocasiones · primera a las 11:03:00 ART.
- `porota-iol-shadow-collector-rc6.timer` y `porota-rc6-candle-inmarket-verify.timer` · 38 ocasiones · primera a las 10:40:00 ART.
- `porota-iol-shadow-collector-rc6.timer` y `porota-runtime-evidence-rc6.timer` · 8 ocasiones · primera a las 10:50:00 ART.
- `porota-iol-shadow-collector-rc6.timer` y `porota-snapshot-postclose-rc6.timer` · 1 ocasiones · primera a las 17:08:00 ART.
- `porota-local-eod-history-rc6.timer` y `porota-runtime-evidence-rc6.timer` · 6 ocasiones · primera a las 17:50:00 ART.
- `porota-rc6-candle-inmarket-verify.timer` y `porota-runtime-evidence-rc6.timer` · 7 ocasiones · primera a las 10:50:00 ART.

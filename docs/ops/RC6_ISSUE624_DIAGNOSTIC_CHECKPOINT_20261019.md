# RC6 #624 — checkpoint de la sonda diagnóstica, 19/10/2026

Checkpoint ID: RC6-624-READONLY-DISK-MAP-20261019

## Alcance

Este archivo registra el PR diagnóstico [#645](https://github.com/mbalbo2023/Porota-trading/pull/645), rama `diagnostic/rc6-624-safe-disk-map-20261019`, basado en `main` `5a87538d3816601a4fddf4aaafc975b4ebc674d7`. No forma parte del candidato productivo [#646](https://github.com/mbalbo2023/Porota-trading/pull/646), SHA `133dde467c3e47f6598d42fe0222b0448089e353`, ni propone fusionarse. HEAD productivo sigue `d52e45fe85ed184a76d35120ca543f5d63f38b2c`.

La sonda en `.github/workflows/rc6-624-637-failed-disk-cleanup.yml` es READ_ONLY y limitada. Comprueba identidad productiva exacta, PAPER/0, sesión CLOSED real, ausencia de posiciones, IDs de contenedores, memoria >128 MiB, PSI I/O some avg10 <=5 y reserva de disco antes del recorrido profundo. Sólo tras admitir esas puertas puede hacer una pasada de metadatos y `du` limitado; no contiene código de borrado. Con una puerta roja se detiene antes del recorrido y no se declara mapa completo.

## Evidencia y resultado

- [Run 39333168267](https://github.com/mbalbo2023/Porota-trading/actions/runs/39333168267), head `e83295f1f59d13e59e55cfbcf139f3bf33b16139`, falló cerrado a las 22:11:42 UTC. Estado observado: `MARKET_CLOSED/WAITING_MARKET/PRODUCTION_PAPER`, `real_orders_sent=0` entero y heartbeat 4.32 s.
- `paper_positions OPEN=1`, futuros ACTIVE=0, cauciones OPEN=0. Libre 4,354,555,904 B; inodos libres 89.52%; `MemAvailable=124,641,280` B por debajo de 134,217,728; PSI I/O some avg10=12.35 por encima de 5. Hay tres puertas independientes de NO_GO: posición, RAM y presión I/O.
- El ZIP de inventario 12224779247 quedó vacío: no demuestra ocupación por ruta. Recuperado 0 B. El `FileNotFoundError` al abrir un lock preexistente fue contexto del traceback; la aserción de posición abierta detuvo `snapshot()` antes de recorrer datos.
- El piso del único artifact OFFHOST certificado para PR646 es 4,031,519,848 B ([Predeploy 39330909711](https://github.com/mbalbo2023/Porota-trading/actions/runs/39330909711)); la muestra de disco deja un margen puntual de 323,036,056 B, que debe revalidarse al momento de cualquier transferencia futura. No equivale a host admitido.
- Dos imágenes activas y ocho caches activos, backups keep3: no hay huérfano nuevo demostrado. El barrido profundo de `/data` sigue NO_VERIFICADO. No se justifica repetirlo bajo las mismas puertas rojas.

## Seguridad y continuidad

`PRODUCTION_PAPER`, `REAL_ORDERS_SENT=0`, `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`, SHADOW fail-closed. PPI Watch intocado. No Deploy, cuarentena productiva, limpieza, escritura en DB, reinicio ni cambio de imagen. PR645 permanece DRAFT/NO_MERGE. Sólo una admisión futura auténtica con cero posiciones y recursos verdes justificaría un inventario profundo; esa ejecución no está programada aquí. La misión y matriz causal integradas están en [#624](https://github.com/mbalbo2023/Porota-trading/issues/624#issuecomment-6278663653).

Este checkpoint describe evidencia existente; la mera aprobación del check de metadatos no valida el Droplet ni autoriza promoción.

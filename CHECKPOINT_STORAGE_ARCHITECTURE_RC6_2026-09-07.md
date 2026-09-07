# POROTA TRADING — CHECKPOINT STORAGE ARCHITECTURE RC6 — 2026-09-07

## Estado

Checkpoint arquitectónico abierto. No autoriza borrado ni transformación de datos canónicos.

## Decisión operativa confirmada

### Rollback

- El rollback canónico NO debe depender de una copia local en el Droplet.
- Los artefactos/código necesarios para rollback deben recuperarse desde GitHub.
- No se debe conservar una imagen Docker local o una copia `predeploy` únicamente por función de rollback.
- Antes de cada despliegue debe existir identidad inequívoca de commit/tag/release recuperable desde GitHub.
- El procedimiento de rollback debe reconstruir/descargar desde GitHub y ejecutar preflight + activación transaccional + postflight.
- Los backups generales de datos son una política distinta al rollback de aplicación y no deben confundirse con éste.

## Resultado de limpieza autorizada 2026-09-07

La limpieza de residuos fue ejecutada mediante GitHub Actions + SSH estricto, con gates SQLite y de seguridad antes/después.

Estado filesystem:

- antes: 24 GB, 15 GB usados, 8.7 GB libres, 63% usado;
- después: 24 GB, ~11 GB usados, ~13 GB libres, 48% usado;
- recuperación aproximada observada: ~4.3 GB;
- objetivo `<65%` restablecido con margen amplio.

Acciones ejecutadas:

- eliminada copia local RC5 predeploy/rollback, de acuerdo con política GitHub-only;
- conservado únicamente el backup general host más reciente y verificado; generaciones anteriores retiradas;
- eliminado instalador `.deb` de Chrome ya instalado, preservando `chrome-profile` y venv;
- limpiado cache APT;
- journal reducido a ~146 MB;
- eliminadas imágenes antiguas del dashboard que no estaban activas ni referenciadas;
- preservadas imágenes activas y `porota-iol-history-shadow`;
- builder cache quedó en 0 B;
- podada únicamente metadata Git de worktrees inválidos; no se eliminaron directorios de backup/worktree en este paso.

Postflight:

- `observer_v17.db`: `PRAGMA quick_check=ok`;
- `market_history.db`: `PRAGMA quick_check=ok`;
- `real_orders_sent=0`;
- observer: running, restart=0;
- dashboard: running, healthy, restart=0;
- `historical_raw_archive`: no modificado;
- `observer_v17.db`: no VACUUM;
- `market_history.db`: no modificado por cleanup.

Evidencia canónica de este paso:

- `POROTA_DISK_AUDIT_RC6_2026-09-07.txt` en rama de auditoría;
- `POROTA_DISK_DEEP_AUDIT_RC6_2026-09-07.txt` en rama de auditoría;
- `POROTA_DISK_POSTCLEANUP_PROOF_RC6_2026-09-07.txt` en rama `cleanup/rc6-disk-safe-20260907`.

## Hallazgo arquitectónico principal de almacenamiento

La auditoría de disco del 2026-09-07 encontró que `data/paper_v17/observer_v17.db` ocupa aproximadamente 1.4 GiB y que el objeto SQLite `historical_raw_archive` explica aproximadamente 1.266 GB del archivo.

Esto requiere análisis porque la arquitectura HF6/RC6 ya define un History Store separado mediante `HIST_DB_PATH` / `market_history.db`, y el objetivo de storage lifecycle indica que los históricos deben concentrarse en representación normalizada, evitando crecimiento indefinido de payload raw dentro del hot path.

## Regla hasta resolver el checkpoint

PROHIBIDO durante limpieza operativa:

- borrar filas de `historical_raw_archive`;
- ejecutar `VACUUM` sobre `observer_v17.db`;
- mover tablas o modificar esquema del observer;
- truncar WAL/SHM manualmente;
- comprimir la DB SQLite activa;
- deduplicar raw sin demostrar persistencia equivalente de evidencia normalizada/hash/versionado.

`observer_v17.db`, `market_history.db`, WAL/SHM activos, ledger PAPER, posiciones, settlement, risk state y Contract Evidence permanecen protegidos.

## Auditoría arquitectónica requerida

Antes de cualquier migración se debe responder con evidencia:

1. Origen exacto de `historical_raw_archive` y todos los writers que todavía insertan en ella.
2. Cantidad de filas, bytes aproximados y crecimiento 24h/7d.
3. Distribución por fuente, familia financiera, instrumento, fecha y tipo de payload.
4. Existencia de duplicados exactos y repetición por hash.
5. Correspondencia de cada raw con `history_versions_v2`, `history_canonical_v2`, legacy `market_historical_ohlcv` y/o Contract Evidence.
6. Qué raw constituye evidencia única y qué raw es reconstructible/redundante.
7. Retención requerida por categoría: HOT / WARM / COLD.
8. Diseño para impedir nuevas escrituras raw redundantes dentro de `observer_v17.db`.
9. Plan de migración reversible hacia storage histórico dedicado/frío.
10. Gate de integridad antes y después: `PRAGMA quick_check`, conteos, hashes, coverage, freshness, replay/backtest y health runtime.
11. Estrategia de compactación posterior sólo en ventana segura y únicamente si hay espacio recuperable demostrado.
12. Política de backups posterior para evitar que el crecimiento raw multiplique el tamaño de copias diarias.

## Diseño objetivo a evaluar

- `observer_v17.db`: sólo estado operacional/hot path, ledger, decisiones necesarias, riesgo, posiciones, settlement y evidencia operativa imprescindible.
- `market_history.db` / History Store: históricos normalizados con identidad financiera completa y versionado controlado.
- raw histórico: almacenamiento COLD separado, deduplicado por hash y comprimible según retención; nunca crecimiento ilimitado en la DB operacional.
- Contract Evidence: preservar evidencia contractual normalizada + hash; raw repetitivo sujeto a lifecycle después de demostrar equivalencia.

## Métricas obligatorias futuras

Registrar diariamente:

- filesystem usado/libre;
- tamaño `observer_v17.db`, WAL y SHM;
- tamaño `market_history.db`;
- bytes/filas de `historical_raw_archive`;
- crecimiento 24h y 7d;
- backups PAPER y History Store;
- Docker images/build cache;
- journal/logs;
- estimación de días hasta 65%, 75% y 85% del filesystem.

## Hallazgo operacional separado — no atribuido a cleanup

El post-cleanup inventory observó un tercer contenedor, `porota_critical_approval_rc6`, en estado `unhealthy` y ejecutándose con la misma imagen activa `porota-trading-bot:17.0.0-rc6`.

La limpieza NO eliminó ni reemplazó esa imagen y no reinició este contenedor. El observer y dashboard permanecieron correctos. Este estado debe auditarse como checkpoint operacional separado antes de considerarlo resuelto.

## Estado del checkpoint

`OPEN_ARCHITECTURE_REVIEW`

La limpieza de residuos quedó completada. Este checkpoint sólo se cierra con auditoría de writers, equivalencia de datos y plan reversible aprobado.
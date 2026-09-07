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

## Estado del checkpoint

`OPEN_ARCHITECTURE_REVIEW`

La limpieza segura de residuos puede continuar en paralelo, pero este checkpoint sólo se cierra con auditoría de writers, equivalencia de datos y plan reversible aprobado.
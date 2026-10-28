# Evidencia de dataset PAPER v17 — 2026-08-29

## Resultado

El dataset PAPER v17 fue verificado como nuevo, independiente y sin datos operativos heredados. La verificación y la limpieza posterior se realizaron con los motores detenidos y sin habilitar promoción ni producción real.

Estado al cerrar esta evidencia:

- namespace: `POROTA_PAPER_V17_FRESH`
- dataset_id: `ff96a909-e3ed-4012-8dbb-f4d816fcd472`
- origin: `FRESH_EMPTY`
- imported_legacy: `0`
- capital inicial: ARS 1.000.000; USD, USD_CCL y USD_MEP en cero
- SHA-256 de `observer_v17.db`: `6edbf1948b9ff2abf4ca3f0ca9990369368e62b1bbdd2554d5675cfb67223bbc`
- `PRAGMA quick_check`: `ok`
- órdenes enviadas durante los diagnósticos: `0`
- acceso de red durante la auditoría inmutable: `none`
- credenciales y archivo de entorno montados: `false`
- datos legacy montados o tocados: `false`

## Tablas operativas de arranque

Todas estas tablas tenían cero filas:

- `candle_versions`
- `market_snapshots`
- `paper_cauciones`
- `paper_decisions`
- `paper_fills`
- `paper_learning_samples`
- `paper_positions`
- `paper_sale_receivables`

Los estados persistidos de observer, supervisor, exit reader, notifications y candles estaban en `STOPPED`.

## Auditoría inmutable

La base se abrió exclusivamente con:

```text
mode=ro&immutable=1
PRAGMA query_only=ON
```

El SHA-256 fue idéntico antes y después. La auditoría utilizó un contenedor temporal sin red, sin credenciales y sin montar datos legacy; el contenedor temporal fue retirado al terminar.

## Auxiliares huérfanos detectados

La revisión de artefactos encontró dos archivos de una prueba de restauración:

- `.restore_test_13.db-wal` — 0 bytes
- `.restore_test_13.db-shm` — 32.768 bytes

No pertenecían a la base PAPER activa. La causa estaba en `create_backup`: el archivo temporal principal se eliminaba, pero no siempre su bundle SQLite `-wal`/`-shm`.

La corrección quedó incorporada en:

- implementación: `bd35dfacb3768932a4e47c5fd4daa80dbcc434f2`
- pruebas: `cc2a1521e1fba85c0cf8931940cd9885a4692413`

Las pruebas cubren tanto la eliminación directa del bundle SQLite como la ausencia de `.restore_test_*.db*` y auxiliares del backup después del flujo completo.

## CI de la corrección

El workflow de GitHub Actions #238 sobre `cc2a1521e1fba85c0cf8931940cd9885a4692413` terminó correctamente:

- escaneo de credenciales y secretos
- sintaxis
- suite completa
- cobertura financiera mínima
- cobertura global: 69,03 %
- construcción de imagen
- ausencia de archivos de entorno dentro de la imagen
- arranque y apagado del stack de prueba
- panel y seguridad de `/health`

## Limpieza controlada

El diagnóstico `v17-cleanup-restore-aux-1` eliminó exclusivamente los dos auxiliares huérfanos. No abrió ni modificó SQLite, no tocó el backup comprimido ni los reportes y mantuvo los motores detenidos.

El hash de `observer_v17.db` permaneció exactamente igual antes y después:

```text
6edbf1948b9ff2abf4ca3f0ca9990369368e62b1bbdd2554d5675cfb67223bbc
```

## Candidata siguiente

La rama declara `17.0.0-rc2` para distinguir la candidata que contiene la corrección de limpieza de la imagen `rc1` previamente construida. Este cambio de etiqueta no implica despliegue.

Antes de cualquier canario se exige:

1. CI verde para el head que declara `rc2`.
2. checkout limpio y exacto de ese commit en el Droplet.
3. construcción de una nueva imagen `porota-trading-bot:17.0.0-rc2`.
4. smoke offline sin red, credenciales ni datos legacy.
5. confirmación explícita de motores detenidos y `orders_sent=0`.

## Portones vigentes

Esta evidencia no autoriza merge, despliegue, arranque de motores, acceso operativo a PPI ni promoción a producción real. La PR #3 continúa en borrador hasta completar los portones posteriores.

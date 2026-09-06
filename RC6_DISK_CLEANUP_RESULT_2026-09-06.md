# POROTA TRADING — RC6 CONTROLLED DISK CLEANUP RESULT

**Fecha:** 2026-09-06  
**Workflow run:** `34065925326`  
**Ops branch:** `ops/rc6-disk-cleanup-20260906`  
**Workflow commit:** `3a6b18069c2caee2d8115fb3c979936ef1530bfb`  
**Live branch verificada:** `hotfix/rc6-cedear-us-labor-day-20260906`  
**Live SHA verificado:** `db26c76723bb988c956589c572b87cbcb4191731`

## Política aplicada

La recuperación de aplicación se basa en GitHub/GitHub Actions por SHA exacto. No se conserva una imagen Docker local antigua sólo como rollback de aplicación.

No se ejecutó `docker system prune -a`, no se tocaron volúmenes, backups de datos ni el perfil browser/Contract Evidence.

## Preflight

Antes de borrar se verificó:

- observer running;
- dashboard running;
- restart count 0 en ambos;
- observer root filesystem read-only;
- imagen activa `porota-trading-bot:17.0.0-rc6`;
- label de commit de la imagen activa = `db26c76723bb988c956589c572b87cbcb4191731`;
- DB observer `PRAGMA quick_check = ok`;
- DB history `PRAGMA quick_check = ok`;
- modo `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- `/health` OK;
- ningún contenedor referenciaba las imágenes antiguas seleccionadas;
- A3 y Contract Evidence no estaban ejecutando jobs pesados activos.

## Candidatos eliminados

Sólo residuos Docker previamente identificados:

1. `porota-trading-bot:17.0.0-rc6-prehotfix-5bdad270c2a2`
   - image id: `sha256:76006b385d77274f8fd1ebd21a28559271306deaeeae23189a62e7153a495159`
   - contenedores referenciándolo: ninguno.
2. `porota-trading-bot:17.0.0-rc5`
   - image id: `sha256:bc3603df37d0fb277a89a7ae82803aa89e766ce4fb31cc7050de7a7594b0f766`
   - contenedores referenciándolo: ninguno.
3. Build cache Docker clasificado como no requerido por runtime activo.

La imagen activa no se eliminó ni se retagueó.

## Resultado de capacidad

Antes:

- filesystem `/`: 24 GB;
- usado: 14 GB;
- disponible: 9.4 GB;
- uso: 60%;
- imágenes Docker: 3;
- build cache: 2.692 GB.

Después:

- filesystem `/`: 24 GB;
- usado: 11 GB;
- disponible: 13 GB;
- uso: 47%;
- imágenes Docker: 1, únicamente la activa;
- build cache: 0 B.

**Espacio recuperado medido:** `3,258,228,736 bytes` (~3.03 GiB / ~3.26 GB decimal).

## Postflight

Después de la limpieza:

- observer = running, restart=0, readonly=true;
- dashboard = running, restart=0;
- imagen activa conservó exactamente el mismo image id `sha256:daae0ff8d6ec067efde48357b8dd6489046ed59efc231d8b15a6c03a44bd7292`;
- label de commit activo = `db26c76723bb988c956589c572b87cbcb4191731`;
- DB observer quick_check = ok;
- DB history quick_check = ok;
- modo = `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- health = OK.

## Elementos deliberadamente NO tocados

- `/opt/porota-trading/data/backups`;
- perfil browser/Playwright y sesión autenticada de Contract Evidence;
- volúmenes Docker;
- logs/journal;
- históricos/candles/databases;
- archivos untracked del repo;
- configuración/runtime;
- timers/systemd.

## Estado

`RC6_DISK_CLEANUP=GREEN`

El Droplet queda con margen de disco holgado para el Go Live PAPER condicionado al preopen de mañana. La limpieza de backups de datos, raw históricos, logs o artefactos adicionales queda separada y requiere clasificación específica; no se debe confundir con rollback de aplicación.

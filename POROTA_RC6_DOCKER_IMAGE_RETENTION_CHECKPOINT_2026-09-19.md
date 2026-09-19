# Checkpoint RC6: retención y limpieza de imágenes Docker

**Checkpoint ID:** POROTA-RC6-DOCKER-RETENTION-20260919-01  
**Fecha:** 2026-09-19 (UTC; Argentina UTC−3)  
**Repositorio:** `mbalbo2023/Porota-trading`  
**Rama productiva:** `deploy/rc6-pr69-isolated-20260915`  
**SHA desplegado:** `21ad724a403e412d5774e574e33bdee01fcac348`  
**Baseline anterior:** `07544d3dbe9f4479fa27946205b3076db733b19b`

## Resultado

La acumulación de candidatas y etiquetas de rollback quedó corregida en el workflow canónico y se ejecutó una limpieza acotada durante el despliegue autorizado.

- **PR de runtime:** [#112](https://github.com/mbalbo2023/Porota-trading/pull/112), integrado.
- **Run canónico:** [35463990653](https://github.com/mbalbo2023/Porota-trading/actions/runs/35463990653), **SUCCESS**.
- **Workflow:** `.github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml`.
- **Pruebas del candidato:** preflight [35463884955](https://github.com/mbalbo2023/Porota-trading/actions/runs/35463884955) y suite [35463884993](https://github.com/mbalbo2023/Porota-trading/actions/runs/35463884993), ambas **SUCCESS**.
- Una ejecución previa de preflight falló porque una prueba aún exigía `STORAGE_CLEANUP=NOT_EXECUTED`. Se actualizó la prueba para verificar el nuevo límite de limpieza; la repetición aprobó.

## Causa y cambio

Cada despliegue creaba una referencia `candidate-<SHA>` y etiquetaba la imagen estable previa como `rollback-<SHA>`. No se retiraban las referencias antiguas. Una ejecución podía terminar roja en la verificación posterior y aun así haber construido y etiquetado su candidata.

El workflow ahora:

1. Ya no crea una imagen/etiqueta de rollback por despliegue, de acuerdo con la preferencia vigente del usuario.
2. Ejecuta una limpieza acotada mediante un trap de salida dentro del script remoto, incluso si una etapa posterior del deploy falla.
3. Solo examina referencias con SHA completo que coincidan exactamente con `porota-trading-bot:17.0.0-rc6-candidate-<40 hex>` o `...-rollback-<40 hex>`.
4. Conserva la etiqueta estable `porota-trading-bot:17.0.0-rc6`, la candidata del SHA actual y toda imagen referenciada por cualquier contenedor activo o detenido.
5. Falla cerrado si no puede inventariar los contenedores o imágenes. No fuerza eliminaciones ni usa `docker image prune`, `docker system prune` o `docker builder prune`.

## Evidencia de limpieza

En la misma ejecución, el inventario al entrar al trap (después de construir la candidata nueva) y el posterior fueron:

| Medición Docker | Antes | Después |
|---|---:|---:|
| Imágenes | 90 | 11 |
| Contabilización de imágenes | 8.735 GB | 5.19 GB |
| Reclamable informado por Docker | 564 MB | 117 MB |

- Se retiraron **161 referencias** antiguas de candidatas/rollback.
- Solo quedó registrada como conservada la candidata del SHA actual; no quedaron referencias antiguas protegidas por contenedores.
- Los 8.735 GB ya incluyen la imagen nueva construida en esta ejecución. La caída de 3.545 GB es la contabilidad Docker; no equivale directamente a bytes físicos porque las imágenes comparten capas.

La filesystem audit posterior informó `/dev/vda1: 24G total, 17G usado, 6.3G disponible, 74%`. La última medición exacta previa, después de la limpieza de backups, fue run 35460915038: 24,883,167,232 B total, 19,178,954,752 B usados, 5,687,435,264 B libres, 78%. Comparando `df -h` redondeado, el espacio libre subió aproximadamente 1 GiB y el uso bajó 4 puntos. **Bytes exactos posteriores: NOT_VERIFIED**; la ejecución actual solo imprimió `df -h`.

## Estado runtime y seguridad

La verificación posterior del mismo run confirmó:

- `POST_STATE=ok|PRODUCTION_PAPER|0`
- `REAL_ORDERS_SENT=0`
- `REAL_ORDER_ROUTES=NOT_CALLED`
- `RC6_UNIFIED_PAPER_DEPLOY=GREEN`
- `GO_CHECK=GREEN`; PPI auth `OK`, sesión `WAITING_MARKET/MARKET_CLOSED`, heartbeat fresco.

PPI Watch no fue modificado ni reiniciado. El Cockpit privado/puerto 8766 no fue tocado. No se borraron contenedores, cachés, volúmenes, bases de datos, backups ni imágenes de ChromaDB.

## Skill reutilizable

Se creó `porota-docker-image-retention` en `.agents/skills/porota-docker-image-retention/SKILL.md` y se guardó una copia persistente en Library como `SKILL.md`.

- **PR documental:** [#113](https://github.com/mbalbo2023/Porota-trading/pull/113), integrada en `main` mediante el commit `656fe0dc778b4ece098accbd01dafa483908ba9c`; es exclusivamente documental y no dispara el deploy canónico.
- CI documental [35464661918](https://github.com/mbalbo2023/Porota-trading/actions/runs/35464661918) y attestation final [35464692299](https://github.com/mbalbo2023/Porota-trading/actions/runs/35464692299): **SUCCESS**. Los intentos previos de attestation fallaron por campos faltantes; se completó la attestation y la ejecución final aprobó.
- SHA del commit con el skill antes de añadir este checkpoint: `7d82615497a8b46f55b384a0e332f7fbe91164bc`.

## Pendientes exactos

- 🟢 Workflow canónico actualizado e integrado.
- 🟢 Limpieza ejecutada y verificada; imagen estable y seguridad PAPER comprobadas.
- 🟢 PR documental #113 integrada; skill y checkpoint ya están en `main`. Attestation final y CI documental aprobaron.
- ⚪ Obtener `df -PB1` posterior en la siguiente auditoría read-only para fijar bytes exactos; no es un stopper y no repetir limpieza Docker para obtenerlo.

## Instrucción para retomar

Leer este checkpoint y el skill `.agents/skills/porota-docker-image-retention/SKILL.md`. La PR #113 está integrada. No repetir la limpieza completada. Mantener las imágenes de contenedores en uso y seguir usando exclusivamente el workflow canónico para futuros deploys RC6.

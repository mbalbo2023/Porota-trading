# A681 — Deploy V2 y housekeeping en host Docker compartido

**Fecha:** 2026-11-05 ART. **Estado:** DESARROLLADO/OFFHOST; no significa
DESPLEGADO ni VALIDADO_RUNTIME. Fuente de verdad: SHA + Actions + artifact +
runtime, en ese orden. **No se ejecutaron mutaciones en el droplet.**

## Requerimiento permanente

El droplet alberga Porota Trading RC6, Boca Socios y otros servicios ajenos.
Ningún cleanup del repositorio Porota puede borrar, detener o alterar
contenedores, imágenes, redes, volúmenes, capas, caches, montajes o archivos
que no sean inequívocamente propiedad del intento exacto de Porota. Preservar
PPI Watch, SQLite, logs, artefactos de evidencia, backups y ordenes reales=0.

### RCA del código de partida #945

1. `rc6_disk_housekeeping.py` incluía borrado global por prefijos `porota` y
   `rc6` bajo `/tmp`, `apt-get clean`, eliminación de listas apt y
   `journalctl --vacuum-size`. Esas acciones eran riesgos de otras apps si se
   invocaba `cleanup`/`auto`. El Deploy V2 canónico invocaba solamente
   `--mode audit --preserve-backups`: **no hay evidencia de ejecución dañina**.
   El JSON incluía expresiones cron; no constituyen por sí solas un timer real.
   Estado del cron/runtime externo: **NO_VERIFICADO**.
2. La promoción tenía dos loops ad hoc que recorrían imágenes candidatas por
   nombre y eliminaban IDs de imagen tras reconciliar el crítico; no probaban
   labels OCI de propiedad ni aliases externos. También eliminaba el tag
   frozen directamente. El precancel de `diskguard` repetía la misma clase.
3. El helper de failure cleanup protegía containers detenidos, montajes y
   aliases, pero aún removía IDs de imagen para liberar huérfanos, una clase
   excluida en host compartido. Conservamos todos sus controles previos.
4. El script histórico `ops/rc6_build_cache_safe_cleanup.sh` podía ejecutar
   `docker builder prune -f`. El workflow manual fechado 2026-09-30 tenía
   `image/builder/buildx prune` global y `rm -rf` por prefijo de `/tmp`.
   Su historical materializer de 2026-09-09 podía regenerar el script anterior.

## Guard permanente propuesto en branch aislada

- Housekeeping: `audit` intacto y no destructivo. En `cleanup/auto`, política
  `shared_host` explícita obligatoria; no hay barrido global de `/tmp`, apt,
  journal ni backups. Las candidatas no verificadas se conservan y se informa
  capacidad residual real. Política niega ámbitos globales incluso cuando
  existe espacio insuficiente; no disfraza `RED` de `GREEN`. Los tres helpers
  antiguos que borraban archivos/apt/journal ahora lanzan excepción de
  prohibición **incluso llamados directamente**, para impedir regresiones.
- Candidate Docker: helper único para precarga y postpromoción; inspección de
  **todos** los contenedores (incl. detenidos), tags, digests y labels OCI
  `porota.predeploy=v2` y `porota.commit=SHA exacto` con consistencia entre
  aliases. Solo `docker image rm TAG` sin `--force`, jamás por ID. La build
  canónica `.github/workflows/porota-predeploy-v2.yml` declara ambas etiquetas
  en la acción de construcción inmutable. Falta demostrar que la imagen
  desplegada realmente conserva esas etiquetas: **NO_VERIFICADO**.
- Las imágenes sin labels, con tags/digests compartidos, referenciadas por
  contenedores ajenos o con identidad desconocida **se retienen y alertan**.
- Preparación de stage: no reutilizar/borrar directorio existente. Remoción
  postpromoción de stage y tarballs se restringe a ruta exacta
  `/tmp/porota-deploy-v2-$SHA-$RUN-$ATTEMPT`, sin symlinks ni montajes host/
  Docker. Cleanup final comparte validaciones del helper de falla y deja
  recibos JSON persistentes incluso en anomalías.
- Se neutralizan (`READ_ONLY`) los caminos históricos de limpieza global; el
  materializador histórico no ejecuta nada.
- No hay prune global de containers/images/networks/volumes/build cache; no
  toca Docker de Boca ni de otros proyectos. No cambia puertos/systemd/runtime.
- La limpieza selectiva se intenta después del Deploy V2 exitoso y del fallo;
  un resultado inseguro conserva el recurso, reporta advertencia y mantiene
  la validación de cuota disco separada.

## Evidencia OFFHOST

- `tests/test_rc6_a681_shared_host_policy.py`: tmp extranjero, modo audit,
  fail-closed, ausencia de comandos globales en Deploy V2 e históricos.
- `tests/test_rc6_pretransfer_image_cleanup.py`: labels exactas, tag ajeno
  en mismo ID, Boca y tercer servicio, contenedores detenidos, carreras de
  imagen, digests aliases, origen OCI emitido por Predeploy V2.
- `tests/test_rc6_deploy_failure_cleanup.py`: mounts bind de Boca apuntando
  al stage, refs de contenedores detenidos y huérfanos de otros intentos.
- `tests/test_rc6_disk_deploy_guard.py`,
  `tests/test_rc6_deploy_runtime_completeness.py`,
  `tests/test_rc6_backup_preservation.py` y tests de capacidad/reserva.
- Sin acciones host reales ni prueba de espacio reclaimed en droplet; todo
  resultado de test cubre únicamente regresión OFFHOST.

## Integración / preflight pendiente

1. Reconciliar SHA del DRAFT con #778/#929 y candidate owner; no merge ni
   deploy individual. Verificar que no haya edición concurrente de limpieza.
2. Predeploy V2 de candidato único: invocar test de tres apps, sintaxis de
   shell y fuente de etiquetas OCI, validar artifact/manifest/digest.
3. Antes del único Deploy V2, inventario READ_ONLY de todos los contenedores,
   imágenes y montajes; registrar `docker system df`, espacio libre, inodes,
   memoria disponible; comprobar que servicios y puertos externos se mantienen.
4. Después del deploy, inspeccionar recibos selectivos y comparativas de
   recursos; validar runtime, DB, PAPER/0, tres contenedores de Porota,
   PPI Watch y servicios/puertos de terceros sin interrupciones.
5. No tomar como prueba suficiente un workflow `success`; sin recibos y
   comprobación de Docker real declarar `NO_VERIFICADO`.

### Bloqueos no resueltos por este PR

- No se ha auditado el cron real del droplet para descartar tarea privada
  que ejecute antiguas rutinas de limpieza. Se requiere observación READ_ONLY.
- El proveedor de runtimes systemd/Compose usa nombres específicos Porota;
  la etiqueta de Compose no prueba por sí misma aislamiento de otros servicios.
  Hay que verificar inventario real y unicidad de nombres/puertos antes de
  promoción. El PR no modifica servicios ni sus labels existentes.
- Los envíos a producción, merge y deploy requieren candidato coherente y
  los gates globales de diez familias/PIT/PPI/SQLite; este PR no los sustituye.

**Decisión arquitectónica:** limpieza estrictamente basada en propiedad,
manifiestos inmutables y checks de referencia de todos los consumidores.
Si una identidad es ambigua, **se conserva el recurso** con evidencia y
alerta. Nunca se libera espacio de terceros para hacer pasar un deploy.

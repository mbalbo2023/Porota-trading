# POROTA HF6 v2 - Política de almacenamiento, compresión y limpieza

Documento WIP. Define la política; no ejecuta borrados.

## Objetivo

Preparar el Droplet para crecimiento de históricos, Contract Evidence, aprendizaje y observabilidad sin permitir que datos reconstructibles desplacen a la evidencia canónica ni que Docker consuma el disco indefinidamente.

## Principio de clasificación

Cada dato debe pertenecer a una de estas clases:

### 1. HOT / CANÓNICO

Se conserva sin compresión mientras participa de consultas normales:

- `observer_v17.db` y sus índices;
- ledger PAPER y fills;
- posiciones, settlement receivables y riesgo diario;
- Contract Evidence v2 normalizado/current;
- candles/barras normalizadas necesarias para señales y replay reciente;
- configuración efectiva y manifiestos activos.

Nunca se elimina automáticamente.

### 2. WARM / HISTÓRICO CONSULTABLE

Datos que deben conservarse pero no necesitan ocupar espacio en estructuras repetitivas del hot path:

- snapshots contractuales antiguos;
- barras históricas fuera de la ventana operativa inmediata;
- reportes de introspección;
- auditorías y EOD consolidados;
- learning evidence histórica.

La estrategia preferida es conservar datos normalizados en SQLite con índices mínimos y archivar payloads raw redundantes fuera de las tablas calientes cuando la reconstrucción esté demostrada.

No se comprimirá una base SQLite activa como archivo. Para archivo frío se exportará una copia cerrada/verificada, nunca el DB vivo.

### 3. COLD / EVIDENCIA RAW

- payload JSON original de PPI/BYMA/A3/Data912;
- HTML/browser snapshots que aportaron evidencia única;
- respuestas contractuales antiguas que ya tienen hash y normalización persistidos.

Política propuesta:

- 30 días raw sin comprimir cuando sea necesario para diagnóstico rápido;
- después, compresión `zstd` si está disponible o `gzip` como fallback;
- 180 días para raw que sustente cambios contractuales, conflictos o auditorías;
- raw puramente repetitivo con hash idéntico puede deduplicarse después de validar que el snapshot normalizado y el hash sobreviven;
- evidencia asociada a `CHANGED_REVIEW_REQUIRED`, `CONFLICT`, incidente, deploy o auditoría se conserva hasta resolución y luego entra en la retención aprobada.

Ninguna cookie, secreto o perfil Chrome se incluye en archivos de evidencia.

## Históricos de mercado

El crecimiento de históricos debe concentrarse en barras normalizadas, no en duplicar indefinidamente payload JSON completo.

- conservar OHLCV normalizado por identidad financiera y resolución;
- índices por `(symbol, asset_class, date/resolution)`;
- no duplicar una vela idéntica por corrida;
- mantener `known_at`/versionado cuando una fuente revisa una barra;
- raw de una descarga se conserva mientras sea necesario para trazabilidad y después se comprime/deduplica;
- no rellenar huecos con datos sintéticos;
- una revisión histórica no sobreescribe silenciosamente la versión anterior cuando cambia la evidencia.

## SQLite / WAL

- mantener WAL durante operación;
- ejecutar `PRAGMA quick_check` como gate;
- no usar `VACUUM` rutinario dentro de la rueda;
- `wal_checkpoint` sólo en ventana segura y después de verificar lectores/escritores;
- considerar `VACUUM` únicamente si existe espacio recuperable significativo y con procedimiento de protección de datos y recuperación comprobado;
- medir tamaño antes/después de cualquier mantenimiento.

## Logs

- las últimas líneas deben permanecer disponibles en Sistema -> Logs;
- rotación por tamaño y/o día;
- logs rotados se comprimen;
- retención operativa propuesta: 30 días local;
- incidentes, deploys y errores críticos se preservan como evidencia asociada aunque superen la retención normal;
- nunca eliminar el único log que explica un fallo activo.

## Docker y recuperación de aplicación

Política canónica: **NO se conserva una imagen Docker local de rollback sólo por si hubiera que volver atrás.** El disco del Droplet no es el repositorio de rollback de la aplicación.

La recuperabilidad de aplicación se basa en:

- commit/SHA exacto y trazable en GitHub;
- GitHub Actions como mecanismo preferido de redeploy/recovery;
- artefacto o imagen inmutable de registry cuando exista y esté verificado;
- si no existe artefacto inmutable utilizable, reconstrucción desde el commit exacto de GitHub mediante el pipeline gobernado.

Después de un deploy VERDE:

1. identificar la imagen activa por digest y los contenedores que la referencian;
2. verificar que el SHA exacto desplegado y su procedimiento de reconstrucción/redeploy estén disponibles en GitHub/GitHub Actions;
3. conservar localmente sólo la imagen necesaria para el runtime activo, salvo otra necesidad explícita y documentada distinta de rollback;
4. listar imágenes anteriores no referenciadas;
5. listar contenedores detenidos y build cache no referenciado;
6. presentar los candidatos y GB recuperables;
7. sólo con autorización del propietario, eliminar candidatos;
8. volver a medir disco y ejecutar smoke/health/DB checks.

Una imagen vieja, incluida una imagen pre-hotfix, **no queda protegida por política de rollback local**. Si no está referenciada por un runtime activo ni constituye evidencia única indispensable, es candidata a limpieza.

Está prohibido usar `docker system prune -a` a ciegas.

## Artefactos de deploy y pruebas

- bundles temporales, tarballs, ZIP heredados, scripts de instalación ya consumidos y outputs intermedios tienen TTL corto;
- la evidencia canónica de auditoría, identidad de release, SHA/digest y manifiestos necesarios para reconstrucción se preservan en GitHub/registry según corresponda;
- no se conserva una imagen Docker local antigua sólo como rollback de aplicación;
- después de cada implementación operativa y VERDE se ejecuta un inventario automático de residuos;
- la limpieza destructiva requiere lista explícita y autorización del propietario.

## Chrome / Playwright

- el perfil persistente de Chrome es secreto operacional y NO entra en backups compartibles, bundles ni GitHub;
- screenshots/HTML/JSON de investigación repetitivos se someten a retención;
- Contract Evidence normalizado y hashes sobreviven a la limpieza del raw;
- nunca exportar cookies/localStorage/sessionStorage.

## Umbrales de capacidad

Propuesta inicial, a ajustar con el inventario real del Droplet:

- < 65% filesystem usado: VERDE;
- 65-75%: AMARILLO, revisar crecimiento semanal;
- 75-85%: acción de limpieza/compresión planificada;
- >= 85%: ROJO para nuevas ingestas pesadas hasta recuperar margen;
- objetivo post-deploy/post-cleanup: al menos 30% de filesystem libre.

La ingesta de market data live no se detiene por una alerta de raw histórico si existe espacio seguro para el runtime. La ingesta pesada sí puede pausarse para proteger la base y el sistema.

## Control de crecimiento

Registrar diariamente:

- tamaño DB principal;
- tamaño WAL;
- filas por tablas de mayor crecimiento;
- número y tamaño de barras históricas;
- raw archive count/bytes;
- Contract Evidence snapshots/count;
- logs local/container/journal;
- Docker images/build cache/volumes;
- filesystem usado/libre;
- crecimiento 24h y 7d;
- estimación de días hasta 75% y 85% al ritmo actual.

## Ciclo obligatorio post-deploy

`deploy -> smoke -> health -> quick_check -> real_orders_sent=0 -> declarar VERDE -> verificar recuperabilidad GitHub -> inventario de residuos -> presentar limpieza -> autorización -> limpieza -> medición final -> smoke final`.

El deploy no se considera completamente cerrado hasta registrar el estado final de disco, la recuperabilidad por SHA exacto y los residuos deliberadamente conservados.

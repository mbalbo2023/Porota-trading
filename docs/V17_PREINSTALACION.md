# Chequeo previo a preparar el directorio v17 — sólo lectura

La separación PAPER ya está implementada y CI234 aprobó 1.368 pruebas y la
imagen candidata. No se desplegó. Antes de decidir cómo crear su directorio
en el Droplet, faltan metadatos actuales del host: propietario/permisos de
`data`, existencia del destino y espacio disponible. No se deben extrapolar
de un montaje Docker de lectura/escritura ni cambiar recursivamente los
permisos de todo el historial.

## Una acción del operador

Subir por SFTP `porota_v17_preinstalacion_r1.zip` a `/tmp`, sin descomprimir.
Es un nombre nuevo: no reemplazar los ZIP de diagnóstico del ledger.

```bash
python3 /tmp/porota_v17_preinstalacion_r1.zip --host
```

Pegar el JSON completo. Se imprime y se envía por OSC52 al portapapeles si
Termius lo permite. Debe indicar `v17-preinstall-1`.

## Alcance exacto

- Una llamada a `sudo -n docker inspect` de los dos motores conocidos, sólo
  nombre, running/restart, imagen por ID y usuario. No se imprimen Env/Mounts
  ni configuración completa. No crea ni arranca contenedores.
- `lstat` de cuatro rutas fijas: proyecto, `data`, `data/paper_v17` y su archivo
  `observer_v17.db`. Informa tipo, UID/GID, modo, tamaño y cantidad de enlaces.
  No lista directorios ni abre ningún archivo, SQLite, `.env` o credencial.
- `statvfs` del filesystem de `data`, UID/GID efectivos y consulta `access`
  del proceso lector. No crea un archivo para probar escritura.
- Rechaza enlaces, tipos inesperados, motores activos/reinicio habilitado,
  cambios de instalación, errores de permisos o Docker; sin fallback ni retry.
- No hace mkdir, chmod, chown, build, pull, copia, migración, instalación,
  llamadas PPI/Telegram o lecturas de cuentas. Siempre promotion_allowed=false.

`COMPLETED_HOST_METADATA` permite planificar; no es aprobación de instalación.
Si el archivo de destino existe: `OBSERVED_EXISTING_V17_PATH_REVIEW`, sin
abrirlo, adoptarlo o sobrescribirlo. `STOPPED` requiere revisar el motivo,
no cambiar permisos para forzar el chequeo.

No verifica ACL, UID remapeados de Docker, escritura efectiva de botuser,
capacidad completa para construir/rollback ni cambios posteriores. Esa
información y sus límites se usarán para preparar un procedimiento aislado.
El procedimiento futuro todavía no está autorizado como arranque/despliegue.

Código y pruebas independientes del motor. ZIP sólo con el lector estándar
y su manifiesto, sin datos, dependencias externas ni credenciales.

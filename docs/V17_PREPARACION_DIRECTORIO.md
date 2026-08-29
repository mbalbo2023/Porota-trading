# Preparación aislada del directorio PAPER v17

El preflight real del 29/08/2026 terminó `COMPLETED_HOST_METADATA`: ambos motores
apagados con reinicio `no`, proyecto y `data` pertenecen a UID/GID 1000, el
destino no existe y hay 10.118.623.232 bytes disponibles. El Dockerfile crea
`botuser` como UID 1000, por lo que no hace falta cambiar el propietario de la
raíz ni recorrer el historial.

`v17_prepare_directory.py` exige esos mismos motores detenidos, ejecutarse como
UID/GID 1000, padres reales (no enlaces), propietario correcto y al menos 1 GiB
libre. Rechaza un destino ajeno, no vacío o con una base ya existente. Crea
únicamente `data/paper_v17` con modo 0750, prueba una escritura exclusiva,
sincroniza y elimina el archivo de prueba. No crea ni abre SQLite, no toca
`data/observer`, no arranca contenedores y siempre informa
`promotion_allowed=false`. Una segunda ejecución válida es idempotente.

Compose deja de ejecutar `chown -R` sobre `/app/data`. Su inicializador sólo
prepara rutas técnicas explícitas, archivos de estado conocidos y `paper_v17`;
`data/observer` queda excluido. Conserva el ajuste recursivo de cachés, logs e
índice SRE, que no contienen el ledger. Antes de aceptar una
base existente en la ruta nueva exige archivo regular y no enlace.

Acción del operador, tras subir el archivo a `/tmp`:

```bash
python3 /tmp/v17_prepare_directory.py --prepare
```

El éxito esperado es `PREPARED`, carpeta UID/GID 1000 y modo 0750, prueba
creada/eliminada y base no creada. El resultado todavía no autoriza despliegue,
arranque ni órdenes; sirve para continuar con la promoción aislada.

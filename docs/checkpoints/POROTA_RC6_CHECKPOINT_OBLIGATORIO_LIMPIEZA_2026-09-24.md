# Checkpoint obligatorio RC6 — limpieza post-despliegue

Fecha: 2026-09-24  
Estado: obligatorio para todo cierre de implementación RC6.

## Regla de cierre

La limpieza no es una tarea opcional posterior. Todo despliegue RC6 se considera cerrado solamente cuando registra:

1. inventario antes y después: `docker system df`;
2. clasificación de artefactos;
3. limpieza segura ejecutada o una razón verificable de por qué no era necesaria;
4. espacio recuperado (incluido `0 B` cuando corresponda);
5. preservación explícita de contenedores activos, imágenes referenciadas, volumenes, bases, backups y evidencia;
6. `PRODUCTION_PAPER | SIMULATED | real_orders_sent=0`.

## Alcance permitido

- El workflow canónico debe conservar la imagen RC6 activa, las imágenes referenciadas por contenedores y el candidato actual.
- Sólo se pueden retirar etiquetas candidatas/rollback no referenciadas, con inventario válido.
- La limpieza debe emitir métricas antes/después y fallar de forma visible si no puede determinar con seguridad qué está en uso.

## Prohibiciones

- No `docker system prune`, `docker image prune` ni `docker builder prune`.
- No tocar build cache, contenedores, volúmenes, bases, ChromaDB, backups, evidencia ni PPI Watch.
- No usar la limpieza para rollback, redeploy ni reinicios.
- No ejecutar limpieza fuera del workflow canónico ni por acceso directo al Droplet.

## Verificación y continuidad

Cada checkpoint operativo debe incluir el run, commit, resultado de limpieza, espacio antes/después, artefactos preservados y el próximo control pendiente.  
Si esta evidencia falta, el despliegue queda **incompleto** aunque la aplicación responda verde.

Este checkpoint se mantiene en una rama documental separada para no convertir documentación en un disparador de deploy.

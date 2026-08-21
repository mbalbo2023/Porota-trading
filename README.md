# Porota Trading — infraestructura v16.1

Este repositorio queda preparado para recibir el paquete productivo v16.1 sin
subir todavía el código de aplicación.

## Flujo previsto

`develop` → `testing` → `main` → DigitalOcean

- **Codespaces:** entorno reproducible de pruebas mediante `.devcontainer/`.
- **testing:** validación del paquete final y simulación contra SANDBOX.
- **main:** versión promovida y etiquetada.
- **DigitalOcean:** producción, mediante un workflow manual y explícito.
- **Telegram:** la autorización operativa del bot sigue siendo responsabilidad
  de la aplicación; la infraestructura nunca la reemplaza.

## Regla de seguridad

No se versionan `.env`, credenciales, tokens ni datos runtime. Los secretos de
producción deben existir únicamente en el servidor y/o GitHub Actions Secrets.

## Carga del paquete final

1. Copiar el paquete final a una rama de trabajo.
2. Ejecutar `bash scripts/release-guard.sh`.
3. Abrir/usar un Codespace desde esa rama.
4. Ejecutar `bash codespace-test.sh`.
5. Confirmar CI verde.
6. Promover mediante `Promover a producción`.
7. Desplegar mediante `Despliegue de producción`.

El Codespace no se inicia automáticamente y el bot tampoco se inicia al crear
el entorno: esto evita consumir cuota y evita cualquier operación accidental.

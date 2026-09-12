# Porota Trading

La referencia operativa vigente es RC6. Este README documenta el flujo de cambios, validación y despliegue.

## Flujo previsto

`develop` → `testing` → `main` → DigitalOcean

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
3. Confirmar CI verde.
4. Promover mediante `Promover a producción`.
5. Desplegar mediante `Despliegue de producción`.

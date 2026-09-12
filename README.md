# Porota Trading

La referencia operativa vigente es RC6.

## Baseline operativo

- Versión: `17.0.0-rc6`
- SHA de origen certificado: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- Modo validado: `PRODUCTION_PAPER`, ejecución simulada y órdenes reales bloqueadas.

Usar siempre el SHA exacto aprobado para identificar una versión. El nombre de una rama de desarrollo no identifica por sí solo el código operativo.

## Flujo de cambios

1. Crear una rama de trabajo única para un objetivo acotado.
2. Ejecutar `bash scripts/release-guard.sh` y el CI aplicable al mismo commit.
3. Abrir un pull request hacia `main` con base, alcance, responsable, validaciones e impacto.
4. Un único responsable de integración revisa el diff y coordina la fusión.
5. No integrar cambios directamente a `main` desde una rama de desarrollo.

## Flujo de producción

Un despliegue requiere autorización explícita, CI verde del SHA exacto aprobado y una referencia inmutable que resuelva a ese mismo SHA. Registrar el SHA y la evidencia de validación/postdespliegue en el checkpoint correspondiente.

Los workflows manuales de promoción y despliegue del repositorio siguen bajo revisión para alinearlos con estas reglas. Este README no autoriza a ejecutarlos.

## Seguridad operativa

No se versionan `.env`, credenciales, tokens ni datos runtime. Los secretos de producción deben existir únicamente en el servidor y/o GitHub Actions Secrets.

La autorización operativa del bot sigue siendo responsabilidad de la aplicación; la infraestructura nunca la reemplaza.

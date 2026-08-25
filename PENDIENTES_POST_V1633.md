# Pendientes posteriores a v16.3.3

## Separación de servicios Docker — prioridad alta

Separar el despliegue actual en tres servicios con ciclo de vida independiente:

1. `dashboard`: disponible 24x7, sin credenciales PPI y con sesiones persistentes.
2. `trading-engine`: se inicia únicamente en la ventana BYMA y contiene PPI/ejecución.
3. `maintenance`: scheduler de históricos, backups, macro, noticias e informes.

La base SQLite y los estados runtime deben compartirse mediante volúmenes con permisos mínimos. El dashboard debe ser estrictamente de lectura salvo endpoints explícitamente auditados. La migración necesita prueba de sesión persistente, despliegue sin interrupción, healthchecks separados y rollback individual por servicio.

No se incluye en este hotfix para evitar un cambio arquitectónico antes de la prueba de autenticación PPI.

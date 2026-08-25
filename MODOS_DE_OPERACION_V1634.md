# Modos de operación — Porota Trading v16.3.4

El dashboard corre siempre en un contenedor separado y no recibe credenciales
de PPI. El selector impide que dos motores queden activos al mismo tiempo.

| Modo | Datos | Ejecución | Telegram |
|---|---|---|---|
| DETENIDO | Ninguna API | Ninguna | Avisa la detención |
| SANDBOX | PPI Sandbox | Órdenes de prueba | Informa SANDBOX |
| SIMULACIÓN PRODUCTIVA | PPI Producción, solo mercado | Compras/ventas locales simuladas | Informa SIMULACIÓN |
| PRODUCCIÓN REAL | PPI Producción | Dinero real | Bloqueado en v16.3.4 |

Comandos del administrador instalado:

```bash
sudo porota-mode status
sudo porota-mode simulation
sudo porota-mode sandbox
sudo porota-mode stop
```

`production` permanece deliberadamente bloqueado. Habilitarlo requiere otra
versión auditada, una marca local independiente y confirmación explícita de
dinero real. El cambio de modo se guarda en `data/operation_mode.json` y se
muestra en todas las páginas del dashboard y en Salud.

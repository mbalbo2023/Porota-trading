# POROTA TRADING RC6 — AUTORIZACIÓN CONTROL PLANE E2E

Fecha: 2026-09-07

`CONTROL_PLANE_E2E_DEPLOY=AUTHORIZED_USER`

Autorización explícita recibida del operador en ChatGPT para desplegar **únicamente** el control plane E2E temporal y aislado contra el Issue sintético #40.

Alcance permitido:
- Issue `[POROTA][RED][E2E-TEST]` #40 únicamente;
- gateway temporal, fail-closed;
- botón Telegram de autorización ficticia;
- registrar la decisión E2E en GitHub;
- comprobar limpieza y que el runtime de trading no cambió.

Fuera de alcance y NO autorizado:
- merge del PR #39;
- deploy del motor de trading;
- modificación o reinicio del observer/dashboard;
- cambios de configuración PAPER;
- credenciales o llamadas PPI;
- habilitar órdenes reales;
- cualquier operación de compra/venta;
- convertir la autorización E2E en autorización de hotfix real.

Invariantes obligatorias:
- `PRODUCTION_PAPER` intacto;
- `real_orders_sent=0`;
- Issue #40 con `E2E_TEST=true` y `HOTFIX_AUTOMATION_ALLOWED=false`;
- contenedor E2E temporal eliminado al finalizar o fallar;
- PR #39 permanece DRAFT/no merge.

Esta evidencia versionada fue creada después de la autorización humana y complementa el comentario canónico del PR #39. No constituye autorización para producción del control plane.
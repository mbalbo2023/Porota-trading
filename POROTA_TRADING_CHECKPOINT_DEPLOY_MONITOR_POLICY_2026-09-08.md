# POROTA TRADING — CHECKPOINT DEPLOY MONITOR POLICY — 2026-09-08

## Regla operativa permanente solicitada por Martín

A partir de este checkpoint, todos los despliegues y todos los estados explícitos READY_FOR_DEPLOY deben quedar bajo monitor automático.

### Alertas obligatorias
El monitor debe avisar cuando ocurra cualquiera de estos eventos:
- un deploy comienza;
- un deploy termina GREEN;
- un deploy falla;
- un frente/candidato queda READY_FOR_DEPLOY.

La alerta debe incluir, cuando exista evidencia disponible:
- branch;
- commit SHA;
- workflow/run ID;
- resultado de validate/deploy/postflight;
- estado observer/dashboard;
- DB quick_check y modo;
- real_orders_sent.

### Política ante fallos de deploy
- NO rollback automático.
- NO rollback manual sin autorización explícita de Martín.
- Ante un fallo: diagnosticar RCA, corregir la causa y hacer redeploy automático cuando la corrección sea segura, aislada y respaldada por evidencia.
- Si se considera rollback, detenerse y pedir autorización explícita a Martín.

### Invariantes de seguridad
- real_orders_sent=0.
- No realizar pruebas de órdenes reales.
- No llamar Budget/Confirm/Cancel u otras rutas de orden salvo prueba sandbox aislada y expresamente autorizada.
- Nunca registrar secretos en GitHub, logs, checkpoints o alertas.

### Monitor creado
Se creó un monitor recurrente de condición para el repositorio `mbalbo2023/Porota-trading`, con chequeo horario y notificación sólo ante cambios relevantes de deploy/readiness.

Esta política complementa y refuerza la regla ya vigente de no rollback automático.

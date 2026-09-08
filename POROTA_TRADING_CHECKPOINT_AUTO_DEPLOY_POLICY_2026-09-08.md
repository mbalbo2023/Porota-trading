# POROTA TRADING RC6 — POLÍTICA CANÓNICA DE AUTO-DEPLOY

Fecha: 2026-09-08

## Orden operativa del usuario

Desde este checkpoint, un candidato POROTA que alcance estado explícito GREEN / READY_FOR_DEPLOY y cumpla sus gates de seguridad no requiere una aprobación manual adicional de Martín para iniciar el deploy controlado.

## Flujo obligatorio

1. Continuar desarrollando, compilando, testeando y preparando oleadas independientes en paralelo.
2. Los deploys que afectan el mismo runtime deben ejecutarse con suficiente secuencialidad para conservar diagnóstico y trazabilidad.
3. Cuando una oleada queda GREEN / READY_FOR_DEPLOY:
   - preparar/lanzar automáticamente el deploy controlado;
   - aplicar baseline gate exacto;
   - preflight host;
   - build exacto;
   - activación;
   - postflight;
   - verificar observer/dashboard, DB quick_check, modo y real_orders_sent.
4. Mantener PRODUCTION_PAPER y real_orders_sent=0.
5. No hacer pruebas de órdenes reales ni invocar Budget/Confirm/Cancel/order routes salvo una autorización separada y explícita para un test únicamente sandbox.
6. Si un deploy falla:
   - NO hacer rollback automático;
   - diagnosticar RCA;
   - corregir el defecto de forma aislada;
   - revalidar;
   - hacer redeploy automáticamente cuando la evidencia lo soporte.
7. Sólo si rollback es la última alternativa viable se debe detener la automatización y pedir autorización explícita a Martín antes de cualquier rollback.

## Alertas

El monitor POROTA Auto Deploy Watch debe avisar ante:
- READY_FOR_DEPLOY que no pueda auto-desplegarse por un bloqueo concreto;
- inicio de deploy;
- deploy GREEN;
- fallo de deploy;
- necesidad excepcional de autorización de rollback.

## Regla de continuidad

Esta política reemplaza cualquier interpretación anterior según la cual Martín deba aprobar cada deploy GREEN. La aprobación manual de Martín queda reservada para rollback de última instancia y para cualquier prueba sandbox de rutas de órdenes que requiera autorización separada.

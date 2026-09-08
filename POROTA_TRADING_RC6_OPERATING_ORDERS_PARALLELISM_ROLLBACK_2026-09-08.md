# POROTA TRADING RC6 — ÓRDENES OPERATIVAS PERSISTENTES

Fecha: 2026-09-08
Rama canónica: `checkpoint/rc6-ppi-dom-api-operability-alerting-20260908`

## 1. Paralelización obligatoria por defecto

Orden del usuario:

- Todo trabajo independiente, seguro y aislable debe avanzar en paralelo siempre que no exista una dependencia técnica real que obligue a serializarlo.
- No esperar a que el usuario vuelva a pedir “paralelizar”.
- Mantener ramas/checkpoints separados para evitar mezclar frentes no relacionados.
- Priorizar CI, auditorías read-only, pruebas puras, documentación, modelos SHADOW y preparación de deploys mientras otros bloqueadores externos sigan abiertos.
- No usar “paralelizar” como excusa para mezclar cambios de runtime, seguridad, storage, riesgo o trading en un mega-deploy.
- Mantener trazabilidad de cada frente, estado y dependencia.

Esta regla debe considerarse instrucción persistente de continuidad y quedar reflejada en checkpoints futuros.

## 2. Política de rollback — autorización explícita obligatoria

Orden del usuario:

- NO ejecutar rollback automático ante una falla de deploy.
- NO ejecutar rollback manual por iniciativa del asistente.
- Ante una falla, primero diagnosticar la causa, corregir y volver a desplegar cuando sea técnicamente seguro.
- El rollback sólo puede ejecutarse si el usuario lo autoriza expresamente.
- Si el asistente considera que un rollback es la opción adecuada, debe pedir autorización explícita antes de ejecutar cualquier rollback desde GitHub o sobre el runtime.
- Un workflow de deploy NO debe tener lógica de rollback automático.
- Puede existir un mecanismo preparado de rollback, pero debe permanecer inactivo hasta autorización explícita del usuario.

## 3. Seguridad preservada

Estas órdenes NO modifican los invariantes de seguridad existentes:

- `real_orders_sent=0` mientras RC6 siga en PAPER/NO-TRADE;
- producción real NO-GO hasta aprobación separada;
- no probar rutas de órdenes reales;
- browser PPI read-only;
- no borrar evidencia/históricos sin equivalencia demostrada;
- no hacer `docker system prune`;
- no hacer cleanup destructivo amplio;
- no asumir cambios de runtime sin evidencia postflight.

## 4. Aplicación inmediata a Wave 1

Wave 1 debe seguir:

1. preflight read-only;
2. integración de safety/observability;
3. CI/regresión;
4. deploy controlado;
5. postflight;
6. si falla: diagnosticar y corregir;
7. NO rollback automático;
8. rollback sólo con autorización explícita del usuario.

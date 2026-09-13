# POROTA TRADING — Runbook PPI Web `BLOCKED_AUTH_SESSION_EXPIRED`

## Propósito

Este runbook es la respuesta canónica ante `BLOCKED_AUTH_SESSION_EXPIRED` en Contract Evidence. **No volver a investigar este síntoma desde cero** mientras este mecanismo y sus precondiciones sigan vigentes.

## Workaround canónico conocido

Ante una captura contractual que devuelve `BLOCKED_AUTH_SESSION_EXPIRED`:

1. Adquirir el lock exclusivo `/run/lock/porota-ppi-web-browser.lock`.
2. Verificar en modo read-only que `observer_v17.db` tenga `PRAGMA quick_check=ok`, `mode=PRODUCTION_PAPER` y `real_orders_sent=0`.
3. Reutilizar el mismo perfil persistente trusted-device; **no recrearlo**.
4. Ejecutar `rc6_ppi_web_reauth.py` como el usuario del browser usando únicamente el secret local protegido del host.
5. El helper sólo puede aceptar los POST de autenticación/SSO explícitamente aprobados. No visita rutas de órdenes y nunca automatiza OTP/2FA.
6. Si el resultado es `AUTHENTICATED_TRUSTED_DEVICE`, volver a ejecutar **una sola vez** el collector contractual GET-only.
7. Validar captura: schema esperado, auth trusted, `real_orders_sent=0`, cero fills de cantidad/precio y endpoints contractuales presentes.
8. La importación/recompute es una etapa separada; no se ejecuta si la captura no supera esas validaciones.

## Fix conocido de telemetría

El helper actual debe contener el manejo que aborta silenciosamente POST accesorios de hosts ajenos a PPI (por ejemplo telemetría) sin tratarlos como una mutación PPI no aprobada. Esto evita repetir el RCA histórico en el que telemetría de Microsoft Clarity producía un falso bloqueo del flujo de autenticación.

El helper actual también contempla los endpoints de autenticación PPI `/api/Seguridad/Auth/Login` y el SSO de Trading `/api/logInSSO`.

## Estados que NO deben automatizarse

Fail-closed y requerir diagnóstico/acción humana ante cualquiera de estos estados: `BLOCKED_AUTH_2FA_REQUIRED`, password-change requerido, credenciales rechazadas/challenge, secret local ausente/incompleto o con ownership/permisos incorrectos, página inesperada, rate-limit, error de servidor PPI o helper instalado sin el fix canónico.

No automatizar correo, OTP, PIN ni segundo factor. No hacer reintentos en loop.

## Seguridad obligatoria

Antes y después del intento: `PRODUCTION_PAPER`, `real_orders_sent=0`, DB `quick_check=ok`. El collector posterior al reauth debe seguir siendo GET/HEAD/OPTIONS-only. El procedimiento no debe arrancar importación histórica, un segundo writer ni rutas reales de órdenes.

## Antecedente y artefactos

Workaround histórico: branch `hotfix/rc6-contract-evidence-auto-reauth-20260907`, helper `rc6_ppi_web_reauth.py` y runtime `scripts/porota_contract_evidence_rc6_runtime.sh`.

Workflow operativo actual: `.github/workflows/rc6-contract-narrow-capture-20260914.yml`.

Este runbook se creó el 2026-09-14 para que el error conocido `BLOCKED_AUTH_SESSION_EXPIRED` tenga una respuesta persistente y repetible.

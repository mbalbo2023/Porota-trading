# POROTA TRADING RC6 — PPI WEB AUTH EXACT FLOW — 2026-09-07

## Estado ejecutivo

STATUS=YELLOW_FAIL_CLOSED
PPI_WEB_AUTH=ACCOUNT_BLOCKED_OR_COMPLIANCE_BLOCKED
EXACT_SUBTYPE=UNKNOWN
MORE_LOGIN_ATTEMPTS=STOP_UNTIL_UNLOCK_OR_EXPLICIT_SECURITY_ACTION
PRODUCTION_MODE=PRODUCTION_PAPER
REAL_ORDERS_SENT=0

Este checkpoint continúa desde `POROTA_TRADING_CHECKPOINT_PPI_WEB_AUTH_TEST_2026-09-07.md` y reemplaza la clasificación genérica previa `BLOCKED_AUTH_SESSION_EXPIRED` por una causa mucho más estrecha y sustentada en el flujo web real de PPI.

## 1. Hallazgo principal — el login real de PPI sí fue reproducido fielmente

Se inspeccionó primero el JavaScript público del login de PPI mediante GET-only y sin credenciales. El contrato web observado es:

- API base: `https://api.portfoliopersonal.com/`
- endpoint login: `/api/Seguridad/Auth/Login`
- body JSON: `{usuario, clave}`
- headers públicos del frontend:
  - `AuthorizedClient=191206`
  - `ClientKey=pp123456`
- header de fingerprint: `fp`, cuyo valor se toma del cookie `fp` del navegador.

La aplicación oficial interpreta `e.data.success || e.data.status===0` como éxito y toma `e.data.payload`.

Después se ejecutó un probe de un único intento usando el propio formulario/JavaScript de PPI dentro del perfil Chrome persistente del droplet. El probe no reconstruyó manualmente el request: llenó usuario/contraseña locales, pulsó el botón oficial de login y permitió solamente un mutation POST al endpoint exacto de autenticación. Todos los demás POST/PUT/PATCH/DELETE y todas las rutas operativas/order fueron bloqueados.

Resultado del run fiel:

- workflow: `RC6 PPI faithful visual login probe 2026-09-07`
- run: `34176935784`
- request de login: exactamente 1
- HTTP: `400`
- respuesta: JSON
- shape segura observada: `message`
- token: NO
- twoFAInfo: NO
- changePassword: NO
- señal de 2FA: NO
- señal de CAPTCHA/Incode: NO
- señal de fingerprint/device error: NO
- señal de bloqueo en el mensaje: SI
- `fp` cookie presente: SI
- auth cookies `tk_ob` / `rtk_ob`: NO
- navegación trading autenticada: NO
- `REAL_ORDERS_SENT=0`
- observer antes/después: `PRODUCTION_PAPER|0`

Por lo tanto ya no corresponde clasificar este problema como simple `SESSION_EXPIRED`, ni como un fallo causado por POST de terceros, ni como 2FA, CAPTCHA o falta de fingerprint.

## 2. Clasificación del error 400 con el código público oficial de PPI

Se ejecutó un segundo relevamiento estrictamente GET-only sobre el bundle público de login, sin credenciales y sin usar el perfil confiable.

El frontend oficial contiene un `switch` sobre `e.response.data.message` con estos casos explícitos:

1. `Usuario o clave incorrectos.` -> error en los campos de login.
2. `Usuario bloqueado.` -> modal de usuario bloqueado por máximo de intentos y acción oficial para enviar email de desbloqueo.
3. `Usuario bloqueado compliance.` -> modal de usuario bloqueado que no puede desbloquearse por esa vía y pide contactar a PPI.
4. cualquier otro texto -> modal genérico `No pudimos iniciar sesión`.

El probe fiel detectó semántica de `blocked` en el mensaje devuelto por PPI. `Usuario o clave incorrectos.` no contiene esa semántica, mientras que los dos casos explícitos de bloqueo sí.

Clasificación segura actual:

`PPI_WEB_AUTH=ACCOUNT_BLOCKED_OR_COMPLIANCE_BLOCKED`

No se afirma todavía cuál de los dos subtipos es el exacto porque el probe, por diseño, no imprimió el texto literal de la respuesta sensible del login.

Dado que previamente hubo varios intentos de login automáticos, un lock ordinario por máximo de intentos es plausible, pero no se declara como hecho hasta distinguirlo de un bloqueo compliance.

## 3. Política inmediata de seguridad

A partir de este checkpoint se detienen nuevos intentos automáticos de login hasta que ocurra una de estas condiciones:

1. el usuario desbloquee la cuenta por la vía oficial de PPI; o
2. el usuario autorice explícitamente una acción de seguridad/account-recovery concreta y previamente acotada.

Motivo: repetir POST de autenticación cuando PPI ya indica bloqueo puede empeorar o prolongar el estado.

No se ejecutó y no se debe ejecutar sin aprobación explícita:

- `ForgotPasswordBlockedUser` / envío de email de desbloqueo;
- cambio de contraseña;
- recuperación de credenciales;
- modificación de 2FA;
- modificación de dispositivo seguro;
- OTP/PIN automation;
- ningún bypass de seguridad.

## 4. Metadatos locales de autenticación

Auditoría local-only, sin red y sin leer valores de credenciales/cookies:

- `/etc/porota/contract-evidence-web.env`:
  - owner: `porotaadmin`
  - group: `porotaadmin`
  - mode: `600`
  - valores de usuario/contraseña: NO impresos
- perfil Chrome persistente disponible.
- cookie relevante presente: `fp` para `.portfoliopersonal.com`.
- `fp` es persistente y vigente.
- auth cookies `tk_ob` / `rtk_ob`: ausentes.

Esto demuestra que existe fingerprint de navegador/dispositivo, pero actualmente no existe una sesión web autenticada utilizable.

## 5. Contract Evidence y PPI Web History

`contract_evidence_v2_runs` permanece en `84` en el snapshot fresco. No se fuerza un nuevo E2E mientras la autenticación esté bloqueada.

PPI Web History SHADOW continúa:

- infraestructura/safety: GREEN
- authenticated coverage proof: BLOCKED por PPI Web Auth
- `canonical_write=DENY`
- `db_write=NO`

Cuando la autenticación vuelva a GREEN:

1. ejecutar un único login limpio;
2. confirmar `AUTHENTICATED_TRUSTED_DEVICE` o equivalente autenticado;
3. ejecutar Contract Evidence E2E sólo si está DUE y verificar `contract_evidence_v2_runs > 84`;
4. confirmar timer Contract Evidence GREEN;
5. correr PPI Web History SHADOW autenticado;
6. inventariar XHR/JSON históricos, profundidad, OHLCV, settlement, RAW/ADJUSTED, paginación y familias;
7. realizar Full Contract Sweep de todas las familias PPI.

## 6. Runtime y seguridad general frescos

Snapshot read-only de reanudación:

- observer `porota_production_observer`: running, restart count 0;
- image: `porota-trading-bot:17.0.0-rc6`;
- DB observer: `quick_check=ok`;
- mode: `PRODUCTION_PAPER`;
- `REAL_ORDERS_SENT=0`;
- session_state: `MARKET_CLOSED`;
- `market_snapshots=54152` al corte;
- disco `/`: ~24G total, ~14G usados, ~9.7G libres, 59% usado.

No se ejecutó limpieza destructiva.

## 7. SRE latency — RCA estrechado

La latencia SRE continúa abierta, pero ya está mejor localizada:

- lectura `observer_state`: sub-ms en auditoría previa;
- gates: ~1 ms;
- market snapshot count/max: ~121 ms;
- `PRAGMA quick_check`: ~28.95 s en auditoría previa;
- `PRAGMA quick_check`: ~70.9 s en snapshot fresco.

Conclusión operativa: las lecturas normales son rápidas; el chequeo integral pesado de SQLite es el componente dominante de latencia y no debe estar en el hot/fast health path.

## 8. Frentes paralelos ya avanzados

### History Store completeness + price_basis

Branch `fix/rc6-history-store-completeness-price-basis-20260907`.

Run `34173755984`: SUCCESS.

Estado: SHADOW/offline GREEN.

Invariantes ya validados:

- clave canonical incluye `price_basis`;
- RAW/ADJUSTED quedan separados;
- `COMPLETENESS_RANK` existe;
- FULL_OHLCV no debe perder contra una serie menos completa sólo por prioridad de source.

Todavía no autoriza secondary canonical write. Falta replay/regresión contra línea live y gate final.

### MFE/MAE provenance

Branch `feature/rc6-mfe-mae-provenance-20260907`.

Tests/compile offline GREEN. Sigue desacoplado del hot runtime hasta integración controlada.

### Empirical Evidence Architecture / storage

Sigue vigente el diseño content-addressed + manifests + único History Ingestion Coordinator.

No se inicia retiro destructivo de `historical_raw_archive`, VACUUM ni migración destructiva antes del Gate 0 funcional y las pruebas de equivalencia.

## 9. Próximo orden de ejecución

P1 inmediato:

1. resolver el bloqueo de cuenta PPI por vía oficial/segura;
2. un único login limpio y clasificado;
3. Contract Evidence E2E si DUE;
4. PPI Web History SHADOW autenticado;
5. Full Contract Sweep;
6. History Store v3 replay/regresión y gate;
7. decisión final de source policy PPI API + PPI Web vs IOL/A3 fallback/cross-check.

Paralelos ya preparados o abiertos:

- SRE/introspection latency;
- MFE/MAE integration;
- Forward Lab v2;
- Event Risk/backtests;
- sector/correlations/family normalization;
- cohorts/version separation;
- PAPER/SHADOW campaign;
- Samsung/Voice Access field test;
- kill-switch requalification;
- logs/source semantics;
- Evidence Architecture Gate 0 posterior al checkpoint funcional.

## 10. Invariantes preservados

- `PRODUCTION_PAPER`
- real money NO-GO
- `REAL_ORDERS_SENT=0`
- no network order tests
- no secretos/cookies/tokens impresos
- no OTP bypass
- no account-security mutation sin autorización explícita
- no secondary canonical write todavía
- no storage destructive work todavía
- no nuevos intentos automáticos de login mientras PPI indique bloqueo

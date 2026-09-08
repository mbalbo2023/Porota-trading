# POROTA TRADING RC6 — PPI WEB AUTH TEST — 2026-09-07

## Resultado ejecutivo

Se probó inmediatamente el usuario suministrado por el operador, sin imprimir credenciales y sin tocar el observer ni el dashboard.

Estado final: **YELLOW / FAIL-CLOSED**.

- El username quedó disponible en `/etc/porota/contract-evidence-web.env` bajo la clave exacta `PPI_WEB_USERNAME`, con el archivo preservado en modo `600` y owner `porotaadmin`.
- El bloqueo previo `BLOCKED_AUTH_USERNAME_REQUIRED` quedó superado.
- El primer intento con username válido encontró POSTs de telemetría de terceros (`www.google.com/g/collect` y luego `px.ads.linkedin.com/wa/`). Esos POSTs fueron siempre abortados; no eran necesarios para autenticación.
- Se endureció el test para abortar silenciosamente **todo POST de terceros**, manteniendo fail-closed para cualquier POST first-party de PPI que no sea el endpoint de login explícitamente aprobado.
- En el tercer run, el helper pudo completar cinco ciclos de submit de login sin que apareciera un POST first-party no aprobado, pero permaneció en `https://cuenta.portfoliopersonal.com/login` y terminó en `BLOCKED_AUTH_SESSION_EXPIRED`.
- No se detectó autenticación efectiva ni navegación a una página trading autenticada.
- No se detectó OTP/2FA como estado final en este run.

## Evidencia de runs

### Run 1 — username persistido

- branch: `hotfix/rc6-contract-evidence-username-refresh-20260907`
- commit: `583859e04a0fbc7b97eacd3935a16669e673d597`
- run: `34171810657`
- resultado: FAILURE fail-closed
- marcador: `PPI_WEB_USERNAME=PRESENT_RESTRICTED`
- bloqueo: `BLOCKED_AUTH_UNAPPROVED_POST`
- ruta sanitizada: `www.google.com/g/collect`
- observer DB antes/después: `ok|PRODUCTION_PAPER|0`
- `REAL_ORDERS_SENT=0`

### Run 2 — telemetría Google abortada silenciosamente

- commit: `d2d7af800db9df12d776a118306485b92bb4f707`
- run: `34172064041`
- resultado: FAILURE fail-closed
- política: `TELEMETRY_POST_POLICY=ABORT_SILENTLY`
- bloqueo siguiente: `BLOCKED_AUTH_UNAPPROVED_POST`
- ruta sanitizada: `px.ads.linkedin.com/wa/`
- observer DB antes/después: `ok|PRODUCTION_PAPER|0`
- `REAL_ORDERS_SENT=0`

### Run 3 — todos los POST de terceros abortados silenciosamente

- commit: `de6443ee0aa1403c993be38f65670ef87f5a43e6`
- run: `34172296567`
- resultado: FAILURE fail-closed
- política: `THIRD_PARTY_POST_POLICY=ABORT_SILENTLY`
- username: `PRESENT_RESTRICTED`
- secreto: valores no impresos
- observer DB antes: `ok|PRODUCTION_PAPER|0`
- auth result: `REAUTH_RC=4`
- auth status: `BLOCKED_AUTH_SESSION_EXPIRED`
- attempts: `5`
- page final: `https://cuenta.portfoliopersonal.com/login`
- stage final: `SUBMIT_LOGIN`
- observer DB después: `ok|PRODUCTION_PAPER|0`
- `REAL_ORDERS_SENT=0`

## Interpretación

El username ya no es el problema. El formulario de login se puede completar y enviar dentro del guard de seguridad, pero PPI no establece una sesión autenticada con la combinación actualmente disponible en el secreto local/perfil actual.

Con la evidencia disponible NO corresponde afirmar todavía si la causa exacta es contraseña no aceptada, requisito visual adicional/CAPTCHA, bloqueo de cuenta, challenge de sesión, cambio de flujo de login o mensaje de error no clasificado. El helper termina fail-closed y no imprime texto sensible de la página.

## Impacto sobre históricos por scraping

PPI Web History SHADOW sigue técnicamente GREEN en seguridad, pero permanece bloqueado por la misma autenticación web:

- `PPI_WEB_HISTORY_AUTH=BLOCKED_AUTH_SESSION_EXPIRED`
- JSON responses=0
- historical candidates=0
- FULL_OHLCV candidates=0
- `canonical_write=DENY`
- `db_write=NO`
- observer/dashboard unchanged
- `REAL_ORDERS_SENT=0`

Por lo tanto todavía no se puede declarar que PPI Web reemplaza IOL/A3 para históricos. Ese veredicto depende de lograr sesión autenticada y medir cobertura real de XHR/JSON históricos.

## Pendientes priorizados

### P1 — bloqueantes inmediatos

1. Diagnosticar de forma sanitizada por qué PPI mantiene el login después de submit: clasificar error de credenciales, CAPTCHA/challenge, bloqueo, mantenimiento o flujo de login cambiado, sin imprimir texto sensible.
2. Si la contraseña local estuviera desactualizada, renovarla **en el host o interfaz segura**, nunca pegarla en ChatGPT.
3. Lograr `AUTHENTICATED_TRUSTED_DEVICE`.
4. Ejecutar Contract Evidence E2E auténtico y confirmar incremento de `contract_evidence_v2_runs` cuando esté DUE, timer GREEN y `REAL_ORDERS_SENT=0`.
5. Re-ejecutar PPI Web History SHADOW autenticado y levantar inventario de endpoints/XHR históricos, profundidad, OHLCV, settlement, RAW/ADJUSTED, paginación y familias.
6. Integrar `completeness` + `price_basis` en History Store y replay/regresión antes de cualquier secondary canonical write.
7. Decidir policy final de fuentes: PPI API + PPI Web como core si la cobertura alcanza; IOL/A3 degradados a fallback/cross-check sólo con evidencia suficiente.

### P1/P2 ya abiertos y no olvidados

- Full Contract Sweep de todas las familias PPI.
- Cauciones: vencimiento exacto, quantity step, day-count/redondeo, profundidad/paginación, semántica de Budget y saldo DOLAR.
- SRE/introspection latency RCA.
- MFE/MAE executable + provenance.
- Forward Lab v2 / walk-forward / robustness.
- Event Risk preopen/open/close/postclose y backtests.
- sector map/correlations/family normalization.
- cohorts/version separation.
- PAPER/SHADOW campaign.
- retention/storage Gate 0 + Evidence Architecture v1 después del checkpoint funcional.
- Samsung/Voice Access field test.
- kill-switch requalification.
- logs/source semantics.
- IOL REST 401 y A3 quedan abiertos como fallback/cross-check hasta que PPI Web History pruebe cobertura suficiente.

## Invariantes preservados

- `PRODUCTION_PAPER`
- real money NO-GO
- `REAL_ORDERS_SENT=0`
- no network order tests
- no secretos/cookies/tokens impresos
- no canonical write de PPI Web History
- observer y dashboard no modificados por estas pruebas

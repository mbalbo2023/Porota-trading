# POROTA TRADING RC6 — CHECKPOINT CONTRACT EVIDENCE / PLAYWRIGHT HOTFIX

Fecha: 2026-09-07
Estado: PARCIALMENTE CORREGIDO — dependencia browser GREEN; sesión PPI confiable expirada
Ámbito: Contract Evidence / scraping autenticado PPI, exclusivamente read-only

## 1. Resumen ejecutivo

Se confirmó que Contract Evidence no estaba generando nueva evidencia aunque su timer systemd figurara enabled/active.

Causa técnica primaria encontrada y corregida:

- el host tenía Google Chrome disponible;
- el collector RC6 requería `playwright.sync_api`;
- Python del host no tenía Playwright;
- el deployment nativo había instalado scripts/timer pero no la dependencia browser;
- el primer job DUE real terminó en `BLOCKED_PLAYWRIGHT_UNAVAILABLE` y luego entró en backoff.

Hotfix aplicado de forma aislada:

- venv dedicado: `/opt/porota-contract-evidence-venv`;
- Playwright fijado en `1.62.0`;
- Google Chrome existente reutilizado como ejecutable;
- runtime Contract Evidence ejecuta únicamente el collector browser con el Python del venv;
- observer y dashboard NO fueron recreados ni modificados.

Después de corregir Playwright, el E2E llegó efectivamente al sitio PPI y descubrió el siguiente estado real:

`AUTH_STATUS=BLOCKED_AUTH_SESSION_EXPIRED`

Por lo tanto el scraping ya no está bloqueado por infraestructura browser. El pendiente actual es renovar de forma segura la sesión del perfil confiable, sin automatizar usuario/contraseña/OTP y sin almacenar credenciales.

## 2. Evidencia del diagnóstico inicial

Aproximadamente 11:40–11:50 AR del 2026-09-07:

- timer Contract Evidence: enabled / active;
- service oneshot: exit status 0;
- DB: `quick_check=ok`;
- observer: `PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- tabla `contract_evidence_v2_runs`: presente;
- runs existentes: 84;
- cuatro jobs estaban DUE:
  - `CONTRACT_EVIDENCE_DYNAMIC`;
  - `CONTRACT_EVIDENCE_CAUCIONES`;
  - `CONTRACT_EVIDENCE_AUCTIONS`;
  - `CONTRACT_EVIDENCE_DERIVATIVES`;
- últimas ejecuciones efectivas seguían fechadas 2026-09-04.

Journal seguro:

- `STATUS=AMARILLO_AUTH_BLOCKED`;
- `AUTH_STATUS=BLOCKED_PLAYWRIGHT_UNAVAILABLE`;
- luego `STATUS=AMARILLO_AUTH_BACKOFF`.

Dependencias verificadas en host:

- `/usr/bin/python3`: Python 3.12.3;
- Playwright: MISSING inicialmente;
- pip3 global: unavailable;
- `/usr/bin/google-chrome-stable`: PRESENT.

## 3. Hotfix Playwright aplicado

Rama de trabajo:

`hotfix/rc6-contract-evidence-playwright-20260907`

Archivos de hotfix:

- `requirements-contract-evidence.txt`;
- `scripts/porota_contract_evidence_rc6_runtime.sh`;
- workflow de instalación/validación aislada;
- workflows de E2E de diagnóstico/reintento.

Contrato nuevo del runtime:

`POROTA_CE_PYTHON=/opt/porota-contract-evidence-venv/bin/python`

El collector falla cerrado si el Python dedicado o Playwright no existen.

La instalación dejó:

- `PLAYWRIGHT_IMPORT=OK`;
- `PLAYWRIGHT_VERSION=1.62.0`;
- observer DB antes/después: `ok|PRODUCTION_PAPER|0`;
- `OBSERVER_CHANGED=NO`;
- `DASHBOARD_CHANGED=NO`;
- `REAL_ORDERS_SENT=0`.

Workflow de instalación:

- run `34137014486`;
- commit `95b5c86194b2d9c8908e09fadaf790fdca40b058`;
- resultado: SUCCESS.

## 4. Hallazgo adicional: runtime_state.json root/0600

Durante el primer reintento se observó que un probe sin privilegios informaba `BACKOFF_STATE_BEFORE=NONE`, mientras el runtime ejecutado con sudo veía un backoff existente.

Causa: `runtime_state.json` es deliberadamente protegido/root-owned. El diagnóstico fue corregido para leerlo mediante `sudo` sin exponer su contenido sensible.

En el E2E root-state se verificó explícitamente:

`ROOT_BACKOFF_STATE_BEFORE=BLOCKED_PLAYWRIGHT_UNAVAILABLE`

Se borró exclusivamente ese estado obsoleto:

`STALE_PLAYWRIGHT_BACKOFF=CLEARED`

No se autorizó borrar otros estados de autenticación.

## 5. E2E browser después del hotfix

Workflow:

- run `34137782799`;
- commit `4b90deaac01f36023fcb04e4f71ca5931b072c7a`;
- resultado: SUCCESS como diagnóstico seguro.

Resultado funcional:

- `PLAYWRIGHT_RUNTIME=GREEN:1.62.0`;
- `PLAYWRIGHT_IMPORT=OK`;
- browser fue iniciado;
- `blocked_nonread=0`;
- `real_orders_sent=0`;
- jobs solicitados: dynamic, cauciones, auctions y derivatives;
- estado final del collector: `BLOCKED_AUTH_SESSION_EXPIRED`;
- `STATUS=AMARILLO_AUTH_BLOCKED`;
- `AUTH_BROWSER_STARTED=YES`;
- rutas capturadas: 0;
- endpoints capturados: 0;
- `CE_RUNS_BEFORE=84`;
- `CE_RUNS_AFTER=84`.

La falta de incremento es correcta: el importer no debe aceptar evidencia obtenida sin sesión autenticada.

## 6. Estado de seguridad después del trabajo

Observer live esperado y comprobado durante los workflows:

- SHA: `5076b6dff8c644ed73160b4148eb7a1cf9ca7e43`;
- image: `porota-trading-bot:17.0.0-rc6`;
- running: true;
- restart count: 0;
- root filesystem: read-only;
- DB: `quick_check=ok`;
- mode: `PRODUCTION_PAPER`;
- `real_orders_sent=0`.

Dashboard:

- running;
- restart count 0;
- imagen classic tables;
- no fue recreado/modificado por este hotfix.

## 7. Estado semáforo

### GREEN

- causa `BLOCKED_PLAYWRIGHT_UNAVAILABLE`: corregida;
- Playwright aislado y versionado: GREEN;
- Chrome disponible: GREEN;
- timer/service wiring: GREEN;
- collector mantiene guard read-only: GREEN;
- solicitudes no-read detectadas durante el E2E: 0;
- observer/dashboard aislados del cambio: GREEN;
- `real_orders_sent=0`: GREEN.

### YELLOW

- sesión autenticada del perfil confiable PPI: `BLOCKED_AUTH_SESSION_EXPIRED`;
- Contract Evidence DUE E2E completo con importación: todavía NO demostrado;
- `contract_evidence_v2_runs` permanece en 84 hasta renovar sesión y capturar/importar evidencia válida.

### NO AUTORIZADO

- login automático con usuario/contraseña;
- almacenamiento de credenciales u OTP;
- bypass de 2FA;
- considerar GREEN una captura no autenticada;
- tocar observer/estrategia/órdenes para resolver Contract Evidence.

## 8. Próximo criterio de cierre

Contract Evidence podrá pasar a GREEN funcional cuando, con una sesión confiable autenticada restablecida:

1. un job DUE abra PPI mediante Playwright;
2. `AUTH_STATUS=AUTHENTICATED_TRUSTED_DEVICE`;
3. `blocked_nonread=0`;
4. se capturen rutas/endpoints sanitizados;
5. importer finalice con RC 0;
6. `contract_evidence_v2_runs` aumente respecto de 84;
7. `DB_QUICK_CHECK=ok`;
8. observer/dashboard mantengan identidad e integridad;
9. `real_orders_sent=0`.

Hasta entonces el estado correcto es YELLOW por sesión expirada, no RED del observer PAPER.

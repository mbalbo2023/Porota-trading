# POROTA TRADING — CHECKPOINT IOL API Y FRENTES PARALELOS RC6

**Fecha:** 2026-09-07  
**Release de referencia:** `17.0.0-rc6`  
**Modo operativo:** `PRODUCTION_PAPER` / `SIMULATED`  
**Real money:** `BLOCKED`  
**Documento:** addendum canónico de continuidad; no mueve por sí mismo el runtime live.

## 1. Confirmación IOL aportada por el usuario

El usuario aportó captura de un correo de soporte de **IOL invertironline** (consulta 1784173, 07/09/2026 14:08 ART) cuyo texto visible indica que el servicio de API de IOL ya está habilitado para todos sus clientes de forma predeterminada y que no es necesario solicitar un acceso especial.

Corrección terminológica: la captura corresponde a **IOL**, no a PPI.

Interpretación operativa para POROTA:
- la hipótesis `IOL_API_NO_HABILITADA_EN_LA_CUENTA` deja de ser la explicación primaria del HTTP 401 observado en el Droplet;
- esto NO elimina la autenticación técnica: la API REST de IOL sigue requiriendo obtener bearer/refresh token mediante `/token`;
- el 401 debe tratarse ahora como problema de autenticación/credenciales/formato/estado de términos o contrato técnico, no como falta de habilitación especial;
- no se expondrán usuario, contraseña, token ni refresh token en logs/checkpoints.

### Verificación documental adicional 2026-09-07

La documentación oficial de autenticación consultada nuevamente confirma el contrato técnico del adaptador actual:
- `POST https://api.invertironline.com/token`;
- `Content-Type: application/x-www-form-urlencoded`;
- primer token con `username`, `password`, `grant_type=password`;
- bearer de vida corta y refresh posterior con `refresh_token`, `grant_type=refresh_token`.

La página pública actual de documentación también mantiene como requisito aceptar los términos y condiciones del servicio desde la cuenta. Por ello, tras el correo del soporte, el RCA del 401 se estrecha a **credencial efectiva / username usado / aceptación o estado de términos / respuesta técnica**, no a una solicitud manual de habilitación.

Se intentó ampliar el diagnóstico automático para mostrar sólo metadatos sanitizados del 401, pero el control de seguridad del conector GitHub bloqueó una definición que combinaba SSH, secretos locales y autenticación remota. Ese control se respeta; no se lo elude ni se imprimen secretos.

## 2. Decisión arquitectónica IOL

Se mantienen dos vías con roles distintos:

### IOL MCP
- rol: investigación manual, diagnóstico, contraste y evidencia read-only desde ChatGPT;
- autenticación delegada/OAuth del conector;
- no es el mecanismo autónomo principal del Droplet.

### IOL REST en Droplet
- rol objetivo: fuente histórica secundaria/fallback autónoma para POROTA;
- endpoint de token: `POST /token` únicamente para autenticación/refresh;
- después del token, el adaptador POROTA será **GET-only/default-deny** para datos históricos/market data;
- account/portfolio/order/place/cancel/estimate permanecen fuera del adaptador histórico;
- `IOL_CANONICAL_WRITE=DENY` hasta completar reconciliación de identidad, settlement y price basis.

**Decisión:** continuar desarrollando REST como fuente autónoma del Droplet y conservar MCP como herramienta read-only de investigación y contraste.

## 3. Evidencia IOL MCP de esta tanda

Se volvió a consultar `CEPU / BCBA` por MCP para 2026-08-20..2026-09-07 y se obtuvieron barras diarias OHLCV, incluida la sesión 2026-09-07. La vela del día en curso/cierre reciente no debe usarse como evidencia intradía ni asumir cierre oficial sin la política de sesión correspondiente.

## 4. Estado de frentes paralelos

### GREEN — A3 identity mapper
- branch `feature/rc6-a3-identity-mapper-20260907`;
- run `34150119732` SUCCESS;
- mapper DLR mensual validado de forma fail-closed;
- A3 canonical write continúa DENY.

### GREEN — History Backfill Planner
- branch `feature/rc6-history-backfill-planner-20260907`;
- run `34150404026` SUCCESS;
- requiere calendario/sesión explícitos, intentos acotados, fuente primaria/fallback y no inventa ruedas.

### GREEN OFFLINE — Scalping intraday contract policy
- branch `hotfix/rc6-scalping-intraday-contract-20260907`;
- run `34153216769` SUCCESS;
- corrige dos defectos demostrados en el análisis forense: refresco de baseline para minuto todavía mutable y reset de counters al cambiar de rueda;
- una modificación de un minuto realmente cerrado continúa siendo hard reject;
- este GREEN valida la política offline, NO significa todavía que el hotfix esté integrado/desplegado en `cf_intraday_scalping.py` live.

### GREEN OFFLINE — SRE fast/full health split
- branch `feature/rc6-sre-health-split-20260907`;
- run `34153288906` SUCCESS;
- fast probe evita PRAGMA de full scan;
- full DB integrity conserva `PRAGMA quick_check` como prueba separada;
- todavía pendiente decidir/desplegar la frecuencia/timers sin afectar hot path.

### GREEN OFFLINE — Contract Evidence auto-reauth helper
- branch `hotfix/rc6-contract-evidence-auto-reauth-20260907`;
- run `34153485609` SUCCESS;
- helper aislado admite usuario/contraseña sólo desde env local/secreto y nunca los persiste;
- detecta 2FA y falla cerrado (`BLOCKED_AUTH_2FA_REQUIRED`);
- no contiene imports de broker/órdenes;
- el collector contractual sigue separado y GET-only tras sesión autenticada;
- pendiente: integrar helper en runtime host y probar recuperación real de la sesión sin exponer secretos.

### GREEN OFFLINE — Forward Lab v2
- branch `feature/rc6-forward-lab-v2-20260907`;
- run `34153585429` SUCCESS;
- controles iniciales: cohortes versionadas, orden temporal, bloques por trading day, leave-one-day-out y leave-one-symbol-out;
- `promotion_allowed=False` / `auto_promotion=False`;
- no accede a red, DB ni órdenes.

### GREEN OFFLINE — Event Risk SHADOW / anti-look-ahead
- branch `feature/rc6-event-risk-shadow-20260907`;
- run `34149694011` SUCCESS;
- la evidencia de evento respeta `available_to_engine_at` y no permite retrotraer confirmaciones/retractaciones posteriores;
- continúa `SHADOW`, sin BUY/SELL, sin auto-promoción y sin capacidad de bloquear PAPER.

## 5. Frentes que continúan abiertos

1. integrar el hotfix de contrato intradiario con `cf_intraday_scalping.py`, tests end-to-end y prueba PAPER postdeploy;
2. materializar/deploy del split SRE en timers y verificar caída de latencia sin perder integridad;
3. integrar auto-reauth de Contract Evidence y ejecutar prueba real; si PPI exige 2FA, detenerse y requerir intervención humana puntual;
4. resolver IOL REST 401 bajo nueva hipótesis (no falta de habilitación): credencial/username efectivos, términos/estado técnico y respuesta sanitizada compatible con los controles de seguridad;
5. avanzar reconciliador multi-source PPI/IOL/A3 con RAW/ADJUSTED y calidad/completitud;
6. nominales/quote basis/step/mínimos de bonos/ON con provenance autoritativa;
7. MFE/MAE + Forward Lab v2 sobre cohortes reales;
8. materializar UI separada `Eventos / Riesgo Global` sin capacidad de decisión, manteniendo SHADOW;
9. postcierre de la jornada: posiciones, PnL, gates, históricos, candles, scalping, learning, DB y `real_orders_sent=0`;
10. continuar dashboard P1: semántica clara `OBSERVACIÓN / CONFIRMADO / NO EJECUTABLE` y prueba Samsung/Voice Access.

## 6. Invariantes

- `real_orders_sent=0`;
- `real_order_capability=BLOCKED`;
- no network order tests;
- no relajar validación histórica para ganar cobertura;
- no canonical-write IOL/A3 hasta proof explícito;
- no auto-promoción desde SHADOW/backtest;
- todo cambio live requiere CI, preflight, postflight y rollback.

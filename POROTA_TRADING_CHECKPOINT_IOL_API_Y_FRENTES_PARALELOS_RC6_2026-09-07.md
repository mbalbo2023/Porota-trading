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

También se verificó:
- `TX28D / BCBA`: Título Público, USD, `units_per_lot=100`, `term=T1`, relacionado con `TX28` en ARS;
- `MRCXC / BCBA`: `not_found` en el catálogo IOL consultable por ese símbolo.

`units_per_lot=100` se conserva como evidencia de lote/unidad reportada por IOL, pero **no se interpreta automáticamente como lámina mínima o step nominal**. `MRCXC` queda para reconciliación de identidad; no se adivina.

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

### GREEN SOURCE-INTEGRATED OFFLINE — Scalping intraday contract
- branch `hotfix/rc6-scalping-intraday-contract-20260907`;
- policy run `34153216769` SUCCESS;
- integration run `34155090175` SUCCESS;
- persistence run `34155288329` SUCCESS;
- source integrado persistido en commit `fbd3ea89de970059398690a3e0ebebc4e0480022`;
- `cf_intraday_scalping.py` ya usa `classify_revision` + `previous_for_session`;
- una revisión de un minuto todavía mutable refresca `price/volume/last_verified_at` y actualiza el baseline;
- una revisión genuina de un minuto cerrado continúa hard reject;
- counters/rechazo previo no contaminan la rueda siguiente;
- este GREEN es de **source + CI offline**. Aún NO está desplegado al observer live y requiere postclose deploy + prueba PAPER.

### GREEN OFFLINE — SRE fast/full health split + wiring candidato
- branch `feature/rc6-sre-health-split-20260907`;
- run base `34153288906` SUCCESS;
- run wiring `34154742970` SUCCESS;
- fast probe evita PRAGMA de full scan;
- full DB integrity conserva `PRAGMA quick_check` como prueba separada;
- candidato systemd: `porota-fast-functional-health-rc6.service/.timer` mantiene liveness cada 5 min sin full scan;
- `porota-full-db-integrity-rc6.service` mantiene integridad completa como tarea explícita; deliberadamente no existe timer de full integrity cada 5 min;
- pendiente deploy postclose y comparación de latencia antes/después sin perder control de integridad.

### GREEN SOURCE-INTEGRATED OFFLINE — Contract Evidence auto-reauth
- branch `hotfix/rc6-contract-evidence-auto-reauth-20260907`;
- helper run `34153485609` SUCCESS;
- runtime wiring run `34155223743` SUCCESS;
- helper `rc6_ppi_web_reauth.py` admite usuario/contraseña sólo desde secreto local, no los imprime ni persiste, no navega rutas de órdenes y no contiene broker/order imports;
- `BLOCKED_AUTH_2FA_REQUIRED` continúa fail-closed;
- runtime candidato ahora trata `BLOCKED_AUTH_SESSION_EXPIRED` como recuperable: ejecuta helper separado y sólo si obtiene `AUTHENTICATED_TRUSTED_DEVICE` vuelve a lanzar el collector Contract Evidence GET-only;
- secreto local requiere owner del browser-user y mode 0600; secreto ausente/owner/mode incorrecto => fail-closed;
- el systemd unit admite configuración protegida vía `/etc/porota/contract-evidence-rc6.conf` sin credenciales embebidas;
- pendiente host deploy y prueba real. Si PPI exige 2FA, se detiene y requiere intervención humana puntual.

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

### GREEN OFFLINE — Vista `Eventos / Riesgo Global`
- branch `feature/rc6-event-risk-dashboard-view-20260907`;
- run `34154585438` SUCCESS;
- renderer separado `fj_event_risk_dashboard_rc6.py` con tabla semántica clásica y accesible;
- presenta `GLOBAL_EVENT_RISK`, timestamp, evento, región, nivel de confirmación, resumen factual, exposiciones, identidades POROTA, sesgo no operativo, confianza, freshness, fuente/tier, `available_to_engine_at`, retorno observado, análogo histórico y provenance;
- valida escaping de contenido no confiable;
- pruebas verifican ausencia de DB, red, broker y órdenes;
- `CAN_BLOCK_PAPER=NO`, `CAN_SEND_ORDER=NO`, `AUTO_PROMOTION=NO`;
- pendiente únicamente el wiring al dashboard separado y su deploy posterior; no se publica durante la rueda.

## 5. Frentes que continúan abiertos

1. **Scalping:** deploy postclose del source integrado `fbd3ea89...`, pre/postflight y prueba PAPER end-to-end `scanner → contract → candidate → gates → fill simulado` sin alterar thresholds;
2. **SRE:** deploy postclose del split fast/full, desactivar el viejo full-scan cada 5 min y medir latencia antes/después;
3. **Contract Evidence:** deploy host del auto-reauth wiring y prueba real; si aparece 2FA, detenerse y pedir intervención puntual;
4. **IOL REST:** resolver 401 bajo nueva hipótesis (no falta de habilitación): credencial/username efectivos, términos/estado técnico y respuesta sanitizada compatible con controles de seguridad;
5. **Históricos multi-source:** llevar reconciliador PPI/IOL/A3 a campaña shadow con RAW/ADJUSTED, calidad/completitud y canonical-write todavía DENY para IOL/A3;
6. **Nominales:** completar quote basis/step/mínimos de bonos/ON con provenance autoritativa; `units_per_lot` por sí solo no habilita;
7. **MFE/MAE + Forward Lab v2:** ejecutar sobre cohortes reales y ampliar robustez estadística;
8. **Eventos / Riesgo Global:** cablear la vista separada al dashboard manteniendo SHADOW y sin capacidad de decisión;
9. **Postcierre:** posiciones, PnL, gates, históricos, candles, scalping, learning, DB y `real_orders_sent=0` antes de cualquier deploy del observer;
10. **Dashboard P1:** semántica `OBSERVACIÓN / CONFIRMADO / NO EJECUTABLE`, tablas clásicas y prueba Samsung/Voice Access.

## 6. Invariantes

- `real_orders_sent=0`;
- `real_order_capability=BLOCKED`;
- no network order tests;
- no relajar validación histórica para ganar cobertura;
- no canonical-write IOL/A3 hasta proof explícito;
- no auto-promoción desde SHADOW/backtest;
- todo cambio live requiere CI, preflight, postflight y rollback.

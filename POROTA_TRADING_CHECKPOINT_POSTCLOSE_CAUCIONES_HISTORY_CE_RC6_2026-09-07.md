# POROTA TRADING — CHECKPOINT POSTCIERRE CAUCIONES / HISTÓRICOS / CONTRACT EVIDENCE RC6

**Corte:** 2026-09-07 postcierre AR  
**Timezone:** America/Argentina/Buenos_Aires  
**Release:** `17.0.0-rc6`  
**Modo:** `PRODUCTION_PAPER` / `SIMULATED`  
**Dinero real:** `BLOCKED`  
**Observer live branch:** `hotfix/rc6-cauciones-official-live-20260907`  
**Observer live SHA:** `6f47ec943d7b18756d93f17364903def1d7a1746`  
**Deploy run:** `34168767730`

Este addendum prevalece sobre el SHA live indicado en checkpoints anteriores. No reemplaza las políticas canónicas de seguridad, provenance, PAPER-only ni fail-closed.

## 1. Cauciones PPI — GREEN de discovery read-only, contrato financiero aún NO habilitado

Se corrigió el discovery de cauciones PPI para dejar de usar el filtro genérico inválido `CAUCION` como consulta de red. El alias legado se conserva sólo como señal interna y se transforma localmente al contrato de búsqueda observado/oficial:

- ticker `PESOS{días}` / `DOLAR{días}`;
- `Name={días}`;
- plazos probados/configurados inicialmente: `1, 2, 7, 30, 120` días;
- endpoint `SearchInstrument` bajo la barrera HTTP read-only ya existente.

Seguridad del lector:
- POST permitido únicamente para login/refresh PPI;
- datos de mercado/configuración sólo por GET allow-listed;
- no existen métodos `order_budget`, `budget`, `send_order`, `place_order`, `cancel_order` ni `order` en `ProductionMarketReader`;
- no se realizaron pruebas de órdenes de red.

CI funcional previo: run `34164979207` SUCCESS.

Deploy transaccional posterior al cierre: run `34168767730` SUCCESS.

Postflight exacto:
- `OBSERVER_SAFETY=GREEN` antes y después;
- `CAUCIONES_POSTCLOSE_DEPLOY=GREEN`;
- `TARGET_SHA=6f47ec943d7b18756d93f17364903def1d7a1746`;
- `REAL_ORDERS_SENT=0`;
- `DASHBOARD_UNCHANGED=YES`;
- DB observer `quick_check=ok`;
- `mode=PRODUCTION_PAPER`;
- observer read-only, restart count 0;
- `PAPER_T1_FULL_DATE_RELEASE=true` preservado.

### Restricción importante

Esto **NO** convierte cauciones en `READY_PAPER`. `bu_instrument_catalog.py` mantiene `CAUCIONES -> NEEDS_CAUCION_TERMS`. Antes de habilitar simulación financiera faltan términos autoritativos como day-count/forma de tasa, step/mínimo, vencimiento/liquidación y cualquier otra semántica que PPI confirme. El usuario está gestionando esa consulta por mail.

## 2. Históricos postcierre — store sano y rueda 07/09 presente

Auditoría schema-aware posterior al cierre:
- History Store v2 `quick_check=ok`;
- aproximadamente 48.751 versiones al corte auditado;
- ACCIONES y CEDEARS PPI ya alcanzan `2026-09-07` en canonical;
- se observaron 14 acciones y 46 CEDEARs con evidencia canónica del 07/09 en el corte;
- CEPU y AAPL, entre otros, ya tenían evidencia del 07/09.

No hay evidencia de que el histórico de acciones haya quedado detenido en 04/09.

### RCA confirmado: PARTIAL se cuenta erróneamente como “fallido” en source_sync

En `_download_histories()` el payload se clasifica correctamente como:
- `VALID_PAYLOAD`: todas las filas válidas;
- `PARTIAL`: contiene filas válidas y también filas no válidas/no completas;
- `EMPTY_OR_INVALID`: cero filas válidas;
- `ERROR`: excepción real.

History Store v2 reutiliza ese mismo payload y salvaguarda/versiona la evidencia FULL OHLCV válida con sus reglas de calidad; no hace una segunda consulta al broker.

El error está en la **métrica exterior**: actualmente todo estado distinto de `VALID_PAYLOAD`, incluido `PARTIAL` con `valid_rows>0`, incrementa el contador `failures`. Por eso un lote puede informar, por ejemplo, muchos “fallidos” aunque haya incorporado evidencia válida de esas identidades.

Corrección P1 aprobada para implementar sin relajar calidad:
- separar `full_valid`, `partial_with_valid_evidence`, `empty_invalid`, `errors`;
- `PARTIAL` permanece AMARILLO y visible, pero no se denomina “fallido”;
- sólo `EMPTY_OR_INVALID` y `ERROR` cuentan como fallo duro;
- `last_success_at` puede avanzar cuando existe evidencia utilizable (`VALID_PAYLOAD` o `PARTIAL` con filas válidas), sin convertir el lote en VERDE si hay parciales;
- no promover close-only ni filas inválidas;
- no modificar selector canonical, History Store ni presión/cadencia PPI;
- agregar tests de semántica antes de cualquier deploy.

## 3. Contract Evidence — YELLOW por username ausente, no por password

Auto-reauth offline: run `34165743286` SUCCESS.

E2E real: run `34165743278` terminó fail-closed con:
- observer live previo preservado;
- DB `ok|PRODUCTION_PAPER|0`;
- secreto web presente/restringido;
- valores de credenciales nunca impresos;
- `CE_RUNS_BEFORE=84`;
- `REAUTH_RC=4`;
- página final `https://cuenta.portfoliopersonal.com/login`;
- estado `BLOCKED_AUTH_USERNAME_REQUIRED`;
- `credentials_exposed=false`;
- `orders_visited=false`;
- `real_orders_sent=0`.

La corrección para aceptar un username precompletado por el perfil Chrome pasó CI, pero el intento real demostró que ese perfil no tenía un valor utilizable en el campo. Por lo tanto Contract Evidence sigue YELLOW hasta disponer del username de forma local segura o completar reautenticación interactiva. No se debe adivinar ni recuperar el username desde fuentes inseguras, y no se deben pedir contraseña/OTP por chat.

El contador E2E permanece en 84 hasta una reautenticación exitosa y una ejecución DUE autenticada.

## 4. SRE / dashboard / invariantes

- SRE quick health separado del `PRAGMA quick_check` pesado: GREEN en la corrección ya desplegada previamente.
- Dashboard no fue recreado por el deploy de cauciones; invariantes de ID/estado se verificaron en el workflow.
- `real_orders_sent=0` continúa siendo absoluto.
- dinero real sigue bloqueado.

## 5. IOL History — read-only/shadow, no canonical write

La rama estricta `feature/rc6-iol-history-readonly-20260907` mantiene separación del cliente legacy y sólo admite token POST + history GET allow-listed. RAW/ADJUSTED y settlement alignment se modelan explícitamente y `IOL_CANONICAL_WRITE_NOT_AUTHORIZED` sigue vigente.

Debe verificarse el último CI de esa rama y continuar reconciliación PPI↔IOL/A3. No integrar todavía IOL al canonical writer ni al hot path observer.

## 6. Próximo orden de ejecución

1. Implementar/validar la semántica `PARTIAL_WITH_VALID_EVIDENCE` de históricos desde el SHA live `6f47ec943...`.
2. Mantener Contract Evidence aislado en YELLOW hasta resolver username de forma segura; no bloquear otros frentes.
3. Confirmar CI actual de IOL read-only y mantenerlo SHADOW/no canonical write.
4. Continuar RCA y reconciliación histórica sin relajar calidad.
5. Abrir Gate 0 de `POROTA EMPIRICAL EVIDENCE ARCHITECTURE v1` sólo sin otro deploy del observer en curso.

## 7. Veredicto de este corte

- Observer PAPER: **GREEN**.
- Real orders: **GREEN / 0**.
- Cauciones discovery PPI: **GREEN read-only**.
- Cauciones contrato financiero/simulación: **YELLOW — espera términos autoritativos**.
- History Store integridad: **GREEN**.
- History source_sync semántica de PARTIAL: **YELLOW — RCA confirmado, corrección P1 pendiente**.
- Contract Evidence: **YELLOW — username local faltante**.
- IOL canonical write: **BLOCKED / NOT AUTHORIZED**.

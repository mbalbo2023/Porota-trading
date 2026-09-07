# POROTA TRADING — CHECKPOINT CANÓNICO RC6 POST-HOTFIX

**Corte base:** 2026-09-06 19:30 AR  
**Cierre nocturno actualizado:** 2026-09-06 22:30 AR  
**Timezone operacional:** America/Argentina/Buenos_Aires  
**Release:** `17.0.0-rc6`  
**Branch live congelada:** `hotfix/rc6-cedear-us-labor-day-20260906`  
**SHA live exacto desplegado:** `db26c76723bb988c956589c572b87cbcb4191731`  
**Modo:** `PRODUCTION_PAPER`  
**Ejecución:** `SIMULATED`  
**Real money:** `BLOCKED`  
**Checkpoint/docs branch:** `checkpoint/rc6-20260906-posthotfix`

> Este documento es el contexto canónico de continuidad. La branch live del observer se mantiene congelada en `db26c...`; la documentación vive en branch separada y no debe mover la identidad live. Ante contradicción con textos anteriores, prevalece este checkpoint y las correcciones/addenda posteriores de esta misma branch.

## 1. Estado live después del hotfix

El blocker de calendario USA para CEDEARs quedó corregido y desplegado.

Regla validada para 2026-09-07:
- todos los CEDEARs con subyacente USA: nueva apertura `HOLD — UNDERLYING_MARKET_CLOSED: US_LABOR_DAY`;
- no limitar la regla a AAPL/AAPLD/AAPLC;
- acciones argentinas como GGAL no quedan bloqueadas por Labor Day USA si el mercado local opera;
- cotizaciones CEDEAR continúan observándose/registrándose;
- la sesión no debe tratarse como cohorte CEDEAR normal para aprendizaje.

La corrección pasó compilación, tests específicos, suite activa RC6 offline, invariantes de seguridad, build/publicación inmutable, deploy transaccional y postdeploy DB/health/orders.

Runtime probado:
- observer running, restart=0, readonly=true;
- observer image `porota-trading-bot:17.0.0-rc6`;
- dashboard running, restart=0;
- DB observer `quick_check=ok`;
- DB history `quick_check=ok`;
- PPI auth OK/read-only;
- `real_orders_sent=0`;
- real-order capability bloqueada.

### Política canónica de recuperación de aplicación

**NO se mantiene rollback de aplicación como política de retención en el disco del Droplet.**

- la identidad recuperable es el commit/SHA exacto en GitHub;
- GitHub Actions es el mecanismo preferido de redeploy/recovery;
- se puede reutilizar una imagen/artefacto inmutable de registry si existe y está verificado;
- si no existe, se reconstruye desde el commit exacto mediante pipeline gobernado;
- imágenes Docker antiguas no quedan protegidas por el solo hecho de poder servir como rollback;
- cualquier imagen anterior que quede temporalmente tras una transacción entra al siguiente lifecycle cleanup si no está referenciada ni es evidencia única indispensable.

Esta regla prevalece sobre cualquier texto anterior que indicara conservar una imagen local pre-hotfix o de rollback.

## 2. Auditoría de ingesta/históricos/aprendizaje — resultado

### PPI live / snapshots — GREEN
- datos reales read-only;
- snapshots y símbolos existentes;
- último dato de mercado consistente con último día hábil;
- hot path independiente de A3/Contract Evidence.

### Candles — GREEN
- worker activo;
- cursor de snapshots avanzado;
- candle integrity timer activo;
- no fabricar candles en domingo/feriado sin ticks.

### Store histórico canónico — GREEN de integridad
- DB sana;
- versionado/provenance presentes;
- fuentes PPI Production History + Data912 separadas;
- Data912 actúa como fallback/legacy, no como fuente dinámica primaria.

### PPI historical — P1 PARCIAL, RCA AVANZADO

La ingesta no está detenida. El problema dominante es calidad/semántica de determinadas filas/series y priorización, no corrupción general del store.

Evidencia de la noche:
- History Store v2 conserva barras FULL_OHLC válidas aunque otras filas del mismo símbolo sean rechazadas;
- existe evidencia close-only separada;
- los rechazos se concentran en `OHLC_INCONSISTENT`, `HIGH_NONPOSITIVE` y `OPEN_NONPOSITIVE`;
- variantes de especies, especialmente determinados sufijos C/D, concentran una parte relevante del problema;
- la métrica vieja de coverage simple subestima el material útil disponible porque no refleja bien la preservación parcial de barras válidas/close-only;
- la selección de targets tiene margen para priorizar mejor identidades incompletas y evitar ciclos sobre series ya cubiertas.

Pendientes P1:
- mejorar priorización de `_historical_targets()` sin aumentar presión sobre PPI;
- continuar backfill bounded con retry/backoff;
- clasificar JSON defectuoso/transitorio por instrumento;
- mantener validación fail-closed y provenance;
- nunca aceptar datos inválidos sólo para elevar coverage;
- medir avance con snapshots antes/después.

### A3 Historical — P1 REAL

La conexión y el servicio funcionan, pero identity alignment no.

Primera ejecución controlada `WEEKEND_DEEP`:
- selected=40;
- failures de transporte=0;
- `ALIGNMENT_UNVERIFIED`=40;
- filas A3 incorporadas=0.

Causa observada: divergencia de identidad, por ejemplo PPI `DLR/SEP26` vs A3 `DLR092026`.

Pendiente:
- mapping canónico producto+vencimiento+familia/mercado;
- provenance del símbolo original A3;
- fail-closed para ambigüedad;
- tests;
- repetir ingesta controlada después de corregir.

No bloquea PAPER porque A3 es background/read-only y no veta el hot path PPI.

### IOL / InvertirOnline — P1 DEFINIDO

Estado real confirmado:
- existe código IOL legacy en `ak_iol_client.py` y `al_historical_ingest.py`;
- está deshabilitado/no cableado al observer RC6, scheduler histórico RC6 ni History Store v2;
- `j_main.py` es runtime legacy y no debe activarse;
- el cliente legacy no es estrictamente GET-only porque contiene un POST de estimación;
- el store legacy hace UPSERT por `(symbol,date)` y no es apto como canonical v2 por riesgo de reemplazar provenance.

Próxima acción:
- construir `IOL_HISTORY_READONLY` aislado, allowlist GET-only;
- proof pequeño PPI↔IOL inicialmente sin persistencia canonical;
- comparar OHLCV, huecos, valores <=0, inconsistencias, adjusted/unadjusted e identidad;
- si el proof es satisfactorio, integrar como fuente separada `IOL_HISTORY` al History Store v2 con versionado/provenance;
- scraping IOL sólo como complemento de Contract Evidence/spot checks si la API no alcanza y después de revisar términos; no scraping masivo como fuente histórica primaria.

IOL no es blocker del PAPER del lunes y no cambia PPI como fuente live actual.

### Contract Evidence / scraping — scheduler GREEN, primer DUE pendiente
- RC6 native overlay/timer presente;
- fuera de ventana devuelve `GREEN_NOT_DUE`;
- no browser/auth artificial fuera de política;
- cero órdenes;
- primera ejecución RC6 realmente DUE debe verificarse cuando corresponda.

### Aprendizaje SHADOW — GREEN de arquitectura
- IA intradía OFF;
- collector read-only;
- `real_money_authorized=false`;
- `automatic_promotion=false`;
- gates económicos/expectancy/régimen/concentración siguen SHADOW/observación;
- learning samples y contrafactual deben seguir acumulándose durante la campaña PAPER.

## 3. Dashboard RC6 — GREEN FUNCIONAL, P1 UX DE TABLAS PENDIENTE

Dashboard desplegado de forma independiente al observer:
- imagen activa: `porota-trading-dashboard:17.0.0-rc6-go-live-final`;
- dashboard SHA: `da2c87936d90cda17512de3bc529d13f4693c1c1`;
- deploy run: `34071773312`;
- validate: SUCCESS;
- deploy: SUCCESS;
- matriz HTTP postdeploy: GREEN.

Superficies verificadas HTTP 200:
- `/`;
- `/vivo`;
- `/historicos`;
- `/reportes`;
- `/config`;
- `/sistema?section=introspeccion`;
- `/salud`;
- `/universo-operativo`;
- `/scalping`;
- `/instrumentos`;
- `/aprendizaje`;
- `/dashboard/logs`.

Correcciones funcionales/observabilidad ya incorporadas:
- panel ejecutivo con máximo cinco operaciones PAPER válidas y sin presentar fin de semana/feriado como actividad normal;
- remoción del bloque técnico de workers de En Vivo;
- navegación lateral de Sistema preservada en drill-down;
- configuración alineada a RC6;
- reportes macro/performance recuperados en Reportes;
- Telegram toma verdad de worker/outbox/jobs actuales;
- SRE explica su AMARILLO por latencia sin ocultar integridad/disco;
- introspección selecciona snapshots RC/HF actuales y usa ventana coherente con cadencia horaria.

### CORRECCIÓN CANÓNICA DEL REQUERIMIENTO DE TABLAS

El requerimiento visual de “tabla” fue mal interpretado en el rediseño. El formato actual que convierte registros en bloques/tarjetas apiladas con pares etiqueta/valor **NO es aceptado** para la operación real; el usuario lo considera completamente ilegible.

La interpretación correcta para mañana es restaurar el estilo anterior al rediseño:
- tabla matricial real con **filas y columnas**;
- una fila por registro/entidad;
- encabezados de columna visibles y alineados;
- valores comparables verticalmente alineados;
- NO convertir cada registro en una tarjeta;
- NO repetir etiquetas de columna dentro de cada celda como diseño principal;
- NO activar automáticamente modo card/stacked en la tablet Samsung por ancho de contenido;
- preservar contraste, tamaño táctil, foco y compatibilidad con Voice Access;
- no introducir scroll horizontal global de la página; si una tabla excepcionalmente requiere más ancho, resolver columnas/prioridad o limitar el scroll al contenedor de esa tabla.

Pantallas obligatorias a revisar/restaurar:
- Caja y patrimonio por moneda;
- Histórico de trading;
- Universo operativo / matriz por familia;
- Scalping / contrato intradiario;
- Instrumentos y contratos;
- Aprendizaje / cobertura empírica;
- Salud de APIs;
- Jobs internos;
- Scraping;
- Backups;
- cualquier otra superficie convertida a pseudo-tabla/card durante el rediseño y que originalmente fuera una grilla de filas/columnas.

**Prioridad:** P1 inmediato de UX operativa para mañana. No es P0 de safety, pero es necesario para controlar la jornada. El cambio debe ser dashboard-only y no alterar queries/source-of-truth, observer, estrategia, gates ni órdenes.

## 4. Control final de introspección posterior al deploy — GREEN

Control read-only run `34072514381` completado SUCCESS.

Evidencia:
- timer `porota-introspeccion-hf5.timer`: enabled + active;
- `LastTriggerUSec=Mon 2026-09-07 01:15:00 UTC`;
- `NextElapseUSecRealtime=Mon 2026-09-07 02:15:00 UTC`;
- primer snapshot RC6 posterior al deploy final: `porota_introspection_rc6_20260906_221536_301594.json`;
- timestamp interno: `2026-09-06T22:15:01-03:00`;
- edad durante la prueba: ~143 s;
- `POST_DEPLOY_INTROSPECTION=GREEN`;
- dashboard `/sistema?section=introspeccion`: HTTP 200, `system-nav` presente, sin falso warning stale y con evidencia FRESH;
- `DASHBOARD_INTROSPECTION=GREEN`;
- postflight runtime: `ok|ok|PRODUCTION_PAPER|0`;
- observer no reiniciado;
- estrategia no modificada;
- real-order capability sigue BLOCKED.

Nota de prueba: el primer intento de control falló sólo porque exigía una frase literal que la UI no imprimía; el snapshot ya era válido. El segundo control aceptó la evidencia real `snapshot FRESH` y terminó GREEN. No fue un fallo del runtime.

## 5. Telegram / SRE / logs

### Telegram
- VERDE;
- worker RUNNING;
- heartbeat vigente;
- outbox SENT=83;
- FAILED=0;
- no bloqueante para PAPER.

### SRE
- estado persistido AMARILLO;
- `quick_check=ok`;
- capacidad de disco por encima del gate;
- causa observada: latencia elevada de la propia medición SRE frente al umbral de 250 ms;
- no bloqueante por sí solo mientras integridad/disco sigan sanos;
- queda P1 para optimizar/entender latencia.

### Logs
- archivos observer/bot en 0 bytes no deben interpretarse automáticamente como caída del runtime;
- dashboard/logging debe distinguir ausencia de archivo/flujo legacy de evidencia runtime actual;
- seguimiento no bloqueante durante campaña PAPER.

## 6. Storage / limpieza

La limpieza de disco de esta noche se realizó de forma controlada, sin `docker system prune -a`, `git clean` ni `VACUUM` ciego, y sin tocar DBs/históricos/evidencia canónica.

Política vigente:
- sólo imagen/runtime activo es necesaria por defecto;
- GitHub/GitHub Actions por SHA exacto es la fuente de recovery;
- cualquier imagen anterior que quede como residuo transaccional no está protegida como rollback y entra en lifecycle cleanup cuando no esté referenciada;
- no conservar rollback de aplicación local como política.

## 7. Plan obligatorio para mañana

### 7.1 Antes del preopen / primera prioridad UX
- Restaurar las tablas clásicas de filas/columnas según §3, si puede hacerse de forma segura antes de la apertura.
- Validar visualmente en Samsung/Voice Access.
- Deploy sólo dashboard con preflight/postflight; observer no se reinicia.
- Si el cambio visual no puede cerrarse con seguridad antes de la apertura, no arriesgar el hot path; se mantiene el dashboard funcional y se corrige después de rueda, pero se registra como limitación operativa.

### 7.2 Preopen 10:15–10:30 AR
- verificar SHA live exacto `db26c...`;
- observer/dashboard/image/restarts/readonly;
- `/health=ok`;
- DB quick_check;
- disk;
- timers;
- PPI auth;
- `real_orders_sent=0`;
- calendar local/USA;
- política CEDEAR Labor Day;
- confirmar que fines de semana/feriados locales no permitan operar;
- confirmar que background ingestion pueda seguir fuera de rueda;
- field test esencial Samsung/Voice Access.

### 7.3 Durante rueda 10:30–17:00
- PAPER only;
- no cambios de código/config salvo incidente real;
- observar quotes/freshness;
- decisiones/gates SHADOW;
- fills PAPER/positions/PnL/marks;
- CEDEAR USA bloqueado sólo para nuevas aperturas;
- candles/history/backfill sin bloquear hot path;
- Contract Evidence primera ejecución DUE si corresponde;
- introspección/early-warning;
- evidencia horaria a `/validacion`;
- registrar defects visuales, pero no hacer rediseños durante rueda.

### 7.4 Cierre
- reconciliar PAPER;
- confirmar `real_orders_sent=0`;
- comprobar avance postclose de históricos;
- candle integrity;
- errores/retries PPI historical;
- aprendizaje SHADOW;
- actualizar `/validacion` y checkpoint.

## 8. `/validacion` — criterio para completar hitos

- M0 Infraestructura/capacidad: deploy transaccional, recuperabilidad por SHA exacto desde GitHub/GitHub Actions, disk gate, containers, timers, health y evidencia de operación.
- M1 Safety: real-order block, readonly, DB integrity, calendar fail-closed, recovery runbook desde GitHub, safety gates.
- M2 Fuentes/contratos: PPI read-only + Contract Evidence DUE real + provenance/contratos por familia.
- M3 Mercado/históricos: progreso sostenido PPI/Data912/A3/IOL si se incorpora, freshness/gaps/coverage suficiente; scheduler instalado no equivale a completo.
- M4–M8: campaña PAPER forward, realismo, estabilidad, estadística, A11Y y jornadas sostenidas.
- M9: auditoría/consenso.
- M10: governance candidate real-money.
- M11: real-money sigue BLOCKED.

## 9. Backlog P1/P2 después del cierre nocturno

### P1
- Restauración de tablas clásicas filas/columnas en dashboard.
- PPI historical: priorización/calidad/full-universe y retries controlados.
- IOL_HISTORY_READONLY proof PPI↔IOL sin persistencia canonical inicial.
- A3 identity alignment.
- Contract Evidence primera ejecución DUE.
- SRE measurement latency RCA/optimización.
- Forward Lab v2.
- MFE/MAE executable + provenance.
- campaña SHADOW sostenida.
- A11Y Samsung/Voice Access en dispositivo real.
- calendar local/USA fail-closed completo por familia.
- kill-switch fail-closed antes de reutilizar.

### P2
- lifecycle adicional de residuos Docker/untracked si aparecen.
- sector map/correlation/family normalization.
- close-only salvage effectiveness.
- candle integrity longitudinal.
- Telegram noise/suppression con CRITICAL no suprimible.
- mejoras estéticas no operativas posteriores; **la corrección de tablas no es P2**.

## 10. Veredicto de cierre 2026-09-06

- **P0 conocidos abiertos para PAPER: 0.**
- hotfix Labor Day CEDEAR: CORREGIDO/DEPLOYED.
- dashboard RC6: DEPLOYED/GREEN funcionalmente, con P1 UX de tablas pendiente.
- control postdeploy introspección automática: GREEN.
- runtime: `PRODUCTION_PAPER`, DBs sanas, `real_orders_sent=0`.
- PPI historical: funcional pero parcial — P1.
- IOL: proof read-only pendiente — P1/no blocker.
- A3: conexión/job funciona, alignment pendiente — P1.
- Contract Evidence: primera ejecución DUE pendiente — P1/no blocker hot path.
- Go Live mañana: **GO condicionado a preopen GREEN y ausencia de nueva evidencia crítica.**
- Real-money: **NO-GO / BLOCKED**.

## 11. Regla de continuidad

En cualquier chat futuro:
1. leer este checkpoint completo;
2. leer `POROTA_TRADING_PENDIENTES_GO_LIVE_Y_SEMANA_2026-09-06.md` y los addenda/correcciones IOL/dashboard de esta branch;
3. verificar GitHub y runtime live antes de asumir estado actual;
4. exigir SHA runtime `db26c...` mientras no exista nuevo deploy explícito del observer;
5. distinguir identidad del observer de identidad del dashboard, ambos siguen siendo RC6;
6. no mover la branch live por documentación;
7. registrar cada hallazgo en `/validacion` y siguiente checkpoint;
8. no transformar pendientes P1/P2 en blockers sin evidencia nueva;
9. no declarar GREEN una fuente sólo porque el timer existe;
10. no conservar rollback de aplicación en disco como política: recovery desde GitHub/GitHub Actions por SHA exacto;
11. para cualquier cambio visual de tablas, respetar la corrección canónica: **filas y columnas clásicas, no cards/stacked en tablet**.

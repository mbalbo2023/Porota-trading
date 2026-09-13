# POROTA TRADING — CAUCIONES SEMANTIC EVIDENCE

**Fecha:** 2026-09-13  
**Rama:** `ops/rc6-ppi-web-residual-ready-20260913`  
**Modo:** evidencia / read-only / fail-closed  
**Regla:** este documento NO habilita trading ni `READY_PAPER`. Sólo clasifica qué semánticas están demostradas y cuáles siguen bloqueadas.

## 1. Evidencia PPI API autenticada read-only

Run canónico: GitHub Actions `34773357093`, job `103766883647`.

Confirmado:
- `CAUCIONES` está declarado como tipo de instrumento.
- `BYMA` está disponible como mercado.
- PPI declara la operación `COLOCAR-CAUCION`.
- Descubrimiento oficial devolvió 10 identidades: `PESOS1/2/7/30/120` y `DOLAR1/2/7/30/120`.
- `current` expone `date, marketChange, marketChangePercent, max, min, openingPrice, previousClose, price, volume`.
- `book` expone `bids, date, offers`.
- El snapshot dominical del book tenía fecha sentinel `0001-01-01...`; NO es dinámica operable.
- Los `current.date` observados correspondían a ruedas previas; NO son quote fresca EOD.

Valores observados que permiten reconciliación externa, sin convertirlos por sí solos en quote ejecutable:
- `PESOS1 current.price = 17.6`
- `PESOS2 current.price = 25.0`
- `PESOS7 current.price = 20.5` en el snapshot API previo
- `PESOS30 current.price = 20.6`
- `PESOS120 current.price = 26.0`

## 2. Evidencia pública oficial externa

### PPI — cotizaciones públicas de cauciones

Fuente oficial: `https://www3.portfoliopersonal.com/Cotizaciones/Cauciones`

La pantalla pública identifica los valores como porcentajes de caución/“Último operado”. Los valores de 1, 2 y 30 días observados públicamente coincidieron exactamente con `current.price` del snapshot autenticado PPI (`17.60`, `25.00`, `20.60`).

**Conclusión:** `current.price` puede clasificarse como **última tasa operada expresada en porcentaje**, pero sigue siendo un dato de última operación y NO una cotización ejecutable/depth.

### BYMA — naturaleza de la tasa de caución

Fuente oficial: `https://www.byma.com.ar/productos/productos-financieros/caucion`

BYMA define al colocador como quien entrega fondos al tomador y cobra capital más intereses. La normativa/manuales de caución de BYMA describen la tasa de caución como Tasa Nominal Anual (TNA) vencida y distinguen en pantalla tasa/monto tomador de tasa/monto colocador.

**Conclusión:** la tasa de mercado de caución se expresa como TNA. Esto respalda interpretar la **última tasa operada** de PPI como TNA porcentual. No demuestra por sí solo qué array genérico PPI (`bids` u `offers`) corresponde a la punta colocadora.

## 3. Matriz semántica canónica

| Campo / transformación | Estado | Uso permitido |
|---|---|---|
| `PPI current.price` -> última tasa operada % | VALIDADO | observación / reconciliación |
| última tasa operada % -> TNA porcentual de caución | VALIDADO | observación / histórico de última operación |
| `current.price` -> quote ejecutable EOD | BLOQUEADO | NO |
| `current.volume` -> capital disponible | RECHAZADO | NO; volumen acumulado no es profundidad ejecutable |
| `book.level.price` -> TNA % | PARCIAL / requiere snapshot activo | no alimentar sweep todavía |
| `book.level.quantity` -> principal ejecutable colocador | NO VALIDADO | HOLD |
| `book.bids` -> lado colocador | NO VALIDADO | HOLD |
| `book.offers` -> lado colocador | NO VALIDADO | HOLD |
| `book.date` sentinel dominical -> freshness | RECHAZADO | NO |
| `COLOCAR-CAUCION` como operación oficial PPI | VALIDADO | identidad semántica, no routing real |

## 4. Corrección de seguridad aplicada

El helper histórico `rc6_cauciones_contract.py` ya no puede:
- asumir que la punta colocadora es `bids`;
- convertir `price` a TNA sin prueba explícita;
- convertir `quantity` a principal ejecutable sin prueba explícita.

Los tres pasos fallan cerrado y exigen tokens de prueba semántica explícitos. El adaptador canónico `rc6_caucion_offer_adapter.py` además rechaza directamente estructuras PPI crudas (`current`, `book`, `bids`, `offers`, `price`, `volume`, `quantity`).

## 5. Evidencia todavía necesaria durante rueda activa

Para liberar A4/A5 se necesita una captura read-only estrecha durante rueda activa que incluya, por ticker:
- timestamp fresco del book;
- niveles numéricos de `bids` y `offers`;
- `current` contemporáneo sólo como control;
- una fuente PPI/BYMA explícita que permita etiquetar cuál punta representa la caución colocadora;
- confirmación de que la cantidad del nivel equivale al principal realmente disponible para esa punta;
- costos completos para el mismo principal y moneda por una fuente no transaccional/segura.

Si cualquiera de esas equivalencias sigue ambigua, el resultado obligatorio es `HOLD` y `can_simulate=0`.

## 6. Estado operativo

- `READY_PAPER` integral: **BLOQUEADO**.
- `can_simulate` CAUCIONES: **0/10** hasta nueva evidencia y recálculo.
- órdenes reales: **0**.
- no se inició/reinició ningún servicio por este trabajo.
- no se habilitó ningún producer/timer runtime.

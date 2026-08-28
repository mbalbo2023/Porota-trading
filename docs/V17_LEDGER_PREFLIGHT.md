# v17 — diagnóstico del ledger anterior, sin migración

Estado: paquete de inspección; **no es un instalador ni habilita producción**.
Revisión actual del lector: `v17-ledger-preflight-2`.
Requiere una ejecución del operador porque este entorno no tiene acceso SSH
al servidor. No repite el diagnóstico público de PPI ya recibido.

## Ejecución única

Subir por SFTP `porota_ledger_preflight_v17.zip` a `/tmp`, sin descomprimir.
En Termius ejecutar:

```bash
python3 /tmp/porota_ledger_preflight_v17.zip --host
```

Si ya se ejecutó la revisión 1, descargar la nueva revisión y sustituir sólo
el ZIP subido a `/tmp`; no cambiar ningún archivo de la base. El resultado
debe identificar `v17-ledger-preflight-2`, para no repetir el paquete anterior.

El JSON aparece en terminal y se envía al portapapeles mediante OSC52, si el
cliente lo permite. Pegar ese resultado para continuar con evidencia del
ledger real. No necesita claves, hash ni un comando extenso. Se conservan
el ZIP original y sus permisos. No usar sudo Python ni cambiar permisos si
el resultado indica una denegación: revisar ese bloqueo antes de continuar.

## Qué verifica

- Que `porota_trading_bot` y `porota_production_observer` estén detenidos y
  con `restart=no` al comenzar. Nunca los arranca ni los modifica.
- Usa la imagen local exacta del observador 16.3.5, por ID, sin descargarla.
  Crea un contenedor temporal propio, sin red, con usuario `botuser`, sin
  healthcheck y con entrada directa al lector Python; no ejecuta el motor.
- Sólo monta el paquete y `/opt/porota-trading/data/observer` en lectura.
  El subdirectorio permite leer la base junto a su WAL/SHM existente.
  No monta `.env`, secretos ni el directorio general de datos.
- Abre `/observer/observer_production.db` con SQLite `mode=ro`,
  `query_only=ON` y una transacción de lectura. No usa `immutable`, que
  podría omitir operaciones confirmadas aún presentes en el WAL.
- Examina exclusivamente las tablas `paper_positions`, `paper_fills`,
  `paper_spot_sales` y `paper_sale_receivables`, si existen. Contrasta salidas
  contra entradas almacenadas: identidad, cantidades, costos asignados,
  PnL, precio agregado y condiciones de liquidación declaradas.
- Informa filas huérfanas, recibos incompatibles y cierres simples sin recibo.
  Si faltan columnas monetarias legacy, proyecta ARS/BYMA sólo en memoria
  y lo declara explícitamente. Esa hipótesis **no prueba la moneda original**.
- No exporta IDs, símbolos, importes o `features_json`; devuelve conteos y
  hasta diez números de fila con códigos de error. No muestra excepciones
  con contenido de registros.
- Revisión 2: identifica etapa (`stage`), versión de Python/SQLite y códigos
  nativos de error, sin mostrar el mensaje SQL. Informa presencia/tamaño y
  resultado de apertura en lectura de la base, WAL, SHM y journal, con rutas
  fijas. Sólo lee los primeros 20 bytes del encabezado de la base para
  distinguir WAL/rollback; no exporta esos bytes ni lee payloads auxiliares.
  Esta fotografía de archivos es previa, no atómica con la transacción.
- Retira únicamente el contenedor que creó esta ejecución y la copia
  temporal del paquete. No borra/reutiliza un contenedor de igual nombre
  preexistente. Si falla la limpieza, lo informa sin declarar éxito.

## Estados y límites

| Estado | Interpretación |
|---|---|
| `OBSERVED_NO_DETECTED_INCONSISTENCIES` | No se detectaron problemas dentro de estos controles; no es certificación integral |
| `OBSERVED_REVIEW_REQUIRED` | Hay inconsistencias, campos legacy asumidos o recibos faltantes; revisar antes de migrar |
| `STOPPED` | Lectura/instalación/permisos/límites impidieron completar la inspección; no extrapolar los conteos parciales |

Siempre `promotion_allowed=false`, `database_modified=false` y
`migration_performed=false`. No certifica origen de entradas, compras,
comisiones históricas, saldos reales de PPI, cauciones, derivados, señales,
rentabilidad ni cambios coordinados que mantengan la aritmética.
Un ledger vacío tampoco prueba que exista historial recuperable.

Límites: 10.000 posiciones/recibos, 100.000 fills/ventas parciales, lector
acotado a 120 segundos, espera externa 150 segundos, memoria 256 MiB y una
CPU. Al superar límites se requiere planificar una auditoría por lotes,
no asumir un resultado aprobado. Si faltan permisos de lectura del WAL/SHM
en modo read-only, se detiene; no modifica permisos ni ignora el WAL.
La comprobación inicial no impide que un tercero arranque motores después.

## Paquete reproducible

`scripts/build_v17_ledger_preflight.py` empaqueta únicamente el lector y
cuatro módulos puros (`bs_instrument_contracts`, `cd_spot_ledger`,
`cf_sale_settlement`, `ak_byma_calendar`), más un manifiesto SHA256.
No incluye datos, credenciales, SDK de PPI ni `PaperStore` (su constructor
realiza migraciones). ZIP determinista; la creación no sobreescribe archivos.

Pruebas locales con bases temporales actuales/legacy, WAL, registros alterados,
límites y Docker simulado. La prueba standalone usa Python sin site-packages.
Esto no sustituye ejecutar el paquete en el servidor.

## Resultado recibido de la revisión 1

El operador entregó `v17-ledger-preflight-1`, generado el 28/08/2026 a las
18:37:58.404854 UTC y completado a las 18:37:58.625477 UTC. Estado `STOPPED`,
razón `OperationalError`, conteos vacíos. Evidencia del lanzador: ambos motores
detenidos antes de la lectura, misma imagen del observador 16.3.5, sin red,
directorio en lectura, sin credenciales/env montados y contenedor retirado.

Ese resultado **no prueba base vacía, corrupción, un problema de permisos ni
una causa WAL concreta**. El primer lector omitía el código SQLite y el punto
de fallo. La revisión 2 corrige esa insuficiencia del diagnóstico; no afirma
haber corregido la causa del servidor, que permanece sin determinar.

[SQLite documenta](https://www.sqlite.org/wal.html#read_only_databases) que una
base WAL puede necesitar los archivos auxiliares existentes y legibles para
leer desde un medio de sólo lectura. Es una hipótesis compatible, no un
diagnóstico confirmado de esta base. Los
[códigos nativos](https://www.sqlite.org/rescode.html) permiten distinguir
READONLY, CANTOPEN, BUSY/LOCKED, errores SQL y corrupción sin divulgar SQL.

No se añadieron reintentos, copia/recuperación de la base, `immutable`,
checkpoint, cambios de journal, montaje de escritura, cambio de usuario del
contenedor ni permisos nuevos. Si la revisión 2 confirma un impedimento de
acceso, se mantiene `STOPPED` y requiere una decisión explícita antes de
cualquier procedimiento distinto. No repetir la revisión 1.

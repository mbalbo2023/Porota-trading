# POROTA TRADING — CHECKPOINT PPI PRODUCTION AUTH FORENSICS — 2026-09-08

## Alcance
Forense read-only de la autenticación de PPI Producción usada por `PRODUCTION_PAPER`. No se realizaron órdenes, Budget, Confirm, Cancel ni cambios destructivos.

## Runtime observado
- HOST_HEAD: `c17b0d777ba49d88c53c5a7ed14218d8eaa94638`
- Observer: `porota_production_observer`
- Imagen: `porota-trading-bot:17.0.0-rc6`
- Running: true
- Restarts: 0
- `real_orders_sent=0` se mantiene como invariante.

## Fuente real de credenciales productivas
El observer productivo NO toma las credenciales PPI desde `.env`.
La fuente es `/opt/porota-trading/.secrets/ppi_production.json`, montada read-only dentro del contenedor en `/run/secrets/ppi_production.json`.
Campos esperados: `api_key`, `api_secret`.

## Evidencia del host
- Secret mode: `0400`
- Owner: `porotaadmin:porotaadmin`
- Secret mtime: `2026-08-27 03:31:34 UTC`
- Secret SHA256: `e6b140dcb91f6040574f556e711f78d40c51cf3965f61b4552b1f5d5306b9975`
- `.env` mode: `0600`
- `.env` mtime: `2026-08-29 20:50:27 UTC`
- `.env` SHA256: `23505cae2f1d59b843aefb186a59d06cdd3d3fadd94e64b05a4ad9be49f3d016`
- Mount del secreto: `RW=false`.

## Fingerprints no reversibles
No se registran valores secretos.
- `api_key`: len 28, FP12 SHA256 `44bd3f0a865d`
- `api_secret`: len 48, FP12 SHA256 `7ce81ffe57fa`

## Pruebas realizadas
### Pareja actualmente persistida
Un único login productivo read-only mediante el mismo `ProductionMarketReader` del runtime devolvió:
- `PPI_PRODUCTION_AUTH=RED`
- `Credenciales invalidas.`
- cero order routes.
- secret y `.env` con hashes idénticos antes/después.

### Candidata aportada por el operador
La cadena de 48 caracteres aportada como secret productivo coincide por longitud y fingerprint con el `api_secret` actualmente persistido. No se registra el valor.
Una api_key candidata alternativa fue probada únicamente en memoria con el `api_secret` actual y fue rechazada. El script terminó `FINAL=NO_CHANGE_CANDIDATE_REJECTED`; no modificó archivos.

## Forense de copias históricas
Se buscaron copias/candidatos de secretos PPI productivos en las rutas operativas auditadas del Droplet. No apareció una pareja histórica alternativa utilizable. El archivo persistido actual sigue siendo la única pareja productiva localizada.

## Código y contrato de login
El reader productivo usa `PPI(sandbox=False)` + `login_api(api_key, api_secret)` detrás de `bd_ppi_readonly_guard.py`, con host productivo `clientapi.portfoliopersonal.com`. El guard sólo permite login/refresco y endpoints GET de configuración/market data. El runtime HF6 aceptado el 2026-09-02 ya utilizaba esencialmente este mismo mecanismo, por lo que no hay evidencia de que NO-TRADE o un cambio reciente del flujo productivo haya causado el rechazo actual.

## Veredicto actual
`PPI_API_PRODUCTION_AUTH=RED`.
Las hipótesis más soportadas son:
1. credencial revocada/invalidada/rotada del lado PPI; o
2. existe una pareja productiva vigente más nueva que nunca fue persistida en `.secrets/ppi_production.json`.

No hay evidencia de:
- cambio reciente de `.env` como causa;
- modificación del secret durante las pruebas;
- relación causal con NO-TRADE;
- orden real enviada.

## Siguiente paso autorizado cuando aparezca la credencial vigente
1. probar la nueva pareja sólo en memoria y read-only;
2. exigir login GREEN + configuración/market-data GREEN;
3. crear backup local 0400 del secreto anterior;
4. actualizar atómicamente `ppi_production.json` sólo si el test es GREEN;
5. reiniciar únicamente observer;
6. postflight de PPI auth, marketdata, history, DB quick_check, observer health y `real_orders_sent=0`;
7. rollback automático si cualquier postflight falla.

## Regla Termius / accesibilidad reafirmada
Todo script manual debe poder ejecutarse con `bash archivo.sh` sin depender de bit ejecutable, usar `sudo -n`, guardar salida en `.txt`, producir resumen compacto e intentar clipboard OSC52.

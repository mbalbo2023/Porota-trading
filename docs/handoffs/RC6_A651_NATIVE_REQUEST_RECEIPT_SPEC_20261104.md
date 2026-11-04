# RC6 A651-R42 — PPI native request/response witness: OFFHOST only

**Fecha:** 2026-11-04 ART. **Estado del diseño:** DESARROLLADO sólo en branch
aislada; CI/GitHub/runtime requieren recibos independientes. Este documento
amplía [#909/R38](https://github.com/mbalbo2023/Porota-trading/issues/909#issuecomment-6637848216),
[#909/R39](https://github.com/mbalbo2023/Porota-trading/issues/909#issuecomment-6637954736)
y [#909/R41](https://github.com/mbalbo2023/Porota-trading/issues/909#issuecomment-6638400193).

## Identidad económica: lo que PPI realmente responde

En la [documentación REST oficial de PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/),
`MarketData/SearchInstrument` devuelve `ticker`, `type`, `market`, `currency`
y descripción; **no** devuelve `settlement`. `MarketData/Current` y
`MarketData/Book` reciben `Ticker,Type,Settlement` como parámetros GET,
pero el JSON de respuesta no reitera esos tres campos, ni moneda ni mercado.
Una respuesta a esa URL es una observación *de la consulta solicitada*;
**no es un campo proveedor `PPI_FIELD`**. Si hay varios siblings de un ticker/type
en markets/currencies diferentes, Book/Current por sí solos no distinguen cuál.

## Alcance del módulo R42

- `rc6_ppi_native_request_receipt_guard.py`: valida un recibo JSON hipotético
  firmado por **capturador** con key/release pins externos. Consume cuatro
  bodies privados **provistos por caller**, jamás red, credenciales, DB o host.
- `tests/test_rc6_a651_ppi_native_request_receipt_guard.py`: adversariales
  con respuestas PPI **sintéticas**. El positivo no es una captura de mercado.
- No hay integración con `bu_instrument_catalog.py`,
  `cr_contract_evidence_v2_mass_hf6.py`, `rc6_contract_bridge.py`, PAPER,
  scanners, PIT runtime ni SDK. No se habilita ningún instrumento.
- Salida máxima, incluso con capturer signature que la prueba verifica:
  `EVIDENCE_ONLY_NO_GO`, `can_simulate=false`, `instrument_ready=false`,
  `paper_fills_accredited=0`, `provider_signature_verified=false`,
  `budget_sidecar_independently_verified=false`,
  `decision_signature_verified=false`, `source_pit_verified=false`,
  `economic_contract_verified=false`.

## Esquema de recibo PROPOSED v1

`schema=rc6.ppi-native-request-causal-offhost.v1`;
`source=PPI_SDK_HTTPS_CAPTURE_NOT_PROVIDER_SIGNED`;
`issuer_role=RC6_PPI_READONLY_CAPTURE`;
`capture_id` UUID4. `expected_identity=[ticker,family,market,currency,settlement]`
representa **target solicitado** y no una respuesta PPI. Modo
`PRODUCTION_PAPER`, contadores de **esta captura** `orders_sent_by_capture=0`
y `writes_by_capture=0` no certifican el histórico `real_orders_sent=0`
del runtime.

**Versiones y hashes tienen tipos distintos:**

- `producer_git_sha`: **40** caracteres hexadecimales del commit Git.
- `artifact_digest_sha256_claim`: **64** caracteres hexadecimales, sólo
  claim hasta comparar contra un manifest/digest inmutable **independiente**.
- `signer_key_sha256`: 64 hex del hash de la clave pública Ed25519.
- `signature_base64`: firma del capturador sobre el JSON canónico (sin firma),
  no una firma PPI. Los tres pins del módulo permanecen `None` en esta fase.
  Nunca confundir `POROTA_BUILD_SHA` (Git 40) con digest SHA256 (64),
  ni construir un pin circular con la propia release de este verificador.

Cada entrada `search`, `current`, `book1`, `book2` exige:

| Campo | Significado |
|---|---|
| `method,url,http_status` | `GET`, dominio PPI HTTPS permitido y URI/query exacta; HTTP 200 |
| `sent_at_utc,received_at_utc` | timestamps aware UTC, secuencia comprobada |
| `body_sha256,body_bytes` | SHA del **cuerpo realmente recibido**, contrastado con los bytes privados suministrados |
| `lease.authority,instance_sha256,lease_id,state` | lease de presupuesto runtime declarada por capturador, distintos IDs y mismo sidecar |
| `lease.acquired_at_utc,started_at_utc,finished_at_utc` | aritmética causal y bounds del GET; no reemplaza lectura sidecar independiente |

`search` requiere sólo Ticker/Type en query, sin filtro de market que pueda
ocultar siblings. Exige una fila coincidente 4-key, sin `settlement` inventado;
la unicidad *dentro de un resultado Search* no implica que la API entregue
un inventario global completo. `current`/`book1`/`book2` requieren
Ticker/Type/Settlement exactos, mismo target, respuesta JSON bien formada
sin duplicate keys/NaN, body digests iguales, precio Current finito, Book
con `date`, `bids`, `offers`, `position` y price/quantity finitos, primera
profundidad positiva. Reloj nativo nunca posterior a observación ni >120s
obsoleto; cuatro capturas secuenciales dentro de diez minutos. BOOK1 debe
ser anterior a `decision_claim`, Book2 nativo estrictamente posterior
a `decision_known_at + latency`; ambas observaciones caen en misma sesión ART.

`decision_claim` contiene `decision_at_utc`, `decision_known_at_utc`,
`latency_ms`, `decision_sha256_claim`; **no** es una firma verificada ni
un COMMIT real. La aritmética Book1/Book2 es sólo estructura temporal.
La pieza aún no identifica el primer Book2 futuro entre *todos* los eventos
ni demuestra unidades negociables/fees/riesgo/EXIT. No convertirla en fill.

## Alcance de pruebas y bloqueos explícitos

Pruebas adversariales: ausencia de trust pin, firma rota, key ajena,
40-vs-64, identidad alterada, sibling market/currency, settlement `PPI_FIELD`
forjado en Search, endpoint host y query restringida/duplicada/alias,
IOL fuente primaria falsa, HTTP 403, digest/cuerpo manipulado, JSON
duplicado/NaN/HTML/deep nesting, tamaño excesivo, Current stale/futuro,
book sin profundidad o posición inválida, lease reutilizado/instance
distinta/reloj invertido, decisión Book2 anticipada, sesión ART cruzada.
**No hay GET PPI** en los tests. No llamar los resultados sintéticos `READY`.

Condiciones para considerar la futura integración:

1. #924 y ruta SDK legacy cerrados con cuota/EXIT global por **cada** GET;
   sidecar físico/leases corroborados independientemente.
2. Productor capturador firmado por clave protegida, commit SHA 40 y
   artifact digest SHA256 64 atestado contra manifest externo; recibo real
   con body/digest/URL/HTTP/reloj PPI y evidencia de Search completa.
3. Revisión de posible alias ticker/type en mercados y monedas; incertidumbre
   de unicidad falla cerrada, no inferir PPI market/currency de GET 3-key.
4. Decisión PAPER COMMIT firmada y conocida antes del **primer** Book2 futuro,
   journal privado íntegro, Book1/Book2 con relojes nativos y ART, contratos
   económicos (lotes, liquidación, tarifas, NAV especial FCI), riesgo y EXIT
   verificables **por instrumento**.
5. Nuevo WRITE_OWNER distinto para consumidores y revisión adversarial;
   Predeploy V2 exacto y único despliegue sólo tras GO consolidado.

**PPI Watch intocable. PAPER/SHADOW only, órdenes reales bloqueadas.
No merges product, host GET/changes ni Predeploy/Deploy en A651-R42.**

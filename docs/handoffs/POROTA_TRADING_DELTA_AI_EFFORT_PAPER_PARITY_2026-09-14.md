# Delta cross-chat: eficiencia, paridad PAPER y plan Wave A — 2026-09-14

**ID:** POROTA-DELTA-AI-EFFORT-PAPER-PARITY-20260914-01  
**Estado:** PENDING_INTEGRATION  
**Rama aislada:** `docs/ai-effort-and-paper-parity-20260914`  
**Base:** `ad0259c04c396f7fb40bbd06bab3b05f971298bc`  
**Baseline de código RC6:** `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`  
**Alcance:** política cross-chat y hallazgos de solo lectura. No modifica runtime, DB, importación, servicios ni órdenes.

## Regla solicitada: esfuerzo y contexto

Ajustar el esfuerzo del modelo a la complejidad, ambigüedad y riesgo: usar la menor cantidad de razonamiento, llamadas y tokens que complete la tarea con evidencia suficiente; aumentar profundidad y verificación cuando el cambio sea operativo, financiero, irreversible o haya discrepancias. La eficiencia nunca omite las lecturas obligatorias, pruebas proporcionales, evidencia ni restricciones del usuario. Agrupar lecturas independientes, ejecutar en paralelo sólo tareas independientes y aisladas, conservar un ledger/checkpoint compacto con decisiones, evidencias, incertidumbres y siguiente paso exacto, y no repetir trabajo ya cerrado. Si los últimos chats no son accesibles, decirlo y usar el repositorio como fuente verificable; no inventar memoria conversacional.

## Hallazgo: elegibilidad spot versus apertura PAPER

Auditoría estática del baseline RC6:

- `READY_PAPER_SPOT` es una puerta legacy de elegibilidad para el motor spot, no el readiness contractual integral de PPI.
- `bu_instrument_catalog.py` asigna a ciertas familias BYMA una convención de unidad con multiplicador/paso 1 aunque dichos términos no provengan explícitamente de la respuesta de búsqueda PPI.
- El observer entrega cotizaciones al broker PAPER; `be_paper_engine.py` puede aceptar `OPENED_SIMULATED` si se cumplen sus otros controles de identidad, precio/profundidad, frescura, sesión, caja, costos y riesgo. Los módulos nuevos de Contract Evidence/readiness no están conectados a esa ruta de apertura; terminan en `READY_PAPER_CANDIDATE`.
- `PAPER_ECONOMIC_GATE_MODE=SHADOW` permite registrar evaluaciones aunque el gate económico marque would-block. Esto explica evaluaciones y que el camino legacy pueda abrir PAPER; no prueba que haya ocurrido una apertura concreta.
- Los 246 del dashboard son pool elegible rotativo, no 246 posiciones ni prueba de fills: 55 ACCIONES + 191 CEDEARS. El máximo activo configurado se documentó como 20.
- La última matriz estricta registrada era 0/444 antes de fixes y no se recalculó. Resultado estricto actual: `NOT_VERIFIED`. Históricos: 239/246 con cobertura; siete CEDEARs tienen `HISTORY_GAP`.

### Regla de producto para paridad

En familias con órdenes PPI simulables, `READY_PAPER_SPOT` sólo permite evaluación de mercado/diagnóstico; no autoriza una apertura ni fill PAPER. Para iniciar una operación simulada se requiere `READY_PAPER_PPI` con identidad, datos/analytics requeridos, contrato explícito PPI, riesgo, frescura, costos, settlement/calendario y reglas de unidad/tick/step validadas. `SHADOW` puede comparar e informar would-block, pero no puede habilitar la apertura. Los flujos sin operación intradía conservan su state machine propia.

Esta es una política/documentación propuesta a partir de la aclaración expresa del usuario; su integración al runtime sigue pendiente. No se afirma que haya habido una violación de fill histórico hasta inspeccionar evidencia runtime autorizada. Sin inspeccionar DB/runtime, fills históricos: `NOT_VERIFIED`.

## 444 instrumentos: evidencia y alternativas

- PPI API de histórico: 1.960/1.960 tareas terminales, pero 679 resultados residuales de calidad. PPI Web residual: 642/642 terminales, 0 válidos. Corridas cerradas: no repetir scraping/ingesta masiva, no segundo writer ni retry ciego.
- Capturas contractuales recientes confirmaron llamadas a `InstrumentosOperables`, `CaucionesOperables` y `ConfiguracionOperatoriaSimplificada`; una fila AL30 y 120 Cauciones. No se observó `DatosTecnicos`; faltan términos explícitos en AL30 y semántica contractual completa de Cauciones. La captura de inventario expuso una ruta sensible en logs; corregir redacción antes de nuevas capturas.
- La API estructurada IOL se probó en instrumentos representativos y campos como cotizaciones, historia, analítica de renta fija, opciones y tasas de caución. No se probó cobertura completa ni identidad canónica de 444.
- IOL Web scraping quedó bloqueado por diseño hasta demostrar que un campo crítico falta en API pero aparece establemente en Web. No hay evidencia de scrape masivo de IOL. La cobertura completa de 444 vía IOL API sigue sin probarse.
- Las fuentes disponibles no documentan pruebas de proveedores de mercado terceros o alternativas de bulk feed. Quedan `NOT_RESEARCHED`, no son opciones validadas.
- El usuario solicitó revisar los últimos diez chats. En esta sesión no hay herramienta para leer conversaciones anteriores; GitHub es la fuente comprobable. No se pueden certificar intentos que sólo estén en transcripciones inaccesibles.

## Wave A — fundamentos encontrados, avance pendiente

Código reusable en RC6: `bs_instrument_contracts.py`, `cp_contract_evidence_v2_hf6.py`, `cq_family_contract_rules_hf6.py`, `cq_contract_readiness_hf6.py`, `rc6_ppi_contract_normalizer.py` y cliente IOL legacy `ak_iol_client.py`. No duplicar contratos/evidence store/readiness. Riesgos de diseño: aliases divergentes de familias, campos distintos `price_precision`/ `price_tick`, fuente prioritaria dependiente del campo/familia, y superficie account/portfolio amplia del cliente IOL.

Slices independientes a preparar sobre una rama de código RC6 aislada, con pruebas offline: resolver identidad canónica puro; vistas/adaptadores de provenance sobre Evidence v2; allowlist de IOL read-only; PPI contract DTO a partir de normalizador actual; evaluator unificado de gates; comparador de divergencias. Diseño de DB primero, sin migraciones.

La rama reciente `ops/rc6-contract-open-session-immediate-20260914` sólo contiene documentación y diagnósticos; no es base de código para Wave A. `main` también es docs/ops-only. El SHA RC6 `f8adec…` es el baseline de código. No se tocaron la rama del pipeline PPI, runtime ni DB. Implementación/CI quedan pendientes de fijar la rama/owner de integración y disponer de verificación de pruebas sobre la base de código.

## Estado de ejecución

- Código: no modificado en este delta.
- CI/tests: no ejecutados.
- Runtime/DB/importación/readiness recompute: no inspeccionados ni ejecutados.
- Capturas/ingesta masiva: no se repitieron.
- Órdenes reales: no habilitadas; invariantes PAPER/fail-closed se preservan.
- Checkpoint canónico: este archivo es delta aislado, no reemplaza ni sobrescribe el checkpoint activo.

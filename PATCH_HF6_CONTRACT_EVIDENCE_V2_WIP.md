# POROTA HF6 — Contract Evidence v2 / WIP

Estado: **ABIERTO — NO DESPLEGAR TODAVÍA**.

Esta rama se creó desde `patch/hf6-contract-evidence` para preparar el patch nocturno sin mover el tag `v17.0.0-rc3-hf6` y sin modificar el runtime activo.

## Invariantes

- `PRODUCTION_PAPER`
- `SIMULATED`
- PPI market data producción read-only
- PPI Orders bloqueado
- IA intradía OFF
- `real_orders_sent=0`
- ninguna familia se habilita por inferencia
- browser PPI nunca es canal de ejecución

## Evidencia nueva incorporada al diseño

- Chrome/Playwright autenticado con perfil persistente y dispositivo confiable.
- Navegación privada PPI validada.
- Rutas Operar reales: FCI local/exterior, Acciones, Acciones exterior, Bonos, Cauciones, Cedears, ETFs, Futuros `/Operar/260`, Letras, ON, Opciones, Licitaciones, Canjes.
- Endpoints de alto valor: `InstrumentosOperables`, `ConfiguracionOperatoriaSimplificada`, `CaucionesOperables`, `SubyacenteOpciones`, `Bono/<id>/DatosTecnicos` y cotizaciones relacionadas.
- AE38: ISIN, lámina mínima, emisión/vencimiento, moneda de emisión/pago, amortización, cupón/intereses, pagos por año, TIR, duration, paridad, intereses corridos, valor técnico/residual y flujos teóricos.
- ON: formulario confirma monto/títulos, plazo, precio por cada 100 títulos y precio mercado.
- Opciones: formulario confirma subyacente, serie, cantidad de lotes y precio por cada opción.

## Archivos WIP creados

- `co_contract_ingestion_policy_hf6.py`: TTL/cadencia por tipo de evidencia.
- `cp_contract_evidence_v2_hf6.py`: snapshots append-only, current pointer y change events.
- `cq_contract_readiness_hf6.py`: requisitos fail-closed por familia.
- `cr_pending_settlement_diagnostics_hf6.py`: diagnóstico read-only de ventas pendientes vs snapshot de equity.

## Política de ingesta propuesta

- Market data live: motor/API actual, sin browser.
- Operabilidad/catálogos: 15 min durante rueda.
- Cauciones y licitaciones abiertas: 5 min.
- Series opciones/futuros: 15 min.
- Contrato estático: preopen + post-cierre, TTL 1 día, hash versionado.
- Tarifario: checksum diario + auditoría mensual.
- Browser completo: semanal o ante cambio/conflicto.

## Pendientes antes de cerrar el patch

1. Captura EOD y confirmación `MARKET_CLOSED`.
2. Diagnóstico del panel de ventas pendientes de liquidación.
3. Recibir e incorporar auditoría externa pendiente.
4. Implementar collector structured-first y normalizadores por familia.
5. Integrar Contract Evidence v2 con scheduler.
6. Integrar readiness en Universo Operativo/dashboard.
7. Implementar/validar simuladores especializados antes de promover familias.
8. Agregar tests y fixtures sanitizados.
9. Build, smoke, DB quick_check, invariantes y rollback.
10. Definir exactamente qué familias quedan READY_PAPER en este patch; las incompletas quedan observables/HOLD.

## Regla de cierre

Este WIP **no se considera paquete final** hasta incorporar la auditoría externa. No crear release/tag nuevo ni desplegar automáticamente desde esta rama.

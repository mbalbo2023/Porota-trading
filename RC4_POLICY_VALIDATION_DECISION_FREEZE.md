# RC4 — Policy validation decision freeze

Fecha: 2026-09-03

Estado: **NO IMPLEMENTAR cambios de autoridad SHADOW/BINDING, ATR, stop, target ni sensibilidad hasta autorización explícita.**

El runtime canónico actual es `PRODUCTION_PAPER`: usa datos reales de la API productiva de PPI, ejecuta fills simulados y mantiene `real_orders=0`. No debe llamarse `PPI Sandbox`, porque el sandbox de PPI es otro entorno que no está siendo utilizado.

## Hallazgos que exigen resolución antes de implementar

1. `ck_policy_gate_hf6.py` sólo produce bloqueadores cuando el modo es `BINDING`. En `SHADOW` retorna cadena vacía, por lo que no genera por sí mismo el contrafáctico "would block" que el documento de exploración requiere.
2. El ejemplo de integración del módulo usa `self.store.empirical_samples()`, `self.store.breadth_snapshot()` y `self.store.sector_snapshot()`. Esos métodos no existen en candidate1 auditado.
3. El ejemplo usa `q.contract.sector`, pero `InstrumentContract` de candidate1 no posee campo `sector`.
4. El documento/auditoría menciona ATR en `bl_candle_engine`, pero candidate1 exacto no expone función ATR en ese módulo. Hay lógica ATR en otros componentes; debe elegirse una autoridad única antes de conectar barreras dinámicas.
5. En `PRODUCTION_PAPER` no hay fills reales del broker. Por lo tanto no existe un arancel efectivamente cobrado por PPI para cada fill simulado. Puede compararse contra tarifario/estimador oficial, pero eso no equivale a reconciliar costo cobrado real.
6. El documento nuevo propone comparar semanas con configuraciones diferentes. Eso puede confundir efecto de configuración con cambio de régimen. RC4 debe preferir contrafácticos paralelos/replay sobre el mismo flujo de datos y versionar cada cohorte/configuración.

## Principio de diseño propuesto

Separar **medición** de **autoridad**:

- cada política debe evaluarse siempre y persistir `PASS/WOULD_BLOCK/INSUFFICIENT_DATA`;
- `SHADOW` significa: calcula y registra, nunca veta;
- `BINDING_PAPER` significa: calcula, registra y puede vetar sólo una apertura simulada;
- ninguna de las dos puede enviar órdenes reales;
- todos los veredictos deben quedar asociados a `strategy_version`, `config_hash`, familia, moneda, régimen y timestamp;
- antes de pasar una política a BINDING se exige evidencia reproducible y decisión explícita documentada.

## RC4 que sí puede continuar mientras esta decisión está congelada

- `/vivo` operativo con operaciones primero, drill-down, P&L y timestamp;
- Scalping top-level y resumen en `/vivo`;
- Contract Evidence + scraping/XHR scheduler;
- Históricos 0/823 y conexión History Store v2;
- Scheduler multi-fuente y estados explicados;
- Salud/SRE con causa de amarillos;
- Introspección con freshness;
- Logs del Bot descargables;
- Backups como matriz de cobertura;
- Storage Lifecycle audit-only;
- inventario de módulos huérfanos/deprecados;
- respuesta exhaustiva a auditoría antes del deploy.

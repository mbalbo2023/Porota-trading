# Delta aislado: evidencia contractual y redacción de logs — 2026-09-14

Estado: PENDING_INTEGRATION  
Repositorio: mbalbo2023/Porota-trading  
Base de revisión: `ad0259c04c396f7fb40bbd06bab3b05f971298bc`  
Rama aislada: `docs/contract-probe-log-redaction-20260914`

Este delta conserva los resultados de las capturas recientes y el hallazgo de privacidad sin modificar la rama operativa ni importar datos al runtime. No contiene identificadores de cuenta ni rutas crudas de cuenta.

## Evidencia observada

- Run `34865060794`, job `104046703322`: inventario contractual de red completado en modo autenticado de solo lectura. Se observaron 6/6 rutas permitidas, 154 registros GET first-party y 28 registros JSON. Las solicitudes no-read fueron bloqueadas por diseño. No hubo importación a DB, reinicio de servicios ni órdenes reales.
- Run `34865452877`, job `104048028211`: captura contractual dirigida completada; respuestas de `InstrumentosOperables`, `CaucionesOperables` y `ConfiguracionOperatoriaSimplificada`. AL30: una fila; Cauciones: 120 filas. `DatosTecnicos` no apareció. Los campos `quantity_step` y `price_tick` siguen ausentes; no inferirlos de la precisión decimal.
- Run `34865706726`, job `104048898315`: diagnóstico UI de AL30 llegó a `/Operar/Bonos`, pero no identificó un selector único; `selected_input=null`. No se escribió AL30, no se hizo click en una opción ni se completaron cantidad/precio o controles de orden. Solo se observó `ConfiguracionOperatoriaSimplificada`; `DatosTecnicos` explícito: NO.
- Run `34865900760`, job `104049542542`: se encontraron dos inputs React Select candidatos sin etiquetas suficientes para atribuir de forma segura cuál es el selector de instrumento. El probe se detuvo sin adivinar.

## Hallazgo de privacidad y corrección requerida

El inventario de red registró en la salida una ruta bajo un endpoint de performance de cartera con un segmento numérico que puede identificar una cuenta. El sanitizador actual de `ops/rc6_ppi_contract_network_inventory_20260914.py` conserva el path completo al registrar host y ruta; por tanto, la salida no está completamente saneada. Este delta omite el valor y cualquier ruta cruda.

Antes de una captura futura, corregir la sanitización en captura y resumen: redactar segmentos de cuenta, o excluir rutas account-scoped; aplicar la regla también a listas de solicitudes bloqueadas. Revisar pruebas con datos sintéticos para confirmar que ningún identificador ni path sensible llegue a logs. Los logs de la ejecución existente no deben copiarse a nuevos artefactos.

## Estado contractual y operativo

- AL30 permanece HOLD: falta evidencia PPI inequívoca de settlement, nominal/mínimo/paso/tick, disponibilidad y horario; `DatosTecnicos` no fue observado.
- Cauciones permanece `NEEDS_CAUCION_TERMS`: las 120 filas prueban presencia del catálogo, pero falta mapear por moneda/lado/plazo y verificar semántica de tasa, mínimos/máximos, paso de importe, liquidación, colateral/aforo, comisiones, horarios y disponibilidad.
- Los runs finalizaron exitosamente como diagnósticos seguros; éxito del workflow no equivale a contrato completo, importación, readiness ni elegibilidad de órdenes.
- Seguridad observada: cero órdenes reales, cero importaciones y cero reinicios en las ejecuciones citadas.

## Siguientes pasos preservados

1. Resolver primero la exposición de paths en logs y añadir verificación de redacción con valores sintéticos.
2. Para AL30, inspeccionar labels y contenedor DOM para identificar un único selector por evidencia. Solo entonces repetir una interacción UI de lectura que pueda disparar el GET técnico; sin completar cantidad/precio, confirmar orden ni usar POST.
3. Si el endpoint técnico no está expuesto, registrar el límite y mantener HOLD; usar IOL y fuentes oficiales solo para analytics/términos correspondientes, sin atribuirles autoridad de contrato PPI.
4. Mapear el catálogo de Cauciones y verificar productores runtime y `ObligationSnapshot` existentes antes de cualquier prueba E2E PAPER.
5. Mantener este delta pendiente de integración. No editar checkpoint canónico, no importar ni recalcular readiness, y no iniciar nuevas capturas masivas.

## Relación con continuidad

Este delta complementa el checkpoint cross-chat del 2026-09-14 y el tracker de waves de la rama base. No reemplaza la revisión de owner/integración de código ni autoriza cambios en ramas operativas.

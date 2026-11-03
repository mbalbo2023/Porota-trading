# Porota Trading 17.0.0-rc3-hf4

Fecha operativa: 1 de septiembre de 2026.

HF4 conserva exclusivamente `PRODUCTION_PAPER`. PPI Orders continúa bloqueado,
la IA intradiaria continúa apagada y `real_orders_sent` debe permanecer en cero.

## Bloqueos corregidos

- Modela la bonificación intradiaria publicada por PPI únicamente cuando el
  contrato obliga al cierre en la misma jornada. Conserva derechos de mercado
  e impuestos aplicables y acredita la bonificación estimada en el ledger.
- Mantiene el costo completo para caja, dimensionamiento conservador, posiciones
  overnight o cualquier operación sin elegibilidad demostrable.
- Las familias que requieren un ciclo de derivados dejan de provocar
  `DATA_ERROR`: se registran como `HOLD` explicado.
- El lector de salidas actualiza su heartbeat durante el recorrido de posiciones
  y reduce su pausa, evitando bloqueos falsos por `EXIT_READER_STALE`.
- Registra el propietario de cada login PPI para confirmar o refutar la hipótesis
  de invalidación cruzada sin apagar el scanner de scalping.
- Bloquea los endpoints JSON legacy en `PRODUCTION_PAPER` para que no publiquen
  posiciones o decisiones pertenecientes a otra base.
- Históricos distingue fecha del dato bursátil, última corrida y último éxito de
  ingesta.
- El dashboard deja de refrescar el documento completo y ofrece un botón
  pronunciable “Actualizar página”.
- Añade avisos idempotentes de transición PREOPEN, OPEN y CLOSED.
- Añade introspección funcional horaria y pruebas de composición desplegada.

## Decisiones expresamente no incluidas

- No se descongelan límites de riesgo HF3.
- No se reduce el umbral económico para fabricar operaciones.
- No se apaga scalping sin evidencia de colisión de sesiones.
- No se habilitan fills de scalping, derivados, futuros ni cauciones sin sus
  contratos y fuentes verificadas.
- No se cambia el objetivo de 5 % ni la lógica estratégica antes de reunir datos.
- No se modifica retrospectivamente ninguna operación HF3.
- No se realiza backup previo automático.

## Criterios de activación

1. Suite completa verde dentro de la imagen.
2. Prueba de composición con `BINDING` y reward/risk neto mínimo 1,20.
3. Imagen y `/health` reportan `17.0.0-rc3-hf4`.
4. SQLite `quick_check=ok`, posiciones y ledger coherentes.
5. `real_orders_sent=0` y PPI Orders bloqueado.
6. Introspección y timer de preapertura activos.

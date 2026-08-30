# Porota Trading 17.0.0 RC3 - Hotfix HF1

## Alcance

Este candidato es exclusivamente `PRODUCTION_PAPER`. Consume datos de mercado
en modo de sólo lectura y registra fills simulados. No habilita, confirma,
modifica ni cancela órdenes reales.

## Cambios operativos

- IA fuera del circuito intradiario: no se inicializa ni se consulta Gemini.
- El selector operativo tampoco exige ni inyecta Gemini al iniciar simulación;
  Telegram y el estado de modo informan decisiones determinísticas en Python.
- Decisión reproducible: señal, economía, riesgo, patrimonio y liquidez en Python.
- Aprendizaje conservado: features, gates, fills, PnL y lecciones siguen persistidos.
- Umbral de score congelado por versión; se elimina la adaptación procíclica.
- Ventana activa de 6 muestras en 90 minutos con foco estable y rotación.
- Frescura separada: book/recepción 120 segundos y último negocio 900 segundos.
- Sizing sobre el menor entre capital inicial y último equity observado.
- Relogin PPI permitido sólo después de un cooldown de 900 segundos.
- Login, catálogo e históricos continúan fuera de rueda; current/book no.
- Dashboard sin portón IA y con Pulso Porota de líderes.
- Corrección del PnL informativo de cauciones con costos pagados upfront.
- Identidad única de imagen y aplicación: `17.0.0-rc3-hf1`.

## Configuración recomendada

```dotenv
PAPER_AI_GATE_MODE=OFF
PAPER_ECONOMIC_GATE_MODE=SHADOW
PAPER_SIGNAL_MIN_SAMPLES=6
PAPER_SIGNAL_WINDOW_MINUTES=90
PAPER_BOOK_MAX_AGE_SECONDS=120
PAPER_TRADE_MAX_AGE_SECONDS=900
PAPER_SCORE_THRESHOLD=0.62
```

`PAPER_ECONOMIC_GATE_MODE=SHADOW` es deliberado para la primera salida:
recolecta contrafactuales sin presentar el rendimiento como validado. No debe
promoverse a dinero real. El paso a `BINDING` requiere reconciliar la
bonificación intradiaria del broker y validar señal/salida con replay y
walk-forward.

## Criterio de salida

El hotfix puede desplegarse para observación y simulación si la suite completa,
el preflight del servidor, el backup y el restore test terminan en verde. No se
autoriza producción con dinero real ni se afirma rentabilidad.

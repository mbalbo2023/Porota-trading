# POROTA TRADING RC6 — CHECKPOINT F-01 Y PENDIENTES
Fecha: 2026-09-12
Rama: fix/rc6-f01-only-pending-rest-20260912
Base verificada: 2bee4ca94a6f0bb71b02572ccb6aa0141e7cfa7d

## Estado ejecutivo
- F-01 (paridad del modelo de salida PAPER/backtest): **CORREGIDO EN ESTA RAMA**.
- F-02 a F-10: **PENDIENTES DE INTEGRACIÓN, REVISIÓN O RESOLUCIÓN**, detallados abajo.
- Runtime productivo: **SIN CAMBIOS / SIN DEPLOY**.
- Modo certificado preservado: PRODUCTION_PAPER / EXECUTION=SIMULATED.
- Órdenes reales: bloqueadas; no se ejecutaron operaciones.

## F-01 corregido
- Se agregó bk_exit_model.py con el modelo canónico versionado PAPER_FIXED_PERCENT_V1.
- PaperBroker aplica la función común en diagnóstico económico y al crear stop/target.
- El default PAPER se alinea con parámetros RC6: stop 2%, objetivo 5%.
- El backtest legado retirado, si se invoca internamente, usa la misma geometría fija; no se presenta como validador/promotor.
- Los candidatos ATR quedan declarados no comparables con PAPER y no promovibles.
- Se persisten modelo y porcentajes efectivos en features de la posición.
- Hay pruebas de fórmula, defaults, reporte de modelo y aislamiento del candidato ATR.
- Regresiones focalizadas y compilación reportadas GREEN en la rama F-01 original. No se ejecutó deploy.

## Pendientes del repositorio
Los siguientes hallazgos quedan expresamente fuera del alcance de esta rama y no deben considerarse cerrados ni desplegados:

- F-02 — identidad completa en latest_quote: pendiente de integración/revisión.
- F-03 — estado fail-closed persistido ante errores SQLite en riesgo diario: pendiente de integración/revisión.
- F-04 — control del POST de estimación IOL mediante opt-in: pendiente de integración/revisión; IOL sigue desactivado por defecto.
- F-05 — lock reproducible de dependencias: pendiente de resolución en Python 3.11/Docker; no generar pins sin validar el entorno objetivo.
- F-06 — claves duplicadas en configuración de entorno/dashboard: pendiente de integración/revisión.
- F-07 — observabilidad de fallbacks relevantes: pendiente.
- F-08 — accesibilidad semántica del dashboard: pendiente.
- F-09 — analítica de causas secundarias bajo precedencia EOD: pendiente de decisión/producto; la precedencia actual se conserva.
- F-10 — reducción/gobierno de workflows: pendiente de revisión.

Nota de trazabilidad: existe otra rama de trabajo con propuestas/cambios para algunos de F-02/F-03/F-04/F-06. No se incorpora a esta rama F-01 y debe revisarse por separado. No se ha eliminado ninguna rama ni commit.

## Validación y seguridad
- F-01: pruebas focalizadas de paridad, take-profit, supervisor, backtest e integración PAPER reportadas GREEN.
- Compilación de los módulos F-01: GREEN.
- Este checkpoint no certifica ni sustituye una nueva prueba del contenedor Docker Python 3.11 ni de producción.
- No hubo cambios en host, credenciales, datos financieros ni políticas de ejecución.
- No hay autorización de trading real; conservar PRODUCTION_PAPER / SIMULATED y REAL_ORDER_CAPABILITY=BLOCKED.

## Próximo paso
Revisión independiente del diff F-01 en esta rama y CI completa sobre el entorno objetivo. Resolver los pendientes F-02 a F-10 de forma separada, actualizar este checkpoint tras cada aceptación y no desplegar sin aprobación y post-deploy certification.

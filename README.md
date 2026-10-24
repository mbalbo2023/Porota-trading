# Porota Trading RC6

Sistema de observación SHADOW y simulación PAPER para el mercado argentino. **Las órdenes reales permanecen bloqueadas**; una pantalla de catálogo, un estado READY o una cotización no autorizan una apertura PAPER ni una operación real. PPI es la identidad primaria; IOL y otras fuentes se usan sólo con su procedencia y alcance verificados.

## Estado y evidencia

La fuente de seguimiento es [#732](https://github.com/mbalbo2023/Porota-trading/issues/732). Allí constan el SHA de producto, el último Deploy V2, los alcances CLOSED/OPEN, las autorrectificaciones y la aprobación requerida antes del próximo predeploy o deploy. Un comentario o este README documenta un estado; el código, las pruebas, Actions, el artefacto y el runtime se verifican por separado. No llamar GREEN a una fase sin su evidencia.

Las diez familias solicitadas para evaluación PAPER son ACCIONES, CEDEARS, ETFS, BONOS, LETRAS, OBLIGACIONES, OPCIONES, FUTUROS, CAUCIONES y FCI. Su visualización y catálogo no demuestran estrategia, contrato, fuente, riesgo, admisión y fill simulados para cada instrumento. El interlock global de preopen/NO-GO se sigue en [#61](https://github.com/mbalbo2023/Porota-trading/issues/61).

## Panel y operación

El dashboard protegido organiza 52 vistas canónicas en Inicio, En Vivo, Trading, Universo, Instrumentos, Riesgo, Analítica y Sistema. El mapa efectivo está en [rc6_trader_dashboard/navigation.py](rc6_trader_dashboard/navigation.py); los alias antiguos pueden resolver a vistas nuevas y no representan operaciones adicionales. Los registros y sus detalles indican fuente, frescura y alcance cuando la evidencia existe. **NO_VERIFICADO** y **SHADOW** no se convierten en ejecución PAPER.

La promoción de una versión usa exclusivamente los workflows controlados [Predeploy V2](.github/workflows/porota-predeploy-v2.yml) y [Deploy V2](.github/workflows/porota-deploy-v2-promote.yml), con artefacto congelado, ownership y autorización expresa del alcance. No usar `docker compose up -d` de una guía histórica como procedimiento de promoción. No tocar PPI Watch ni ejecutar estrés en el Droplet por una auditoría RC6.

## Continuidad

- [#485](https://github.com/mbalbo2023/Porota-trading/issues/485): inventario, retención y gobierno de PR.
- [#624](https://github.com/mbalbo2023/Porota-trading/issues/624): SHADOW, seguridad de promoción e infraestructura.
- [#478](https://github.com/mbalbo2023/Porota-trading/issues/478): SQLite, retención y recuperación con diagnóstico causal.
- [AGENTS.md](AGENTS.md): reglas de trabajo del repositorio.

Los secretos pertenecen a los mecanismos seguros de entorno y Actions; no se publican en el repositorio, los logs ni los artefactos de auditoría.

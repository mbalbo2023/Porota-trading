# RC6 A178 — A175 transition routing, temporal drift and preflight regression

**Fecha:** 29-Oct-2026 ART. **Estado en este corte:** `NO_GO_PENDING_EXPLICIT_REARM`, PAPER/SHADOW, `real_orders_sent=0`. Prohibidos órdenes reales, rollback, estrés host E09/E10 y tocar PPI Watch. El presente documento registra la RCA; no autoriza una promoción.

## Pruebas observadas antes de tocar el Droplet

- Predeploy V2 [#40882743065](https://github.com/mbalbo2023/Porota-trading/actions/runs/40882743065) **SUCCESS** del SHA `d2bff7c6b16df130adb9f81f86fecea67a22cdaa`, tree `17046358fe78b64d4ec1d8cee3c2f79f1c6df605`, ZIP `12902527167`, digest `sha256:1b3bd6ba0d4bca81e806a1a23434c2e519be6c6effa92e00cc7f506cff1bbb80`. Esta certificación del artifact se **invalida para promoción** al descubrirse wiring faltante que requiere nuevo SHA.
- Preflight read-only [#40886293672](https://github.com/mbalbo2023/Porota-trading/actions/runs/40886293672): RED por montar asignaciones de stdin *antes* de un `from __future__ import annotations`. El script de host no llegó a ejecutar. Solución: ensamblar tras el future y exigir `compile()` del script completo en el runner y la fixture, nunca sólo el archivo plantilla.
- Preflight read-only [#40886669772](https://github.com/mbalbo2023/Porota-trading/actions/runs/40886669772): RED por `QUIESCENCE_A175_RUNNING_BOOK_CHANGED`.
- Preflight read-only [#40887068433](https://github.com/mbalbo2023/Porota-trading/actions/runs/40887068433): RED esperado con diagnóstico preciso: el origen A175 sigue instalado, `flat_book=VERIFIED`, `RUNNING/MARKET_OPEN`, `real_orders_sent=0`, `critical_capability=issues_only`, `source_is_a175=true`; el ingreso cerrado exige `WAITING_MARKET/MARKET_CLOSED`. El guard falló antes de apagar servicios, modificar DB, limpiar Docker o mover una imagen.

## Omisión WIP confirmada: tests de texto no comprobaron la ejecución

La prueba antigua `tests/test_rc6_a176_a175_workflow.py` verificaba que el YAML **contuviera** rutas A175, pero no ejecutaba sus destinos. Por tanto, un Predeploy global GREEN no capturaba las siguientes desconexiones:

| Etapa | Workflow previo | Receptor Python previo | Falla cierta de continuar |
| --- | --- | --- | --- |
| Suspender | `RC6_QUIESCENCE_MODE=CLOSED_A175_PENDING_FIX_FORWARD` | `scheduler_suspend.run('suspend')` no seleccionaba marker A175 | Elegía otra rama histórica; no adoptaba A175. |
| Drenar e instalar | `--adopt-a175-running-pending` | `rc6_deploy_quiescence.main` no declaraba ese argumento ni `run(...allow_a175...)` | `argparse` rechazaba antes de comenzar. |
| Imagen congelada | `image-intent` de candidato A175 sucesor | Gate de intención sólo admitía A143/A171/A174/rescue | `QUIESCENCE_RESCUE_IMAGE_INTENT_STATE_RED`. |
| Fallo de sucesor | `recover` | No existía rama `a175_pending_adopted` | Riesgo de recuperación genérica inadecuada. |
| Cleanup de timers | `recover-a175-pending` | `scheduler_suspend.main` no reconocía esa acción | `argparse` rechazaba la contención después del fallo. |

**Causa raíz de ingeniería:** los contratos entre YAML, parser, motor de quiescencia, scheduler y recuperación evolucionaron por incorporación de literales, sin una *prueba ejecutable* completa de la transición antigua → nueva. Se validaba cada componente por separado, pero no se verificaba la coincidencia de todos los mensajes de entrada y estados al cruzar la frontera. Este caso explica por qué varios intentos sucesivos podían cruzar un guard para descubrir una falla de principiante en el paso siguiente.

## Fix-forward bajo desarrollo A178

1. Routear marker A175 a una adopción scheduler específica y de solo lectura en el suspend: SHA de recibo anterior, 16 timers, 14 unidades quietas, preopen deshabilitado, IOL guard preservado, PPI Watch snapshot inalterado, A176 NO_GO actual.
2. Exigir `--adopt-a175-running-pending` aislado en CLI y ejecutarlo en drain/install con prueba A175 instalado, fuentes, marker, tag/ID Docker, libros PAPER planos y stop limpio. Nunca volver a iniciar un contenedor A175 `DEPLOYED_VALIDATION_PENDING` como si fuera validado.
3. Dar acceso a A175 al gate de `image-intent`; registrar nueva imagen sólo contra el manifest congelado.
4. Añadir recuperación A175 exclusiva para preinstall (`PRIOR_A175_UNVALIDATED_HELD_STOPPED`) y postinstall (`FIX_FORWARD_VALIDATION_REQUIRED` con nuevo runtime documentado o `RECOVERY_UNVERIFIED`). Conservar timers en hold; nunca inferir runtime GREEN.
5. Implementar `recover-a175-pending` en el scheduler con contraste exacto entre recibos, Docker y PAPER/0. Todas las verificaciones de host que se han ejecutado hasta aquí fueron READ_ONLY.
6. Pruebas ejecutables (`tests/test_rc6_a178_a175_wiring.py`) que llaman realmente a `scheduler.run`, `quiescence.run`, los parsers, image-intent, recovery y negativas de alteración de recibos, modo incompatible y órdenes reales. No basta `assert 'flag' in yaml`.

## Gate de operación y pendiente real

La ruta A175 permanece **deliberadamente cerrada a una sesión `RUNNING/MARKET_OPEN`**; no se relaja para hacer pasar el preflight. Antes de un Deploy canónico debe probarse una ventana de origen *real* que satisfaga el contrato, o desarrollar de manera separada una transición intradiaria explícitamente autorizada y totalmente testeada, **sin reutilizar el artifact anterior**. Tras cualquier cambio del candidato: nuevo SHA/tree, nuevo Predeploy V2 integral y artifact/digest; preflight read-only GREEN exacto, autorizaciones literales nuevas #624, merge de dos padres, Deploy V2 y evidencia DB/logs/runtime para `VALIDATED_RUNTIME`. `NO_GO` hasta rearm expresamente ordenado.

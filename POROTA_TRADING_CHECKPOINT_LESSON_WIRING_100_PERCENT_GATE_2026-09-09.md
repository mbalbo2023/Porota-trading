# POROTA TRADING — CHECKPOINT LECCIÓN APRENDIDA: WIRING 100% OBLIGATORIO

Fecha: 2026-09-09
Estado: CANÓNICO / OBLIGATORIO

## Incidente que origina esta regla

Después del deploy convergido de Waves 6–8, CI, imports y postflight técnico reportaron GREEN, pero la verificación visual del operador detectó que mejoras comprometidas de UX no estaban realmente conectadas en el dashboard vivo: no aparecía el menú superior de Riesgo y el wiring RC6 de tablas tampoco estaba instalado. La auditoría live confirmó `HAS_RISK_TOP_NAV=False`, `HAS_EQ_INSTALL_IN_BG=False`, `HAS_ER_INSTALL_IN_BG=False` y ausencia del CSS/script RC6 esperado.

Conclusión: **módulo presente + import exitoso + CI GREEN + contenedor sano NO equivalen a feature desplegada ni usable.**

## Regla absoluta nueva

Ninguna Wave, feature, hotfix o deploy de POROTA TRADING puede declararse `GREEN`, `CLOSED`, `READY_FOR_GO_LIVE` ni "100% desplegado" hasta demostrar **wiring end-to-end en runtime**.

El gate obligatorio debe verificar, según corresponda:

1. El código esperado está en el SHA realmente desplegado.
2. El módulo se importa correctamente dentro del contenedor correcto.
3. El módulo está **instalado/registrado/invocado por el entrypoint vivo**, no solamente presente en el filesystem.
4. Las rutas/endpoints esperadas están registradas y responden en el runtime real.
5. Los menús/enlaces esperados aparecen en HTML renderizado real.
6. CSS/JS/componentes de presentación comprometidos aparecen realmente en el HTML/runtime y afectan la vista correcta.
7. Las tablas, navegación, drill-down, semáforos, timestamps y demás UX comprometida se validan por contenido renderizado, no por existencia de archivos.
8. Los schedulers/timers/jobs comprometidos se prueban por wiring + ejecución/persistencia observable, no solamente por definición.
9. Los feeds/APIs se prueban por wiring, freshness y persistencia, no solamente por cliente/import.
10. Las reglas de riesgo se prueban en el call graph/runtime binding real y no sólo como funciones disponibles.
11. Se hace regresión cross-wave: el deploy de una Wave posterior no puede desconectar una anterior.
12. Se valida la experiencia real esperada para Samsung/Android/Voice Access en todo cambio de dashboard.
13. `PRODUCTION_PAPER`, `real_orders_sent=0`, no order routes, DB `quick_check=ok`, observer/dashboard sanos y safety gates siguen siendo obligatorios.

## Definición de GREEN a partir de ahora

`GREEN = CODE + TESTS + INTEGRATION + DEPLOY + LIVE_WIRING + RENDER/ROUTES + DATA/FRESHNESS + CROSS-WAVE REGRESSION + SAFETY`.

Si cualquiera de esos términos no está probado, el estado máximo permitido es `YELLOW / NOT_PROVEN`.

## Aplicación inmediata

Se ordena una investigación completa Waves 1–8 contra el runtime desplegado, con matriz por Wave de:
- feature comprometida;
- archivo/módulo;
- entrypoint/wiring;
- ruta/menu/UI;
- scheduler/feed/persistencia si aplica;
- prueba runtime;
- resultado PASS/FAIL;
- corrección necesaria;
- revalidación postdeploy.

Wave8 queda reabierta hasta corregir y demostrar live wiring de UX/Riesgo/tablas. Cualquier otro gap encontrado en Waves 1–7 también reabre automáticamente la Wave correspondiente.

## Regla operativa

La investigación, corrección, tests y materialización se paralelizan por defecto. Los deploys runtime se serializan sólo cuando comparten estado mutable. No se requiere nueva autorización del usuario para safe fixes/deploys GREEN. Nunca rollback automático.

# RC6 A174 — selección de continuación A171 en scheduler (28 Oct 2026)

## Incidente y alcance

Deploy V2 canónico #40781245319, merge `690a5f460d09f7da11261fc0c8a3a30df4a93d79`, candidato A172 `a457159dc1c412b3dbbe80cde89f33054a0da96f`, Predeploy #40777333679 GREEN (5.384/5.384, artifact 12857425803) y preflight #40780727921 GREEN. El host pasó identidad 594077619/nyc1, aprobaciones literales #624, prueba A171 RUNNING/PAPER/0, fingerprint anterior y disco (7.310.491.648 B libres; 4.058.014.130 B requeridos). A las 22:38:04 UTC / 19:38:04 ART, la fase `Suspend versioned RC6 schedulers` terminó RED con `QUIESCENCE_A143_STOP_SOURCE_CHANGED`. No se transfirió el artefacto ni se cargó una imagen A172.

## RCA

La clasificación anterior ya había reconocido correctamente `CLOSED_A171_PENDING_FIX_FORWARD`. El dispatcher del scheduler, sin embargo, importaba `scripts.rc6_deploy_quiescence` desde `/opt/porota-trading` para decidir entre A171 y A169. Antes de la instalación, esa ruta es el **código A171 instalado**; carece de la nueva constante `A171_FAILED_CANDIDATE`. El `getattr` falso desviaba el flujo a `adopt_a169_stopped_hold`, que intentaba probar una fuente A143 detenida aunque los IDs A171 estaban RUNNING. El rechazo fue correcto para esa ruta equivocada. La falla es de selección de helper en una frontera de versiones, no de escasez de disco, OAuth o estado PAPER.

## IVAC y controles

- El selector usa el helper de quiescencia del **checkout congelado**, ya enviado por la ruta `RC6_A145_QUIESCENCE_GZIP_B64`, y lee el marcador actual. Si el marcador indica A171, invoca sólo `adopt_a171_running_hold`; esa función verifica recibo A171, cadena A169/A170, imagen/IDs, libro plano, hold A151, PPI state-only y 16 timers antes de escribir un recibo. Si falta cualquier prueba, falla cerrado. La ruta A169 sigue disponible para su marcador histórico.
- La regresión en `tests/test_rc6_a145_iol_carveout.py` simula que el módulo instalado no expone ninguna constante A172. Antes del fix el test deriva a A169 y falla como el host; después elige A171 y no hace ningún `systemctl start`. Los 20 casos del archivo y 78 casos dirigidos de continuidad, scheduler, aprobación, gzip y presupuesto SSH pasaron localmente. El nuevo SHA requiere Predeploy V2 integral y artefacto propio; los GREEN anteriores no lo certifican.
- Failure-safe cleanup del run RED reportó `status=GREEN`, staging ausente, sin tags/IDs eliminados, sin imágenes A172 y 7.310.163.968 B antes/después de esa limpieza. La repetición **read-only** de #40780727921 (attempt 2) a las 22:40:06 UTC / 19:40:06 ART confirmó Droplet exacto, A171 RUNNING y mismo image ID, 16 timers retenidos, guardian AUTHENTICATED, barrera ausente, PAPER/0, PPI state-only, cero mutaciones y 7.310.110.720 B libres. La pequeña diferencia de bytes respecto de la limpieza no se atribuye a un recurso particular sin una sonda adicional.

Estado: Deploy #40781245319 **RED**, A171 `DEPLOYED_VALIDATION_PENDING`, operativo **NO_GO**. A174 queda `DESARROLLADO` local hasta commit, Predeploy exacto, nuevo preflight y aprobaciones #624; ninguna prueba autoriza órdenes reales ni stress en el host.

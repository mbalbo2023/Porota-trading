# RCA — dependencia runtime IOL omitida en deploy

Fecha: 2026-09-24

## Incidente

El timer `porota-iol-shadow-collector-rc6.timer` permaneció habilitado y activo,
pero su servicio falló con `ModuleNotFoundError: rc6_source_consolidation`.

## Causa

El workflow canónico incluía `rc6_source_consolidation.py` en el tarball candidato,
pero lo omitía de las listas de instalación, backup y rollback. En consecuencia, el
archivo no llegaba a `/opt/porota-trading`, desde donde se ejecuta el colector IOL.

OAuth no fue la causa.

## Prevención obligatoria

Toda dependencia runtime debe cumplir las cuatro etapas en el workflow:

1. empaquetado en el candidato;
2. instalación en el host;
3. inclusión en backup/rollback;
4. verificación post-deploy mediante importación y hash del contenedor.

`tests/test_rc6_deploy_runtime_completeness.py` protege explícitamente esta
dependencia. El workflow ejecuta ese test, comprueba el archivo en staging y host,
importa el módulo y valida su hash dentro del observer.

## Seguridad

La corrección sólo afecta el empaquetado. El runtime continúa
`PRODUCTION_PAPER` / `SIMULATED` con `real_orders_sent=0`.

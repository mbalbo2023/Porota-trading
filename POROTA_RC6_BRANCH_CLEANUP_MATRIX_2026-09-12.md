# Matriz de limpieza de ramas RC6 — 2026-09-12

## Base y alcance

- SHA de referencia RC6 auditado: `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`.
- Auditoría de solo lectura: no se borraron ramas, no se cambiaron workflows y no se ejecutaron Actions.
- El circuito paralelo excluido por el usuario no forma parte de esta matriz.
- En las ramas consultadas, la API de ramas devuelve `protected: false`; la lectura de rulesets no está disponible. Esto no se toma como autorización para borrar.

## Decisión por grupo

| Grupo/ref | SHA punta | Comparación con RC6 | Evidencia | Decisión |
|---|---|---:|---|---|
| `codespace-setup`, `codespace-setup-v2`…`v6`, `infra/v16.1-codespaces-ready` | `f39cadaac8bff62f7176e10b80ffda80f6ee6f9b` (7 refs) | 0 ahead / 1144 behind | Mismo commit y árbol vacío; sin PR ni ejecuciones observadas. | Candidatas fuertes a consolidar: conservar una referencia canónica y retirar las otras seis, tras revisión de rulesets y referencias. |
| `codespace-config` | `475025a26035918e9eeb5704ef4f2224d09e930e` | 11 ahead / 1144 behind; diverged | Siete archivos únicos de configuración de desarrollo, Docker y smoke test; sin PR. | Conservar hasta decidir si ese trabajo se integra o se archiva. No es alias. |
| `chore/disable-codespaces-ci` | `c564c45d9da41837f0b26aa4f9766c75505eb940` | 1 ahead / 1130 behind; diverged | Único cambio en `.github/workflows/ci.yml`; sin PR. | Conservar hasta revisar el cambio único. |
| `hotfix/v17.0.0-rc3-hf6` y `hotfix/v17.0.0-rc3-hf7` | `9ec1fdb7f6571cc9c237276944b240c3469bd024` | 0 ahead / 750 behind | Mismo SHA; sin PR. HF6 tiene dos ejecuciones antiguas del 2-Sep; HF7 no tiene ejecuciones observadas. | HF7 es candidata a retiro como duplicado; conservar HF6 como referencia hasta comprobar referencias externas. |
| `release/v17.0.0-rc3-hf6-v2-candidate` | `5b61467657e772d9bbf939d313564d5f81ebe814` | 6 ahead / 643 behind; diverged | Seis archivos únicos, incluidos documentación de release/rollback y scripts; sin PR. | No borrar. Decidir integración o archivo explícito de ese trabajo único. |
| `candidate/v17.0.0-rc5-integration-20260905` | `109d328f5a40c3887eb2c96310a65b02e0acc264` | 0 ahead / 598 behind | Sin PR y sin ejecuciones de Actions observadas para la rama. | Candidata a retiro después de retirar su validador asociado y revisar referencias. |
| `release/v17.0.0-rc5` | `852b812d610860b57b579212978443f7450e8855` | 0 ahead / 591 behind | Sin PR; tuvo un despliegue RC5 exitoso el 5-Sep. Su workflow instala/habilita un timer remoto de Contract Evidence. | Mantener en espera. Primero debe verificarse el retiro de ese efecto remoto; esta auditoría no consultó el host. |
| `infra/production-paper-dispatcher-main-20260905` | `cf51aafb2c0275b6d5b15e0f947554a91a2b3113` | 5 ahead / 1129 behind; diverged | Cabeza del PR #13, abierto y en borrador, hacia `main`; agrega un workflow. | Conservar mientras el PR siga abierto. |
| `deploy-prep/rc6-wave18-currentbase-20260909` | `dcca3d9e82d4d6f26226b5f149244354cdb31d78` | 5 ahead / 290 behind; diverged | Cinco workflows únicos en el diff; sin PR. | Conservar hasta decidir el destino de ese trabajo y desactivar escritores duplicados. |
| `release-candidate/v17.0.0-rc6-deploy3-20260906` | `5bdad270c2a23bb2456a320d41e18940ce70ec6f` | 0 ahead / 406 behind | Sin PR; dos workflows escriben en el mismo script de la rama con grupos de concurrencia distintos. | No retirar hasta resolver esas automatizaciones y su destino. |
| `hotfix/rc6-byma-calendar-failclosed-20260909` | `bf25769749a203463b993d0923b116101f4ae032` | 0 ahead / 316 behind | Sin PR; workflows del árbol RC6 aún apuntan a esa rama. | Candidata posterior, una vez desactivados/reemplazados los escritores y revisadas referencias. |
| `fix/rc6-dashboard-operator-ux-v2-20260909` | `9197aedf35fe1739b594c742902e80df069c47c9` | 0 ahead / 305 behind | Sin PR; workflows del árbol RC6 aún apuntan a esa rama. | Candidata posterior, después de retirar los escritores y revisar referencias. |

## Orden seguro recomendado

1. Confirmar reglas de protección/rulesets con acceso administrativo.
2. Retirar o convertir automatizaciones que hacen push a ramas compartidas; mantener validación y revisión por PR.
3. Resolver PR #13 y conservar cualquier punta que tenga trabajo único.
4. Consolidar primero los alias exactos que no sostienen PR ni ejecuciones.
5. Retirar ramas históricas sin trabajo único solo después de revisar workflows, referencias y efectos remotos.
6. Mantener commits/tags históricos para trazabilidad; no reescribir el historial.

No se ejecutó ninguna acción de limpieza. Esta matriz es un checkpoint de auditoría, no una autorización de borrado.

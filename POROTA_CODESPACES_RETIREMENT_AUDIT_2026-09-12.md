# Retiro de referencias a Codespaces — 2026-09-12

## Hallazgo

RC6 operativo auditado (`a47f3339ec6dfe9d5afde444b1aaddabceb0e94d`) no contiene `.devcontainer/` ni `codespace-test.sh`; su README y `ci.yml` tampoco tienen referencias a Codespaces. El bot desplegado no depende de Codespaces.

La configuración sí sigue viva en `main`:
- `.devcontainer/devcontainer.json` se titula “Porota Trading v16.1 — Codespaces”, configura una imagen de desarrollo, Docker-in-Docker, Python, hooks post-create/post-attach y usuario `codespace`.
- `.devcontainer/post-create.sh` y `.devcontainer/welcome.sh` presentan el entorno como v16.1 y de testing/desarrollo.
- `README.md` aún recomienda abrir un Codespace y ejecutar `codespace-test.sh`.
- `codespace-test.sh` construye y levanta Compose para una prueba local, consulta health y luego baja el stack; es un test manual del entorno, no el despliegue del bot.

Por eso GitHub todavía ofrece una configuración de Codespaces al partir de `main`. No es una dependencia de producción ni se inicia automáticamente.

## Ramas

| Referencia | Estado | Recomendación |
|---|---|---|
| `codespace-setup`, `codespace-setup-v2`…`v6`, `infra/v16.1-codespaces-ready` | Siete referencias al mismo commit `f39cadaac8bff62f7176e10b80ffda80f6ee6f9b`; árbol vacío, 1144 commits detrás de RC6; sin PR ni ejecuciones observadas. | Conservar una referencia canónica temporal y retirar las otras seis luego de revisar reglas de repositorio. |
| `codespace-config` | Diverge de `main` (11 commits propios / 15 de `main`); añade siete archivos de configuración/desarrollo; sin PR. | No es una dependencia activa de RC6. Retirar o archivar después de comprobar las diferencias genéricas de Docker y requirements. |
| `chore/disable-codespaces-ci` | Una modificación única de `.github/workflows/ci.yml`; sin PR. | No está integrada ni activa por sí sola. Revisar el cambio y después retirar la referencia si resulta redundante. |
| `infra/production-paper-dispatcher-main-20260905` | Cabeza del PR #13, abierto y en borrador hacia `main`; no es una rama de Codespaces. | Mantener mientras el PR siga abierto. |

## Limpieza sugerida, aún no ejecutada

1. Abrir un PR de housekeeping a `main` que quite `.devcontainer/`, `codespace-test.sh` y el bloque de instrucciones de Codespaces en `README.md`. Mantener intactos archivos genéricos de Docker/requirements si tienen uso fuera de Codespaces.
2. Verificar que la eliminación no cambie el CI normal de `main`; no desplegar la aplicación por este cambio.
3. Retirar los seis alias de rama exactos y conservar por ahora una sola referencia.
4. Resolver `codespace-config` y `chore/disable-codespaces-ci` tras revisar sus cambios únicos.
5. Mantener historial de commits/tags; no reescribir historia.

En las ramas consultadas GitHub devuelve `protected: false`, pero la lectura de rulesets no está disponible. No se borró ni modificó ninguna referencia. Este checkpoint no incluye el circuito paralelo expresamente excluido.

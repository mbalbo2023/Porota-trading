# Checkpoint RC6 — corrección de completitud de deploy IOL

Fecha: 2026-09-24

- RCA confirmado: IOL fallaba por ausencia de `rc6_source_consolidation.py` en
  `/opt/porota-trading`, no por OAuth.
- Regla nueva: una dependencia runtime debe estar empaquetada, instalada,
  protegida por rollback e importada/verificada post-deploy.
- El workflow canónico incorpora la corrección y una prueba de regresión.
- No se habilitan órdenes reales. Modo requerido: `PRODUCTION_PAPER` /
  `SIMULATED`, `real_orders_sent=0`.
- Despliegue exclusivo por
  `.github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml`.

# POROTA TRADING RC6 — CHECKPOINT 2026-09-09 14:58 ART

## Baseline live observado
- Droplet HEAD observado: `9197aedf35fe1739b594c742902e80df069c47c9`.
- Este SHA es descendiente directo del baseline BYMA `bf25769749a203463b993d0923b116101f4ae032`, 11 commits adelante y 0 atrás.
- Política absoluta: `PRODUCTION_PAPER`, `real_orders_sent=0`, sin pruebas de rutas de órdenes reales, sin rollback automático.

## W12 — prioridad P0
- RCA confirmado: cuando PPI vence la sesión, collector devuelve `4 = BLOCKED_AUTH_SESSION_EXPIRED`; el wrapper tenía `set -e` reactivado antes de devolver ese código y terminaba antes de ejecutar reauth.
- Fix de handoff `rc=4 -> reauth -> recollect` pasó CI (19 tests).
- Collector endurecido a `/Cotizaciones/*` exclusivamente y métodos GET/HEAD/OPTIONS; guard CI GREEN.
- Primera prueba live controlada se abortó antes de instalar o tocar PPI porque esperaba `bf257...` y el Droplet ya estaba en `9197...`. La guarda funcionó correctamente.
- Siguiente gate: certificar `9197...` live; después repetir W12 oneshot y exigir captura fresca `AUTHENTICATED_TRUSTED_DEVICE` + import a Contract Evidence + invariantes PAPER.

## Estado de olas
- W9: CODE READY, CI GREEN.
- W10: wiring de concentración sectorial BINDING localizado; prueba semántica lanzada, no READY hasta demostrar comportamiento.
- W11: CODE READY, CI GREEN.
- W12: código y policy GREEN; live proof pendiente.
- W13: CODE READY; A3 sigue fail-closed salvo alignment.
- W14: CODE READY.
- W15: CODE READY.
- W16: safe housekeeping materializado, CI GREEN.
- W17: funcionalidad UX live ya comprobada previamente; formal proof current-base lanzado.
- W18: materialización v1 falló por packaging; v2 lanzada para incluir deploy script y rechazar rollback automático.

## Históricos / IOL
- IOL es apto como fuente read-only complementaria para backfill/reconciliación.
- Antes de activarlo como multi-source, preservar provenance por fuente; la clave histórica actual `(symbol,date)` no debe permitir que IOL pise silenciosamente una vela PPI.
- IOL no reemplaza PPI Contract Evidence ni especificaciones contractuales.

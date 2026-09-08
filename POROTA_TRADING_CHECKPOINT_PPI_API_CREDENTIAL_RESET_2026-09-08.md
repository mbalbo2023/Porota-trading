# POROTA TRADING RC6 — PPI PRODUCTION API CREDENTIAL RESET

Fecha: 2026-09-08

## Hecho confirmado

El usuario informó y mostró evidencia visual de que PPI reseteó las credenciales de la API productiva y volvió a exponer una nueva pareja de key pública / key privada en el portal oficial `trading.portfoliopersonal.com/usuarioApi`.

## Seguridad

- NO copiar claves crudas a GitHub, logs, checkpoints, CI artifacts ni chat de salida.
- NO persistir valores de credenciales dentro del repositorio.
- La key privada se muestra una sola vez según el portal; debe tratarse como secreto de máxima sensibilidad.

## Implicancia técnica

La evidencia anterior de `Credenciales invalidas` correspondía a la pareja previa instalada y ya no puede utilizarse para concluir que la nueva pareja reseteada también falla.

## Próximo paso autorizado y seguro

1. Probar primero la nueva pareja **en memoria y read-only** contra LoginApi.
2. No reemplazar todavía `/opt/porota-trading/.secrets/ppi_production.json` antes de demostrar auth GREEN.
3. Si LoginApi queda GREEN, hacer únicamente Configuration + MarketData read-only.
4. Cero llamadas a Budget/Confirm/Cancel/order routes durante esta validación.
5. Sólo después de prueba GREEN promover la nueva pareja al secret productivo y reiniciar/revalidar observer de forma controlada.
6. Mantener `real_orders_sent=0`.

## Estado

- PPI Web: GREEN último estado válido.
- PPI Production API: pasa de RED confirmado con credencial anterior a YELLOW / RETEST REQUIRED con la nueva pareja reseteada.
- Producción real: NO-GO hasta completar validación.

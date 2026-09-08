# POROTA TRADING — Pendiente de prioridad muy baja: conectividad PPI Sandbox

Fecha: 2026-09-08

## Estado
PENDIENTE — PRIORIDAD MUY BAJA.

## Contexto
Se intentó una prueba read-only contra PPI Sandbox desde el host de POROTA. El objetivo era validar únicamente autenticación y endpoints de configuración de sandbox, sin tocar producción ni rutas de órdenes.

Resultado observado del intento:
- STATUS=RED
- LOGIN=ERROR
- ERROR_TYPE=URLError
- PRODUCTION_TOUCHED=NO
- ORDER_ROUTES_CALLED=NO

## Investigación realizada
El host objetivo usado fue:
- https://clientapi_sandbox.portfoliopersonal.com

La documentación pública de PPI Sandbox expone el endpoint de login bajo `/api/1.0/Account/LoginApi` y endpoints read-only de `Configuration`.

## Próximo paso cuando se retome
Ejecutar diagnóstico de conectividad sin credenciales, en este orden:
1. DNS del host sandbox.
2. TCP 443.
3. TLS/certificado.
4. GET al Swagger público de sandbox.
5. Recién después, login sandbox read-only.

## Reglas de seguridad
- Sandbox únicamente.
- Nunca tocar `clientapi.portfoliopersonal.com` de producción en esta prueba.
- No Budget / Confirm / Cancel / order routes.
- No persistir ni imprimir credenciales.
- No usar este pendiente para bloquear las olas de compilación/deploy de RC6.

## Regla operativa Termius
Todo script entregado al operador debe:
- generar salida compacta,
- guardarla en `.txt`,
- intentar copiar automáticamente el resultado al portapapeles,
- indicar explícitamente `CLIPBOARD=OK` o `CLIPBOARD=FAILED`,
- salvo que el operador pida otra cosa.

## Prioridad
MUY BAJA. Retomar sólo cuando no compita con deploys, validaciones, compilación o estabilización de POROTA RC6.

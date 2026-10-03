# RC6 — cierre técnico validado del candidato unificado

**VALIDADO_RUNTIME técnico.** No equivale a todas las familias READY ni a actividad intradiaria demostrada.

- PR #445; product SHA `e9cf3fdbd9e6a71a0aa72365ff3b2727378db10f`.
- Candidato `b001ef1caf2f414a5022d2781016fa34f1611260`; tree `f3440892a5b3c14b5013f696710016209dc5a689`.
- Predeploy V2 `37077028639`; artifact `11256399584`.
- Deploy V2 único `37078173314` SUCCESS; auditoría independiente `37083378849` SUCCESS.
- Dos snapshots independientes separados por 120 segundos.
- Suite gobernada: discovered=2459; executed=2459; failures=0; errors=0; skipped=0; xfail=0.
- Candidatos READY: 8082 / 8082.
- Tres contenedores running con la imagen cargada `sha256:076310512caa512d8934157985364a3aa7d9cdb58d14c490d9196d55c766a398`, sin OOM ni reinicios automáticos en ambas lecturas.
- PRODUCTION_PAPER; real_orders_sent=0; rutas reales NOT_CALLED; PPI Watch intacto; dashboard HTTP 200.
- Nueve PR fuente cerrados como absorbidos; ramas/evidencia preservadas.

## Pendientes explícitos

Caución: book PPI fresco y decisión/colocación del cash-sweep PAPER en ventana válida siguen NO_VERIFICADO. Las pruebas sintéticas no son evidencia de rueda.

Scalping: capacidad de confirmación y operación simulada durante rueda válida siguen NO_VERIFICADO después de este deploy con mercado cerrado.

Readiness es por identidad y fail-closed; filas no READY no se promueven ni borran automáticamente.

La clasificación de units legacy fallidas y el backlog más amplio no se cierran silenciosamente por este release.

Protecciones administrativas de rama: NO_VERIFICADAS por el conector; no se eludieron ni modificaron.

## Evidencia y aprendizaje

Contadores completos y procedencia: `RC6_UNIFIED_FINAL_STATE_2026-10-02.json`. Los errores/correcciones de los auditores auxiliares y de integración se conservan en el checkpoint histórico. No se ejecutó un segundo deploy, rollback ni rebuild en el droplet.

El auditor inicial con esquema incorrecto quedó superado. El cierre usa el contrato canónico y el auditor corregido. El primer finalizador falló al parsear YAML por texto Python multilínea mal indentado: se separó en script Python compilado, con siete fixtures negativos antes de esperar o escribir. Ninguno de esos errores auxiliares se presenta como fallo del servidor.

Ownership: pendiente liberación explícita en issue #441 tras revisar el cierre.

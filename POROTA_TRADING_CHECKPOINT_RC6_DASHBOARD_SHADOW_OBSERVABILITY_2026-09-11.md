# POROTA TRADING RC6 — CHECKPOINT DASHBOARD / SHADOW OBSERVABILITY

Fecha: 2026-09-11 (ART, America/Argentina/Buenos_Aires)
Estado del documento: CHECKPOINT INCREMENTAL / DOCUMENTACION SOLAMENTE

## 1. Verdad de runtime

- Version logica: `17.0.0-rc6`
- SHA desplegado/certificado que sigue siendo canonico: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`
- Modo runtime: `PRODUCTION_PAPER`
- Ejecucion real: BLOQUEADA
- Invariante: `real_orders_sent=0`
- Este checkpoint NO implica deploy de ningun desarrollo nuevo.
- Rama documental: `checkpoint/rc6-parallel-advance-20260911`

## 2. Integracion trader-facing del dashboard

Rama de integracion:
`feature/rc6-trader-dashboard-shadow-observability-20260911`

Base certificada de observabilidad scalping:
`d1d4c6c8c990ac5d47d4068eaed3d908ccd2e721`

HEAD certificado de integracion:
`f5f657e1050da6ed95c289ef132aae005d0b0685`

Estado:
- PRESENT IN SOURCE: SI
- TESTED: SI
- CERTIFIED: SI
- DEPLOYED: NO
- ACTIVE EN RUNTIME: NO

### Ubicacion funcional para el trader

- `Scalping`: calidad temporal de velas y revisiones PPI (p50/p95/max, mutable vs closed-revision, simbolos con mas revisiones).
- `Trading -> Estrategias`: `SWING_PAPER` overnight, explicitamente `SHADOW_ONLY`.
- `Trading -> Cauciones`: cash-sweep de fin de rueda, explicitamente `SHADOW_ONLY`.
- `En vivo`: resumen compacto de las tres capacidades, con modo, evidencia/estado y binding.

Regla de UX/arquitectura: el dashboard consume evidencia y presenta estado. No decide, no ejecuta, no llama PPI y no promueve SHADOW a binding.

## 3. Certificacion GREEN del dashboard

Workflow:
`RC6 Trader Dashboard Shadow Observability 2026-09-11`

Run GREEN:
`34664781940`

Job GREEN:
`103474394749`

Conclusion:
`SUCCESS`

Pasos certificados:
1. checkout exacto de rama;
2. Python 3.12;
3. dependencias acotadas de test;
4. exact-delta guard;
5. compilacion de modulos de integracion;
6. tests de ubicacion y seguridad trader-facing;
7. static non-binding safety guard;
8. verificacion `CAUCIONES_AUTO_PLACEMENT=false`;
9. cierre de job completo.

La lista blanca del delta impide que se cuele un archivo adicional inesperado.

## 4. Incidente de CI y RCA

Primer HEAD probado:
`4eeb411e708aa1fbc8738e12b8805f070d1f4684`

Run fallido:
`34664680120`

Job fallido:
`103474107430`

RCA:
- exact delta guard: PASS;
- compilacion: PASS;
- pytest fallo durante COLLECTION;
- causa: la raiz del repositorio no estaba expuesta en `PYTHONPATH`, causando `ModuleNotFoundError` para modulos RC6;
- las pruebas de producto no llegaron a ejecutarse.

Forward fix aplicado SOLO al certifier:
- `PYTHONPATH=.`;
- ejecucion via `python -m pytest`.

No se modifico logica de scalping, SWING, caucion ni dashboard para resolver este incidente.

Resultado posterior: GREEN en run `34664781940`, job `103474394749`.

## 5. Controles de seguridad certificados

- No existe formulario/boton de ejecucion en las nuevas secciones.
- Overlay trader-facing sin imports de PPI, broker, PAPER engine ni exit supervisor.
- Sin `requests.post/put/delete`.
- Sin INSERT/UPDATE/DELETE de DB.
- Una falla de lectura de cauciones degrada a `EVIDENCE_READ_UNAVAILABLE` y no derriba el dashboard.
- Un `EOD_PAPER` NO se interpreta ni reclasifica como `SWING_PAPER`.
- `SWING_PAPER` exige `execution_style=SWING_PAPER` explicito.
- `SCALPING_PAPER` / `INTRADAY_PAPER` siguen siendo intradia.
- `CAUCIONES_AUTO_PLACEMENT=false` permanece obligatorio en certificacion.
- Ninguna capa nueva agrega superficie de orden real.

## 6. Estado de frentes paralelos

### Scalping revision telemetry
- Instrumentacion de edad de revision: TESTED/CERTIFIED.
- Umbral: sigue en 120 s.
- Acciones observadas: `REFRESH_MUTABLE` y `REJECT_CLOSED_REVISION`.
- Telemetria best-effort: una falla de registro no altera el veredicto.
- DEPLOYED: NO.
- Evidencia real de latencia PPI: pendiente de rueda abierta.

### Scalping telemetry observability
- Rama: `feature/rc6-scalping-telemetry-observability-20260911`.
- HEAD: `d1d4c6c8c990ac5d47d4068eaed3d908ccd2e721`.
- Run: `34662775025`.
- Job: `103468540971`.
- Estado: GREEN / CERTIFIED / NO DEPLOY.

### SWING_PAPER shadow
- Politica pura y no-binding: CERTIFIED.
- No infiere SWING desde posiciones existentes ni desde `EOD_PAPER`.
- Economia: `SWING_NON_INTRADAY`; no asume bonus intradia overnight.
- CEDEAR: requiere calendario del subyacente verificado.
- DEPLOYED: NO.

### Caucion cash-sweep shadow
- Planificador shadow sobre planner conservador existente: CERTIFIED.
- Cutoff derivado de horario verificado; no hardcodea T-10.
- Requiere calendario, schedule source, quote fresca, fee exacta, settlement y caja libre luego de reservas/obligaciones.
- No vende posiciones para generar caja.
- `real_execution_allowed=False`.
- DEPLOYED: NO.

## 7. EOD / overnight — frente de replay

Proximo trabajo read-only:
medir cobertura real de snapshots posteriores a cierres `EOD_PAPER` antes de emitir cualquier conclusion sobre mantener overnight.

Si la cobertura lo permite, el replay debe medir:
- primer snapshot de la sesion siguiente / gap;
- MFE y MAE;
- si stop o target original habrian sido alcanzados;
- resultado despues de 1, 2 o mas sesiones;
- costos overnight completos, sin rebate intradia;
- comparacion contra el cierre EOD realmente ejecutado en PAPER.

Si la cobertura no es suficiente, el resultado correcto es `COVERAGE_INSUFFICIENT`; no se inventa un contrafactual.

## 8. Bloqueantes / evidencia pendiente

- Para cambiar el umbral temporal de 120 s se requiere telemetria real durante rueda abierta.
- Para promover SWING_PAPER se requiere replay/counterfactual y evidencia suficiente; hoy sigue SHADOW_ONLY.
- Para promover caucion se requieren feed/horario/quote/calendario certificados en runtime y una etapa PAPER previa; hoy sigue SHADOW_ONLY.
- Dashboard integrado esta certificado en source, pero no desplegado.

## 9. Lo que NO se debe repetir / hacer

- NO inferir SWING por haber llegado a EOD.
- NO cambiar 120 s por intuicion o datos fuera de rueda.
- NO activar caucion real ni `CAUCIONES_AUTO_PLACEMENT`.
- NO llamar rutas reales de orden ni `/Operar`.
- NO vender una posicion valida para generar caja para caucion.
- NO confundir SOURCE/TESTED/CERTIFIED con DEPLOYED/ACTIVE.
- NO reabrir frentes GREEN sin evidencia nueva.

## 10. Siguiente secuencia segura

1. Medicion read-only de cobertura EOD -> overnight sobre runtime desplegado.
2. Construccion de replay contrafactual si la cobertura es suficiente.
3. Preparacion de deploy aislado de telemetria/observabilidad del scalper, sin arrastrar SWING/caucion activos.
4. Integracion dashboard solo junto a las capacidades que efectivamente se desplieguen, siempre etiquetando `SHADOW_ONLY` cuando corresponda.
5. Postdeploy cert con `real_orders_sent=0`, salud, DB, modo y ausencia de llamadas reales.

Este checkpoint es incremental y no sustituye la verdad del postdeploy de `eddcc29...`; la complementa con el estado de desarrollo/certificacion de los frentes paralelos.

# RC4 — RESPUESTA A AUDITORÍA HF6-v2 — WIP

> Documento de trabajo. NO es la respuesta final y NO autoriza deploy.
> Se cerrará antes del deploy RC4 con evidencia de código, tests, replay y runtime aislado.

## Regla de decisión

Cada hallazgo termina en uno de cuatro estados:

- **ACEPTADO**: diagnóstico confirmado y corrección incorporada.
- **ACEPTADO_PARCIAL**: la causa o necesidad es válida, pero la solución propuesta requiere cambio o evidencia adicional.
- **RECHAZADO_JUSTIFICADO**: no se incorpora; se documenta evidencia concreta.
- **PENDIENTE_EVIDENCIA**: todavía no se puede decidir sin medir/confirmar una fuente externa.

Ningún snippet de la auditoría se copia a runtime por autoridad del documento. Primero se contrasta con el candidate1 exacto.

## Verificaciones independientes de los artefactos adjuntos

### `ck_policy_gate_hf6.py`

- El módulo compila y `assert_policy_gate_invariants()` pasa.
- La motivación es válida: expectancy/regime/sector están declarados pero no tienen autoridad de admisión.
- **No es integrable tal como está sugerido**: candidate1 no expone `PaperStore.empirical_samples`, `breadth_snapshot` ni `sector_snapshot`; `InstrumentContract` tampoco expone `sector`.
- El fallback sectorial con `candidate_sector=None` no satisface la política RC4 de identidad explícita: un candidato sin mapping debe ser `SECTOR_MAPPING_REQUIRED`/fail-closed cuando ese gate sea BINDING, no confundirse con la concentración máxima de posiciones existentes.
- Expectancy debe evaluarse en la identidad correcta (al menos moneda + estrategia, y por familia si la muestra lo permite), no como veto accidental cruzado entre libros independientes.

**Decisión preliminar:** ACEPTADO_PARCIAL. Se conserva la idea de un gate puro y testeable, pero se rediseña la interfaz y no se habilita BINDING sin sus prerequisitos.

### `porota_hf6_v3_bloque1_observabilidad.sh`

Prueba independiente ejecutada contra una copia exacta del candidate1:

- patches de Históricos/Scheduler/NEWS aplican;
- `compileall` pasa;
- con `PYTHONPATH=.` los 5 tests entregados pasan;
- la estrategia de staging y anchors idempotentes es útil.

Hallazgo propio sobre el script:

- si `pytest` falla, registra `PYTEST=REVISAR` pero no pone `FAILED=1`; el resumen puede declarar `BLOQUE1_ESTADO=OK` con tests fallidos.
- el Scheduler RC4 además debe incorporar evidencia de timers/systemd sanitizada cuando corresponda; tres tablas no cubren todo el modelo final de observabilidad.

**Decisión preliminar:** ACEPTADO_PARCIAL. Se portan las correcciones al source RC4 versionado; no se promoverá el script `--in-place` como mecanismo de release.

---

# Matriz de los 15 hallazgos

## P0-1 — Tres políticas declarativas sin portón real

**Estado preliminar: ACEPTADO_PARCIAL.**

Confirmado conceptualmente y por código. RC4 incorporará autoridad explícita y testeable, pero:

1. `PAPER_MARKET_REGIME_POLICY` conserva `ALERT_ONLY` según política operativa vigente; no se vuelve BINDING por el mero hecho de existir código.
2. Sector sólo podrá ser BINDING cuando haya mapping sectorial explícito y auditable. Un candidato sin sector no se inferirá por ticker.
3. Expectancy permanece observacional hasta muestra suficiente y segmentación correcta.
4. Ninguno de estos controles puede abrir una operación rechazada por otro gate.

## P0-2 — Reward/risk +5% no valida esperanza

**Estado preliminar: ACEPTADO_PARCIAL.**

Se acepta que reward/risk sin probabilidad de ocurrencia es insuficiente y que el 5% no está validado como barrera efectiva. No se acepta todavía como hecho demostrado que toda la estrategia sea matemáticamente negativa: la hipótesis de caminata aleatoria y las barreras no reemplazan el replay del proceso real de señal + EOD + costos.

El snippet propuesto tampoco es directamente ejecutable: `bl_candle_engine.py` no contiene `intraday_sessions()` ni objetos `first_touch()`.

RC4 hará primero replay/versionado de sesiones y probabilidad de barrera con datos disponibles sin look-ahead. Stop/target/slippage no se recalibran antes de esa evidencia.

## P0-3 — History Store v2 huérfano

**Estado preliminar: ACEPTADO.**

El grafo estático confirma que la cadena post-close no tiene raíz operacional en candidate1. RC4 conectará History Store v2 por primera vez.

No se aceptará ciegamente una doble ejecución timer + fallback dentro del observer: se elegirá una autoridad de scheduling con lock/idempotencia para impedir corridas concurrentes. El dashboard mostrará última corrida, próxima, fuente y resultado.

## P1-1 — Históricos 0/823

**Estado preliminar: ACEPTADO.**

La corrección `available=False` cuando no existe `history_canonical_v2` es correcta. El panel debe caer al conteo legacy real mientras V2 no existe y distinguir `LEGACY` de `V2`.

## P1-2 — Scheduler SIN_REGISTRO

**Estado preliminar: ACEPTADO_PARCIAL.**

Confirmado. RC4 unificará `operational_jobs`, `source_sync`, `api_health` y, para jobs de host, snapshot sanitizado de systemd. Debe resolver duplicados por prioridad/frescura, mostrar última/next/duración/resultado/causa y reservar gris para SIN_EVIDENCIA/NO_INSTALADO/NUNCA_EJECUTADO.

## P1-2b — NEWS_REFRESH

**Estado preliminar: ACEPTADO.**

La UI mostrará cadencia efectiva: 45 min cuando la ingesta está ON; control extendido (~12 h) y `NO_APLICA` cuando está OFF.

## P1-3 — Release no reproducible desde Git

**Estado preliminar: ACEPTADO.**

RC4 no se construirá como commit + patchers de runtime. Los cambios materializados de candidate1 se consolidarán en source versionado y ése será el commit de release. Patchers quedan como evidencia/herramientas de desarrollo, no como parte necesaria para reconstruir el binario.

`.bak`, `.pre-hf6*`, `:memory:*`, `*.ses` quedan fuera de release/build context.

## P1-4 — `PAPER_MAX_OPEN_POSITIONS` muerto / emergency cap 50

**Estado preliminar: ACEPTADO_PARCIAL.**

Confirmado que el límite normal no debe gobernar admisión. Se retirará de acceptance/config visible como autoridad estratégica.

El valor técnico del emergency cap se reducirá desde 50, pero el número final se justificará contra el máximo concurrente plausible y pruebas anti-runaway; no se adopta `12` sólo porque figure en la auditoría.

## P1-5 — `PAPER_MAX_HOLD_MINUTES=360` no muerde

**Estado preliminar: ACEPTADO_PARCIAL.**

Se acepta que 360 min queda dominado por la salida EOD en la sesión actual. No se adopta automáticamente 120 min: cambiar hold cambia la distribución de la estrategia y parte la serie de validación. RC4 medirá duración/exit causes y sólo fijará un valor nuevo con replay; si cambia durante Shadow->Binding, se registra el corte de serie.

## P1-6 — Bonificación PPI asumida sin reconciliación

**Estado preliminar: ACEPTADO.**

Es prioridad RC4. Se construirá captura versionada del costo informado por PPI o, si PPI no lo expone por fill, una fuente de verdad alternativa de resumen de cuenta. No se inventará el dato.

El portón económico deberá pasar a SHADOW durante la ventana de validación; volver a BINDING será una decisión explícita después de cumplir criterios.

## P2-1 — Settlement `time.max`

**Estado preliminar: ACEPTADO_PARCIAL.**

No se cambia `time.max` hasta confirmar cutoff oficial exacto. Sí se agrega al dashboard la caja inmovilizada por la hipótesis conservadora y la fuente del cutoff.

## P2-2 — Concentración sectorial

**Estado preliminar: ACEPTADO.**

El riesgo existe y fue observado nuevamente el 03/09. RC4 crea mapping sectorial explícito/versionado, cobertura visible y gate fail-closed para sector desconocido cuando la política sea BINDING.

## P2-3 — Señal momentum sobre ticks, Candle Engine no usado

**Estado preliminar: ACEPTADO_PARCIAL.**

El signo del momentum es correcto. Se acepta que el muestreo irregular no equivale a SMA temporal y que el candle engine debe entrar al laboratorio/replay. RC4 no cambia de golpe la señal productiva: ejecutará una señal candidata SHADOW/replay paralela para comparar sin contaminar la serie.

## P2-4 — `:memory:.ses` y artefactos filtrados

**Estado preliminar: ACEPTADO.**

Se excluyen del release y además se localizará el productor. No basta con ocultar el archivo si el bug de path continúa generándolo.

## P2-5 — Colisión de prefijos

**Estado preliminar: ACEPTADO.**

No se renombran 212 módulos en RC4. Se generará `MODULOS.md` con propósito, capa, importadores/llamadores, entrypoint, estado (runtime/offline/test/deprecated) y salida. El chequeo de huérfanos será parte de acceptance.

## P2-6 — `slippage_bps=2`

**Estado preliminar: PENDIENTE_EVIDENCIA.**

Se acepta que debe medirse. No se aceptan 8/10/15 pb como defaults sin replay. RC4 instrumentará slippage implícito por fill y por causa de salida; después se calibra.

---

# Nuevo requisito RC4 — menú Validación

Menú principal `/validacion`.

El portón económico tendrá una campaña Shadow->Binding con cuatro hitos:

- H1 Instrumentación
- H2 Fidelidad de costos
- H3 Fidelidad de ejecución
- H4 Consistencia y decisión

Debe mostrar:

- modo actual;
- fecha real de inicio y ventana propuesta;
- ruedas transcurridas/restantes;
- operaciones cerradas/reconciliadas y faltantes a 60;
- los 8 criterios, valor observado, objetivo y gap restante;
- estado de cada hito;
- contrafáctico de lo que BINDING habría bloqueado;
- configuración congelada/fingerprint e invalidación si cambia;
- lección aprendida por hito, sólo con evidencia persistida;
- decisión permitida: `VALIDATING`, `BLOCKED`, `WINDOW_INVALIDATED` o `ELIGIBLE_FOR_BINDING_REVIEW`.

**Nunca cambia a BINDING automáticamente.**

Q3/edge de estrategia se mostrará como validación separada y de mayor plazo; cuatro semanas no la certifican.

---

# Huérfanos — política RC4

Se ejecutará un grafo de imports + entrypoints + systemd/scripts sobre el source final. Cada módulo sin camino operacional deberá terminar clasificado como una de:

- `RUNTIME_REACHABLE`
- `SCHEDULED_ENTRYPOINT`
- `OFFLINE_TOOL`
- `MANUAL_ADMIN_TOOL`
- `TEST_ONLY`
- `DEPRECATED_WITH_REASON`
- `ORPHAN_ERROR`

`ORPHAN_ERROR > 0` bloqueará el release. Un módulo deliberadamente offline/deprecated no se elimina sin justificar dependencias, datos que preserva y motivo.

# POROTA TRADING RC6 — PAPER/SHADOW ONLY

POROTA TRADING RC6 es un sistema de investigación, observación y simulación de estrategias de mercado. **No está autorizado a enviar órdenes reales.** Las pruebas de contratos, señales, fuentes y motores se realizan bajo política `PRODUCTION_PAPER / SIMULATION` y admisión individual *fail-closed*.

> **Estado de esta rama:** código candidato en revisión; `EN_GITHUB` no equivale a `DESPLEGADO` ni `VALIDADO_RUNTIME`. La situación efectiva del droplet, la base de datos y las órdenes simuladas requiere recibos actuales independientes; en ausencia de ellos, `NO_VERIFICADO`.

## Reglas inviolables

- `PAPER/SHADOW ONLY`; `real_orders_sent=0` como invariante obligatoria, **no** como medición actual inferida del código.
- Rutas de órdenes reales bloqueadas y nunca llamadas. **Ninguna opción, incluida Telegram o configuración, está autorizada a habilitar órdenes reales; cualquier ruta existente debe permanecer bloqueada.**
- **PPI Watch NO TOCAR** salvo autorización expresa y acotada; su existencia, unidades y estado se verifican antes de atribuirle cualquier resultado.
- **FIX-FORWARD ONLY**: sin rollback, sin retroceder silenciosamente el estado productivo y sin purgas inseguras.
- GitHub y GitHub Actions son el plano de control habitual. No se exige al operador una terminal, SSH ni comandos de contenedores manuales para tareas normales; SSH sólo es excepcional (*break-glass*).
- Todo cambio debe declarar `READ_ONLY`, `WRITE_OWNER` o `DEPLOY_OWNER`, con ownership por ámbito y una única promoción final coordinada.

## Qué fuente acredita cada afirmación

| Afirmación | Evidencia necesaria |
|---|---|
| Código disponible | Commit SHA y diff del repositorio/PR correspondiente |
| Pruebas ejecutadas | GitHub Actions, run/job, SHA exacto y resultado; un job omitido no es GREEN operacional |
| Paquete liberable | Artefacto inmutable, manifest generado desde el checkout y hashes/digest verificados |
| Código desplegado | SHA/imagen realmente instalados y recibo de Deploy V2 del droplet |
| Función operativa o fill PAPER | DB/logs/APIs y trazas causales auténticas, atribuibles a instrumento y sesión |

Repositorios y ramas cumplen funciones distintas: `main` contiene controles y diagnósticos independientes; `deploy/rc6-pr69-isolated-20260915` es la rama del producto; [PR #778](https://github.com/mbalbo2023/Porota-trading/pull/778) concentra el **único candidato**. Sus HEAD son móviles: se consultan en GitHub cada vez, nunca se deducen del nombre de la rama ni de este texto.

Para estados, SHA, dependencias, runs, propietarios y bloqueos actuales, consultar el [checkpoint de continuidad #732](https://github.com/mbalbo2023/Porota-trading/issues/732), la [auditoría integral #909](https://github.com/mbalbo2023/Porota-trading/issues/909) y las pruebas originales. Este README no reemplaza sus evidencias.

## Fuentes, instrumentos y decisiones simuladas

- **PPI** es la referencia primaria de identidad de instrumentos. IOL, BYMA, A3 y ROFEX complementan según procedencia válida; nunca sobrescriben arbitrariamente la identidad PPI.
- Se exige identidad exacta de instrumento, mercado, moneda, liquidación y familia, contrato vigente, unidad/nominal/lote, comisiones, presupuesto y capacidad de EXIT cuando corresponda.
- El embudo de observación y selección COLD/otros estados sólo habilita oportunidades cuando su implementación y los datos lo acreditan. **COLD2 designa un guard de archivo/quiescencia SHADOW asociado al despliegue; no es una etapa del embudo comercial por el mero nombre.**
- Un PPI Current/Book o un replay constituye evidencia de observación, no automáticamente un fill acreditado. PIT causal, decisión, segundo Book cuando corresponda, precio/unidad/costos, liquidación y ledger deben verificarse de forma independiente.
- `OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES` no significa que todos los instrumentos estén `READY`. Cada admisión PAPER es por instrumento y se bloquea ante contratos ambiguos, presupuesto/EXIT no acreditados o fuentes insuficientes.
- Caídas o cotizaciones antiguas de proveedores externos generan alertas acotadas; no se convierten por sí solas en un veto global al Deploy V2. La admisión PAPER afectada sí permanece bloqueada.

## Predeploy y Deploy V2

El único mecanismo normal de promoción es **Deploy V2** con `build once`: compilación única, pruebas, paquete y manifest de checkout, tar/digest inmutables, preflight y validación directa del runtime. El droplet **no recompila** la imagen; sólo recibe y valida el mismo artefacto probado. El mutex es `rc6-unified-paper-deploy`, `cancel-in-progress=false`.

La ejecución de pruebas focalizadas de un PR **DRAFT** puede figurar bajo un workflow llamado «Predeploy V2», pero si `Exact artifact predeploy gate=SKIPPED` no es un Predeploy V2 completo, no genera paquete promovible ni valida runtime.

**No ejecutar Predeploy V2 completo ni Deploy V2 hasta satisfacer los requisitos previos, congelar SHA/tree, reconciliar todos los frentes, validar el artefacto exacto y obtener el GO específico de promoción.** No hacer promociones independientes por cada PR, compilación o arranque manual de contenedores, borrados generales de Docker, escrituras directas en la rama productiva ni reinicios manuales por rutina.

Tras un deploy autorizado deben comprobarse mode/seguridad PAPER, contenedores, servicios, DB, rutas, logs, integridad de imagen y limpieza Docker selectiva con recibo de espacio antes/después. No borrar volúmenes, DB, WAL/SHM vivos, imágenes referenciadas ni activos PPI Watch.

## Histórico y documentación

La retención de históricos se gobierna por **365 fechas ART inclusivas**, procedencia y referencias/pins cruzados. Un conteo de registros antiguos o un archivo WAL/SHM abierto por SQLite **no autoriza** purgar, compactar o ejecutar `VACUUM`. Las propuestas de retención se prueban primero fuera del host y siguen sin autoridad de eliminación.

Referencias de trabajo:

- [AGENTS.md](AGENTS.md) — gobierno y seguridad del repositorio.
- [Política RC6](ops/policy/porota-policy.yaml) — fuente normativa de PAPER y Deploy.
- [Arquitectura Deploy V2](docs/DEPLOY_V2_ARCHITECTURE.md) — control de construcción/promoción.
- [Issue #732](https://github.com/mbalbo2023/Porota-trading/issues/732) — checkpoints y evidence contract actualizados.
- [Issue #909](https://github.com/mbalbo2023/Porota-trading/issues/909) — auditoría transversal pendiente antes de Predeploy.
- [README anterior, versión 16.2 — archivo histórico no operativo](https://github.com/mbalbo2023/Porota-trading/blob/0f1e14735a6fc3423e86b6bc091c58f65537d9f3/README.md). Se conserva por trazabilidad, **no** como guía de ejecución vigente.

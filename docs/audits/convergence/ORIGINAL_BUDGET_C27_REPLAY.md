La reproducción independiente aporta evidencia histórica para tres IDs originales: U01, U07 y U27. Ejecutó APIs nativas sobre el archivo íntegro de `c27dfd963c4fe83465c0f2105347e974fbbe6356`, tree `bf3cf193434641aa89e4c746b26c77aec5d1d2b2`: 1109 archivos sin cambios, 66 imports internos, ningún import de otro checkout y ningún evento de red del producto. El [binder JSON](ORIGINAL_BUDGET_C27_REPLAY.json) conserva las tres filas originales del CSV, los límites de cada prueba y los hashes de archivos. El [recibo](evidence/original-budget-c27/native-c27-replay-receipt.json) conserva el inventario completo, las trazas y los resultados.

| ID original | Observación histórica nativa | Control y alcance |
|---|---|---|
| U01 | Un owner OPENED ya enviado, con body HTTP de 120 ms, causa un error EXIT en cada una de tres ventanas separadas por 31 s. El follower abandona antes de que termine el body. | Owner de 15 ms: cero errores en las tres ventanas. Un solo wire book por ventana en ambos casos. SDK, guard, collector y deuda son nativos; `HTTPAdapter.send` es simulado. |
| U07 | Cinco posiciones spot OPEN y cinco futuros ACTIVE creados por `PaperBroker` se cuentan como cinco. El runtime reserva demanda EXIT 30 en vez de 60. Con límite book 75, discovery consume 40 y después sólo pasan 35 de las 60 solicitudes EXIT. | El mismo presupuesto archivado, con conteo correcto mediante `budget_policy`, admite discovery 5 y EXIT 60/60. Controller y benchmark son nativos sobre HTTP simulado; los intentos `acquire/start/finish` posteriores modelan el wire en SQLite. |
| U27 | `consolidate` y `reconcile` publican `shadow_promotion=true` en IOL-only y BYMA-only, con `selection_eligible=false`. | Entry, live y real-money permanecen en false. La selección PPI y cinco variantes de identidad conservan su alcance. Se inventariaron todos los archivos del archive: dos productores, dos tests y dos JSON de auditoría; ningún consumidor de autoridad del flag. |

U01 también ejecuta intactas las definiciones `Clock`, `policy` y `single_flight_priority_probe` del [driver original](evidence/original-budget-c27/u01-original-driver.py.source), sin ejecutar su prologue con un checkout obsoleto. Ese driver cubre tres ciclos separados por seis segundos y un fetch modelado; declara cinco posiciones pero modela una sola identidad y reserva 5/book 6, sin cubrir demanda de cadencia 30. Se preserva como fue entregado. La extensión SDK usa una posición PAPER real y demanda 6/book 7 suficiente, y separa su RED de esa limitación del fixture original. Este trabajo no reproduce un owner histórico que exceda todo el deadline de cinco segundos ni vuelve a ejecutar los guards actuales de restart, ronda o START.

Los futuros de U07 abren el 24 de agosto con contratos y quotes explícitamente `OFFLINE_SYNTHETIC`. AGO y SEP permanecen ACTIVE pendientes de salida al corte de octubre y se cuentan conservadoramente. La prueba no afirma cinco series DLR reales vigentes ni evidencia contractual conocida en agosto. Los límites 75/225 alcanzan la demanda matemática del fixture; no certifican capacidad PPI spendable ni cadencia del collector. La falta real de capacidad debe conservar su rechazo y no se clasifica como fallo de fixture o software.

U27 demuestra una inconsistencia advisory del flag y no demuestra un bypass de entrada o dinero real. El control PPI verifica selección: la reconciliación original puede publicar `INSUFFICIENT_EVIDENCE` y conservar `selection_eligible=true`; no se presenta ese control como un contrato READY.

El audit hook prohíbe crear sockets, conectar o resolver DNS. Su control negativo bloquea un intento explícito antes de importar producto. También bloquea la detección local de IPv6 de urllib3 antes de crear su socket; esa traza queda separada, no hubo bind/connect/DNS y `HAS_IPV6` queda en false. Los imports nativos provienen sólo del archive; el SDK instalado es `ppi-client 1.3.0`, con `requests 2.34.2` y Python 3.11.16. Los IDs PAPER mantienen su generación UUID nativa; no se inventa un seed determinista.

Dos ensayos iniciales se descartaron: un fixture disparó correctamente el cap de emergencia y otro carecía del prefijo de identidad PPI requerido para la quote de futuros. Se corrigieron sólo los inputs sintéticos. El caso aceptado usa cash PAPER 100000000, riesgo 0,2%, soft stop 4% y cap AUTO derivado nativamente como 20; no reemplaza ni desactiva gates. El prefijo final declara `PPI_CATALOG:OFFLINE_SYNTHETIC_FIXTURE_NOT_DATED_PROVIDER_EVIDENCE`.

Para repetir desde el repositorio que contiene el [probe](../../../tests/probes/rc6_budget_original_c27_replay.py), crear un directorio nuevo para el archive íntegro y ejecutar:

```bash
umask 022
git archive --format=tar --output=/tmp/rc6-budget-original-c27.tar c27dfd963c4fe83465c0f2105347e974fbbe6356
mkdir -p /tmp/rc6-budget-original-c27-full
tar -xf /tmp/rc6-budget-original-c27.tar -C /tmp/rc6-budget-original-c27-full
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /workspace/venv_rc6/bin/python tests/probes/rc6_budget_original_c27_replay.py \
  --source-root /tmp/rc6-budget-original-c27-full \
  --archive /tmp/rc6-budget-original-c27.tar \
  --original-u01-driver docs/audits/convergence/evidence/original-budget-c27/u01-original-driver.py.source \
  --receipt /tmp/rc6-budget-c27-replay.json
```

El recibo aceptado sale con código 0 y valida las observaciones. Los controles, ciclos, variantes de identidad y solicitudes SQL no agregan IDs ni escenarios R. Los guards del código corregido se referencian desde el snapshot `60dda81f` sin atribuirles una ejecución nueva: el GREEN final requiere el congelamiento, FIP/JUnit y evidencias externas que coordina el integrador. Este tranche sólo agrega probe, documentación y evidencia; conserva intacto `REMEDIATION_REGISTER.json`.

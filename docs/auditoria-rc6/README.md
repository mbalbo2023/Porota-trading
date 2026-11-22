# Auditoría técnica y funcional de Porota RC6

Corte físico del **22/11/2026**. La captura de timers y esquema comenzó a las **19:57:50 ART / 22:57:50 UTC**. La lectura de los calendarios y recibos de salud fue a las **20:08:47 ART / 23:08:47 UTC**.

| Capa | Autoridad |
| --- | --- |
| Fuente operativa | [Commit productivo](https://github.com/mbalbo2023/Porota-trading/commit/223725e2860b295533d480d6ad24aee46f098b8e) · árbol `bc8432a6d03f7fa4b44b46694486c60fe0787b3c` |
| Paquete original | [Predeploy #1294](https://github.com/mbalbo2023/Porota-trading/actions/runs/45077165989/artifacts/14776245742) · 9.100 pruebas |
| Instalación | [Deploy V2 #198](https://github.com/mbalbo2023/Porota-trading/actions/runs/45078550557/artifacts/14776396505) · PAPER / SIMULATION · 0 órdenes reales |
| Aplicación | [Panel, Observer y Scheduler después del deploy](https://github.com/mbalbo2023/Porota-trading/actions/runs/45079212842/artifacts/14776746555) |
| Configuración física y DB | [32 timers y catálogo SQLite](https://github.com/mbalbo2023/Porota-trading/actions/runs/45080576897/artifacts/14777037929) |
| Activación y recibos | [Directivas efectivas, preopen e IOL](https://github.com/mbalbo2023/Porota-trading/actions/runs/45081613140/artifacts/14777706130) |
| Correcciones candidatas | [PR #1251](https://github.com/mbalbo2023/Porota-trading/pull/1251) · 181 pruebas focalizadas · estado DRAFT |

## Navegación

- [Los 32 timers físicos, servicio, horarios, última ejecución y cruces](TIMERS.md).
- [RCA, revalidación y lecciones](RCA_Y_LECCIONES.md).
- [Runbook de desarrollo y release](../RUNBOOK.md).
- [Checkpoint anterior, ownership y recibos](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7087064703).

## Estado de la auditoría

- **PPI.** La [API REST](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/) pide `Settlement` en Current/Book. El cuerpo documentado de esas respuestas no lista el plazo. Capturar la respuesta con su consulta sellada, y conservar un valor literal sólo al observarlo en el cuerpo. El criterio de admisión de la clave PPI sigue en `bu_instrument_catalog.ppi_full_key_evidence_reason`. El [PR #1251](https://github.com/mbalbo2023/Porota-trading/pull/1251) corrige representaciones equivalentes y conserva el literal observado.
- **BONOS/ON.** Los ensayos aislados cubren identidad, VN, costos, libro posterior, firma y aplicación idempotente o parcial. El modo de venta nativa aún requiere proveedor de testigo, huella de clave/release, política económica y Book2 causal. Las dos huellas están sin fijar en el código productivo y el runtime no conecta el proveedor de testigo.
- **Scheduler.** 16 activos, 11 deshabilitados y 5 enmascarados. Última salud y exportador se cruzaron 2 s. El journal completo no estuvo accesible al usuario de inspección. Los cruces nominales de calendario se recogen aparte.
- **IOL y preopen.** Recibo de IOL en `REAUTH_REQUIRED`, HTTP 403 al renovar OAuth. Último preopen del viernes a las 10:20 ART con `NO_GO_PAPER_SCALPING`, anterior al release actual. La siguiente rueda ordinaria prevista es el 24/11, según el [feriado bursátil BYMA del 23/11](https://www.byma.com.ar/mercado/calendario-bursatil).
- **Código y datos.** Árbol completo de 1.275 archivos. 1.107 Python interpretados por AST, 0 errores de sintaxis, 0 grupos de blobs idénticos y 28 módulos sin referencias estáticas detectadas. Son candidatos a revisar también por ABI, importación dinámica, CI, manuales y systemd. La DB Observer auditada presenta 116 tablas, 30 índices nombrados y 19 triggers sobre WAL. Se consultó la estructura sin extraer registros.

## Operación y regeneración bajo demanda

### Cómo seguir velas, históricos y embudos

Partir del checkout fijado arriba. `bl_candle_engine.py` toma muestras `TRADE` de `market_snapshots` en orden de ID, asigna ventanas ART de 1m y 5m y guarda revisiones con `known_at`. Una consulta debe aplicar el corte sobre el fin de la barra y la hora de conocimiento. `rc6_local_eod_history.py` copia sólo el delta de revisiones aceptables a COLD después de 17:15 ART en rueda hábil. El diario de `TRADE_SAMPLES` registra cobertura parcial y minutos faltantes. El histórico externo tiene su política de identidad, calidad, proveedor y corte en `history_source_policy_rc6.py` y su conciliación en `fb_history_reconciler_rc6.py`.

`rc6_shadow_runtime/funnel.py` enlaza catálogo, descubrimiento, señal evaluada y candidata, economía, riesgo, apertura PAPER, salida y P&L. Las evaluaciones, los inputs distintos, los instrumentos y las posiciones tienen contadores propios. El funnel cruza evidencias nativas por versión, cohorte, identidad, reloj y moneda. `be_paper_engine.py`, `cf_intraday_scalping.py` y los motores por familia mantienen sus decisiones y motivos de admisión reproducibles.

### Recuperación del acceso complementario IOL

El operador consiente en la tablet con el callback `http://127.0.0.1:8765/callback` por el túnel ya configurado. Actions prepara state y PKCE nuevos con el cliente existente. Elegir **Solo lectura** en IOL. El proceso temporal confirma la lista de herramientas de lectura y actualiza la credencial de forma atómica. Revisar el recibo `AUTHENTICATED_READ_ONLY` para acreditar la autorización. La continuidad del pipeline y de PPI conserva su evidencia propia. [Documentación oficial de IOL MCP](https://mcp.invertironline.com/documentacion-tecnica).

### Generadores

Los generadores de esta carpeta consumen un árbol Git limpio y los dos JSON sanitizados que se descargan de las Actions enlazadas. Las salidas documentan el corte y se pueden renovar con los recibos del siguiente release.

```sh
python3 index_repo.py --source /ruta/al/checkout --tree bc8432a6d03f7fa4b44b46694486c60fe0787b3c --out inventario.json --markdown inventario.md
python3 timer_report.py --runtime porota-runtime-audit.json --effective porota-effective-schedule.json --next-day 2026-11-24 --json timers.json --markdown TIMERS.md
```

El inventario se enlaza por árbol y el reporte de timers por release observado. La documentación y sus generadores viven en la rama de control.

"""Technical detail belongs here; no host commands or mutations."""
import json
from pathlib import Path

from .components import fields, definition_list, detail, notice, metric
from .datasets import artifact, config, logs
from .projection import Page
from .view_common import render_table, WORKERS, WORKER_DETAIL, SOURCES, SOURCE_DETAIL


def render(p, destination, tab):
    if tab == "resumen":
        state = p.runtime
        cards = "".join((metric("Proceso runtime", state.get("process_state"), "observer_state", kind="status"),
                         metric("Lectura SQLite", "AVAILABLE" if p.store.connection else "NO_VERIFICADO", "mode=ro / query_only"),
                         metric("Dashboard", "REQUEST_RENDERED", "Esta solicitud, sin certificar host runtime"),
                         metric("Disco / última identidad Deploy", None, "Requiere snapshot operativo publicado")))
        return "<div class='metric-grid'>" + cards + "</div>" + render_table(p, destination, tab, p.workers(), "Salud crítica", WORKERS, WORKER_DETAIL)
    if tab == "salud":
        state = p.runtime
        cards = "".join((metric("Heartbeat del observer", state.get("heartbeat_freshness"),
                                "observer_state · reloj de proceso", kind="status"),
                         metric("Seguridad de órdenes reales", state.get("real_orders_sent"),
                                "Contador factual; desconocido no equivale a cero", kind="number"),
                         metric("Generación SHADOW", p.shadow["state"],
                                "Corte comprometido; revisar evidencia", kind="status")))
        return "<div class='metric-grid'>" + cards + "</div>" + render_table(
            p, destination, tab, p.workers(), "Salud de runtime y workers", WORKERS, WORKER_DETAIL,
            note="Integraciones tiene su propia vista. Un heartbeat o un HTTP 200 no acreditan actividad PAPER ni fills.")
    if tab == "integraciones":
        return render_table(p, destination, tab, p.sources(), "Salud de integración · sin autoridad BUY", SOURCES,
                            (*SOURCE_DETAIL, *fields("last_success_at|Último éxito|time;next_check|Próximo chequeo|time")),
                            identity=False)
    if tab == "workers":
        return render_table(p, destination, tab, p.workers(critical=False), "Workers & lag", WORKERS,
                            (*WORKER_DETAIL, *fields("failures|Fallos|number;lag|Lag (s)|number")))
    if tab == "scheduler":
        return render_table(p, destination, tab, artifact(p, "scheduler"), "Scheduler · snapshot read-only",
                            fields("unit|Unidad / job;state|Estado|status;freshness|Snapshot freshness|status;next_run|Próxima ejecución|time;last_run|Última ejecución|time"),
                            fields("enabled|Habilitado;active|Activo;last_result|Resultado servicio;exec_main_status|Exit status;description|Descripción;reason|Excepción"),
                            note="El timer activo no certifica ejecución exitosa. Estado y reloj de un snapshot vencido no son evidencia actual; esta pantalla no ejecuta comandos ni habilita timers.")
    if tab == "evidencia":
        cut = p.shadow
        pointer, manifest = cut.get("pointer", {}), cut.get("manifest", {})
        quality = cut["report"].get("input_quality", {})
        truncated = quality.get("observation_read_truncated") if isinstance(quality, dict) else None
        row = {"state": cut["state"], "reason": cut["reason"], "generation_id": pointer.get("generation_id"),
               "sequence": pointer.get("sequence"), "manifest_digest": pointer.get("manifest_sha256"),
               "source_watermark": manifest.get("source_watermark"), "configuration_fingerprint": manifest.get("configuration_fingerprint"),
               "members": manifest.get("files"), "retention": cut["report"].get("evidence_retention"),
               "preopen_digest": cut["report"].get("preopen_file"), "as_of": cut["report"].get("as_of"),
               "observation_coverage": ("COBERTURA_PARCIAL" if truncated is True else
                                        "SCOPE_COMPLETO" if truncated is False else "NO_VERIFICADO")}
        return (notice("Sólo CURRENT.json y su generación comprometida/coherente. El adapter requiere el reader canónico de #466; latest/checkpoint/status independientes no son autoridad.") +
                "<section class='panel'><h2>Validación — Camino a Producción</h2>"
                "<p>Consultar el estado y las brechas de M0–M11, uno por uno. "
                "El dinero real continúa bloqueado por política RC6.</p>"
                "<a class='text-link' href='/validacion'>Ver hitos M0–M11</a></section>" +
                "<section class='panel'><h2>Integridad de generación SHADOW</h2>" + definition_list(row, fields(
                    "state|Estado de verificación|status;as_of|Watermark as_of|time;reason|Motivo"))
                + detail(row, fields("observation_coverage|Cobertura de observaciones SHADOW|status;generation_id|Generation ID;sequence|Sequence|number;manifest_digest|Manifest digest;source_watermark|Source watermark;configuration_fingerprint|Configuration fingerprint;members|Report / status cross hashes;retention|Retención / pins / ACK;preopen_digest|Preopen freeze"), "sistema-evidencia", 1)
                + "</section>")
    if tab == "backups":
        try:
            policy = json.loads((Path(__file__).resolve().parents[1] /
                                 "ops/policy/rc6-disk-housekeeping-v1.json").read_text(encoding="utf-8"))
            suspended = policy["backups"]["suspended"] is True
        except (OSError, ValueError, KeyError, TypeError):
            suspended = False
        if suspended:
            page = Page("ops/policy/rc6-disk-housekeeping-v1.json",
                        [{"store": store, "state": "SUSPENDED_BY_OPERATOR",
                          "coverage": "NO_VERIFICADO", "protected": "NO_VERIFICADO"}
                         for store in ("general", "paper", "history")], 3, "AVAILABLE")
            return notice("Generación de backups suspendida por decisión del operador. "
                          "Esta vista no acredita el borrado de archivos previos; consultar el recibo de host.") + render_table(
                p, destination, tab, page, "Backups · política vigente",
                fields("store|Store;state|Estado|status;last_backup_at|Último backup|time;coverage|Coverage;protected|Protegido"),
                fields("last_checkpoint_at|Checkpoint|time;retention|Retención;reason|Excepción"))
        return render_table(p, destination, tab, artifact(p, "backups"), "Backups & protected stores",
                            fields("store|Store;state|Estado|status;last_backup_at|Último backup|time;coverage|Coverage;protected|Protegido"),
                            fields("last_checkpoint_at|Checkpoint|time;retention|Retención;reason|Excepción"))
    if tab == "logs":
        return render_table(p, destination, tab, logs(p), "Logs sanitizados · últimas 10 líneas", fields("state|Scope|status;line|Mensaje")) + "<a class='text-link' href='/api/trader/logs/download'>Descargar últimas líneas sanitizadas</a>"
    return render_table(p, destination, tab, config(p), "Configuración efectiva no secreta", fields("setting|Setting;value|Valor;origin|Origen;state|Estado|status"),
                        note="Configuración no secreta; los modos operativos continúan usando observer_state como autoridad.")

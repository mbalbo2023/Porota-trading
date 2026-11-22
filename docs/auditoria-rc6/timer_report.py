"""Render the installed timer inventory and calculate evidence-bounded overlaps."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo


AR = ZoneInfo("America/Argentina/Buenos_Aires")
CALENDAR = re.compile(r"(?:Next elapse:|Iteration #\d+:)\s+([A-Z][a-z]{2} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC)")
TASKS = {
    "1016-github-verify": "Verificación puntual histórica de GitHub. La finalidad está en el nombre y la fecha; cuerpo del script fuera del árbol auditado.",
    "a3-history-daily-rc6": "Importación incremental A3/CEM en el perfil DAILY_INCREMENTAL del servicio plantilla.",
    "a3-history-reconcile-rc6": "Conciliación A3/CEM en el perfil RECONCILE del servicio plantilla.",
    "a3-history-weekend-rc6": "Historia A3/CEM acotada de fin de semana en el perfil WEEKEND_DEEP.",
    "byma-morning-watch-rc6": "Consulta fuentes públicas BYMA y prepara señal de subastas/autoridad para preopen.",
    "candle-integrity-rc6": "Audita cobertura, frescura e integridad de velas en modo de observación.",
    "contract-evidence-hf6": "Publicador HF6 de evidencia de catálogo, mantenido como legado.",
    "contract-evidence-rc6": "Lote legado matinal de evidencia contractual RC6 y selección de rueda.",
    "contract-evidence-weekend-backfill-rc6": "Backfill contractual legado en una ventana puntual. Servicio no acreditado en esta captura.",
    "fast-functional-health-rc6": "Verifica Observer, guard PAPER, DB de solo lectura y señales de salud.",
    "full-db-integrity-rc6": "Ejecuta el control integral programado de SQLite; su periodicidad evita convertirlo en healthcheck ligero.",
    "history-postclose-rc6": "Reparación histórica posterior al cierre del proveedor; flujo distinto del EOD local.",
    "host-general-backup-rc6": "Perfil de copia general del host sujeto a política operativa.",
    "introspeccion-hf4": "Introspección histórica HF4 del estado operacional.",
    "introspection-publish-rc6": "Publica por GitHub el reporte de introspección sanitizado.",
    "introspection-rc6": "Genera introspección de runtime y encola alertas clasificadas.",
    "iol-oauth-health-rc6": "Comprueba la sesión OAuth de IOL y registra salud, renovación o reautenticación requerida.",
    "iol-shadow-collector-rc6": "Recoge referencias IOL complementarias, únicamente para observación SHADOW.",
    "live-decision-cockpit-rc6": "Recolector legado del cockpit de decisiones y observación.",
    "local-eod-history-rc6": "Drena el delta de la base operativa hacia EOD local mediante candado e importación idempotente.",
    "log-export-hf6": "Exportación de logs de soporte HF6. Conservación histórica.",
    "market-watch-silence-rc6": "Verifica silencios y frescura de ingesta en ventana de mercado.",
    "ppi-argentina-nightly-rc6": "Ingesta nocturna PPI Argentina de la etapa anterior; legado deshabilitado.",
    "preopen-rc6": "Evalúa T-45 y T-10 y publica el gate de apertura PAPER ligado al release.",
    "preopen": "Preopen legado separado de RC6 productivo. Servicio no acreditado.",
    "rc6-candle-inmarket-verify": "Verifica progresión intrarrueda de velas con la evidencia del Observer.",
    "runtime-evidence-rc6": "Publica identidad, estados y observación por instrumento con metadatos sanitizados.",
    "scheduler-export-hf6": "Exportador histórico del Scheduler HF6, reemplazado por RC6.",
    "scheduler-export-rc6": "Lee systemd con su candado, publica JSON atómico y alimenta el dashboard.",
    "snapshot-postclose-rc6": "Captura postcierre, Action 4, evidencia de decisiones y publicación de la campaña M0–M11.",
    "snapshot-preopen-rc6": "Captura el corte previo a la rueda para auditoría del panel.",
    "weekend-ingestion-audit-rc6": "Auditoría histórica puntual del fin de semana; ejecución en fecha fija.",
}


def parse_timestamp(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def local_time(raw):
    t = parse_timestamp(raw)
    return t.astimezone(AR).strftime("%d/%m %H:%M:%S") if t is not None else "—"


def occurrences(expr, start, end):
    result = subprocess.run(
        ["systemd-analyze","calendar","--base-time="+start.strftime("%Y-%m-%d %H:%M:%S UTC"),
         "--iterations=1500",expr], capture_output=True,text=True,timeout=12,check=False,
        env={**os.environ,"TZ":"UTC","LC_ALL":"C"})
    if result.returncode != 0:
        raise ValueError(f"Calendario no interpretable: {expr}")
    return [t for s in CALENDAR.findall(result.stdout) if (t := parse_timestamp(s)) and start <= t < end]


def calendar_pairs(settings, day):
    start = datetime.fromisoformat(day).replace(tzinfo=AR).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    due = defaultdict(set)
    failures = []
    for unit, definition in settings.items():
        if not definition["read"]:
            continue
        active = []
        for key, value in definition["directives"]:
            if key.startswith("On") and not value:
                active.clear()
            elif key == "OnCalendar":
                active.append(value)
        for pattern in active:
            try:
                for due_at in occurrences(pattern,start,end):
                    due[due_at].add(unit)
            except (ValueError,subprocess.TimeoutExpired) as exc:
                failures.append([unit,pattern,type(exc).__name__])
    pairs = Counter()
    first = {}
    for when, units in sorted(due.items()):
        for a,b in combinations(sorted(units),2):
            pairs[a,b] += 1
            first.setdefault((a,b),when.astimezone(AR).strftime("%H:%M:%S"))
    return {"date_ar":day,"events":sum(len(names) for names in due.values()),
            "pairs":[[a,b,n,first[a,b]] for (a,b),n in sorted(pairs.items())],"parse_failures":failures}


def report(runtime, definition, next_day):
    now = datetime.fromisoformat(runtime["observed_at"])
    today = now.astimezone(AR).date().isoformat()
    ranges = []
    rows = []
    for row in runtime["timers"]:
        timer, service = row["timer"],row["service"]
        name = row["unit"]
        a,b = (parse_timestamp(service.get(key)) for key in ("ExecMainStartTimestamp","ExecMainExitTimestamp"))
        if a and b and a<b and a.astimezone(AR).date().isoformat()==today:
            ranges.append((name,a,b))
        settings = definition["settings"].get(name,{}).get("directives",[])
        planned = [key+"="+val for key,val in settings if key.startswith("On")]
        if not planned:
            raw = timer.get("TimersCalendar") or timer.get("TimersMonotonic")
            planned = [raw.split(" ;",1)[0].lstrip("{ ")] if raw else []
        actual_result = service.get("Result") if a is not None and service.get("ExecMainCode") not in (None,"","0") else "sin ejecución acreditada"
        rows.append({"unit":name, "purpose":TASKS.get(name.removeprefix("porota-").removesuffix(".timer"),"Semántica pendiente de verificación"),
                     "physical_active":timer.get("ActiveState"),"installed_state":timer.get("UnitFileState"),
                     "activity":actual_result, "exit":service.get("ExecMainStatus") if a else None,
                     "last_run_ar":local_time(service.get("ExecMainStartTimestamp")),"next_ar":local_time(timer.get("NextElapseUSecRealtime")),
                     "effective":planned, "service":row["actual_service"],
                     "service_loaded":service.get("LoadState"),"dropins":row["timer_dropin_count"],"runner":row["entrypoints"]["files"]})
    overlaps = []
    for (na,a0,a1),(nb,b0,b1) in combinations(ranges,2):
        lower,upper=max(a0,b0),min(a1,b1)
        if lower<upper:
            overlaps.append({"units":[na,nb],"start_ar":lower.astimezone(AR).isoformat(),
                             "end_ar":upper.astimezone(AR).isoformat(),"seconds":(upper-lower).total_seconds()})
    return {"observed_at":runtime["observed_at"],"observed_ar":now.astimezone(AR).isoformat(),
            "deployment_sha":runtime["deployment_sha"],"rows":rows,
            "totals":dict(Counter(row["installed_state"] for row in rows)),
            "active":sum(row["physical_active"]=="active" for row in rows),
            "latest_intervals_today":len(ranges),"observed_overlaps":overlaps,
            "planned_today":calendar_pairs(definition["settings"],today),
            "planned_next":calendar_pairs(definition["settings"],next_day),
            "journal":runtime["journal"]["state"],"preopen":definition["preopen"],"iol":definition["iol"]}


def markdown(result):
    out = ["# Scheduler instalado por Porota RC6","",
           f"Captura {result['observed_ar']} (ART) · {result['observed_at']} (UTC) · release `{result['deployment_sha']}`.","",
           f"{len(result['rows'])} unidades · {result['active']} activas · {result['totals'].get('disabled',0)} deshabilitadas · {result['totals'].get('masked',0)} enmascaradas.","",
           "| Timer | Estado | Trabajo y destino | Programación visible | Última ejecución ART | Resultado |",
           "| --- | --- | --- | --- | --- | --- |"]
    for row in result["rows"]:
        sched = " · ".join(row["effective"]) or "—"
        out.append(f"| `{row['unit']}` | {row['physical_active']} / {row['installed_state']} | "
                   f"{row['purpose']} Servicio `{row['service']}`. | {sched} | {row['last_run_ar']} | "
                   f"{row['activity']}" + (f" ({row['exit']})" if row["exit"] is not None else "") + " |")
    out+=["","## Solapamientos","",
          f"Se pudieron comparar {result['latest_intervals_today']} últimos intervalos del día. Estado del journal `{result['journal']}`."]
    for row in result["observed_overlaps"]:
        out.append(f"- {' y '.join(row['units'])} se cruzaron {row['seconds']:g} segundos desde {row['start_ar']}.")
    for day in ("planned_today","planned_next"):
        value = result[day]
        out+=["",f"**Calendario nominal {value['date_ar']}.** {value['events']} disparos calendarizados y {len(value['pairs'])} pares con coincidencia exacta.",""]
        for a,b,n,first in value["pairs"]:
            out.append(f"- `{a}` y `{b}` · {n} ocasiones · primera a las {first} ART.")
    return "\n".join(out)+"\n"


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime",type=Path,required=True)
    p.add_argument("--effective",type=Path,required=True)
    p.add_argument("--next-day",required=True)
    p.add_argument("--json",type=Path,required=True)
    p.add_argument("--markdown",type=Path,required=True)
    x=p.parse_args()
    data=report(json.loads(x.runtime.read_text()),json.loads(x.effective.read_text()),x.next_day)
    x.json.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    x.markdown.write_text(markdown(data))

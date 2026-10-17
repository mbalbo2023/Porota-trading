#!/usr/bin/env python3
"""READ-ONLY/GitHub-hosted: build an offline peer-review packet. No broker or host access."""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PRODUCT = "47d949b72f0b3affffecdc6cc90d31ba1347d9b7"
GUARD = "3a1ce8a4000458982d6b31dec47e8269867e28ab"
DIAGNOSTIC = "95ea79450f681fb781046ff2cfce54101a435180"
DOCS = "dd1200430fe571765335b243120a7e393449887a"
REPORT = "docs/external_audit/rc6_shadow_20261017/REPORTE_TECNICO_PARA_AUDITOR_EXTERNO.md"
OUTPUT = "release-audit/POROTA_RC6_AUDITORIA_EXTERNA_2026-10-17.zip"
REPO = "mbalbo2023/Porota-trading"
HOST_EVIDENCE = [
    (12106506637, "39046830865", "perfil_5min"),
    (12106366647, "39047388608", "perfil_90s"),
    (12106577220, "39047764577", "salud_puntual"),
]
CRITICAL_FILES = [
    "rc6_shadow_runtime/worker.py",
    "rc6_shadow_runtime/timing.py",
    "rc6_shadow_runtime/persistence.py",
    "rc6_shadow_runtime/funnel.py",
    "scripts/rc6_deploy_generation_gate.py",
    "scripts/rc6_postdeploy_stress.py",
    "scripts/rc6_postdeploy_stress_host.py",
    ".github/workflows/porota-predeploy-v2.yml",
    ".github/workflows/porota-deploy-v2-promote.yml",
    "Dockerfile",
]
GUARD_PATHS = [
    "rc6_shadow_runtime/funnel.py",
    "rc6_shadow_runtime/persistence.py",
    "rc6_shadow_runtime/worker.py",
    "scripts/rc6_deploy_generation_gate.py",
    "scripts/rc6_postdeploy_stress.py",
    "scripts/rc6_postdeploy_stress_host.py",
    "tests/test_rc6_issue620_temporal.py",
    "tests/test_rc6_postdeploy_stress_contract.py",
]
NAME_EXCLUDE = re.compile(
    r"(^|/)(\.env(?:\.|$)|\.git/|\.ssh/|__pycache__/|private/|data/|secret[s]?/|"
    r"credential[s]?/|keys?/|certificate[s]?/|\.DS_Store$)", re.I
)
EXT_EXCLUDE = (".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".pyc", ".log")
SENSITIVE_LINE = re.compile(
    r"(?im)^\s*(?:[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)"
    r"\s*=\s*[^\s#][^\n]*$"
)

def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)

def files_at(sha: str) -> list[str]:
    return git("ls-tree", "-r", "--name-only", sha).decode().splitlines()

def include_product(path: str) -> bool:
    if NAME_EXCLUDE.search(path) or path.lower().endswith(EXT_EXCLUDE):
        return False
    if path in CRITICAL_FILES:
        return True
    if path.startswith(("rc6_shadow_runtime/", "rc6_dynamic_universe/", "rc6_audit_evidence/", "rc6_performance/")):
        return path.endswith((".py", ".c", ".h", ".json", ".md"))
    if path.startswith("scripts/"):
        name = path.rsplit("/",1)[-1].lower()
        return name.endswith(".py") and any(term in name for term in (
            "shadow", "stress", "deploy", "disk_", "image_source", "build_deploy", "artifact",
            "manifest", "runtime", "sqlite", "funnel", "source_authority", "preflight",
            "audit", "budget", "readiness", "postclose", "history", "capacity",
        ))
    if path.startswith("tests/"):
        name=path.rsplit("/",1)[-1].lower()
        return name.endswith(".py") and (name.startswith("test_rc6_") and
            any(term in name for term in ("shadow", "generation", "issue600", "issue603",
            "issue620", "postdeploy", "runtime", "stress", "capacity", "artifact", "sqlite",
            "preopen", "funnel", "safety", "timing", "native")))
    if path.startswith("ops/policy/"):
        return path.endswith(".json")
    if path.startswith(".github/workflows/"):
        return path.rsplit("/",1)[-1] in (
            "porota-predeploy-v2.yml","porota-deploy-v2-promote.yml","rc6-market-watch-readonly.yml")
    return path in ("Dockerfile", ".dockerignore", "docker-compose.yml", "docker-compose.yaml",
                    "requirements.txt", "requirements.lock.txt", "pyproject.toml",
                    "cg_paper_workspace.py", "bm_exit_supervisor.py",
                    "az_maintenance_job.py", "rc6_preopen.py")

def safe_source(path: str, raw: bytes) -> bool:
    if len(raw) > 3 * 1024 * 1024:
        return False
    if b"\x00" in raw:
        return False
    try:
        s=raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    # Exact source is preferred. If a line looks like a literal tracked secret, abort
    # rather than silently modify a program that the auditor might execute.
    if SENSITIVE_LINE.search(s):
        raise RuntimeError("POTENTIAL_TRACKED_SECRET_REQUIRES_REVIEW:" + path)
    return True

def sha256(x: bytes) -> str:
    return hashlib.sha256(x).hexdigest()

def load_bytes(sha: str, path: str) -> bytes:
    return git("show", f"{sha}:{path}")

def safe_name(name: str) -> str:
    p=Path(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or not name:
        raise RuntimeError("UNSAFE_PATH")
    return name

def scrub_summary(value: dict, run_id: str) -> dict:
    out = {key:value.get(key) for key in (
        "schema","status","started_at","finished_at","candidate_sha","read_only",
        "services_changed","ppi_watch_accessed","not_acceptance"
    ) if key in value}
    out["origin_run_id"] = run_id
    for key in ("runtime_before", "runtime_after"):
        src=value.get(key,{})
        if not isinstance(src,dict): continue
        x={k:src[k] for k in ("health_http","unauth_trading_http","heartbeat_age_seconds",
            "heartbeat_at","mode","real_orders_sent","session_state","process_state")
            if k in src}
        if isinstance(src.get("source_identity"),dict):
            x["source_identity"]={k:src["source_identity"][k] for k in
                ("source_sha","candidate_tree_sha") if k in src["source_identity"]}
        out[key]=x
    samples=[]
    pid_to_alias={}
    for sample in value.get("samples", []):
        if not isinstance(sample,dict): continue
        s={k:sample.get(k) for k in (
            "at","host_mem_available","host_cpu_count","host_load","host_swap_total",
            "host_swap_free","disk_free","inodes_free","cgroup_memory_current",
            "cgroup_swap_current","cgroup_memory_pressure","host_memory_pressure",
            "host_io_pressure","cgroup_memory_events","cgroup_cpu","host_vmstat","clock_ticks")
            if k in sample}
        if isinstance(sample.get("pointer"),dict):
            s["pointer"]={k:sample["pointer"][k] for k in (
                "as_of","sequence","generation_id","manifest_sha256") if k in sample["pointer"]}
        if isinstance(sample.get("status"),dict):
            s["status"]={k:sample["status"][k] for k in (
                "as_of","status","mode","generation_timing","runtime_sql_diagnostics",
                "error_class","sqlite_errorcode","sqlite_errorname")
                if k in sample["status"]}
        ps=[]
        for proc in sample.get("processes",[]):
            if not isinstance(proc,dict):continue
            pid=str(proc.get("pid",len(pid_to_alias)))
            if pid not in pid_to_alias:pid_to_alias[pid]="P"+str(len(pid_to_alias)+1).zfill(3)
            q={k:proc[k] for k in ("rss_bytes","nice","state","sched_runtime_ns","sched_wait_ns",
                "major_faults","minor_faults","system_ticks","user_ticks","thread_states")
                if k in proc}
            q["process_alias"]=pid_to_alias[pid]
            if isinstance(proc.get("memory"),dict):
                q["memory"]={k:proc["memory"][k] for k in ("VmRSS","VmSwap","RssAnon","RssFile")
                    if k in proc["memory"]}
            if isinstance(proc.get("io"),dict):
                q["io"]={k:proc["io"][k] for k in ("read_bytes","write_bytes","rchar","wchar")
                    if k in proc["io"]}
            ps.append(q)
        s["processes"]=ps
        samples.append(s)
    out["samples"]=samples
    out["redaction"]="IP/hostname/private paths/auth/real PID values and Docker IDs intentionally omitted"
    return out

def download_artifact(id: int) -> bytes:
    # GitHub Actions token is only used in this GitHub-hosted audit packet job.
    # gh api follows the binary redirect and writes the content to stdout.
    return subprocess.check_output(["gh", "api", f"repos/{REPO}/actions/artifacts/{id}/zip"],
                                   stderr=subprocess.DEVNULL,timeout=90)

def zinfo(path: str) -> zipfile.ZipInfo:
    info=zipfile.ZipInfo(safe_name(path), date_time=(2026,10,17,12,0,0))
    info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=(0o644 << 16)
    return info

def main():
    current=git("rev-parse","HEAD").decode().strip()
    git("merge-base", "--is-ancestor", PRODUCT, current)
    known_product=set(files_at(PRODUCT))
    missing=[p for p in CRITICAL_FILES if p not in known_product]
    if missing: raise RuntimeError("CRITICAL_SOURCE_NOT_IN_FROZEN_PRODUCT:"+",".join(missing))
    if git("rev-parse",GUARD).decode().strip()!=GUARD:
        raise RuntimeError("GUARD_REF_NOT_EXACT")
    if git("rev-parse",DIAGNOSTIC).decode().strip()!=DIAGNOSTIC:
        raise RuntimeError("DIAGNOSTIC_REF_NOT_EXACT")
    pkg:dict[str,bytes]={}
    origins:dict[str,dict]={}
    def add(name: str, raw: bytes, origin: str):
        name=safe_name(name)
        if name in pkg:raise RuntimeError("DUPLICATE_PATH:"+name)
        pkg[name]=raw
        origins[name]={"origin":origin,"sha256":sha256(raw),"bytes":len(raw)}
    for path in files_at(PRODUCT):
        if include_product(path):
            raw=load_bytes(PRODUCT,path)
            if safe_source(path,raw):add("codigo/product_619/"+path,raw,PRODUCT+":"+path)
    if len([k for k in pkg if k.startswith("codigo/product_619/")]) < 35:
        raise RuntimeError("INSUFFICIENT_SOURCE_FILES")
    for path in GUARD_PATHS:
        raw=load_bytes(GUARD,path)
        if safe_source(path,raw):
            add("codigo/guard_622/"+path,raw,GUARD+":"+path)
    diff=git("diff","--binary",PRODUCT,GUARD,"--",*GUARD_PATHS)
    add("codigo/DIFERENCIAS_PR622_vs_619.patch",diff,GUARD+"-vs-"+PRODUCT)
    for path in ("scripts/rc6_native620_passive_profile.py",
                 "scripts/rc6_native619_health_trace_readonly.py"):
        try:raw=load_bytes(DIAGNOSTIC,path)
        except subprocess.CalledProcessError:continue
        if safe_source(path,raw):
            add("codigo/diagnostic_621/"+path,raw,DIAGNOSTIC+":"+path)
    for path in ("docs/releases/RC6_ISSUE620_20261017.md",
                 "docs/releases/RC6_ISSUE603_20261016.md"):
        try:raw=load_bytes(DOCS,path)
        except subprocess.CalledProcessError:continue
        if safe_source(path,raw):
            add("documentacion/canonic_github/"+path.rsplit("/",1)[-1],raw,DOCS+":"+path)
    add("documentacion/REPORTE_TECNICO_PARA_AUDITOR_EXTERNO.md",
        Path(REPORT).read_bytes(),current+":"+REPORT)
    # Download three small GitHub-hosted diagnostic artifacts: sanitization
    # is allowlist-based and deliberately drops tokens/network/private details.
    for artifact_id,run,label in HOST_EVIDENCE:
        try:
            raw=download_artifact(artifact_id)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                candidate=next((x for x in z.namelist() if x.endswith("host-summary.json")),None)
                if candidate is None:raise RuntimeError("HOST_SUMMARY_MISSING")
                source=json.loads(z.read(candidate))
            data=json.dumps(scrub_summary(source,run),sort_keys=True,ensure_ascii=False,indent=2).encode()
            add(f"evidencia/{label}_sanitizado.json",data,f"GitHub Actions run {run} artifact {artifact_id}")
        except Exception as ex:
            # This is explicitly partial, not a fictitious host receipt.
            print("EVIDENCE_UNAVAILABLE",run,type(ex).__name__)
            raise RuntimeError("REQUIRED_DIAGNOSTIC_ARTIFACT_UNAVAILABLE:"+run)
    summary={
      "packet_version":"RC6_SHADOW_OFFLINE_AUDIT_20261017_V1",
      "generated_in":"GitHub Actions isolated audit branch, no DO runtime interaction",
      "baseline_product_sha":PRODUCT,"guard_only_sha":GUARD,
      "diagnostic_sha":DIAGNOSTIC,"docs_sha":DOCS,
      "reconciliation":{
        "product619":"installed_NOT_ACCEPTED","guard622":"CI_CERTIFIED_NOT_DEPLOYED",
        "shadow_2gen":"RED","droplet_stress405":"NOT_EXECUTED","stress_restore":"NOT_EXECUTED",
        "paper_orders":"INTEGER_0_IN_SAMPLED_PROBES","ppi_watch":"NOT_ACCESSED_IN_SAMPLED_PROBES"
      },
      "limit":"No production DB, credentials, external providers, runtime binaries or authorized performance-fix claim",
      "file_count_without_manifests":len(pkg)
    }
    add("INVENTARIO.json",json.dumps({"summary":summary,"files":origins},sort_keys=True,
        indent=2,ensure_ascii=False).encode(),"local audit pack builder")
    help_text=(
      "# LECTURA DEL PAQUETE\n\n"
      "1. Abrir documentacion/REPORTE_TECNICO_PARA_AUDITOR_EXTERNO.md.\n"
      "2. codigo/product_619 contiene archivos seleccionados exactos del SHA instalado "
      "(NO el repositorio total). codigo/guard_622 tiene 8 archivos completos y diff, "
      "SIN deploy; codigo/diagnostic_621 documenta métricas read-only.\n"
      "3. Evidencia de sondas es sanitizada por allowlist: PIDs e IP no forman parte "
      "del material. Ver origin_run_id dentro de cada JSON.\n"
      "4. Inventario identifica cada archivo, fuente exacta y sha256; SHA256SUMS.txt "
      "permite detección de corrupción. Excluidos datos productivos, .env, claves y "
      "cualquier payload no incluido deliberadamente.\n"
      "5. No sostener que una prueba CI demuestra aceptación nativa o que este paquete "
      "permite recrear el estado del Droplet; el propósito es auditoría de arquitectura, "
      "reproducción sintética y revisión de RCA.\n"
      "\n## Valores materiales para auditor\n"
      "1vCPU, 1024MiB RAM,25GiB disco,swap ~2GiB, poll30s, freshness120s, "
      "nice10,SQL2s,stress 512MiB/.75CPU, reserva host128MiB RAM y2GiB disco.\n"
      "Pair real60.835+30+69.032=159.867s>120. MemAvailable mínima70,311,936B. "
      "No hubo estrés nativo del motor sucesor.\n"
      "\nNo usar el contenido del paquete para órdenes financieras ni para acceder a brokers.\n"
    ).encode()
    add("LEEME_PRIMERO.md",help_text,"local audit pack builder")
    hashes="\n".join(f"{sha256(pkg[k])}  {k}" for k in sorted(pkg))+"\n"
    pkg["SHA256SUMS.txt"]=hashes.encode()
    out=Path(OUTPUT);out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for name in sorted(pkg):
            z.writestr(zinfo(name),pkg[name])
    with zipfile.ZipFile(out) as z:
        bad=z.testzip()
        if bad:raise RuntimeError("ZIP_FAILED:"+bad)
        for line in hashes.strip().splitlines():
            checksum,name=line.split("  ",1)
            if sha256(z.read(name))!=checksum:raise RuntimeError("FILE_HASH_MISMATCH:"+name)
    print(json.dumps({"status":"GREEN_PACKET_ONLY","zip":str(out),"files":len(pkg),
      "size":out.stat().st_size,"sha256":sha256(out.read_bytes()),"product_sha":PRODUCT,
      "guard_sha":GUARD},sort_keys=True))

if __name__ == "__main__":
    main()

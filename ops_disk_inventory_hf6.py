#!/usr/bin/env python3
"""Inventario de disco HF6: clasifica antes de eliminar y no modifica nada."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PRESERVE = {
    "paper_v17": "DB, ledger y estados PAPER",
    "introspection": "evidencia operativa",
    "reports": "informes y aprendizaje",
    "backups": "copias existentes; retención requiere autorización",
    "model_cache": "modelos persistentes",
    "sre_vector_db": "índice SRE persistente",
}


def command(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30, check=False)
    return {"returncode": result.returncode, "stdout": result.stdout[:200000],
            "stderr": result.stderr[:4000]}


def tree_size(path):
    if not path.exists() or path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in files:
            item = Path(root) / name
            try:
                if not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                continue
    return total


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def inventory(root):
    usage = shutil.disk_usage(root)
    categories = []
    for relative, reason in PRESERVE.items():
        location = root / (relative if relative in {"model_cache", "sre_vector_db"}
                           else f"data/{relative}")
        categories.append({"path": str(location), "bytes": tree_size(location),
                           "classification": "PRESERVE", "reason": reason})
    logs = root / "data/logs"
    categories.append({"path": str(logs), "bytes": tree_size(logs),
                       "classification": "ROTATE_ONLY", "reason": "logs del runtime; conservar ventana vigente"})
    publish = root / "data/introspection_publish"
    categories.append({"path": str(publish), "bytes": tree_size(publish),
                       "classification": "REGENERABLE", "reason": "copia sanitizada; no es fuente autoritativa"})
    docker_df = command("docker", "system", "df", "--format", "{{json .}}")
    containers = command("docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}")
    images = command("docker", "image", "ls", "--no-trunc", "--format", "{{json .}}")
    recommendations = [
        {"class": "SAFE_AFTER_RUNTIME_VALIDATION", "target": "Docker build cache no usado",
         "action": "medir, limpiar sólo cache de build y volver a medir; no eliminar imágenes"},
        {"class": "REVIEW", "target": "contenedores detenidos",
         "action": "confirmar que no sean rollback ni evidencia antes de retirar"},
        {"class": "REVIEW", "target": "imágenes sin contenedor",
         "action": "proteger imagen activa y rollback; eliminar sólo IDs enumerados y aprobados"},
        {"class": "KEEP", "target": "DB, WAL, ledger, históricos, velas, aprendizaje, .env y secretos",
         "action": "no borrar ni reescribir"},
    ]
    return {
        "schema": "porota-disk-inventory-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root), "filesystem": {"total": usage.total, "used": usage.used,
        "free": usage.free, "used_pct": round(usage.used / usage.total * 100, 2)},
        "categories": sorted(categories, key=lambda item: item["bytes"], reverse=True),
        "docker": {"system_df": docker_df, "containers": containers, "images": images},
        "recommendations": recommendations, "modified": False, "deleted_bytes": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/porota-trading"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    report = inventory(args.root.resolve())
    destination = (args.output_dir or args.root / "data/introspection")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = destination / f"disk_inventory_hf6_{stamp}.json"
    atomic_json(path, report)
    compact = json.dumps({"status": "OK", "used_pct": report["filesystem"]["used_pct"],
                          "free_bytes": report["filesystem"]["free"], "modified": False,
                          "file": path.name}, separators=(",", ":"))
    print(compact)
    if sys.stdout.isatty():
        print("\033]52;c;" + base64.b64encode(compact.encode()).decode() + "\a", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

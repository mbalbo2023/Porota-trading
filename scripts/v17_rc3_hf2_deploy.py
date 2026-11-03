#!/usr/bin/env python3
"""Despliegue reanudable RC3-HF2 desde el host de Porota.

Contrato: sudo no interactivo, sin backup previo, HF1 activo durante build y
pruebas, rollback a HF1 ante falla crítica y backup general solo al final.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


HF1_COMMIT = "1776fd0c29feb905451cff24bb3bf62f8debe724"
HF1_IMAGE = "porota-trading-bot:17.0.0-rc3-hf1"
HF2_VERSION = "17.0.0-rc3-hf2"
HF2_IMAGE = f"porota-trading-bot:{HF2_VERSION}"
RESULT_NAME = "v17_rc3_hf2_deploy_result.json"
LOCK_PATH = Path("/tmp/porota-v17-rc3-hf2-deploy.lock")
MIN_FREE_BYTES = 4 * 1024**3


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{stamp()}] {message}", flush=True)


def run(args, *, cwd: Path | None = None, capture=False, check=True,
        echo_capture=True):
    printable = " ".join(str(item) for item in args)
    log(f"EJECUTANDO={printable}")
    completed = subprocess.run(
        [str(item) for item in args], cwd=str(cwd) if cwd else None,
        check=False, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if capture and completed.stdout and echo_capture:
        print(completed.stdout.rstrip(), flush=True)
    if check and completed.returncode:
        raise RuntimeError(f"Comando falló ({completed.returncode}): {printable}")
    return completed


def docker(*args, capture=False, check=True, echo_capture=True):
    return run(("sudo", "-n", "docker", *args), capture=capture, check=check,
               echo_capture=echo_capture)


def write_result(repo: Path, payload: dict) -> None:
    target = repo / RESULT_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)


def validate_commit(repo: Path, commit: str) -> None:
    if not commit or any(char not in "0123456789abcdef" for char in commit.lower()):
        raise RuntimeError("El commit HF2 no es un SHA hexadecimal")
    resolved = run(("git", "rev-parse", f"{commit}^{{commit}}"), cwd=repo, capture=True)
    if resolved.stdout.strip().splitlines()[-1] != commit:
        raise RuntimeError("El commit resuelto no coincide exactamente con el solicitado")
    parents = run(
        ("git", "show", "-s", "--format=%P", commit), cwd=repo, capture=True
    ).stdout.strip().splitlines()[-1].split()
    if parents != [HF1_COMMIT]:
        raise RuntimeError(f"HF2 debe tener como único padre HF1; padres={parents}")
    run(("git", "diff", "--quiet"), cwd=repo)
    run(("git", "diff", "--cached", "--quiet"), cwd=repo)


def extract_git_archive(repo: Path, commit: str, build_dir: Path) -> None:
    build_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.NamedTemporaryFile(prefix="porota-hf2-", suffix=".tar") as archive_file:
        run(("git", "archive", "--format=tar", "--output", archive_file.name, commit), cwd=repo)
        with tarfile.open(archive_file.name, "r:") as archive:
            base = build_dir.resolve()
            for member in archive.getmembers():
                target = (build_dir / member.name).resolve()
                if base != target and base not in target.parents:
                    raise RuntimeError(f"Ruta insegura en git archive: {member.name}")
                if member.issym() or member.islnk():
                    raise RuntimeError(f"Enlace no admitido en git archive: {member.name}")
            archive.extractall(build_dir)


def image_version() -> str:
    result = docker(
        "run", "--rm", "--entrypoint", "python", HF2_IMAGE,
        "-c", "import _version; print(_version.VERSION)", capture=True,
    )
    return result.stdout.strip().splitlines()[-1]


def health_payload(timeout_seconds=120) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = "sin intento"
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                status = response.status
            if status == 200 and payload.get("status") == "ok":
                return payload
            last_error = f"HTTP {status}: {payload}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(5)
    raise RuntimeError(f"Health no quedó operativo: {last_error}")


def container_image(name: str) -> str:
    result = docker("inspect", "--format", "{{.Config.Image}}", name, capture=True)
    return result.stdout.strip().splitlines()[-1]


def selected_env(name: str) -> dict:
    result = docker(
        "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", name,
        capture=True, echo_capture=False,
    )
    allowed = {
        "PAPER_AI_GATE_MODE", "PAPER_ECONOMIC_GATE_MODE",
        "PPI_BACKGROUND_INGEST_SECONDS", "PAPER_FOCUS_MINIMUM_FOR_OPENINGS",
        "PAPER_SIGNAL_MIN_SAMPLES", "PAPER_SIGNAL_WINDOW_MINUTES",
    }
    values = {}
    gemini_names = []
    for raw in result.stdout.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.startswith("GEMINI"):
            gemini_names.append(key)
        if key in allowed:
            values[key] = value
    values["GEMINI_ENV_COUNT"] = len(gemini_names)
    return values


def database_state(repo: Path) -> dict:
    database = repo / "data" / "paper_v17" / "observer_v17.db"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT mode,process_state,session_state,ppi_auth,real_orders_sent "
            "FROM observer_state WHERE id=1"
        ).fetchone()
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("Falta observer_state id=1")
    return {**dict(row), "quick_check": check}


def validate_runtime(repo: Path) -> dict:
    health = health_payload()
    if health.get("version") != HF2_VERSION:
        raise RuntimeError(f"Health reporta versión incorrecta: {health}")
    images = {
        name: container_image(name)
        for name in ("porota_production_observer", "porota_production_dashboard")
    }
    if set(images.values()) != {HF2_IMAGE}:
        raise RuntimeError(f"Contenedores con imagen incorrecta: {images}")
    mode = json.loads((repo / "data" / "operation_mode.json").read_text(encoding="utf-8"))
    expected_mode = mode.get("mode") == "PRODUCTION_PAPER"
    expected_execution = mode.get("execution") == "SIMULATED"
    expected_apis = (
        mode.get("apis", {}).get("PPI_ORDERS") == "BLOCKED"
        and mode.get("apis", {}).get("PYTHON_MATH_ENGINE") == "ACTIVE"
    )
    if not (expected_mode and expected_execution and expected_apis):
        raise RuntimeError(f"Manifiesto operativo inseguro: {mode}")
    env = selected_env("porota_production_observer")
    expected_env = {
        "PAPER_AI_GATE_MODE": "OFF", "PAPER_ECONOMIC_GATE_MODE": "SHADOW",
        "PPI_BACKGROUND_INGEST_SECONDS": "21600",
        "PAPER_FOCUS_MINIMUM_FOR_OPENINGS": "4",
        "PAPER_SIGNAL_MIN_SAMPLES": "6", "PAPER_SIGNAL_WINDOW_MINUTES": "90",
        "GEMINI_ENV_COUNT": 0,
    }
    if env != expected_env:
        raise RuntimeError(f"Entorno defensivo incorrecto: {env}")
    state = database_state(repo)
    if state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
        raise RuntimeError(f"Estado PAPER inseguro: {state}")
    if state["quick_check"] != "ok":
        raise RuntimeError(f"SQLite no supera quick_check: {state}")
    return {"health": health, "images": images, "mode": mode, "env": env, "db": state}


def activate(repo: Path, commit: str) -> None:
    run(("git", "checkout", "--detach", commit), cwd=repo)
    run(("sudo", "-n", "python3", "porota_mode_manager.py", "simulation"), cwd=repo)


def rollback(repo: Path) -> dict:
    outcome = {"attempted": True, "commit": HF1_COMMIT, "image": HF1_IMAGE}
    try:
        run(("git", "checkout", "--detach", HF1_COMMIT), cwd=repo)
        run(("sudo", "-n", "python3", "porota_mode_manager.py", "simulation"), cwd=repo)
        payload = health_payload()
        if payload.get("version") != "17.0.0-rc3-hf1":
            raise RuntimeError(f"Rollback con health inesperado: {payload}")
        outcome.update(status="OK", health=payload)
    except Exception as exc:
        outcome.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
    return outcome


def post_validation_backup(repo: Path) -> dict:
    script = repo / "scripts" / "v17_host_general_backup.py"
    try:
        result = run(
            ("sudo", "-n", "python3", script, "--source", repo,
             "--destination", "/opt/porota-backups"), capture=True,
        )
        lines = result.stdout.strip().splitlines()
        start = next(index for index, line in enumerate(lines) if line.lstrip().startswith("{"))
        return json.loads("\n".join(lines[start:]))
    except Exception as exc:
        return {"status": "WARNING", "error": f"{type(exc).__name__}: {exc}",
                "predeploy_step": False, "operational_impact": "NONE"}


def deploy(repo: Path, commit: str, build_root: Path) -> int:
    activated = False
    result = {"schema_version": 1, "started_at": stamp(), "target_commit": commit,
              "target_image": HF2_IMAGE, "predeploy_backup": False}
    try:
        log("FASE=PRECONDICIONES")
        run(("sudo", "-n", "true"))
        if shutil.disk_usage(build_root.parent).free < MIN_FREE_BYTES:
            raise RuntimeError("Menos de 4 GiB libres para construir de forma aislada")
        validate_commit(repo, commit)
        docker("image", "inspect", HF1_IMAGE, capture=True)

        build_dir = build_root / ("hf2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        log(f"FASE=CONSTRUCCION_AISLADA DIRECTORIO={build_dir}")
        extract_git_archive(repo, commit, build_dir)
        docker("build", "--pull=false", "-t", HF2_IMAGE, str(build_dir))
        if image_version() != HF2_VERSION:
            raise RuntimeError("La identidad interna de la imagen HF2 no coincide")

        log("FASE=PRUEBAS_IMAGEN")
        docker("run", "--rm", "--entrypoint", "pytest", HF2_IMAGE, "-q")

        log("FASE=ACTIVACION_SIN_BACKUP_PREVIO")
        activate(repo, commit)
        activated = True

        log("FASE=VALIDACION_CRITICA")
        runtime = validate_runtime(repo)
        result.update(status="HF2_OPERATIVO", runtime=runtime, build_dir=str(build_dir))

        log("FASE=BACKUP_GENERAL_POSTERIOR")
        backup = post_validation_backup(repo)
        result["backup"] = backup
        if backup.get("status") != "OK":
            result["status"] = "HF2_OPERATIVO_CON_ADVERTENCIA_BACKUP"
        result["finished_at"] = stamp()
        write_result(repo, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as exc:
        result.update(status="FAILED", error=f"{type(exc).__name__}: {exc}",
                      failed_at=stamp(), activated=activated)
        if activated:
            log("FASE=ROLLBACK_HF1")
            result["rollback"] = rollback(repo)
        write_result(repo, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="/opt/porota-trading")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--build-root", default="/opt/porota-builds")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    build_root = Path(args.build_root).resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("DESPLIEGUE_YA_EN_PROGRESO")
        return deploy(repo, args.commit.lower(), build_root)


if __name__ == "__main__":
    raise SystemExit(main())

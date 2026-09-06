#!/usr/bin/env python3
"""Backup general del host, separado del despliegue y sin depender de Docker.

SQLite se copia con su API online; WAL/SHM nunca se archivan directamente.
Los secretos quedan excluidos salvo autorización explícita del operador.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_PARTS = ("data", "sre_vector_db")
SKIP_NAMES = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".hypothesis",
    # Nunca anidar copias previas ni fixtures de restauración en un backup
    # general: son derivados, volátiles y pueden contener .env históricos.
    "backups", "restore_test",
}
SECRET_FILENAMES = {".env"}


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(target), timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with sqlite3.connect(str(target)) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite inválido después del backup: {source}: {result}")


def _copy_tree(source: Path, staging: Path) -> list[str]:
    copied = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in SKIP_NAMES for part in relative.parts):
            continue
        if path.name in SECRET_FILENAMES:
            continue
        # Los symlinks son metadatos/atajos del árbol vivo. Nunca se siguen ni
        # se archivan: el archivo regular de destino, si pertenece al árbol,
        # aparece por su propia ruta en el recorrido y se copia por separado.
        if path.is_symlink():
            continue
        if path.is_dir():
            continue
        if path.name.endswith(("-wal", "-shm")):
            continue
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".db":
            _sqlite_backup(path, target)
        else:
            shutil.copy2(path, target)
        copied.append(str(relative))
    return copied


def _archive_verified_files(staging: Path, final: Path, copied: list[str]) -> None:
    """Archiva únicamente rutas estables ya copiadas y verificadas.

    No se recorre ``staging`` de forma recursiva: una base en modo WAL puede
    crear y retirar ``-wal``/``-shm`` entre ``listdir`` y ``lstat``. Esos
    auxiliares nunca pertenecen al backup y tampoco deben provocar una carrera.
    """
    files = sorted({*copied, "BACKUP_MANIFEST.json"})
    directories = {Path(".")}
    for relative in files:
        parent = Path(relative).parent
        while parent != Path("."):
            directories.add(parent)
            parent = parent.parent
    with tarfile.open(final, "w:gz", compresslevel=6) as archive:
        archive.add(staging, arcname="porota-trading", recursive=False)
        for relative in sorted(directories - {Path(".")},
                               key=lambda item: (len(item.parts), str(item))):
            archive.add(staging / relative,
                        arcname=str(Path("porota-trading") / relative), recursive=False)
        for relative in files:
            source = staging / relative
            if not source.is_file() or source.is_symlink():
                raise RuntimeError(f"Archivo verificado ausente o inseguro: {relative}")
            archive.add(source, arcname=str(Path("porota-trading") / relative),
                        recursive=False)


def create_backup(source: Path, destination: Path, *, include_secrets=False) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError(f"Origen inexistente: {source}")
    if destination == source or source in destination.parents:
        raise ValueError("El destino no puede estar dentro del árbol de origen")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"porota-general-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="porota-general-") as temporary:
        staging = Path(temporary) / "porota-trading"
        copied = []
        for name in DEFAULT_PARTS:
            part = source / name
            if part.is_dir():
                copied.extend(f"{name}/{item}" for item in _copy_tree(part, staging / name))
        for name in ("_version.py", "docker-compose.yml"):
            item = source / name
            if item.is_file():
                (staging / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, staging / name)
                copied.append(name)
        if include_secrets:
            secret = source / ".env"
            if secret.is_file():
                shutil.copy2(secret, staging / ".env")
                os.chmod(staging / ".env", 0o600)
                copied.append(".env")
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
            "secrets_included": bool(include_secrets),
            "excluded_subtrees": ["data/backups", "data/restore_test"],
            "nested_secret_files_excluded": True,
            "files": copied,
        }
        (staging / "BACKUP_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _archive_verified_files(staging, final, copied)
    os.chmod(final, 0o600)
    with tarfile.open(final, "r:gz") as archive:
        names = archive.getnames()
        if "porota-trading/BACKUP_MANIFEST.json" not in names:
            raise RuntimeError("El archivo final no contiene manifiesto")
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    result = {
        "status": "OK",
        "archive": str(final),
        "sha256": digest,
        "size_bytes": final.stat().st_size,
        "files": len(copied),
        "secrets_included": bool(include_secrets),
        "docker_required": False,
        "predeploy_step": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/opt/porota-trading")
    parser.add_argument("--destination", default="/opt/porota-backups")
    parser.add_argument("--include-secrets", action="store_true")
    args = parser.parse_args()
    create_backup(Path(args.source), Path(args.destination),
                  include_secrets=args.include_secrets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

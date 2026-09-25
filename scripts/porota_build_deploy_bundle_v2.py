#!/usr/bin/env python3
"""Build a canonical RC6 deploy-source bundle without manual file lists."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

from scripts.porota_validate_deploy_artifact import is_runtime_relevant

METADATA_PREFIXES = ("ops/policy/", "ops/state/")


def tracked_files(repo_root: Path) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z"], text=False
    )
    return sorted(x.decode("utf-8") for x in out.split(b"\0") if x)


def select_bundle_paths(paths: list[str]) -> list[str]:
    return sorted({
        p for p in paths
        if is_runtime_relevant(p) or p.startswith(METADATA_PREFIXES)
    })


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bundle(repo_root: Path, output: Path, manifest_out: Path | None = None) -> dict:
    selected = select_bundle_paths(tracked_files(repo_root))
    missing = [p for p in selected if not (repo_root / p).is_file()]
    if missing:
        raise RuntimeError("TRACKED_BUNDLE_INPUT_MISSING:" + ",".join(missing))

    manifest = {
        "schema_version": 1,
        "status": "GREEN",
        "files": [
            {
                "path": p,
                "sha256": sha256(repo_root / p),
                "bytes": (repo_root / p).stat().st_size,
            }
            for p in selected
        ],
    }
    manifest["file_count"] = len(manifest["files"])

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for rel in selected:
                    src = repo_root / rel
                    info = tar.gettarinfo(str(src), arcname=rel)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with src.open("rb") as f:
                        tar.addfile(info, f)
                data=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
                info=tarfile.TarInfo("POROTA_DEPLOY_BUNDLE_MANIFEST.json")
                info.size=len(data); info.mtime=0; info.uid=0; info.gid=0
                import io
                tar.addfile(info, io.BytesIO(data))

    manifest["bundle_sha256"] = sha256(output)
    if manifest_out:
        manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    return manifest


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",default=".")
    ap.add_argument("--output",required=True)
    ap.add_argument("--manifest-out")
    args=ap.parse_args()
    result=build_bundle(
        Path(args.repo_root).resolve(),
        Path(args.output).resolve(),
        Path(args.manifest_out).resolve() if args.manifest_out else None,
    )
    print(json.dumps(result,indent=2,sort_keys=True))
    print(f"POROTA_DEPLOY_BUNDLE_V2=GREEN|files={result['file_count']}|sha256={result['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a canonical RC6 deploy-source bundle without manual file lists."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

try:
    from scripts.porota_artifact_provenance import (
        BUNDLE_MANIFEST_NAME, SOURCE_MANIFEST_NAME, bundle_manifest, canonical_bytes,
        create_source_manifest, is_bundle_path, verify_source_manifest,
    )
except ModuleNotFoundError:
    from porota_artifact_provenance import (
        BUNDLE_MANIFEST_NAME, SOURCE_MANIFEST_NAME, bundle_manifest, canonical_bytes,
        create_source_manifest, is_bundle_path, verify_source_manifest,
    )


def select_bundle_paths(paths: list[str]) -> list[str]:
    return sorted({p for p in paths if is_bundle_path(p)})


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bundle(repo_root: Path, output: Path, manifest_out: Path | None = None,
                 source_manifest_path: Path | None = None) -> dict:
    source = (verify_source_manifest(repo_root, source_manifest_path)
              if source_manifest_path else create_source_manifest(repo_root))
    selected = [row["path"] for row in source["files"] if row["bundle_required"]]
    missing = [p for p in selected if not (repo_root / p).is_file()]
    if missing:
        raise RuntimeError("TRACKED_BUNDLE_INPUT_MISSING:" + ",".join(missing))

    manifest = bundle_manifest(source)

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
                for name, data in ((BUNDLE_MANIFEST_NAME, canonical_bytes(manifest)),
                                   (SOURCE_MANIFEST_NAME, canonical_bytes(source))):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.mode = 0o644
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
    ap.add_argument("--source-manifest")
    args=ap.parse_args()
    result=build_bundle(
        Path(args.repo_root).resolve(),
        Path(args.output).resolve(),
        Path(args.manifest_out).resolve() if args.manifest_out else None,
        Path(args.source_manifest).resolve() if args.source_manifest else None,
    )
    print(json.dumps(result,indent=2,sort_keys=True))
    print(f"POROTA_DEPLOY_BUNDLE_V2=GREEN|files={result['file_count']}|sha256={result['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

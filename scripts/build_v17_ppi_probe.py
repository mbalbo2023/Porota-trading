"""Empaqueta exclusivamente el diagnóstico y la barrera probados, sin secretos."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCES={'__main__.py':'scripts/v17_ppi_public_probe.py',
         'bd_ppi_readonly_guard.py':'bd_ppi_readonly_guard.py',
         '_version.py':'_version.py'}


def build(destination):
    manifest={'format':'porota-ppi-public-probe-1','files':{}}
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,source in SOURCES.items():
            content=(ROOT/source).read_bytes()
            manifest['files'][name]={'source':source,'sha256':hashlib.sha256(content).hexdigest()}
            entry=zipfile.ZipInfo(name,date_time=(2026,8,28,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(entry,content)
        archive.writestr(zipfile.ZipInfo('MANIFEST.json',date_time=(2026,8,28,0,0,0)),
                         json.dumps(manifest,sort_keys=True,indent=2))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    print(json.dumps(build(parser.parse_args().destination),indent=2))

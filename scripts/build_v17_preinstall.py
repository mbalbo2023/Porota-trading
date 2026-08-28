"""Empaqueta exclusivamente el chequeo de metadatos; sin instalador ni motor."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

SOURCE = Path(__file__).with_name('v17_preinstall_readonly.py')


def build(destination):
    content = SOURCE.read_bytes()
    manifest = {'format':'porota-v17-preinstall-1',
                'sha256':hashlib.sha256(content).hexdigest(),
                'source':'scripts/v17_preinstall_readonly.py'}
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in (('__main__.py',content),
                            ('MANIFEST.json',json.dumps(manifest,sort_keys=True).encode())):
            entry = zipfile.ZipInfo(name, date_time=(2026,8,28,0,0,0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, value)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print(json.dumps(build(parser.parse_args().destination), indent=2))

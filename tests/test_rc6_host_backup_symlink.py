import json
import tarfile
from pathlib import Path

from scripts.v17_host_general_backup import create_backup


def test_general_backup_skips_symlink_but_copies_regular_target(tmp_path):
    source = tmp_path / 'porota'
    data = source / 'data' / 'diagnosticos'
    data.mkdir(parents=True)
    target = data / 'snapshot-20260906.json'
    target.write_text('{"state":"OK"}\n', encoding='utf-8')
    latest = data / 'latest.json'
    latest.symlink_to(target.name)
    (source / 'sre_vector_db').mkdir()
    (source / '_version.py').write_text('MODE="PRODUCTION_PAPER"\n', encoding='utf-8')
    destination = tmp_path / 'backups'

    result = create_backup(source, destination)
    assert result['status'] == 'OK'
    assert result['secrets_included'] is False

    with tarfile.open(result['archive'], 'r:gz') as archive:
        names = set(archive.getnames())
        assert 'porota-trading/data/diagnosticos/snapshot-20260906.json' in names
        assert 'porota-trading/data/diagnosticos/latest.json' not in names
        manifest = json.load(archive.extractfile('porota-trading/BACKUP_MANIFEST.json'))
    assert 'data/diagnosticos/snapshot-20260906.json' in manifest['files']
    assert 'data/diagnosticos/latest.json' not in manifest['files']

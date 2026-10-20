"""The failed COLD2 image may be removed only when every Docker owner is absent."""
import json
from types import SimpleNamespace

import pytest

from scripts import rc6_offline_failed_image_cleanup as cleanup


@pytest.mark.parametrize('change', ['none', 'referenced', 'foreign_tag', 'different_id'])
def test_exact_failed_image_eligibility(monkeypatch, change):
    tags = list(cleanup.TAGS)
    if change == 'foreign_tag': tags.append('third-party:keep')
    identity = 'sha256:'+'f'*64 if change == 'different_id' else cleanup.IMAGE
    monkeypatch.setattr(cleanup, 'docker', lambda *args: json.dumps([{
        'Id': identity, 'RepoTags': tags, 'RepoDigests': []}]))
    monkeypatch.setattr(cleanup, 'references', lambda: {cleanup.IMAGE}
                        if change == 'referenced' else set())
    if change == 'none':
        assert cleanup.eligible_tags() == tags
    else:
        with pytest.raises(ValueError, match='OFFLINE_IMAGE_REFERENCED_OR_ALIASED'):
            cleanup.eligible_tags()


def test_offline_cleanup_rechecks_admission_before_each_tag(tmp_path, monkeypatch):
    root = tmp_path/'host'
    lock = root/'data/deploy/.deploy-quiescence.lock'
    lock.parent.mkdir(parents=True)
    lock.touch()
    monkeypatch.setattr(cleanup, 'ROOT', root)
    monkeypatch.setattr(cleanup, 'admission', lambda: {'verified': True})
    monkeypatch.setattr(cleanup, 'ppi_units', lambda: {})
    remaining = list(cleanup.TAGS)
    monkeypatch.setattr(cleanup, 'eligible_tags', lambda: remaining.copy())
    monkeypatch.setattr(cleanup, 'references', lambda: set())
    removed = []

    def docker(*args):
        assert args[:2] == ('image', 'rm')
        remaining.remove(args[2]); removed.append(args[2])
        return ''

    monkeypatch.setattr(cleanup, 'docker', docker)
    monkeypatch.setattr(cleanup.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=4000+len(removed)))
    report = cleanup.cleanup()
    assert report['removed_tags'] == list(cleanup.TAGS)
    assert report['disk_after'] > report['disk_before']


def test_offline_cleanup_preserves_image_when_admission_changes(tmp_path, monkeypatch):
    root = tmp_path/'host'
    lock = root/'data/deploy/.deploy-quiescence.lock'
    lock.parent.mkdir(parents=True)
    lock.touch()
    monkeypatch.setattr(cleanup, 'ROOT', root)
    calls = iter([{'verified': True}, {'verified': False}])
    monkeypatch.setattr(cleanup, 'admission', lambda: next(calls))
    monkeypatch.setattr(cleanup, 'ppi_units', lambda: {})
    monkeypatch.setattr(cleanup, 'eligible_tags', lambda: list(cleanup.TAGS))
    monkeypatch.setattr(cleanup, 'references', lambda: set())
    monkeypatch.setattr(cleanup.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=4000))
    monkeypatch.setattr(cleanup, 'docker', lambda *args: pytest.fail('no image deletion'))
    with pytest.raises(ValueError, match='OFFLINE_ADMISSION_CHANGED'):
        cleanup.cleanup()

"""Admission RED remains recoverable without authorizing any producer."""
import json
from pathlib import Path

import pytest

from scripts import rc6_development_checks as checks


def reject(output, message='EXACT_ADMINISTRATIVE_SUCCESSOR_SESSION_REQUIRED'):
    return checks.preserve_admission_rejection(output, ValueError(message),
        source_sha='a'*40,source_tree='b'*40,plan_sha256='c'*64)


def test_original_admission_failure_is_preserved_before_tooling_or_fixtures(tmp_path):
    output=tmp_path/'controls'
    row=reject(output)
    assert json.loads((output/'admission-rejected.json').read_bytes())==row
    assert row['error_signature']=='EXACT_ADMINISTRATIVE_SUCCESSOR_SESSION_REQUIRED'
    assert row['status']=='RED_ADMISSION'
    assert all(row[key] is False for key in ('G0_G8_qualification','Product157_qualified',
        'workload_started','fixture_created','launch_authorized','cleanup_authorized'))
    assert output.stat().st_mode & 0o777==0o700


def test_red_control_cannot_overwrite_its_original_evidence(tmp_path):
    output=tmp_path/'controls';reject(output)
    original=(output/'admission-rejected.json').read_bytes()
    with pytest.raises(FileExistsError):reject(output,'DIFFERENT_REJECTION')
    assert (output/'admission-rejected.json').read_bytes()==original


@pytest.mark.parametrize('kind',['parent_alias','leaf_alias','source','ancestor','world_writable'])
def test_rejection_does_not_write_to_source_aliases_or_unowned_control(tmp_path,kind):
    real=tmp_path/'real';real.mkdir()
    if kind=='parent_alias':
        parent=tmp_path/'alias';parent.symlink_to(real,target_is_directory=True);output=parent/'control'
    elif kind=='leaf_alias':
        output=tmp_path/'alias';output.symlink_to(real,target_is_directory=True)
    elif kind=='source':output=checks.ROOT/'admission-rejected'
    elif kind=='ancestor':output=checks.ROOT.parent
    else:output=tmp_path/'bad';output.mkdir(mode=0o777);output.chmod(0o777)
    with pytest.raises((ValueError,OSError)):reject(output)
    assert not (real/'admission-rejected.json').exists()


def test_unstructured_error_text_is_not_copied_into_evidence(tmp_path):
    row=reject(tmp_path/'controls','sensitive-example-value')
    assert row['error_signature']=='ValueError'
    assert 'sensitive-example-value' not in (tmp_path/'controls'/'admission-rejected.json').read_text()

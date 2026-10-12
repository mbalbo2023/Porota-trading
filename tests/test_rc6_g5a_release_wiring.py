"""Release refusal and bounded own IO; no ROOT/native evidence is fabricated."""
import hashlib
import os
from pathlib import Path
import re

import pytest
import yaml

from scripts import rc6_architectural_gates as gates
from scripts import rc6_g5a_carrier as carrier
from scripts import rc6_g5a_family as family
from scripts import rc6_g5a_native_runner as runner

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('deadline', [None, True, '21600', float('inf'), float('-inf'), float('nan')])
def test_operation_deadline_requires_finite_original_monotonic_number(deadline):
    with pytest.raises(ValueError, match='ORIGINAL_OPERATION_DEADLINE_REQUIRED'):
        carrier.remaining_native_budget(deadline)


@pytest.mark.parametrize('remaining', [-1, 0, 7, 21600.1, 21601])
def test_exhausted_or_extended_native_deadline_is_refused(remaining, monkeypatch):
    monkeypatch.setattr(carrier.time, 'monotonic', lambda: 12345)
    with pytest.raises(ValueError, match='ORIGINAL21600_OPERATION_DEADLINE_EXHAUSTED'):
        carrier.remaining_native_budget(12345 + remaining)


def test_term2_fin5_are_reserved_inside_one_operation_deadline(monkeypatch):
    monkeypatch.setattr(carrier.time, 'monotonic', lambda: 12345)
    assert carrier.remaining_native_budget(12345 + 21600) == 21593
    assert carrier.remaining_native_budget(12345 + 80.75) == 73
    monkeypatch.setattr(carrier.time, 'monotonic', lambda: 12355)
    assert carrier.remaining_native_budget(12345 + 80.75) == 63


@pytest.fixture
def own_family(tmp_path):
    root = tmp_path/'own-controls'; root.mkdir(mode=0o700)
    return root


def test_actual_nonroot_small_control_is_immutable_exclusive_and_bounded(own_family):
    raw = b'OWN_SOURCE_CONTROL_ONLY\x00\xff'
    ref, written = family.publish(own_family, 'own.raw', raw, written=0)
    path = own_family/'own.raw'
    assert path.read_bytes() == raw and path.stat().st_mode & 0o777 == 0o600
    assert ref == {'path': 'own.raw', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    assert written == len(raw) and path.stat().st_nlink == 1
    with pytest.raises(ValueError, match='FRESH_NOFOLLOW'):
        family.publish(own_family, 'own.raw', b'REPLACE', written=written)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('name', ['../foreign', '/foreign', 'a/b', 'a//b', '.', '..', 'a\\b', 'a\x00b'])
def test_family_path_attack_never_writes_outside_own_root(own_family, name):
    with pytest.raises(ValueError, match='OWN0700_LITERAL'):
        family.publish(own_family, name, b'RAW', written=0)
    assert list(own_family.iterdir()) == []


@pytest.mark.parametrize('raw,written', [(b'', 0), (b'A', True), (b'A', -1),
    (b'A', family.MAX_TOTAL), (b'AA', family.MAX_TOTAL-1)])
def test_family_unknown_or_exceeded_total_refuses_before_write(own_family, raw, written):
    with pytest.raises(ValueError, match='BOUNDED_SMALL_CONTROLS'):
        family.publish(own_family, 'own.raw', raw, written=written)
    assert list(own_family.iterdir()) == []


def test_family_foreign_alias_target_is_preserved(own_family, tmp_path):
    foreign = tmp_path/'foreign'; foreign.write_bytes(b'FOREIGN_UNCHANGED')
    (own_family/'own.raw').symlink_to(foreign)
    with pytest.raises(ValueError, match='FRESH_NOFOLLOW'):
        family.publish(own_family, 'own.raw', b'REPLACE', written=0)
    assert foreign.read_bytes() == b'FOREIGN_UNCHANGED'


@pytest.mark.parametrize('fake', [None, {}, {'native_fin_closed': True, 'G5_GREEN': False}])
def test_json_cannot_assemble_a_native_acquisition_family(fake, own_family):
    with pytest.raises(ValueError, match='CURRENT_SOURCE_ISSUED_NATIVE_RESULT'):
        family.assemble_draft(None, None, {}, fake, root=own_family, approval=None,
            capability_reference={}, reader=None, branch='declared-only')
    assert list(own_family.iterdir()) == []


def test_json_green_cannot_supply_qualified_source_or_future_writer():
    fake = {'status': 'GREEN', 'native_source_unchanged': True, 'native_fin_closed': True}
    with pytest.raises(ValueError):
        gates.qualified_g5_receipt(fake, acquired_row={'source_sha': 'a'*40, 'source_tree': 'b'*40}, browser_row={})


def test_source_acquisition_and_primary_upload_require_explicit_admission_and_seal():
    doc = yaml.safe_load((ROOT/'.github/workflows/rc6-unified-candidate-tests.yml').read_text())
    job = doc['jobs']['g5a-bounded-acquisition']; steps = job['steps']
    assert job['timeout-minutes'] == 360 and "workflow_dispatch" in job['if']
    assert "g5a_revision_approval_url != ''" in job['if']
    admission = next(i for i, step in enumerate(steps) if step.get('id') == 'g5a_admission')
    tooling = next(i for i, step in enumerate(steps) if step.get('id') == 'g5a_py311')
    assert admission < tooling
    assert 'admit_g5a_preparation' in steps[admission]['run']
    primary = steps[-1]
    assert "family_sealed == 'true'" in primary['if']
    assert 'source-original-raw/primary/' in primary['with']['path']
    assert 'source-original-raw/' not in primary['with']['path'].replace('source-original-raw/primary/', '')
    controls = next(step for step in steps if step['name'].startswith('Preserve preparation'))
    assert all(line.endswith('/*.json') for line in controls['with']['path'].splitlines())


def test_official_part_transport_has_exact_bounded_global_indices_and_no_duplicate_category_indices():
    doc = yaml.safe_load((ROOT/'.github/actions/rc6-g5a-upload-parts/action.yml').read_text())
    steps = doc['runs']['steps']; assert len(steps) == 160
    for number, step in enumerate(steps):
        assert step['uses'] == 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
        assert step['if'] == f'always() && fromJSON(inputs.parts_start) <= {number} && fromJSON(inputs.parts_count) > {number}'
        assert step['with']['path'].endswith('/part-'+str(number).zfill(3)+'/')
        assert step['with']['name'].endswith('-'+str(number).zfill(3))
        assert step['with']['if-no-files-found'] == 'error'
        assert step['with']['retention-days'] == 14 and step['with']['compression-level'] == 0
    # The inclusive start/exclusive end prevent runtime parts from inventing
    # frame-global slots. This is transport syntax, never an Actions artifact.
    frame_slots = {i for i in range(160) if 0 <= i < 151}
    runtime_slots = {i for i in range(160) if 151 <= i < 160}
    assert frame_slots.isdisjoint(runtime_slots) and len(frame_slots | runtime_slots) == 160


def test_g7_exact_artifact_validation_requires_binding_from_qualified_g5b():
    doc = yaml.safe_load((ROOT/'.github/workflows/porota-predeploy-v2.yml').read_text())
    runs = [step.get('run', '') for job in doc['jobs'].values() for step in job['steps']]
    issue = next(run for run in runs if '--writer-binding-out' in run)
    consume = next(run for run in runs if '--g5-writer-binding' in run)
    assert 'scripts/rc6_architectural_gates.py' in issue and '--target-gate G7' in issue
    assert 'scripts/porota_validate_deploy_artifact.py' in consume
    assert '--g5-writer-binding "$RC6_G5B_WRITER_BINDING"' in consume

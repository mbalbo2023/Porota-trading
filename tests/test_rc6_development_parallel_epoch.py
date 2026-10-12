"""Real own small economic workers; no material or ROOT qualification."""
import copy
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

from scripts import rc6_development_parallel_epoch as parallel
from scripts import rc6_development_checks as development


def original_xml(name, *, failure=False):
    suite=ET.Element('testsuite',name='pytest',tests='1',failures=str(int(failure)),errors='0',skipped='0',time='0.01')
    case=ET.SubElement(suite,'testcase',classname='tests.'+name,name='test_original',time='0.01')
    if failure:ET.SubElement(case,'failure',message='ORIGINAL_DISCRIMINANT').text='Original failure body\n'
    return ET.tostring(suite)


def test_partition_covers_all_original_selections_once_and_keeps_module_fixture_together():
    selected=['tests/test_a.py::test_one','tests/test_b.py','tests/test_a.py::test_two','tests/test_c.py']
    before=list(selected); a,b=parallel.partition(selected)
    assert selected==before and set(a).isdisjoint(b) and set(a+b)==set(selected)
    assert set(selected[::2]) <= set(a)


@pytest.mark.parametrize('value', [None, [], ['tests/test_a.py'], ['tests/test_a.py','tests/test_a.py'],
    ['../foreign','tests/test_b.py'], ['tests/test_a.py;touch_x','tests/test_b.py']])
def test_unknown_repeated_or_unsafe_partition_is_refused(value):
    with pytest.raises(ValueError):parallel.partition(value)


def test_actual_worker_case_nodes_and_failure_bodies_are_preserved_and_dual_ids_stable():
    raws=[original_xml('b',failure=True),original_xml('a')]
    combined=parallel.merge_junit(raws)
    assert combined==parallel.merge_junit(list(reversed(raws)))
    facts=development.junit_facts(combined)
    assert facts['cases']==2 and facts['failures']==1 and facts['errors']==facts['skipped']==0
    assert ET.fromstring(combined).find('.//failure').text=='Original failure body\n'


@pytest.mark.parametrize('change', ['duplicate','count','empty','missing_id'])
def test_worker_report_cannot_invent_case_counts_or_hide_a_missing_worker(change):
    raws=[original_xml('a'),original_xml('b')]
    if change=='duplicate':raws[1]=raws[0]
    else:
        root=ET.fromstring(raws[1])
        if change=='count':root.set('tests','2')
        elif change=='empty':root.remove(root.find('testcase'))
        else:root.find('testcase').attrib.pop('classname')
        raws[1]=ET.tostring(root)
    with pytest.raises(ValueError):parallel.merge_junit(raws)


def test_actual_two_own_workers_publish_original_xml_and_all_cases(tmp_path, monkeypatch):
    source=tmp_path/'own-source';source.mkdir(mode=0o755);(source/'tests').mkdir(mode=0o755)
    for name in ('a','b'):(source/'tests'/('test_'+name+'.py')).write_text('def test_original():\n    assert True\n')
    namespace=tmp_path/'own-epoch';namespace.mkdir(mode=0o700)
    monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1')
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE','1')
    assert parallel.execute(source,namespace,['tests/test_a.py','tests/test_b.py'])==0
    assert development.junit_facts((namespace/'junit.xml').read_bytes())['cases']==2
    for index in (0,1):
        assert development.junit_facts((namespace/('worker-'+str(index))/'junit.xml').read_bytes())['cases']==1
        assert (namespace/('worker-'+str(index))/'native.log').is_file()


def test_epoch_parallelism_has_no_fresh_worker_deadline_or_new_process_group():
    source=Path(parallel.__file__).read_text()
    assert 'timeout=' not in source and 'start_new_session=' not in source and 'setsid' not in source
    controller=Path(development.__file__).read_text()
    assert "timeout_seconds=180" in controller and "'-x','--tb=short'" in controller
    assert "for worker in ('worker-0','worker-1')" in controller

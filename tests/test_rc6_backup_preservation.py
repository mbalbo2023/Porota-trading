"""Operator624 forbids deleting backups during canonical deployment cleanup."""
import copy,json
from pathlib import Path
import pytest
from scripts import rc6_disk_housekeeping as housekeeping

def fixture(tmp_path,monkeypatch):
    policy=json.loads((Path(__file__).resolve().parents[1]/'ops/policy/rc6-disk-housekeeping-v1.json').read_text())
    for name in ('paper','history','general'):
        root=tmp_path/name;root.mkdir()
        for n in range(6):(root/f'backup-{n}.gz').write_bytes(b'synthetic')
        policy['backups'][name].update(root=str(root),glob='backup-*.gz',keep_newest=3,pinned_names=[])
    policy['backups']['protected_paths']=[]
    monkeypatch.setattr(housekeeping,'disk_free_bytes',lambda _:5*1024**3)
    monkeypatch.setattr(housekeeping,'inode_free_percent',lambda _:90)
    monkeypatch.setattr(housekeeping,'runtime_snapshot',lambda:{})
    monkeypatch.setattr(housekeeping,'validate_runtime',lambda *_:None)
    monkeypatch.setattr(housekeeping,'compare_runtime',lambda *_:None)
    monkeypatch.setattr(housekeeping,'tmp_candidates',lambda _:[])
    monkeypatch.setattr(housekeeping,'_candidate_open_references',lambda _:[])
    monkeypatch.setattr(housekeeping,'_clean_apt',lambda _:[])
    monkeypatch.setattr(housekeeping,'_vacuum_journal',lambda _:'fixture')
    monkeypatch.setattr(housekeeping,'_docker_observation',lambda:'fixture')
    return policy

def test_legacy_retention_would_delete_nine_fixture_backups(tmp_path,monkeypatch):
    p=fixture(tmp_path,monkeypatch)
    p['backups']['suspended']=False
    result,code=housekeeping.execute('cleanup',p)
    assert code==0 and len(result['deleted'])==9
    assert sum(len(list((tmp_path/name).iterdir())) for name in ('paper','history','general'))==9

def test_suspension_preserves_all_backups_even_without_invocation_flag(tmp_path,monkeypatch):
    p=fixture(tmp_path,monkeypatch)
    result,code=housekeeping.execute('cleanup',p)
    assert code==0 and result['preserve_backups'] is True
    assert result['deleted']==[]
    assert sum(len(list((tmp_path/name).iterdir())) for name in ('paper','history','general'))==18

def test_operator_preserves_all_backups_without_changing_policy_or_reserves(tmp_path,monkeypatch):
    p=fixture(tmp_path,monkeypatch);original=copy.deepcopy(p)
    result,code=housekeeping.execute('cleanup',p,preserve_backups=True)
    assert code==0 and result['preserve_backups'] is True
    assert result['deleted']==[] and p==original
    assert all(len(group['kept'])==6 and group['delete']==[] and len(group['operator_preserved'])==3
               for group in result['candidates']['backups'].values())
    assert sum(len(list((tmp_path/name).iterdir())) for name in ('paper','history','general'))==18
    assert p['disk']['post_cleanup_min_free_bytes']==2*1024**3

def test_preservation_does_not_turn_insufficient_disk_green(tmp_path,monkeypatch):
    p=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(housekeeping,'disk_free_bytes',lambda _:2*1024**3-1)
    result,code=housekeeping.execute('cleanup',p,preserve_backups=True)
    assert code==43 and result['status']=='RED' and result['deleted']==[]
    assert result['reason']=='POST_CLEANUP_FREE_BELOW_MINIMUM'

def test_preservation_flag_reaches_pretransfer_candidate_helper_and_final_cleanup():
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert 'HOUSEKEEPING_B64="$(base64 -w0 scripts/rc6_disk_housekeeping.py)"' in workflow
    pre=workflow[workflow.index('RC6_DISK_PRECLEAN=START'):workflow.index('RC6_DISK_PRETRANSFER_GATE=GREEN')]
    final=workflow[workflow.index('# Mandatory final cleanup after runtime validation/provenance.'):]
    assert 'base64 -d | sudo -n python3 -' in pre and '--preserve-backups' in pre
    assert '--preserve-backups' in final

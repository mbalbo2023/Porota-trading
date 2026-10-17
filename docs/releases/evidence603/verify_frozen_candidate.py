"""Independent byte verification of a completed exact Predeploy artifact.

Read-only except extraction into scratch. URL is supplied in a separate
private input file and is never printed. Does not activate product code.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def verify(meta):
    base=Path(meta['evidence_root']);base.mkdir(exist_ok=True)
    archive=base/(meta['prefix']+'.zip')
    if not archive.exists():
        with urllib.request.urlopen(meta['download_url'],timeout=90) as source,archive.open('wb') as target:
            shutil.copyfileobj(source,target,1024*1024)
    digest=sha(archive)
    assert digest==meta['zip_sha256'] and archive.stat().st_size==meta['zip_bytes']
    dest=base/meta['prefix'];dest.mkdir(exist_ok=True)
    names=['porota-frozen-candidate.json','porota-governed-tests.json',
           'porota-deploy-bundle-v2-manifest.json','porota-deploy-bundle-v2.tgz',
           'porota-predeploy-image.tar.gz','porota-host-manifest-v2.json',
           'porota-host-policy-v2.json','porota-governed-tests.xml']
    with zipfile.ZipFile(archive) as z:
        assert len(z.namelist())==len(set(z.namelist()))
        for name in names:
            found=[n for n in z.namelist() if n.split('/')[-1]==name]
            assert len(found)==1,name
            with z.open(found[0]) as source,(dest/name).open('wb') as target:
                shutil.copyfileobj(source,target,1024*1024)
    frozen=json.loads((dest/names[0]).read_text())
    tests=json.loads((dest/names[1]).read_text())
    manifest=json.loads((dest/names[2]).read_text())
    assert frozen['candidate_sha']==meta['candidate_sha']
    assert frozen['candidate_tree_sha']==meta['candidate_tree']
    assert frozen['build_once'] is True
    assert frozen['paper_mode_required']=='PRODUCTION_PAPER'
    assert type(frozen['real_orders_sent_required']) is int and frozen['real_orders_sent_required']==0
    assert frozen['real_order_capability_required']=='BLOCKED'
    assert tests['status']=='GREEN' and tests['discovered']==tests['executed']==meta['expected_cases']
    assert all(tests[k]==0 for k in ('failures','errors','skipped','xfail','pytest_exit_code'))
    root=ET.parse(dest/names[7]).getroot()
    assert len(list(root.iter('testcase')))==tests['executed']
    assert all(not list(root.iter(tag)) for tag in ('failure','error','skipped'))
    properties=[p.attrib for p in root.iter('property') if p.get('name')=='RC6_PRECLEAN_PRIVILEGE_BOUNDARY']
    assert properties and all(p.get('value')=='OS_DIFFERENT_UID' for p in properties),properties
    for needle in ('failed_stress_container_removal','same_identity_but_exited_observer',
                   'unchanged_heartbeat','restored_process_restart'):
        assert any(needle in p.get('name','') for p in root.iter('testcase')),needle
    assert sha(dest/names[3])==manifest['bundle_sha256']
    assert sha(dest/names[4])==frozen['image_tar_sha256']
    source_root=Path(meta['checkout'])
    with tarfile.open(dest/names[3],'r:gz') as tar:
        members=tar.getmembers();files={m.name:m for m in members}
        assert len(files)==len(members)
        expected={row['path'] for row in manifest['files']}
        assert files.keys()==expected|{'POROTA_DEPLOY_BUNDLE_MANIFEST.json'}
        embedded=files['POROTA_DEPLOY_BUNDLE_MANIFEST.json']
        assert embedded.isfile() and not embedded.issym() and not embedded.islnk()
        assert json.loads(tar.extractfile(embedded).read())=={k:v for k,v in manifest.items() if k!='bundle_sha256'}
        for row in manifest['files']:
            member=files[row['path']]
            assert member.isfile() and not member.issym() and not member.islnk()
            assert member.size==row['bytes']
            assert hashlib.sha256(tar.extractfile(member).read()).hexdigest()==row['sha256'],row['path']
            assert sha(source_root/row['path'])==row['sha256'],row['path']
    config=None;config_sha=frozen['image_id'].split(':')[1]
    with tarfile.open(dest/names[4],'r|gz') as tar:
        for member in tar:
            if member.name.split('/')[-1] in (config_sha,config_sha+'.json'):
                raw=tar.extractfile(member).read()
                assert hashlib.sha256(raw).hexdigest()==config_sha
                config=json.loads(raw);break
    assert config is not None
    env=dict(value.split('=',1) for value in config['config']['Env'])
    assert env.get('POROTA_BUILD_SHA')==frozen['candidate_sha']
    assert env.get('POROTA_CANDIDATE_TREE_SHA')==frozen['candidate_tree_sha']
    proof={'schema':'rc6.frozen-artifact-independent-verification.v2','status':'GREEN',
        'artifact_id':meta['artifact_id'],'predeploy_run':meta['predeploy_run'],
        'zip_bytes':archive.stat().st_size,'zip_sha256':digest,
        'candidate_sha':frozen['candidate_sha'],'candidate_tree_sha':frozen['candidate_tree_sha'],
        'image_id':frozen['image_id'],'image_size_bytes':frozen['image_size_bytes'],
        'image_tar_sha256':frozen['image_tar_sha256'],'bundle_sha256':manifest['bundle_sha256'],
        'bundle_hashes_verified':len(manifest['files']),'all_bundle_sources_equal_checkout':True,
        'image_config_blob_sha256_verified':config_sha,
        'source_environment_observed':{k:env[k] for k in ('POROTA_BUILD_SHA','POROTA_CANDIDATE_TREE_SHA')},
        'tests':tests,'native_privilege_properties':properties,
        'not_a_droplet_acceptance_or_stress_result':True}
    (base/(meta['prefix']+'-verification.json')).write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('metadata')
    args=parser.parse_args();verify(json.loads(Path(args.metadata).read_text()))

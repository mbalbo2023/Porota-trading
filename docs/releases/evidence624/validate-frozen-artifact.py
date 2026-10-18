"""Independent byte inspection of the frozen ZIP; never loads/rebuilds an image."""
import argparse,gzip,hashlib,json,shutil,tarfile,xml.etree.ElementTree as ET,zipfile
from pathlib import Path,PurePosixPath

def sha_stream(stream):
    h=hashlib.sha256()
    while block:=stream.read(1048576):h.update(block)
    return h.hexdigest()
def sha(path):
    with Path(path).open('rb') as stream:return sha_stream(stream)
def junit(raw):
    root=ET.fromstring(raw);cases=list(root.iter('testcase'))
    return {'cases':len(cases),**{kind:sum(len(list(c.iter(kind))) for c in cases) for kind in ('failure','error','skipped')},
            'xml_sha256':hashlib.sha256(raw).hexdigest()}
def main():
    p=argparse.ArgumentParser();p.add_argument('--zip',type=Path,required=True);p.add_argument('--digest',required=True)
    p.add_argument('--sha',required=True);p.add_argument('--tree',required=True);p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    digest=sha(args.zip);assert digest==args.digest.removeprefix('sha256:'),'ZIP_DIGEST_MISMATCH'
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    receipt={'schema':'rc6.frozen624.independent-byte-validation.v1','candidate_sha':args.sha,'candidate_tree':args.tree,
             'zip_sha256':digest,'zip_bytes':args.zip.stat().st_size,'droplet_touched':False}
    with zipfile.ZipFile(args.zip) as archive:
        members=archive.infolist();assert len(members)<1000
        for m in members:
            path=PurePosixPath(m.filename);assert not path.is_absolute() and '..' not in path.parts
        def member(name):
            found=[m for m in members if PurePosixPath(m.filename).name==name and not m.is_dir()]
            assert len(found)==1,('MEMBER_CARDINALITY',name,len(found));return found[0]
        def read(name,limit=8*1024**2):
            m=member(name);assert m.file_size<=limit,('MEMBER_LIMIT',name,m.file_size)
            return archive.read(m)
        def data(name):return json.loads(read(name))
        frozen=data('porota-frozen-candidate.json');manifest=data('porota-deploy-bundle-v2-manifest.json')
        assert frozen['candidate_sha']==args.sha and frozen['candidate_tree_sha']==args.tree
        assert frozen['paper_mode_required']=='PRODUCTION_PAPER' and type(frozen['real_orders_sent_required']) is int and frozen['real_orders_sent_required']==0
        assert frozen['build_once'] is True and frozen['real_order_capability_required']=='BLOCKED'
        receipt['frozen']=frozen
        for name in ('porota-predeploy-image.tar.gz','porota-deploy-bundle-v2.tgz'):
            with archive.open(member(name)) as source,(out/name).open('wb') as target:shutil.copyfileobj(source,target,1048576)
        assert sha(out/'porota-predeploy-image.tar.gz')==frozen['image_tar_sha256']
        assert sha(out/'porota-deploy-bundle-v2.tgz')==manifest['bundle_sha256']
        governed=data('porota-governed-tests.json');root_xml=junit(read('porota-governed-tests.xml'))
        assert governed['discovered']==governed['executed']==root_xml['cases']>0
        assert all(governed[k]==0 for k in ('failures','errors','skipped','xfail'))
        assert all(root_xml[k]==0 for k in ('failure','error','skipped'))
        assert len(governed['exclusions'])==1
        receipt['governed_tests']=governed;receipt['root_junit']=root_xml
        receipt['native_bound']=junit(read('native-bound.xml'))
        assert receipt['native_bound']['cases']>0 and all(receipt['native_bound'][k]==0 for k in ('failure','error','skipped'))
        catalog=data('full-catalog.json');assert catalog['catalog_count']==14611 and catalog['ready_fixture_count']==9902
        assert len(catalog['cuts'])==2 and catalog['cuts'][1]['sequence']>catalog['cuts'][0]['sequence']
        assert catalog['process_peak_rss_kib']<=512*1024
        assert catalog['real_orders_sent']==catalog['provider_requests']==0
        receipt['native_catalog']=catalog
        stress=data('stress.json');assert stress['status']=='GREEN' and stress['smoke'] is False
        assert stress['session']['simulated_minutes']==405
        assert stress['session']['real_orders_sent']==stress['session']['provider_requests']==0
        expected={'sql_shadow_wal','publication_io_identity','signals_risk_exit_ledger','sources_readiness_network',
                  'scalping_capacity','treasury_caucion','dashboard_auth_52','scheduler_monitor_backup'}
        assert {row['name'] for row in stress['scenarios']}==expected and len(stress['scenarios'])==8
        groups=[]
        for row in stress['scenarios']:
            measured=junit(read(row['name']+'.xml'))
            assert measured['cases']==row['cases']>0 and measured['xml_sha256']==row['junit_sha256']
            assert row['exit_code']==0 and all(row[k]==measured[k]==0 for k in ('failure','error','skipped'))
            groups.append({'name':row['name'],**measured})
        receipt['exact_image_session']=stress;receipt['stress_groups']=groups;receipt['stress_cases']=sum(g['cases'] for g in groups)
        receipt['artifact_members']=[{'name':m.filename,'bytes':m.file_size} for m in members]
        for name in ('porota-frozen-candidate.json','porota-deploy-bundle-v2-manifest.json','porota-governed-tests.json',
                     'porota-governed-tests.xml','stress.json','full-catalog.json','native-bound.xml'):
            (out/name).write_bytes(read(name))
    with tarfile.open(out/'porota-deploy-bundle-v2.tgz','r:gz') as bundle:
        names={m.name for m in bundle.getmembers()};paths={r['path'] for r in manifest['files']}
        assert names==paths|{'POROTA_DEPLOY_BUNDLE_MANIFEST.json'} and manifest['file_count']==len(paths)==len(manifest['files'])
        embedded=json.load(bundle.extractfile('POROTA_DEPLOY_BUNDLE_MANIFEST.json'))
        assert embedded=={k:v for k,v in manifest.items() if k!='bundle_sha256'}
        for row in manifest['files']:
            item=bundle.getmember(row['path']);assert item.isfile() and item.size==row['bytes']
            assert sha_stream(bundle.extractfile(item))==row['sha256']
            assert sha(args.repo/row['path'])==row['sha256'],('CHECKOUT_MEMBER_MISMATCH',row['path'])
    receipt['bundle_file_count']=manifest['file_count'];receipt['bundle_sha256']=manifest['bundle_sha256'];receipt['all_bundle_members_verified']=True
    with tarfile.open(out/'porota-predeploy-image.tar.gz','r:gz') as image:
        manifest_image=json.load(image.extractfile('manifest.json'));assert len(manifest_image)==1
        entry=manifest_image[0];config_raw=image.extractfile(entry['Config']).read();config=json.loads(config_raw)
        assert 'sha256:'+hashlib.sha256(config_raw).hexdigest()==frozen['image_id'],'OCI_CONFIG_ID_MISMATCH'
        environ=dict(v.split('=',1) for v in config['config']['Env'] if '=' in v)
        assert environ['POROTA_BUILD_SHA']==args.sha and environ['POROTA_CANDIDATE_TREE_SHA']==args.tree
        layers=entry['Layers'];diff_ids=config['rootfs']['diff_ids'];assert len(layers)==len(diff_ids)
        layer_receipts=[]
        for name,expected in zip(layers,diff_ids):
            stream=image.extractfile(name);magic=stream.read(2);stream.seek(0)
            measured=sha_stream(gzip.GzipFile(fileobj=stream) if magic==b'\x1f\x8b' else stream)
            assert 'sha256:'+measured==expected,('OCI_LAYER_DIGEST_MISMATCH',name)
            layer_receipts.append({'name':name,'diff_id':expected})
        receipt['oci']={'image_id':frozen['image_id'],'config_sha256':hashlib.sha256(config_raw).hexdigest(),
                        'source_sha_observed':environ['POROTA_BUILD_SHA'],'source_tree_observed':environ['POROTA_CANDIDATE_TREE_SHA'],
                        'architecture':config['architecture'],'os':config['os'],'layers':layer_receipts}
    receipt['status']='GREEN';(out/'byte-validation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'status':'GREEN','candidate_sha':args.sha,'zip_sha256':digest,'root_cases':root_xml['cases'],
                      'stress_cases':receipt['stress_cases'],'bundle_files':manifest['file_count'],'image_id':frozen['image_id']}))
if __name__=='__main__':main()

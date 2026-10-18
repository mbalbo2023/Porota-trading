"""Read-only verification of engine-normalized content from the frozen ZIP."""
import argparse,gzip,hashlib,json,os,subprocess,tarfile,zipfile
from pathlib import Path

SEMANTIC_KEYS=('Env','Entrypoint','Cmd','User','WorkingDir','StopSignal','Healthcheck','ExposedPorts','Volumes','Labels','OnBuild','Shell')
def semantic_config(config):
    result={}
    for key in SEMANTIC_KEYS:
        value=config.get(key)
        if key in {'Env','Entrypoint','Cmd','OnBuild','Shell'}:value=value or []
        elif key in {'Healthcheck','ExposedPorts','Volumes','Labels'}:value=value or {}
        else:value=value or ''
        result[key]=value
    raw=json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1048576):h.update(chunk)
    return h.hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--profile',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.output.parent;archive=root/'frozen.zip'
    url='https://api.github.com/repos/mbalbo2023/Porota-trading/actions/artifacts/12112809598/zip'
    args=['curl','--fail','--silent','--show-error','--location','-H','Authorization: Bearer '+os.environ['GH_TOKEN'],'-H','Accept: application/vnd.github+json',url,'-o',str(archive)]
    fetched=subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=120)
    if fetched.returncode:raise ValueError('FROZEN_ARCHIVE_DOWNLOAD_FAILED')
    assert sha(archive)=='7a4161313698fc6f16b18a5eef22020fbe973ee4b50ae9510df775152395b4a9'
    with zipfile.ZipFile(archive) as z:
        def member(name):
            matches=[n for n in z.namelist() if Path(n).name==name];assert len(matches)==1;return matches[0]
        frozen=json.loads(z.read(member('porota-frozen-candidate.json')))
        target=root/'frozen-image.tar.gz'
        with z.open(member('porota-predeploy-image.tar.gz')) as source,target.open('wb') as output:
            while chunk:=source.read(1048576):output.write(chunk)
    assert sha(target)==frozen['image_tar_sha256']
    with tarfile.open(target,'r:gz') as tar:
        manifest=json.load(tar.extractfile('manifest.json'));assert len(manifest)==1
        raw=tar.extractfile(manifest[0]['Config']).read();config=json.loads(raw)
    assert 'sha256:'+hashlib.sha256(raw).hexdigest()==frozen['image_id']
    profile=json.loads(a.profile.read_text());loaded=profile['loaded_image']
    expected=semantic_config(config['config'])
    assert loaded['semantic_config_sha256']==expected,'LOADED_CONFIG_SEMANTICS_MISMATCH'
    assert loaded['rootfs_diff_ids']==config['rootfs']['diff_ids'],'LOADED_ROOTFS_MISMATCH'
    assert loaded['os']==config['os'] and loaded['architecture']==config['architecture']
    assert profile['source_sha']==frozen['candidate_sha'] and profile['source_tree']==frozen['candidate_tree_sha']
    result={'schema':'rc6.loaded-image-content-identity.v1','status':'GREEN_CONTENT_IDENTITY','candidate_sha':frozen['candidate_sha'],'candidate_tree':frozen['candidate_tree_sha'],'original_image_id':frozen['image_id'],'loaded_image_id':loaded['id'],'zip_sha256':sha(archive),'image_tar_sha256':frozen['image_tar_sha256'],'rootfs_layers_verified':len(config['rootfs']['diff_ids']),'rootfs_diff_ids_equal':True,'semantic_config_sha256':expected,'semantic_config_equal':True,'normalization_fields':list(SEMANTIC_KEYS),'read_only':True}
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    archive.unlink();target.unlink() # Only this GitHub-runner scratch copy.
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()

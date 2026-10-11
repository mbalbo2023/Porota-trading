"""Pure captured bootstrap formats; no native quota/build/qualification runs."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from scripts import rc6_architectural_gates as gates
from scripts import rc6_capacity_calibration as calibration
from tests.test_rc6_private_pid_capture_replay import kernel


@pytest.fixture
def captured_bootstrap(tmp_path, monkeypatch):
    namespace=tmp_path/'declared-format';project=namespace/'mount/project';project.mkdir(parents=True)
    (project/'controls').mkdir()
    commits=[b'tree '+(f'{n:040x}').encode()+b'\n\nDECLARED_NONNATIVE_FORMAT_'+str(n).encode()+b'\n' for n in range(19)]
    objects=[{'sha':hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
        'github_tree':f'{n:040x}','raw_commit_sha256':calibration.digest(raw),
        'local_ref':'refs/declared-format/'+str(n)} for n,raw in enumerate(commits)]
    objects_raw=calibration.wire({'objects':objects});original_read=calibration.read
    monkeypatch.setattr(calibration,'OBJECTS_SHA256',calibration.digest(objects_raw))
    def read(path,maximum=calibration.CONTROLS):
        if Path(path)==calibration.ROOT/calibration.OBJECTS_MEMBER:return objects_raw
        return original_read(path,maximum)
    monkeypatch.setattr(calibration,'read',read)
    monkeypatch.setattr(calibration,'verify_python',lambda path,epoch:path)
    monkeypatch.setattr(calibration,'inventory',lambda path:{'declared_format_only':True})
    policy=calibration.decode(original_read(calibration.ROOT/'ops/policy/rc6-supply-chain-v1.json'))
    packages=sorted([[r['name'],r['version']] for r in policy['packages']+policy['build_tools']])
    request={'project':str(project),'source_root':str(calibration.ROOT),'source_sha':'a'*40,'source_tree':'b'*40,
        'pythons':{'311':'/declared/python3.11','312':'/declared/python3.12'},
        'toolchain_binaries':{'git':{'path':'/usr/bin/git'},'cc':{'path':'/usr/bin/cc'}}}
    command=[request['pythons']['311'],'-I','-B',str(calibration.ROOT/'scripts/rc6_capacity_calibration.py'),
        '--probe-child','--project',str(project),'--outside',str(namespace)]
    capkernel=kernel(8,limit=60,command=command);capkernel['peak_rss_bytes']=4096
    commands=[{'label':'capability','argv':command,'kernel':capkernel,'log':str(project/'controls/capability.log')}]
    raws={'capability.log':b'{}\n','capability.kernel.json':calibration.wire(capkernel)}
    def run(argv,label,seconds,extra=None):
        raw=b'DECLARED_NONNATIVE_FORMAT\n'
        if label.startswith('interpreter'):raw=calibration.VERSIONS[label.removeprefix('interpreter')].encode()+b'\n'
        if label.startswith('closure'):raw=calibration.wire(packages)
        if label.startswith('pipcheck'):raw=b'No broken requirements found.\n'
        if label=='seed-shallow':raw=b'false\n'
        if label.startswith('seed-object-check'):raw=commits[int(label.removeprefix('seed-object-check'))]
        if label.startswith('identity'):raw=b'a'*40+b'\n'+b'b'*40+b'\nfalse\n'
        if label.startswith('venv'):
            Path(argv[argv.index('--receipt')+1]).write_bytes(b'DECLARED_NONNATIVE_CREATION_FORMAT')
        log=project/'controls'/f'{label}.log';log.write_bytes(raw)
        k=kernel(100+len(commands),limit=seconds,command=argv);k['peak_rss_bytes']=4096
        row={'label':label,'argv':argv,'kernel':k,'log':str(log)};commands.append(row)
        raws[label+'.log']=raw;raws[label+'.kernel.json']=calibration.wire(k)
        return row
    # Only Source-fixed orchestration runs against the declared nonnative run
    # mock. No interpreter, pip, Git, native manager or privilege syscall runs.
    built=calibration.bootstrap(request,run)
    receipt={'mode':'bootstrap','source_sha':request['source_sha'],'source_tree':request['source_tree'],
        'limits':calibration.limits_for('bootstrap'),'worker':{'bootstrap':built,'commands':commands,
            'privilege':{'uid':calibration.os.geteuid()},'actual_own_fin_closed':True,'required_raw_complete':True},
        'required_raw':[{'path':name,'bytes':len(raw),'sha256':calibration.digest(raw)} for name,raw in raws.items()]}
    return receipt,{'diagnostic/'+name:raw for name,raw in raws.items()}


def test_original_bootstrap_source_orchestration_capture_replays_without_native_authority(captured_bootstrap):
    receipt,preserved=captured_bootstrap
    result=gates.verify_capacity_bootstrap_raw(receipt,preserved)
    assert result['original_objects']==19 and result['PRODUCT157_epochs']==['311','312']
    assert all(result[name] is False for name in ('material_gate_GREEN_granted',
        'new_workload_authorization_granted','qualification_claimed'))


@pytest.mark.parametrize('fault',('missing_command','reorder','argv','kernel_command','deadline','missing_raw',
    'raw_hash','closure_name','closure_version','pipcheck','git_shallow','commit19','limits','FIN','epoch'))
def test_bootstrap_cannot_accept_aggregate_flags_or_partial_original_raw(captured_bootstrap,fault):
    receipt,preserved=captured_bootstrap;receipt=copy.deepcopy(receipt);preserved=dict(preserved)
    def replace_raw(name,raw):
        preserved['diagnostic/'+name]=raw
        row=next(row for row in receipt['required_raw'] if row['path']==name)
        row.update(bytes=len(raw),sha256=calibration.digest(raw))
    if fault=='missing_command':receipt['worker']['commands'].pop(4)
    elif fault=='reorder':receipt['worker']['commands'][5:7]=list(reversed(receipt['worker']['commands'][5:7]))
    elif fault=='argv':
        row=next(r for r in receipt['worker']['commands'] if r['label']=='locked311-1')
        row['argv'].remove('--require-hashes');row['kernel']['command']=row['argv']
        replace_raw(row['label']+'.kernel.json',calibration.wire(row['kernel']))
    elif fault in ('kernel_command','deadline'):
        row=receipt['worker']['commands'][2]
        row['kernel']['command']=['/declared/other'] if fault=='kernel_command' else row['argv']
        if fault=='deadline':row['kernel']['launcher_management_deadline_seconds']=61
        replace_raw(row['label']+'.kernel.json',calibration.wire(row['kernel']))
    elif fault=='missing_raw':del preserved['diagnostic/closure312.log']
    elif fault=='raw_hash':preserved['diagnostic/closure312.log']+=b' '
    elif fault.startswith('closure_'):
        packages=gates.document(preserved['diagnostic/closure311.log'])
        packages[0][0 if fault=='closure_name' else 1]='DECLARED_REBIND'
        receipt['worker']['bootstrap']['PRODUCT157']['311']['packages']=packages
        replace_raw('closure311.log',calibration.wire(packages))
    elif fault=='pipcheck':replace_raw('pipcheck311.log',b'Broken dependency\n')
    elif fault=='git_shallow':replace_raw('identity312.log',b'a'*40+b'\n'+b'b'*40+b'\ntrue\n')
    elif fault=='commit19':replace_raw('seed-object-check18.log',b'tree '+b'c'*40+b'\n\nDIFFERENT\n')
    elif fault=='limits':receipt['limits']['backing_image_bytes']+=1
    elif fault=='FIN':receipt['worker']['actual_own_fin_closed']=False
    else:del receipt['worker']['bootstrap']['PRODUCT157']['312']
    with pytest.raises(ValueError,match='BOOTSTRAP_|CALIBRATION_PRODUCT157_'):
        gates.verify_capacity_bootstrap_raw(receipt,preserved)


def test_bootstrap_verifier_authenticates_capability_before_bootstrap(tmp_path,monkeypatch):
    calls=[];cap={'artifact_id':1};boot={'artifact_id':2}
    monkeypatch.setattr(gates,'verify_capacity_capability_artifact',lambda ref,**kw:calls.append(('cap',ref)) or {'origin':{}})
    monkeypatch.setattr(gates,'_verify_capacity_diagnostic_artifact',lambda ref,**kw:
        calls.append(('boot',ref,kw['capability_reference'],kw['diagnostic_gate'])) or {'origin':{}})
    result=gates.verify_capacity_bootstrap_artifact(boot,capability_reference=cap,source_sha='a'*40,
        source_tree='b'*40,read_contract_sha256='c'*64,evidence_root=tmp_path/'read-only-artifacts')
    assert calls==[('cap',cap),('boot',boot,cap,'capacity-calibration')]
    assert 'verified_capability_prerequisite' in result

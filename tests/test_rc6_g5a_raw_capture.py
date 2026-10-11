"""Declared pure framed-byte fixtures; no ROOT, quota, image or native proof."""
import copy
import errno
import gzip
import io
import os
from pathlib import Path
import stat
import struct

import pytest

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5a_carrier as carrier
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_g5a_raw_capture as capture


def declared_request():
    return {'source_sha':'a'*40,'source_tree':'b'*40,'source_root':'/declared/source',
        'source_repo':'/declared/git','source_index_sha256':'c'*64,'owner_uid':1001,'owner_gid':1001,
        'binding':{'candidate_sha':'a'*40,'candidate_tree':'b'*40,'gate':'Horizon','run_id':7,'run_attempt':1}}


def declared_identity(kind,size,inode):
    return {'st_dev':27,'st_ino':inode,'st_uid':1001,'st_gid':1001,
        'st_mode':(stat.S_IFDIR|0o700) if kind=='DIRECTORY' else (stat.S_IFREG|0o600),
        'st_nlink':2 if kind=='DIRECTORY' else 1,'st_size':4096 if kind=='DIRECTORY' else size,
        'st_blocks':8 if kind=='DIRECTORY' else (size+511)//512,
        'st_atime_ns':1,'st_mtime_ns':2,'st_ctime_ns':3}


def declared_fin(request):
    stages=('nonnegative_stdlib_finalizers','join_owned_children','stop_owned_forkserver',
        'remaining_stdlib_finalizers','stop_owned_resource_tracker','verify_kernel_echild')
    return {'schema':'rc6.native-horizon-owned-fin.v1','pid':2,'parent_pid':1,
        'entry_module':'scripts.rc6_material_horizon_producer',
        'entry_member':'scripts/rc6_material_horizon_producer.py',
        **{key:request[key] for key in ('source_sha','source_tree','source_root','source_index_sha256')},
        'native_finalizer_closed':True,'child_infrastructure_finalization':{'status':'GREEN',
            'management_bound_seconds':5,'finalization_thread_finished':True,
            'kernel_echild_before_phase_return':True,'signal_guard_installed_and_witnessed':True,
            'forced_termination_attempted':False,'forced_termination':False,'signal_vetoed':False,
            'termination_signal_attempts':[],'errors':[],'unexpected_kernel_children':[],
            'wall_seconds':0.1,'protocol_steps':[{'stage':stage,'completed':True} for stage in stages]}}


def fixture_parts(*,extra=None,status='EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN'):
    """Build documentary bytes only; never insert a native authority registry."""
    request=declared_request();fin=declared_fin(request)
    contents={'control/producer-owned-fin.json':calibration.wire(fin),
        'control/g5a-native-physical-control.json':b'{"declared_pure_fixture":true}\n',
        'project/result.json':b'{"status":"RED","declared_pure_fixture":true}\n',
        'edquot-probe/original.bin':b'ORIGINAL\x00\xff'}
    contents.update(extra or {})
    directories={'control','project','edquot-probe'}
    for name in contents:
        for parent in Path(name).parents:
            if str(parent)!='.':directories.add(parent.as_posix())
    entries=[];headers=[];payloads=[]
    for inode,name in enumerate(sorted(directories|set(contents)),1):
        kind='DIRECTORY' if name in directories else 'FILE';raw=b'' if kind=='DIRECTORY' else contents[name]
        entry={'kind':kind,'name':name,'bytes':len(raw),'identity':declared_identity(kind,len(raw),inode)}
        entries.append(entry);headers.append({'schema':'rc6.g5a-source-raw-frame.v2',**entry,
            **({'sha256':calibration.digest(raw)} if kind=='FILE' else {})});payloads.append(raw)
    inventory={'schema':'rc6.g5a-own-source-stream-inventory.v2','entries':entries,
        'payload_bytes':sum(row['bytes'] for row in entries),
        'allocated_bytes':sum(row['identity']['st_blocks']*512 for row in entries),
        'retained_entries':len(entries),'source_sha':request['source_sha'],'source_tree':request['source_tree'],
        'binding':request['binding'],'data_substitution_performed_by_streamer':False,
        'storage_reduction_extrapolated':False,'qualification_claimed':False}
    named={'producer-owned-fin.json':'control/producer-owned-fin.json',
        'native-horizon-result.json':'project/result.json',
        'g5a-native-physical-control.json':'control/g5a-native-physical-control.json'}
    for name in ('comparison-result.json.gz','g5a-ledger.json.gz','g5a-quota.json.gz',
            'g5b-writer-binding.json.gz','g5b-original-frames-manifest.json.gz'):
        relative='control/original-cas-comparison/'+name
        if relative in contents:named[name]=relative
    references=[{'path':name,'relative_source':source,'bytes':len(contents[source]),
        'sha256':calibration.digest(contents[source])} for name,source in named.items()]
    worker={'schema':runner.WORKER_SCHEMA,'request_sha256':calibration.digest(calibration.wire(request)),
        'source_sha':request['source_sha'],'source_tree':request['source_tree'],'binding':request['binding'],
        'source_stream_inventory_sha256':calibration.digest(calibration.wire(inventory)),
        'source_stream_retained_entries':len(entries),'source_stream_payload_bytes':inventory['payload_bytes'],
        'report':{'status':status,'operation_scope':runner.OPERATION_SCOPE,'required_raw_complete':True,
            'G5_GREEN':False,'qualification_claimed':False,'external_ROOT_FIN_claimed':False,'real_orders_sent':0,
            'own_kernel_after_producer':{'kernel_echild_verified':True,'errno':errno.ECHILD},
            'native_producer_fin':fin,'raw_files':references,'raw_total_bytes':sum(r['bytes'] for r in references)}}
    return request,headers,payloads,worker,inventory


def encode(parts,*,end_changes=None,worker_change=None,raw_header=None,trailing=b''):
    request,headers,payloads,worker,inventory=copy.deepcopy(parts)
    if worker_change:worker_change(worker)
    output=io.BytesIO();output.write(runner.STREAM_MAGIC)
    def frame(header,raw=b''):
        encoded=calibration.wire(header)
        if raw_header:encoded=raw_header(header,encoded)
        output.write(struct.pack('>I',len(encoded)));output.write(encoded);output.write(raw)
    for header,payload in zip(headers,payloads):frame(header,payload)
    control=calibration.wire(worker)
    frame({'schema':'rc6.g5a-source-raw-frame.v2','kind':'WORKER_CONTROL',
        'name':'g5a-worker-report.json','bytes':len(control),'sha256':calibration.digest(control)},control)
    end={'schema':'rc6.g5a-source-raw-frame.v2','kind':'END','source_sha':request['source_sha'],
        'source_tree':request['source_tree'],'binding':request['binding'],'data_entries':len(inventory['entries']),
        'data_payload_bytes':inventory['payload_bytes'],'bytes_before_end':output.tell(),
        'whole_current_own_namespace_streamed':True,'external_ROOT_FIN_claimed':False,
        'G5_GREEN':False,'qualification_claimed':False}
    end.update(end_changes or {});frame(end);output.write(trailing)
    return output.getvalue()


def parse(raw,request=None,**kwargs):
    return capture.parse_stream(io.BytesIO(raw),request=request or declared_request(),
        expected_bytes=len(raw),expected_sha256=calibration.digest(raw),**kwargs)


def test_original_binary_source_inventory_offsets_and_controls_are_lossless_without_authority():
    parts=fixture_parts();raw=encode(parts);result=parse(raw)
    assert result['inventory']==parts[4]
    for name,row in result['files'].items():
        actual=raw[row['offset']:row['offset']+row['bytes']]
        assert calibration.digest(actual)==row['sha256']
    assert result['G5_GREEN'] is False and result['qualification_claimed'] is False
    assert capture._RETIRED=={} and capture._CAPTURES=={}


def test_a_complete_logical_red_remains_lossless_raw_and_does_not_require_full1202():
    result=parse(encode(fixture_parts(status='BLOQUEADO')))
    assert result['worker']['report']['status']=='BLOQUEADO'
    assert result['G5_GREEN'] is False and 'actual_original_cuts' not in result


@pytest.mark.parametrize('fault',('text_only','short_magic','header_truncated','zero_header','oversize_header',
    'noncanonical','duplicate_key','invalid_json','invalid_utf8','unknown_kind','file_hash','whole_hash','header_extra','missing_parent',
    'path_traversal','foreign_tree','foreign_device','owner','hardlink','symlink','mode','size','sort',
    'file_limit','negative_inode','bool_identity','missing_root','entry_limit'))
def test_source_frame_failures_are_discriminating_and_never_create_native_authority(fault,monkeypatch):
    parts=fixture_parts();headers=parts[1];file=next(row for row in headers if row['kind']=='FILE')
    raw=None;request=None;expected='G5A_CAPTURE_'
    if fault=='text_only':raw=b'{"status":"BLOQUEADO"}\n';expected='ORIGINAL_BINARY_STREAM_MAGIC'
    elif fault=='short_magic':raw=runner.STREAM_MAGIC[:4];expected='ORIGINAL_BINARY_STREAM_MAGIC'
    elif fault=='header_truncated':raw=runner.STREAM_MAGIC+b'\x00\x00';expected='HEADER_TRUNCATED'
    elif fault=='zero_header':raw=runner.STREAM_MAGIC+struct.pack('>I',0);expected='64K_HEADER_BOUND'
    elif fault=='oversize_header':raw=runner.STREAM_MAGIC+struct.pack('>I',65537);expected='64K_HEADER_BOUND'
    elif fault=='noncanonical':raw=encode(parts,raw_header=lambda h,r:b' '+r);expected='CANONICAL_SOURCE_FRAME'
    elif fault=='duplicate_key':
        raw=encode(parts,raw_header=lambda h,r:r.replace(b'{',b'{"kind":"DIRECTORY",',1));expected='DUPLICATE_JSON_KEY'
    elif fault=='invalid_json':raw=encode(parts,raw_header=lambda h,r:b'not-json');expected='CANONICAL_FRAME_JSON_REQUIRED'
    elif fault=='invalid_utf8':raw=encode(parts,raw_header=lambda h,r:b'\xff');expected='CANONICAL_FRAME_JSON_REQUIRED'
    elif fault=='unknown_kind':headers[0]['kind']='ROOT_SUCCESS';expected='KIND_OR_FRAME_ORDER'
    elif fault=='file_hash':file['sha256']='0'*64;expected='FILE_BYTES_CHANGED'
    elif fault=='whole_hash':raw=encode(parts);expected='WHOLE_SOURCE_STREAM_DIGEST'
    elif fault=='header_extra':file['accepted']=True;expected='SORTED_SOURCE_NODE_INVENTORY'
    elif fault=='missing_parent':file['name']='control/absent/original';expected='PARENT_DIRECTORY'
    elif fault=='path_traversal':file['name']='control/../secret';expected='ONLY_THREE_ORIGINAL'
    elif fault=='foreign_tree':headers[0]['name']='foreign';expected='ONLY_THREE_ORIGINAL'
    elif fault=='foreign_device':file['identity']['st_dev']=33;expected='FOREIGN_FILESYSTEM'
    elif fault=='owner':file['identity']['st_uid']=0;expected='NATIVE_IDENTITY'
    elif fault=='hardlink':file['identity']['st_nlink']=2;expected='SINGLELINK_REGULAR'
    elif fault=='symlink':file['identity']['st_mode']=stat.S_IFLNK|0o600;expected='SINGLELINK_REGULAR'
    elif fault=='mode':file['identity']['st_mode']=stat.S_IFREG|0o666;expected='SINGLELINK_REGULAR'
    elif fault=='size':file['identity']['st_size']+=1;expected='SINGLELINK_REGULAR'
    elif fault=='sort':headers[1]['name']=headers[0]['name'];expected='SORTED_SOURCE_NODE_INVENTORY'
    elif fault=='file_limit':file['bytes']=capture.MAX_FILE+1;expected='16MIB_FILE_BOUND'
    elif fault=='negative_inode':file['identity']['st_ino']=-1;expected='NATIVE_IDENTITY'
    elif fault=='bool_identity':file['identity']['st_nlink']=True;expected='NATIVE_IDENTITY'
    elif fault=='missing_root':
        index=next(i for i,row in enumerate(headers) if row['name']=='project')
        headers.pop(index);parts[2].pop(index)
        file=next(row for row in headers if row['name']=='project/result.json');file['name']='edquot-probe/z'
        raw=encode(parts,end_changes={'data_entries':len(headers)})
        expected='ALL_THREE_CURRENT_ORIGINAL_TREES'
    elif fault=='entry_limit':monkeypatch.setattr(runner,'MAX_STREAM_ENTRIES',1);expected='SORTED_SOURCE_NODE_INVENTORY'
    raw=raw or encode(parts)
    with pytest.raises(ValueError,match=expected):
        if fault=='whole_hash':
            capture.parse_stream(io.BytesIO(raw),request=declared_request(),expected_bytes=len(raw),expected_sha256='0'*64)
        else:parse(raw,request=request)


@pytest.mark.parametrize('field,value',(('source_sha','f'*40),('source_tree','f'*40),('binding',{}),
    ('data_entries',True),('data_payload_bytes',0),('bytes_before_end',0),
    ('whole_current_own_namespace_streamed',False),('external_ROOT_FIN_claimed',True),
    ('G5_GREEN',True),('qualification_claimed',True)))
def test_end_cannot_rebind_source_counts_or_fin_and_green(field,value):
    with pytest.raises(ValueError,match='COMPLETE_END_SOURCE_OR_COUNTS_REBOUND'):
        parse(encode(fixture_parts(),end_changes={field:value}))


@pytest.mark.parametrize('fault',('request_hash','inventory_hash','source','binding','complete_flag','green',
    'orders','echild','producer_source','finalizer','fin_bytes','reference_path','reference_hash','total'))
def test_worker_controls_cannot_substitute_original_source_or_native_fin(fault):
    def change(worker):
        report=worker['report'];fin=report['native_producer_fin'];reference=report['raw_files'][0]
        if fault=='request_hash':worker['request_sha256']='f'*64
        elif fault=='inventory_hash':worker['source_stream_inventory_sha256']='f'*64
        elif fault=='source':worker['source_sha']='f'*40
        elif fault=='binding':worker['binding']={}
        elif fault=='complete_flag':report['required_raw_complete']=False
        elif fault=='green':report['G5_GREEN']=True
        elif fault=='orders':report['real_orders_sent']=1
        elif fault=='echild':report['own_kernel_after_producer']['kernel_echild_verified']=False
        elif fault=='producer_source':fin['source_tree']='f'*40
        elif fault=='finalizer':fin['child_infrastructure_finalization']['forced_termination']=True
        elif fault=='fin_bytes':fin['unexpected_extra']='not_in_original'
        elif fault=='reference_path':reference['relative_source']='control/forged.json'
        elif fault=='reference_hash':reference['sha256']='f'*64
        else:report['raw_total_bytes']+=1
    with pytest.raises(ValueError,match='G5A_CAPTURE_SOURCE_WORKER_|G5A_CAPTURE_ACTUAL_ORIGINAL_|G5A_CAPTURE_ORIGINAL_'):
        parse(encode(fixture_parts(),worker_change=change))


@pytest.mark.parametrize('fault',('truncated_payload','truncated_end','trailing'))
def test_truncated_or_trailing_stream_is_not_retirement_evidence(fault):
    raw=encode(fixture_parts())
    if fault=='truncated_payload':raw=raw[:len(runner.STREAM_MAGIC)+400]
    elif fault=='truncated_end':raw=raw[:-1]
    else:raw+=b'TRAILING'
    with pytest.raises(ValueError,match='G5A_CAPTURE_'):
        parse(raw)


def test_first_scan_is_bounded_same_inode_and_makes_no_output_copy(tmp_path):
    request=declared_request();raw=encode(fixture_parts());path=tmp_path/'original-source.log';path.write_bytes(raw)
    path.chmod(0o600);before=capture.identity(path.lstat())
    result=capture.scan_transferred_stream(path,request=request,
        reference={'bytes':len(raw),'sha256':calibration.digest(raw)},expected_identity=before)
    assert capture.identity(path.lstat())==before and list(tmp_path.iterdir())==[path]
    assert result['complete_original_binary_stream_verified'] is True and result['future_copy_claimed'] is False
    assert result['G5_GREEN'] is False and result['qualification_claimed'] is False
    path.write_bytes(raw[:-1])
    with pytest.raises(ValueError,match='IDENTITY_BEFORE_SCAN_REBOUND'):
        capture.scan_transferred_stream(path,request=request,
            reference={'bytes':len(raw),'sha256':calibration.digest(raw)},expected_identity=before)


@pytest.mark.parametrize('value',(None,True,{}, {'actual_root_service_FIN':True},object.__new__(capture.RetiredBacking)))
def test_serialized_native_results_cannot_retire_an_image(value,monkeypatch):
    monkeypatch.setattr(os,'unlink',lambda *a,**kw:pytest.fail('NO_UNLINK_WITHOUT_NATIVE_SOURCE_REGISTRY'))
    with pytest.raises(ValueError,match='G5A_CARRIER_CURRENT_SOURCE_ISSUED_NATIVE_RESULT_REQUIRED'):
        capture.retire_own_backing(value)


@pytest.mark.parametrize('value',(None,True,{}, {'complete_original_stream_scan':True},object.__new__(capture.RetiredBacking)))
def test_documentary_scan_or_type_only_retirement_cannot_authorize_copy(value,tmp_path,monkeypatch):
    monkeypatch.setattr(os,'open',lambda *a,**kw:pytest.fail('NO_CAPTURE_IO_WITHOUT_RETIREMENT_REGISTRY'))
    with pytest.raises(ValueError,match='CURRENT_OPAQUE_NATIVE_RETIREMENT_REQUIRED'):
        capture.capture(value,destination=tmp_path/'unauthorized')


@pytest.mark.parametrize('value',(None,{}, {'payload_root':'/fake'},object.__new__(capture.RawCapture)))
def test_serialized_raw_capture_cannot_provide_original_frame_bytes(value):
    with pytest.raises(ValueError,match='CURRENT_OPAQUE_PINNED_STREAM_REQUIRED'):
        capture.validate_capture(value,require_complete_original_1202=True)


@pytest.mark.parametrize('factory',(capture.RetiredBacking,capture.RawCapture))
def test_capture_authorities_have_no_public_boolean_or_json_constructor(factory):
    with pytest.raises(ValueError,match='FACTORY_REQUIRED'):
        factory({'actual_ROOT_FIN_verified':True,'G5_GREEN':True})


def test_each_parser_read_is_bounded_without_loading_the_original_stream():
    parts=fixture_parts(extra={'project/original.bin':b'\x00\xff'*(capture.CHUNK+1)})
    raw=encode(parts)
    class BoundedReader(io.BytesIO):
        def read(self,size=-1):
            assert 0<=size<=capture.CHUNK
            return super().read(size)
    result=capture.parse_stream(BoundedReader(raw),request=parts[0],
        expected_bytes=len(raw),expected_sha256=calibration.digest(raw))
    assert result['files']['project/original.bin']['bytes']==2*capture.CHUNK+2


def test_log_identity_change_during_first_complete_scan_cannot_authorize_retirement(tmp_path,monkeypatch):
    raw=encode(fixture_parts());path=tmp_path/'original-source.log';path.write_bytes(raw);path.chmod(0o600)
    expected=capture.identity(path.lstat());original=capture.parse_stream
    def rebind(*args,**kwargs):
        result=original(*args,**kwargs)
        os.utime(path,ns=(expected['st_atime_ns'],expected['st_mtime_ns']+1))
        return result
    monkeypatch.setattr(capture,'parse_stream',rebind)
    with pytest.raises(ValueError,match='IDENTITY_DURING_SCAN_REBOUND'):
        capture.scan_transferred_stream(path,request=declared_request(),
            reference={'bytes':len(raw),'sha256':calibration.digest(raw)},expected_identity=expected)


def declared_capture_state(tmp_path,monkeypatch,*,frames_count=0):
    """Isolate actual file copy/parser only; mock retirement explicitly NONNATIVE."""
    extras={};frames=[]
    for cut in range(frames_count):
        raw=gzip.compress(calibration.wire({'declared_pure_fixture':True,'cut_index':cut}),mtime=0)
        path='cut-'+str(cut).zfill(4)+'.json.gz';extras[capture.FRAME_PREFIX+path]=raw
        frames.append({'cut_index':cut,'path':path,'bytes':len(raw),'sha256':calibration.digest(raw)})
    if frames_count:
        manifest={'schema':'rc6.g5a-all1202-original-byte-frames.v2','source_sha':'a'*40,'source_tree':'b'*40,
            'actual_original_cuts':frames_count,'frames':frames,'writer_codec_restorer_binding_sha256':'d'*64,
            'qualification_claimed':False}
        extras[capture.FRAMES_MANIFEST]=gzip.compress(calibration.wire(manifest),mtime=0)
        for role,name in capture.SOURCE_ROLE_PATHS.items():
            if role!='g5b_original_frames_manifest' and name!='project/result.json':
                extras[name]=gzip.compress(calibration.wire({'declared_pure_fixture':True,'role':role}),mtime=0)
    parts=fixture_parts(extra=extras,status='BLOQUEADO');raw=encode(parts)
    namespace=tmp_path/'namespace';namespace.mkdir(mode=0o700)
    controls=tmp_path/'controls';controls.mkdir(mode=0o700)
    source=namespace/'root-worker-native.log';source.write_bytes(raw);source.chmod(0o600)
    class DeclaredNamespace:pass
    handle=DeclaredNamespace();handle.path=namespace
    result={'namespace':handle,'request':parts[0],'controls':controls}
    scan={'inventory_sha256':calibration.digest(calibration.wire(parts[4])),
        'worker_report_sha256':calibration.digest(calibration.wire(parts[3]))}
    state={'result':result,'captured':False,'stream_path':source,'stream_identity':capture.identity(source.lstat()),
        'reference':{'bytes':len(raw),'sha256':calibration.digest(raw)},'scan':scan,
        'documentary':{'declared_pure_fixture':True,'native_qualification':False}}
    monkeypatch.setattr(capture,'_retired',lambda value:state)
    monkeypatch.setattr(calibration,'filesystem',lambda path:{'declared_pure_fixture':True})
    monkeypatch.setattr(carrier,'preflight',lambda observation,**kw:{'declared_pure_fixture':True})
    return state,controls/'lossless-source',frames


@pytest.mark.parametrize('count',(0,2,1202))
def test_actual_lossless_file_copy_preserves_red_partial_and_all1202_without_green(tmp_path,monkeypatch,count):
    state,destination,frames=declared_capture_state(tmp_path,monkeypatch,frames_count=count)
    value=capture.capture(object(),destination=destination)
    try:
        doc=value.documentary
        assert value.path==destination and value.files==doc['files'] and doc['payload_root']==str(destination)
        assert doc['actual_original_cuts']==count and doc['complete_original_1202_frame_set_observed']==(count==1202)
        assert doc['G5_GREEN'] is False and doc['qualification_claimed'] is False
        assert doc['cleanup_authority_granted'] is False and doc['artifact_ids_invented'] is False
        # Actual parser/copy output from this isolated pure fixture still has
        # no native ROOT registry and cannot authorize the native part packer.
        with pytest.raises(ValueError,match='CURRENT_SOURCE_ISSUED_NATIVE_RESULT_REQUIRED'):
            capture.validate_capture(value,require_complete_original_1202=True)
        for row in value.files:
            raw=value.read_original(row['relative_source'])
            assert (destination/row['capture_file']).read_bytes()==raw
            assert calibration.digest(raw)==row['sha256']
        if frames:
            assert calibration.digest(value.frame_capture(frames[-1]))==frames[-1]['sha256']
            changed={**frames[-1],'sha256':'f'*64}
            with pytest.raises(ValueError,match='ORIGINAL_CUT_SOURCE_HASH_REBOUND'):value.frame_capture(changed)
        with pytest.raises(ValueError,match='ONE_SHOT_ORIGINAL_STREAM_REQUIRED'):
            capture.capture(object(),destination=destination.parent/'again')
        state['stream_path'].write_bytes(b'REBOUND')
        with pytest.raises(ValueError,match='ORIGINAL_STREAM_REBOUND_AFTER_CAPTURE'):
            value.read_original('project/result.json')
    finally:capture._CAPTURES.pop(id(value),None)


def test_post_retirement_copy_rechecks_first_pass_inventory_digest(tmp_path,monkeypatch):
    state,destination,_=declared_capture_state(tmp_path,monkeypatch)
    state['scan']['inventory_sha256']='f'*64
    with pytest.raises(ValueError,match='SECOND_PASS_SOURCE_INVENTORY_REBOUND'):
        capture.capture(object(),destination=destination)


def test_post_retirement_actual_capacity_preflight_precedes_output_creation(tmp_path,monkeypatch):
    _,destination,_=declared_capture_state(tmp_path,monkeypatch)
    def fail(*a,**kw):raise ValueError('CALIBRATION_INSUFFICIENT_PHYSICAL_RESERVE')
    monkeypatch.setattr(carrier,'preflight',fail)
    with pytest.raises(ValueError,match='INSUFFICIENT_PHYSICAL_RESERVE'):
        capture.capture(object(),destination=destination)
    assert not destination.exists()

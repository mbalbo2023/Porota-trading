"""Post-FIN own backing retirement and bounded lossless G5A Source capture.

Retirement uses the issuer's actual in-process native-result registry, original
namespace FIN, external ROOT guardian FIN and exact autoclear loop birth. It
unlinks only the positively owned 26 GiB image after a complete binary scan.
The stream parser is documentary:
it never reconstructs ROOT authority, certifies G5 or invents an artifact ID.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct

from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_g5a_native_runner as runner

MAX_FILE=16*1024**2
CHUNK=512*1024
_RETIRED={}
_CAPTURES={}
IDENTITY_FIELDS=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_blocks',
    'st_atime_ns','st_mtime_ns','st_ctime_ns')
STABLE_TRANSFER=('st_dev','st_ino','st_mode','st_nlink','st_size','st_blocks','st_atime_ns','st_mtime_ns')
FRAME_PREFIX='control/original-cas-comparison/original-frames/'
FRAMES_MANIFEST='control/original-cas-comparison/g5b-original-frames-manifest.json.gz'
SOURCE_ROLE_PATHS={'native_Horizon':'project/result.json',
    'g5a_quota':'control/original-cas-comparison/g5a-quota.json.gz',
    'g5a_ledger':'control/original-cas-comparison/g5a-ledger.json.gz',
    'g5b_original_comparison':'control/original-cas-comparison/comparison-result.json.gz',
    'g5b_writer_binding':'control/original-cas-comparison/g5b-writer-binding.json.gz',
    'g5b_original_frames_manifest':FRAMES_MANIFEST}


def require(value,reason):
    if not value:raise ValueError(reason)


def clone(value):return calibration.decode(calibration.wire(value))


def identity(info):return {key:getattr(info,key) for key in IDENTITY_FIELDS}


def source_document(raw,reason):
    try:return calibration.decode(raw)
    except (ValueError,UnicodeDecodeError) as error:
        literal=str(error)
        if re.fullmatch(r'[A-Z][A-Z0-9_]{0,191}',literal):raise
        raise ValueError(reason) from None


def _birth():
    return {'pid':os.getpid(),'uid':os.geteuid(),'gid':os.getegid(),
        'start_ticks':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}


def _sha_stream(stream,count):
    total=0;hasher=hashlib.sha256()
    while total<count:
        part=stream.read(min(CHUNK,count-total))
        require(type(part) is bytes and bool(part),'G5A_CAPTURE_STREAM_TRUNCATED')
        total+=len(part);hasher.update(part)
    return hasher.hexdigest()


def _validate_identity(value,*,kind,bytes_count,owner):
    require(type(value) is dict and set(value)==set(IDENTITY_FIELDS)
        and all(type(number) is int and number>=0 for number in value.values())
        and (value['st_uid'],value['st_gid'])==owner and value['st_dev']>0 and value['st_ino']>0,
        'G5A_CAPTURE_SOURCE_NODE_NATIVE_IDENTITY_REQUIRED')
    if kind=='DIRECTORY':
        require(stat.S_ISDIR(value['st_mode']) and stat.S_IMODE(value['st_mode'])==0o700 and bytes_count==0,
            'G5A_CAPTURE_SOURCE_DIRECTORY700_REQUIRED')
    else:
        require(stat.S_ISREG(value['st_mode']) and stat.S_IMODE(value['st_mode']) in (0o400,0o600,0o644)
            and value['st_nlink']==1 and value['st_size']==bytes_count,
            'G5A_CAPTURE_SOURCE_SINGLELINK_REGULAR_REQUIRED')


def _relative(name):
    require(type(name) is str and 0<len(name)<=4096 and '\x00' not in name and '\\' not in name,
        'G5A_CAPTURE_LITERAL_SOURCE_MEMBER_REQUIRED')
    path=PurePosixPath(name)
    require(not path.is_absolute() and path.as_posix()==name and not any(part in ('','.','..') for part in path.parts)
        and path.parts[0] in ('project','control','edquot-probe'),
        'G5A_CAPTURE_ONLY_THREE_ORIGINAL_OWN_TREES_REQUIRED')
    return path


def parse_stream(stream,*,request,expected_bytes,expected_sha256,copy_file=None):
    """Pure bounded parser; copy callback writes only already-validated bytes.

    File offsets point at original framed bytes, never at a reconstructed input.
    Native callers independently authenticate stream custody before this parser.
    """
    require(type(expected_bytes) is int and 0<expected_bytes<=runner.MAX_STREAM_BYTES
        and type(expected_sha256) is str and runner.SHA64.fullmatch(expected_sha256),
        'G5A_CAPTURE_SOURCE_STREAM_EXACT_BOUND_AND_DIGEST_REQUIRED')
    require(stream.read(len(runner.STREAM_MAGIC))==runner.STREAM_MAGIC,
        'G5A_CAPTURE_ORIGINAL_BINARY_STREAM_MAGIC_REQUIRED')
    entries=[];directories=set();files={};previous=None;device=None;payload=allocated=0;worker=None;end=None
    owner=(request['owner_uid'],request['owner_gid']);request_sha=calibration.digest(calibration.wire(request))
    while end is None:
        before=stream.tell();length_raw=stream.read(4)
        require(type(length_raw) is bytes and len(length_raw)==4,'G5A_CAPTURE_FRAME_HEADER_TRUNCATED')
        length=struct.unpack('>I',length_raw)[0]
        require(0<length<=runner.MAX_FRAME_HEADER,'G5A_CAPTURE_CANONICAL64K_HEADER_BOUND')
        require(stream.tell()+length<=expected_bytes,'G5A_CAPTURE_FRAME_HEADER_EXCEEDS_ORIGINAL_STREAM')
        raw=stream.read(length)
        require(type(raw) is bytes and len(raw)==length,'G5A_CAPTURE_FRAME_HEADER_TRUNCATED')
        header=source_document(raw,'G5A_CAPTURE_CANONICAL_FRAME_JSON_REQUIRED')
        require(type(header) is dict and calibration.wire(header)==raw
            and header.get('schema')=='rc6.g5a-source-raw-frame.v2',
            'G5A_CAPTURE_EXACT_CANONICAL_SOURCE_FRAME_HEADER_REQUIRED')
        kind=header.get('kind')
        if kind=='END':
            require(worker is not None and set(header)=={'schema','kind','source_sha','source_tree','binding',
                'data_entries','data_payload_bytes','bytes_before_end','whole_current_own_namespace_streamed',
                'external_ROOT_FIN_claimed','G5_GREEN','qualification_claimed'}
                and header['source_sha']==request['source_sha'] and header['source_tree']==request['source_tree']
                and header['binding']==request['binding'] and header['data_entries']==len(entries)
                and type(header['data_entries']) is int and header['data_payload_bytes']==payload
                and type(header['data_payload_bytes']) is int and header['bytes_before_end']==before
                and type(header['bytes_before_end']) is int and header['whole_current_own_namespace_streamed'] is True
                and all(header[key] is False for key in ('external_ROOT_FIN_claimed','G5_GREEN','qualification_claimed')),
                'G5A_CAPTURE_COMPLETE_END_SOURCE_OR_COUNTS_REBOUND')
            end=header;break
        require(kind in ('DIRECTORY','FILE','WORKER_CONTROL') and worker is None,
            'G5A_CAPTURE_SOURCE_KIND_OR_FRAME_ORDER_REQUIRED')
        size=header.get('bytes');name=header.get('name')
        require(type(size) is int and 0<=size<=MAX_FILE,'G5A_CAPTURE_ORIGINAL16MIB_FILE_BOUND')
        if kind=='WORKER_CONTROL':
            require(set(header)=={'schema','kind','name','bytes','sha256'} and name=='g5a-worker-report.json'
                and size>0,'G5A_CAPTURE_SOURCE_FIXED_WORKER_CONTROL_REQUIRED')
        else:
            _relative(name)
            require(set(header)==({'schema','kind','name','bytes','identity','sha256'} if kind=='FILE'
                else {'schema','kind','name','bytes','identity'}) and len(entries)<runner.MAX_STREAM_ENTRIES
                and (previous is None or name>previous),'G5A_CAPTURE_EXACT_SORTED_SOURCE_NODE_INVENTORY_REQUIRED')
            _validate_identity(header['identity'],kind=kind,bytes_count=size,owner=owner)
            if device is None:device=header['identity']['st_dev']
            require(header['identity']['st_dev']==device,'G5A_CAPTURE_SOURCE_FOREIGN_FILESYSTEM_VETO')
            path=PurePosixPath(name)
            require(len(path.parts)==1 or str(path.parent) in directories,
                'G5A_CAPTURE_SOURCE_ORIGINAL_PARENT_DIRECTORY_REQUIRED')
            if kind=='DIRECTORY':directories.add(name)
            entries.append({key:header[key] for key in ('kind','name','bytes','identity')});previous=name
            allocated+=header['identity']['st_blocks']*512;payload+=size
            require(allocated<=runner.MAX_STREAM_BYTES and payload<=runner.MAX_STREAM_BYTES,
                'G5A_CAPTURE_ORIGINAL20GIB_PHYSICAL_OR_PAYLOAD_BOUND')
        offset=stream.tell()
        require(offset+size<=expected_bytes,'G5A_CAPTURE_FRAME_BYTES_EXCEED_ORIGINAL_STREAM')
        if kind=='DIRECTORY':continue
        digest=header.get('sha256')
        require(type(digest) is str and runner.SHA64.fullmatch(digest),
            'G5A_CAPTURE_ORIGINAL_FILE_HASH_REQUIRED')
        hasher=hashlib.sha256();remaining=size;writer=None
        if copy_file is not None:writer=copy_file(name,size,digest)
        try:
            control=[]
            while remaining:
                block=stream.read(min(CHUNK,remaining))
                require(type(block) is bytes and 0<len(block)<=remaining,'G5A_CAPTURE_FRAME_CONTENT_TRUNCATED')
                remaining-=len(block);hasher.update(block)
                if writer is not None:writer.write(block)
                if kind=='WORKER_CONTROL':control.append(block)
            require(hasher.hexdigest()==digest,'G5A_CAPTURE_ORIGINAL_FILE_BYTES_CHANGED')
            if writer is not None:writer.flush();os.fsync(writer.fileno())
            files[name]={'name':name,'bytes':size,'sha256':digest,'offset':offset,'kind':kind,
                **({'identity':header['identity']} if 'identity' in header else {})}
            if kind=='WORKER_CONTROL':worker=source_document(b''.join(control),'G5A_CAPTURE_ORIGINAL_WORKER_CONTROL_JSON_REQUIRED')
        finally:
            if writer is not None:writer.close()
    require(stream.tell()==expected_bytes and stream.read(1)==b'',
        'G5A_CAPTURE_ORIGINAL_STREAM_END_OR_TRAILING_BYTES_REBOUND')
    require({'project','control','edquot-probe'}<=directories,
        'G5A_CAPTURE_ALL_THREE_CURRENT_ORIGINAL_TREES_REQUIRED')
    stream.seek(0)
    require(_sha_stream(stream,expected_bytes)==expected_sha256,'G5A_CAPTURE_WHOLE_SOURCE_STREAM_DIGEST_REBOUND')
    inventory={'schema':'rc6.g5a-own-source-stream-inventory.v2','entries':entries,'payload_bytes':payload,
        'allocated_bytes':allocated,'retained_entries':len(entries),'source_sha':request['source_sha'],
        'source_tree':request['source_tree'],'binding':request['binding'],
        'data_substitution_performed_by_streamer':False,'storage_reduction_extrapolated':False,'qualification_claimed':False}
    require(type(worker) is dict and worker.get('schema')==runner.WORKER_SCHEMA
        and worker.get('request_sha256')==request_sha and worker.get('source_sha')==request['source_sha']
        and worker.get('source_tree')==request['source_tree'] and worker.get('binding')==request['binding']
        and worker.get('source_stream_inventory_sha256')==calibration.digest(calibration.wire(inventory))
        and worker.get('source_stream_retained_entries')==len(entries) and worker.get('source_stream_payload_bytes')==payload,
        'G5A_CAPTURE_SOURCE_WORKER_OR_WHOLE_INVENTORY_REBOUND')
    validate_original_control(worker,files,request=request)
    return {'schema':'rc6.g5a-closed-source-stream-index.v2','binding':request['binding'],
        'source_sha':request['source_sha'],'source_tree':request['source_tree'],'files':files,
        'inventory':inventory,'worker':worker,'end':end,'stream_bytes':expected_bytes,
        'stream_sha256':expected_sha256,'G5_GREEN':False,'qualification_claimed':False}


def validate_original_control(worker,files,*,request):
    """Replay original Source controls; this pure check mints no live authority."""
    import errno
    from scripts import rc6_material_horizon_producer as producer
    report=worker.get('report');fin=report.get('native_producer_fin') if type(report) is dict else None
    require(type(report) is dict and report.get('required_raw_complete') is True
        and report.get('operation_scope')==runner.OPERATION_SCOPE
        and report.get('status') in ('EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN','BLOQUEADO')
        and all(report.get(key) is False for key in ('G5_GREEN','qualification_claimed','external_ROOT_FIN_claimed'))
        and report.get('real_orders_sent')==0 and type(report.get('real_orders_sent')) is int
        and report.get('own_kernel_after_producer')=={'kernel_echild_verified':True,'errno':errno.ECHILD},
        'G5A_CAPTURE_ACTUAL_ORIGINAL_CONTROL_SCOPE_AND_PRODUCER_FIN_REQUIRED')
    require(type(fin) is dict and fin.get('schema')=='rc6.native-horizon-owned-fin.v1'
        and all(type(fin.get(key)) is int and fin[key]>0 for key in ('pid','parent_pid'))
        and fin.get('entry_module')=='scripts.rc6_material_horizon_producer'
        and fin.get('entry_member')=='scripts/rc6_material_horizon_producer.py'
        and all(fin.get(key)==request[key] for key in ('source_sha','source_tree','source_root','source_index_sha256'))
        and fin.get('native_finalizer_closed') is True
        and producer.native_finalizer_closed(fin.get('child_infrastructure_finalization')),
        'G5A_CAPTURE_ORIGINAL_SOURCE_PRODUCER_FIN_REBOUND')
    fin_raw=calibration.wire(fin);fin_row=files.get('control/producer-owned-fin.json',{})
    require(fin_row.get('bytes')==len(fin_raw) and fin_row.get('sha256')==calibration.digest(fin_raw),
        'G5A_CAPTURE_ORIGINAL_PRODUCER_FIN_BYTES_REBOUND')
    references=report.get('raw_files');seen=set()
    require(type(references) is list and 3<=len(references)<=8,
        'G5A_CAPTURE_ORIGINAL_FIXED_CONTROL_REFERENCES_REQUIRED')
    paths={'producer-owned-fin.json':'control/producer-owned-fin.json',
        'native-horizon-result.json':'project/result.json',
        'g5a-native-physical-control.json':'control/g5a-native-physical-control.json',
        'comparison-result.json.gz':'control/original-cas-comparison/comparison-result.json.gz',
        'g5a-ledger.json.gz':'control/original-cas-comparison/g5a-ledger.json.gz',
        'g5a-quota.json.gz':'control/original-cas-comparison/g5a-quota.json.gz',
        'g5b-writer-binding.json.gz':'control/original-cas-comparison/g5b-writer-binding.json.gz',
        'g5b-original-frames-manifest.json.gz':FRAMES_MANIFEST}
    total=0
    for reference in references:
        require(type(reference) is dict and set(reference)=={'path','relative_source','bytes','sha256'}
            and reference['path'] in paths and reference['path'] not in seen
            and reference['relative_source']==paths[reference['path']],
            'G5A_CAPTURE_ORIGINAL_CONTROL_REFERENCE_PATH_REBOUND')
        row=files.get(reference['relative_source'],{})
        require(type(reference['bytes']) is int and reference['bytes']==row.get('bytes')
            and reference['sha256']==row.get('sha256'),
            'G5A_CAPTURE_ORIGINAL_CONTROL_REFERENCE_BYTES_REBOUND')
        seen.add(reference['path']);total+=reference['bytes']
    require(seen=={name for name,path in paths.items() if path in files}
        and {'producer-owned-fin.json','native-horizon-result.json','g5a-native-physical-control.json'}<=seen
        and total==report.get('raw_total_bytes') and type(report.get('raw_total_bytes')) is int
        and total<=runner.MAX_RAW,'G5A_CAPTURE_ORIGINAL_CONTROL_REFERENCE_TOTAL_REBOUND')


def scan_transferred_stream(path,*,request,reference,expected_identity):
    """First pass: verify all original framed bytes without making a future copy."""
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
    try:
        require(identity(os.fstat(descriptor))==expected_identity==identity(path.lstat()),
            'G5A_CAPTURE_NATIVE_STREAM_IDENTITY_BEFORE_SCAN_REBOUND')
        with os.fdopen(descriptor,'rb',closefd=False) as stream:
            index=parse_stream(stream,request=request,expected_bytes=reference['bytes'],
                expected_sha256=reference['sha256'])
        require(identity(os.fstat(descriptor))==expected_identity==identity(path.lstat()),
            'G5A_CAPTURE_NATIVE_STREAM_IDENTITY_DURING_SCAN_REBOUND')
        return {'schema':'rc6.g5a-post-fin-whole-original-stream-scan.v2',
            'stream_bytes':index['stream_bytes'],'stream_sha256':index['stream_sha256'],
            'inventory_sha256':calibration.digest(calibration.wire(index['inventory'])),
            'retained_entries':index['inventory']['retained_entries'],
            'source_sha':request['source_sha'],'source_tree':request['source_tree'],'binding':request['binding'],
            'worker_report_sha256':index['files']['g5a-worker-report.json']['sha256'],
            'worker_status':index['worker']['report']['status'],'complete_original_binary_stream_verified':True,
            'future_copy_claimed':False,'G5_GREEN':False,'qualification_claimed':False}
    finally:os.close(descriptor)


class RetiredBacking:
    __slots__=()
    def __new__(cls,*a,**kw):raise ValueError('G5A_CAPTURE_AUTHENTIC_NATIVE_RETIREMENT_FACTORY_REQUIRED')
    @property
    def documentary(self):return clone(_retired(self)['documentary'])


def _retired(value):
    state=_RETIRED.get(id(value))
    require(type(value) is RetiredBacking and state is not None and state['object'] is value
        and state['birth']==_birth(),'G5A_CAPTURE_CURRENT_OPAQUE_NATIVE_RETIREMENT_REQUIRED')
    from scripts import rc6_g5a_carrier as carrier
    carrier.validate_native_result(state['result'])
    return state


def retire_own_backing(result):
    """Unlink only the actual original image after BOTH FIN and loop release."""
    from scripts import rc6_authenticated_fixture_lifecycle as owned
    from scripts import rc6_g5a_carrier as carrier
    from scripts import rc6_private_pid_capture_replay as replay
    from scripts import rc6_privileged_custody as privileged
    from scripts.rc6_controlled_native_child_manager import managed_custody_closed
    carrier.validate_native_result(result)
    namespace=result['namespace'];fin=result['fin'];request=result['request'];custody=result['custody_request']
    owned.require_fin(namespace,fin);runner.validate_request_shape(request)
    require(request['namespace_receipt']==owned.namespace_receipt(namespace)
        and result['kernel']==fin.kernel and managed_custody_closed(result['kernel']),
        'G5A_CAPTURE_ORIGINAL_NAMESPACE_AND_OUTER_FIN_REBOUND')
    request_raw=runner.read_input(namespace.path/'root-request.json',owner=(request['owner_uid'],request['owner_gid']))
    require(request_raw==calibration.wire(request),'G5A_CAPTURE_SOURCE_ROOT_REQUEST_BYTES_REBOUND')
    relay_raw=runner.read_input(namespace.path/'producer-native.log',maximum=2*1024**2,
        owner=(request['owner_uid'],request['owner_gid']))
    relay=source_document(relay_raw,'G5A_CAPTURE_ACTUAL_ROOT_FIN_RELAY_JSON_REQUIRED')
    require(type(relay) is dict,'G5A_CAPTURE_ACTUAL_ROOT_FIN_RELAY_OBJECT_REQUIRED')
    root=relay.get('privileged_controller',{});external=relay.get('external_guardian',{})
    require(type(root) is dict and type(external) is dict,'G5A_CAPTURE_ACTUAL_ROOT_FIN_RELAY_CONTROLLERS_REQUIRED')
    require(relay.get('schema')=='porota.rc6.g5a-custody-worker.v1'
        and relay.get('operation_scope')==runner.OPERATION_SCOPE and relay.get('binding')==request['binding']
        and relay.get('source_sha')==request['source_sha'] and relay.get('source_tree')==request['source_tree']
        and relay.get('request_sha256')==calibration.digest(request_raw)
        and relay.get('G5_GREEN') is False and relay.get('qualification_claimed') is False
        and root.get('root_native_FIN_closed') is True and managed_custody_closed(root.get('worker_kernel',{}))
        and root.get('manager_sha256')==external.get('original_manager_sha256')==calibration.DRIVER_SHA256
        and external.get('actual_root_FIN_closed') is True and external.get('private_PID1_native_pidfd_FIN') is True
        and managed_custody_closed(external.get('private_broker_kernel',{})),
        'G5A_CAPTURE_ACTUAL_ROOT_WORKER_AND_EXTERNAL_GUARDIAN_FIN_REQUIRED')
    birth=calibration.decode(runner.read_input(namespace.path/'broker-birth.json'))
    ack=calibration.decode(runner.read_input(namespace.path/'broker-birth.observed.json'))
    custody_raw=runner.read_input(namespace.path/'custody-request.json')
    require(custody_raw==calibration.wire(custody),'G5A_CAPTURE_ORIGINAL_CUSTODY_REQUEST_BYTES_REBOUND')
    replay._service_ack(birth,ack,result['actual_root_service_FIN'],request=custody,
        request_raw=custody_raw,controller=external.get('controller'))
    reference=relay.get('root_worker',{});transfer=external.get('g5a_raw_transfer',{})
    require(type(reference) is dict and type(transfer) is dict and reference.get('path')=='root-worker-native.log'
        and type(reference.get('bytes')) is int and 0<reference['bytes']<=runner.MAX_STREAM_BYTES
        and runner.SHA64.fullmatch(reference.get('sha256','')) and reference.get('actual_worker_FIN') is True
        and transfer.get('path')==reference['path'] and transfer.get('bytes')==reference['bytes']
        and transfer.get('sha256')==reference['sha256'] and transfer.get('identity_before')==reference.get('identity')
        and transfer.get('actual_root_worker_FIN_verified') is True
        and transfer.get('actual_private_PID1_FIN_verified') is True
        and transfer.get('actual_original_private_broker_FIN_verified') is True
        and type(transfer.get('guardian_kernel_echild')) is dict
        and transfer['guardian_kernel_echild'].get('kernel_echild_verified') is True,
        'G5A_CAPTURE_ACTUAL_ROOT_FIN_BOUNDED_RAW_TRANSFER_REQUIRED')
    before,after=transfer.get('identity_before'),transfer.get('identity_after')
    require(type(before) is dict and type(after) is dict and set(before)==set(after)==set(IDENTITY_FIELDS)
        and all(type(number) is int and number>=0 for value in (before,after) for number in value.values())
        and all(before[key]==after[key] for key in STABLE_TRANSFER)
        and (before['st_uid'],before['st_gid'])==(0,0)
        and (after['st_uid'],after['st_gid'])==(request['owner_uid'],request['owner_gid'])
        and stat.S_ISREG(after['st_mode']) and stat.S_IMODE(after['st_mode'])==0o600 and after['st_nlink']==1
        and after['st_size']==reference['bytes'],
        'G5A_CAPTURE_EXACT_GUARDIAN_RAW_TRANSFER_IDENTITY_REQUIRED')
    stream_path=namespace.path/'root-worker-native.log'
    require(identity(stream_path.lstat())==after,'G5A_CAPTURE_TRANSFERRED_NATIVE_STREAM_INODE_REBOUND')
    # A digest of a ROOT log alone cannot authorize retirement. A small RED
    # JSON response, incomplete binary stream or partial frame fails here,
    # while the original image and raw log are still preserved.
    scan=scan_transferred_stream(stream_path,request=request,reference=reference,expected_identity=after)
    loop,birth_sha=calibration.read_loop_birth(request,calibration.digest(request_raw))
    loop_absent=calibration.loop_gone(loop)
    image=namespace.path/'image.ext4'
    require(str(image)==request['image'] and image.parent==namespace.path and not image.is_symlink(),
        'G5A_CAPTURE_ONLY_ORIGINAL_OWN_IMAGE_RETIREMENT_ALLOWED')
    directory=os.open(namespace.path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    descriptor=None
    try:
        descriptor=os.open(image.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC,dir_fd=directory)
        actual=os.fstat(descriptor);expected=request['image_identity']
        require(stat.S_ISREG(actual.st_mode) and actual.st_nlink==1 and stat.S_IMODE(actual.st_mode)==0o600
            and (actual.st_uid,actual.st_gid)==(request['owner_uid'],request['owner_gid'])
            and actual.st_size==26*1024**3 and actual.st_blocks*512>=26*1024**3
            and all(getattr(actual,key)==expected[key] for key in
                ('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size'))
            and actual.st_dev==namespace.identity[0]
            and calibration.descriptor_mount_id(descriptor)==namespace.mount_id
            and identity(actual)==identity(os.stat(image.name,dir_fd=directory,follow_symlinks=False)),
            'G5A_CAPTURE_ORIGINAL26GIB_SINGLELINK_OWN_IMAGE_REBOUND')
        own_before=calibration.filesystem(namespace.path)
        owned.require_fin(namespace,fin);calibration.loop_gone(loop)
        require(identity(stream_path.lstat())==after,
            'G5A_CAPTURE_COMPLETELY_SCANNED_NATIVE_STREAM_REBOUND_BEFORE_RETIREMENT')
        os.unlink(image.name,dir_fd=directory);os.fsync(directory)
        os.close(descriptor);descriptor=None
        require(not os.path.lexists(image),'G5A_CAPTURE_ORIGINAL_IMAGE_UNLINK_NOT_OBSERVED')
        own_after=calibration.filesystem(namespace.path)
        require(own_after['device']==own_before['device'] and own_after['mount_id']==own_before['mount_id'],
            'G5A_CAPTURE_POST_RETIREMENT_FILESYSTEM_REBOUND')
    finally:
        if descriptor is not None:os.close(descriptor)
        os.close(directory)
    value=object.__new__(RetiredBacking)
    documentary={'schema':'rc6.g5a-original-own-backing-retirement.v2','source_sha':request['source_sha'],
        'source_tree':request['source_tree'],'binding':request['binding'],'namespace_nonce':namespace.nonce,
        'original_request_sha256':calibration.digest(request_raw),'relay_sha256':calibration.digest(relay_raw),
        'complete_original_stream_scan':scan,
        'loop_birth_sha256':birth_sha,'loop_finalization':loop_absent,'image_identity_at_unlink':identity(actual),
        'physically_unlinked_original_image_bytes':actual.st_blocks*512,'before':own_before,'after':own_after,
        'actual_free_bytes_delta':own_after['available_bytes']-own_before['available_bytes'],
        'future_cleanup_credit_bytes':0,'foreign_paths_removed':0,'global_loop_detach_attempted':False,
        'original_outer_FIN_verified':True,'actual_ROOT_worker_and_guardian_FIN_verified':True,
        'G5_GREEN':False,'qualification_claimed':False,'real_orders_sent':0}
    _RETIRED[id(value)]={'object':value,'birth':_birth(),'result':result,'documentary':documentary,
        'stream_path':stream_path,'stream_identity':after,'reference':clone(reference),'scan':scan,'captured':False}
    calibration.publish(result['controls']/'g5a-backing-retirement.json',documentary)
    return value


class RawCapture:
    __slots__=()
    def __new__(cls,*a,**kw):raise ValueError('G5A_CAPTURE_ORIGINAL_PINNED_STREAM_FACTORY_REQUIRED')
    @property
    def documentary(self):return clone(_capture(self)['documentary'])
    @property
    def path(self):return _capture(self)['destination']
    @property
    def files(self):return clone(_capture(self)['documentary']['files'])
    def read_original(self,name):return _read_captured(_capture(self),name)
    def frame_capture(self,reference):
        state=_capture(self)
        require(type(reference) is dict and type(reference.get('cut_index')) is int
            and 0<=reference['cut_index']<1202
            and reference.get('path')=='cut-'+str(reference['cut_index']).zfill(4)+'.json.gz',
            'G5A_CAPTURE_EXACT_ORIGINAL_CUT_REFERENCE_REQUIRED')
        name=FRAME_PREFIX+reference['path'];row=state['index']['files'].get(name)
        require(type(row) is dict and row['bytes']==reference.get('bytes') and row['sha256']==reference.get('sha256'),
            'G5A_CAPTURE_ORIGINAL_CUT_SOURCE_HASH_REBOUND')
        return _read_captured(state,name)


def _capture(value):
    state=_CAPTURES.get(id(value))
    require(type(value) is RawCapture and state is not None and state['object'] is value
        and state['birth']==_birth(),'G5A_CAPTURE_CURRENT_OPAQUE_PINNED_STREAM_REQUIRED')
    require(identity(state['stream_path'].lstat())==state['stream_identity'],
        'G5A_CAPTURE_ORIGINAL_STREAM_REBOUND_AFTER_CAPTURE')
    return state


def validate_capture(value,*,require_complete_original_1202=False):
    """Authenticate same-process copied Source bytes for the bounded part packer.

    The return value is a documentary copy. The actual issued handle, original
    native FIN and unchanged original stream remain mandatory; no JSON can mint
    this authority, and this function grants no namespace cleanup permission.
    """
    from scripts import rc6_g5a_carrier as carrier
    state=_capture(value)
    require(type(require_complete_original_1202) is bool,'G5A_CAPTURE_FIXED_ORIGINAL_PACK_SCOPE_REQUIRED')
    carrier.validate_native_result(state['result'])
    if require_complete_original_1202:
        require(state['documentary']['complete_original_1202_frame_set_observed'] is True
            and state['documentary']['actual_original_cuts']==1202,
            'G5A_CAPTURE_LOGICAL_RED_CANNOT_AUTHORIZE_FULL1202_PARTS')
    return clone(state['documentary'])


def _read_captured(state,name):
    row=state['index']['files'].get(name)
    require(type(row) is dict and row['bytes']<=MAX_FILE,'G5A_CAPTURE_ORIGINAL_BOUNDED_MEMBER_REQUIRED')
    descriptor=os.open(state['stream_path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
    try:
        require(identity(os.fstat(descriptor))==state['stream_identity'],'G5A_CAPTURE_PINNED_STREAM_DESCRIPTOR_REBOUND')
        raw=os.pread(descriptor,row['bytes'],row['offset'])
        require(len(raw)==row['bytes'] and calibration.digest(raw)==row['sha256']
            and identity(os.fstat(descriptor))==state['stream_identity'],
            'G5A_CAPTURE_ORIGINAL_OFFSET_BYTES_REBOUND')
        return raw
    finally:os.close(descriptor)


def capture(retired,*,destination):
    """Copy actual whole own stream after measured retirement; never certify G5."""
    from scripts import rc6_g5a_carrier as carrier
    from scripts import rc6_g5b_evidence as evidence
    state=_retired(retired);result=state['result'];namespace=result['namespace'];request=result['request']
    require(not state['captured'],'G5A_CAPTURE_ONE_SHOT_ORIGINAL_STREAM_REQUIRED');state['captured']=True
    destination=runner.canonical_path(str(destination))
    require(not os.path.lexists(destination) and destination.parent==result['controls']
        and not destination.is_relative_to(namespace.path)
        and not destination.is_relative_to(Path(request['source_root']))
        and not destination.is_relative_to(Path(request['source_repo'])),
        'G5A_CAPTURE_FRESH_OWN_SOURCE_CONTROL_DESTINATION_REQUIRED')
    measured=calibration.filesystem(result['controls']);bound=carrier.preflight(measured,capture_phase=True)
    destination.mkdir(mode=0o700)
    fd=os.open(state['stream_path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
    saved={}
    try:
        require(identity(os.fstat(fd))==state['stream_identity'],'G5A_CAPTURE_HELD_ORIGINAL_STREAM_REBOUND')
        def copy_file(name,size,digest):
            require(name not in saved,'G5A_CAPTURE_DUPLICATED_OUTPUT_MEMBER')
            path=destination/(str(len(saved)).zfill(6)+'.raw')
            output=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            saved[name]={'capture_file':path.name,'bytes':size,'sha256':digest}
            return os.fdopen(output,'wb')
        with os.fdopen(fd,'rb',closefd=False) as stream:
            index=parse_stream(stream,request=request,expected_bytes=state['reference']['bytes'],
                expected_sha256=state['reference']['sha256'],copy_file=copy_file)
        require(identity(os.fstat(fd))==state['stream_identity']==identity(state['stream_path'].lstat()),
            'G5A_CAPTURE_SOURCE_STREAM_CHANGED_DURING_COPY')
        require(calibration.digest(calibration.wire(index['inventory']))==state['scan']['inventory_sha256']
            and index['files']['g5a-worker-report.json']['sha256']==state['scan']['worker_report_sha256'],
            'G5A_CAPTURE_POST_RETIREMENT_SECOND_PASS_SOURCE_INVENTORY_REBOUND')
        result_report=index['worker'].get('report',{})
        require(result_report.get('required_raw_complete') is True
            and all(result_report.get(key) is False for key in ('G5_GREEN','qualification_claimed')),
            'G5A_CAPTURE_ORIGINAL_CONTROL_CANNOT_GRANT_G5_GREEN')
        raw_roles={}
        for role,name in SOURCE_ROLE_PATHS.items():
            if name not in saved:continue
            raw_roles[role]=runner.read_input(destination/saved[name]['capture_file'],
                expected_sha256=saved[name]['sha256'],maximum=MAX_FILE)
        manifest=evidence.capture_document(raw_roles['g5b_original_frames_manifest']) \
            if 'g5b_original_frames_manifest' in raw_roles else None
        frames=[] if manifest is None else manifest.get('frames')
        require(manifest is None or (manifest.get('schema')=='rc6.g5a-all1202-original-byte-frames.v2'
            and manifest.get('source_sha')==request['source_sha'] and manifest.get('source_tree')==request['source_tree']
            and type(manifest.get('actual_original_cuts')) is int and 0<=manifest['actual_original_cuts']<=1202
            and type(frames) is list and len(frames)==manifest['actual_original_cuts']
            and type(manifest.get('writer_codec_restorer_binding_sha256')) is str
            and runner.SHA64.fullmatch(manifest['writer_codec_restorer_binding_sha256'])
            and manifest.get('qualification_claimed') is False),
            'G5A_CAPTURE_ORIGINAL_FRAME_MANIFEST_SOURCE_OR_COUNTS_REBOUND')
        for cut,frame in enumerate(frames):
            require(type(frame) is dict and frame.get('cut_index')==cut and type(frame.get('cut_index')) is int
                and frame.get('path')=='cut-'+str(cut).zfill(4)+'.json.gz'
                and FRAME_PREFIX+frame['path'] in saved
                and saved[FRAME_PREFIX+frame['path']]['bytes']==frame.get('bytes')
                and saved[FRAME_PREFIX+frame['path']]['sha256']==frame.get('sha256'),
                'G5A_CAPTURE_EXACT_ORIGINAL_FRAME_BYTES_REQUIRED')
        require(len([name for name in saved if name.startswith(FRAME_PREFIX)])==len(frames),
            'G5A_CAPTURE_NO_ADDITIONAL_ORIGINAL_FRAMES_ALLOWED')
        complete=len(frames)==1202 and set(raw_roles)==set(SOURCE_ROLE_PATHS)
        documentary={'schema':'rc6.g5a-post-fin-lossless-capture.v2','source_sha':request['source_sha'],
            'source_tree':request['source_tree'],'binding':request['binding'],
            'payload_root':str(destination),'raw_capture_status':'COMPLETE_CURRENT_OWN_RAW_EVIDENCE_ONLY',
            'stream_sha256':state['reference']['sha256'],'stream_bytes':state['reference']['bytes'],
            'original_retirement':state['documentary'],'actual_post_retirement_capacity':measured,
            'control_bound':bound,'files':[{'relative_source':name,**saved[name],
                'original_stream_offset':index['files'][name]['offset']} for name in sorted(saved)],
            'original_frame_members':[{'cut_index':cut,'path':frame['path'],'bytes':frame['bytes'],
                'sha256':frame['sha256'],'capture_file':saved[FRAME_PREFIX+frame['path']]['capture_file']}
                for cut,frame in enumerate(frames)],
            'actual_original_cuts':len(frames),'complete_original_1202_frame_set_observed':complete,
            'writer_codec_restorer_binding_sha256':manifest.get('writer_codec_restorer_binding_sha256') if manifest else None,
            'Source_RAW_roles':{role:{'relative_source':name,**saved[name]} for role,name in SOURCE_ROLE_PATHS.items() if name in saved},
            'missing_Source_RAW_roles':sorted(set(SOURCE_ROLE_PATHS)-set(raw_roles)),
            'original_producer_fin':result_report.get('native_producer_fin'),
            'worker_report':index['worker'],'G5_GREEN':False,'qualification_claimed':False,'real_orders_sent':0,
            'artifact_ids_invented':False,'cleanup_authority_granted':False,'storage_reduction_extrapolated':False}
        require(len(calibration.wire(documentary))<=16*1024**2,
            'G5A_CAPTURE_SOURCE_MANIFEST16MIB_PHYSICAL_CONTROL_BOUND')
        calibration.publish(destination/'manifest.json',documentary)
        value=object.__new__(RawCapture)
        _CAPTURES[id(value)]={'object':value,'birth':_birth(),'documentary':documentary,'index':index,
            'stream_path':state['stream_path'],'stream_identity':state['stream_identity'],'destination':destination,
            'result':result}
        return value
    finally:os.close(fd)

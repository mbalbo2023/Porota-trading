"""READ_ONLY host-stage observations and the unresolved aggregate-capacity guard.

The original manager polls every five seconds and spends at most five seconds
closing a failed phase. Those observations are useful for stopping a RED; they
do not enforce a 20 GiB aggregate filesystem limit between observations. This
module never substitutes them for native aggregate enforcement or opens a gate.
The separate native issuer source has no accepted all-stage Actions proof yet.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import stat
import time

from scripts import rc6_capacity_calibration as calibration

GIB=1024**3
HARD_STAGE_BYTES=20*GIB
RESERVE_BYTES=4*GIB
STAGES=frozenset({'preparation','reader-collection','reader-execution'})
CAS_REVIEW_MODULES=frozenset({'tests/test_rc6_archive_slice_reuse.py',
    'tests/test_rc6_archive_reader_review.py','tests/test_rc6_archive_v3_image_smoke.py',
    'tests/test_rc6_cas_original_comparison.py'})
SOURCE_MEMBERS=CAS_REVIEW_MODULES|frozenset({'scripts/rc6_g5a_actions.py',
    'scripts/rc6_controlled_native_child_manager.py','scripts/rc6_g5b_native_readers.py',
    'tests/test_rc6_component_archive.py','tests/test_rc6_archive_grouped_wire.py',
    'requirements.build.lock.txt','requirements.lock.txt'})
BARRIER='G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED'


def require(value,reason):
    if not value:raise ValueError(reason)


def source_risk_inventory(source_root):
    """Record actual Source, not an invented bound for its dynamic writes."""
    from scripts import rc6_archive_reader_review as readers
    root=Path(source_root).absolute()
    require(readers.CAS_REVIEW_MODULES==CAS_REVIEW_MODULES,
        'G5A_HOST_STAGE_ORIGINAL_FOUR_REVIEW_MODULES_REBOUND')
    raw={name:calibration.read(root/name,4*1024**2) for name in sorted(SOURCE_MEMBERS)}
    require(calibration.digest(raw['scripts/rc6_controlled_native_child_manager.py'])==calibration.DRIVER_SHA256,
        'G5A_HOST_STAGE_ORIGINAL_MANAGER_BYTES_REBOUND')
    preparation=ast.parse(raw['scripts/rc6_g5a_actions.py'])
    functions={node.name:node for node in preparation.body if isinstance(node,ast.FunctionDef)}
    require('product311_commands' in functions,'G5A_HOST_STAGE_ORIGINAL_INSTALLER_SOURCE_REQUIRED')
    installer=functions['product311_commands']
    literals={node.value for node in ast.walk(installer) if isinstance(node,ast.Constant) and type(node.value) is str}
    require({'--require-hashes','--only-binary=:all:',
        '--no-binary=msgpack,ppi-client,signalrcoreppi,ta','--no-build-isolation',
        'requirements.lock.txt','requirements.build.lock.txt'}<=literals,
        'G5A_HOST_STAGE_ORIGINAL157_INSTALLER_FLAGS_REBOUND')
    calls={}
    for name in sorted(CAS_REVIEW_MODULES|{'tests/test_rc6_component_archive.py','tests/test_rc6_archive_grouped_wire.py'}):
        tree=ast.parse(raw[name]);calls[name]=sorted({ast.unparse(node.func) for node in ast.walk(tree)
            if isinstance(node,ast.Call)})
    return {'schema':'rc6.g5a-host-stage-source-risk-inventory.v1',
        'source_members_sha256':{name:calibration.digest(value) for name,value in raw.items()},
        'exact_review_modules':sorted(CAS_REVIEW_MODULES),'actual_source_call_sites':calls,
        'original157_source_build_exceptions':['msgpack','ppi-client','signalrcoreppi','ta'],
        'installer_can_execute_locked_source_builds':True,
        'source_build_hash_is_not_an_aggregate_allocation_bound':True,
        'original_manager_progress_poll_seconds':5,'original_failed_phase_cleanup_seconds':5,
        'complete_write_burst_bound_proved':False,'aggregate_native_enforcement_observed':False,
        'bootstrap_private_quota_conveys_host_scope':False,
        'reason':BARRIER,'launch_authorized':False,'G5_GREEN':False,'qualification_claimed':False}


def allocation_observation(root,*,stage):
    """Observe existing own inodes only, without payload reads or any mutation.

    Symlink inodes are counted without following them (venv may contain lib64).
    Unlinked-but-open files and temporary allocate/delete bursts are explicitly
    outside this sampled tree observation, so it cannot prove a hard peak.
    """
    require(stage in STAGES,'G5A_HOST_STAGE_EXACT_STAGE_REQUIRED')
    root=Path(root).absolute();filesystem=calibration.filesystem(root)
    owner=(os.getuid(),os.getgid());top=root.lstat()
    require(owner[0]>0 and owner[1]>0 and (top.st_uid,top.st_gid)==owner and stat.S_ISDIR(top.st_mode),
        'G5A_HOST_STAGE_NATIVE_NONROOT_OWN_DIRECTORY_REQUIRED')
    queue=[root];total=entries=0
    while queue:
        path=queue.pop();info=path.lstat()
        require(info.st_dev==filesystem['device'] and (info.st_uid,info.st_gid)==owner
            and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)),
            'G5A_HOST_STAGE_FOREIGN_DEVICE_OWNER_OR_SPECIAL_NODE')
        entries+=1;total+=info.st_blocks*512
        require(entries<=100000,'G5A_HOST_STAGE_ORIGINAL100000_ENTRY_LIMIT')
        if stat.S_ISDIR(info.st_mode):
            descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
            try:
                held=os.fstat(descriptor)
                require((held.st_dev,held.st_ino,held.st_uid,held.st_gid)==
                    (info.st_dev,info.st_ino,info.st_uid,info.st_gid),
                    'G5A_HOST_STAGE_DIRECTORY_CUSTODY_REBOUND')
                queue.extend(path/name for name in os.listdir(descriptor))
            finally:os.close(descriptor)
    return {'schema':'rc6.g5a-host-stage-allocation-sample.v1','stage':stage,'filesystem':filesystem,
        'root':str(root),'root_identity':[top.st_dev,top.st_ino,top.st_uid,top.st_gid,top.st_mode],
        'actual_tree_allocated_bytes':total,'actual_retained_entries':entries,
        'review_short_path_bytes':len(os.fsencode(root)),
        'original_home_filesystem_matches':filesystem['device']==Path.home().stat().st_dev,
        'measurement_finished_monotonic_ns':time.monotonic_ns(),
        'metric':'SAMPLED_ST_BLOCKS_NOT_CONTINUOUS_HIGH_WATER',
        'unlinked_open_bytes_accounted':False,'transient_burst_bound_proved':False,
        'launch_authorized':False,'G5_GREEN':False,'qualification_claimed':False}


def preflight_observation(observed):
    """Check actual reserves without claiming the still-missing hard enforcement."""
    require(type(observed) is dict and observed.get('stage') in STAGES,
        'G5A_HOST_STAGE_CURRENT_NATIVE_OBSERVATION_REQUIRED')
    require(type(observed.get('actual_tree_allocated_bytes')) is int
        and 0<=observed['actual_tree_allocated_bytes']<=HARD_STAGE_BYTES,
        'G5A_HOST_STAGE_ORIGINAL20GIB_SAMPLE_EXCEEDED')
    fs=observed.get('filesystem');calibration.require_capacity(fs,HARD_STAGE_BYTES)
    if observed['stage'].startswith('reader-'):
        require(type(observed.get('review_short_path_bytes')) is int
            and observed['review_short_path_bytes']<=50 and observed.get('original_home_filesystem_matches') is True,
            'G5A_HOST_STAGE_ORIGINAL_HOME_FILESYSTEM_AND_SHORT_PATH_REQUIRED')
    return {'status':'RESERVES_OBSERVED_AGGREGATE_ENFORCEMENT_NOT_VERIFIED',
        'stage':observed['stage'],'hard_stage_bytes':HARD_STAGE_BYTES,'physical_reserve_bytes':RESERVE_BYTES,
        'minimum_free_inode_fraction':'1/10','future_cleanup_credit_bytes':0,'future_GC_credit_bytes':0,
        'compression_credit_bytes':0,'capacity_launch_authorized':False,'reason':BARRIER,
        'G5_GREEN':False,'qualification_claimed':False}


class DiagnosticCutoff:
    """Composable original-manager progress guard, never an admission factory.

    A raise lets the original manager close only its own child/PGID and preserve
    its actual FIN. This class never signals, launches, deletes or reads payload.
    Five-second sampling plus cleanup cannot bound a previously unseen burst.
    """
    def __init__(self,root,*,stage,previous=None):
        require(stage in STAGES and (previous is None or callable(previous)),
            'G5A_HOST_STAGE_ORIGINAL_MANAGER_CALLBACK_SCOPE_REQUIRED')
        self.root=Path(root).absolute();self.stage=stage;self.previous=previous;self.samples=[]
        self.sampled_maximum=0

    def progress(self,stage,pid,entered,deadline,fd):
        require(stage in ('started','poll') and type(pid) is int and pid>0
            and type(fd) is int and stat.S_ISREG(os.fstat(fd).st_mode),
            'G5A_HOST_STAGE_ORIGINAL_MANAGER_PROGRESS_REQUIRED')
        observed=allocation_observation(self.root,stage=self.stage);fs=observed['filesystem']
        self.samples.append(observed);self.sampled_maximum=max(self.sampled_maximum,observed['actual_tree_allocated_bytes'])
        require(observed['actual_tree_allocated_bytes']<=HARD_STAGE_BYTES,
            'G5A_HOST_STAGE_ORIGINAL20GIB_LIVE_SAMPLE_CUTOFF')
        require(fs['available_bytes']>=RESERVE_BYTES,'G5A_HOST_STAGE_ORIGINAL4GIB_LIVE_RESERVE_CUTOFF')
        require(fs['free_inodes']*10>=fs['total_inodes'],'G5A_HOST_STAGE_ORIGINAL10PCT_INODE_CUTOFF')
        if self.previous:self.previous(stage,pid,entered,deadline,fd)

    @property
    def documentary(self):
        return {'metric':'SAMPLED_ALLOCATED_MAXIMUM_NOT_CONTINUOUS_PEAK','sampled_allocated_maximum_bytes':self.sampled_maximum,
            'sample_count':len(self.samples),'hard_aggregate_enforcement_proved':False,
            'launch_authorized':False,'G5_GREEN':False,'qualification_claimed':False}


def require_host_stage_admission(*,stage,enforcement=None,owner_acceptance=None):
    """Fail before installing, exporting or reviewing; approval cannot bypass it.

    The issuer source is not an authenticated native all-stage receipt. Private
    ROOT writer quota, JSON receipts, accepted-contract flags and sampled maxima
    cannot confer this missing scope. Keep the barrier until all original stage
    writers have the same kernel quota, real EDQUOT, seal and FIN evidence.
    """
    require(stage in STAGES,'G5A_HOST_STAGE_EXACT_STAGE_REQUIRED')
    raise ValueError(BARRIER)


def owner_proposal():
    return {'schema':'rc6.g5a-host-stage-owner-proposal.v1',
        'proposal':'G5A_HOST_STAGE_AGGREGATE_20GIB_V1','state':'PROPUESTO_REQUIRES_EXPLICIT_OWNER_DECISION',
        'required_scope':sorted(STAGES),'hard_stage_bytes':HARD_STAGE_BYTES,'physical_reserve_bytes':RESERVE_BYTES,
        'minimum_free_inode_fraction':'1/10','required_review_modules':sorted(CAS_REVIEW_MODULES),
        'decision':'Authorize an own enforced-filesystem topology covering original157 installation, complete Git/Source '
            'preparation and the exact four post-FIN reader modules, with native aggregate20GiB enforcement for every writer '
            'and temporary, while preserving the original Source/home filesystem and path requirements.',
        'mandatory_native_proof':['own immutable filesystem/marker/Source identity','aggregate20GiB hardlimit readback',
            'actual EDQUOT control','all original installers, builds, subprocesses and temporary roots in that filesystem',
            'exact original four review modules and PRODUCT157','sealed mutation denied',
            'original native manager and actual FIN','current physical reserve4GiB and free inodes10%'],
        'polling_only_contract_acceptable':False,'bootstrap_private_quota_confers_host_scope':False,
        'approval_alone_authorizes_launch':False,'limits_relaxed':False,'future_deletion_credit_bytes':0,
        'G5_GREEN':False,'qualification_claimed':False,'real_orders_sent':0}

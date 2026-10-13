import os,sys,subprocess,json,hashlib,time,signal
from pathlib import Path
repo=Path('/workspace/porota_rc6_worktrees/sre_maximum_member_c437')
raw=Path('/tmp/rc6-sre-maximum-member-c437-pythonpath-raw')
assert not raw.exists(), 'RAW_MUST_BE_FRESH'
sys.path.insert(0,str(repo))
from scripts.porota_dependency_repro_audit import installed_distribution_audit
closure=installed_distribution_audit(json.loads((repo/'ops/policy/rc6-supply-chain-v1.json').read_bytes()))
assert closure['status']=='GREEN' and closure['installed_total']==closure['expected_total']==157, closure
assert subprocess.check_output(['git','--no-replace-objects','-C',str(repo),'rev-parse','HEAD'],text=True).strip()=='c43782ba33e23cbb813a4a82416f97df9957fc46'
assert subprocess.check_output(['git','--no-replace-objects','-C',str(repo),'status','--porcelain'],text=True)==''
raw.mkdir(mode=0o700)
env={**os.environ,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONDONTWRITEBYTECODE':'1'}
base=[sys.executable,'-I','-B','-m','pytest','tests/test_rc6_archive_maximum_member_guard.py','-p','no:cacheprovider','-o','pythonpath=.']
collection=subprocess.run(base+['--collect-only','-q','-o','addopts=--strict-markers'],cwd=repo,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
(raw/'collection.log').write_bytes(collection.stdout)
expected=['tests/test_rc6_archive_maximum_member_guard.py::test_native_near64mib_original_ancestor_depth32_restore_has_real_bounded_rss_and_exact_members','tests/test_rc6_archive_maximum_member_guard.py::test_native_admission_rejects_three_observed_near64mib_images_under_original_live128mib_without_mutation']
collected=[line for line in collection.stdout.decode().splitlines() if line.startswith('tests/')]
assert collection.returncode==0 and collected==expected, (collection.returncode,collected,collection.stdout)
command=base+['-o','junit_family=legacy','--junitxml='+str(raw/'maximum-member.xml')]
control={'schema':'rc6.sre-maximum-member-focal-launch.v1','source_sha':'c43782ba33e23cbb813a4a82416f97df9957fc46','source_tree':'04ae55d3eb7eb27ec737134ece91f64c27c872ba','cwd':str(repo),'command':command,'uid':os.geteuid(),'interpreter':sys.executable,'python':sys.version,'installed_closure':closure,'collected_nodes_before_fixture':collected,'collection_sha256':hashlib.sha256(collection.stdout).hexdigest(),'automatic_guard_nodes':2,'native_executions_requested':1,'inner_native_full_envelope_seconds':300,'inner_termination_window_seconds':330,'outer_pytest_management_only_seconds':390,'source_clean_before':True,'script_sha256':hashlib.sha256((repo/'scripts/rc6_archive_maximum_member_probe.py').read_bytes()).hexdigest(),'test_sha256':hashlib.sha256((repo/'tests/test_rc6_archive_maximum_member_guard.py').read_bytes()).hexdigest(),'corrected_harness_only':'EXPLICIT_PYTEST_PYTHONPATH_CANONICAL_VERIFIED_CWD; SOURCE_UNCHANGED','previous_infrastructure_raw':'/tmp/rc6-sre-maximum-member-c437-raw'}
(raw/'launch.json').write_text(json.dumps(control,indent=2)+'\n')
started=time.monotonic()
with (raw/'pytest.log').open('xb') as log:
 child=subprocess.Popen(command,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 (raw/'pytest.pid').write_text(str(child.pid)+'\n')
 print(json.dumps({'event':'START','pytest_pid':child.pid,'wrapper_pid':os.getpid(),'raw':str(raw),'command':command,'collected_nodes':collected,'closure':closure}),flush=True)
 timed_out=False
 while True:
  pid,status,usage=os.wait4(child.pid,os.WNOHANG)
  if pid:
   child.returncode=os.waitstatus_to_exitcode(status)
   break
  if time.monotonic()-started>390:
   timed_out=True
   os.killpg(child.pid,signal.SIGKILL)
   pid,status,usage=os.wait4(child.pid,0)
   child.returncode=os.waitstatus_to_exitcode(status)
   break
  time.sleep(.25)
 elapsed=time.monotonic()-started
resource={'schema':'rc6.sre-maximum-member-pytest-parent-wait4.v1','pid':child.pid,'returncode':child.returncode,'management_timeout':timed_out,'elapsed_wall_seconds':elapsed,'cpu_user_seconds':usage.ru_utime,'cpu_system_seconds':usage.ru_stime,'real_peak_rss_bytes':usage.ru_maxrss*1024,'scope':'EXACT_PYTEST_PID_AND_KERNEL_ACCOUNTED_REAPED_DESCENDANTS; NATIVE_CHILD_300_SECOND_POLICY_IS_SEPARATE_ACTUAL_INNER_WAIT4','source_clean_after':subprocess.check_output(['git','--no-replace-objects','-C',str(repo),'status','--porcelain'],text=True)==''}
(raw/'pytest-resource.json').write_text(json.dumps(resource,indent=2)+'\n')
print(json.dumps({'event':'END','resource':resource}),flush=True)
print((raw/'pytest.log').read_text(),flush=True)
raise SystemExit(child.returncode)

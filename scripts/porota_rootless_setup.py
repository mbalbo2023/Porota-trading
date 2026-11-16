#!/usr/bin/env python3
"""One-time Porota rootless setup; inspection never changes the host.

Run through the authorized GitHub Action as the existing SSH account.  This
module may install only missing, plan-checked distro networking packages. It
never changes another user's daemon or prints configuration values. Routine
promotion is in porota_rootless_host.py.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

LEGACY_NAMES = (
    "porota_production_observer", "porota_production_dashboard", "porota_trading_bot",
)
SERVICE = "porota-rc6-docker.service"
DOCKER = "/usr/bin/docker"
SUDO = "/usr/bin/sudo"
SYSTEMCTL = "/usr/bin/systemctl"
INSPECT_UNITS = (
    "porota-snapshot-preopen-rc6.service", "porota-snapshot-postclose-rc6.service",
    "porota-runtime-evidence-rc6.service", "porota-critical-approval-rc6.service",
    "porota-critical-github-proxy-rc6.service",
)


# This program performs only bounded reads of the five observed RC6 unit
# definitions. Environment values and opaque shell arguments never leave it.
UNIT_DETAILS = r'''import hashlib,json,os,re,shlex,stat,sys
names=set(json.loads(sys.argv[1])); paths=json.loads(sys.argv[2]); results=[]
simple={'User','Group','WorkingDirectory','ProtectSystem','ProtectHome','ProtectKernelTunables','ProtectKernelModules','ProtectControlGroups','PrivateTmp','PrivateDevices','NoNewPrivileges','ReadWritePaths','ReadOnlyPaths','InaccessiblePaths','EnvironmentFile','ConditionPathExists','RuntimeDirectory','StateDirectory','SupplementaryGroups','After','Wants','Requires','Type','TimeoutStartSec','TimeoutStopSec','DynamicUser','BindPaths','BindReadOnlyPaths','RootDirectory'}
commands={'ExecStart','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecCondition','ExecReload'}
def command(value,depth=0):
    result={'sha256':hashlib.sha256(value.encode()).hexdigest(),'tokens':[]}
    try: tokens=shlex.split(value)
    except ValueError: return dict(result,parse='UNVERIFIED')
    for token in tokens[:80]:
        if re.fullmatch(r'[-+!@:]?/[A-Za-z0-9_./@%+:-]+',token) and '..' not in token.split('/'):
            result['tokens'].append({'path':token})
        elif re.fullmatch(r'--?[A-Za-z][A-Za-z0-9_-]*',token):
            result['tokens'].append({'flag':token})
        elif token in {'python','python3','docker','exec','sh','bash','porota_production_observer','porota_production_dashboard'}:
            result['tokens'].append({'literal':token})
        else:
            opaque={'opaque_sha256':hashlib.sha256(token.encode()).hexdigest(),'bytes':len(token.encode())}
            if depth==0 and any(c.isspace() for c in token): opaque['nested']=command(token,1)
            result['tokens'].append(opaque)
    return result
for path in paths:
    item={'path':path,'status':'NO_VERIFICADO'}; results.append(item)
    try:
        allowed=any(path in ('/etc/systemd/system/'+name,'/usr/lib/systemd/system/'+name,'/lib/systemd/system/'+name) or re.fullmatch('/etc/systemd/system/'+re.escape(name)+r'\.d/[A-Za-z0-9_.-]+\.conf',path) for name in names)
        if not allowed: continue
        meta=os.lstat(path); item.update(uid=meta.st_uid,gid=meta.st_gid,mode=oct(stat.S_IMODE(meta.st_mode)),bytes=meta.st_size,symlink=stat.S_ISLNK(meta.st_mode))
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_nlink!=1 or meta.st_mode&0o022 or meta.st_size>65536: continue
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try: raw=os.read(fd,65537)
        finally: os.close(fd)
        if len(raw)!=meta.st_size: continue
        item.update(status='OBSERVED',sha256=hashlib.sha256(raw).hexdigest(),directives=[],environment_names=[])
        joined=raw.decode().replace('\\\n',' ')
        for line in joined.splitlines():
            if not line or line.lstrip().startswith(('#',';','[')): continue
            key,sep,value=line.partition('='); key=key.strip(); value=value.strip()
            if not sep: continue
            if key=='Environment':
                try: entries=shlex.split(value)
                except ValueError: entries=[]
                item['environment_names']+=sorted({v.split('=',1)[0] for v in entries if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',v.split('=',1)[0])})
            elif key in commands: item['directives'].append({'name':key,**command(value)})
            elif key in simple:
                if re.fullmatch(r'[A-Za-z0-9_./@%:+!~* -]{0,2048}',value) and '://' not in value:
                    item['directives'].append({'name':key,'value':value})
                else: item['directives'].append({'name':key,'opaque_sha256':hashlib.sha256(value.encode()).hexdigest()})
    except (OSError,ValueError,UnicodeError): pass
print(json.dumps(results,sort_keys=True))
'''


def detailed_units(inventory, *, run=subprocess.run):
    selected = [row for row in inventory.get("units", []) if row.get("name") in INSPECT_UNITS]
    paths = set()
    for row in selected:
        if row.get("FragmentPath"):
            paths.add(row["FragmentPath"])
        paths.update(row.get("DropInPaths", "").split())
    result = _call((SUDO, "-n", "--", "/usr/bin/python3", "-c", UNIT_DETAILS,
                    json.dumps(INSPECT_UNITS), json.dumps(sorted(paths))), run=run)
    if result is None or result.returncode:
        return {"status": "NO_VERIFICADO", "files": []}
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) > 30:
            raise ValueError()
    except ValueError:
        return {"status": "NO_VERIFICADO", "files": []}
    return {"status": "OBSERVED", "files": rows}


def toolchain_metadata(*, run=subprocess.run):
    result = {}
    for name in ("docker", "dockerd", "dockerd-rootless.sh", "rootlesskit", "slirp4netns", "pasta", "vpnkit", "gvproxy"):
        path = shutil.which(name, path="/usr/bin:/bin:/usr/sbin:/sbin")
        row = {"available": bool(path), "path": path, "version": "NO_VERIFICADO"}
        result[name] = row
        if path and name != "dockerd-rootless.sh":
            version = _call((path, "--version"), run=run, timeout=8)
            match = re.search(r"\b(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?)\b",
                              version.stdout[:4096] if version is not None and version.returncode == 0 else "")
            if match:
                row["version"] = match.group(1)
    return result


def subid_metadata(identity):
    result = {}
    for name in ("subuid", "subgid"):
        rows = []
        try:
            raw = Path("/etc/" + name).read_bytes()
            if len(raw) > 1048576:
                raise ValueError()
            for line in raw.decode("ascii").splitlines():
                parts = line.split(":")
                if len(parts) == 3 and parts[0] in {identity.pw_name, str(identity.pw_uid)} and all(
                        part.isdigit() for part in parts[1:]):
                    rows.append({"start": int(parts[1]), "count": int(parts[2])})
            result[name] = {"status": "OBSERVED", "ranges": rows}
        except (OSError, ValueError):
            result[name] = {"status": "NO_VERIFICADO", "ranges": []}
    return result


def filesystem_metadata(path):
    """Observe the nearest existing ancestor without creating a store."""
    try:
        target = path
        while not target.exists() and target != target.parent:
            target = target.parent
        return {"status": "OBSERVED", "observed_path": str(target), "target_exists": path.exists(),
                "device": target.stat().st_dev, "free_bytes": shutil.disk_usage(target).free}
    except OSError:
        return {"status": "NO_VERIFICADO"}


def unit_inventory(source=REPO, *, run=subprocess.run):
    """Read the actual states of the versioned RC6 unit allowlist."""
    policy_available = True
    try:
        policy = json.loads((source / "ops/policy/host-control-plane-reconciliation-v2.json").read_text())
        units = policy["units"]
    except (OSError, ValueError, KeyError, TypeError):
        policy_available = False
        units = {}
    discovered = set()
    for arguments in (("list-unit-files", "--no-legend", "--no-pager", "porota*rc6*"),
                      ("list-units", "--all", "--plain", "--no-legend", "--no-pager", "porota*rc6*")):
        result = _call((SYSTEMCTL, *arguments), run=run)
        if result is None or result.returncode:
            return {"status": "NO_VERIFICADO", "units": []}
        discovered.update(line.split()[0] for line in result.stdout.splitlines() if line.split())
    if len(discovered) > 128 or any(not re.fullmatch(r"porota-[A-Za-z0-9_@.:-]*rc6[A-Za-z0-9_@.:-]*\.(service|timer)", name)
                                  for name in discovered):
        return {"status": "NO_VERIFICADO", "units": []}
    definitions = {Path(path).name: item for path, item in units.items()}
    rows = []
    for name in sorted(set(definitions) | discovered):
        item = definitions.get(name, {"role": "UNKNOWN"})
        if not name.startswith("porota-") or "rc6" not in name or "watch" in name and "ppi" in name:
            return {"status": "NO_VERIFICADO", "units": []}
        properties = _properties(_call((SYSTEMCTL, "show", name,
            "--property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths"), run=run),
            {"Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath", "DropInPaths"})
        rows.append({"name": name, "policy_role": item["role"], **properties})
    # Template instances require independent review before migration.
    return {"status": "OBSERVED" if policy_available else "OBSERVED_POLICY_UNAVAILABLE",
            "units": rows, "unmapped_units": sorted(discovered - set(definitions))}


def _call(command, *, run=subprocess.run, timeout=12):
    """Bound commands and discard arbitrary error text, including secrets."""
    account = pwd.getpwuid(os.getuid())
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
           "HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
           "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus"}
    try:
        value = run(tuple(command), check=False, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=timeout, env=env, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return None
    if not isinstance(value.stdout, str) or len(value.stdout) > 262144:
        return None
    return value


def _metadata(path):
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return {"present": False}
    except OSError:
        return {"present": None, "status": "NO_VERIFICADO"}
    return {"present": True, "uid": meta.st_uid, "gid": meta.st_gid,
            "mode": oct(stat.S_IMODE(meta.st_mode)), "symlink": stat.S_ISLNK(meta.st_mode),
            "socket": stat.S_ISSOCK(meta.st_mode), "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK)}


def _properties(value, allowed):
    if value is None or value.returncode != 0:
        return {"status": "NO_VERIFICADO"}
    return {key: item for line in value.stdout.splitlines()
            for key, separator, item in [line.partition("=")]
            if separator and key in allowed}


def legacy_command_identity(item):
    """Classify exact launcher pairs; never publish unknown command arguments."""
    config = item.get("Config") or {}
    command = {"entrypoint": config.get("Entrypoint"), "cmd": config.get("Cmd")}
    result = {"profile": "UNRECOGNIZED", "sha256": hashlib.sha256(
        json.dumps(command, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    # The legacy manager used: docker run --entrypoint python IMAGE SCRIPT.
    expected = {LEGACY_NAMES[0]: ("BV_PAPER_RUNTIME", "bv_paper_runtime.py"),
                LEGACY_NAMES[1]: ("DASHBOARD", "o_dashboard.py")}
    profile, script = expected.get(item.get("Name", "").lstrip("/"), (None, None))
    if profile and command == {"entrypoint": ["python"], "cmd": [script]}:
        result.update(profile=profile, entrypoint=["python"], cmd=[script])
    return result


def inspect_host(root: Path, *, run=subprocess.run):
    """Observe fixed RC6 coordinates, not all host containers or applications."""
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    home = Path(account.pw_dir)
    socket = Path(f"/run/user/{uid}/porota-rc6-docker.sock")
    sudo = _call((SUDO, "-n", "--", "/usr/bin/true"), run=run)
    legacy = []
    for name in LEGACY_NAMES:
        value = _call((SUDO, "-n", "--", DOCKER, "--host", "unix:///var/run/docker.sock",
                       "inspect", "--type", "container", name), run=run)
        row = {"name": name, "status": "NO_VERIFICADO"}
        if value is not None and value.returncode == 0:
            try:
                entries = json.loads(value.stdout)
                item = entries[0] if len(entries) == 1 else {}
                if item.get("Name") == "/" + name:
                    row.update({"status": item.get("State", {}).get("Status", "NO_VERIFICADO"),
                                "running": item.get("State", {}).get("Running"),
                                "cid": item.get("Id"), "image_id": item.get("Image"),
                                "command_identity": legacy_command_identity(item),
                                "data_mount_matches": any(
                                    m.get("Destination") == "/app/data" and
                                    m.get("Source") == str(root / "data")
                                    for m in item.get("Mounts", []))})
            except (ValueError, TypeError, KeyError, IndexError):
                pass
        legacy.append(row)
    own_info = {"status": "NO_VERIFICADO"}
    if socket.exists():
        info = _call((DOCKER, "--host", f"unix://{socket}", "info", "--format", "{{json .}}"), run=run)
        if info is not None and info.returncode == 0:
            try:
                raw = json.loads(info.stdout)
                own_info = {k: raw.get(k) for k in (
                    "ID", "SecurityOptions", "CgroupVersion", "CgroupDriver", "DockerRootDir")}
            except (ValueError, TypeError):
                pass
    user_service = _call((SYSTEMCTL, "--user", "show", SERVICE,
                          "--property=LoadState,ActiveState,SubState,FragmentPath"), run=run)
    linger = _call(("/usr/bin/loginctl", "show-user", str(uid), "--property=Linger"), run=run)
    controllers = []
    try:
        path = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/cgroup.controllers")
        controllers = sorted(set(path.read_text(encoding="ascii").split()) & {"cpu", "memory", "pids"})
    except (OSError, UnicodeError):
        pass
    files = {relative: _metadata(root / relative) for relative in (
        "data", "data/paper_v17/observer_v17.db", "data/paper_v17/observer_v17.db-wal",
        "data/paper_v17/observer_v17.db-shm", ".env", ".secrets/ppi_production.json",
        "data/diagnosticos/dashboard_preview_v1633.env")}
    units = unit_inventory(root, run=run)
    return {"schema": "porota.rootless.inspect.v1", "state": "NO_VERIFICADO",
            "operation": "inspect", "host_mutations": 0,
            "uid": uid, "gid": os.getgid(), "account": account.pw_name,
            "rootless_socket": str(socket), "socket_metadata": _metadata(socket),
            "conventional_user_socket": _metadata(Path(f"/run/user/{uid}/docker.sock")),
            "rootful_socket": _metadata(Path("/var/run/docker.sock")),
            "target_data_root": str(home / ".local/share/porota-rc6/docker"),
            "binaries": {name: shutil.which(name) is not None for name in (
                "docker", "dockerd-rootless.sh", "rootlesskit", "newuidmap", "newgidmap",
                "slirp4netns", "systemctl", "sudo")},
            "sudo_noninteractive_true": sudo is not None and sudo.returncode == 0,
            "user_service": _properties(user_service, {"LoadState", "ActiveState", "SubState", "FragmentPath"}),
            "linger": _properties(linger, {"Linger"}), "cgroup_controllers": controllers,
            "own_daemon": own_info, "legacy_rc6": legacy, "files": files,
            "legacy_units": units, "unit_details": detailed_units(units, run=run),
            "toolchain": toolchain_metadata(run=run), "subordinate_ids": subid_metadata(account),
            "filesystems": {"data": filesystem_metadata(root / "data"),
                "docker": filesystem_metadata(home / ".local/share/porota-rc6/docker"),
                "staging": filesystem_metadata(home / ".local/state/porota-rc6")}}


def checked(command, *, run=subprocess.run, timeout=30):
    from scripts.porota_rootless_host import Hold
    result = _call(command, run=run, timeout=timeout)
    if result is None or result.returncode:
        raise Hold("BOOTSTRAP_COMMAND_UNAVAILABLE")
    return result.stdout.strip()


INSTALL_UNITS = r'''import base64,hashlib,json,os,pathlib,stat,sys,tempfile
requests=json.loads(sys.argv[1]); allowed=set(json.loads(sys.argv[2])); done=[]
def checked(path,digest):
    assert path.resolve()==path
    parent=path.parent.lstat(); assert stat.S_ISDIR(parent.st_mode) and parent.st_uid==0 and not parent.st_mode&0o022
    meta=path.lstat(); assert stat.S_ISREG(meta.st_mode) and meta.st_uid==0 and meta.st_nlink==1 and not meta.st_mode&0o022 and meta.st_size<65536
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
for item in requests:
    name=item['unit']; assert name in allowed and '/' not in name
    fragment=pathlib.Path('/etc/systemd/system')/name
    checked(fragment,item['fragment_sha256'])
    for path,digest in item['dropins'].items():
        path=pathlib.Path(path); assert path.parent==pathlib.Path(str(fragment)+'.d')
        checked(path,digest)
    raw=base64.b64decode(item['content'],validate=True); assert len(raw)<65536
    if fragment.read_bytes()!=raw:
        fd,temp=tempfile.mkstemp(prefix='.porota-rootless-',dir=fragment.parent)
        try:
            os.fchmod(fd,0o644)
            with os.fdopen(fd,'wb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp,fragment)
        finally:
            if os.path.exists(temp): os.unlink(temp)
    for path,digest in item['dropins'].items(): checked(pathlib.Path(path),digest)
    done.append({'unit':name,'fragment_sha256':hashlib.sha256(raw).hexdigest(),'preserved_dropins':item['dropins']})
print(json.dumps(done))
'''


READ_INMARKET_BASELINE = r'''import base64,json,os,pathlib,re,stat,sys
try:
    path=pathlib.Path('/var/lib/porota/rc6-candle-inmarket-baseline-20260917'); assert path.resolve()==path
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        meta=os.fstat(fd)
        assert stat.S_ISREG(meta.st_mode) and meta.st_nlink==1 and meta.st_uid in (0,int(sys.argv[1])) and meta.st_size<=128
        raw=os.read(fd,129)
        assert len(raw)<=128 and re.fullmatch(rb'[0-9]{1,20}',raw.strip())
    finally: os.close(fd)
    print(json.dumps({'state':'OBSERVED','raw':base64.b64encode(raw).decode()}))
except (OSError,AssertionError):
    print(json.dumps({'state':'NO_VERIFICADO'}))
'''


def prepare_inmarket_baseline(identity, *, run=subprocess.run):
    """Copy an existing scalar exactly; absence blocks only its diagnostic job."""
    import base64
    from scripts.porota_rootless_host import atomic_write, decode
    value = _call((SUDO, "-n", "--", "/usr/bin/python3", "-c", READ_INMARKET_BASELINE, str(identity.uid)), run=run)
    if value is None or value.returncode:
        return {"state": "NO_VERIFICADO", "job": "porota-rc6-candle-inmarket-verify.service"}
    observed = decode(value.stdout)
    if observed.get("state") != "OBSERVED":
        return {"state": "NO_VERIFICADO", "job": "porota-rc6-candle-inmarket-verify.service"}
    raw = base64.b64decode(observed["raw"], validate=True)
    if len(raw) > 128 or not re.fullmatch(rb"[0-9]{1,20}", raw.strip()):
        return {"state": "NO_VERIFICADO", "job": "porota-rc6-candle-inmarket-verify.service"}
    atomic_write(identity.state / "inmarket-baseline-20260917", raw)
    return {"state": "EXACT_EXISTING_BASELINE_COPIED", "sha256": hashlib.sha256(raw).hexdigest(), "source_mutated": False}


class SchedulerPlan:
    """Preserve existing timers and host services; adapt only typed RC6 jobs."""
    def __init__(self, root, source, identity, inventory, digest, *, run=subprocess.run, guard=None, source_sha=None):
        from scripts.porota_rootless_host import require, read_regular, decode
        from scripts.porota_rootless_jobs import ADAPTED_SERVICES, PRESERVED_SERVICES, PRESERVED_COMPANIONS, INTROSPECTION
        self.root, self.source, self.identity = root, source, identity
        self.source_sha = source_sha
        self.run, self.guard, self.digest = run, guard, digest
        self.rows = inventory["units"]
        self.adapted, self.pause, self.retire = [], [], []
        self.journal = identity.state / "scheduler-transition.json"
        self.receipt = {"schema": "porota.rootless.scheduler-transition.v1", "state": "PLANNED",
                        "adapted_services": [], "preserved_host_units": [], "preserved_inert_units": [],
                        "planned_pauses": [], "paused_timers": [], "restored_timers": [], "retired_units": []}
        pending = []
        if self.journal.exists():
            previous = decode(read_regular(self.journal))
            meta = self.journal.stat()
            require(meta.st_uid == identity.uid and not meta.st_mode & 0o077 and
                    previous.get("schema") == self.receipt["schema"], "SCHEDULER_JOURNAL_UNSAFE")
            if previous.get("state") != "RESTORED":
                pending = previous.get("planned_pauses", [])
                require(isinstance(pending, list) and all(isinstance(name, str) and
                        name.endswith(".timer") and name[:-6] + ".service" in ADAPTED_SERVICES for name in pending),
                        "SCHEDULER_JOURNAL_UNSAFE")
        policy = json.loads((source / "ops/policy/host-control-plane-reconciliation-v2.json").read_text())["units"]
        by_name = {row["name"]: row for row in self.rows}
        for row in self.rows:
            name = row["name"]
            active = row.get("ActiveState") in {"active", "activating", "reloading"}
            enabled = row.get("UnitFileState") in {"enabled", "enabled-runtime"}
            service = name[:-6] + ".service" if name.endswith(".timer") else name
            if row.get("status") == "NO_VERIFICADO":
                # systemctl cannot show the uninstantiated, quarantined A3
                # template. Its three masked timers provide the actual state.
                a3 = [value for value in self.rows if value["name"].startswith("porota-a3-history-") and value["name"].endswith(".timer")]
                require(name == "porota-a3-history-rc6@.service" and len(a3) == 3 and all(
                    value.get("LoadState") == "masked" and value.get("ActiveState") == "inactive" for value in a3),
                    "LEGACY_UNIT_STATE_UNVERIFIED")
                self.receipt["preserved_inert_units"].append(name)
                continue
            if service in PRESERVED_SERVICES or service in PRESERVED_COMPANIONS:
                self.receipt["preserved_host_units"].append(name)
                continue
            if name in ADAPTED_SERVICES and row.get("LoadState") == "loaded":
                require(row.get("FragmentPath") == "/etc/systemd/system/" + name, "SCHEDULER_FRAGMENT_LOCATION")
                paths = [source / path for path in policy if Path(path).name == name]
                require(len(paths) == 1, "SCHEDULER_SOURCE_DEFINITION_REQUIRED")
                row["source_fragment"] = paths[0].read_bytes()
                # Preserve every existing drop-in byte. An ExecStart override
                # needs its own reviewed typed job before it can be replaced.
                row["dropins"] = {}
                for path in row.get("DropInPaths", "").split():
                    require(Path(path).parent == Path("/etc/systemd/system") / (name + ".d"), "SCHEDULER_DROPIN_LOCATION")
                    raw = read_regular(Path(path), 65536)
                    require(Path(path).stat().st_uid == 0 and not Path(path).stat().st_mode & 0o022,
                            "SCHEDULER_DROPIN_CUSTODY")
                    require(not any(line.strip().startswith("ExecStart=") for line in raw.decode().splitlines()),
                            "SCHEDULER_EXECSTART_OVERRIDE_NEEDS_REVIEW")
                    require(not any(line.split("=", 1)[0].strip() in {"After", "Wants", "Requires", "BindsTo", "PartOf"} and
                                    set(line.split("=", 1)[1].split()) & {"docker.service", "docker.socket"}
                                    for line in raw.decode().splitlines() if "=" in line),
                            "SCHEDULER_DROPIN_DOCKER_DEPENDENCY")
                    row["dropins"][path] = hashlib.sha256(raw).hexdigest()
                extra = _properties(_call((SYSTEMCTL, "show", name,
                    "--property=User,Group,ProtectHome,DynamicUser,After,Wants,Requires"), run=run),
                    {"User", "Group", "ProtectHome", "DynamicUser", "After", "Wants", "Requires"})
                protected = extra.get("ProtectHome", "no") in {"yes", "true", "tmpfs"}
                require(extra.get("status") != "NO_VERIFICADO" and extra.get("User", "") in {"", "root", identity.name} and
                        extra.get("DynamicUser", "no") == "no" and (not protected or name == INTROSPECTION),
                        "SCHEDULER_EXECUTION_IDENTITY_UNSUPPORTED")
                row["private_namespace"] = name == INTROSPECTION
                row["runtime_properties"] = extra
                row["fragment_sha256"] = digest(Path(row["FragmentPath"]))
                require(row["fragment_sha256"] in {hashlib.sha256(row["source_fragment"]).hexdigest(),
                        hashlib.sha256(self.fragment(row)).hexdigest()}, "SCHEDULER_FRAGMENT_DRIFT")
                self.adapted.append(row)
                continue
            if name.endswith(".timer") and service in ADAPTED_SERVICES:
                require(service in by_name and by_name[service].get("LoadState") == "loaded",
                        "SCHEDULER_SERVICE_MISSING")
                if active or name in pending:
                    self.pause.append(row)
                continue
            if row.get("policy_role", "").startswith(("RETIRED_", "QUARANTINED_")) and (active or enabled):
                fragment = Path(row.get("FragmentPath", ""))
                require(row.get("LoadState") == "loaded" and not row.get("DropInPaths") and fragment.name == name and
                        fragment.parent in {Path("/etc/systemd/system"), Path("/usr/lib/systemd/system"), Path("/lib/systemd/system")},
                        "LEGACY_UNIT_IDENTITY_UNVERIFIED")
                row["fragment_sha256"] = digest(Path(row["FragmentPath"]))
                paths = [source / path for path in policy if Path(path).name == name]
                require(len(paths) == 1 and hashlib.sha256(paths[0].read_bytes()).hexdigest() == row["fragment_sha256"],
                        "LEGACY_UNIT_SOURCE_DRIFT")
                self.retire.append(row)
                continue
            if active or enabled:
                from scripts.porota_rootless_host import Hold
                failure = Hold("LEGACY_FUNCTIONAL_UNIT_NEEDS_ROOTLESS_SUCCESSOR")
                failure.public_details = {"functional_units_needing_successor": [name]}
                raise failure
            self.receipt["preserved_inert_units"].append(name)
        require(set(pending) <= {row["name"] for row in self.pause}, "SCHEDULER_PENDING_TIMER_MISSING")
        self.receipt["planned_pauses"] = [row["name"] for row in self.pause]

    def command(self, *args, timeout=330):
        if self.guard:
            self.guard()
        return checked((SUDO, "-n", "--", SYSTEMCTL, *args), run=self.run, timeout=timeout)

    def fragment(self, row):
        from scripts.porota_rootless_host import require
        identity, unit = self.identity, row["name"]
        require(all(re.fullmatch(r"[A-Za-z0-9_./-]+", value) for value in (identity.name, str(identity.home))),
                "SCHEDULER_IDENTITY_PATH_UNSUPPORTED")
        env = f"/usr/bin/env -i HOME={identity.home} USER={identity.name} LOGNAME={identity.name} PATH=/usr/bin:/bin:/usr/sbin:/sbin POROTA_RUNTIME_BACKEND=ROOTLESS PYTHONDONTWRITEBYTECODE=1"
        command = f"{env} /usr/bin/python3 -B {identity.state}/scheduler-dispatch.py --unit {unit}"
        if row["runtime_properties"].get("User", "") in {"", "root"}:
            command = f"/usr/sbin/runuser -u {identity.name} -- " + command
        # Dependencies cannot be removed by resetting a drop-in. Change the
        # proven base fragment and preserve every existing drop-in verbatim.
        lines, section, starts = [], "", 0
        for line in row["source_fragment"].decode().splitlines():
            if line.startswith("["):
                section = line.strip()
            key, separator, value = line.partition("=")
            if section == "[Unit]" and separator and key in {"After", "Wants", "Requires", "BindsTo", "PartOf"}:
                value = " ".join(item for item in value.split() if item not in {"docker.service", "docker.socket"})
                line = key + "=" + value
            if section == "[Service]" and key == "ExecStart" and separator:
                line, starts = "ExecStart=" + command, starts + 1
            if section == "[Service]" and key == "ProtectHome" and row.get("private_namespace"):
                # ProtectHome=yes forbids nested binds. tmpfs keeps all other
                # homes hidden and permits only these precise read-only views.
                line = "\n".join(key + "=" + value for key, value in self.namespace_properties().items())
            lines.append(line)
        require(starts == 1, "SCHEDULER_SOURCE_COMMAND_UNSUPPORTED")
        return ("\n".join(lines) + "\n").encode()

    def namespace_properties(self):
        identity = self.identity
        mode = f":ro,uid={identity.uid},gid={identity.gid},mode=0700"
        return {"ProtectHome": "tmpfs", "TemporaryFileSystem": str(identity.home) + mode + " " + str(identity.socket.parent) + mode,
                "BindReadOnlyPaths": " ".join(map(str, (identity.state, identity.socket,
                    identity.home / ".config/porota-rc6/client")))}

    def check_namespace(self):
        from scripts.porota_rootless_host import require, decode, SHA, private_dir
        if not any(row.get("private_namespace") for row in self.adapted):
            return
        require(SHA.fullmatch(str(self.source_sha)), "SCHEDULER_SOURCE_SHA_REQUIRED")
        identity = self.identity
        private_dir(identity.home / ".config/porota-rc6/client")
        source = identity.state / "releases" / self.source_sha / "source"
        row = next(row for row in self.adapted if row.get("private_namespace"))
        root_launcher = row["runtime_properties"].get("User", "") in {"", "root"}
        properties = {**self.namespace_properties(), "User": "root" if root_launcher else identity.name,
                      "Group": row["runtime_properties"].get("Group") or ("root" if root_launcher else str(identity.gid)),
                      "NoNewPrivileges": "yes", "ProtectSystem": "strict", "PrivateTmp": "yes",
                      "RuntimeMaxSec": "25", "TimeoutStopSec": "5"}
        command = [SUDO, "-n", "--", "/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect",
                   "--unit=porota-rc6-access-probe-" + os.urandom(6).hex(), "--service-type=exec"]
        for key, value in properties.items():
            command.extend(("--property", key + "=" + value))
        code = ("import json; from pathlib import Path; from scripts.porota_rootless_host import Identity,Host,read_regular; "
                "i=Identity.current(); read_regular(i.state/'deploy.lock'); "
                "Host(Path('/opt/porota-trading'),i).boundary(); print(json.dumps({'uid':i.uid,'rootless':True}))")
        if root_launcher:
            command.extend(("/usr/sbin/runuser", "-u", identity.name, "--"))
        command.extend(("/usr/bin/env", "-i", "HOME=" + str(identity.home), "USER=" + identity.name,
            "PATH=/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONPATH=" + str(source), "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3", "-B", "-c", code))
        if self.guard:
            self.guard()
        result = decode(checked(command, run=self.run, timeout=40))
        require(result == {"uid": identity.uid, "rootless": True}, "SCHEDULER_PRIVATE_NAMESPACE_UNVERIFIED")
        self.receipt["private_namespace"] = {"state": "PROBED", "uid": identity.uid,
            "other_homes_visible": False, "binds": self.namespace_properties()["BindReadOnlyPaths"].split()}

    def save(self):
        from scripts.porota_rootless_host import write_json
        write_json(self.journal, self.receipt)

    def quiesce(self):
        import base64
        from scripts.porota_rootless_host import atomic_write, private_dir, require, decode
        from scripts.porota_rootless_jobs import LAUNCHER, ADAPTED_SERVICES, INMARKET
        if self.adapted:
            private_dir(self.identity.state)
            launcher = self.identity.state / "scheduler-dispatch.py"
            atomic_write(launcher, LAUNCHER.encode())
            private_dir(self.identity.state / "bin")
            shim = self.identity.state / "bin/docker"
            atomic_write(shim, ("#!/bin/sh\nexec /usr/bin/python3 -B " + str(launcher) + " --docker-bridge \"$@\"\n").encode())
            shim.chmod(0o700)
            if any(row["name"] == INMARKET for row in self.adapted):
                self.receipt["inmarket_baseline"] = prepare_inmarket_baseline(self.identity, run=self.run)
        self.check_namespace()
        self.receipt["state"] = "QUIESCING"
        self.save()  # Persist intended restorations before the first stop.
        for row in self.pause:
            self.command("stop", row["name"])
            self.receipt["paused_timers"].append(row["name"])
            self.save()
        for row in self.adapted:
            # Stop the client service; the following exact legacy-container
            # stop terminates any exec process that outlived its Docker client.
            self.command("stop", row["name"])
        requests = [{"unit": row["name"], "fragment_sha256": row["fragment_sha256"],
                     "dropins": row["dropins"], "content": base64.b64encode(self.fragment(row)).decode()}
                    for row in self.adapted]
        if requests:
            if self.guard:
                self.guard()
            result = checked((SUDO, "-n", "--", "/usr/bin/python3", "-c", INSTALL_UNITS,
                              json.dumps(requests), json.dumps(sorted(ADAPTED_SERVICES))), run=self.run)
            self.receipt["adapted_services"] = decode(result)
            self.save()
            self.command("daemon-reload")
            self.verify_installed()
        for row in sorted(self.retire, key=lambda value: not value["name"].endswith(".timer")):
            require(self.digest(Path(row["FragmentPath"])) == row["fragment_sha256"], "LEGACY_UNIT_CHANGED")
            self.command("disable", "--now", row["name"])
            self.receipt["retired_units"].append(row["name"])
        self.receipt["state"] = "QUIESCENT"
        self.save()
        return self.receipt

    def verify_installed(self):
        from scripts.porota_rootless_host import require
        for row in self.adapted:
            require(self.digest(Path(row["FragmentPath"])) == hashlib.sha256(self.fragment(row)).hexdigest(),
                    "SCHEDULER_INSTALLED_FRAGMENT_MISMATCH")
            for path, digest in row["dropins"].items():
                require(self.digest(Path(path)) == digest, "SCHEDULER_DROPIN_CHANGED")
            observed = _properties(_call((SYSTEMCTL, "show", row["name"],
                "--property=ExecStart,Requires,Wants,BindsTo,PartOf,DropInPaths,ProtectHome"), run=self.run),
                {"ExecStart", "Requires", "Wants", "BindsTo", "PartOf", "DropInPaths", "ProtectHome"})
            require(str(self.identity.state / "scheduler-dispatch.py") in observed.get("ExecStart", "") and
                    ("--unit " + row["name"]) in observed.get("ExecStart", "") and not any(
                    set(observed.get(key, "").split()) & {"docker.service", "docker.socket"}
                    for key in ("Requires", "Wants", "BindsTo", "PartOf")) and
                    set(observed.get("DropInPaths", "").split()) == set(row["dropins"]),
                    "SCHEDULER_EFFECTIVE_COMMAND_UNVERIFIED")
            require(not row.get("private_namespace") or observed.get("ProtectHome") == "tmpfs",
                    "SCHEDULER_PRIVATE_NAMESPACE_UNVERIFIED")

    def restore(self):
        from scripts.porota_rootless_host import require
        self.verify_installed()
        for name in self.receipt["paused_timers"]:
            self.command("start", name)
            status = _properties(_call((SYSTEMCTL, "show", name, "--property=ActiveState,UnitFileState"), run=self.run),
                                 {"ActiveState", "UnitFileState"})
            require(status.get("ActiveState") == "active", "SCHEDULER_TIMER_RESTORE_UNVERIFIED")
            self.receipt["restored_timers"].append(name)
            self.save()
        self.receipt["state"] = "RESTORED"
        self.receipt["functional_execution"] = "NO_VERIFICADO_UNTIL_SCHEDULED_RUN"
        self.save()
        return self.receipt


def subids_available(path, identity):
    from scripts.porota_rootless_host import read_regular
    import grp
    records = []
    for line in read_regular(path, 1048576).decode("ascii").splitlines():
        parts = line.split(":")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            records.append((parts[0], int(parts[1]), int(parts[2])))
    selected = [row for row in records if row[0] in (identity.name, str(identity.uid))]
    protected = ([item.pw_uid for item in pwd.getpwall()] if path.name == "subuid" else
                 [item.gr_gid for item in grp.getgrall()])
    return bool(selected) and all(start >= 65536 and count >= 65536 and start + count <= 2**32 and
        all(not start <= account < start + count for account in protected) and
        all(other is row or start + count <= other[1] or other[1] + other[2] <= start
            for other in records)
        for row in selected for _, start, count in [row])


def network_install_plan(output):
    """Accept only new, authenticated distro network prerequisites, not upgrades."""
    from scripts.porota_rootless_host import require
    packages = {}
    configured = set()
    for line in output.splitlines():
        if line.startswith("Inst "):
            match = re.fullmatch(r"Inst (slirp4netns|libslirp0)(?::[a-z0-9]+)? \(([0-9][A-Za-z0-9.+:~_-]*) [^\r\n]+\)", line)
            require(match is not None and match[1] not in packages, "ROOTLESS_NETWORK_INSTALL_SCOPE")
            packages[match[1]] = match[2]
        elif line.startswith("Conf "):
            configured.add(line.split()[1].split(":")[0])
        elif line.startswith(("Remv ", "Purg ")):
            require(False, "ROOTLESS_NETWORK_INSTALL_SCOPE")
    summary = re.search(r"(\d+) upgraded, (\d+) newly installed, (\d+) to remove", output)
    require(summary is not None and summary[1] == summary[3] == "0" and
            int(summary[2]) == len(packages) and "slirp4netns" in packages and configured <= set(packages),
            "ROOTLESS_NETWORK_INSTALL_SCOPE")
    return packages


def ensure_network(receipt, *, run=subprocess.run):
    from scripts.porota_rootless_host import require
    candidates = {name: shutil.which(name, path="/usr/bin:/bin:/usr/sbin:/sbin")
                  for name in ("slirp4netns", "pasta", "vpnkit")}
    receipt.update({"available_before": {name: bool(path) for name, path in candidates.items()},
                    "installed_packages": {}, "daemon_restarts_requested": 0})
    if any(candidates.values()):
        receipt.update(driver=next(name for name, path in candidates.items() if path), source="EXISTING_BINARY")
        return receipt["driver"]
    simulate = ("/usr/bin/apt-get", "--simulate", "--no-install-recommends", "install")
    observed = _call((*simulate, "slirp4netns"), run=run, timeout=60)
    require(observed is not None and observed.returncode == 0, "ROOTLESS_NETWORK_PACKAGE_PLAN_UNAVAILABLE")
    packages = network_install_plan(observed.stdout)
    receipt["planned_packages"] = packages
    pinned = tuple(name + "=" + version for name, version in sorted(packages.items()))
    repeated = _call((*simulate, *pinned), run=run, timeout=60)
    require(repeated is not None and repeated.returncode == 0 and
            network_install_plan(repeated.stdout) == packages, "ROOTLESS_NETWORK_PACKAGE_PLAN_CHANGED")
    # These libraries install no daemon. Keep needrestart in listing mode and
    # forbid removals; never request upgrade/update or touch Docker packages.
    command = (SUDO, "-n", "--", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_MODE=l",
        "/usr/bin/apt-get", "--yes", "--no-remove", "--no-install-recommends",
        "-o", "APT::Get::AllowUnauthenticated=false", "install", *pinned)
    receipt["installation_requested"] = True
    installed = _call(command, run=run, timeout=180)
    require(installed is not None and installed.returncode == 0, "ROOTLESS_NETWORK_PACKAGE_INSTALL_FAILED")
    observed = _call(("/usr/bin/dpkg-query", "--show", "--showformat=${Package}\t${Version}\t${db:Status-Status}\n",
                      *sorted(packages)), run=run)
    require(observed is not None and observed.returncode == 0, "ROOTLESS_NETWORK_PACKAGE_VERIFY_FAILED")
    actual = {parts[0]: parts[1] for line in observed.stdout.splitlines() for parts in [line.split("\t")]
              if len(parts) == 3 and parts[2] == "installed"}
    require(actual == packages and shutil.which("slirp4netns", path="/usr/bin:/bin:/usr/sbin:/sbin") is not None,
            "ROOTLESS_NETWORK_PACKAGE_VERIFY_FAILED")
    receipt.update(driver="slirp4netns", source="AUTHENTICATED_DISTRIBUTION_PACKAGES", installed_packages=actual)
    return "slirp4netns"


@contextmanager
def preparation_failure(identity, receipt):
    try:
        yield
    except Exception as exc:
        from scripts.porota_rootless_host import write_json
        exc.public_details = {**getattr(exc, "public_details", {}), "preparation": receipt}
        try:
            write_json(identity.state / "prepare.json", receipt)
        except Exception:
            pass
        raise


def daemon_configuration(identity, launcher, network="slirp4netns"):
    from scripts.porota_rootless_host import require
    # systemd unit syntax is not shell syntax; accept ordinary installed paths only.
    require(all(value and all(c.isalnum() or c in "/._-" for c in value)
                for value in (str(identity.home), identity.name, launcher)), "BOOTSTRAP_PATH_UNSUPPORTED")
    require(network in {"slirp4netns", "pasta", "vpnkit"}, "ROOTLESS_NETWORK_BACKEND_UNSUPPORTED")
    runtime = f"/run/user/{identity.uid}/porota-rc6"
    config_path = identity.home / ".config/porota-rc6/daemon.json"
    config = {"hosts": [f"unix://{identity.socket}"], "data-root": str(identity.store),
              "exec-root": runtime + "/exec", "pidfile": runtime + "/dockerd.pid",
              "exec-opts": ["native.cgroupdriver=systemd"], "live-restore": False,
              "features": {"containerd-snapshotter": False}, "storage-driver": "overlay2"}
    unit = f'''[Unit]
Description=Porota RC6 dedicated rootless Docker
After=network-online.target
Requires=dbus.socket

[Service]
Type=notify
NotifyAccess=all
Environment=HOME={identity.home}
Environment=PATH=/usr/bin:/bin:/usr/sbin:/sbin
Environment=XDG_RUNTIME_DIR={runtime}
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{identity.uid}/bus
Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_NET={network}
RuntimeDirectory=porota-rc6
RuntimeDirectoryMode=0700
ExecStart={launcher} --config-file={config_path}
Delegate=yes
KillMode=mixed
Restart=on-failure
RestartSec=3
TimeoutStartSec=90
TimeoutStopSec=120
LimitNOFILE=infinity
LimitNPROC=infinity
TasksMax=infinity

[Install]
WantedBy=default.target
'''
    return config_path, (json.dumps(config, sort_keys=True) + "\n").encode(), unit.encode()


def clear_unstarted_images(identity, docker, receipt):
    """Remove only images bound to a private, pre-transition failed release."""
    from scripts.porota_rootless_host import (require, read_regular, no_alias, decode,
        SHA, IMAGE_ID, ATTEMPT, parse_env, remove_unreferenced_image, write_json)
    change = receipt["storage_transition"]
    change["provenance_stage"] = "LIST"
    require(not os.path.lexists(identity.state / "current.json") and
            not docker.call("container", "ls", "--all", "--quiet", "--no-trunc"),
            "ROOTLESS_STORE_TRANSITION_NOT_EMPTY")
    ids = set(docker.call("image", "ls", "--all", "--quiet", "--no-trunc").splitlines())
    require(all(IMAGE_ID.fullmatch(value) for value in ids), "ROOTLESS_STORE_IMAGE_PROVENANCE")
    planned = []
    for image_id in sorted(ids):
        change["provenance_stage"] = "IMAGE_ID"
        rows = docker.json("image", "inspect", image_id)
        require(len(rows) == 1 and rows[0].get("Id") == image_id, "ROOTLESS_STORE_IMAGE_PROVENANCE")
        config = rows[0].get("Config") or {}
        change["provenance_stage"] = "IMAGE_LABELS"
        env, labels = parse_env("\n".join(config.get("Env", []))), config.get("Labels") or {}
        sha, tree = env.get("POROTA_BUILD_SHA", ""), env.get("POROTA_CANDIDATE_TREE_SHA", "")
        require(SHA.fullmatch(sha) and SHA.fullmatch(tree) and labels.get("porota.commit") == sha and
                labels.get("porota.predeploy") == "v2", "ROOTLESS_STORE_IMAGE_PROVENANCE")
        path = identity.state / "releases" / sha / "release.json"
        change["provenance_stage"] = "RELEASE_CUSTODY"
        for directory in (identity.state, path.parent.parent, path.parent):
            meta = no_alias(directory).stat()
            require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == identity.uid and not meta.st_mode & 0o022,
                    "ROOTLESS_STORE_IMAGE_PROVENANCE")
        raw = read_regular(path, 2 * 1024**2)
        require(path.stat().st_uid == identity.uid and not path.stat().st_mode & 0o077,
                "ROOTLESS_STORE_IMAGE_PROVENANCE")
        change["provenance_stage"] = "RELEASE_IDENTITY"
        release = decode(raw)
        require(release.get("schema") == "porota.release.v1" and release.get("source_sha") == sha and
                release.get("source_tree") == tree and IMAGE_ID.fullmatch(str(release.get("image_id", ""))) and
                release.get("operating_mode") == "PRODUCTION_PAPER" and release.get("execution_mode") == "SIMULATION" and
                release.get("real_order_capability") == "BLOCKED" and
                type(release.get("real_orders_sent_required")) is int and release["real_orders_sent_required"] == 0,
                "ROOTLESS_STORE_IMAGE_PROVENANCE")
        release_hash = hashlib.sha256(raw).hexdigest()
        proven = False
        change["provenance_stage"] = "JOURNAL_CUSTODY"
        for journal in (identity.state / "attempts").glob("*/receipt.json"):
            if not ATTEMPT.fullmatch(journal.parent.name):
                continue
            change["provenance_stage"] = "JOURNAL_CUSTODY"
            for entry, mask in ((journal.parent.parent, 0o022), (journal.parent, 0o077), (journal, 0o077)):
                meta = no_alias(entry).stat()
                require(meta.st_uid == identity.uid and not meta.st_mode & mask,
                        "ROOTLESS_STORE_IMAGE_PROVENANCE")
            journal_raw = read_regular(journal, 2 * 1024**2)
            change["provenance_stage"] = "JOURNAL_MATCH"
            attempt = decode(journal_raw)
            if (attempt.get("schema") == "porota.rootless.deploy.v1" and
                attempt.get("state") == "NO_VERIFICADO" and attempt.get("roles") == {} and
                attempt.get("transition_started") is False and
                all(attempt.get(key) == release[key] for key in ("source_sha", "source_tree", "image_id")) and
                attempt.get("release_manifest_sha256") == release_hash):
                proven = True
                break
        change["provenance_stage"] = "JOURNAL_MATCH"
        require(proven, "ROOTLESS_STORE_IMAGE_PROVENANCE")
        planned.append({"image_id": image_id, "source_sha": sha, "source_tree": tree,
                        "config_image_id": release["image_id"], "release_manifest_sha256": release_hash})
    change.update(state="CLEANUP", images=planned, removed_images=[],
                  free_bytes_before=shutil.disk_usage(identity.store).free)
    write_json(identity.state / "prepare.json", receipt)
    for item in planned:
        require(remove_unreferenced_image(docker, item["image_id"]) == "REMOVED_UNREFERENCED",
                "ROOTLESS_STORE_IMAGE_REFERENCED")
        change["removed_images"].append(item["image_id"])
        write_json(identity.state / "prepare.json", receipt)
    require(not docker.call("image", "ls", "--all", "--quiet", "--no-trunc") and
            not docker.call("container", "ls", "--all", "--quiet", "--no-trunc"),
            "ROOTLESS_STORE_TRANSITION_NOT_EMPTY")
    change.update(state="IMAGES_CLEARED", free_bytes_after=shutil.disk_usage(identity.store).free)
    change["reclaimed_bytes"] = max(0, change["free_bytes_after"] - change["free_bytes_before"])
    write_json(identity.state / "prepare.json", receipt)


def prepare(identity=None, *, run=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    from scripts.porota_rootless_host import (Identity, Host, Docker, require, Hold,
        no_alias, private_dir, private_attempts_dir, read_regular, atomic_write, user_lock, write_json)
    identity = identity or Identity.current()
    private_dir(identity.state)
    receipt = {"schema": "porota.rootless.prepare.v1", "state": "NO_VERIFICADO",
               "uid": identity.uid, "socket": str(identity.socket), "service": SERVICE,
               "ppi_watch_touched": False, "boca_touched": False}
    with preparation_failure(identity, receipt), user_lock(identity.state / "deploy.lock"):
        required = ("docker", "dockerd-rootless.sh", "rootlesskit", "newuidmap", "newgidmap", "systemctl")
        binaries = {name: shutil.which(name, path="/usr/bin:/bin:/usr/sbin:/sbin") for name in required}
        missing = [name for name, path in binaries.items() if not path]
        receipt["missing_prerequisites"] = missing
        write_json(identity.state / "prepare.json", receipt)
        require(not missing, "ROOTLESS_PACKAGES_REQUIRED")
        require(all(subids_available(Path(path), identity) for path in ("/etc/subuid", "/etc/subgid")),
                "ROOTLESS_SUBIDS_REQUIRED")
        controllers = Path(f"/sys/fs/cgroup/user.slice/user-{identity.uid}.slice/user@{identity.uid}.service/cgroup.controllers")
        require({"cpu", "memory", "pids"} <= set(read_regular(controllers).decode().split()),
                "ROOTLESS_CGROUP_DELEGATION_REQUIRED")
        checked((SYSTEMCTL, "--user", "show-environment"), run=run)
        version = Docker(identity, run=run).call("compose", "version", "--short")
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.\d+(?:[-+].*)?", version)
        require(match is not None and tuple(map(int, match.groups())) >= (2, 30),
                "COMPOSE_RAW_ENV_SUPPORT_REQUIRED")
        receipt["network"] = {}
        network = ensure_network(receipt["network"], run=run)
        linger = checked(("/usr/bin/loginctl", "show-user", str(identity.uid), "--property=Linger", "--value"), run=run)
        if linger != "yes":
            checked((SUDO, "-n", "--", "/usr/bin/loginctl", "enable-linger", identity.name), run=run)
        require(checked(("/usr/bin/loginctl", "show-user", str(identity.uid), "--property=Linger", "--value"), run=run) == "yes",
                "USER_LINGER_REQUIRED")
        config_path, config, unit = daemon_configuration(identity, binaries["dockerd-rootless.sh"], network)
        private_dir(config_path.parent)
        store = no_alias(identity.store)
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
        meta = store.stat()
        # Moby setupDaemonRoot sets its data-root to 0710 after bootstrap.
        # Validate that contract without chmod or relaxing private state/config.
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == identity.uid and
                stat.S_IMODE(meta.st_mode) in (0o700, 0o710), "ROOTLESS_STORE_DIRECTORY_REQUIRED")
        unit_path = no_alias(identity.home / ".config/systemd/user" / SERVICE)
        unit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(unit_path.parent.stat().st_uid == identity.uid and not unit_path.parent.stat().st_mode & 0o022,
                "USER_UNIT_DIRECTORY_UNSAFE")
        existing = unit_path.exists()
        previous = json.loads(config)
        previous.pop("features")
        previous.pop("storage-driver")
        previous = (json.dumps(previous, sort_keys=True) + "\n").encode()
        raw_config = read_regular(config_path) if config_path.exists() else None
        require(raw_config is None or raw_config in (config, previous) and
                config_path.stat().st_uid == identity.uid and not config_path.stat().st_mode & 0o077,
                "ROOTLESS_SETUP_DRIFT")
        require(not existing or read_regular(unit_path) == unit and unit_path.stat().st_uid == identity.uid and
                not unit_path.stat().st_mode & 0o022, "ROOTLESS_SETUP_DRIFT")
        require(not existing or raw_config is not None, "ROOTLESS_SETUP_DRIFT")
        require(raw_config != previous or existing, "ROOTLESS_SETUP_DRIFT")
        manager = Host(Path("/opt/porota-trading"), identity, Docker(identity, run=run))
        restart = False
        if existing:
            properties = checked((SYSTEMCTL, "--user", "show", SERVICE,
                "--property=FragmentPath,DropInPaths"), run=run)
            observed = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
            require(observed.get("FragmentPath") == str(unit_path) and observed.get("DropInPaths") == "",
                    "ROOTLESS_SETUP_DRIFT")
            if raw_config == config:
                # A failed restart may leave the known new configuration with
                # its daemon stopped. Starting an already active unit is a no-op.
                checked((SYSTEMCTL, "--user", "enable", "--now", SERVICE), run=run, timeout=100)
            # The only pre-upgrade exception is the storage backend; socket,
            # rootless UID, private store and effective cgroups remain required.
            daemon_id = manager.boundary(classic_store=False)
            info = manager.docker.json("info", "--format", "{{json .}}")
            require(info.get("ID") == daemon_id, "ROOTLESS_DAEMON_CHANGED")
            classic = info.get("Driver") == "overlay2" and isinstance(info.get("DriverStatus"), list) and \
                ["driver-type", "io.containerd.snapshotter.v1"] not in info["DriverStatus"]
            if raw_config != config or not classic:
                require(classic or info.get("Driver") == "overlayfs" and isinstance(info.get("DriverStatus"), list) and
                        ["driver-type", "io.containerd.snapshotter.v1"] in info.get("DriverStatus", []),
                        "ROOTLESS_CLASSIC_IMAGE_STORE_REQUIRED")
                change = receipt["storage_transition"] = {"state": "NO_VERIFICADO", "daemon_id": daemon_id,
                    "driver_before": info.get("Driver"), "driver_after": "NO_VERIFICADO", "restart_requested": False}
                change["provenance_stage"] = "JOURNAL_CUSTODY"
                private_attempts_dir(identity)
                clear_unstarted_images(identity, manager.docker, receipt)
                require(manager.boundary(classic_store=False) == daemon_id, "ROOTLESS_DAEMON_CHANGED")
                restart = True
        if raw_config != config:
            atomic_write(config_path, config)
        if not existing:
            atomic_write(unit_path, unit, private=False)
        checked((SYSTEMCTL, "--user", "daemon-reload"), run=run)
        if restart:
            receipt["storage_transition"]["restart_requested"] = True
            write_json(identity.state / "prepare.json", receipt)
            checked((SYSTEMCTL, "--user", "restart", SERVICE), run=run, timeout=130)
        checked((SYSTEMCTL, "--user", "enable", "--now", SERVICE), run=run, timeout=100)
        try:
            deadline = clock() + 45
            while True:
                try:
                    daemon_id = manager.boundary()
                    break
                except (Hold, OSError) as exc:
                    if isinstance(exc, Hold) and str(exc) == "ROOTLESS_CLASSIC_IMAGE_STORE_REQUIRED":
                        raise
                    require(clock() < deadline, "ROOTLESS_DAEMON_START_TIMEOUT")
                    sleep(2)
        except Exception:
            if not existing:
                _call((SYSTEMCTL, "--user", "stop", SERVICE), run=run, timeout=130)
            raise
        receipt.update({"state": "PREPARADO", "daemon_id": daemon_id,
                        "runtime_validation": "NO_VERIFICADO",
                        "image_store": {"driver": "overlay2", "containerd_snapshotter": False}})
        if restart:
            receipt["storage_transition"].update(state="COMPLETE", driver_after="overlay2")
        write_json(identity.state / "prepare.json", receipt)
        return receipt


ROOT_LOCK = r'''import fcntl,os,stat,sys
p='/run/lock/porota-rc6-deploy-v2.lock'
fd=os.open(p,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
s=os.fstat(fd)
assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert not os.path.lexists('/run/lock/porota-rc6-deploy-v2-attempt.json')
print('LOCKED',flush=True)
sys.stdin.buffer.read(1)
'''


@contextmanager
def legacy_lock(identity):
    from scripts.porota_rootless_host import require
    import selectors
    process = subprocess.Popen((SUDO, "-n", "--", "/usr/bin/python3", "-c", ROOT_LOCK),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=identity.environment(), cwd="/")
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            require(bool(selector.select(15)), "LEGACY_LOCK_UNAVAILABLE")
        require(process.stdout.readline(32) == b"LOCKED\n" and process.poll() is None,
                "LEGACY_OWNER_BUSY_OR_UNAVAILABLE")
        yield process
        require(process.poll() is None, "LEGACY_LOCK_LOST")
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


class LegacyTransition:
    """No arbitrary receipt can substitute for observed legacy quiescence."""
    def __init__(self, root, source, *, identity=None, run=subprocess.run, guard=None, source_sha=None):
        from scripts.porota_rootless_host import require, decode, Identity
        require(root == Path("/opt/porota-trading"), "LEGACY_ROOT_NOT_AUTHORIZED")
        self.root, self.source, self.run = root, source, run
        self.guard = guard
        self.receipt = {"schema": "porota.rootless.legacy-quiescence.v1", "state": "NO_VERIFICADO",
                        "containers": [], "observed_containers": [], "units": [],
                        "functional_successors": "NO_VERIFICADO"}
        self.daemon_id = decode(self.docker("info", "--format", "{{json .}}"))["ID"]
        # The exact-name filters expose no unrelated applications.
        ids = self.docker("container", "ls", "--all", "--quiet", "--no-trunc", "--filter",
                          "name=^/(porota_production_observer|porota_production_dashboard|porota_trading_bot)$").splitlines()
        self.containers = []
        for cid in ids:
            item = self.inspect(cid)
            command = self.validate(item)
            self.containers.append(item)
            self.receipt["observed_containers"].append({"cid": cid, "name": item["Name"].lstrip("/"),
                "image_id": item["Image"], "command_identity": command})
        require(len({item["Name"] for item in self.containers}) == len(self.containers), "LEGACY_CONTAINER_AMBIGUOUS")
        inventory = unit_inventory(source, run=run)
        require(inventory["status"] == "OBSERVED", "LEGACY_UNIT_INVENTORY_UNVERIFIED")
        self.units = inventory["units"]
        self.scheduler = SchedulerPlan(root, source, identity or Identity.current(), inventory,
                                       self.unit_digest, run=run, guard=guard, source_sha=source_sha)
        self.receipt["scheduler"] = self.scheduler.receipt

    def docker(self, *args):
        if self.guard:
            self.guard()
        return checked((SUDO, "-n", "--", DOCKER, "--host", "unix:///var/run/docker.sock", *args), run=self.run)

    def inspect(self, cid):
        from scripts.porota_rootless_host import CID, require, decode
        require(CID.fullmatch(cid), "LEGACY_CID_INVALID")
        rows = decode(self.docker("container", "inspect", cid))
        require(len(rows) == 1 and rows[0].get("Id") == cid, "LEGACY_CONTAINER_IDENTITY")
        return rows[0]

    def validate(self, item):
        from scripts.porota_rootless_host import require, IMAGE_ID, Hold
        name = item.get("Name", "").lstrip("/")
        require(name in LEGACY_NAMES and IMAGE_ID.fullmatch(str(item.get("Image", ""))), "LEGACY_CONTAINER_IDENTITY")
        require(any(m.get("Type") == "bind" and m.get("Destination") == "/app/data" and
                    m.get("Source") == str(self.root / "data") for m in item.get("Mounts", [])), "LEGACY_DATA_IDENTITY")
        command = legacy_command_identity(item)
        if name == LEGACY_NAMES[2]:
            require(item.get("State", {}).get("Running") is False, "LEGACY_MONOLITH_NEEDS_REVIEW")
        elif command["profile"] == "UNRECOGNIZED":
            failure = Hold("LEGACY_COMMAND_IDENTITY")
            failure.public_details = {"legacy_command_identity": {
                "name": name, "cid": item["Id"], "command_identity": command}}
            raise failure
        return command

    def unit_digest(self, path):
        from scripts.porota_rootless_host import require
        code = "import hashlib,os,stat,sys; p=sys.argv[1]; f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW); s=os.fstat(f); assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<65536; print(hashlib.sha256(os.read(f,65536)).hexdigest())"
        value = checked((SUDO, "-n", "--", "/usr/bin/python3", "-c", code, str(path)), run=self.run)
        require(len(value) == 64 and all(c in "0123456789abcdef" for c in value), "LEGACY_UNIT_IDENTITY_UNVERIFIED")
        return value

    def quiesce(self):
        from scripts.porota_rootless_host import require, decode, database_snapshot, DB_RELATIVE
        before = database_snapshot(self.root / DB_RELATIVE)
        for item in self.containers:
            observed = self.inspect(item["Id"])
            self.validate(observed)
            require(observed["Name"] == item["Name"] and observed["Image"] == item["Image"], "LEGACY_CONTAINER_CHANGED")
        self.scheduler.quiesce()
        self.receipt["units"] = self.scheduler.receipt["retired_units"]
        for item in self.containers:
            self.docker("container", "update", "--restart=no", item["Id"])
            if self.inspect(item["Id"])["State"].get("Running"):
                self.docker("container", "stop", "--time", "30", item["Id"])
            final = self.inspect(item["Id"])
            command = self.validate(final)
            require(final["State"].get("Running") is False and
                    final.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") == "no", "LEGACY_STOP_UNVERIFIED")
            self.receipt["containers"].append({"cid": item["Id"], "image_id": item["Image"],
                "state": "STOPPED_RETAINED", "command_identity": command})
        require(decode(self.docker("info", "--format", "{{json .}}"))["ID"] == self.daemon_id, "LEGACY_DAEMON_CHANGED")
        require(database_snapshot(self.root / DB_RELATIVE) == before, "DATABASE_CHANGED_IDENTITY")
        after = unit_inventory(self.source, run=self.run)
        stopped = {row["name"] for row in self.scheduler.adapted + self.scheduler.pause + self.scheduler.retire}
        retired = {row["name"] for row in self.scheduler.retire}
        observed = {row["name"]: row for row in after["units"]}
        require(after["status"] == "OBSERVED" and stopped <= set(observed) and all(
            observed[name].get("ActiveState") in {"inactive", "failed"} and (name not in retired or
            observed[name].get("UnitFileState") not in {"enabled", "enabled-runtime"}) for name in stopped),
            "LEGACY_UNIT_NOT_QUIESCENT")
        self.receipt.update({"state": "QUIESCENT", "database": before, "rootful_daemon_stopped": False,
                             "functional_successors": "ADAPTED_AND_HOST_SERVICES_PRESERVED"})
        return self.receipt


PREPARE_ACCESS = r'''import json,os,stat,sys
uid=int(sys.argv[1]); gid=int(sys.argv[2])
assert uid>0 and int(os.environ['SUDO_UID'])==uid
base=os.open('/opt/porota-trading',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
specs=[('.env',False),('.secrets',True),('.secrets/ppi_production.json',False),
       ('data',True),('data/diagnosticos',True),('data/paper_v17',True),
       ('data/diagnosticos/dashboard_preview_v1633.env',False),
       ('data/paper_v17/observer_v17.db',False),('data/deploy',True)]
for name in ('data/paper_v17/observer_v17.db-wal','data/paper_v17/observer_v17.db-shm'):
    if os.path.lexists('/opt/porota-trading/'+name): specs.append((name,False))
opened=[]
try:
    for relative,directory in specs:
        parent=os.dup(base)
        try:
            parts=relative.split('/')
            for part in parts[:-1]:
                nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                os.close(parent); parent=nextfd
            try:
                fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|(os.O_DIRECTORY if directory else 0),dir_fd=parent)
            except FileNotFoundError:
                if relative=='data/deploy':
                    os.mkdir(parts[-1],0o755,dir_fd=parent)
                    fd=os.open(parts[-1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                else: raise
        finally: os.close(parent)
        meta=os.fstat(fd)
        assert meta.st_uid in (0,uid)
        assert (stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode) and meta.st_nlink==1)
        opened.append((fd,relative,directory,meta))
    changed=[]
    for fd,relative,directory,meta in opened:
        private=relative.startswith('.secrets') or relative=='.env' or relative.endswith('.env')
        mode=(0o700 if directory else 0o600) if private else (stat.S_IMODE(meta.st_mode)|(0o700 if directory else 0o600))
        if meta.st_uid!=uid or meta.st_gid!=gid or stat.S_IMODE(meta.st_mode)!=mode:
            os.fchown(fd,uid,gid); os.fchmod(fd,mode); changed.append(relative)
    print(json.dumps({'scope':'EXPLICIT_REQUIRED_RC6_PATHS','changed_paths':changed,'recursive':False}))
finally:
    for fd,*_ in opened: os.close(fd)
    os.close(base)
'''


def prepare_access(identity, *, run=subprocess.run):
    from scripts.porota_rootless_host import decode
    return decode(checked((SUDO, "-n", "--", "/usr/bin/python3", "-c", PREPARE_ACCESS,
                           str(identity.uid), str(identity.gid)), run=run))


def migrate(root, release_dir, sha, tree, attempt, *, identity=None, run=subprocess.run):
    from scripts.porota_rootless_host import Host, Identity, Docker, require, write_json, read_regular, decode
    from scripts.porota_release import verify_release
    from scripts.porota_operator_intent import migrate_intent
    identity = identity or Identity.current()
    current_path = identity.state / "current.json"
    if current_path.exists():
        current = decode(read_regular(current_path))
        require(current.get("migration_pending") is True, "USE_ROUTINE_DEPLOY_AFTER_MIGRATION")
    verify_release(release_dir, expected_sha=sha, expected_tree=tree)
    manager = Host(root, identity, Docker(identity, run=run))
    manager.boundary()
    transition = None
    try:
        with legacy_lock(identity) as lock_owner:
            def guard():
                require(lock_owner.poll() is None, "LEGACY_LOCK_LOST")
            transition = LegacyTransition(root, release_dir.parent / "source", identity=identity, run=run, guard=guard, source_sha=sha)
            guard()
            access_receipt = prepare_access(identity, run=run)
            operator_receipt = migrate_intent(identity.home)
            def quiesce():
                try:
                    return transition.quiesce()
                finally:
                    # A partially completed producer is evidence of mutation,
                    # never a forged quiescence authorization.
                    manager.receipt["legacy_quiescence"] = transition.receipt
            result = manager.deploy(release_dir, sha, tree, attempt, transition=quiesce, guard=guard)
            result["operator_intent"] = operator_receipt
            result["initial_access"] = access_receipt
            result["phase"] = "SCHEDULER_RESTORE"
            write_json(current_path, result)
            transition.scheduler.restore()
            result["migration_pending"] = False
            result["phase"] = "COMPLETE"
            write_json(current_path, result)
            write_json(identity.state / "attempts" / attempt / "receipt.json", result)
            return result
    except Exception as exc:
        exc.public_details = {**getattr(exc, "public_details", {}), "deployment": manager.receipt}
        if transition is not None:
            exc.public_details["legacy_quiescence"] = transition.receipt
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "prepare", "migrate"))
    parser.add_argument("--root", type=Path, default=Path("/opt/porota-trading"))
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--source-tree")
    parser.add_argument("--attempt")
    args = parser.parse_args(argv)
    try:
        if args.operation == "inspect":
            result = inspect_host(args.root)
        elif args.operation == "prepare":
            result = prepare()
        else:
            from scripts.porota_rootless_host import require
            require(all((args.release_dir, args.source_sha, args.source_tree, args.attempt)), "RELEASE_ARGUMENTS_REQUIRED")
            result = migrate(args.root, args.release_dir, args.source_sha, args.source_tree, args.attempt)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        public_reason = (str(exc) if type(exc).__name__ == "Hold" and
                         re.fullmatch(r"[A-Z0-9_]{1,80}", str(exc)) else "UNCLASSIFIED_BOOTSTRAP_FAILURE")
        print(json.dumps({"schema": "porota.rootless.setup.v1", "state": "BLOQUEADO", "operation": args.operation,
                          "failure_signature": public_reason,
                          **getattr(exc, "public_details", {})}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""One-time Porota rootless setup; inspection never changes the host.

Run through the authorized GitHub Action as the existing SSH account.  This
module never installs packages, changes another user's daemon, or prints
configuration values.  Routine promotion is in porota_rootless_host.py.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
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


def _scheduler_execution_evidence(properties, unit):
    """Only fixed systemd result fields; never export arbitrary unit output."""
    def stamp(key):
        value = properties.get(key)
        return value if isinstance(value, str) and len(value) <= 96 and re.fullmatch(
            r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) 20[0-9]{2}-[0-9]{2}-[0-9]{2} "
            r"[0-2][0-9]:[0-5][0-9]:[0-5][0-9] "
            r"(?:UTC|[+-][0-9]{2,4}|[A-Za-z][A-Za-z_+/\-]{1,30})", value) else None
    if unit.endswith(".timer"):
        return {key: stamp(key) for key in ("LastTriggerUSec", "NextElapseUSecRealtime")}
    def status(key):
        value = properties.get(key)
        return int(value) if isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value) and int(value) <= 65535 else None
    result = properties.get("Result")
    return {"Result": result if isinstance(result, str) and
            re.fullmatch(r"[a-z][a-z0-9-]{0,39}", result) else None,
            "ExecMainCode": status("ExecMainCode"), "ExecMainStatus": status("ExecMainStatus"),
            "ExecMainStartTimestamp": stamp("ExecMainStartTimestamp"),
            "ExecMainExitTimestamp": stamp("ExecMainExitTimestamp")}


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
        base = {"Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath", "DropInPaths"}
        evidence = ({"LastTriggerUSec", "NextElapseUSecRealtime"} if name.endswith(".timer") else
                    {"Result", "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainExitTimestamp"})
        properties = _properties(_call((SYSTEMCTL, "show", name,
            "--property=" + ",".join(sorted(base | evidence))), run=run), base | evidence)
        rows.append({"name": name, "policy_role": item["role"],
                     **{key: value for key, value in properties.items() if key in base},
                     **_scheduler_execution_evidence(properties, name)})
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


def private_release_reference(home, source_sha, source_tree):
    """Use only the matching private release, never a caller-supplied image reference."""
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
               for value in (source_sha, source_tree)):
        return None
    path = home / ".local/state/porota-rc6/releases" / source_sha / "release.json"
    try:
        if any(part.is_symlink() for part in (path, *path.parents)):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or
                    meta.st_nlink != 1 or meta.st_size > 2 * 1024**2):
                return None
            raw = os.read(fd, 2 * 1024**2 + 1)
        finally:
            os.close(fd)
        if len(raw) > 2 * 1024**2:
            return None
        release = json.loads(raw)
        if (not isinstance(release, dict) or release.get("schema") != "porota.release.v1" or
                release.get("source_sha") != source_sha or release.get("source_tree") != source_tree or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", str(release.get("image_id", "")))):
            return None
        return {key: release[key] for key in ("source_sha", "source_tree", "image_id")}
    except (OSError, ValueError, TypeError):
        return None


def private_release_custody(home, source_sha, *, include_attempts=True):
    """Read fixed release/journal metadata without authorizing cleanup."""
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        return None
    state = home / ".local/state/porota-rc6"
    directories = {"state": state, "releases": state / "releases",
                   "release": state / "releases" / source_sha, "attempts": state / "attempts"}
    def metadata(path):
        if any(parent.is_symlink() for parent in reversed(path.parents)):
            return {"present": None, "status": "PARENT_ALIAS"}
        return _metadata(path)
    def directory_safe(path, private=False):
        try:
            observed = metadata(path)
            item = path.lstat() if observed.get("present") is True else None
            return bool(item and stat.S_ISDIR(item.st_mode) and item.st_uid == os.getuid() and
                        not item.st_mode & (0o077 if private else 0o022))
        except OSError:
            return False
    def record(path, parents_safe):
        result = {"metadata": metadata(path), "status": "NO_VERIFICADO"}
        if not parents_safe or result["metadata"].get("symlink") is not False:
            return result
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                item = os.fstat(stream.fileno())
                if (not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or item.st_nlink != 1 or
                        item.st_mode & 0o077 or item.st_size > 2 * 1024**2):
                    return result
                raw = stream.read(2 * 1024**2 + 1)
            if len(raw) != item.st_size or len(raw) > 2 * 1024**2:
                return result
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            value = json.loads(raw)
            if not isinstance(value, dict):
                return result
            fields = {}
            for key, pattern in (("source_sha", r"[0-9a-f]{40}"), ("source_tree", r"[0-9a-f]{40}"),
                    ("image_id", r"sha256:[0-9a-f]{64}"), ("release_manifest_sha256", r"[0-9a-f]{64}")):
                if key in value:
                    fields[key] = value[key] if isinstance(value[key], str) and re.fullmatch(pattern, value[key]) else None
            for key, allowed in (("schema", {"porota.release.v1", "porota.rootless.deploy.v1"}),
                    ("state", {"NO_VERIFICADO", "VALIDADO_RUNTIME", "BLOQUEADO", "DESPLEGADO"})):
                if key in value:
                    fields[key] = value[key] if isinstance(value[key], str) and value[key] in allowed else None
            if "transition_started" in value:
                fields["transition_started"] = value["transition_started"] if type(value["transition_started"]) is bool else None
            if "roles" in value:
                fields["roles_empty"] = value["roles"] == {} if isinstance(value["roles"], dict) else None
            result.update(status="OBSERVED", fields=fields)
        except (OSError, ValueError, TypeError):
            pass
        return result
    result = {"directories": {key: metadata(path) for key, path in directories.items()},
              "attempts": [], "attempts_status": "NO_VERIFICADO"}
    result["release"] = record(directories["release"] / "release.json",
        all(directory_safe(directories[key]) for key in ("state", "releases", "release")))
    if not include_attempts:
        return result
    if not directory_safe(state) or not directory_safe(directories["attempts"]):
        return result
    try:
        with os.scandir(directories["attempts"]) as entries:
            bounded = []
            for entry in entries:
                bounded.append(entry.name)
                if len(bounded) > 32:
                    return dict(result, attempts_status="LIMIT_EXCEEDED")
        result["attempts_status"] = "OBSERVED"
        for name in sorted(bounded):
            if not re.fullmatch(r"[1-9][0-9]{0,19}-[1-9][0-9]{0,9}", name):
                result["attempts_status"] = "UNKNOWN_ENTRY"
                continue
            path = directories["attempts"] / name
            result["attempts"].append({"attempt": name, "directory": metadata(path),
                "receipt": record(path / "receipt.json", directory_safe(path, private=True))})
    except OSError:
        pass
    return result


def private_image_inventory(home, socket, info, *, run=subprocess.run):
    """Bounded local metadata; listed IDs and inspected descriptors are not aliases."""
    uid = os.getuid()
    meta = _metadata(socket)
    if (uid <= 0 or socket != Path(f"/run/user/{uid}/porota-rc6-docker.sock") or
            meta.get("uid") != uid or meta.get("socket") is not True or meta.get("symlink") is not False or
            not isinstance(info, dict) or "name=rootless" not in info.get("SecurityOptions", []) or
            info.get("DockerRootDir") != str(home / ".local/share/porota-rc6/docker") or not info.get("ID")):
        return {"status": "NO_VERIFICADO", "reason": "PRIVATE_DAEMON_UNVERIFIED"}
    def text(value):
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", value) else None
    def digest(value):
        return value if isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) else None
    def platform(value):
        return {key: text(value[key]) for key in ("os", "architecture", "variant", "os.version")
                if isinstance(value, dict) and text(value.get(key)) is not None}
    def descriptor(value):
        if not isinstance(value, dict):
            return None
        result = {"digest": digest(value.get("digest")), "platform": platform(value.get("platform"))}
        media = value.get("mediaType")
        if isinstance(media, str) and re.fullmatch(r"application/[A-Za-z0-9_.+-]{1,128}", media):
            result["mediaType"] = media
        if type(value.get("size")) is int and value["size"] >= 0:
            result["size"] = value["size"]
        return result
    def query(*args):
        return _call((DOCKER, "--host", f"unix://{socket}", *args), run=run, timeout=8)
    result = {"status": "NO_VERIFICADO", "ServerVersion": text(info.get("ServerVersion")),
              "KernelVersion": text(info.get("KernelVersion")), "Driver": text(info.get("Driver")),
              "DriverStatus": [], "backing_filesystem": None,
              "images": [], "containers": {"status": "NO_VERIFICADO"}}
    filesystem = _call(("/usr/bin/stat", "--file-system", "--format=%T",
                        str(home / ".local/share/porota-rc6/docker")), run=run, timeout=8)
    if filesystem is not None and filesystem.returncode == 0:
        value = filesystem.stdout.strip()
        if re.fullmatch(r"[A-Za-z0-9_/-]{1,64}", value):
            result["backing_filesystem"] = value
    allowed_driver_fields = {"driver-type", "Backing Filesystem", "Supports d_type", "Using metacopy",
                             "Native Overlay Diff", "userxattr"}
    for row in (info.get("DriverStatus") or [])[:16]:
        if isinstance(row, list) and len(row) == 2 and row[0] in allowed_driver_fields and text(row[1]) is not None:
            result["DriverStatus"].append(row)
    listing = query("image", "ls", "--all", "--quiet", "--no-trunc")
    if listing is None or listing.returncode:
        return dict(result, reason="PRIVATE_IMAGE_INVENTORY_UNAVAILABLE")
    ids = list(dict.fromkeys(listing.stdout.splitlines()))
    if not all(digest(value) for value in ids):
        return dict(result, reason="PRIVATE_IMAGE_INVENTORY_INVALID")
    result.update(status="OBSERVED", image_count=len(ids), images_truncated=len(ids) > 8)
    containers = query("container", "ls", "--all", "--quiet", "--no-trunc")
    if containers is not None and containers.returncode == 0:
        cids = list(dict.fromkeys(containers.stdout.splitlines()))
        if all(re.fullmatch(r"[0-9a-f]{64}", cid) for cid in cids):
            result["containers"] = {"status": "OBSERVED", "count": len(cids), "ids": cids[:32], "truncated": len(cids) > 32}
    observations = {}
    def inspect_image(image_id):
        if image_id in observations:
            return observations[image_id]
        row = {"requested_id": image_id, "status": "NO_VERIFICADO", "returncode": None}
        observations[image_id] = row
        inspected = query("image", "inspect", image_id)
        if inspected is not None:
            row["returncode"] = inspected.returncode
        if inspected is None or inspected.returncode:
            return row
        try:
            items = json.loads(inspected.stdout)
            if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
                return row
            item = items[0]
            config = item.get("Config") if isinstance(item.get("Config"), dict) else {}
            environment = config.get("Env") if isinstance(config.get("Env"), list) else []
            labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
            def source(key):
                values = [entry.partition("=")[2] for entry in environment
                          if isinstance(entry, str) and entry.startswith(key + "=")]
                return values[0] if len(values) == 1 and re.fullmatch(r"[0-9a-f]{40}", values[0]) else None
            row.update(status="OBSERVED", Id=digest(item.get("Id")), Descriptor=descriptor(item.get("Descriptor")),
                platform=platform({"os": item.get("Os"), "architecture": item.get("Architecture"), "variant": item.get("Variant")}),
                source_sha=source("POROTA_BUILD_SHA"), source_tree=source("POROTA_CANDIDATE_TREE_SHA"))
            row["labels"] = {key: labels[key] for key, pattern in (
                ("porota.commit", r"[0-9a-f]{40}"), ("porota.predeploy", r"v2"))
                if isinstance(labels.get(key), str) and re.fullmatch(pattern, labels[key])}
        except (ValueError, TypeError):
            pass
        return row
    for image_id in ids[:8]:
        row = dict(inspect_image(image_id))
        result["images"].append(row)
        custody = private_release_custody(home, row.get("source_sha"), include_attempts="journal_custody" not in result)
        if custody is not None:
            journals, status = custody.pop("attempts"), custody.pop("attempts_status")
            row["release_custody"] = custody
            if "journal_custody" not in result:
                result["journal_custody"] = {"status": status, "attempts": journals}
        release = private_release_reference(home, row.get("source_sha"), row.get("source_tree"))
        if release:
            row["release"] = release
            row["release_image_lookup"] = dict(inspect_image(release["image_id"]))
    return result


def private_runtime_inventory(home, socket, info, *, run=subprocess.run):
    """Observe only current.json's exact two private CIDs; no exec or logs."""
    uid = os.getuid()
    socket_meta = _metadata(socket)
    result = {"status": "NO_VERIFICADO", "reason": "CURRENT_RUNTIME_UNAVAILABLE"}
    if (uid <= 0 or socket != Path(f"/run/user/{uid}/porota-rc6-docker.sock") or
            socket_meta.get("uid") != uid or socket_meta.get("socket") is not True or
            socket_meta.get("symlink") is not False or not isinstance(info, dict) or
            "name=rootless" not in info.get("SecurityOptions", []) or
            info.get("DockerRootDir") != str(home / ".local/share/porota-rc6/docker") or
            not isinstance(info.get("ID"), str)):
        return dict(result, reason="PRIVATE_DAEMON_UNVERIFIED")
    state = home / ".local/state/porota-rc6"
    current = state / "current.json"
    try:
        if any(path.is_symlink() for path in (current, *current.parents)):
            return dict(result, reason="CURRENT_PATH_ALIAS")
        directory = state.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != uid or
                directory.st_mode & 0o077):
            return dict(result, reason="CURRENT_STATE_CUSTODY")
    except OSError:
        return result

    def read_current():
        fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != uid or meta.st_nlink != 1 or
                    meta.st_mode & 0o077 or meta.st_size > 131072):
                return None
            raw = os.read(fd, 131073)
            return raw if len(raw) == meta.st_size else None
        finally:
            os.close(fd)

    try:
        original = read_current()
        document = json.loads(original) if original else None
    except (OSError, ValueError, TypeError):
        return result
    if not isinstance(document, dict):
        return result
    roles = document.get("roles")
    sha = document.get("source_sha")
    tree = document.get("source_tree")
    image = document.get("image_id")
    attempt = document.get("attempt")
    if (document.get("schema") != "porota.rootless.deploy.v1" or
            document.get("state") != "VALIDADO_RUNTIME" or document.get("phase") != "COMPLETE" or
            document.get("daemon_id") != info["ID"] or
            not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) or
            not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree) or
            not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or
            not isinstance(attempt, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", attempt) or
            not isinstance(roles, dict) or set(roles) != {"observer", "dashboard"} or
            any(not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid)
                for cid in roles.values()) or len(set(roles.values())) != 2):
        return dict(result, reason="CURRENT_IDENTITY_UNVERIFIED")

    observed = {}
    for role in ("observer", "dashboard"):
        cid = roles[role]
        response = _call((DOCKER, "--host", f"unix://{socket}",
                          "container", "inspect", cid), run=run, timeout=8)
        if response is None or response.returncode:
            return dict(result, reason="PRIVATE_CONTAINER_INSPECT_UNAVAILABLE")
        try:
            entries = json.loads(response.stdout)
            if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
                return dict(result, reason="PRIVATE_CONTAINER_IDENTITY_UNVERIFIED")
            item = entries[0]
            config = item.get("Config")
            config = config if isinstance(config, dict) else {}
            labels = config.get("Labels")
            labels = labels if isinstance(labels, dict) else {}
            environment = config.get("Env")
            def source_value(key):
                if not isinstance(environment, list) or len(environment) > 1024:
                    return None
                values = [value.partition("=")[2] for value in environment
                          if isinstance(value, str) and value.startswith(key + "=")]
                return values[0] if len(values) == 1 else None
            runtime = item.get("State")
            runtime = runtime if isinstance(runtime, dict) else {}
            status = runtime.get("Status")
            health = runtime.get("Health")
            health_status = (health.get("Status") if isinstance(health, dict) else None)
            started = runtime.get("StartedAt")
            if (item.get("Id") != cid or item.get("Name") != f"/porota-rc6-{role}-{attempt}" or
                    item.get("Image") != image or labels.get("io.porota.project") != "rc6" or
                    labels.get("io.porota.role") != role or labels.get("io.porota.attempt") != attempt or
                    source_value("POROTA_BUILD_SHA") != sha or
                    source_value("POROTA_CANDIDATE_TREE_SHA") != tree or
                    status not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"} or
                    type(runtime.get("Running")) is not bool or type(runtime.get("OOMKilled")) is not bool or
                    type(runtime.get("Pid")) is not int or runtime["Pid"] < 0 or
                    type(runtime.get("ExitCode")) is not int or
                    type(item.get("RestartCount")) is not int or item["RestartCount"] < 0 or
                    health_status not in {None, "starting", "healthy", "unhealthy"}):
                return dict(result, reason="PRIVATE_CONTAINER_IDENTITY_UNVERIFIED")
            observed[role] = {"cid": cid, "Status": status, "Running": runtime["Running"],
                              "OOMKilled": runtime["OOMKilled"], "Pid": runtime["Pid"],
                              "ExitCode": runtime["ExitCode"], "RestartCount": item["RestartCount"],
                              "Health": health_status,
                              "StartedAt": started if isinstance(started, str) and re.fullmatch(
                                  r"(?:20[0-9]{2}|0001)-[0-9]{2}-[0-9]{2}T[0-9:.]+(?:Z|[+-][0-9:]+)",
                                  started) and len(started) <= 64 else None}
        except (ValueError, TypeError):
            return dict(result, reason="PRIVATE_CONTAINER_INSPECT_INVALID")
    try:
        if read_current() != original:
            return dict(result, reason="CURRENT_CHANGED_DURING_INSPECT")
    except OSError:
        return dict(result, reason="CURRENT_CHANGED_DURING_INSPECT")
    return {"status": "OBSERVED", "read_only": True, "runtime_certified": False,
            "source_sha": sha, "source_tree": tree, "image_id": image,
            "attempt": attempt, "roles": observed}


def private_controller_health(root, source_sha, source_tree, *, now=None):
    """Read one bounded controller heartbeat, never child-process/secret payloads."""
    result = {"status": "NO_VERIFICADO", "reason": "CONTROLLER_HEALTH_UNAVAILABLE"}
    path = root / "data/paper_v17/artifacts/observer_v17.db/runtime-health.json"
    try:
        if any(part.is_symlink() for part in (path, *path.parents)):
            return dict(result, reason="CONTROLLER_PATH_ALIAS")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or
                    meta.st_nlink != 1 or meta.st_mode & 0o022 or meta.st_size > 65536):
                return dict(result, reason="CONTROLLER_FILE_CUSTODY")
            raw = os.read(fd, 65537)
            if len(raw) != meta.st_size:
                return result
        finally:
            os.close(fd)
        document = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return result
    if (not isinstance(document, dict) or document.get("schema") != "rc6.runtime-child-health.v1" or
            document.get("source_sha") != source_sha or
            document.get("candidate_tree_sha") != source_tree):
        return dict(result, reason="CONTROLLER_SOURCE_MISMATCH")

    def instant(raw):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else None
            return value if value is not None and value.tzinfo is not None else None
        except ValueError:
            return None

    observed_at = instant(document.get("recorded_at"))
    reference = now or datetime.now(timezone.utc)
    if observed_at is None or reference.tzinfo is None or not 0 <= (reference - observed_at).total_seconds() <= 120:
        return dict(result, reason="CONTROLLER_HEALTH_STALE")
    roles = ("scanner", "performance", "dynamic_shadow", "exit_reader",
             "notifications", "candles", "intraday_scalping", "caucion_cash_sweep")
    children = document.get("children")
    if not isinstance(children, dict) or set(children) != set(roles):
        return dict(result, reason="CONTROLLER_CHILD_ROLES_MISMATCH")
    observed, pids = {}, set()
    for role in roles:
        row = children[role]
        if not isinstance(row, dict):
            return dict(result, reason="CONTROLLER_CHILD_IDENTITY_UNVERIFIED")
        pid = row.get("pid")
        started_at = instant(row.get("started_at"))
        if (row.get("state") != "RUNNING" or type(pid) is not int or pid <= 0 or pid in pids or
                started_at is None or started_at > observed_at):
            return dict(result, reason="CONTROLLER_CHILD_IDENTITY_UNVERIFIED")
        pids.add(pid)
        observed[role] = {"state": "RUNNING", "pid": pid, "started_at": started_at.isoformat()}
    return {"status": "OBSERVED", "recorded_at": observed_at.isoformat(),
            "source_sha": source_sha, "source_tree": source_tree, "children": observed,
            "freshness_seconds": 120, "process_liveness": "NO_VERIFICADO"}


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
                if not isinstance(raw, dict):
                    raise ValueError()
                own_info = {k: raw.get(k) for k in (
                    "ID", "SecurityOptions", "CgroupVersion", "CgroupDriver", "DockerRootDir")}
                own_info.update(private_image_inventory(home, socket, raw, run=run))
                own_info["runtime"] = private_runtime_inventory(home, socket, raw, run=run)
                if own_info["runtime"].get("status") == "OBSERVED":
                    own_info["runtime"]["controller"] = private_controller_health(
                        root, own_info["runtime"]["source_sha"], own_info["runtime"]["source_tree"])
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
            "current_metadata": _metadata(home / ".local/state/porota-rc6/current.json"),
            "conventional_user_socket": _metadata(Path(f"/run/user/{uid}/docker.sock")),
            "rootful_socket": _metadata(Path("/var/run/docker.sock")),
            "target_data_root": str(home / ".local/share/porota-rc6/docker"),
            "directory_metadata": {name: _metadata(home / relative) for name, relative in (
                ("state", ".local/state/porota-rc6"),
                ("daemon_config", ".config/porota-rc6"),
                ("daemon_store", ".local/share/porota-rc6/docker"),
                ("client_config", ".config/porota-rc6/client"))},
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


def daemon_configuration(identity, launcher):
    from scripts.porota_rootless_host import require
    # systemd unit syntax is not shell syntax; accept ordinary installed paths only.
    require(all(value and all(c.isalnum() or c in "/._-" for c in value)
                for value in (str(identity.home), identity.name, launcher)), "BOOTSTRAP_PATH_UNSUPPORTED")
    runtime = f"/run/user/{identity.uid}/porota-rc6"
    config_path = identity.home / ".config/porota-rc6/daemon.json"
    config = {"hosts": [f"unix://{identity.socket}"], "data-root": str(identity.store),
              "exec-root": runtime + "/exec", "pidfile": runtime + "/dockerd.pid",
              "exec-opts": ["native.cgroupdriver=systemd"], "live-restore": False}
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


def prepare(identity=None, *, run=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    from scripts.porota_rootless_host import (Identity, Host, Docker, require, Hold,
        no_alias, private_dir, read_regular, atomic_write, user_lock, write_json)
    identity = identity or Identity.current()
    private_dir(identity.state)
    receipt = {"schema": "porota.rootless.prepare.v1", "state": "NO_VERIFICADO",
               "uid": identity.uid, "socket": str(identity.socket), "service": SERVICE,
               "ppi_watch_touched": False, "boca_touched": False}
    with user_lock(identity.state / "deploy.lock"):
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
        linger = checked(("/usr/bin/loginctl", "show-user", str(identity.uid), "--property=Linger", "--value"), run=run)
        if linger != "yes":
            checked((SUDO, "-n", "--", "/usr/bin/loginctl", "enable-linger", identity.name), run=run)
        require(checked(("/usr/bin/loginctl", "show-user", str(identity.uid), "--property=Linger", "--value"), run=run) == "yes",
                "USER_LINGER_REQUIRED")
        config_path, config, unit = daemon_configuration(identity, binaries["dockerd-rootless.sh"])
        private_dir(config_path.parent)
        private_dir(identity.store)
        unit_path = no_alias(identity.home / ".config/systemd/user" / SERVICE)
        unit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(unit_path.parent.stat().st_uid == identity.uid and not unit_path.parent.stat().st_mode & 0o022,
                "USER_UNIT_DIRECTORY_UNSAFE")
        existing = unit_path.exists()
        # Never overwrite another daemon definition or undocumented configuration.
        for path, raw in ((config_path, config), (unit_path, unit)):
            if path.exists():
                require(read_regular(path) == raw and path.stat().st_uid == identity.uid, "ROOTLESS_SETUP_DRIFT")
            else:
                atomic_write(path, raw, private=path != unit_path)
        checked((SYSTEMCTL, "--user", "daemon-reload"), run=run)
        checked((SYSTEMCTL, "--user", "enable", "--now", SERVICE), run=run, timeout=100)
        manager = Host(Path("/opt/porota-trading"), identity, Docker(identity, run=run))
        try:
            deadline = clock() + 45
            while True:
                try:
                    daemon_id = manager.boundary()
                    break
                except (Hold, OSError):
                    require(clock() < deadline, "ROOTLESS_DAEMON_START_TIMEOUT")
                    sleep(2)
        except Exception:
            if not existing:
                _call((SYSTEMCTL, "--user", "stop", SERVICE), run=run, timeout=130)
            raise
        receipt.update({"state": "PREPARADO", "daemon_id": daemon_id,
                        "runtime_validation": "NO_VERIFICADO"})
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
        yield
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
    def __init__(self, root, source, *, run=subprocess.run):
        from scripts.porota_rootless_host import require, decode
        require(root == Path("/opt/porota-trading"), "LEGACY_ROOT_NOT_AUTHORIZED")
        self.root, self.source, self.run = root, source, run
        self.receipt = {"schema": "porota.rootless.legacy-quiescence.v1", "state": "NO_VERIFICADO",
                        "containers": [], "units": [], "functional_successors": "NO_VERIFICADO"}
        self.daemon_id = decode(self.docker("info", "--format", "{{json .}}"))["ID"]
        # The exact-name filters expose no unrelated applications.
        ids = self.docker("container", "ls", "--all", "--quiet", "--no-trunc", "--filter",
                          "name=^/(porota_production_observer|porota_production_dashboard|porota_trading_bot)$").splitlines()
        self.containers = []
        for cid in ids:
            item = self.inspect(cid)
            self.validate(item)
            self.containers.append(item)
        require(len({item["Name"] for item in self.containers}) == len(self.containers), "LEGACY_CONTAINER_AMBIGUOUS")
        inventory = unit_inventory(source, run=run)
        require(inventory["status"] == "OBSERVED" and not inventory["unmapped_units"], "LEGACY_UNIT_INVENTORY_UNVERIFIED")
        self.units = inventory["units"]
        blocked = [item["name"] for item in self.units if item["policy_role"].startswith("ACTIVE_") and
                   (item.get("ActiveState") in {"active", "activating", "reloading"} or
                    item.get("UnitFileState") in {"enabled", "enabled-runtime", "linked", "linked-runtime"})]
        self.receipt["functional_units_needing_successor"] = blocked
        if blocked:
            from scripts.porota_rootless_host import Hold
            failure = Hold("LEGACY_FUNCTIONAL_UNIT_NEEDS_ROOTLESS_SUCCESSOR")
            failure.public_details = {"functional_units_needing_successor": blocked}
            raise failure
        for item in self.units:
            require(item.get("LoadState") in {"not-found", "loaded", "masked"} and
                    not item.get("DropInPaths"), "LEGACY_UNIT_IDENTITY_UNVERIFIED")
            if item.get("LoadState") == "loaded":
                path = Path(item["FragmentPath"])
                require(path.parent in {Path("/etc/systemd/system"), Path("/usr/lib/systemd/system"), Path("/lib/systemd/system")} and
                        path.name == item["name"], "LEGACY_UNIT_IDENTITY_UNVERIFIED")
                # Metadata and bytes are checked again before stopping any unit.
                item["fragment_sha256"] = self.unit_digest(path)
                policy = json.loads((source / "ops/policy/host-control-plane-reconciliation-v2.json").read_text())["units"]
                paths = [source / name for name in policy if Path(name).name == item["name"]]
                require(len(paths) == 1 and hashlib.sha256(paths[0].read_bytes()).hexdigest() == item["fragment_sha256"],
                        "LEGACY_UNIT_SOURCE_DRIFT")

    def docker(self, *args):
        return checked((SUDO, "-n", "--", DOCKER, "--host", "unix:///var/run/docker.sock", *args), run=self.run)

    def inspect(self, cid):
        from scripts.porota_rootless_host import CID, require, decode
        require(CID.fullmatch(cid), "LEGACY_CID_INVALID")
        rows = decode(self.docker("container", "inspect", cid))
        require(len(rows) == 1 and rows[0].get("Id") == cid, "LEGACY_CONTAINER_IDENTITY")
        return rows[0]

    def validate(self, item):
        from scripts.porota_rootless_host import require, IMAGE_ID
        name = item.get("Name", "").lstrip("/")
        config = item.get("Config") or {}
        require(name in LEGACY_NAMES and IMAGE_ID.fullmatch(str(item.get("Image", ""))), "LEGACY_CONTAINER_IDENTITY")
        require(any(m.get("Type") == "bind" and m.get("Destination") == "/app/data" and
                    m.get("Source") == str(self.root / "data") for m in item.get("Mounts", [])), "LEGACY_DATA_IDENTITY")
        expected = {LEGACY_NAMES[0]: ["python", "bf_production_paper_observer.py"],
                    LEGACY_NAMES[1]: ["python", "o_dashboard.py"]}
        if name == LEGACY_NAMES[2]:
            require(item.get("State", {}).get("Running") is False, "LEGACY_MONOLITH_NEEDS_REVIEW")
        else:
            require(config.get("Cmd") == expected[name] and not config.get("Entrypoint"), "LEGACY_COMMAND_IDENTITY")

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
        for item in sorted(self.units, key=lambda row: not row["name"].endswith(".timer")):
            if item.get("LoadState") != "loaded":
                continue
            require(self.unit_digest(Path(item["FragmentPath"])) == item["fragment_sha256"], "LEGACY_UNIT_CHANGED")
            if item["policy_role"].startswith("ACTIVE_"):
                # Inactive functional definitions remain installed and unchanged.
                continue
            checked((SUDO, "-n", "--", SYSTEMCTL, "disable", "--now", item["name"]), run=self.run, timeout=330)
            self.receipt["units"].append(item["name"])
        for item in self.containers:
            self.docker("container", "update", "--restart=no", item["Id"])
            if self.inspect(item["Id"])["State"].get("Running"):
                self.docker("container", "stop", "--time", "30", item["Id"])
            final = self.inspect(item["Id"])
            self.validate(final)
            require(final["State"].get("Running") is False and
                    final.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") == "no", "LEGACY_STOP_UNVERIFIED")
            self.receipt["containers"].append({"cid": item["Id"], "image_id": item["Image"], "state": "STOPPED_RETAINED"})
        require(decode(self.docker("info", "--format", "{{json .}}"))["ID"] == self.daemon_id, "LEGACY_DAEMON_CHANGED")
        require(database_snapshot(self.root / DB_RELATIVE) == before, "DATABASE_CHANGED_IDENTITY")
        after = unit_inventory(self.source, run=self.run)
        require(after["status"] == "OBSERVED" and not after["unmapped_units"] and all(
            row.get("ActiveState") not in {"active", "activating", "reloading"} and
            row.get("UnitFileState") not in {"enabled", "enabled-runtime"} for row in after["units"]), "LEGACY_UNIT_NOT_QUIESCENT")
        self.receipt.update({"state": "QUIESCENT", "database": before, "rootful_daemon_stopped": False,
                             "functional_successors": "NO_ACTIVE_LEGACY_FUNCTIONS_OBSERVED"})
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
        mode=(0o700 if directory else 0o600) if private else (0o755 if directory else stat.S_IMODE(meta.st_mode)|0o600)
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
    from scripts.porota_rootless_host import Host, Identity, Docker, require
    from scripts.porota_release import verify_release
    from scripts.porota_operator_intent import migrate_intent
    identity = identity or Identity.current()
    require(not (identity.state / "current.json").exists(), "USE_ROUTINE_DEPLOY_AFTER_MIGRATION")
    verify_release(release_dir, expected_sha=sha, expected_tree=tree)
    manager = Host(root, identity, Docker(identity, run=run))
    manager.boundary()
    transition = None
    try:
        with legacy_lock(identity):
            transition = LegacyTransition(root, release_dir.parent / "source", run=run)
            access_receipt = prepare_access(identity, run=run)
            operator_receipt = migrate_intent(identity.home)
            result = manager.deploy(release_dir, sha, tree, attempt, transition=transition.quiesce)
            result["operator_intent"] = operator_receipt
            result["initial_access"] = access_receipt
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
        return 79


if __name__ == "__main__":
    raise SystemExit(main())

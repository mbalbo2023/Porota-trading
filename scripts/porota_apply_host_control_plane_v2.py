#!/usr/bin/env python3
"""Apply the complete RC6 systemd lifecycle policy on the deployment host.

Canonical Git-tracked RC6 units are managed from the versioned units map.
Historical host residue may be retired only through the separate exact-name
legacy_retire_units allowlist. No discovery-based deletion is permitted.

The tool never touches PPI Watch, unknown host units, data, databases, Docker
volumes or secrets. Independently managed external control-plane units are
explicitly preserved by policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

UNIT_RE = re.compile(r"^porota-[A-Za-z0-9_.@-]*rc6[A-Za-z0-9_.@-]*\.(?:service|timer)$")
LEGACY_RETIRE_UNIT_RE = re.compile(r"^porota-[A-Za-z0-9_.@-]+\.(?:service|timer)$")
IOL_OAUTH_GUARD_TIMER = 'porota-iol-oauth-health-rc6.timer'
PAPER_PREOPEN_TIMER = 'porota-preopen-rc6.timer'


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(policy: dict) -> list[dict]:
    result=[]
    for source_path,row in sorted(policy.get("units", {}).items()):
        source=Path(source_path)
        name=source.name
        if (
            source.is_absolute()
            or ".." in source.parts
            or not (source_path.startswith("systemd/") or source_path.startswith("ops/systemd/"))
            or not UNIT_RE.fullmatch(name)
        ):
            raise ValueError(f"UNSAFE_UNIT_SOURCE:{source_path}")
        if "ppi" in name.lower() and "watch" in name.lower():
            raise ValueError("PPI_WATCH_FORBIDDEN")
        role=str(row.get("role") or "")
        preserved = row.get('suspend_during_deploy') is False
        if 'suspend_during_deploy' in row and row['suspend_during_deploy'] is not True:
            if (name != IOL_OAUTH_GUARD_TIMER or not preserved or
                    role != 'ACTIVE_TIMER' or row.get('install') is not True or
                    row.get('enabled') is not True or row.get('active') is not True):
                raise ValueError('HOST_UNAUTHORIZED_TIMER_CARVEOUT:' + name)
        result.append({
            "source_path":source_path,
            "unit_name":name,
            "role":role,
            "install":bool(row.get("install")),
            "enabled":row.get("enabled"),
            "active":row.get("active"),
            "masked":bool(row.get("masked")),
            "remove_on_deploy":bool(row.get("remove_on_deploy")),
            "preserve_during_deploy":preserved,
        })
    return result


def build_legacy_retire_plan(policy: dict) -> list[str]:
    """Return the exact legacy host-unit retirement allowlist.

    This deliberately does not discover units.  Only names explicitly versioned
    in policy may be touched, PPI Watch is always forbidden, and independently
    managed external control-plane units cannot be included.
    """
    external=set((policy.get("external_host_units") or {}).keys())
    residual_review=set((policy.get("residual_host_units") or {}).keys())
    result=[]
    seen=set()
    for raw in policy.get("legacy_retire_units") or []:
        name=str(raw or "").strip()
        if not LEGACY_RETIRE_UNIT_RE.fullmatch(name):
            raise ValueError(f"UNSAFE_LEGACY_RETIRE_UNIT:{name}")
        if "ppi" in name.lower() and "watch" in name.lower():
            raise ValueError("PPI_WATCH_FORBIDDEN")
        if name in external:
            raise ValueError(f"EXTERNAL_HOST_UNIT_FORBIDDEN:{name}")
        if name in residual_review:
            raise ValueError(f"RESIDUAL_REVIEW_UNIT_FORBIDDEN:{name}")
        if name in seen:
            raise ValueError(f"DUPLICATE_LEGACY_RETIRE_UNIT:{name}")
        seen.add(name)
        result.append(name)
    return result


def _require_inherited_finalizer_mutex():
    """Trust only the actual root-owned lock FD passed by Deploy V2."""
    from scripts import rc6_deploy_host_mutex as mutex
    fd_raw = os.environ.get('RC6_DEPLOY_MUTEX_FD', '')
    if not re.fullmatch(r'[0-9]{1,8}', fd_raw):
        raise RuntimeError('POSTFINAL_OPERATOR_TIMER_MUTEX_REQUIRED')
    try:
        held = os.fstat(int(fd_raw))
        path = os.lstat(mutex.LOCK_PATH)
    except OSError as error:
        raise RuntimeError('POSTFINAL_OPERATOR_TIMER_MUTEX_UNVERIFIED') from error
    if (not stat.S_ISREG(held.st_mode) or not stat.S_ISREG(path.st_mode)
            or (held.st_dev, held.st_ino) != (path.st_dev, path.st_ino)
            or path.st_uid != 0 or path.st_mode & 0o022):
        raise RuntimeError('POSTFINAL_OPERATOR_TIMER_MUTEX_UNTRUSTED')


def reconcile_finalized_operator_paper_timer(candidate_sha: str,
                                             candidate_tree_sha: str) -> dict:
    """Apply current owner PAPER intent only AFTER exact runtime finalization.

    The caller is Deploy V2's already-exclusive `finalize` host-mutex phase,
    after it atomically published VALIDATED_RUNTIME. Do not call this helper
    during COLD, scheduler restore, a pending marker, or outside that mutex.
    The root-owned durable ACTIVE/PAUSED receipt is independent of code SHA;
    each call nevertheless proves the *current* frozen release and PAPER/0.
    Never touch unrelated timers, providers, PPI Watch or real-order routes.
    """
    from scripts import rc6_operator_paper_rearm as operator

    if os.geteuid() != 0:
        raise RuntimeError('POSTFINAL_OPERATOR_TIMER_ROOT_REQUIRED')
    if (not operator.SHA.fullmatch(candidate_sha) or
            not operator.SHA.fullmatch(candidate_tree_sha)):
        raise ValueError('POSTFINAL_OPERATOR_TIMER_CANDIDATE_INVALID')
    _require_inherited_finalizer_mutex()

    # Fatal release/safety corruption aborts before *any* systemd mutation.
    source, tree, image = operator._validated_paper_release(
        marker_path=operator.MARKER_HOST, frozen_path=operator.FROZEN_HOST,
        context='host', candidate_sha=candidate_sha,
        candidate_tree_sha=candidate_tree_sha)
    for name in ('porota_production_observer', 'porota_production_dashboard',
                 'porota_critical_approval_rc6'):
        result = _run('docker', 'inspect', '-f', '{{.State.Running}}|{{.Image}}',
                      name, check=False)
        if result.returncode or result.stdout.strip() != 'true|' + image:
            raise RuntimeError('POSTFINAL_OPERATOR_TIMER_RUNTIME_IMAGE_RED:' + name)

    now = datetime.now(operator.TZ)
    try:
        receipt = operator._root_owned_receipt(operator.OPERATOR_DIR_HOST)
        intent = operator._validated_intent(receipt, now)
    except (OSError, ValueError, TypeError, KeyError):
        # An absent/invalid receipt cannot invent or prolong a PAPER GO.
        # It is a scoped NO_GO, never an impediment to technical deployment.
        intent = 'UNKNOWN_NO_GO'
    authorized, reason, summary = operator.evaluate_rearm(now=now, context='host')
    if intent == operator.REARM_STATUS:
        if (not authorized or not isinstance(summary, dict) or
                summary.get('candidate_sha') != source or
                summary.get('candidate_tree_sha') != tree or
                summary.get('image_id') != image):
            raise RuntimeError('POSTFINAL_OPERATOR_TIMER_ACTIVE_PROOF_RED:' + reason)
        target = ('enabled', 'active')
    else:
        if authorized:
            raise RuntimeError('POSTFINAL_OPERATOR_TIMER_INCONSISTENT_NO_GO')
        target = ('disabled', 'inactive')

    def timer_state():
        enabled = _run('systemctl', 'is-enabled', PAPER_PREOPEN_TIMER,
                       check=False).stdout.strip()
        active = _run('systemctl', 'is-active', PAPER_PREOPEN_TIMER,
                      check=False).stdout.strip()
        if (enabled not in {'enabled', 'disabled'} or
                active not in {'active', 'inactive'}):
            raise RuntimeError('POSTFINAL_OPERATOR_TIMER_STATE_UNKNOWN')
        return enabled, active

    before = timer_state()
    try:
        if target == ('enabled', 'active'):
            if before == ('disabled', 'inactive'):
                _run('systemctl', 'enable', '--now', PAPER_PREOPEN_TIMER)
            elif before == ('enabled', 'inactive'):
                _run('systemctl', 'start', PAPER_PREOPEN_TIMER)
            elif before != target:
                raise RuntimeError('POSTFINAL_OPERATOR_TIMER_UNEXPECTED_ACTIVE_STATE')
        elif before != target:
            # Explicit PAUSE (or absent trustworthy ACTIVE consent) is fail-
            # closed even if an earlier T-10 receipt still says GO.
            _run('systemctl', 'disable', '--now', PAPER_PREOPEN_TIMER)
        after = timer_state()
        if after != target:
            raise RuntimeError('POSTFINAL_OPERATOR_TIMER_POSTVERIFY_RED')
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        if target == ('enabled', 'active'):
            # `enable --now` can partially mutate the timer before systemctl
            # fails. Keep the durable operator ACTIVE intent unchanged, but
            # quarantine this unverified technical admission until a tested
            # fix-forward retry. A failure may never be called PAPER GO.
            try:
                _run('systemctl', 'disable', '--now', PAPER_PREOPEN_TIMER)
                if timer_state() != ('disabled', 'inactive'):
                    raise RuntimeError('POSTFINAL_OPERATOR_TIMER_QUARANTINE_NOT_CONFIRMED')
            except (OSError, subprocess.CalledProcessError, RuntimeError) as failure:
                raise RuntimeError('POSTFINAL_OPERATOR_TIMER_QUARANTINE_RED') from failure
            raise RuntimeError('POSTFINAL_OPERATOR_TIMER_QUARANTINED_NO_GO') from error
        raise
    # Do not assert an ACTIVATE after an owner PAUSE raced our first read.
    # The same root mutex serializes the authoritative operator Actions.
    next_authorized, _, next_summary = operator.evaluate_rearm(
        now=datetime.now(operator.TZ), context='host')
    if next_authorized != authorized or (authorized and next_summary != summary):
        raise RuntimeError('POSTFINAL_OPERATOR_TIMER_INTENT_CHANGED')
    return {'status': 'GREEN', 'candidate_sha': source,
            'candidate_tree_sha': tree, 'intent': intent,
            'timer_before': '|'.join(before), 'timer_after': '|'.join(after),
            'paper_mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
            'real_order_routes': 'NOT_CALLED', 'ppi_watch_mutated': False}


def apply(root: Path, policy: dict, systemd_root: Path, *, keep_active_timers_paused: bool = False) -> dict:
    plan=build_plan(policy)
    legacy_retire=build_legacy_retire_plan(policy)
    operator_preopen_paused=False
    if any(item['role']=='ACTIVE_TIMER' and
           item['unit_name']=='porota-preopen-rc6.timer' for item in plan):
        current=_run('systemctl','is-enabled','porota-preopen-rc6.timer',check=False).stdout.strip()
        if current=='disabled':
            # A125 authorized stopping preopen ONLY on 2026-10-27 ART.
            # Its immutable receipt may explain a continuing disabled state,
            # but does not authorize a new day's trading or any rearming.
            # Every unrelated disabled-timer condition stays RED.
            if root.is_symlink() or not (root/'scripts/rc6_deploy_scheduler_suspend.py').is_file():
                raise RuntimeError('HOST_OPERATOR_PAUSE_HELPER_MISSING')
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from scripts.rc6_deploy_scheduler_suspend import (
                operator_preopen_hold_evidence, operator_preopen_timer_disabled)
            if not keep_active_timers_paused:
                raise RuntimeError('HOST_OPERATOR_PREOPEN_REARM_FORBIDDEN')
            operator_preopen_hold_evidence()
            if not operator_preopen_timer_disabled():
                raise RuntimeError('HOST_OPERATOR_PREOPEN_STATE_CHANGED')
            operator_preopen_paused=True
    changed=[]
    for item in plan:
        src=(root / item["source_path"]).resolve()
        target=systemd_root / item["unit_name"]
        if item["install"]:
            if not src.is_file():
                raise RuntimeError(f"HOST_UNIT_SOURCE_MISSING:{item['source_path']}")
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.is_symlink():
                target.unlink()
            shutil.copy2(src,target)
            os.chmod(target,0o644)
            changed.append({"unit":item["unit_name"],"action":"INSTALL","sha256":_sha256(target)})
        elif item["role"].startswith("RETIRED_"):
            _run("systemctl","disable","--now",item["unit_name"],check=False)
            if target.exists() or target.is_symlink():
                target.unlink()
            changed.append({"unit":item["unit_name"],"action":"RETIRE"})
        elif item["role"]=="QUARANTINED_TIMER":
            _run("systemctl","disable","--now",item["unit_name"],check=False)
            if target.exists() or target.is_symlink():
                target.unlink()
            changed.append({"unit":item["unit_name"],"action":"QUARANTINE"})

    for name in legacy_retire:
        target=systemd_root / name
        _run("systemctl","disable","--now",name,check=False)
        if target.exists() or target.is_symlink():
            target.unlink()
        _run("systemctl","reset-failed",name,check=False)
        changed.append({"unit":name,"action":"RETIRE_EXACT_LEGACY"})

    _run("systemctl","daemon-reload")

    for item in plan:
        name=item["unit_name"]
        role=item["role"]
        if role=="ACTIVE_TIMER":
            _run("systemctl","unmask",name,check=False)
            if keep_active_timers_paused:
                # The deploy owns an exact scheduler suspension receipt. Unit
                # reconciliation must not fire these timers during COLD2 or
                # contract evidence materialization.
                if item['preserve_during_deploy']:
                    # Scheduler already migrated exactly one OAuth-only timer
                    # from the A138 16-timer hold. Do not start an unproven
                    # guardian through the broad host reconciler.
                    if (_run('systemctl','is-active',name,check=False).stdout.strip() != 'active' or
                            _run('systemctl','is-enabled',name,check=False).stdout.strip() != 'enabled'):
                        raise RuntimeError('HOST_IOL_GUARD_CARVEOUT_NOT_ADOPTED')
                elif not (operator_preopen_paused and name=='porota-preopen-rc6.timer'):
                    _run("systemctl","enable",name)
            else:
                _run("systemctl","enable","--now",name)
        elif role=="QUARANTINED_TIMER":
            _run("systemctl","mask",name)
            _run("systemctl","stop",name,check=False)
        elif role=="RETIRED_TIMER":
            _run("systemctl","disable","--now",name,check=False)

    verification=[]
    for item in plan:
        name=item["unit_name"]
        role=item["role"]
        if role=="ACTIVE_TIMER":
            enabled=_run("systemctl","is-enabled",name,
                         check=not (operator_preopen_paused and
                                    name=='porota-preopen-rc6.timer')).stdout.strip()
            active=_run("systemctl","is-active",name,check=False).stdout.strip()
            expected_active = ("active" if item['preserve_during_deploy'] else "inactive") if keep_active_timers_paused else "active"
            expected_enabled = ("disabled" if operator_preopen_paused and
                                name=='porota-preopen-rc6.timer' else "enabled")
            if enabled!=expected_enabled or active!=expected_active:
                raise RuntimeError(f"ACTIVE_TIMER_VERIFY_FAILED:{name}:{enabled}:{active}")
            verification.append({"unit":name,"state":f"{expected_enabled}|{expected_active}"})
        elif role=="QUARANTINED_TIMER":
            enabled=_run("systemctl","is-enabled",name,check=False).stdout.strip()
            active=_run("systemctl","is-active",name,check=False).stdout.strip()
            if enabled!="masked" or active=="active":
                raise RuntimeError(f"QUARANTINE_VERIFY_FAILED:{name}:{enabled}:{active}")
            verification.append({"unit":name,"state":f"{enabled}|{active}"})
        elif role.startswith("RETIRED_"):
            active=_run("systemctl","is-active",name,check=False).stdout.strip()
            if active=="active":
                raise RuntimeError(f"RETIRED_UNIT_ACTIVE:{name}")
            verification.append({"unit":name,"state":"retired|inactive"})
        elif item["install"]:
            target=systemd_root / name
            if not target.is_file():
                raise RuntimeError(f"SERVICE_INSTALL_VERIFY_FAILED:{name}")
            verification.append({"unit":name,"state":"installed"})

    for name in legacy_retire:
        active=_run("systemctl","is-active",name,check=False).stdout.strip()
        target=systemd_root / name
        if active=="active":
            raise RuntimeError(f"LEGACY_RETIRED_UNIT_ACTIVE:{name}")
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"LEGACY_RETIRED_UNIT_FILE_PRESENT:{name}")
        verification.append({"unit":name,"state":"legacy-retired|inactive|absent"})

    return {
        "status":"GREEN",
        "changed":changed,
        "verification":verification,
        "count":len(plan),
        "legacy_retired_count":len(legacy_retire),
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=".")
    ap.add_argument("--policy",default="ops/policy/host-control-plane-reconciliation-v2.json")
    ap.add_argument("--systemd-root",default="/etc/systemd/system")
    ap.add_argument("--json-out")
    ap.add_argument("--keep-active-timers-paused", action="store_true")
    args=ap.parse_args()
    root=Path(args.root).resolve()
    policy=json.loads((root / args.policy).read_text(encoding="utf-8"))
    result=apply(root,policy,Path(args.systemd_root),
                 keep_active_timers_paused=args.keep_active_timers_paused)
    payload=json.dumps(result,indent=2,sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload+"\n",encoding="utf-8")
    print(f"POROTA_HOST_CONTROL_PLANE_APPLY=GREEN|units={result['count']}")
    print(f"POROTA_HOST_LEGACY_RETIREMENT=GREEN|units={result['legacy_retired_count']}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())

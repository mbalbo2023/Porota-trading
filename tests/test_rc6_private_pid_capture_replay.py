"""Pure captured-format fixtures and tamper attacks; no native qualification."""
from __future__ import annotations

import base64
import copy
import errno
import hashlib
import os
import stat

import pytest

from scripts import rc6_heavy_test_preflight as capacity
from scripts import rc6_native_namespace_filter as native
from scripts import rc6_private_pid_boundary as boundary
from scripts import rc6_private_pid_capture_replay as replay
from scripts import rc6_privileged_custody as custody
from scripts import rc6_root_actor_seal as seal


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def process(pid, *, parent=1, ticks="200", ns=20, nspids=None, uid=0, group=None, caps=None):
    return {"pid": pid, "parent_pid": parent, "start_ticks": ticks, "pid_namespace_inode": ns,
        "namespace_pids": [pid] if nspids is None else nspids, "uids": [uid] * 4, "gids": [uid] * 4,
        "capabilities": {"CapInh": 0, "CapAmb": 0, "CapPrm": seal.RETAINED_MASK,
            "CapEff": seal.RETAINED_MASK, "CapBnd": seal.RETAINED_MASK} if caps is None else caps,
        "cgroup": "0::/system.slice/" + group, "boot_id": "11111111-1111-1111-1111-111111111111"}


def kernel(pid, *, supervisor=1, uid=0, limit=10, command=None, code=0, hang=False):
    return {"pid": pid, "returncode": code, "timed_out": hang, "wall_seconds": 3 if hang else 0.1,
        "actual_child_reaped": True, "wait4_reaped_pid": pid, "kernel_pre_popen_echild_verified": True,
        "owned_children_exhaustion_verified": True, "process_group_absent_after_reap": True,
        "subreaper_activation_readback_verified": True, "subreaper_restore_attempted": True,
        "subreaper_restoration_readback_verified": True, "remaining_owned_children": [],
        "late_observed_main_reap_irreversible_red": False, "kernel_wait4_zero_observed_irreversible_red": False,
        "residual_descendants_observed": [], "adopted_descendants_reaped": [], "supervisor_errors": [],
        "owned_group_signal_observations": [{"pgid": pid, "signal": number, "outcome": "SENT"}
            for number in (15, 9)] if hang else [], "supervisor_pid": supervisor, "supervisor_native_tid": supervisor,
        "supervisor_kernel_identity_before_spawn": {"Pid": str(supervisor), "Tgid": str(supervisor), "Uid": " ".join([str(uid)] * 4)},
        "launcher_management_deadline_seconds": limit, "sigint_failure_grace_seconds": 0,
        "owned_cleanup_management_bound_seconds": 5, "command": command}


def root_receipt(identity, *, request, hashes, mountinfo_sha256, parent=None):
    owner = request["owner_uid"]
    root = request["namespace_receipt"]["path"]
    plan = seal.build_plan(owner, owner)
    bounds = {"host_pid_namespace_inode": 10, "host_mount_namespace_inode": 11,
        "host_device_number": 100, "host_devpts_device_number": 101,
        "control_root": root, "control_identity": request["namespace_receipt"]["identity"],
        "mountinfo_sha256": mountinfo_sha256,
        "private_proc_readonly": True, "private_devpts_readonly": True, "foreign_mounts_readonly": True,
        "cgroup_readonly": True, "host_boundary_provenance_requires_caller_live_custody": True}
    for index, (key, target, kind) in enumerate((("proc_mount", "/proc", "proc"),
            ("device_mount", "/dev", "tmpfs"), ("devpts_mount", "/dev/pts", "devpts")), start=1):
        bounds[key] = {"id": index + 1, "device": 21 + index, "root": "/", "target": target, "type": kind, "options": ["ro"]}
    count = 1 if parent is None else parent["filters_after"]
    denials = [{"probe": label, "syscall": name, "returncode": -1, "errno": number}
        for label, name, _, number in seal._probe_specs()]
    value = {"schema": seal.SCHEMA, "scope": "ROOT_POST_SETUP_ONLY", "source_binding": {
        "source_sha": request["source_sha"], "source_tree": request["source_tree"]}, "identity": identity,
        "plan": plan, "plan_sha256": digest(native.canonical(plan)), "module_sha256": hashes[custody.ROOT_SEAL_MEMBER],
        "rules_sha256": "3" * 64, "exported_bpf_sha256": "4" * 64, "exported_bpf_bytes": 64,
        "native_architecture": native.SUPPORTED_ARCHITECTURES[0], "seccomp_load_returncode": 0,
        "filters_before": count, "filters_after": count + 1, "tsync_requested": True, "no_new_privileges": True,
        "dumpable": 0, "kernel_bpf_bytes_readback_claimed": False, "capabilities": {
            "CapInh": 0, "CapAmb": 0, "CapPrm": seal.RETAINED_MASK, "CapEff": seal.RETAINED_MASK, "CapBnd": seal.RETAINED_MASK},
        "boundary": bounds, "denial_probes": denials, "ROOT_custody_certified": False,
        "G0_G8_qualification": False, "quota_bootstrap_allowed": False}
    if parent is not None:
        value["inherited"] = {"schema": seal.INHERITANCE_SCHEMA, "identity": identity,
            "parent_identity": parent["identity"], "parent_receipt_sha256": digest(native.canonical(parent)),
            "source_binding": value["source_binding"], "fork_exec_inheritance_observed": True,
            "ROOT_custody_certified": False, "filters_before_own_installation": count,
            "native_denials_before_own_installation": denials}
    return value


class CaptureFormatFixture:
    """Declared format fixture only; no object has in-process native authority."""
    def __init__(self):
        self.binding = {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40,
            "producer": "capacity-probe", "attempt_id": "DECODER_ONLY", "owner_id": "DECODER_ONLY",
            "workload_fingerprint": "c" * 64, "runner_class": "github-hosted/ubuntu-24.04"}
        self.hashes = {name: digest(name.encode()) for name in replay.HASH_MEMBERS}
        self.hashes[custody.MANAGER_MEMBER] = custody.MANAGER_SHA256
        self.nonce = "1" * 32
        self.original = "/work/own/r6-" + base64.urlsafe_b64encode(bytes.fromhex(self.nonce)).decode().rstrip("=")
        claim = {"schema": "porota.rc6.generated-fixture-consumer-binding.v1", "binding": self.binding,
            "path": self.original, "identity": [33, 44, 1001, 1001, 0o700], "mount_id": 55,
            "namespace_nonce": self.nonce, "cleanup_authority_granted": False}
        self.values = {replay.MARKER: {"schema": "porota.rc6.generated-fixture-owner.v1", "binding": self.binding,
            "namespace_nonce": self.nonce, "path": self.original, "identity": claim["identity"],
            "mount_id": 55, "runtime_paths_authorized": False, "real_orders_sent": 0}}
        claim["marker_sha256"] = digest(custody.wire(self.values[replay.MARKER]))
        unit = "rc6-native-" + self.nonce + ".service"
        pid1 = process(1, parent=0, ns=10, ticks="1", group="init.scope")
        pid1.pop("pid_namespace_inode")
        self.request = {"schema": "porota.rc6.root-custody-request.v1", "source_root": "/work/captured-source",
            "source_sha": self.binding["candidate_sha"], "source_tree": self.binding["candidate_tree"], "binding": self.binding,
            "code_hashes": self.hashes, "owner_uid": 1001, "owner_gid": 1001, "namespace_receipt": claim,
            "issuer": {"pid": 4000, "start_ticks": "100", "boot_id": pid1["boot_id"], "pid_namespace_inode": 10},
            "host_PID1": pid1, "host_boundary": {"mount_namespace_inode": 11, "device_number": 100, "devpts_device_number": 101},
            "runtime_seconds": 6}
        self.probe = {**self.request, "runtime_seconds": 45, "boundary_revision": boundary.REVISION, "boundary_host_proc_device": 12}
        self.values.update({"custody-request.json": self.request, "probe-request.json": self.probe})
        self.fin_receipts = {}
        controllers = []
        for index, (guard, prefix) in enumerate(((True, "guardian"), (False, "broker"))):
            name = "rc6-native-" + self.nonce + ("-guard" if guard else "") + ".service"
            request = self.request if guard else self.probe
            actor = process(4001 + index, ns=10, ticks=str(110 + index), group=name,
                caps={"CapInh": 0, "CapAmb": 0, "CapPrm": 1 << 5, "CapEff": 1 << 5, "CapBnd": 1 << 5})
            props = {"RuntimeMaxSec": "6" if guard else "45", "TimeoutStopSec": "2", "KillMode": "control-group",
                "SendSIGKILL": "yes", "FinalKillSignal": "SIGKILL", "PrivateMounts": "yes", "PrivateDevices": "yes",
                "ProtectControlGroups": "yes", "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "read-only",
                "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectKernelLogs": "yes", "Restart": "no",
                "ReadWritePaths": self.original,
                "RestrictAddressFamilies": "AF_INET AF_INET6", "RestrictNamespaces": "~cgroup net user ipc uts",
                "SystemCallErrorNumber": "EPERM", "SystemCallFilter": custody.SYSTEMD_DENIED_FILTER,
                "InaccessiblePaths": "/proc/1"}
            if guard:
                props["LogLevelMax"] = "debug"
            unit_raw = ("[Service]\n" + "\n".join(key + "=" + value for key, value in props.items()) + "\n").encode()
            original = {"path": "/run/systemd/transient/" + name, "sha256": digest(unit_raw),
                "raw_base64": base64.b64encode(unit_raw).decode(), "identity": [66, 77 + index, 0, 0, stat.S_IFREG | 0o644]}
            controller = {"schema": "porota.rc6.pid1-root-controller-live.v1", "unit": name, "actor": actor, "PID1": pid1,
                "host_boundary": self.request["host_boundary"], "mount_namespace_inode": 31 + index,
                "external_PID1_identity_pending_live_ACK": True, "systemd_guard_exists_before_guardian_exec": True,
                "ROOT_actor_boundary_certified": False, "root_controller_claimed_by_metadata_only": False,
                "transient_unit": {**original, **custody.parse_unit_properties(unit_raw)}}
            controllers.append(controller)
            birth = {"schema": "porota.rc6.root-custody-controller-birth.v1", "binding": self.binding,
                "request_sha256": digest(custody.wire(request)), "controller": controller}
            ack = {"schema": "porota.rc6.root-controller-native-pidfd-observed.v1", "binding": self.binding,
                "birth_sha256": digest(custody.wire(birth)), "issuer": request["issuer"], "controller_pid": actor["pid"],
                "actual_controller": custody.public_process_identity(actor), "actual_PID1": pid1,
                "host_boundary": request["host_boundary"], "ROOT_custody_qualified_by_JSON": False}
            self.values[prefix + "-birth.json"] = birth
            self.values[prefix + "-birth.observed.json"] = ack
            self.values[prefix + "-unit-original.json"] = {"schema": "porota.rc6.root-owned-transient-unit-diagnostic.v1",
                "unit": name, "binding": self.binding, "source_sha": request["source_sha"], "source_tree": request["source_tree"],
                "original_unit": original, "scope": "DIAGNOSTIC_ONLY", "ROOT_custody_qualified": False, "ROOT_FIN_claimed": False}
            self.fin_receipts["guardian_actual_FIN" if guard else "controller_actual_FIN"] = {
                "pidfd_exit_observed": True, "PID1_actual_broker_reap_observed": True, "owned_cgroup_empty_or_removed": True,
                "root_actor": actor, "external_PID1_live_kernel_identity": pid1,
                "external_PID1_birth_pidfd_ACK_verified": True, "ROOT_custody_qualified_by_JSON": False}
            source = request["source_root"]
            command = ["sudo", "-n", "--", "systemd-run", "--wait", "--unit=" + name, "--working-directory=" + source,
                "--", "/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-opaque-own-fd", "/usr/bin/python3", "-I", "-B",
                source + "/" + custody.MEMBER, "--guardian", "guard-hang" if guard else "probe", "--request",
                self.original + ("/custody-request.json" if guard else "/probe-request.json"), "--unit", name]
            managed = kernel(4100 + index, supervisor=4000, uid=1001, limit=20 if guard else 60,
                command=command, code=1 if guard else 0)
            self.values[replay.FIN_NAMES[index]] = {"schema": "porota.rc6.generated-fixture-owned-fin.v1",
                "binding": self.binding, "namespace_nonce": self.nonce, "manager_sha256": custody.MANAGER_SHA256,
                "kernel": managed, "actual_owned_fin_closed": True, "phase_green": not guard,
                "global_or_other_producer_FIN_claimed": False, "real_orders_sent": 0}
        guardian = controllers[1]
        self.guardian = guardian
        issuer = process(4000, ns=10, ticks="100", uid=1001, group="runner.scope")
        issuer.pop("pid_namespace_inode")
        host = process(4010, parent=4009, ticks="200", nspids=[4010, 1], group=unit)
        local = process(1, parent=0, ticks="200", group=unit)
        bridge = {"schema": "porota.rc6.original-own-directory-fd-native.v1", "descriptor": 3,
            "identity": claim["identity"], "original_mount_id": 55, "marker_sha256": claim["marker_sha256"],
            "namespace_nonce": self.nonce, "foreign_host_proc_exposed": False}
        mounts = {"old_host_proc_detached": True, "single_readonly_private_proc": True,
            "private_devpts_newinstance_created": True, "foreign_mounts_readonly_required": True, "quota_ancestor_sealed": False}
        mount_rows = ["1 0 0:99 / / ro - ext4 /dev/root ro", "2 1 0:22 / /proc ro - proc proc ro",
            "3 1 0:23 / /dev ro - tmpfs tmpfs ro", "4 3 0:24 / /dev/pts ro - devpts devpts ro",
            "5 1 0:25 / /sys/fs/cgroup ro - cgroup2 cgroup ro", "56 1 0:33 / " + self.original + " rw - ext4 /dev/root rw"]
        mount_raw = ("\n".join(mount_rows) + "\n").encode()
        self.mountinfo_sha256 = digest(mount_raw)
        diagnostic = {"schema": "porota.rc6.private-mount-setup-diagnostic.v1", "scope": "DIAGNOSTIC_ONLY",
            "ROOT_FIN_claimed": False, "ROOT_custody_qualified": False, "status": "SETUP_READBACK_ONLY_NO_ROOT_QUALIFICATION",
            "binding": self.binding, "mode": "probe", "foreign_writable_rows": [], "private_control_before": {
                "identity": claim["identity"], "private_mount_id": 56, "target": self.original}}
        diagnostic["private_control_after"] = copy.deepcopy(diagnostic["private_control_before"])
        for name in ("mountinfo_before", "mountinfo_before_readonly", "mountinfo_after_recursive_readonly", "mountinfo_after"):
            raw = mount_raw if name != "mountinfo_after_recursive_readonly" else mount_raw.replace(
                (self.original + " rw").encode(), (self.original + " ro").encode())
            diagnostic[name] = {"raw_base64": base64.b64encode(raw).decode(), "sha256": digest(raw), "bytes": len(raw)}
        diagnostic["operations"] = [{"operation": operation, "target": target, "returncode": 0, "errno": 0}
            for operation, target in (("old_proc_detach", "/proc"), ("proc_mount", "/proc"),
                ("old_devpts_detach", "/dev/pts"), ("devpts_mount", "/dev/pts"))]
        for readonly in (True, False):
            diagnostic["operations"].append({"operation": "mount_setattr", "syscall_number": 442,
                "descriptor_scope": "AT_FDCWD" if readonly else "FRESH_PRIVATE_CONTROL", "path": "/" if readonly else "",
                "flags": 0x8100 if readonly else 0x1000, "attr_set": 1 if readonly else 0, "attr_clr": 0 if readonly else 1,
                "propagation": 0, "userns_fd": 0, "attribute_bytes": 32, "returncode": 0, "errno": 0})
        mounts["mount_diagnostic"] = {"relative_source": "private-mount-setup.native.json", "sha256": digest(custody.wire(diagnostic)),
            "bytes": len(custody.wire(diagnostic)), "scope": "DIAGNOSTIC_ONLY", "ROOT_FIN_claimed": False, "ROOT_custody_qualified": False}
        born = {"binding": self.binding, "request_sha256": digest(custody.wire(self.probe)), "host_private_PID1": host,
            "original_directory_bridge": bridge, "private_mount_setup": mounts, "host_boundary": request["host_boundary"]}
        mapping = {**born, "schema": "porota.rc6.root-private-pid-guardian-observed.v1", "guardian_actual": guardian["actor"],
            "issuer_actual": issuer, "source_sha": request["source_sha"], "source_tree": request["source_tree"],
            "external_PID1_ACK": self.values["broker-birth.observed.json"]}
        self.values.update({"private-broker-birth.json": born, "private-broker.observed.json": mapping,
            "guardian-bridge.json": bridge, "broker-bridge.json": bridge,
            "guardian-controller.json": {**self.values["broker-birth.json"], "source_sha": request["source_sha"],
                "source_tree": request["source_tree"], "code_hashes": self.hashes, "issuer": self.request["issuer"]},
            "private-mount-setup.native.json": diagnostic})
        denial = {"signal": 0, "errno": errno.ESRCH, "host_pid": 4000, "host_process_actually_live": True}
        private = {"schema": "porota.rc6.root-private-pid-native.v1", "local": local, "host": host, "issuer": issuer,
            "foreign_signal_native_denial": denial, "private_pid_namespace_before_actor_work": True}
        seal_identity = {"pid": 1, "native_tid": 1, "parent_pid": 0, "start_ticks": 200, "pid_namespace_inode": 20,
            "mount_namespace_inode": 21, "cgroup_namespace_inode": 40, "boot_id": pid1["boot_id"]}
        parent_seal = root_receipt(seal_identity, request=self.request, hashes=self.hashes, mountinfo_sha256=self.mountinfo_sha256)
        self.values["private-parent-seal.json"] = parent_seal
        rows = []
        for index, case in enumerate(replay.CASES):
            pid, ticks = 2 + index, str(300 + index)
            actor_birth = process(pid, ticks=ticks, group=unit)
            actor_seal = root_receipt({**seal_identity, "pid": pid, "native_tid": pid, "parent_pid": 1,
                "start_ticks": int(ticks)}, request=self.request, hashes=self.hashes,
                mountinfo_sha256=self.mountinfo_sha256, parent=parent_seal)
            parent = copy.deepcopy(local)
            parent.pop("pid_namespace_inode")
            parent["capabilities"] = parent_seal["capabilities"]
            actor = {"schema": "porota.rc6.root-custody-probe-actor.v1", "case": case,
                "root_before_drop": actor_birth, "ROOT_parent": parent,
                "root_actor_seal": actor_seal, "parent_root_actor_seal_sha256": digest(custody.wire(parent_seal)),
                "root_protected_before_first_probe_instruction": True, "contract_revision_required": True,
                "original_PROC_EACCES_equivalence_approved": False,
                "pid_boundary": {**private, "local": actor_birth}, "native_denials": {
                    "foreign_kernel_pid_limit": {"errno": 30, "denied": True, "write_attempted": False},
                    "setns": {"return": -1, "errno": 1, "denied": True},
                    "AF_UNIX": {"errno": 1, "denied": True}, "foreign_cgroup_writer": {"errno": 30, "denied": True}}}
            if case == "nonroot-hang":
                after = {**actor_birth, "uids": [1001] * 4, "gids": [1001] * 4,
                    "capabilities": dict.fromkeys(actor_birth["capabilities"], 0)}
                plan = native.build_plan()
                filter_receipt = {"schema": native.SCHEMA, "source_binding": parent_seal["source_binding"],
                    "plan": plan, "plan_sha256": digest(native.canonical(plan)), "seccomp_load_returncode": 0,
                    "filters_before": 3, "filters_after": 4, "native_architecture": native.SUPPORTED_ARCHITECTURES[0],
                    "tsync_requested": True, "no_new_privileges": True, "dumpable": 0, "kernel_bpf_bytes_readback_claimed": False,
                    "G0_G8_qualification": False, "exported_bpf_sha256": "4" * 64, "rules_sha256": "5" * 64,
                    "module_sha256": self.hashes["scripts/rc6_native_namespace_filter.py"], "exported_bpf_bytes": 64,
                    "identity": actor_seal["identity"], "denial_probes": [{"probe": label, "syscall": name,
                        "returncode": -1, "errno": number} for label, name, _, number in native._probe_specs()]}
                actor.update(after_drop=after, drop={"uid": 1001, "gid": 1001, "capabilities_zero": True,
                    "no_new_privileges": True, "seccomp_mode": 2, "mount_namespace_inode": 21, "native_filter": filter_receipt})
            command = ["/usr/bin/python3", "-I", "-B", "-c", custody.ROOT_ACTOR_RUNPY_LAUNCHER,
                self.request["source_root"] + "/" + custody.MEMBER, "--actor-case", case,
                "--request", self.original + "/custody-request.json"]
            managed = kernel(pid, limit=1 if "hang" in case else 10, command=command,
                code=-9 if "hang" in case else 73 if case == "failure-before-drop" else 0, hang="hang" in case)
            name = case + ".native.log"
            self.values[name] = actor
            rows.append({"case": case, "actor": actor, "kernel": managed,
                "raw": {"path": name, "sha256": digest(custody.wire(actor)), "bytes": len(custody.wire(actor))}})
        actor = process(8, ticks="400", group=unit, uid=1001, caps=dict.fromkeys(local["capabilities"], 0))
        actor.update(mount_namespace_inode=21, supplementary_groups=[], no_new_privileges=1, seccomp_mode=2, seccomp_filters=4)
        host_actor = process(4020, parent=4010, ticks="400", group=unit, ns=20, nspids=[4020, 8], uid=1001, caps=actor["capabilities"])
        guardian_rows = []
        for index, stage in enumerate(("before", "after"), start=1):
            challenge = {"schema": "porota.rc6.private-pid-challenge.v2", "stage": stage, "nonce": str(index) * 64,
                "binding": self.binding, "request_sha256": digest(custody.wire(self.probe)), "actor": actor}
            row = {"schema": boundary.GUARD_SCHEMA, "stage": stage, "challenge_sha256": digest(custody.wire(challenge)),
                "nonce": challenge["nonce"], "binding": self.binding, "request_sha256": challenge["request_sha256"],
                "issuer_actual": copy.deepcopy(issuer), "guardian_actual": copy.deepcopy(guardian["actor"]),
                "host_actor_actual": copy.deepcopy(host_actor),
                "subject_pidfd_alive": True, "actor_pidfd_alive": True, "host_proc_device": 12, "monotonic_ns": index}
            self.values["boundary-" + stage + ".challenge.json"] = challenge
            self.values["boundary-" + stage + ".guardian.json"] = row
            guardian_rows.append(row)
        native_controls = [{"case": case, "status": "REJECTED", "scope": "ACTUAL_KERNEL_CONTROL",
            "qualification_claimed": False, "error": boundary.NATIVE_CONTROL_ERRORS[case],
            **({"actual_waitpid": True, "actual_exited_pidfd": True} if case == "lost_guardian" else {})}
            for case in boundary.NATIVE_NEGATIVES]
        native_controls[0].update(kernel_identity=guardian["actor"], mountinfo_sha256="d" * 64, proc_device=12)
        native_controls[1].update(kernel_identity={**host, "pid": 1}, mountinfo_sha256="e" * 64, proc_device=12)
        observation = {"schema": boundary.SCHEMA, "revision": boundary.REVISION, "contract_sha256": boundary.contract_sha256(),
            "scope": "SMALL_NATIVE_OBSERVATION_ONLY", "binding": self.binding, "request_sha256": digest(custody.wire(self.probe)),
            "actor": actor, "host_boundary": {"pid_namespace_inode": 10, "mount_namespace_inode": 11, "proc_device": 12},
            "mount_view": {"proc_device": 22, "proc_self": "8", "raw_sha256": self.mountinfo_sha256,
                "rows": [{"id": int(row.split()[0]), "root": "/", "target": row.split()[4], "source": row.split()[-2],
                    "type": row.split()[-3], "options": row.split()[5].split(","), "propagation": []} for row in mount_rows]},
            "descriptors": [{"descriptor": number, "device": 99, "inode": number + 1, "rdevice": os.makedev(1, 3),
                "mode": stat.S_IFCHR if number == 0 else stat.S_IFREG, "flags": os.O_RDONLY if number == 0 else os.O_WRONLY}
                for number in range(3)], "external_before": guardian_rows[0], "external_after": guardian_rows[1],
            "hidden_subject": {"host_pid": 4000, "local_pid_visible": False, "signal_zero_errno": errno.ESRCH,
                "magiclinks": {name: {"errno": errno.ENOENT} for name in boundary.MAGICLINKS}},
            "existing_denials": {name: {"errno": errno.EACCES, "write_performed": False,
                "target_existed": name in ("foreign_kernel", "foreign_cgroup")}
                for name in ("foreign_kernel", "foreign_cgroup", "local_root_root", "local_root_cwd", "local_root_fd", "local_root_ns", "local_root_signal")},
            "native_controls": native_controls[2:], "qualification_claimed": False, "G0_GREEN": False, "real_orders_sent": 0}
        self.values["private-pid-v2.native.log"] = observation
        arguments = {"request": self.probe, "guardian_rows": guardian_rows, "native_pre_controls": native_controls[:2], "bridge": bridge}
        boundary.validate_observation(observation, **arguments)
        command = ["/usr/bin/python3", "-I", "-B", "-c", custody.ROOT_ACTOR_RUNPY_LAUNCHER,
            self.request["source_root"] + "/" + custody.MEMBER, "--boundary-worker", "--request", self.original + "/probe-request.json"]
        evidence = {"observation": observation, "kernel": kernel(8, limit=20, command=command),
            "native_inherited_proc_control": native_controls[1],
            "raw": {"path": "private-pid-v2.native.log", "sha256": digest(custody.wire(observation)), "bytes": len(custody.wire(observation))}}
        state = {"kernel_echild_verified": True, "subreaper_state_before": 0, "subreaper_state_after": 0,
            "scope": "THIS_SUPERVISOR_ONLY; NO_GLOBAL_PROC_SCAN_OR_STATE_MUTATION"}
        proof = {"schema": custody.SCHEMA, "request_sha256": digest(custody.wire(self.probe)), "binding": self.binding,
            "manager_sha256": custody.MANAGER_SHA256, "before": state, "after": copy.deepcopy(state), "cases": rows,
            "controller": {"external": guardian, "private_PID1": private, "unit": unit,
                "ROOT_actor_boundary_certified": True, "root_actor_seal": parent_seal}, "private_pid_boundary_v2": evidence,
            **dict.fromkeys(("ROOT_parent_owns_actual_worker_wait4", "native_probe_proved", "actor_mount_operations_attempted",
                "actor_quota_syscall_probes_attempted", "namespace_setup_mounts_executed", "root_actor_seal_probe_only",
                "contract_revision_required", "proof_only_no_G0_qualification"), True),
            **dict.fromkeys(("backing_allocated", "actor_mount_operations_succeeded", "actor_quota_mutations_succeeded",
                "quota_backing_mounts_attempted", "original_PROC_EACCES_equivalence_approved"), False), "real_orders_sent": 0}
        self.values["private-broker-native.log"] = copy.deepcopy(proof)
        private_command = ["unshare", "--mount", "--propagation", "private", "--pid", "--fork", "--kill-child=SIGKILL",
            "--", "/bin/sh", "-c", custody.BRIDGE_SHELL, "rc6-opaque-own-fd", "/usr/bin/python3", "-I", "-B",
            self.request["source_root"] + "/" + custody.MEMBER, "--private-init", "probe", "--request",
            self.original + "/probe-request.json", "--unit", unit]
        proof["external_guardian"] = {"controller": guardian, "private_broker_kernel": kernel(4009, supervisor=4002, limit=35, command=private_command),
            "private_PID1_actual_mapping": mapping, "private_PID1_native_pidfd_FIN": True,
            "private_broker_raw_sha256": digest(custody.wire(self.values["private-broker-native.log"])),
            "original_manager_sha256": custody.MANAGER_SHA256, "actual_root_FIN_closed": True, "root_phase_green": True}
        evidence.update(status="NATIVE_OBSERVATION_PROVED", contract_sha256=boundary.contract_sha256(),
            **arguments, negative_guards=boundary.adversarial_rejections(observation, **arguments))
        self.values["custody-native.log"] = proof
        self.receipt = {"schema": custody.SCHEMA, "status": "NATIVE_CUSTODY_PROVED", "binding": self.binding,
            "source_sha": self.request["source_sha"], "source_tree": self.request["source_tree"], "code_hashes": self.hashes,
            "native_root_proof": proof, **self.fin_receipts, "capture_root": "/work/outer/revision-custody-raw",
            "proof_only_no_G0_qualification": True, "contract_revision_required": True,
            "original_PROC_EACCES_equivalence_approved": False, "real_orders_sent": 0,
            "cleanup": {"schema": "porota.rc6.generated-fixture-scoped-cleanup.v1", "binding": self.binding,
                "namespace_nonce": self.nonce, "owned_fin_kind": "NATIVE_WAIT4_OWNED_FIN",
                "capture_path": "/work/outer/revision-custody-raw", "retained_entries_before": len(replay.REQUIRED) + 1,
                "post_fin_directory_permission_transitions_sha256": digest(b""),
                **dict.fromkeys(("namespace_removed", "actual_owned_fin_closed", "phase_green", "directory_inode_removed",
                    "original_namespace_removed", "original_path_is_nonexistent"), True),
                **dict.fromkeys(("path_replaced_by_authenticated_numbering_control", "link_targets_followed", "global_cleanup_claimed", "runtime_paths_authorized"), False),
                "file_modes_changed_for_retirement": 0, "foreign_paths_removed": 0, "real_orders_sent": 0}}
        self.seal()

    def seal(self):
        self.raws = {name: custody.wire(value) for name, value in self.values.items()}
        self.raws["guardian-native.log"] = b"DECODER_ONLY original failed guard stderr\n"
        rows = [{"relative_source": name, "capture_file": str(index).zfill(4) + ".raw", "bytes": len(self.raws[name]),
            "sha256": digest(self.raws[name]), "source_identity": [33, 100 + index, 1001, 1001, 0o600, 1, len(self.raws[name]), 10, 10]}
            for index, name in enumerate(sorted(self.raws))]
        self.manifest = {"schema": "porota.rc6.generated-fixture-required-capture.v1", "binding": self.binding,
            "namespace_nonce": self.nonce, "files": rows, "total_bytes": sum(map(len, self.raws.values())),
            "actual_owned_fin_closed": True, "owned_fin_kind": "NATIVE_WAIT4_OWNED_FIN", "phase_green": True,
            "kernel_sha256": capacity.digest(self.values[replay.FIN_NAMES[1]]["kernel"]), "real_orders_sent": 0}
        self.publish_manifest()

    def publish_manifest(self):
        self.raws["manifest.json"] = custody.wire(self.manifest)
        value = digest(self.raws["manifest.json"])
        self.receipt["capture_manifest_sha256"] = value
        self.receipt["cleanup"]["capture_manifest_sha256"] = value

    def validate(self):
        return replay.validate_capture(self.receipt, binding=self.binding, code_hashes=self.hashes, read_original=self.raws.__getitem__)

    def synchronize_original_private_bytes(self):
        """Coherent captured-format attack; this does not authenticate an artifact."""
        proof = self.receipt["native_root_proof"]
        for row in proof["cases"]:
            raw = custody.wire(row["actor"])
            row["raw"] = {"path": row["case"] + ".native.log", "sha256": digest(raw), "bytes": len(raw)}
        private = {key: copy.deepcopy(value) for key, value in proof.items() if key != "external_guardian"}
        private["private_pid_boundary_v2"] = {key: copy.deepcopy(proof["private_pid_boundary_v2"][key])
            for key in ("observation", "kernel", "native_inherited_proc_control", "raw")}
        self.values["private-broker-native.log"] = private
        proof["external_guardian"]["private_broker_raw_sha256"] = digest(custody.wire(private))


@pytest.fixture
def captured():
    return CaptureFormatFixture()


def test_complete_captured_closure_grants_no_live_authority_or_green(captured, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("PURE_CAPTURE_REPLAY_MUST_NOT_TOUCH_NATIVE_AUTHORITY_OR_KERNEL")
    for name in ("kernel_process", "public_kernel_process", "prove_custody", "verify_live_controller"):
        monkeypatch.setattr(custody, name, forbidden)
    monkeypatch.setattr(boundary, "boundary_witness", forbidden)
    monkeypatch.setattr(boundary, "validate_witness", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    result = captured.validate()
    assert result["status"] == "CAPTURED_PRIVATE_PID_V2_CLOSURE_VERIFIED"
    assert result["scope"] == "DOCUMENTARY_CLOSURE_NOT_LIVE_AUTHORITY"
    assert result["original_manager_sha256"] == custody.MANAGER_SHA256
    assert result["native_custody_receipt_sha256"] == digest(custody.wire(captured.receipt))
    assert result["boundary_observation_sha256"] == digest(custody.wire(captured.receipt["native_root_proof"]["private_pid_boundary_v2"]))
    assert result["contract_sha256"] == boundary.contract_sha256()
    assert result["root_cases"] == list(replay.CASES)
    assert all(result[name] is False for name in ("in_process_witness_minted", "privileged_launch_authorized",
        "owner_contract_acceptance_granted", "G0_GREEN", "qualification_claimed"))
    assert result["real_orders_sent"] == 0


@pytest.mark.parametrize("name", sorted(replay.REQUIRED))
def test_every_required_original_is_mandatory(captured, name):
    del captured.raws[name]
    with pytest.raises(ValueError, match="REQUIRED_SCHEMA_MISSING_OR_INVALID"):
        captured.validate()


@pytest.mark.parametrize("name", ("manifest.json", "private-broker-native.log", "private-pid-v2.native.log",
    "guardian-native.log", "boundary-before.challenge.json", replay.FIN_NAMES[0], replay.MARKER))
def test_raw_hash_tamper_fails_before_projection(captured, name):
    captured.raws[name] += b" "
    with pytest.raises(ValueError, match="INNER_MANIFEST_REBOUND|ORIGINAL_BYTES_MISSING_OR_CHANGED"):
        captured.validate()


@pytest.mark.parametrize("defect,error", [("source", "RECEIPT_FORGED_OR_REBOUND"),
    ("code_hash", "EXACT_SOURCE_CODE_HASHES_REQUIRED"), ("capture_path", "OUTER_NAMESPACE_PREFIX_CHANGED"),
    ("fin_ack", "EXTERNAL_ROOT_FIN_REBOUND"), ("cleanup_nonce", "OWN_NONCE_CLEANUP_REBOUND"),
    ("cleanup_foreign", "OWN_NONCE_CLEANUP_REBOUND"), ("root_missing_case", "PRIVATE_BROKER_ORIGINAL_BYTES_REBOUND"),
    ("negative_matrix", "NEGATIVE_GUARDS_CHANGED_OR_MISSING"), ("guardian_bytes", "ACTUAL_CHALLENGE_GUARDIAN_BYTES_REBOUND"),
    ("challenge_nonce", "ACTUAL_CHALLENGE_GUARDIAN_BYTES_REBOUND"), ("raw_observer", "PRIVATE_BROKER_ORIGINAL_BYTES_REBOUND"),
    ("manager_fin", "ORIGINAL_WAIT4_FIN_INVALID"), ("enoent_only", "PIDV2_EXISTING_TARGET_NATIVE_DENIAL_REQUIRED")])
def test_coherent_rehashed_evidence_still_requires_full_custody_linkage(captured, defect, error):
    proof = captured.receipt["native_root_proof"]
    evidence = proof["private_pid_boundary_v2"]
    if defect == "source": captured.receipt["source_sha"] = "f" * 40
    elif defect == "code_hash": captured.hashes[custody.MANAGER_MEMBER] = "f" * 64
    elif defect == "capture_path": captured.receipt["capture_root"] = "/work/outer/elsewhere"
    elif defect == "fin_ack": captured.receipt["controller_actual_FIN"]["root_actor"] = {**captured.guardian["actor"], "start_ticks": "999"}
    elif defect == "cleanup_nonce": captured.receipt["cleanup"]["namespace_nonce"] = "f" * 32
    elif defect == "cleanup_foreign": captured.receipt["cleanup"]["foreign_paths_removed"] = 1
    elif defect == "root_missing_case": proof["cases"] = proof["cases"][:-1]
    elif defect == "negative_matrix": evidence["negative_guards"] = evidence["negative_guards"][:-1]
    elif defect == "guardian_bytes": captured.values["boundary-after.guardian.json"] = {**evidence["guardian_rows"][1], "actor_pidfd_alive": False}
    elif defect == "challenge_nonce": captured.values["boundary-after.challenge.json"]["nonce"] = "f" * 64
    elif defect == "raw_observer": evidence["raw"]["sha256"] = "f" * 64
    elif defect == "manager_fin": captured.values[replay.FIN_NAMES[1]]["kernel"]["actual_child_reaped"] = False
    elif defect == "enoent_only":
        evidence["observation"]["existing_denials"] = {}
        captured.values["private-pid-v2.native.log"] = evidence["observation"]
        evidence["raw"] = {"path": "private-pid-v2.native.log", "sha256": digest(custody.wire(evidence["observation"])),
            "bytes": len(custody.wire(evidence["observation"]))}
        private = captured.values["private-broker-native.log"]["private_pid_boundary_v2"]
        private["observation"] = evidence["observation"]
        private["raw"] = evidence["raw"]
        proof["external_guardian"]["private_broker_raw_sha256"] = digest(custody.wire(captured.values["private-broker-native.log"]))
    captured.seal()
    with pytest.raises(ValueError, match=error):
        captured.validate()


@pytest.mark.parametrize("defect,error", [("missing", "REQUIRED_ORIGINAL_FILES_MISSING"),
    ("duplicate", "REQUIRED_ORIGINAL_FILES_MISSING"), ("inode", "SOURCE_INODE_REBOUND"),
    ("foreign_owner", "SOURCE_IDENTITY_FOREIGN"), ("capture_alias", "FILE_RECORD_INVALID"),
    ("bytes_boolean", "FILE_RECORD_INVALID"), ("path_escape", "REQUIRED_ORIGINAL_FILES_MISSING")])
def test_inner_manifest_cannot_hide_or_rebind_original_files(captured, defect, error):
    rows = captured.manifest["files"]
    if defect == "missing": rows.pop()
    elif defect == "duplicate": rows[-1] = copy.deepcopy(rows[0])
    elif defect == "inode": rows[-1]["source_identity"][:2] = rows[0]["source_identity"][:2]
    elif defect == "foreign_owner": rows[-1]["source_identity"][2] = 2000
    elif defect == "capture_alias": rows[-1]["capture_file"] = "../foreign.raw"
    elif defect == "bytes_boolean": rows[-1]["bytes"] = True
    elif defect == "path_escape": rows[-1]["relative_source"] = "../../foreign"
    captured.publish_manifest()
    with pytest.raises(ValueError, match=error):
        captured.validate()


def test_duplicate_json_keys_are_rejected_even_when_outer_hashes_match(captured):
    raw = captured.raws["manifest.json"]
    raw = b'{"binding":{},' + raw[1:]
    captured.raws["manifest.json"] = raw
    captured.receipt["capture_manifest_sha256"] = digest(raw)
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        captured.validate()


def test_boolean_receipt_cannot_replace_native_captured_files(captured):
    with pytest.raises(ValueError, match="RECEIPT_FORGED_OR_REBOUND"):
        replay.validate_capture({"status": "NATIVE_CUSTODY_PROVED", "G0_GREEN": True},
            binding=captured.binding, code_hashes=captured.hashes, read_original=captured.raws.__getitem__)


@pytest.mark.parametrize("defect,error", [("root_term_kill", "ROOT_TERM2_KILL_FIN_MISSING"),
    ("root_birth", "ROOT_CASE_BIRTH_REBOUND"), ("root_wait4_supervisor", "SUPERVISOR_IDENTITY_REBOUND"),
    ("root_actor_command", "ORIGINAL_ACTOR_COMMAND_CHANGED"), ("private_parent_wait4", "PRIVATE_PID1_PARENT_WAIT4_REBOUND"),
    ("challenge_lost_guardian", "PIDV2_GUARDIAN_ACK_FORGED_OR_LOST"), ("seal_host_binding", "PARENT_ROOT_SEAL_REBOUND"),
    ("unrelated_request_flag", "REQUEST_SOURCE_CASE_BINDING_REBOUND")])
def test_rehashed_root_case_or_guardian_cannot_replace_original_kernel_custody(captured, defect, error):
    proof = captured.receipt["native_root_proof"]
    if defect == "root_term_kill": proof["cases"][2]["kernel"]["owned_group_signal_observations"].pop()
    elif defect == "root_birth": proof["cases"][0]["kernel"]["pid"] = 90; proof["cases"][0]["kernel"]["wait4_reaped_pid"] = 90
    elif defect == "root_wait4_supervisor": proof["cases"][0]["kernel"]["supervisor_kernel_identity_before_spawn"]["Uid"] = "1001 1001 1001 1001"
    elif defect == "root_actor_command": proof["cases"][0]["kernel"]["command"][-1] = captured.original + "/probe-request.json"
    elif defect == "private_parent_wait4": proof["external_guardian"]["private_broker_kernel"]["pid"] = 91; proof["external_guardian"]["private_broker_kernel"]["wait4_reaped_pid"] = 91
    elif defect == "challenge_lost_guardian":
        proof["private_pid_boundary_v2"]["guardian_rows"][1]["subject_pidfd_alive"] = False
        observation = proof["private_pid_boundary_v2"]["observation"]
        proof["private_pid_boundary_v2"]["raw"] = {"path": "private-pid-v2.native.log",
            "sha256": digest(custody.wire(observation)), "bytes": len(custody.wire(observation))}
    elif defect == "seal_host_binding": captured.values["private-parent-seal.json"]["boundary"]["host_pid_namespace_inode"] = 99
    elif defect == "unrelated_request_flag": captured.request["privileged_launch_authorized"] = True
    captured.synchronize_original_private_bytes()
    captured.seal()
    with pytest.raises(ValueError, match=error):
        captured.validate()


def test_callback_receives_only_fixed_relative_capture_member_names(captured):
    calls = []
    def original(name):
        assert name == "manifest.json" or name in replay.REQUIRED
        assert "/" not in name
        calls.append(name)
        return captured.raws[name]
    replay.validate_capture(captured.receipt, binding=captured.binding, code_hashes=captured.hashes, read_original=original)
    assert calls == ["manifest.json", *sorted(replay.REQUIRED)]

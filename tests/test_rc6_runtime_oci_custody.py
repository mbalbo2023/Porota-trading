"""Offline adversarial contract for the single three-role Docker birth custodian.

No test in this module opens Docker, sudo, SSH, systemd, or a host socket.
"""

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


from scripts import rc6_runtime_oci_custody as custody


SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64
DIGEST = "sha256:" + "d" * 64
GEN = "e" * 32
NEW_SHA = "f" * 40
NEW_TREE = "0" * 40
NEW_IMAGE = "sha256:" + "e" * 64
NEW_DIGEST = "sha256:" + "f" * 64
NEXT_GEN = "f" * 32
DAEMON = "daemon-pin-123"
ROLES = {"dashboard": "/porota_production_dashboard",
         "observer": "/porota_production_observer",
         "critical": "/porota_critical_approval_rc6"}


def sample_intent():
    return {
        "schema_version": 1, "candidate_sha": NEW_SHA, "candidate_tree": NEW_TREE,
        "image_id": NEW_IMAGE, "artifact_digest": NEW_DIGEST,
        "generation": NEXT_GEN, "run_id": "42593199013", "run_attempt": "1",
        "daemon_id": DAEMON, "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "prior_receipt_sha256": hashlib.sha256(json.dumps(sample_current(),
             sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def sample_current():
    d = {"schema_version": 1, "candidate_sha": SHA, "candidate_tree": TREE,
         "image_id": IMAGE, "artifact_digest": DIGEST,
         "generation": GEN, "run_id": "42213911331", "run_attempt": "1",
         "daemon_id": DAEMON, "paper_mode": "PRODUCTION_PAPER",
         "real_orders_allowed": False, "sealed": True,
         "birth_intent_sha256": "0" * 64, "roles": {}}
    for idx, role in enumerate(ROLES, start=1):
        d["roles"][role] = {
            "cid": str(idx) * 64, "image_id": IMAGE, "created": f"2026-11-09T12:0{idx}:00Z",
            "name": f"/porota_rc6_{role}_g_{GEN}",
        }
    return d


def sample_legacy_current():
    receipt = sample_current()
    for role, name in ROLES.items():
        receipt["roles"][role]["name"] = name
    return receipt


def inspected(role, receipt=None):
    receipt = receipt or sample_current()
    r = receipt["roles"][role]
    return {"Id": r["cid"], "Image": r["image_id"],
            "Name": r["name"], "Created": r["created"],
            "State": {"Status": "running"},
            "Config": {"Labels": {"porota.rc6.role": role,
                                  "porota.rc6.generation": receipt["generation"],
                                  "porota.rc6.source_sha": receipt["candidate_sha"],
                                  "porota.rc6.birth_name": r["name"][1:]}}}


def newly_inspected(role, cid):
    intent = sample_intent()
    name = f"/porota_rc6_{role}_g_{intent['generation']}"
    return {"Id": cid, "Image": intent["image_id"], "Name": name,
            "Created": "2026-11-09T12:15:00Z", "State": {"Status": "running"},
            "Config": {"Labels": {
                "porota.rc6.role": role,
                "porota.rc6.generation": intent["generation"],
                "porota.rc6.source_sha": intent["candidate_sha"],
                "porota.rc6.run_id": intent["run_id"],
                "porota.rc6.run_attempt": intent["run_attempt"],
                "porota.rc6.artifact_digest": intent["artifact_digest"],
                "porota.rc6.birth_name": name[1:],
            }}}


def complete_pending():
    intent = sample_intent()
    names = list(ROLES)
    roles = {role: {"cid": str(i + 4) * 64,
                    "image_id": NEW_IMAGE, "created": "2026-11-09T12:15:00Z",
                    "name": f"/porota_rc6_{role}_g_{NEXT_GEN}"}
             for i, role in enumerate(names)}
    return {"generation": NEXT_GEN,
            "intent_digest": hashlib.sha256(json.dumps(intent, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "prepared_roles": names, "roles": roles}


def newly_sealed():
    intent = sample_intent()
    current = {key: value for key, value in intent.items()
               if key != "prior_receipt_sha256"}
    current["sealed"] = True
    current["birth_intent_sha256"] = hashlib.sha256(json.dumps(
        intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    current["roles"] = complete_pending()["roles"]
    return current


def sample_genesis():
    """External host authority supplies this file; the module never writes it."""
    intent = sample_intent()
    intent.pop("prior_receipt_sha256")
    return {**intent, "state": "LEGACY_UNTRUSTED",
            "authority": "INDEPENDENT_HOST_CONTROL_PLANE"}


def generated_name(role, generation=NEXT_GEN):
    return f"/porota_rc6_{role}_g_{generation}"


@contextmanager
def external_genesis_root():
    """OFFHOST root-owned equivalent, preserving the real private file checks."""
    read = custody._read_regular_file
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        root.chmod(0o700)
        (root / "generations").mkdir(mode=0o700)
        lock = root / "lock"
        lock.write_bytes(b"host lock")
        lock.chmod(0o600)

        def read_locally(path, *, limit=custody.MAX_RECEIPT_BYTES, **_ignored):
            return read(path, required_uid=os.getuid(), limit=limit)

        with patch.object(custody, "TRUSTED_ROOT", root), \
             patch.object(custody, "ROOT_UID", os.getuid()), \
             patch.object(custody, "_read_regular_file", side_effect=read_locally), \
             patch.object(custody, "_verify_root_directory"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON):
            yield root


def host_receipt(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    path.chmod(0o600)


def record_genesis_birth(session, role, cid, *, state="created"):
    path = session.prepare_birth(role)
    path.write_text(cid)
    path.chmod(0o600)
    observed = newly_inspected(role, cid)
    observed["State"]["Status"] = state
    observed["Created"] = datetime.now(timezone.utc).isoformat()
    with patch.object(custody, "_inspect_exact_cid", return_value=observed):
        session.record_birth(role, cid)
    return observed


def external_critical_attestation(intent, observed):
    return {
        "schema_version": 1, "state": "CRITICAL_EXCLUSIVE",
        "authority": "INDEPENDENT_HOST_CONTROL_PLANE",
        "generation": intent["generation"], "candidate_sha": intent["candidate_sha"],
        "run_id": intent["run_id"], "run_attempt": intent["run_attempt"],
        "daemon_id": intent["daemon_id"], "image_id": intent["image_id"],
        "newborn_cid": observed["Id"], "newborn_created": observed["Created"],
        "attested_at": datetime.now(timezone.utc).isoformat(),
        "legacy_critical_quiesced": True, "systemd_unit_quiesced": True,
        "single_consumer_exclusive": True,
    }


def complete_genesis_births(session, root):
    """Fresh three births only; the critical starts after host exclusivity."""
    births = {}
    for idx, role in enumerate(ROLES, start=4):
        births[role] = record_genesis_birth(
            session, role, str(idx) * 64,
            state="created" if role == "critical" else "running")
    critical = births["critical"]
    host_receipt(root / "generations" / NEXT_GEN / "critical_exclusivity.json",
                 external_critical_attestation(sample_genesis(), critical))
    running = {**critical, "State": {"Status": "running"}}
    with patch.object(custody, "_inspect_exact_cid", side_effect=[critical, running]), \
         patch.object(custody, "_docker_run", return_value=SimpleNamespace(stdout="")):
        session.authorize_newborn_start("critical")
    births["critical"] = running
    return births


def confirmed_critical_start_journal():
    event = {"id": "9" * 32, "role": "critical", "operation": "start",
             "cid": "6" * 64, "timestamp": "2026-11-09T12:15:01Z"}
    return {"events": [{**event, "phase": "REQUESTED"},
                       {**event, "phase": "CONFIRMED"}]}


class CustodyOfflineTests(unittest.TestCase):
    def test_legacy_canonical_three_role_current_cannot_stop_by_retro_inspect(self):
        legacy = sample_legacy_current()
        legacy_intent = sample_intent()
        legacy_intent["prior_receipt_sha256"] = custody._canonical_digest(legacy)
        original = inspected("critical", legacy)
        stopped = {**original, "State": {"Status": "exited"}}
        with patch.object(custody, "_read_trusted_current", return_value=legacy), \
             patch.object(custody, "_read_trusted_intent", return_value=legacy_intent), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_inspect_exact_cid", side_effect=[original, stopped]), \
             patch.object(custody, "_load_journal", return_value={"events": []}), \
             patch.object(custody, "_journal_event"), \
             patch.object(custody, "_docker_run", return_value=SimpleNamespace(
                 stdout="")) as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("critical", "stop")
            docker.assert_not_called()

    def test_sealed_current_verification_requires_exact_original_birth_cidfile(self):
        current = sample_current()
        intent = sample_intent()
        with patch.object(custody, "_read_trusted_current", return_value=current), \
             patch.object(custody, "_read_trusted_intent", return_value=intent), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_read_birth_cidfile", side_effect=
                          custody.CustodyHold("BIRTH_CIDFILE_MISSING")), \
             patch.object(custody, "_inspect_exact_cid") as inspect, \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().verify_role("observer")
            inspect.assert_not_called()
            docker.assert_not_called()

    def test_sealed_critical_requires_confirmed_birth_start_journal(self):
        current = sample_current()
        intent = sample_intent()
        pending = {"generation": current["generation"],
                   "intent_digest": current["birth_intent_sha256"],
                   "prepared_roles": list(ROLES), "roles": current["roles"]}
        with patch.object(custody, "_read_trusted_current", return_value=current), \
             patch.object(custody, "_read_trusted_intent", return_value=intent), \
             patch.object(custody, "_read_birth_cidfile", side_effect=lambda _, role:
                          current["roles"][role]["cid"]), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_load_journal", return_value={"events": []}), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid") as inspect, \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().verify_role("critical")
            inspect.assert_not_called()
            docker.assert_not_called()

    def test_sealed_critical_restart_denied_without_fresh_exclusivity_fencing(self):
        """A sealed old critical CID cannot be restarted via the generic API."""
        current = sample_current()
        stopped = inspected("critical", current)
        stopped["State"]["Status"] = "exited"
        restarted = {**stopped, "State": {"Status": "running"}}
        with patch.object(custody, "_read_trusted_current", return_value=current), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", side_effect=[
                 stopped, restarted]) as inspect, \
             patch.object(custody, "_load_journal", return_value={"events": []}), \
             patch.object(custody, "_journal_event") as journal, \
             patch.object(custody, "_docker_run", return_value=SimpleNamespace(
                 stdout="")) as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("critical", "start")
            inspect.assert_not_called()
            journal.assert_not_called()
            docker.assert_not_called()

    def test_sealed_critical_old_requested_start_cannot_be_reconciled(self):
        """A preexisting REQUESTED event is not fresh exclusivity evidence."""
        current = sample_current()
        critical_cid = current["roles"]["critical"]["cid"]
        op_id = "f" * 32
        requested = {"events": [{"id": op_id, "phase": "REQUESTED",
                                "role": "critical", "operation": "start",
                                "cid": critical_cid}]}
        with patch.object(custody, "_read_trusted_current", return_value=current), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=None), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=inspected(
                 "critical", current)) as inspect, \
             patch.object(custody, "_load_journal", return_value=requested), \
             patch.object(custody, "_journal_event") as journal, \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().reconcile_operation(op_id)
            inspect.assert_not_called()
            journal.assert_not_called()
            docker.assert_not_called()

    def test_sealed_observer_exact_cid_restart_remains_allowed(self):
        current = sample_current()
        observer_cid = current["roles"]["observer"]["cid"]
        stopped = inspected("observer", current)
        stopped["State"]["Status"] = "exited"
        restarted = {**stopped, "State": {"Status": "running"}}
        with patch.object(custody, "_read_trusted_current", return_value=current), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", side_effect=[
                 stopped, restarted]), \
             patch.object(custody, "_load_journal", return_value={"events": []}), \
             patch.object(custody, "_journal_event"), \
             patch.object(custody, "_docker_run", return_value=SimpleNamespace(
                 stdout="")) as docker:
            custody.begin_attempt().authorize_operation("observer", "start")
            docker.assert_called_once_with(
                ("docker", "start", observer_cid), capture=False)

    def test_genesis_requires_explicit_external_legacy_untrusted_receipt(self):
        with external_genesis_root() as root, \
             patch.object(custody, "_docker_run") as docker:
            self.assertIsNotNone(getattr(custody, "begin_genesis", None))
            with self.assertRaises(custody.CustodyHold):
                custody.begin_genesis()
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            self.assertIsInstance(session, custody.CustodySession)
            self.assertFalse((root / "current.json").exists())
            docker.assert_not_called()

    def test_genesis_never_adopts_or_mutates_legacy_role(self):
        with external_genesis_root() as root, \
             patch.object(custody, "_docker_run") as docker:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            with self.assertRaises(custody.CustodyHold):
                session.verify_role("critical")
            with self.assertRaises(custody.CustodyHold):
                session.authorize_operation("observer", "stop")
            docker.assert_not_called()

    def test_genesis_rejects_any_preexisting_current_or_intent(self):
        with external_genesis_root() as root, \
             patch.object(custody, "_docker_run") as docker:
            host_receipt(root / "genesis.json", sample_genesis())
            host_receipt(root / "current.json", sample_current())
            with self.assertRaises(custody.CustodyHold):
                custody.begin_genesis()
            (root / "current.json").unlink()
            host_receipt(root / "intent.json", sample_intent())
            with self.assertRaises(custody.CustodyHold):
                custody.begin_genesis()
            docker.assert_not_called()

    def test_genesis_rejects_untrusted_marker_symlink_and_wrong_modes(self):
        with external_genesis_root() as root, \
             patch.object(custody, "_docker_run") as docker:
            path = root / "genesis.json"
            bad = sample_genesis()
            bad["state"] = "TRUSTED_FROM_NAME"
            host_receipt(path, bad)
            with self.assertRaises(custody.CustodyHold):
                custody.begin_genesis()
            path.unlink()
            target = root / "foreign"
            host_receipt(target, sample_genesis())
            path.symlink_to(target)
            with self.assertRaises(custody.CustodyHold):
                custody.begin_genesis()
            path.unlink()
            host_receipt(path, sample_genesis())
            path.chmod(0o666)
            with self.assertRaises(custody.CustodyHold):
                custody.begin_genesis()
            docker.assert_not_called()

    def test_genesis_birth_names_are_deterministic_and_collision_free(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            self.assertIsNotNone(getattr(session, "birth_name", None))
            for role in ROLES:
                with self.subTest(role=role):
                    cli_name = session.birth_name(role)
                    self.assertEqual("/" + cli_name, generated_name(role))
                    self.assertNotEqual("/" + cli_name, ROLES[role])
                    self.assertRegex(cli_name, r"^porota_rc6_[a-z]+_g_[0-9a-f]{32}$")

    def test_genesis_cannot_adopt_canonical_named_legacy_as_newborn(self):
        with external_genesis_root() as root, \
             patch.object(custody, "_docker_run") as docker:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            path = session.prepare_birth("observer")
            path.write_text("4" * 64)
            path.chmod(0o600)
            # Even labels/cidfile cannot authorize an inherited canonical name.
            observed = newly_inspected("observer", "4" * 64)
            observed["Name"] = ROLES["observer"]
            with patch.object(custody, "_inspect_exact_cid", return_value=observed):
                with self.assertRaises(custody.CustodyHold):
                    session.record_birth("observer", "4" * 64)
            self.assertEqual(custody._read_pending(
                custody._genesis_intent(sample_genesis()))["roles"], {})
            docker.assert_not_called()

    def test_created_newborn_start_is_journaled_and_uses_only_exact_cid(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            created = record_genesis_birth(session, "observer", "4" * 64)
            running = {**created, "State": {"Status": "running"}}
            with patch.object(custody, "_inspect_exact_cid", side_effect=[
                    created, running]), \
                 patch.object(custody, "_docker_run", return_value=SimpleNamespace(
                     stdout="")) as docker:
                self.assertIsNotNone(getattr(session, "authorize_newborn_start", None))
                session.authorize_newborn_start("observer")
            docker.assert_called_once_with(("docker", "start", "4" * 64), capture=False)
            journal = custody._load_journal(custody._genesis_intent(sample_genesis()))
            self.assertEqual([e["phase"] for e in journal["events"]],
                             ["REQUESTED", "CONFIRMED"])
            self.assertTrue(all(e["cid"] == "4" * 64 for e in journal["events"]))

    def test_critical_birth_requires_independent_exclusivity_before_docker_start(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            created = record_genesis_birth(session, "critical", "6" * 64)
            with patch.object(custody, "_inspect_exact_cid", return_value=created), \
                 patch.object(custody, "_docker_run") as docker:
                with self.assertRaises(custody.CustodyHold):
                    session.authorize_newborn_start("critical")
                docker.assert_not_called()
            self.assertFalse((root / "generations" / NEXT_GEN /
                              "operations.json").exists())

    def test_critical_running_birth_cannot_skip_exclusive_created_start(self):
        with external_genesis_root() as root, \
             patch.object(custody, "_docker_run") as docker:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            with self.assertRaises(custody.CustodyHold):
                record_genesis_birth(session, "critical", "6" * 64,
                                     state="running")
            docker.assert_not_called()

    def test_critical_created_to_running_requires_fresh_exact_host_attestation(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            created = record_genesis_birth(session, "critical", "6" * 64)
            gate = root / "generations" / NEXT_GEN / "critical_exclusivity.json"
            host_receipt(gate, external_critical_attestation(sample_genesis(), created))
            running = {**created, "State": {"Status": "running"}}
            with patch.object(custody, "_inspect_exact_cid", side_effect=[
                    created, running]), \
                 patch.object(custody, "_docker_run", return_value=SimpleNamespace(
                     stdout="")) as docker:
                session.authorize_newborn_start("critical")
            docker.assert_called_once_with(("docker", "start", "6" * 64), capture=False)
            self.assertEqual(custody._load_journal(custody._genesis_intent(
                sample_genesis()))["events"][-1]["phase"], "CONFIRMED")

    def test_critical_attestation_wrong_cid_or_stale_remains_hold(self):
        for corruption in ("wrong_cid", "stale", "symlink"):
            with self.subTest(corruption=corruption), external_genesis_root() as root:
                host_receipt(root / "genesis.json", sample_genesis())
                session = custody.begin_genesis()
                created = record_genesis_birth(session, "critical", "6" * 64)
                gate = root / "generations" / NEXT_GEN / "critical_exclusivity.json"
                proof = external_critical_attestation(sample_genesis(), created)
                if corruption == "wrong_cid":
                    proof["newborn_cid"] = "7" * 64
                elif corruption == "stale":
                    proof["attested_at"] = "2020-01-01T00:00:00Z"
                if corruption == "symlink":
                    target = root / "attacker_payload"
                    host_receipt(target, proof)
                    gate.symlink_to(target)
                else:
                    host_receipt(gate, proof)
                with patch.object(custody, "_inspect_exact_cid", return_value=created), \
                     patch.object(custody, "_docker_run") as docker:
                    with self.assertRaises(custody.CustodyHold):
                        session.authorize_newborn_start("critical")
                    docker.assert_not_called()

    def test_genesis_seals_three_new_generational_births_then_normal_attempt(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            births = complete_genesis_births(session, root)
            by_cid = {record["Id"]: record for record in births.values()}
            with patch.object(custody, "_inspect_exact_cid",
                              side_effect=lambda cid: by_cid[cid]):
                result = session.seal_generation()
                self.assertEqual(result["role_count"], 3)
                current = custody._read_trusted_current()
                self.assertEqual(current["generation"], NEXT_GEN)
                self.assertEqual(set(current["roles"]), set(ROLES))
                self.assertTrue(all(current["roles"][role]["name"] ==
                                    generated_name(role) for role in ROLES))
                successor = sample_intent()
                successor["generation"] = "a" * 32
                successor["candidate_sha"] = "b" * 40
                successor["prior_receipt_sha256"] = custody._canonical_digest(current)
                host_receipt(root / "intent.json", successor)
                next_session = custody.begin_attempt()
                self.assertEqual(next_session.verify_role("critical")["Id"], "6" * 64)
            self.assertTrue((root / "genesis.json").exists())

    def test_real_seal_cannot_restart_critical_using_old_exclusivity_attestation(self):
        """Complete trusted birth does not grant perpetual critical restart."""
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            births = complete_genesis_births(session, root)
            by_cid = {record["Id"]: record for record in births.values()}
            with patch.object(custody, "_inspect_exact_cid",
                              side_effect=lambda cid: by_cid[cid]):
                session.seal_generation()
            current = custody._read_trusted_current()
            successor = sample_intent()
            successor["generation"] = "a" * 32
            successor["candidate_sha"] = "b" * 40
            successor["prior_receipt_sha256"] = custody._canonical_digest(current)
            host_receipt(root / "intent.json", successor)
            self.assertTrue((root / "generations" / NEXT_GEN /
                             "critical_exclusivity.json").exists())
            with patch.object(custody, "_inspect_exact_cid") as inspect, \
                 patch.object(custody, "_docker_run") as docker:
                with self.assertRaises(custody.CustodyHold) as raised:
                    custody.begin_attempt().authorize_operation("critical", "start")
                self.assertEqual(raised.exception.exit_code, 79)
                inspect.assert_not_called()
                docker.assert_not_called()
            self.assertFalse((root / "generations" / successor["generation"] /
                              "operations.json").exists())

    def test_genesis_seal_refuses_unattested_running_critical(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            births = complete_genesis_births(session, root)
            # Simulate a corrupted/missing independent proof after Docker start.
            (root / "generations" / NEXT_GEN / "critical_exclusivity.json").unlink()
            by_cid = {record["Id"]: record for record in births.values()}
            with patch.object(custody, "_inspect_exact_cid",
                              side_effect=lambda cid: by_cid[cid]), \
                 self.assertRaisesRegex(custody.CustodyHold,
                                        "CRITICAL_EXCLUSIVITY"):
                session.seal_generation()
            self.assertFalse((root / "current.json").exists())

    def test_genesis_seal_requires_confirmed_critical_created_start_journal(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            births = complete_genesis_births(session, root)
            (root / "generations" / NEXT_GEN / "operations.json").unlink()
            by_cid = {record["Id"]: record for record in births.values()}
            with patch.object(custody, "_inspect_exact_cid",
                              side_effect=lambda cid: by_cid[cid]), \
                 self.assertRaisesRegex(custody.CustodyHold,
                                        "CRITICAL_START_NOT_ATTESTED"):
                session.seal_generation()
            self.assertFalse((root / "current.json").exists())

    def test_created_birth_start_crash_reconciles_without_docker_replay(self):
        with external_genesis_root() as root:
            host_receipt(root / "genesis.json", sample_genesis())
            session = custody.begin_genesis()
            created = record_genesis_birth(session, "observer", "4" * 64)
            with patch.object(custody, "_inspect_exact_cid", return_value=created), \
                 patch.object(custody, "_docker_run", side_effect=
                              custody.CustodyHold("DOCKER_EXECUTION_UNVERIFIED")):
                with self.assertRaises(custody.CustodyHold):
                    session.authorize_newborn_start("observer")
            journal = custody._load_journal(custody._genesis_intent(sample_genesis()))
            self.assertEqual(len(journal["events"]), 1)
            op_id = journal["events"][0]["id"]
            self.assertEqual(journal["events"][0]["phase"], "REQUESTED")
            running = {**created, "State": {"Status": "running"}}
            with patch.object(custody, "_inspect_exact_cid", return_value=running), \
                 patch.object(custody, "_docker_run") as docker:
                result = session.reconcile_operation(op_id)
                self.assertEqual(result["operation"], "start")
                self.assertEqual(result["role"], "observer")
                docker.assert_not_called()
            journal = custody._load_journal(custody._genesis_intent(sample_genesis()))
            self.assertEqual([e["phase"] for e in journal["events"]],
                             ["REQUESTED", "CONFIRMED"])




    def test_docker_cli_is_pinned_to_rootful_socket_and_ignores_user_context(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://foreign.example:2375",
                                     "DOCKER_CONTEXT": "foreign-context"}), \
             patch.object(custody.subprocess, "run", return_value=SimpleNamespace(
                 stdout="daemon-identity")) as run:
            custody._docker_run(("docker", "info", "--format", "{{.ID}}"), capture=True)
        args = run.call_args.args[0]
        self.assertEqual(tuple(args[:3]), (
            "/usr/bin/docker", "--host", "unix:///var/run/docker.sock"))
        self.assertEqual(tuple(args[3:]), ("info", "--format", "{{.ID}}"))
        self.assertNotIn("DOCKER_HOST", run.call_args.kwargs["env"])
        self.assertNotIn("DOCKER_CONTEXT", run.call_args.kwargs["env"])

    def test_missing_root_authority_holds_79_without_docker(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(custody, "TRUSTED_ROOT", Path(folder) / "absent"), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold) as raised:
                custody.begin_attempt()
            self.assertEqual(raised.exception.exit_code, 79)
            docker.assert_not_called()

    def test_three_unique_birth_cids_required_before_any_mutation(self):
        for role in ROLES:
            with self.subTest(role=role):
                receipt = sample_current()
                receipt["roles"].pop(role)
                with patch.object(custody, "_read_trusted_current", return_value=receipt), \
                     patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
                     patch.object(custody, "_docker_run") as docker:
                    with self.assertRaises(custody.CustodyHold):
                        custody.begin_attempt().authorize_operation("observer", "stop")
                    docker.assert_not_called()

    def test_cross_role_cid_duplicate_and_truncated_hold(self):
        for bad in ("1" * 63, "2" * 64):
            with self.subTest(bad=bad):
                receipt = sample_current()
                receipt["roles"]["observer"]["cid"] = bad
                with patch.object(custody, "_read_trusted_current", return_value=receipt), \
                     patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
                     patch.object(custody, "_docker_run") as docker:
                    with self.assertRaises(custody.CustodyHold):
                        custody.begin_attempt().authorize_operation("observer", "remove")
                    docker.assert_not_called()

    def test_attempt_generation_must_differ_from_trusted_predecessor(self):
        intent = sample_intent()
        intent["generation"] = GEN
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=intent), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt()
            docker.assert_not_called()

    def test_foreign_same_name_never_overrides_birth_identity(self):
        receipt = sample_current()
        wrong = inspected("observer", receipt)
        wrong["Id"] = "f" * 64
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=wrong), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("observer", "stop")
            docker.assert_not_called()

    def test_exact_cid_is_only_docker_stop_target(self):
        receipt = sample_current()
        cid = receipt["roles"]["observer"]["cid"]
        stopped = inspected("observer", receipt)
        stopped["State"]["Status"] = "exited"
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", side_effect=[
                 inspected("observer", receipt), stopped]), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_journal_event", create=True), \
             patch.object(custody, "_load_journal", create=True,
                          return_value={"events": []}), \
             patch.object(custody, "_docker_run") as docker:
            custody.begin_attempt().authorize_operation("observer", "stop")
            docker.assert_called_once_with(("docker", "stop", "--time", "10", cid), capture=False)

    def test_mutation_has_durable_requested_and_confirmed_events(self):
        receipt = sample_current()
        cid = receipt["roles"]["observer"]["cid"]
        stopped = inspected("observer", receipt)
        stopped["State"]["Status"] = "exited"
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", side_effect=[
                 inspected("observer", receipt), stopped]), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_load_journal", create=True,
                          return_value={"events": []}), \
             patch.object(custody, "_journal_event", create=True) as journal, \
             patch.object(custody, "_docker_run") as docker:
            custody.begin_attempt().authorize_operation("observer", "stop")
            self.assertEqual(journal.call_count, 2)
            self.assertEqual(journal.call_args_list[0].kwargs["phase"], "REQUESTED")
            self.assertEqual(journal.call_args_list[1].kwargs["phase"], "CONFIRMED")
            self.assertEqual(journal.call_args_list[0].kwargs["cid"], cid)
            docker.assert_called_once()

    def test_timeout_preserves_requested_event_without_retry(self):
        receipt = sample_current()
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=inspected("observer", receipt)), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_load_journal", create=True,
                          return_value={"events": []}), \
             patch.object(custody, "_journal_event", create=True) as journal, \
             patch.object(custody, "_docker_run", side_effect=custody.CustodyHold(
                 "DOCKER_EXECUTION_UNVERIFIED")) as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("observer", "stop")
            self.assertEqual(journal.call_count, 1)
            self.assertEqual(journal.call_args.kwargs["phase"], "REQUESTED")
            docker.assert_called_once()

    def test_unresolved_prior_operation_blocks_next_mutation(self):
        receipt = sample_current()
        cid = receipt["roles"]["observer"]["cid"]
        outstanding = {"events": [{"id": "f" * 32, "phase": "REQUESTED",
                                   "role": "observer", "operation": "stop", "cid": cid}]}
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=inspected("observer", receipt)), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_load_journal", create=True,
                          return_value=outstanding), \
             patch.object(custody, "_journal_event", create=True), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("observer", "stop")
            docker.assert_not_called()

    def test_successful_stop_cli_without_exited_postcondition_stays_unresolved(self):
        receipt = sample_current()
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=inspected(
                 "observer", receipt)) as inspect, \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_journal_event") as journal, \
             patch.object(custody, "_load_journal", return_value={"events": []}), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("observer", "stop")
            docker.assert_called_once()
            self.assertEqual(journal.call_count, 1)
            self.assertEqual(journal.call_args.kwargs["phase"], "REQUESTED")
            self.assertEqual(inspect.call_count, 2)

    def test_timed_out_stop_reconciles_exact_cid_without_retry(self):
        receipt = sample_current()
        cid = receipt["roles"]["observer"]["cid"]
        stopped = inspected("observer", receipt)
        stopped["State"]["Status"] = "exited"
        op_id = "f" * 32
        journal = {"events": [{"id": op_id, "phase": "REQUESTED",
                               "role": "observer", "operation": "stop", "cid": cid}]}
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=stopped), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_load_journal", return_value=journal), \
             patch.object(custody, "_journal_event") as commit, \
             patch.object(custody, "_docker_run") as docker:
            result = custody.begin_attempt().reconcile_operation(op_id)
            self.assertEqual(result["status"], "RECONCILED")
            self.assertEqual(commit.call_count, 1)
            self.assertEqual(commit.call_args.kwargs["phase"], "CONFIRMED")
            docker.assert_not_called()

    def test_timed_out_exec_never_reconciles_from_container_state(self):
        receipt = sample_current()
        cid = receipt["roles"]["observer"]["cid"]
        op_id = "f" * 32
        journal = {"events": [{"id": op_id, "phase": "REQUESTED",
                               "role": "observer", "operation": "exec", "cid": cid}]}
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_load_journal", return_value=journal), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().reconcile_operation(op_id)
            docker.assert_not_called()

    def test_wrong_daemon_digest_generation_or_birth_time_holds(self):
        receipt = sample_current()
        for key, value in (("daemon_id", "other-daemon"),
                           ("artifact_digest", "sha256:" + "f" * 64),
                           ("generation", "f" * 32)):
            with self.subTest(key=key):
                old = sample_current()
                old[key] = value
                with patch.object(custody, "_read_trusted_current", return_value=old), \
                     patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
                     patch.object(custody, "_docker_run") as docker:
                    with self.assertRaises(custody.CustodyHold):
                        custody.begin_attempt().authorize_operation("dashboard", "stop")
                    docker.assert_not_called()
        changed = inspected("observer", receipt)
        changed["Created"] = "2026-11-09T13:00:00Z"
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_verify_sealed_birth"), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=changed), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().authorize_operation("observer", "stop")
            docker.assert_not_called()

    def test_duplicate_json_keys_and_nan_are_not_accepted(self):
        for raw in ('{"schema_version":1,"schema_version":2}',
                    '{"schema_version":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(custody.CustodyHold):
                custody._strict_json(raw.encode())

    def test_symlink_and_hardlink_receipts_are_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "payload"
            source.write_text(json.dumps(sample_current()))
            alias = Path(folder) / "alias"
            alias.symlink_to(source)
            with self.assertRaises(custody.CustodyHold):
                custody._read_regular_file(alias, required_uid=os.getuid())
            alias.unlink()
            os.link(source, alias)
            with self.assertRaises(custody.CustodyHold):
                custody._read_regular_file(alias, required_uid=os.getuid())

    def test_record_birth_requires_prepared_exact_cidfile_not_name_inspect(self):
        receipt = sample_current()
        intent = sample_intent()
        with patch.object(custody, "_read_trusted_current", return_value=receipt), \
             patch.object(custody, "_read_trusted_intent", return_value=intent), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_read_pending", return_value={
                 "generation": NEXT_GEN, "intent_digest": hashlib.sha256(json.dumps(
                     intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                 "prepared_roles": ["observer"], "roles": {}}), \
             patch.object(custody, "_read_birth_cidfile", side_effect=custody.CustodyHold(
                 "BIRTH_CIDFILE_MISSING")), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().record_birth("observer", "2" * 64)
            docker.assert_not_called()

    def test_birth_cidfile_must_match_emitted_cid(self):
        intent = sample_intent()
        pending = {"generation": NEXT_GEN,
                   "intent_digest": hashlib.sha256(json.dumps(
                       intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                   "prepared_roles": ["observer"], "roles": {}}
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=intent), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_read_birth_cidfile", return_value="1" * 64), \
             patch.object(custody, "_inspect_exact_cid") as inspect, \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().record_birth("observer", "2" * 64)
            inspect.assert_not_called()
            docker.assert_not_called()

    def test_birth_writes_only_private_pending_and_seal_requires_all_three(self):
        intent = sample_intent()
        pending = {"generation": NEXT_GEN,
                   "intent_digest": hashlib.sha256(json.dumps(
                       intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                   "prepared_roles": ["observer"], "roles": {}}
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=intent), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_read_birth_cidfile", return_value="4" * 64), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=newly_inspected(
                 "observer", "4" * 64)), \
             patch.object(custody, "_write_pending") as write_pending, \
             patch.object(custody, "_write_current") as write_current:
            session = custody.begin_attempt()
            session.record_birth("observer", "4" * 64)
            self.assertEqual(write_pending.call_args.args[0]["roles"]["observer"]["cid"],
                             "4" * 64)
            with self.assertRaises(custody.CustodyHold):
                session.seal_generation()
            write_current.assert_not_called()

    def test_birth_cidfile_path_preexisting_must_hold_without_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            cidfile = Path(folder) / "observer.cid"
            cidfile.write_text("9" * 64)
            with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
                 patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
                 patch.object(custody, "_root_lock", return_value=nullcontext()), \
                 patch.object(custody, "_birth_file_path", return_value=cidfile), \
                 patch.object(custody, "_docker_run") as docker:
                with self.assertRaises(custody.CustodyHold):
                    custody.begin_attempt().prepare_birth("observer")
                self.assertEqual(cidfile.read_text(), "9" * 64)
                docker.assert_not_called()

    def test_seal_refuses_one_stopped_role_and_never_publishes_it(self):
        full = complete_pending()
        role_by_cid = {record["cid"]: role for role, record in full["roles"].items()}

        def inspect(cid):
            role = role_by_cid[cid]
            result = newly_inspected(role, cid)
            if role == "critical":
                result["State"]["Status"] = "exited"
            return result

        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=full), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_load_journal",
                          return_value=confirmed_critical_start_journal()), \
             patch.object(custody, "_inspect_exact_cid", side_effect=inspect), \
             patch.object(custody, "_attest_critical_exclusivity"), \
             patch.object(custody, "_write_current") as write_current:
            with self.assertRaisesRegex(custody.CustodyHold, "SEAL_ROLE_NOT_RUNNING"):
                custody.begin_attempt().seal_generation()
            write_current.assert_not_called()

    def test_seal_publishes_exactly_three_verified_new_births(self):
        full = complete_pending()
        role_by_cid = {record["cid"]: role for role, record in full["roles"].items()}
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=full), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_load_journal",
                          return_value=confirmed_critical_start_journal()), \
             patch.object(custody, "_inspect_exact_cid", side_effect=lambda cid: newly_inspected(
                 role_by_cid[cid], cid)), \
             patch.object(custody, "_attest_critical_exclusivity"), \
             patch.object(custody, "_write_current") as write_current:
            result = custody.begin_attempt().seal_generation()
            self.assertEqual(result["role_count"], 3)
            saved = write_current.call_args.args[0]
            self.assertEqual(saved["generation"], NEXT_GEN)
            self.assertEqual(set(saved["roles"]), set(ROLES))
            self.assertTrue(saved["sealed"])

    def test_next_attempt_can_follow_seal_without_erasing_old_pending_evidence(self):
        prior = newly_sealed()
        next_intent = sample_intent()
        next_intent["generation"] = "a" * 32
        next_intent["candidate_sha"] = "1" * 40
        next_intent["prior_receipt_sha256"] = hashlib.sha256(json.dumps(
            prior, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        old_pending = complete_pending()
        self.assertNotEqual(custody._pending_file_path(next_intent),
                            custody._pending_file_path(sample_intent()))
        result = custody._pending_for_intent(next_intent, None)
        self.assertEqual(result["generation"], "a" * 32)
        self.assertEqual(result["prepared_roles"], [])
        self.assertEqual(result["roles"], {})
        self.assertEqual(old_pending["generation"], NEXT_GEN)

    def test_birth_labels_are_derived_only_from_root_intent(self):
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_root_lock", return_value=nullcontext()):
            flags = custody.begin_attempt().birth_labels("observer")
        labels = [flags[i + 1] for i in range(0, len(flags), 2)]
        self.assertTrue(all(flags[i] == "--label" for i in range(0, len(flags), 2)))
        self.assertIn("porota.rc6.generation=" + NEXT_GEN, labels)
        self.assertIn("porota.rc6.source_sha=" + NEW_SHA, labels)
        self.assertIn("porota.rc6.role=observer", labels)
        self.assertIn("porota.rc6.artifact_digest=" + NEW_DIGEST, labels)
        self.assertNotIn("porota.rc6.role=dashboard", labels)

    def test_pending_birth_verification_uses_fresh_cid_never_old_role(self):
        pending = complete_pending()
        fresh = pending["roles"]["observer"]["cid"]
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_read_birth_cidfile", return_value=fresh), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=newly_inspected(
                 "observer", fresh)) as inspect:
            observed = custody.begin_attempt().verify_pending_birth("observer")
        self.assertEqual(observed["Id"], fresh)
        inspect.assert_called_once_with(fresh)
        self.assertNotEqual(fresh, sample_current()["roles"]["observer"]["cid"])

    def test_pending_birth_rejects_cidfile_mismatch_without_docker(self):
        pending = complete_pending()
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_read_birth_cidfile", return_value="9" * 64), \
             patch.object(custody, "_docker_run") as docker:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().verify_pending_birth("observer")
            docker.assert_not_called()

    def test_newborn_exec_only_allows_bound_observer_hash_command(self):
        pending = complete_pending()
        fresh = pending["roles"]["observer"]["cid"]
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_read_birth_cidfile", return_value=fresh), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_inspect_exact_cid", return_value=newly_inspected(
                 "observer", fresh)), \
             patch.object(custody, "_journal_event", create=True), \
             patch.object(custody, "_load_journal", create=True,
                          return_value={"events": []}), \
             patch.object(custody, "_docker_run") as docker:
            session = custody.begin_attempt()
            with self.assertRaises(custody.CustodyHold):
                session.authorize_newborn_operation("observer", "exec", "sh", "-c", "rm -rf /")
            docker.assert_not_called()
            session.authorize_newborn_operation(
                "observer", "exec", "sha256sum", "/app/cf_intraday_scalping.py", capture=True)
            docker.assert_called_once_with(("docker", "exec", fresh, "sha256sum",
                                           "/app/cf_intraday_scalping.py"), capture=True)

    def test_seal_refuses_unresolved_journal_even_when_three_births_running(self):
        pending = complete_pending()
        with patch.object(custody, "_read_trusted_current", return_value=sample_current()), \
             patch.object(custody, "_read_trusted_intent", return_value=sample_intent()), \
             patch.object(custody, "_read_pending", return_value=pending), \
             patch.object(custody, "_root_lock", return_value=nullcontext()), \
             patch.object(custody, "_daemon_identity", return_value=DAEMON), \
             patch.object(custody, "_load_journal", create=True, return_value={"events": [{
                 "id": "f" * 32, "phase": "REQUESTED", "role": "observer",
                 "operation": "stop", "cid": "2" * 64}]}), \
             patch.object(custody, "_inspect_exact_cid") as inspect, \
             patch.object(custody, "_write_current") as publish:
            with self.assertRaises(custody.CustodyHold):
                custody.begin_attempt().seal_generation()
            inspect.assert_not_called()
            publish.assert_not_called()

    def test_real_private_journal_survives_second_process_style_reload(self):
        intent = sample_intent()
        op_id = "9" * 32
        original_read = custody._read_regular_file
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            generations = root / "generations"
            generations.mkdir(mode=0o700)
            (generations / NEXT_GEN).mkdir(mode=0o700)
            with patch.object(custody, "TRUSTED_ROOT", root), \
                 patch.object(custody, "ROOT_UID", os.getuid()), \
                 patch.object(custody, "_read_regular_file", side_effect=lambda p, **kw:
                              original_read(p, required_uid=os.getuid(), **kw)), \
                 patch.object(custody, "_verify_root_directory"):
                custody._journal_event(intent, role="observer", operation="stop",
                                       cid="4" * 64, op_id=op_id, phase="REQUESTED")
                first = custody._load_journal(intent)
                self.assertEqual(first["events"][0]["phase"], "REQUESTED")
                self.assertEqual((generations / NEXT_GEN / "operations.json")
                                 .stat().st_mode & 0o777, 0o600)
                self.assertEqual(set(custody._unresolved_events(first)), {op_id})
                custody._journal_event(intent, role="observer", operation="stop",
                                       cid="4" * 64, op_id=op_id, phase="CONFIRMED")
                second = custody._load_journal(intent)
                self.assertEqual([e["phase"] for e in second["events"]],
                                 ["REQUESTED", "CONFIRMED"])
                self.assertFalse(custody._unresolved_events(second))

    def test_corrupt_and_symlinked_journal_hold_without_replacing_target(self):
        intent = sample_intent()
        original_read = custody._read_regular_file
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            generations = root / "generations"
            generations.mkdir(mode=0o700)
            gen = generations / NEXT_GEN
            gen.mkdir(mode=0o700)
            journal = gen / "operations.json"
            with patch.object(custody, "TRUSTED_ROOT", root), \
                 patch.object(custody, "ROOT_UID", os.getuid()), \
                 patch.object(custody, "_read_regular_file", side_effect=lambda p, **kw:
                              original_read(p, required_uid=os.getuid(), **kw)), \
                 patch.object(custody, "_verify_root_directory"):
                journal.write_bytes(b'{"events":[]}')
                journal.chmod(0o600)
                with self.assertRaises(custody.CustodyHold):
                    custody._load_journal(intent)
                journal.unlink()
                target = root / "foreign"
                target.write_text("DO_NOT_TOUCH")
                journal.symlink_to(target)
                with self.assertRaises(custody.CustodyHold):
                    custody._journal_event(intent, role="observer", operation="stop",
                                           cid="4" * 64, op_id="8" * 32,
                                           phase="REQUESTED")
                self.assertEqual(target.read_text(), "DO_NOT_TOUCH")

    def test_unsealed_foreign_pending_must_not_be_reset(self):
        foreign = complete_pending()
        foreign["generation"] = "1" * 32
        with self.assertRaises(custody.CustodyHold):
            custody._pending_for_intent(sample_intent(), foreign)


if __name__ == "__main__":
    unittest.main()

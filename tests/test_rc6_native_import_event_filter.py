"""Unit controls for filtering irrelevant native audit events, not import closure.

The actual observer method and RLock run here. The standalone child registers
that method with CPython and uses real builtins.id/sys.audit events. These
controls never claim source qualification, kernel FIN, or material BIG GREEN.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import rc6_native_import_provenance as provenance


def _unit_observer():
    # Actual class/method; narrowly initialize the audit-only fields. Full native
    # qualification remains covered by the existing provenance suite unchanged.
    observer = object.__new__(provenance.NativeImportObserver)
    observer.active = True
    observer.native_pid = os.getpid()
    observer.installing = False
    observer._installation_nonce = None
    observer.installation_receptions = 0
    observer.lock = threading.RLock()
    observer.record_limit = 4
    observer.counts = {"import": 0, "exec": 0, "import_filename_none": 0,
                       "opaque_exec": 0, "overflow": 0, "errors": 0}
    observer.records = []
    observer.seen = set()
    observer.pending_names = set()
    observer.evidence_keys = set()
    return observer


def _event_arguments(event):
    if event == "import":
        return ("rc6_filter_unit_literal", __file__)
    return (compile("pass", __file__, "exec"),)


@pytest.mark.parametrize("event", ["builtins.id", "sys._getframe", "open", "gc.get_objects"])
@pytest.mark.parametrize("active", [True, False])
def test_irrelevant_audit_events_do_not_lookup_pid_or_change_records(monkeypatch, event, active):
    observer = _unit_observer()
    observer.active = active
    before = dict(observer.counts)
    def forbidden_pid():
        raise AssertionError("IGNORED_EVENT_PERFORMED_PID_LOOKUP")
    monkeypatch.setattr(provenance.os, "getpid", forbidden_pid)
    observer.audit(event, ())
    assert observer.counts == before
    assert observer.records == []
    assert observer.seen == set()
    assert observer.evidence_keys == set()
    assert observer.pending_names == set()
    assert observer.installation_receptions == 0


@pytest.mark.parametrize("event", ["import", "exec"])
@pytest.mark.parametrize("state", ["ACTIVE_OWNED", "INACTIVE", "FOREIGN_PID"])
def test_relevant_audit_events_keep_active_pid_and_original_records(monkeypatch, event, state):
    observer = _unit_observer()
    native_getpid = os.getpid
    calls = []
    arguments = _event_arguments(event)
    if state == "INACTIVE":
        observer.active = False
    elif state == "FOREIGN_PID":
        observer.native_pid += 1
    def counted_pid():
        calls.append("NATIVE_PID_READ")
        return native_getpid()
    monkeypatch.setattr(provenance.os, "getpid", counted_pid)
    observer.audit(event, arguments)
    if state == "ACTIVE_OWNED":
        assert calls == ["NATIVE_PID_READ"]
        assert observer.counts[event] == 1
        assert observer.records == [{"event": event,
            "module": arguments[0] if event == "import" else None,
            "filename": __file__}]
        assert len(observer.evidence_keys) == len(observer.seen) == 1
        assert observer.counts["errors"] == observer.counts["overflow"] == 0
    else:
        assert calls == ([] if state == "INACTIVE" else ["NATIVE_PID_READ"])
        assert observer.records == []
        assert observer.evidence_keys == observer.seen == set()
        assert all(value == 0 for value in observer.counts.values())


@pytest.mark.parametrize("event", ["import", "exec"])
def test_relevant_audit_rechecks_active_inside_actual_lock(monkeypatch, event):
    observer = _unit_observer()
    arguments = _event_arguments(event)
    native_getpid = os.getpid
    calls = []
    native_lock = threading.RLock()
    class ActualLockWithBoundaryMutation:
        def __enter__(self):
            native_lock.acquire()
            observer.active = False
        def __exit__(self, *arguments):
            native_lock.release()
    observer.lock = ActualLockWithBoundaryMutation()
    def counted_pid():
        calls.append("NATIVE_PID_READ")
        return native_getpid()
    monkeypatch.setattr(provenance.os, "getpid", counted_pid)
    observer.audit(event, arguments)
    assert calls == ["NATIVE_PID_READ"]
    assert observer.active is False
    assert observer.records == []
    assert all(value == 0 for value in observer.counts.values())
    assert observer.evidence_keys == observer.seen == set()
    assert native_lock.acquire(blocking=False)
    native_lock.release()


@pytest.mark.parametrize("state", ["EXACT", "NOT_INSTALLING", "FOREIGN_PID", "WRONG_NONCE", "WRONG_ARITY"])
def test_installation_event_keeps_original_nonce_pid_and_state_order(monkeypatch, state):
    observer = _unit_observer()
    observer.active = False  # Installation remains independent of active.
    observer.installing = True
    nonce = object()
    observer._installation_nonce = nonce
    arguments = (nonce,)
    if state == "NOT_INSTALLING":
        observer.installing = False
    elif state == "FOREIGN_PID":
        observer.native_pid += 1
    elif state == "WRONG_NONCE":
        arguments = (object(),)
    elif state == "WRONG_ARITY":
        arguments = (nonce, nonce)
    native_getpid = os.getpid
    calls = []
    def counted_pid():
        calls.append("NATIVE_PID_READ")
        return native_getpid()
    monkeypatch.setattr(provenance.os, "getpid", counted_pid)
    observer.audit(provenance._INSTALLATION_EVENT, arguments)
    assert observer.installation_receptions == (1 if state == "EXACT" else 0)
    assert calls == ([] if state == "NOT_INSTALLING" else ["NATIVE_PID_READ"])
    assert observer.records == []
    assert all(value == 0 for value in observer.counts.values())
    assert observer.evidence_keys == observer.seen == set()


def _native_filter_unit_control():
    # CPython runs the tracked file directly in an ephemeral owned subprocess.
    observer = _unit_observer()
    native_getpid = os.getpid
    calls = []
    def counted_pid():
        calls.append("NATIVE_PID_READ")
        return native_getpid()
    sys.addaudithook(observer.audit)
    try:
        provenance.os.getpid = counted_pid
        observer.installing = True
        observer._installation_nonce = object()
        sys.audit(provenance._INSTALLATION_EVENT, observer._installation_nonce)
        assert observer.installation_receptions == 1
        assert calls == ["NATIVE_PID_READ"]
        observer.installing = False
        observer._installation_nonce = None
        calls.clear()
        values = [object(), object(), object()]
        for value in values:
            id(value)  # Actual CPython builtins.id event, not a simulated call.
        assert calls == []
        sys.audit("import", "rc6_filter_native_unit_literal", __file__)
        code = compile("pass", __file__, "exec")
        sys.audit("exec", code)
        assert calls == ["NATIVE_PID_READ", "NATIVE_PID_READ"]
        assert observer.counts["import"] == observer.counts["exec"] == 1
        assert observer.counts["errors"] == observer.counts["overflow"] == 0
        assert len(observer.records) == len(observer.seen) == len(observer.evidence_keys) == 2
        observer.native_pid = native_getpid() + 1
        sys.audit("exec", code)
        assert calls == ["NATIVE_PID_READ"] * 3
        assert observer.counts["exec"] == 1
        observer.native_pid = native_getpid()
        observer.active = False
        sys.audit("exec", code)
        assert calls == ["NATIVE_PID_READ"] * 3
        return {"schema": "rc6.native-audit-filter-unit-control.v1",
            "actual_native_hook_nonce_received": True,
            "actual_builtin_id_events_without_pid_lookup": len(values),
            "actual_import_exec_records": len(observer.records),
            "foreign_pid_veto_preserved": True, "inactive_veto_preserved": True,
            "unit_only_not_source_import_closure": True,
            "material_GREEN_claimed": False}
    finally:
        observer.active = False
        observer.installing = False
        observer._installation_nonce = None
        provenance.os.getpid = native_getpid


def test_real_standalone_audit_hook_filters_builtin_id_and_preserves_relevant_events(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
        "--native-audit-filter-unit-control"], cwd=tmp_path, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    control = json.loads(result.stdout)
    assert control == {"schema": "rc6.native-audit-filter-unit-control.v1",
        "actual_native_hook_nonce_received": True,
        "actual_builtin_id_events_without_pid_lookup": 3,
        "actual_import_exec_records": 2,
        "foreign_pid_veto_preserved": True, "inactive_veto_preserved": True,
        "unit_only_not_source_import_closure": True, "material_GREEN_claimed": False}


if __name__ == "__main__":
    if sys.argv[1:] != ["--native-audit-filter-unit-control"]:
        raise SystemExit("NATIVE_AUDIT_FILTER_UNIT_ARGUMENTS_INVALID")
    print(json.dumps(_native_filter_unit_control(), sort_keys=True))

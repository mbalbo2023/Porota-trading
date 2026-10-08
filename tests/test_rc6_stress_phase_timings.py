from scripts import rc6_issue465_stress as stress


def test_nested_timings_separate_inclusive_from_exclusive(monkeypatch):
    walls, cpus = iter((0.0, 2.0, 5.0, 10.0)), iter((0.0, 1.0, 2.0, 4.0))
    monkeypatch.setattr(stress.time, "monotonic", lambda: next(walls))
    monkeypatch.setattr(stress.time, "process_time", lambda: next(cpus))
    timer = stress._NestedPhaseTimings()
    publication = {"elapsed_seconds": 0.0, "cpu_seconds": 0.0}
    preparation = {"elapsed_seconds": 0.0, "cpu_seconds": 0.0}
    parent = timer.start()
    child = timer.start()
    timer.finish(child, preparation)
    timer.finish(parent, publication)
    assert preparation["elapsed_seconds"] == preparation["exclusive_elapsed_seconds"] == 3.0
    assert publication["elapsed_seconds"] == 10.0
    assert publication["exclusive_elapsed_seconds"] == 7.0
    assert preparation["exclusive_elapsed_seconds"] + publication["exclusive_elapsed_seconds"] == 10.0
    assert preparation["exclusive_cpu_seconds"] == 1.0
    assert publication["exclusive_cpu_seconds"] == 3.0


def test_phase_timing_cannot_close_an_outer_scope_before_its_child():
    timer = stress._NestedPhaseTimings()
    parent = timer.start()
    child = timer.start()
    try:
        timer.finish(parent, {"elapsed_seconds": 0.0, "cpu_seconds": 0.0})
    except RuntimeError as error:
        assert str(error) == "NATIVE_PHASE_TIMING_SCOPE_MISMATCH"
    else:
        raise AssertionError("A mismatched observation scope must fail closed")
    timer.finish(child, {"elapsed_seconds": 0.0, "cpu_seconds": 0.0})
    timer.finish(parent, {"elapsed_seconds": 0.0, "cpu_seconds": 0.0})

"""Pure clock controls for idle gating; no Docker, credentials or host access."""
import ast,math,pathlib,re
from datetime import datetime,timedelta,timezone
tree=ast.parse(pathlib.Path("scripts/rc6_audit_rowshape_native_host.py").read_text())
function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="await_idle_hint")
code=compile(ast.Module(body=[function],type_ignores=[]),"<reviewed-idle-function>","exec")
BASE=datetime(2026,10,17,12,tzinfo=timezone.utc)
def case(mode):
    class Clock:
        elapsed=0
        def sleep(self,value):self.elapsed+=value
    clock=Clock()
    class Date:
        @staticmethod
        def now(_zone):return BASE+timedelta(seconds=clock.elapsed)
        fromisoformat=staticmethod(datetime.fromisoformat)
    def require(ok,text):
        if not ok:raise ValueError(text)
    def envelope(_path,_limit):
        if mode=="bad_envelope":raise ValueError("RCA_ENVELOPE_DIGEST_MISMATCH")
        if mode=="delayed" and clock.elapsed>=120:
            return {"status":"SHADOW_OBSERVING","as_of":(Date.now(timezone.utc)-timedelta(seconds=7)).isoformat(),
                    "generation_timing":{"tick_seconds":5,"poll_seconds":30,"tick_cpu_seconds":3}}
        if mode=="stale":
            return {"status":"SHADOW_OBSERVING","as_of":(Date.now(timezone.utc)-timedelta(days=1)).isoformat(),
                    "generation_timing":{"tick_seconds":5}}
        return {"status":"FAIL_CLOSED","as_of":Date.now(timezone.utc).isoformat(),
                "error_class":"ValueError","error_module":"runtime","error_line":48,
                "runtime_read_stage":"CATALOG_FULL","runtime_deadline_expired":True,
                "runtime_sql_diagnostics":{"budget_ms":2000,"sql_elapsed_ms":21,"transform_elapsed_ms":2001,"transform_expired_stage":"CATALOG_FULL"},
                "private_exception_text":"SECRET_MUST_NOT_BE_EXPORTED"}
    scope={"math":math,"re":re,"datetime":Date,"timezone":timezone,"time":clock,"envelope":envelope,"require":require}
    exec(code,scope);report={}
    try:scope["await_idle_hint"](pathlib.Path("/synthetic"),report)
    except ValueError as error:
        assert mode!="delayed" and str(error)=="RCA_IDLE_WINDOW_NOT_OBSERVED"
        assert clock.elapsed==180 and "idle_window_observed" not in report
    else:
        assert mode=="delayed" and clock.elapsed==120 and report["idle_window_observed"] is True
    assert len(report["idle_observations"])==6
    assert "SECRET_MUST_NOT_BE_EXPORTED" not in str(report)
    if mode in ("delayed","closed"):
        assert report["idle_first_observation"]["status"]=="FAIL_CLOSED"
        assert report["idle_first_observation"]["runtime_deadline_expired"] is True
        assert report["idle_first_observation"]["sql"]["transform_expired_stage"]=="CATALOG_FULL"
    return {"case":mode,"elapsed":clock.elapsed,"observations":len(report["idle_observations"])}
rows=[case(mode) for mode in ("delayed","closed","stale","bad_envelope")]
print("RC6_ROW_SHAPE_IDLE_CONTROLS=GREEN|cases="+str(len(rows))+"|same20secondwindow|delayed120observed|unsafe180rejected|boundednoexceptionpayload",flush=True)
